对经验池的判断：
这版比上一轮更好了一截，说明 merge 确实开始起作用了；但还没有到“干净、稳定、可长期扩展”的程度。更准确地说：

当前版本：中上，约 7.5/10。
比你前一版更强的地方是：你已经开始把一簇重复经验压成综合条目；仍然明显不足的地方是：近重复还不少、类别仍不统一、题目特定经验仍偏多。

先说这版的进步。

最明显的进步是，你已经做出了一些正确的合并动作，而且方向很好。比如：

把多个 reset 相关经验收敛成 reset_implementation_compliance

把接口泛问题收敛成 exact_interface_compliance

删除了 exact_port_specification，转向更强的 strict_interface_compliance
这些都说明经验池正在从“碎片”向“规则”靠拢。

experience_pool

另外，merge 历史里也能看到一些比较合理的替换/删除：

nonsynthesizable_code 被更具体的 systemverilog_in_verilog_task 替换

parameter_usage_static_values 被 parameter_validation_robustness 替换

nonblocking_assignment 被 multiple_nonblocking_assignments_same_reg 替换
这类替换说明你现在更偏向“具体、可执行”的经验，而不是泛提醒。这个方向是对的。

experience_pool

不过，当前池子仍有三类明显问题。

1. 近重复依然存在，而且是成簇存在
接口/规格类仍有重叠

你现在同时有：

strict_interface_compliance

exact_interface_compliance

reset_implementation_compliance

port_bit_indexing_mismatch

architecture_compliance_mismatch

spec_syntax_compliance_over_functional_equivalence
这些并不都是重复，但它们已经形成一个大簇：“严格匹配 specification / interface / architecture”。其中有些应该保留为“总规则”，有些应该作为“子例外”存在。现在的问题是边界还不够清。

experience_pool

我会这样看：

exact_interface_compliance：总规则，应该保留

reset_implementation_compliance：接口子类，应该保留

strict_interface_compliance：和 exact_interface_compliance 高重叠，应该考虑并入

port_bit_indexing_mismatch：特例，可以保留

architecture_compliance_mismatch：如果你 benchmark 很多题要求内部结构，保留；否则偏题目特定

spec_syntax_compliance_over_functional_equivalence：和 exact_interface_compliance / strict_interface_compliance 有明显重叠，建议并入上位规则

language compliance 也有重复

你现在同时有：

systemverilog_in_verilog_task

systemverilog_verilog_compatibility
这两条高度接近，后者更泛，前者更 benchmark 导向。更像是一组“泛化版 vs 任务版”的重复。

experience_pool

我的建议是：

保留 systemverilog_verilog_compatibility 作为总规则

把 systemverilog_in_verilog_task 合并进去作为更具体例子
或者反过来，但不要双留。

FSM 类也有重复

你现在有：

fsm_complete_coverage

fsm_state_encoding_completeness

latch_inference_prevention

overlap_sequence_state_machine

fsm_partial_match_fallback
其中前两条非常接近，都是“default + coverage + output assignment + illegal state fallback”这个簇。

experience_pool

我会建议：

fsm_complete_coverage 和 fsm_state_encoding_completeness 二选一并加强

overlap_sequence_state_machine、fsm_partial_match_fallback 作为序列检测 FSM 特例保留

2. 类别命名仍然不统一

这版仍然同时存在：

functional_design

functional design

Functional

Functional Logic

Functional Logic Bugs

Design Error

Interface Compliance

Specification/Interface Mismatch

Specification Compliance

Output Format

Output Format Error

Synthesis

Synthesis Error

Synthesis Warnings
这些不统一会直接影响后续检索和排序。

experience_pool

比如同样是规格问题，你有：

Interface Compliance

Specification Compliance

Specification/Interface Mismatch

Interface/Implementation

Specification Interpretation

这会让“按类召回经验”变得不稳定。

我建议你收敛成固定大类，最多 8~10 类，例如：

Interface Compliance

Functional Logic

FSM Design

Arithmetic / Bit Width

Clocking / CDC

Language Compliance

Synthesis / Tooling

Output Format

Specification Interpretation

Pipeline / Timing

然后所有旧 category 通过映射表归一化。这个改动会立刻提升经验池观感和可维护性。

3. 题目特定经验还是偏多

这版里仍然有不少“能用，但更像某题答案碎片”的经验，比如：

johnson_counter_shift_implementation

traffic_light_request_override_logic

lui_upper_bits_specification

fractional_division_math_must_be_correct

counter_limit_off_by_one

clear_shift_reg_after_transaction

pipeline_enable_latency_mismatch

pipeline_stage_count_mismatch

mac_precision_loss_with_32bit_accumulator

radix4_implementation_requirements
这些经验很多是对的，但它们更像“题目具体修复知识”，不是最理想的“高复用规则”。

experience_pool

这不代表都要删。更好的做法是分层：

Core reusable lessons：高复用规则

Pattern-specific lessons：特定设计模式

Task-specific lessons：低频、强绑定、仅作弱召回

