"""
配置文件 - 定义 LLM API 配置和提示模板
"""

import os
from typing import Optional
from dataclasses import dataclass, field


@dataclass
class LLMConfig:
    """LLM 配置"""

    api_key: str = ""
    api_base: str = ""
    model: str = "gpt-4"
    temperature: float = 0.7
    max_tokens: int = 16384

    def __post_init__(self):
        # Ark endpoint currently rejects max_tokens > 32768.
        # Keep this clamp centralized so callers using from_env/direct ctor are both safe.
        if self.max_tokens <= 0:
            self.max_tokens = 16384
        self.max_tokens = min(self.max_tokens, 32768)

    @classmethod
    def from_env(cls, prefix: str = "LLM") -> "LLMConfig":
        """从环境变量加载配置"""
        return cls(
            api_key=os.getenv(f"{prefix}_API_KEY", ""),
            api_base=os.getenv(f"{prefix}_API_BASE", ""),
            model=os.getenv(f"{prefix}_MODEL", "gpt-4"),
            temperature=float(os.getenv(f"{prefix}_TEMPERATURE", "0.7")),
            max_tokens=int(os.getenv(f"{prefix}_MAX_TOKENS", "16384")),
        )


@dataclass
class EvolveConfig:
    """经验演化配置"""

    # LLM 配置
    extractor_llm: LLMConfig = field(default_factory=LLMConfig)
    manager_llm: LLMConfig = field(default_factory=LLMConfig)

    # 经验池路径
    experience_pool_path: str = "experience_pool.json"

    # 每次从日志中提取的最大经验数
    max_experiences_per_log: int = 10

    # 经验池最大容量
    max_pool_size: int = 450

    # 是否保存中间结果
    save_intermediate: bool = True
    intermediate_dir: str = "intermediate_results"


# 提示模板 - 英文版本（默认）
EXPERIENCE_EXTRACTION_PROMPT = """You are an expert at distilling reusable Verilog benchmark lessons from agent execution logs.

The input may contain:
- raw task prompts
- model-generated Verilog
- checker or evaluator feedback
- iterative repair history
- a parsed structured summary generated from the log

Your job is to extract a SMALL number of high-value canonical lessons (no more than {max_experiences} items) that improve future single-file Verilog benchmark generation.

Prioritize lessons in this order:
1. Interface/specification mistakes: wrong module name, wrong ports, wrong widths, wrong reset behavior
2. Functional design mistakes: FSM bugs, FIFO full/empty logic, signed/unsigned issues, counter overflow, CDC, pipeline staging
3. Concrete implementation patterns that directly fix recurring benchmark failures

What to avoid:
- Do NOT extract generic repo-management advice unless the log clearly proves it caused the failure
- Do NOT extract lessons about compiling multiple Verilog files together unless the benchmark truly depends on multiple source files
- Do NOT produce vague lessons like 'be careful' or 'check syntax'
- Do NOT output style, readability, or review-comment advice as main experiences
- Do NOT output process reminders like 'always verify' or 'clarify spec' as main experiences
- Do NOT output Chinese; all fields and all text must be English

When a parsed structured summary is present, trust it more than noisy raw log fragments.
Prefer benchmark-specific lessons over generic advice.
If multiple failures share one root cause, produce one stronger canonical lesson instead of duplicates.
Only emit experiences that have:
- a concrete root cause
- a concrete fix action
- direct evidence from the log

Examples of good lessons:
- problem: Module name does not exactly match the required top module name
  solution: Copy the required module name from the task statement verbatim and verify case sensitivity before finalizing code
- problem: FIFO full and empty logic uses incorrect pointer comparison, causing data corruption
  solution: Use one extra pointer bit or Gray-coded synchronized pointers for asynchronous FIFO full/empty detection
- problem: YAML mapping fails because explanatory text and code are mixed in the same fenced block
  solution: Keep executable Verilog inside the fenced block only, and move explanations outside the code block

Log content:
```
{log_content}
```

Output requirements:
- Return valid JSON only
- Keep code_pattern very short or omit it entirely
- Use short, concrete, reusable one-line problem and solution fields
- Keep category names in English and reasonably normalized
- Prefer ids in lowercase snake_case
- Only emit these experience_type values: spec_compliance, functional_bug_fix, implementation_pattern

```json
{{
  "experiences": [
    {{
      "id": "unique_id",
      "category": "category name",
      "experience_type": "spec_compliance | functional_bug_fix | implementation_pattern",
      "root_cause_type": "reset | width | interface | arithmetic | fsm | cdc | timing | syntax | parameterization | handshake | signedness | architecture | general_logic",
      "task_scope": "task_specific | cross_task",
      "problem": "one line problem description",
      "solution": "one line solution",
      "evidence": "short direct quote or precise reference from the log",
      "importance": "high/medium/low",
      "confidence": 0.0
    }}
  ]
}}
```
"""

