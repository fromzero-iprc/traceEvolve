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
            "raw_content": log_content,
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
        pattern = r"<log>@@response</log>(.*?)<log>@@endresponse</log>"
        matches = re.findall(pattern, content, re.DOTALL)
        responses.extend(matches)
        return responses

    def _extract_iterations(self, content: str) -> List[Dict[str, Any]]:
        """提取优化迭代信息"""
        iterations = []
        pattern = (
            r'<analysis title="第(\d+)次优化">(.*?)(?=<analysis title="|### 编译错误|$)'
        )
        matches = re.findall(pattern, content, re.DOTALL)

        for match in matches:
            iteration_num = int(match[0])
            iteration_content = match[1]

            iterations.append(
                {
                    "iteration": iteration_num,
                    "content": iteration_content.strip(),
                    "has_code": "```mlu" in iteration_content
                    or "```" in iteration_content,
                }
            )

        return iterations

    def _extract_errors(self, content: str) -> List[Dict[str, Any]]:
        """提取错误信息"""
        errors = []

        # 提取编译错误
        error_patterns = [
            r"error:(.*?)(?=\n\n|\n[a-zA-Z])",
            r"EXCEPTION_ERROR.*?(?=\n\n|\n[a-zA-Z])",
            r"CN_INVOKE_ERROR.*?(?=\n|$)",
        ]

        for pattern in error_patterns:
            matches = re.findall(pattern, content, re.DOTALL)
            for match in matches:
                errors.append(
                    {
                        "type": "compile_error"
                        if "error:" in pattern
                        else "runtime_error",
                        "message": match.strip(),
                    }
                )

        return errors


class QiMengLogParser:
    """日志解析器 - 解析 QiMeng-Agent 日志格式"""

    def parse(self, log_content: str) -> Dict[str, Any]:
        """解析 QiMeng-Agent 日志内容"""
        result = {
            "task_id": "",
            "task_ids": [],
            "task_count": 0,
            "iterations": 0,
            "final_code": "",
            "errors": [],
            "status": "unknown",
            "status_counts": {},
            "tasks": [],
            "feedback": "",  # checker 反馈
            "raw_content": log_content,
        }

        try:
            # 尝试解析 JSON
            data = json.loads(log_content)
        except json.JSONDecodeError:
            result["errors"].append({"type": "parse_error", "message": "无法解析 JSON"})
            return result

        tasks = data.get("tasks", [])
        result["task_count"] = len(tasks)

        for task in tasks:
            task_id = task.get("task_id", "")
            final_result = task.get("final_result") or {}
            task_status = final_result.get("status", "unknown")
            task_summary = {
                "task_id": task_id,
                "iterations": final_result.get("iterations", 0),
                "status": task_status,
                "errors": [],
            }

            result["iterations"] = max(
                result["iterations"], final_result.get("iterations", 0)
            )

            if not result["final_code"] and final_result.get("final_output"):
                result["final_code"] = final_result["final_output"]

            check_result = final_result.get("check_result") or {}

            errors = check_result.get("errors", [])
            for err in errors:
                error_entry = {
                    "task_id": task_id,
                    "type": err.get("type", "unknown"),
                    "message": err.get("description", ""),
                }
                result["errors"].append(error_entry)
                task_summary["errors"].append(error_entry)

            feedback = check_result.get("feedback", "")
            if feedback and not result["feedback"]:
                result["feedback"] = feedback

            verification = check_result.get("verification", {})
            for tool, verif_data in verification.items():
                result_data = verif_data.get("result", {})
                error_type = result_data.get("type", "")

                if error_type == "compile error":
                    error_entry = {
                        "task_id": task_id,
                        "type": "compile_error",
                        "message": result_data.get("detail", ""),
                        "tool": tool,
                    }
                    result["errors"].append(error_entry)
                    task_summary["errors"].append(error_entry)
                elif error_type == "simulation error":
                    error_entry = {
                        "task_id": task_id,
                        "type": "simulation_error",
                        "message": result_data.get("detail", ""),
                        "tool": tool,
                    }
                    result["errors"].append(error_entry)
                    task_summary["errors"].append(error_entry)
                elif error_type == "pass":
                    task_summary["status"] = "pass"

            if task_id:
                result["task_ids"].append(task_id)
                if not result["task_id"]:
                    result["task_id"] = task_id

            result["tasks"].append(task_summary)

        status_counts: Dict[str, int] = {}
        for task_summary in result["tasks"]:
            status = task_summary.get("status", "unknown")
            status_counts[status] = status_counts.get(status, 0) + 1
        result["status_counts"] = status_counts
        if status_counts:
            if status_counts.get("failed"):
                result["status"] = "failed"
            elif status_counts.get("completed"):
                result["status"] = "completed"
            elif status_counts.get("pass"):
                result["status"] = "pass"

        return result


