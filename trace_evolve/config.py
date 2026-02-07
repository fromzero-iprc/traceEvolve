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
    max_tokens: int = 40960
    
    @classmethod
    def from_env(cls, prefix: str = "LLM") -> "LLMConfig":
        """从环境变量加载配置"""
        return cls(
            api_key=os.getenv(f"{prefix}_API_KEY", ""),
            api_base=os.getenv(f"{prefix}_API_BASE", ""),
            model=os.getenv(f"{prefix}_MODEL", "gpt-4"),
            temperature=float(os.getenv(f"{prefix}_TEMPERATURE", "0.7")),
            max_tokens=int(os.getenv(f"{prefix}_MAX_TOKENS", "40960")),
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
    max_pool_size: int = 100
    
    # 是否保存中间结果
    save_intermediate: bool = True
    intermediate_dir: str = "intermediate_results"


# 提示模板
EXPERIENCE_EXTRACTION_PROMPT = '''你是一个 BangC/MLU 编程专家。请分析以下解决 BangC 问题的日志记录，该日志包含了模型生成代码、代码的编译/执行反馈、以及多轮修正的过程。

请从中提取有价值的经验教训（不超过 {max_experiences} 条）。每条经验应该是针对具体的编译/执行反馈错误进行的，并有一定的普适性。

**示例经验：**
- problem: 编译错误 reference to __mlu_device__ function in __mlu_host__
    - solution: 检查是否用了 __mlu_func__ 而非 __mlu_entry__
- problem: 编译错误 cannot use variable-length arrays
    - solution: 检查 __nram__ 数组是否为常量大小
- problem: 编译错误 Cannot select: ... __fixunsdfsi
    - solution: 检查是否使用了 sqrtf, fmin 等 host 函数
- problem: 运行结果错误（数值不匹配）
    - solution: 检查 tiling 逻辑是否正确处理边界（tile_size 计算）、mean/var 是否累加完整
- problem: 运行结果 inf 或 nan
    - solution: 检查 epsilon 是否正确加到 variance 上，以及 rsqrt 输入是否为正

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
            "category": "问题类别，如：内存管理、编译错误、算法优化、API使用等",
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
'''

EXPERIENCE_MERGE_PROMPT = '''你是一个经验库管理专家。现在有一个已有的经验库，以及从新日志中提取的新经验。你需要判断如何将新经验整合到已有经验库中。

**已有经验库：**
```json
{existing_experiences}
```

**新提取的经验：**
```json
{new_experiences}
```

请分析新旧经验，输出操作指令列表。可执行的操作有：
1. **INSERT** - 插入新经验（当该经验是全新的、没有重复的）
2. **DELETE** - 删除旧经验（当旧经验已过时或被更好的经验替代）
3. **REPLACE** - 替换旧经验（当新经验是旧经验的改进版本）
4. **MERGE** - 合并经验（当多条经验可以整合为一条更全面的经验）
5. **SKIP** - 跳过（当新经验与已有经验完全重复）

**请以 JSON 格式输出操作列表：**
```json
{{
    "operations": [
        {{
            "action": "INSERT/DELETE/REPLACE/MERGE/SKIP",
            "target_ids": ["要操作的经验ID列表，对于INSERT可以为空"],
            "new_experience": {{
                "id": "新经验的ID（如果需要插入或替换）",
                "category": "类别",
                "problem": "问题描述",
                "solution": "解决方案",
                "code_pattern": "代码模式（可选）",
                "importance": "high/medium/low"
            }},
            "reason": "操作原因说明"
        }}
    ],
    "summary": "本次合并操作的总结说明"
}}
```

请确保：
1. 仔细检查语义相似的经验，避免重复
2. 保留更具体、更有价值的版本
3. 合理控制经验库规模，删除低价值或过时的经验
4. 对于每个操作给出清晰的理由
'''
