# AI Handoff — 本轮修改交接文档

> 日期: 2026-03-11
> 会话 ID: db19121e-9cbd-4beb-8f54-c9e23be331d0

---

## 本轮工作目标

将 traceEvolve 的经验提取从 **"整个日志丢给 LLM 一次性提取"** 重构为 **"按 task segment 切分、并发 LLM 调用、per-segment 提取"** 模式，以支持 QiMeng-Agent 日志中 50+ task 的大规模场景，同时提高提取质量。

同步在 QiMeng-Agent 侧优化了日志生成逻辑，减少日志体积（原来单个日志 14000+ 行 / 1.7MB），使下游 traceEvolve 处理更高效。

---

## 已修改文件列表

### traceEvolve 仓库

| 文件 | 修改类型 |
|------|----------|
| `trace_evolve/extractor.py` | **大量新增 + 重构** |
| `trace_evolve/config.py` | 新增 prompt |
| `.cursor/rules/project-basics.mdc` | **新建** |
| `AI_HANDOFF.md` | **新建**（本文件） |

### QiMeng-Agent 仓库（关联修改）

| 文件 | 修改类型 |
|------|----------|
| `qimeng_agent/nodes/composer.py` | 新增 `_subtasks_summary_for_log()` |
| `qimeng_agent/utils/logger.py` | 修复 JSON 格式 |

---

## 各文件具体修改点

### 1. `trace_evolve/extractor.py`

#### 1.1 `Experience` dataclass — 新增 2 个字段

**改动前：**
```python
@dataclass
class Experience:
    id: str
    category: str
    problem: str
    solution: str
    code_pattern: Optional[str] = None
    importance: str = "medium"
    source_file: Optional[str] = None
```

**改动后：**
```python
@dataclass
class Experience:
    id: str
    category: str
    problem: str
    solution: str
    code_pattern: Optional[str] = None
    importance: str = "medium"
    source_file: Optional[str] = None
    evidence: Optional[str] = None    # 新增
    task_id: Optional[str] = None     # 新增
```

**动机：**
- `evidence`: 要求 LLM 在每条经验中附带一个短引用（来自日志的真实错误信息或代码片段），防止 LLM 编造经验。对应 prompt 中的 `"evidence"` 字段要求。
- `task_id`: 标记经验来源的 task，便于后续审计和溯源（"这条经验是从哪个 benchmark task 提取的"）。

**`from_dict()` / `to_dict()` 已同步更新**（`to_dict` 用 `asdict` + 过滤 None，`from_dict` 新增 `data.get("evidence")` 和 `data.get("task_id")`）。

---

#### 1.2 新增 `TaskSegment` dataclass（第 269-430 行）

**改动前：** 不存在。

**改动后：** 新增完整的 `TaskSegment` 数据类，包含：

| 字段 | 来源 | 用途 |
|------|------|------|
| `task_id` | `task["task_id"]` | 标识 |
| `question` | `task["question"]` | 完整原始题面（**不截断**） |
| `status` | `task["final_result"]["status"]` | "completed"/"failed" 等 |
| `passornot` | `check_result["passornot"]` | 布尔值 |
| `iterations` | `final_result["iterations"]` 或 `metrics["iterations"]` | 迭代次数 |
| `errors` | `check_result["errors"]` | Checker 报出的错误列表 |
| `feedback` | `check_result["feedback"]` | Checker 反馈文本 |
| `suggestions` | `check_result["suggestions"]` | Checker 建议列表 |
| `verification_detail` | 遍历 `check_result["verification"]` 拼接 | 各工具验证结果 |
| `final_code` | 从 `final_output` 中提取 Verilog 代码 | **不截断** |
| `final_output` | `final_result["final_output"]` | 完整输出（含代码块外文本） |
| `self_correction_errors` | `phases[phase=="self_correction_audit"]["data"]["errors_found"]` | 自检错误 |
| `code_unchanged` | 同上 `["data"]["code_unchanged"]` | 代码是否未改 |
| `error_count` | `len(errors) + len(self_correction_errors)` | 总错误数 |
| `task_time_seconds` | `metrics["total_time_seconds"]` | 执行耗时 |
| `priority_score` | `_compute_priority()` 计算 | 经验价值打分 |