# 中文版本（保留）
EXPERIENCE_EXTRACTION_PROMPT_CN = """你是一个 Verilog 编程专家。请分析以下解决 Verilog 问题的日志记录，该日志包含了模型生成代码、代码的编译/仿真反馈、以及多轮修正的过程。

请从中提取有价值的经验教训（不超过 {max_experiences} 条）。每条经验应该是针对具体的编译/仿真反馈错误进行的，并有一定的普适性。

**示例经验：**
- problem: 编译错误 module name mismatch
    - solution: 检查模块名是否与测试bench期望的一致
- problem: 编译错误 port width mismatch
    - solution: 检查信号位宽是否匹配
- problem: 编译错误 syntax error near
    - solution: 检查是否有缺失的分号、括号或拼写错误
- problem: 仿真错误 result mismatch
    - solution: 检查时序逻辑和组合逻辑的边界条件  
- problem: 仿真错误 inf or nan
    - solution: 检查除零操作和负数开方

**日志内容：**
```
{log_content}
```

**请以 JSON 格式输出经验列表，格式如下：**
```json
{{
    "experiences": [
        {{
            "id": "唯一标识符",
            "category": "问题类别，如：语法错误、编译错误、仿真错误、时序问题、模块实例化等",
            "problem": "问题的描述",
            "solution": "解决方案",
            "code_pattern": "可选：相关的代码模式或示例片段",
            "importance": "high/medium/low - 表示该经验的重要程度"
        }}
    ]
}}
```

请确保：
- 如果这个日志最终生成出了正确代码，那么尤其要关注日志中的迭代经验，因为说明这种迭代最终能导出正确的结果
"""

SEGMENT_EXTRACTION_PROMPT = """You are an expert at distilling reusable Verilog benchmark lessons from a SINGLE task's execution record.

Below is the structured execution record of one Verilog benchmark task.
It contains the original specification, checker errors, self-correction audit results,
checker feedback, verification outcomes, and the final generated Verilog code.

Your job: extract 0 to {max_experiences} high-value canonical lessons from THIS task.

CRITICAL RULES:
- If this task passed cleanly with no errors, warnings, or interesting patterns, output 0 experiences.
- Every experience MUST include an "evidence" field: a SHORT quote or reference from the task data
  that proves this lesson is real (e.g. the actual error message, the problematic code snippet, or
  the checker feedback sentence). Do NOT fabricate evidence.
- Do NOT produce vague lessons like "be careful with syntax" or "test thoroughly".
- Each lesson must be specific enough that a Verilog code generator can act on it.
- All text must be in English.
- Prefer lowercase_snake_case for ids.
- Only emit lessons whose experience_type is one of:
  - spec_compliance
  - functional_bug_fix
  - implementation_pattern
- Do NOT emit style_or_portability, meta_process, reviewer reminders, or generic verification advice as experiences.
- If two observations share the same root cause, output one stronger canonical lesson instead of multiple variants.

Prioritize:
1. Specification/interface mismatches (wrong module name, wrong ports, wrong widths, wrong reset polarity)
2. Functional logic bugs (FSM errors, FIFO logic, signed/unsigned, counter overflow, CDC, pipeline)
3. Concrete implementation patterns that directly repair a recurring failure mode

Every experience must answer all of these:
- What is the minimal root cause?
- What exact code action fixes it?
- What evidence proves this lesson?

Task execution record:
```
{task_content}
```

Output valid JSON only:
```json
{{{{
  "experiences": [
    {{{{
      "id": "lowercase_snake_case_id",
      "category": "category name",
      "experience_type": "spec_compliance | functional_bug_fix | implementation_pattern",
      "root_cause_type": "reset | width | interface | arithmetic | fsm | cdc | timing | syntax | parameterization | handshake | signedness | architecture | general_logic",
      "task_scope": "task_specific | cross_task",
      "problem": "one-line problem description",
      "solution": "one-line actionable solution",
      "evidence": "short quote from the task data proving this lesson",
      "importance": "high/medium/low",
      "confidence": 0.0,
      "task_id": "{task_id}"
    }}}}
  ]
}}}}
```
"""