class LLMClient:
    """LLM 客户端 - 调用大模型 API (支持 OpenAI 和火山引擎 Ark)"""

    def __init__(self, config: LLMConfig):
        self.config = config
        self._client = None
        self._use_ark = False

    def _get_client(self):
        """延迟初始化客户端"""
        if self._client is None:
            if self.config.api_base and "ark" in self.config.api_base.lower():
                self._use_ark = True
                try:
                    from volcenginesdkarkruntime import Ark

                    self._client = Ark(api_key=self.config.api_key)
                except ImportError:
                    raise ImportError(
                        "请安装火山引擎 SDK: pip install volcengine-python-sdk"
                    )
            else:
                try:
                    from openai import OpenAI

                    self._client = OpenAI(
                        api_key=self.config.api_key,
                        base_url=self.config.api_base if self.config.api_base else None,
                    )
                except ImportError:
                    raise ImportError("请安装 openai 包: pip install openai")
        return self._client

    def call(self, prompt: str) -> str:
        """调用 LLM API"""
        client = self._get_client()

        messages = [
            {
                "role": "system",
                "content": "You are an expert at extracting high-signal programming lessons from Verilog, QiMeng-Agent, and compiler/debugging logs. Always respond in English and prefer concrete, reusable benchmark lessons over vague advice.",
            },
            {"role": "user", "content": prompt},
        ]

        if self._use_ark:
            response = client.chat.completions.create(
                model=self.config.model,
                messages=messages,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
            )
        else:
            response = client.chat.completions.create(
                model=self.config.model,
                messages=messages,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
            )

        return response.choices[0].message.content


