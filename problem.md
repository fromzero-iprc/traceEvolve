是，**旧版经验池质量不高，经验提取器链路本身有明显责任**。
而且不是单点 bug，更像是整条 pipeline 的“知识建模”不够严格：**抽取得太宽、过滤太弱、合并偏插入、分池标准失真**。

我先给结论，再按文件说优化方案。

---

## 结论

你旧池子里出现的几类问题：

* 泛方法论条目混入主池
* 同一问题多条近重复
* 相互冲突的阶段性结论并存
* style / portability / reviewer 建议和功能性经验混排

这些问题，**从代码上看是能解释通的**，不是偶然。原因主要有四个：

### 1. extractor 允许“任何像经验的话”落库

`Experience` 结构只有 `category/problem/solution/evidence/task_id`，**没有经验类型字段**，所以功能 bug、规格一致性、流程提醒、风格建议都会被当成同一种对象处理。

### 2. postprocessor 过滤太轻

当前只做了：

* category 归一化
* id 清洗
* importance 修正
* 最短长度过滤
* 极少数 vague pattern 过滤
* 同 category 的 Jaccard 去重

这对“认真读规格”“comment 更清楚一点”“tool warning 可能是假阳性”这类条目，基本拦不住。

### 3. manager 仍然偏向 INSERT

`_find_candidates()` 的召回条件比较宽，`_strict_fallback_operation()` 在“无候选”或“低 overlap”时很容易直接 `INSERT`，而且只在相似度较高时才 `REPLACE/SKIP`。
这会天然鼓励“同一主题换个说法再来一条”。

### 4. pool_splitter 把“泛经验=core、具体经验=extended”

`classify_experience()` 里，**task-specific 信号高反而被送去 extended**，而 generality 高才进 core。
这和你真正想要的“core 高精度主经验 / extended 次级补充”并不一致，所以分池后很容易把泛条目抬得过高。

所以答案很明确：

> **旧版经验池质量不高，确实很大程度上是经验提取器模块的问题。**

---

# 逐文件看问题和优化方案

---

## 1. `extractor.py`

### 现在的问题

`Experience` schema 太薄，只有：

* `id`
* `category`
* `problem`
* `solution`
* `importance`
* `evidence`
* `task_id` 

这会导致 extractor 无法表达：

* 这是功能 bug 还是流程提醒
* 这是 spec-compliance 还是 style advice
* 这是 canonical rule 还是 task-local workaround

另外，`_extract_qimeng_per_segment()` 是按 task segment 并发抽取，segment 里把 `question / errors / feedback / suggestions / verification / final_code` 都塞进 prompt。这个输入很丰富，但如果 prompt 不强约束，就会把 `suggestions`、review comment、checker meta 也一起抽成经验。 

### 优化方案

先改 schema，再改 prompt。

#### 建议新增字段

给 `Experience` 加这些字段：

```python
experience_type: str   # functional_bug_fix | spec_compliance | implementation_pattern | style_or_portability | meta_process
root_cause_type: str   # reset | width | interface | arithmetic | state_machine | cdc | timing | style | process
task_scope: str        # cross_task | task_specific
canonical: bool        # 是否主条目
confidence: float      # 0~1
```

#### 改抽取 prompt

你现在 prompt 已经写了“不要泛泛经验”，但不够硬。
要改成**先分类、再决定能否入主池**：

* 如果是 `style_or_portability` 或 `meta_process`，默认不进主经验池
* 每条经验必须回答：

  * 这次失败的最小根因是什么
  * 解决动作是否能转成具体代码修改
  * evidence 是否直接指向这个根因

#### 新的抽取规则

只有满足下面三条，才允许产出主经验：

* **失败机制具体**
* **解决动作具体**
* **证据直接**

否则输出到 secondary notes，而不是 experiences。

---

## 2. `postprocessor.py`

### 现在的问题

后处理很弱，核心质量门槛只有：

* `problem` 长度 >= 20
* `solution` 长度 >= 30
* 不匹配少量 vague pattern