SINGLE_EXPERIENCE_MERGE_PROMPT = """You are an experience-pool curator for Verilog benchmark lessons.

Candidate existing experiences (same task/category or semantic overlap):
```json
{existing_candidates}
```

New experience to integrate:
```json
{new_experience}
```

Decide ONE action:
1. INSERT - add as truly new (no overlap with candidates)
2. REPLACE - replace one candidate with this better version (target_ids: [old_id])
3. MERGE - merge with one or more candidates into one stronger experience
4. SKIP - redundant with existing

Rules:
- Prefer MERGE over INSERT when the new experience shares the same root cause / task cluster and mainly adds evidence or tighter wording.
- When the new experience is clearly more specific, more actionable, and better evidenced than an older generic one in the same cluster, prefer REPLACE or MERGE over SKIP.
- Prefer spec_compliance over generic advice, and prefer direct pass-linked evidence over intermediate hints.
- Output valid JSON only. Return exactly one operation.

```json
{{
  "operations": [
    {{
      "action": "INSERT/REPLACE/MERGE/SKIP",
      "target_ids": ["existing_id_or_empty"],
      "new_experience": {{
        "id": "new_id",
        "category": "category",
        "experience_type": "spec_compliance | functional_bug_fix | implementation_pattern",
        "root_cause_type": "reset | width | interface | arithmetic | fsm | cdc | timing | syntax | parameterization | handshake | signedness | architecture | general_logic",
        "task_scope": "task_specific | cross_task",
        "problem": "problem description",
        "solution": "solution description",
        "evidence": "optional",
        "task_id": "optional",
        "importance": "high/medium/low",
        "confidence": 0.0
      }},
      "reason": "short explanation"
    }}
  ]
}}
```
"""

EXPERIENCE_MERGE_PROMPT = """You are an experience-pool curator for Verilog benchmark lessons.

Existing experience pool:
```json
{existing_experiences}
```

Newly extracted experiences:
```json
{new_experiences}
```

Decide how to integrate the new experiences into the pool.
Allowed actions:
1. INSERT - add a truly new experience
2. DELETE - remove an outdated or lower-value existing experience
3. REPLACE - replace an existing experience with a better new version
4. MERGE - merge overlapping experiences into one stronger experience
5. SKIP - ignore a redundant new experience

Rules:
- Output valid JSON only
- All text must be in English
- Keep reason concise and specific
- Prefer more benchmark-relevant, more reusable, and more concrete experiences
- Remove duplicates and near-duplicates
- Prefer normalized English categories
- Do not keep repo-specific or low-signal experiences when a more general benchmark lesson exists
- When a new experience is clearly more specific, more actionable, and better evidenced than an older generic one in the same lesson cluster, prefer REPLACE or MERGE over SKIP
- Prefer MERGE over INSERT when the new item belongs to an existing task/root-cause cluster and mainly contributes evidence or canonical wording
- Prefer spec_compliance and functional_bug_fix over style/process advice

```json
{{
  "operations": [
    {{
      "action": "INSERT/DELETE/REPLACE/MERGE/SKIP",
      "target_ids": ["existing_id_1"],
      "new_experience": {{
        "id": "new_id",
        "category": "category",
        "experience_type": "spec_compliance | functional_bug_fix | implementation_pattern",
        "root_cause_type": "reset | width | interface | arithmetic | fsm | cdc | timing | syntax | parameterization | handshake | signedness | architecture | general_logic",
        "task_scope": "task_specific | cross_task",
        "problem": "problem description",
        "solution": "solution description",
        "code_pattern": "optional short pattern",
        "importance": "high/medium/low",
        "evidence": "optional short quote",
        "task_id": "optional task id",
        "confidence": 0.0
      }},
      "reason": "short English explanation"
    }}
  ],
  "summary": "short English summary"
}}
```
"""
