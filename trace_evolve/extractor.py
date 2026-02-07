"""
经验提取模块 - 从日志文件中提取编程经验
"""
import json
import re
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict
from pathlib import Path

from .config import LLMConfig, EXPERIENCE_EXTRACTION_PROMPT


@dataclass
class Experience:
    """单条经验"""
    id: str
    category: str
    problem: str
    solution: str
    code_pattern: Optional[str] = None
    importance: str = "medium"  # high, medium, low
    source_file: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Experience":
        return cls(
            id=data.get("id", ""),
            category=data.get("category", ""),
            problem=data.get("problem", ""),
            solution=data.get("solution", ""),
            code_pattern=data.get("code_pattern"),
            importance=data.get("importance", "medium"),
            source_file=data.get("source_file"),
        )


class LogParser:
    """日志解析器 - 解析 BangC 问题解决日志"""
    
    # 标记模式
    RESPONSE_START = "@@response"
    RESPONSE_END = "@@endresponse"
    COMPILE_ERROR_PATTERN = r"### 编译错误"
    OPTIMIZATION_PATTERN = r"<analysis title=\"第(\d+)次优化\">"
    
    def parse(self, log_content: str, return_all: bool = True) -> Dict[str, Any]:
        """解析日志内容，提取结构化信息"""
        result = {
            "initial_code": "",
            "iterations": [],
            "final_code": "",
            "errors": [],
            "raw_content": log_content
        }
        
        # 提取所有响应块
        responses = self._extract_responses(log_content)
        if responses:
            result["initial_code"] = responses[0] if responses else ""
            result["final_code"] = responses[-1] if responses else ""
        
        # 提取优化迭代
        iterations = self._extract_iterations(log_content)
        result["iterations"] = iterations
        
        # 提取错误信息
        errors = self._extract_errors(log_content)
        result["errors"] = errors
        
        return result
    
    def _extract_responses(self, content: str) -> List[str]:
        """提取所有响应块"""
        responses = []
        pattern = r'<log>@@response</log>(.*?)<log>@@endresponse</log>'
        matches = re.findall(pattern, content, re.DOTALL)
        responses.extend(matches)
        return responses
    
    def _extract_iterations(self, content: str) -> List[Dict[str, Any]]:
        """提取优化迭代信息"""
        iterations = []
        pattern = r'<analysis title="第(\d+)次优化">(.*?)(?=<analysis title="|### 编译错误|$)'
        matches = re.findall(pattern, content, re.DOTALL)
        
        for match in matches:
            iteration_num = int(match[0])
            iteration_content = match[1]
            
            iterations.append({
                "iteration": iteration_num,
                "content": iteration_content.strip(),
                "has_code": "```mlu" in iteration_content or "```" in iteration_content
            })
        
        return iterations
    
    def _extract_errors(self, content: str) -> List[Dict[str, Any]]:
        """提取错误信息"""
        errors = []
        
        # 提取编译错误
        error_patterns = [
            r'error:(.*?)(?=\n\n|\n[a-zA-Z])',
            r'EXCEPTION_ERROR.*?(?=\n\n|\n[a-zA-Z])',
            r'CN_INVOKE_ERROR.*?(?=\n|$)',
        ]
        
        for pattern in error_patterns:
            matches = re.findall(pattern, content, re.DOTALL)
            for match in matches:
                errors.append({
                    "type": "compile_error" if "error:" in pattern else "runtime_error",
                    "message": match.strip()
                })
        
        return errors


class LLMClient:
    """LLM 客户端 - 调用大模型 API"""
    
    def __init__(self, config: LLMConfig):
        self.config = config
        self._client = None
    
    def _get_client(self):
        """延迟初始化 OpenAI 客户端"""
        if self._client is None:
            try:
                from openai import OpenAI
                self._client = OpenAI(
                    api_key=self.config.api_key,
                    base_url=self.config.api_base if self.config.api_base else None
                )
            except ImportError:
                raise ImportError("请安装 openai 包: pip install openai")
        return self._client
    
    def call(self, prompt: str) -> str:
        """调用 LLM API"""
        client = self._get_client()
        
        response = client.chat.completions.create(
            model=self.config.model,
            messages=[
                {"role": "system", "content": "你是一个BangC/MLU编程专家，擅长分析代码问题和提取编程错误经验。"},
                {"role": "user", "content": prompt}
            ],
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
        )
        
        return response.choices[0].message.content


class ExperienceExtractor:
    """经验提取器 - 从日志中提取经验"""
    
    def __init__(self, llm_config: LLMConfig, max_experiences: int = 10):
        self.llm_client = LLMClient(llm_config)
        self.log_parser = LogParser()
        self.max_experiences = max_experiences
    
    def extract_from_file(self, log_file: str) -> List[Experience]:
        """从单个日志文件提取经验"""
        log_path = Path(log_file)
        if not log_path.exists():
            raise FileNotFoundError(f"日志文件不存在: {log_file}")
        
        log_content = log_path.read_text(encoding='utf-8')
        return self.extract_from_content(log_content, source_file=str(log_path.name))
    
    def extract_from_content(self, log_content: str, source_file: Optional[str] = None) -> List[Experience]:
        """从日志内容提取经验"""
        # 1. 解析日志
        parsed_log = self.log_parser.parse(log_content)
        
        # 2. 构建提示词
        prompt = EXPERIENCE_EXTRACTION_PROMPT.format(
            max_experiences=self.max_experiences,
            log_content=log_content[:15000]  # 限制长度避免超出上下文
        )
        
        # 3. 调用 LLM 提取经验
        response = self.llm_client.call(prompt)
        
        # 4. 解析响应
        experiences = self._parse_llm_response(response, source_file)
        
        return experiences
    
    def _parse_llm_response(self, response: str, source_file: Optional[str] = None) -> List[Experience]:
        """解析 LLM 响应，提取经验列表"""
        experiences = []
        
        # 尝试从响应中提取 JSON
        json_match = re.search(r'```json\s*(.*?)\s*```', response, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
        else:
            # 尝试直接解析整个响应
            json_str = response
        
        try:
            data = json.loads(json_str)
            exp_list = data.get("experiences", [])
            
            for exp_data in exp_list:
                exp_data["source_file"] = source_file
                experiences.append(Experience.from_dict(exp_data))
                
        except json.JSONDecodeError as e:
            print(f"警告: 解析 LLM 响应失败: {e}")
            # 返回空列表，避免中断流程
        
        return experiences
    
    def extract_from_files(self, log_files: List[str]) -> Dict[str, List[Experience]]:
        """从多个日志文件提取经验"""
        all_experiences = {}
        
        for log_file in log_files:
            try:
                experiences = self.extract_from_file(log_file)
                all_experiences[log_file] = experiences
                print(f"从 {log_file} 提取了 {len(experiences)} 条经验")
            except Exception as e:
                print(f"处理 {log_file} 时出错: {e}")
                all_experiences[log_file] = []
        
        return all_experiences