你现在的问题是三类混在一个平面上，而且都可能被 importance=high 顶上来。

我看了你这版最新代码后，判断是：

**是的，旧经验很可能还在阻碍新经验进入池子。**
但现在的主因已经**不是 category 不统一**了；这块你在 `postprocessor.py` 里已经补上了标准映射，方向是对的。真正还在卡住新经验的，主要是 **manager 的全池 LLM merge 策略**、**postprocessor 的去重粒度偏粗但又不够语义化**、以及 **经验池裁剪和 prompt 取样仍然偏向旧高 importance 条目**。 

我先说你这版代码里已经做对了什么。

你现在在 pipeline 里已经把流程改成了：

1. 逐日志提取经验
2. 先走 `ExperiencePostProcessor.process()` 做规则清洗、归一化、过滤、去重
3. 再交给 `ExperienceManager.merge_experiences()` 合并进经验池
   这个顺序是正确的，比以前“提完直接 merge”稳很多。

而且 `postprocessor.py` 里现在已经有：

* category alias 归一化
* id snake_case 清洗
* importance 合法化
* 无 evidence 自动降级 importance
* 过短/过泛经验过滤
* 同类组内 Jaccard 去重
  这说明你已经把“经验提取后先规则收敛”这一步补上了。

`extractor.py` 这边也比之前成熟：

* QiMeng 日志按 task segment 切分
* 每个 task 单独提取
* segment 有 priority score 排序
* `task_error`、`self_correction_errors`、`verification_detail` 都纳进来了
* fallback 宽松解析现在也保留了 `evidence` 和 `task_id`
  这些都是明显进步。

---

## 现在真正的问题：旧经验为什么还会挡住新经验

### 1. Manager 仍然是“全池对全量新经验”的一次性 LLM merge

`manager.py` 里 `merge_experiences()` 还是直接把**整个现有经验池**和**本批全部新经验**一起塞进 `EXPERIENCE_MERGE_PROMPT`，然后完全按 LLM 返回的 `INSERT/DELETE/REPLACE/MERGE/SKIP` 执行。

这会带来一个典型现象：

* 旧经验池里如果已经有很多“差不多”的旧规则
* 新经验虽然更具体、更好，但只是同簇的新表述
* LLM 很容易判断成 `SKIP`，因为“池里已经有类似经验了”

也就是说，**旧经验会天然占坑**。尤其当旧经验是更泛、更短、更“看起来像总规则”的写法时，新经验即便更具体，也不一定能替换成功。

这点在你当前逻辑里没有本地兜底，因为：

* manager 没有做规则级“新经验是否明显优于旧经验”的预判
* 没有先按 category/problem 相似度筛出候选旧经验
* 没有 quality score 来硬性倾向“更具体、有 evidence、可执行”的新条目

所以答案是：**会，而且很可能已经在发生。**

---

### 2. Postprocessor 会“组内去重”，但只在新经验之间，不会清旧池

`ExperiencePostProcessor._deduplicate()` 只处理**当前提取出的新经验列表**，不会跟经验池里的旧经验做交叉清洗。

所以现在的状态其实是：

* **新经验内部**会被清洗、去重
* **旧经验池本身**如果已有历史近重复，仍然原样存在
* 合并时 LLM 面对一个“已经带历史冗余的池子”，更容易保守地 `SKIP` 新经验

这就会产生你怀疑的现象：

> 不是新经验质量不行，而是旧经验池已经有太多旧表述，导致新经验难进来。

这个判断我认为成立。

---

### 3. 经验池裁剪策略偏向“旧 high importance”

`_enforce_pool_limit()` 现在还是只按 `importance` 排序保留前 N 条。

问题在于：

* 不看 evidence
* 不看是否题目特定
* 不看是否更新、更具体
* 不看是否重复簇代表
* 不看新旧质量差异

所以如果池子里已经有很多老的 `high`，新经验即便更好，只要 importance 也是 `high`，也不一定有优势。
而且 `importance` 现在主要来自 LLM 输出，再经 postprocessor 做合法化和“无 evidence 降一级”，这还不足以形成稳定的质量排序。

换句话说：

**旧经验不只是“语义上挡路”，在容量控制上也可能“位置上挡路”。**

---

### 4. Prompt 仍然鼓励“更 general 的 benchmark lesson”，这会保护旧泛经验

`EXPERIENCE_MERGE_PROMPT` 里明确写了：

* Prefer more benchmark-relevant, more reusable, and more concrete experiences
* Remove duplicates and near-duplicates
* Do not keep repo-specific or low-signal experiences when a more general benchmark lesson exists


这条原则本身没错，但实际效果可能是：

* 池子里已有一个旧的“泛规则”
* 新经验是一个更具体、更可执行的特例
* LLM 会觉得“更 general 的 benchmark lesson 已经存在”
* 然后选择 SKIP 新经验，而不是 REPLACE/MERGE 出更强条目