**设计取舍：**
- `question` 和 `final_code` **不截断**，原因是目标 LLM（Kimi K2-thinking）有 256K token 上下文窗口，单个 task 的内容远不到上限。截断反而丢失关键信息。
- `final_code` 通过 `_extract_verilog()` 从 `final_output` 中提取纯 Verilog 代码（先匹配 ` ```verilog ` 代码块，再 fallback 匹配 `module ... endmodule`），如果提取失败则回退显示完整 `final_output`。

---

#### 1.3 `TaskSegment._compute_priority()` 打分逻辑

```
base = 0
if passornot == false:              +100
if verification 含 compile/sim error: +60
if errors 非空:                      +40 + 10*len(errors)
if self_correction_errors 非空:      +25 + 5*len
if iterations >= 2:                  +15
if iterations >= 4:                  再+15
if task_time > 180s:                 +10
if task_time > 300s:                 再+10
if code_unchanged == false:          +10
if suggestions 非空:                 +5
if feedback 含关键词(logic/compile/simulation/mismatch/warning): +10
```

**动机：** 失败 task 和"通过但有风险"的 task（如 edge_detect，passornot=true 但 self_correction_audit 有 logic_warning）优先提取。当前所有 segment 都会调 LLM，打分主要用于：
1. 日志输出中按优先级排序，方便人工审查
2. 未来 benchmark 扩展到 156 个 task 时，可用于筛选只处理 top-N segment

---

#### 1.4 `TaskSegment.render_for_prompt()` 渲染方法

将 `TaskSegment` 的所有非空字段渲染为结构化纯文本，用 `---` 分隔各 section（Question / Checker Errors / Self-Correction Audit Errors / Feedback / Suggestions / Verification / Final Verilog Code）。

**关键设计：** 不做任何截断，完整呈现给 LLM。

---

#### 1.5 `QiMengLogParser.segment_tasks()` 新方法（第 250-266 行）

**改动前：** `QiMengLogParser` 只有 `parse()` 方法，返回扁平的汇总 dict。

**改动后：** 新增 `segment_tasks(log_content) -> List[TaskSegment]`，遍历 `data["tasks"]`，为每个 task 构造一个 `TaskSegment`，最后按 `priority_score` 降序排列返回。

**动机：** 旧的 `parse()` 把所有 task 信息打平到一个 dict 里，信息严重丢失（只保留了最后一个 task 的 final_code、第一个 task 的 feedback）。新方法保留每个 task 的完整上下文。

---

#### 1.6 `ExperienceExtractor.extract_from_content()` 重构

**改动前：** QiMeng 模式下调 `QiMengLogParser.parse()` 获得汇总 dict，拼接后整体丢给 LLM 做单次提取。50 个 task 的内容很容易超出 token 限制或导致 LLM 注意力分散。

**改动后：**
1. `extract_from_content()` 判断 `use_qimeng_parser=True` 时，调用 `_extract_qimeng_per_segment()`
2. `_extract_qimeng_per_segment()`:
   - 调 `segment_tasks()` 切分出 `TaskSegment` 列表
   - 用 `ThreadPoolExecutor(max_workers=self.max_workers)` 并发对每个 segment 调 LLM
   - 每个 segment 的 prompt 用 `SEGMENT_EXTRACTION_PROMPT.format(max_experiences=3, task_content=seg.render_for_prompt(), task_id=seg.task_id)`
   - 收集所有返回的 `Experience` 列表
3. 如果 `segment_tasks()` 返回空（JSON 解析失败），回退到 `_extract_legacy()`

**动机：**
- 每个 task 独立送 LLM，避免 50 个 task 混在一起导致 LLM 注意力分散
- 并发加速（默认 `max_workers=8`）
- 每个 segment 的 prompt 更聚焦，提取质量更高

**新增构造器参数：** `max_workers: int = 8`

---

#### 1.7 旧逻辑保留为 `_extract_legacy()`

原来的单轮提取逻辑（非 QiMeng 日志使用）被重命名为 `_extract_legacy()`，逻辑未改动。保留为 fallback 路径。

---

### 2. `trace_evolve/config.py`

#### 2.1 新增 `SEGMENT_EXTRACTION_PROMPT`（第 160-205 行）

**改动前：** 不存在。

**改动后：** 新增了专用于单个 task segment 提取的 prompt 模板，关键设计点：
- 要求 LLM 对 cleanly passed task 输出 0 条经验（不强制每个 task 都出经验）
- **强制要求 `evidence` 字段**：必须引用 task 数据中的真实内容（错误信息、代码片段、checker feedback），不允许编造
- 优先级：specification mismatch > functional logic bug > self-correction warnings > output format
- JSON 输出格式中包含 `task_id` 字段
- 花括号转义：prompt 中 JSON 示例用 `{{{{` `}}}}` 转义（因为要经过两次 `.format()` —— 实际上是一次，但 prompt 中 JSON 的花括号需要 `{{`，而内层 JSON 的花括号需要 `{{{{`）

**注意：** 这里的 `{{{{` 转义是因为 Python `.format()` 需要 `{{` 来输出字面 `{`，而 prompt 中 JSON 示例本身就包含 `{`。如果改用 `string.Template` 或 f-string，需要重新处理转义。

---

### 3. QiMeng-Agent 侧关联修改

#### 3.1 `qimeng_agent/nodes/composer.py` — 新增 `_subtasks_summary_for_log()`

**改动前：** `ComposerAgent.exec()` 在 `log_phase("before_compose", ...)` 时直接写入完整的 `raw_subtasks` 列表，每个 subtask 的 `output` 字段包含完整 LLM 输出（含 `<think>...</think>` 标签），每个 subtask 的 `prompt_data.system_prompt` 和 `prompt_data.user_prompt` 也完整写入。50 个 task × 每个 subtask output 5000-20000 字符 → 日志膨胀到 14000+ 行。

**改动后：**
1. 新增顶层函数 `_subtasks_summary_for_log(subtasks, task_question_or_description)`
2. 将每个 subtask 的 `output` 替换为 `output_summary`（含 `length`、`hash`（SHA256 前 16 位）、`preview`（前 200 字符））
3. 将 `prompt_data.system_prompt` 替换为 `system_prompt_summary`（同结构，preview 120 字符）
4. 将 `prompt_data.user_prompt` 替换为 `user_prompt_summary`，**仅当** user_prompt 与 task question 相似度 < 0.60 时才保留 preview（用 `difflib.SequenceMatcher.ratio()` 判断）

**动机：**
- `output` 中 `<think>` 标签内容对经验提取无用，是日志膨胀的主要原因
- `user_prompt` 与 `question` 在大多数 task 中高度重复（只是改写措辞），双存浪费空间
- traceEvolve 不需要 `output` 全文，它通过 `final_result.final_output` 获取最终代码

#### 3.2 `qimeng_agent/utils/logger.py` — 修复 JSON 格式 bug

**改动前（错误版本）：** 在优化日志体积时误改为 `json.dump(log_data, f, ensure_ascii=False, separators=(",", ":"))`，导致输出单行 JSON，人不可读。

**改动后（修复）：** 恢复为 `json.dump(log_data, f, indent=2, ensure_ascii=False)`。

**教训：** 日志体积优化应在 source 端（减少冗余字段），而非在序列化端（去掉缩进）。去掉缩进会导致 traceEvolve 和人工审查都受影响。

---

## 涉及的设计取舍

| 取舍点 | 决策 | 理由 |
|--------|------|------|
| question/final_code 是否截断 | **不截断** | 目标 LLM (Kimi K2-thinking) 有 256K token 上下文，单个 task segment 远小于此上限 |
| priority_score 是否用于筛选 segment | **当前不筛选，全部处理** | 50 个 task 全处理可接受；分数仅用于排序输出和未来扩展 |
| 并发 vs 串行 LLM 调用 | **并发**（ThreadPoolExecutor） | API 预算无限制，并发显著缩短总耗时 |
| evidence 字段来源 | 要求 LLM 从 task 数据中引用 | 防止 LLM 编造经验，提高可审计性 |
| 旧 parse() 方法是否删除 | **保留** | 被 `_extract_legacy()` 使用，非 QiMeng 日志仍走旧路径 |
| 经验合并策略 | 保留现有 LLM-based merge（`manager.py`） | 本轮不修改合并逻辑，只改提取端 |
| prompt_data 摘要阈值 0.60 | 经验值，未做严格调参 | 低于 0.60 表示 user_prompt 包含了显著不同于 question 的额外约束 |

---

## 当前仍存在的不确定点

1. **`SEGMENT_EXTRACTION_PROMPT` 的提取质量未经端到端验证**：prompt 是根据日志结构设计的，但尚未用真实日志跑一轮完整 pipeline 来验证提取结果质量。建议下一轮先跑一次，检查 `intermediate_results/` 中的提取 JSON。

2. **`max_workers=8` 是否合适**：取决于目标 LLM API 的并发限制。如果 Kimi K2-thinking API 限频，可能需要降低或加 retry/backoff。当前 `LLMClient.call()` 没有 retry 逻辑。

3. **`_compute_priority()` 中 feedback 关键词匹配是否充分**：当前只匹配 5 个英文关键词（logic/compile/simulation/mismatch/warning）。QiMeng-Agent 的 checker feedback 语言/格式可能变化，需要根据实际日志补充。

4. **`Experience.evidence` 字段在合并（merge）时如何处理**：`EXPERIENCE_MERGE_PROMPT` 尚未更新以感知 `evidence` 和 `task_id` 字段。LLM merge 时可能丢弃这些字段。（需人工确认是否需要更新 merge prompt）

5. **QiMeng-Agent 侧的 `_subtasks_summary_for_log()` 相似度阈值 0.60**：未经大规模验证，可能需要根据实际日志调整。

6. **`__init__.py` 未导出 `TaskSegment` 和 `QiMengLogParser`**：如果外部代码需要直接 import 这两个类，需要更新 `__all__`。（需人工确认是否需要）

---

## 未完成事项

1. **端到端验证**：用真实 QiMeng-Agent 日志跑一轮 `python -m trace_evolve.cli --files <log> --qimeng --pool <pool>`，检查：
   - segment 切分是否正确
   - priority 打分是否合理
   - LLM 提取的经验质量（evidence 是否真实、problem/solution 是否具体）
   - merge 后经验池的内容

2. **`EXPERIENCE_MERGE_PROMPT` 适配**：merge prompt 中的 JSON schema 示例未包含 `evidence` 和 `task_id`，可能导致 merge 时这些字段被丢弃。需要决定：
   - 是否在 merge prompt 中要求保留 evidence
   - 是否让 merge 后的经验保留原始 task_id 还是标记为 "merged"

3. **LLMClient retry/backoff**：`LLMClient.call()` 无重试逻辑。并发 8 路调用时，API 限频或超时会导致该 segment 经验丢失（仅打印错误）。建议加 `tenacity` 或手写 exponential backoff。

4. **单元测试**：本轮修改未添加任何测试。`TaskSegment.from_task_dict()`、`_compute_priority()`、`render_for_prompt()` 都适合写 pytest 单测。

5. **`__init__.py` 导出更新**：如外部使用需要 `TaskSegment`、`QiMengLogParser`，需更新 `__all__`。

---

## 下一位 Agent 的建议行动顺序

1. **先跑一轮端到端测试**
   ```bash
   export LLM_API_KEY=... LLM_API_BASE=... LLM_MODEL=...
   python -m trace_evolve.cli \
     --files /workspace/I/qimeng6/wangchuanhao/QiMeng-Agent/logs/run_*.json \
     --qimeng --pool experience_pool.json \
     --intermediate-dir intermediate_results --verbose
   ```
   检查 `intermediate_results/` 中的提取 JSON，验证经验质量。

2. **根据验证结果调优 `SEGMENT_EXTRACTION_PROMPT`**：如果提取质量不佳（evidence 空泛、problem 太通用），调整 prompt。

3. **更新 `EXPERIENCE_MERGE_PROMPT`**：使其感知 `evidence` 和 `task_id` 字段。

4. **为 `LLMClient.call()` 添加 retry 逻辑**。

5. **如果 benchmark 扩展到 156 个 task**：考虑在 `_extract_qimeng_per_segment()` 中加入 top-N 筛选（利用 `priority_score`），避免对 156 个 segment 全部调 LLM。

6. **添加 pytest 测试**。

---

## 与本轮修改强相关的注意事项

- **不要修改 `TaskSegment.render_for_prompt()` 使其截断 question/final_code**：这是有意设计，利用大上下文窗口。如果要截断，需要同步评估对提取质量的影响。

- **不要删除 `_extract_legacy()` 方法**：它是非 QiMeng 日志的唯一提取路径，也是 `segment_tasks()` 返回空时的 fallback。

- **`config.py` 中 `SEGMENT_EXTRACTION_PROMPT` 的花括号转义 `{{{{`**：如果修改此 prompt，务必注意花括号转义。Python `.format()` 要求字面 `{` 写成 `{{`。

- **QiMeng-Agent 侧的 `composer.py` 修改会影响日志结构**：如果 QiMeng-Agent 侧恢复写入完整 `output` 或 `prompt_data`，traceEvolve 不受影响（`segment_tasks()` 不依赖这些字段）。但如果 QiMeng-Agent 侧改变了 `final_result`、`check_result`、`phases` 等顶层结构，`TaskSegment.from_task_dict()` 需要同步更新。

- **`ExperienceExtractor.__init__` 新增了 `max_workers` 参数**：`EvolvePipeline` 目前没有传递此参数（使用默认值 8）。如果需要从 CLI 控制并发数，需要在 `EvolveConfig` 和 `cli.py` 中加参数透传。

---

# 第二轮修改交接（2026-03-11）

## 本轮工作目标

解决 **「旧经验挡新经验」** 问题：当新提取的经验更具体、更有 evidence、更可操作时，应能 REPLACE 或 MERGE 掉池中旧的泛化经验，而不是被 LLM 偏向保留旧经验而 SKIP 新经验。

同时提升经验池整体质量：池加载时 category 归一化、去重逻辑升级、pool limit 和 ICL 导出按 `quality_score` 排序。

---

## 已修改文件列表

| 文件 | 修改类型 |
|------|----------|
| `trace_evolve/postprocessor.py` | **新建** |
| `trace_evolve/quality.py` | **新建** |
| `trace_evolve/manager.py` | **大量重构** |
| `trace_evolve/config.py` | 新增 prompt + 更新 merge prompt |
| `trace_evolve/pipeline.py` | 集成 postprocessor |
| `trace_evolve/__init__.py` | 导出 `ExperiencePostProcessor`、`quality_score` |

---

## 各文件具体修改点

### 1. `trace_evolve/postprocessor.py`（新建）

**职责**：在 LLM 提取之后、manager merge 之前，对候选经验做规则化清洗。

**主要逻辑**：
- **归一化**：`_normalize_category()` 将多种写法（如 "functional logic"、"Functional Logic"）映射到标准分类（`_CATEGORY_ALIASES`）；`_normalize_id()` 清洗 ID；`_normalize_importance()` 修正 importance；无 evidence 时 importance 降级。
- **质量过滤**：`_passes_quality()` 过滤 problem/solution 过短、含空泛模式词（如 "be careful"、"test thoroughly"）的经验。
- **组内去重**：按 category 分组，组内用 `_jaccard_combined(problem, solution)` 综合相似度去重；`_dedup_threshold_for_category()` 按 category 调整阈值：高频大类（Interface Compliance、Output Format、Functional Logic）略高（0.55），其他略低（0.45）。
- **normalize_pool(pool)**：静态方法，对池中所有经验的 category 做归一化，供 manager 在 merge 前调用。

**动机**：
- 提取阶段 LLM 输出的 category 写法不统一，merge 时同 category 匹配会失败；池中旧经验可能是历史格式，需要统一。
- 仅用 problem Jaccard 去重容易漏掉「problem 相似但 solution 不同」的重复；problem+solution 综合相似度更准确。
- 高频大类经验多，略高阈值减少误删；task-specific 类别略低阈值更激进去重。

---

### 2. `trace_evolve/quality.py`（新建）

**职责**：计算经验质量分 `quality_score(exp)`，用于 merge 规则预判和 pool limit 排序。

**打分规则**（分数越高越应保留）：
- evidence 有值：+2
- task_id 有值：+1
- solution 长度 > 80 且含 ≥2 个动作词：+2；> 50：+1
- 核心 category（Interface Compliance、Functional Logic、Clock/Timing、Language Compliance）：+1
- problem/solution 含 task-specific 关键词（如 johnson_counter、traffic_light）：-2
- problem 过短（<40）或含过泛模式：-1
- importance：high +3，medium +2，low +1

**动机**：
- 有 evidence 的经验更可信，有 task_id 可溯源。
- 动作词多、solution 长的经验更可操作。
- 核心 category 的规则更通用、复用价值高。
- task-specific 经验（含题目专属词）复用性低，应优先被更通用的经验替换。
- 过泛经验应被裁剪。

**设计取舍**：权重为经验值，未做严格调参。`_TASK_SPECIFIC_KEYWORDS` 仅覆盖部分 benchmark 题目，需根据实际日志补充。

---

### 3. `trace_evolve/manager.py`

#### 3.1 池加载时 category 归一化

**改动前**：`merge_experiences()` 直接对池中经验做 merge，不处理 category 格式。

**改动后**：merge 开始前，若池非空，调用 `ExperiencePostProcessor.normalize_pool(self.pool)` 对池中所有经验的 category 做归一化。

**动机**：池中旧经验可能是历史写入，category 写法与 postprocessor 输出不一致，导致 `_find_candidates()` 按 category 过滤时匹配不到。

---

#### 3.2 候选级 merge（`_find_candidates`、`_merge_single`）

**改动前**：`merge_experiences()` 将全池 + 全部新经验一次性丢给 LLM，用 `EXPERIENCE_MERGE_PROMPT` 做批量决策。

**改动后**：
1. 每条新经验单独处理：`_find_candidates(new_exp, top_k=5)` 按同 category + problem Jaccard 相似度取 top-5 旧候选。
2. 无候选则直接 INSERT。
3. 有候选则调用 `_merge_single()`：先规则预判，不满足则用 `SINGLE_EXPERIENCE_MERGE_PROMPT` 调 LLM 做局部决策。
4. 执行操作后继续下一条新经验。

**动机**：
- 全池批量 merge 时，LLM 容易偏向保留旧经验（"已有类似经验"），导致新经验被 SKIP。
- 候选级 merge 将决策范围缩小到 top-5，且 prompt 明确要求「更具体、更好 evidence 的新经验优先 REPLACE/MERGE」。
- 每条新经验一次 LLM 调用，总调用次数增加，但单次 prompt 更短、决策更聚焦。

---

#### 3.3 规则预判 REPLACE

**改动前**：所有 merge 决策都走 LLM。

**改动后**：在 `_merge_single()` 中，当 `sim >= 0.5`（problem Jaccard）且 `quality_score(new) - quality_score(best_cand) >= 3` 时，直接返回 REPLACE 操作，不调 LLM。

**动机**：新经验明显优于旧候选时，无需 LLM 再判断，减少调用、避免 LLM 偏向旧经验。阈值 3 为经验值，表示质量差足够大。

---

#### 3.4 `_enforce_pool_limit` 和 `get_experiences_for_prompt` 按 quality_score 排序

**改动前**：`_enforce_pool_limit()` 超出 max_pool_size 时，裁剪逻辑未明确（或按插入顺序）；`get_experiences_for_prompt()` 取经验时未按质量排序。

**改动后**：
- `_enforce_pool_limit()`：按 `quality_score` 降序排序，保留 top max_pool_size 条，删除其余。
- `get_experiences_for_prompt()`：按 `quality_score` 降序排序，取前 max_count 条。

**动机**：pool 满时优先保留高质量经验；ICL 导出时优先展示高质量经验。

---

### 4. `trace_evolve/config.py`

#### 4.1 新增 `SINGLE_EXPERIENCE_MERGE_PROMPT`

**改动前**：不存在。

**改动后**：新增单条新经验与若干候选的 merge prompt。要求 LLM 在 INSERT/REPLACE/MERGE/SKIP 中选一，并明确规则：「当新经验更具体、更可操作、更好 evidence 时，优先 REPLACE 或 MERGE」。JSON schema 包含 evidence、task_id 字段。

**动机**：候选级 merge 需要专门的单条决策 prompt，与全池批量 prompt 不同。

---

#### 4.2 更新 `EXPERIENCE_MERGE_PROMPT`

**改动前**：JSON schema 示例未包含 evidence、task_id；规则未强调「新经验优于旧经验时优先 REPLACE/MERGE」。

**改动后**：在 new_experience 示例中增加 evidence、task_id 字段；在 Rules 中增加「When a new experience is clearly more specific, more actionable, and better evidenced than an older generic one in the same lesson cluster, prefer REPLACE or MERGE over SKIP」。

**动机**：与第一轮 AI_HANDOFF 中「EXPERIENCE_MERGE_PROMPT 适配」一致，确保 merge 时保留 evidence/task_id，并引导 LLM 在质量差异明显时选择 REPLACE/MERGE。（注：当前主流程已走候选级 merge，此 prompt 可能仅用于 fallback 或未来批量模式。）

---

### 5. `trace_evolve/pipeline.py`

**改动**：在 `_process_batch()` 中，提取完成后、merge 之前，插入 postprocessor 调用：

```python
postprocessor = ExperiencePostProcessor()
all_experiences = postprocessor.process(all_experiences)
```

**动机**：确保进入 merge 的经验已归一化、过滤、去重，与 manager 的候选匹配逻辑一致。

---

### 6. `trace_evolve/__init__.py`

**改动**：在 `__all__` 中新增 `ExperiencePostProcessor`、`quality_score` 的导出。

**动机**：外部可能需要直接使用 postprocessor 或 quality_score。

---

## 涉及的设计取舍

| 取舍点 | 决策 | 理由 |
|--------|------|------|
| 每条新经验一次 LLM 调用 | **采用** | 决策更聚焦，避免全池批量时 LLM 偏向旧经验；调用次数增加但可接受 |
| 规则预判阈值 quality 差 ≥ 3 | **采用** | 经验值，表示质量差足够大才跳过 LLM；可后续调参 |
| problem Jaccard ≥ 0.5 才规则预判 | **采用** | 避免对不相似的经验误 REPLACE |
| 去重用 problem+solution 综合 Jaccard | **采用** | 仅 problem 容易漏掉 solution 不同的重复 |
| 按 category 调整去重阈值 | **采用** | 高频大类略严格，task-specific 略宽松 |
| EXPERIENCE_MERGE_PROMPT 仍保留 | **保留** | 当前主流程用 SINGLE_EXPERIENCE_MERGE_PROMPT，但批量 prompt 可能用于未来扩展 |

---

## 当前仍存在的不确定点

1. **quality_score 权重**：evidence +2、task_id +1、task-specific -2 等为经验值，未经 A/B 验证。若 pool 裁剪或 REPLACE 预判效果不佳，需调参。

2. **`_TASK_SPECIFIC_KEYWORDS` 是否完整**：仅覆盖部分 benchmark 题目（如 johnson_counter、traffic_light）。若新题目有专属词，需补充。

3. **规则预判阈值 3 和 0.5**：未做严格调参。若 REPLACE 过少（仍被 LLM SKIP）或过多（误替换），需调整。

4. **SINGLE_EXPERIENCE_MERGE_PROMPT 的 LLM 决策质量**：尚未端到端验证。若 LLM 仍偏向 SKIP 新经验，需进一步强化 prompt 或增加规则预判覆盖。

---

## 未完成事项

1. **端到端验证**：用真实日志跑一轮，检查：
   - 规则预判 REPLACE 是否触发、是否合理
   - pool limit 裁剪后池中经验质量
   - ICL 导出时经验排序是否合理

2. **quality_score 调参**：若效果不佳，可考虑增加/减少某些信号权重，或扩展 `_TASK_SPECIFIC_KEYWORDS`。

3. **LLM retry**：`LLMClient.call()` 仍无 retry，并发时 API 限频可能导致单条 merge 失败后 fallback INSERT。

---

## 下一位 Agent 的建议行动顺序

1. **端到端跑一轮**：`python -m trace_evolve.cli --files <log> --qimeng --pool <pool> --intermediate-dir intermediate_results`，观察：
   - postprocessor 清洗前后数量
   - merge 时是否有规则预判 REPLACE 的打印
   - 最终池中经验质量

2. **检查 intermediate_results**：查看 merge 结果 JSON，确认 REPLACE/MERGE 比例是否合理。

3. **若新经验仍被 SKIP 过多**：考虑降低规则预判阈值（如 quality 差 ≥ 2）或强化 SINGLE_EXPERIENCE_MERGE_PROMPT。

4. **若 pool 裁剪效果不佳**：调整 quality_score 权重或扩展 task-specific 关键词。

---

## 与本轮修改强相关的注意事项

- **不要删除规则预判 REPLACE**：这是解决「旧经验挡新经验」的核心机制之一。若删除，需确保 LLM prompt 能等效替代。

- **不要跳过 `normalize_pool()`**：池中旧经验 category 可能与 postprocessor 输出不一致，会导致 `_find_candidates()` 匹配失败，新经验全部 INSERT，池膨胀。

- **修改 quality_score 时需同步评估**：`_enforce_pool_limit`、`get_experiences_for_prompt`、规则预判都依赖此函数。改动后需检查 pool 裁剪和 REPLACE 预判行为。

- **postprocessor 的 `_CATEGORY_ALIASES` 与 quality 的 `_CORE_CATEGORIES` 需保持一致**：若新增标准 category，两处都要更新。

- **`calculate_similarity` 在 utils.py**：manager 的 `_find_candidates` 和规则预判使用 `utils.calculate_similarity`（problem Jaccard），与 postprocessor 的 `_jaccard_combined` 不同。前者仅 problem，后者 problem+solution 加权。不要混淆。
