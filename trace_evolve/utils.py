"""
工具函数和辅助类
"""
import json
import uuid
from typing import List, Dict, Any, Optional
from pathlib import Path


def generate_experience_id() -> str:
    """生成唯一的经验ID"""
    return f"exp_{uuid.uuid4().hex[:8]}"


def truncate_text(text: str, max_length: int = 500, suffix: str = "...") -> str:
    """截断文本"""
    if len(text) <= max_length:
        return text
    return text[:max_length - len(suffix)] + suffix


def load_json_file(file_path: str) -> Dict[str, Any]:
    """加载 JSON 文件"""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {file_path}")
    return json.loads(path.read_text(encoding='utf-8'))


def save_json_file(data: Dict[str, Any], file_path: str, indent: int = 2):
    """保存 JSON 文件"""
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=indent), encoding='utf-8')


def extract_code_blocks(text: str, language: Optional[str] = None) -> List[str]:
    """从文本中提取代码块"""
    import re
    
    if language:
        pattern = rf'```{language}\s*(.*?)\s*```'
    else:
        pattern = r'```(?:\w+)?\s*(.*?)\s*```'
    
    matches = re.findall(pattern, text, re.DOTALL)
    return matches


def calculate_similarity(text1: str, text2: str) -> float:
    """
    计算两段文本的相似度 (简单的 Jaccard 相似度)
    用于快速判断经验是否重复
    """
    words1 = set(text1.lower().split())
    words2 = set(text2.lower().split())
    
    if not words1 or not words2:
        return 0.0
    
    intersection = words1 & words2
    union = words1 | words2
    
    return len(intersection) / len(union)


class MockLLMClient:
    """
    模拟 LLM 客户端 - 用于测试和演示
    当没有配置真实 API 时使用
    """
    
    def __init__(self):
        self.call_count = 0
    
    def call(self, prompt: str) -> str:
        """模拟 LLM 调用"""
        self.call_count += 1
        
        # 判断是提取经验还是合并经验
        if "请从中提取有价值的编程经验" in prompt:
            return self._mock_extraction_response()
        elif "判断如何将新经验整合" in prompt:
            return self._mock_merge_response()
        else:
            return '{"message": "unknown prompt type"}'
    
    def _mock_extraction_response(self) -> str:
        """模拟经验提取响应"""
        exp_id = generate_experience_id()
        return f'''```json
{{
    "experiences": [
        {{
            "id": "{exp_id}",
            "category": "内存管理",
            "problem": "NRAM空间不足导致内存越界错误",
            "solution": "将大数据分块处理，使用循环逐块加载和处理数据",
            "code_pattern": "const uint32_t block_size = NRAM_SIZE / 2;",
            "importance": "high"
        }},
        {{
            "id": "{generate_experience_id()}",
            "category": "编译错误",
            "problem": "变量名冲突导致重定义错误",
            "solution": "重命名变量避免与系统类型或其他变量冲突",
            "code_pattern": "const int input_dim = 393216; // 避免与 cnrtDim3_t dim 冲突",
            "importance": "medium"
        }},
        {{
            "id": "{generate_experience_id()}",
            "category": "API使用",
            "problem": "使用了未声明的标准库函数",
            "solution": "在MLU环境中使用 fmin/fmax 替代 min/max",
            "importance": "medium"
        }}
    ]
}}
```'''
    
    def _mock_merge_response(self) -> str:
        """模拟经验合并响应"""
        return '''```json
{
    "operations": [
        {
            "action": "INSERT",
            "target_ids": [],
            "new_experience": null,
            "reason": "新经验与现有经验不重复，直接插入"
        }
    ],
    "summary": "插入了新的经验"
}
```'''


def create_sample_experience_pool(output_path: str):
    """创建示例经验池文件"""
    sample_experiences = {
        "experiences": {
            "exp_sample_001": {
                "id": "exp_sample_001",
                "category": "内存管理",
                "problem": "NRAM空间不足导致内存越界错误",
                "solution": "将大数据分块处理，使用循环逐块加载和处理数据，确保每次处理的数据量不超过NRAM容量",
                "code_pattern": "const uint32_t block_size = NRAM_SIZE / 2;\nfor (uint32_t block = 0; block < blocks; ++block) { ... }",
                "importance": "high",
                "source_file": "sample"
            },
            "exp_sample_002": {
                "id": "exp_sample_002",
                "category": "编译错误",
                "problem": "变量名与系统类型冲突",
                "solution": "检查变量命名是否与MLU SDK中的类型名冲突，使用更具描述性的变量名",
                "importance": "medium",
                "source_file": "sample"
            },
            "exp_sample_003": {
                "id": "exp_sample_003",
                "category": "任务划分",
                "problem": "多核并行任务分配不均匀",
                "solution": "使用余数处理确保任务均匀分配：前rem个core每个多处理1个样本",
                "code_pattern": "uint32_t batch_per_core = batch_size / core_num;\nuint32_t rem = batch_size % core_num;",
                "importance": "medium",
                "source_file": "sample"
            }
        },
        "history": [],
        "metadata": {
            "last_updated": "2024-01-01T00:00:00",
            "total_experiences": 3
        }
    }
    
    save_json_file(sample_experiences, output_path)
    print(f"示例经验池已创建: {output_path}")


def validate_experience_pool(pool_path: str) -> bool:
    """验证经验池文件格式"""
    try:
        data = load_json_file(pool_path)
        
        # 检查必要字段
        if "experiences" not in data:
            print("错误: 缺少 'experiences' 字段")
            return False
        
        # 检查每个经验的格式
        for exp_id, exp in data["experiences"].items():
            required_fields = ["id", "category", "problem", "solution"]
            for field in required_fields:
                if field not in exp:
                    print(f"错误: 经验 {exp_id} 缺少必要字段 '{field}'")
                    return False
        
        print(f"经验池格式验证通过，共 {len(data['experiences'])} 条经验")
        return True
        
    except json.JSONDecodeError as e:
        print(f"错误: JSON 解析失败 - {e}")
        return False
    except Exception as e:
        print(f"错误: {e}")
        return False