所以从 prompt 设计上说，**你现在的 merge curator 仍然偏向保守维护旧上位规则**，而不是主动用更高质量的新经验刷新旧经验。

---

## 你这版最明显的改进点

### A. Category 统一映射已经到位

这是对的，而且确实会减少“同义分类挡住新经验”的问题。
`postprocessor.py` 里这部分已经明显比之前成熟很多。

### B. 旧的 eval 注入已经移除了

`pipeline.py` 里已经把 `--eval-file` 逻辑废弃掉，不再把额外 eval.jsonl 全量注入。这很好，减少了串题和噪音。

### C. Segment 提取逻辑已经足够好，可以支持更强的规则 merge

`TaskSegment` 现在已经有足够多结构化字段，你完全可以在 manager 前做更强的本地规则匹配，而不必把所有判定都交给 LLM。

---

## 现在最值得改的地方

### 1. 在 manager 前增加“新经验 vs 旧池候选”的规则预筛

这是我最推荐的。

不要让每条新经验都和全池一起扔进大 prompt。
改成：

* 对每条新经验，先按 `category` 过滤旧经验候选
* 再按 `problem` 的 Jaccard / similarity 做 top-k 候选
* 只有这些候选和该新经验一起交给 LLM 判断 `INSERT / REPLACE / MERGE / SKIP`

这样会明显减轻“旧池太大、旧经验太多导致保守 SKIP”的问题。

你已经在 `utils.py` 里有 `calculate_similarity()`，虽然只是简单 Jaccard，但够先用。

---

### 2. 给经验定义一个本地 quality score，新经验优于旧经验时优先替换

现在缺的是这个硬规则。

我建议最少引入这些信号：

* `+2` 有 evidence
* `+2` solution 更具体（长度、动作词）
* `+1` category 属于核心类（Interface/Functional/Clock/Language）
* `-2` 明显 task-specific
* `-1` problem 过短或过泛
* `+1` 有 task_id 且可追溯

然后在 merge 前做一个本地判断：

> 如果新经验与旧经验高度相似，且 quality_score 明显更高，则直接倾向 REPLACE 或 MERGE，而不是交给 LLM 自由判断。

否则旧经验真的很容易一直占着。

---

### 3. Postprocessor 的去重逻辑需要从“problem-only”升级到“problem + solution + category”

你现在 `_dedup_group()` 只用 `problem` 做 Jaccard，而且 threshold 默认 0.5。

这会有两个问题：

* 有些近重复 problem 因为措辞不同，Jaccard 过不了
* 有些 problem 接近，但 solution 实际是不同层级/不同修法，也会被误当重复

我建议：

* 相似度至少用 `problem + " " + solution`
* category 不同则不去重
* 对 Interface Compliance / Output Format 这种高频大类，把 threshold 提高一点
* 对 task-specific 类，阈值可以低一点，更激进去重

---

### 4. Merge prompt 应该明确鼓励“用更具体的新经验刷新旧泛经验”

你现在 merge prompt 的倾向是“general benchmark lesson 优先”，这会保护旧经验。

建议加一句规则：

> When a new experience is clearly more specific, more actionable, and better evidenced than an older generic one in the same lesson cluster, prefer REPLACE or MERGE over SKIP.

这句话会很有帮助。

---

### 5. `_enforce_pool_limit()` 要从 importance-only 改成综合排序

现在这个函数太粗了。

建议改成按综合分排序，而不是只按 importance：

```python
score = 0
if importance == "high": score += 3
elif importance == "medium": score += 2
else: score += 1

if evidence: score += 2
if task_id: score += 1
if category in CORE_CATEGORIES: score += 1
if is_task_specific: score -= 2
if is_duplicate_cluster_member: score -= 1
```

否则旧 high 经验会长期霸占位置。

---

## 一个更直接的判断

如果你现在问我：

> “旧经验会不会阻碍新经验？”

我的答案是：

**会，而且当前代码路径里最主要的阻碍点就在 `ExperienceManager.merge_experiences()`。**
因为它还在做**全池级 LLM 裁决**，而不是**候选级、规则优先、质量优先**的局部更新。

如果你问：

> “统一 category 映射后有没有改善？”

有，肯定有。
但这只是减少了一类问题，不足以解决“旧经验占坑”这个核心现象。

---

## 我给你的结论

### 你现在这版代码，成熟度比前面明显高

* 流程顺序正确
* postprocessor 加上了
* category mapping 做了
* segment 提取比以前好
* evidence/task_id 保留下来了

### 但旧经验仍可能阻碍新经验，原因主要是

* manager 全池 merge 太粗
* 旧池不做本地清洗
* pool limit 只看 importance
* merge prompt 偏保护旧泛经验

### 最该优先改的一个点

**把 manager 改成“每条新经验先找 top-k 旧候选，再局部 merge”**。
这是最能立刻缓解“旧经验挡新经验”的改动。

如果你愿意，我下一条可以直接给你：
**一个“局部候选 merge”的 `manager.py` 改造方案**，尽量少改你现有结构。