这解释了为什么旧池里会混进很多：

* checker/process 提醒
* comment/portability 建议
* “可读性更好”式条目

另外，去重只在**同 category 内**做，而且只看 `problem + solution` 的 Jaccard，相似阈值也不高。
这会漏掉很多：

* 跨 category 的近重复
* 不同 wording 的同一任务规律
* 同主题多阶段结论

### 优化方案

#### 质量过滤升级

在 `_passes_quality()` 里增加：

* `experience_type` 白名单：主池只允许 `functional_bug_fix/spec_compliance/implementation_pattern`
* `genericity_penalty`
* `actionability_score`
* `evidence_score`

例如直接拦掉这些模式：

* “be careful / review / make sure / comment could be clearer”
* “tool may warn”
* “this is acceptable but …”
* “prefer / clearer / readability”

这些都很像旧池里那些 style/process 污染条目。

#### 去重升级

不要只按 category 分组。建议改成：

* 同 `root_cause_type`
* 或同 `task_id`
* 或 problem/solution embedding 相近

再做簇内 dedup。

并且保留：

* `merged_from`
* `evidence_list`

而不是简单丢弃重复项。

---

## 3. `manager.py`

### 现在的问题

这是旧池“越积越脏”的另一个主因。

#### 问题 A：候选召回太宽，但裁决不够强

`_find_candidates()` 里，只要：

* same_task
* 或 same_category
* 或 sim >= 0.24

就会进候选。没有的话，sim >= 0.18 也会补一层弱召回。

但后面的 fallback 逻辑是：

* 无候选时，只要不是低信号且没 task_id，就直接 `INSERT`
* overlap 不高时也倾向 `INSERT`

这套组合很容易导致：

> “只要新经验换个表述，没撞上高相似，就新增一条。”

#### 问题 B：没有真正的冲突裁决

`_resolve_conflicts()` 只处理“目标被前序操作删掉”这种执行冲突，不处理**语义冲突**。
所以像：

* 某条说 CDC 必须同步
* 某条说某 CDC warning 是 false positive

这种会一起留着。

#### 问题 C：池内 dedup 仍太表面

`save()` 前会做 `_normalize_and_deduplicate_experiences()`，但还是按 category 分组，复用 postprocessor 的 `_dedup_group()`。
这对“同一任务不同类别描述”没什么办法。

### 优化方案

#### 改成 merge-first，而不是 insert-first

每条新经验的决策顺序改成：

1. 找同簇 canonical 条目
2. 若相似且同根因：`MERGE`
3. 若新版本明显更好：`REPLACE`
4. 若冲突：触发 conflict resolution
5. 只有都不满足才 `INSERT`

而不是现在的“找不到像的就插入”。

#### 引入三分决策

不只是 `INSERT/REPLACE/SKIP`，还要显式支持：

* `MERGE_EVIDENCE`
* `DOWNRANK`
* `REJECT_AS_META`

#### 引入冲突裁决规则

优先级建议：

1. `spec_compliance` > `general engineering advice`
2. 最终验证通过的经验 > 中间阶段经验
3. task-specific hard evidence > reviewer preference

这能直接解决旧池里那种“互相打架都留下”的问题。

---

## 4. `quality.py`

### 现在的问题

`quality_score()` 有个很关键的问题：

它对 `_TASK_SPECIFIC_KEYWORDS` 命中是**减 2 分**。

也就是说，越 task-specific 的经验，质量分反而越低。
这和你实际需要的方向几乎是反的。

此外，它把 `Language Compliance` 也放在核心类别里，solution 里动作词多就加分，这会让一些风格/语法/写法建议也拿到不错分数。

### 优化方案

#### 把 task-specific 惩罚改掉

不要因为出现 `freq_divbyodd`、`serial2parallel` 这类 task 词就扣分。
更合理的是：

* `cross_task` 高复用经验可以加一点
* `task_specific` 不扣分，只是在分池时决定去向

#### 质量分拆成多轴