class ExperienceExtractor:
    """经验提取器 - 从日志中提取经验"""

    def __init__(self, llm_config: LLMConfig, max_experiences: int = 10):
        self.llm_client = LLMClient(llm_config)
        self.log_parser = LogParser()
        self.qimeng_log_parser = QiMengLogParser()
        self.max_experiences = max_experiences

    def extract_from_file(
        self, log_file: str, use_qimeng_parser: bool = False
    ) -> List[Experience]:
        """
        从单个日志文件提取经验。

        Args:
            log_file: 日志文件路径
            use_qimeng_parser: 是否使用 QiMeng 日志解析模式

        Returns:
            提取出的经验列表
        """
        log_path = Path(log_file)
        if not log_path.exists():
            raise FileNotFoundError(f"日志文件不存在: {log_file}")

        log_content = log_path.read_text(encoding="utf-8")
        return self.extract_from_content(
            log_content,
            source_file=str(log_path.name),
            use_qimeng_parser=use_qimeng_parser,
        )

    def extract_from_content(
        self,
        log_content: str,
        source_file: Optional[str] = None,
        use_qimeng_parser: bool = False,
        supplemental_context: Optional[str] = None,
    ) -> List[Experience]:
        """
        从日志内容提取经验。

        Args:
            log_content: 原始日志内容
            source_file: 来源文件名
            use_qimeng_parser: 是否使用 QiMeng 日志解析模式
            supplemental_context: 额外补充给 LLM 的上下文，不参与日志解析

        Returns:
            提取出的经验列表
        """
        parser = self.qimeng_log_parser if use_qimeng_parser else self.log_parser
        parsed_log = parser.parse(log_content)

        # 结构化摘要与原始日志一起提供给 LLM，避免只依赖长文本中的零散片段。
        parsed_log_text = json.dumps(parsed_log, ensure_ascii=False, indent=2)
        prompt_content = (
            log_content[:12000]
            + "\n\n=== Parsed Structured Summary ===\n"
            + parsed_log_text[:3000]
        )

        if supplemental_context:
            prompt_content += (
                "\n\n=== Additional Evaluation Context ===\n"
                + supplemental_context[:3000]
            )

        prompt = EXPERIENCE_EXTRACTION_PROMPT.format(
            max_experiences=self.max_experiences,
            log_content=prompt_content,
        )

        response = self.llm_client.call(prompt)

        experiences = self._parse_llm_response(response, source_file)

        return experiences

    def _parse_llm_response(
        self, response: str, source_file: Optional[str] = None
    ) -> List[Experience]:
        """解析 LLM 响应，提取经验列表"""
        json_str = self._extract_json_payload(response)

        for candidate in (
            json_str,
            self._escape_newlines_in_json_strings(json_str),
        ):
            try:
                data = json.loads(candidate)
                return self._experiences_from_data(data, source_file)
            except json.JSONDecodeError:
                continue

        # 最后兜底：宽松提取关键字段（应对 LLM 返回近似 JSON 的情况）
        fallback = self._loose_extract_experiences(json_str, source_file)
        if fallback:
            print(f"警告: JSON 严格解析失败，已使用宽松解析提取 {len(fallback)} 条经验")
        else:
            print("警告: 解析 LLM 响应失败")
        return fallback

    def _extract_json_payload(self, response: str) -> str:
        stripped = response.strip()
        if stripped.startswith("```json"):
            first_newline = stripped.find("\n")
            last_fence = stripped.rfind("\n```")
            if first_newline != -1 and last_fence != -1 and last_fence > first_newline:
                return stripped[first_newline + 1 : last_fence]
        return stripped

    def _experiences_from_data(
        self, data: Dict[str, Any], source_file: Optional[str]
    ) -> List[Experience]:
        experiences: List[Experience] = []
        for exp_data in data.get("experiences", []):
            exp_data["source_file"] = source_file
            experiences.append(Experience.from_dict(exp_data))
        if experiences:
            print(f"成功解析 {len(experiences)} 条经验")
        return experiences

    def _escape_newlines_in_json_strings(self, text: str) -> str:
        out: List[str] = []
        in_string = False
        escape = False
        for ch in text:
            if escape:
                out.append(ch)
                escape = False
                continue
            if ch == "\\":
                out.append(ch)
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                out.append(ch)
                continue
            if in_string and ch == "\n":
                out.append("\\n")
                continue
            if in_string and ch == "\r":
                continue
            out.append(ch)
        return "".join(out)

    def _loose_extract_experiences(
        self, text: str, source_file: Optional[str]
    ) -> List[Experience]:
        experiences: List[Experience] = []
        obj_blocks = re.findall(r"\{\s*\"id\"\s*:\s*\".*?\"\s*\}", text, re.DOTALL)
        for block in obj_blocks:

            def grab(key: str) -> str:
                m = re.search(rf'"{key}"\s*:\s*"(.*?)"', block, re.DOTALL)
                return m.group(1).strip() if m else ""

            exp = Experience.from_dict(
                {
                    "id": grab("id") or "unknown",
                    "category": grab("category") or "unknown",
                    "problem": grab("problem"),
                    "solution": grab("solution"),
                    "code_pattern": grab("code_pattern") or None,
                    "importance": grab("importance") or "medium",
                    "source_file": source_file,
                }
            )
            if exp.problem and exp.solution:
                experiences.append(exp)
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