建议改成：

* `specificity_score`
* `actionability_score`
* `evidence_score`
* `reusability_score`
* `genericity_penalty`
* `conflict_risk_penalty`

最后再组合。

这样：

* `carry_borrow_width_mismatch` 这种硬经验会高分
* “comment could be clearer” 这种会被 genericity 打下去

---

## 5. `pool_splitter.py`

### 现在的问题

这个文件的规则本身就会制造“core 纯度不够”。

`classify_experience()` 当前逻辑是：

* 低 importance → extended
* category 不在 allowlist → extended
* **specificity 高 → extended**
* generality 高 → core

这相当于：

* 越 task-specific、越贴实际失败的经验，越容易被放到 extended
* 越泛、越像通用建议的经验，越容易去 core

这就是你前面观察到“分池后反而不如单池”的重要根因之一。

### 优化方案

把 core/extended 的定义改掉：

#### 新定义

**Core**：

* 高置信
* 直接 bug-fix / spec-compliance
* 可执行
* 冲突低
* 不一定泛，但必须“主经验”

**Extended**：

* task-local evidence
* style/process/portability
* 仍有用但不够稳定
* 备选路线 / 历史经验

#### 具体规则

可以改成：

* `experience_type in {functional_bug_fix, spec_compliance}` 且质量高 → core
* `implementation_pattern` 视质量决定 core/extended
* `style_or_portability` / `meta_process` → extended
* 明显 task-specific 但高证据、可执行的，也可以进 core，不要天然赶到 extended

---

## 6. `pipeline.py`

### 现在的问题

`pipeline` 本身比较中性，但它暴露出一个流程问题：

* 先 extractor
* 再统一 postprocessor
* 再 manager merge

也就是说，**如果 extractor 先产出了 10 条混杂经验，postprocessor 又拦不住，manager 又偏插入，这三层叠起来池子一定变脏。**

### 优化方案

在 pipeline 里加一层“candidate audit”：

每批经验先输出统计：

* functional_bug_fix 数量
* spec_compliance 数量
* meta_process 数量
* style_or_portability 数量
* 被 reject 的原因分布

这样你每次跑完提取就能看到：

* 是 extractor 在乱提
* 还是 postprocessor 没挡住
* 还是 manager 没合并掉

---

# 最值得立刻改的 6 件事

按收益排序，我建议你先做这 6 个：

### 1. 给 `Experience` 增加 `experience_type`

这是最关键的一步。没有这个字段，后面所有过滤/分池都不稳。

### 2. `quality_score()` 去掉 task-specific 惩罚

这条现在非常伤。它会系统性打压真正有用的 task-local 经验。

### 3. `postprocessor._passes_quality()` 增加 meta/style 拦截

现在这层太松，是旧池污染的重要原因。

### 4. `manager` 改成 merge-first

让“同簇归并”成为默认动作，而不是“低 overlap 就插入”。

### 5. 加语义冲突裁决

不然你永远会积累“不同阶段都像经验”的互斥条目。

### 6. 重写 `pool_splitter` 的 core/extended 规则

现在这套规则会系统性把泛经验送去 core，把 task-specific 主经验送去 extended。

---

# 一句话总结

我的判断很明确：

> **旧版经验池质量不高，确实是经验提取器模块的问题，而且主要不是“提错”，而是“提得太宽、合得太弱、分得不对”。**

它现在的链路更像是在“收集所有看起来像教训的话”，而不是“沉淀少量高置信、可执行、可复用的 canonical 经验”。

你下一步最值得做的，不是继续手工洗池，而是把这条链路改成：

**类型化抽取 → 强过滤 → merge-first → 冲突裁决 → 语义分池**

你要是愿意，我下一步可以直接给你一版**“按文件列出的重构 TODO 清单”**，格式就是：

* `extractor.py` 改哪些字段和 prompt
* `postprocessor.py` 加哪些规则
* `manager.py` 改哪些决策分支
* `quality.py` 怎么重写评分函数
