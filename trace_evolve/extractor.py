"""
经验提取模块 - 从日志文件中提取编程经验
"""

import json
import re
import concurrent.futures
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict, field
from pathlib import Path

from .config import LLMConfig, EXPERIENCE_EXTRACTION_PROMPT, SEGMENT_EXTRACTION_PROMPT


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
    evidence: Optional[str] = None
    task_id: Optional[str] = None
    experience_type: Optional[str] = None
    root_cause_type: Optional[str] = None
    task_scope: Optional[str] = None
    confidence: Optional[float] = None
    canonical: Optional[bool] = None
    evidence_list: Optional[List[str]] = None
    merged_from: Optional[List[str]] = None

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
            evidence=data.get("evidence"),
            task_id=data.get("task_id"),
            experience_type=data.get("experience_type"),
            root_cause_type=data.get("root_cause_type"),
            task_scope=data.get("task_scope"),
            confidence=data.get("confidence"),
            canonical=data.get("canonical"),
            evidence_list=data.get("evidence_list"),
            merged_from=data.get("merged_from"),
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

    def segment_tasks(self, log_content: str) -> List["TaskSegment"]:
        """
        将 QiMeng-Agent 日志按 task 切分为结构化 segment，
        每个 segment 包含单个 task 的完整上下文并附带优先级打分。
        """
        try:
            data = json.loads(log_content)
        except json.JSONDecodeError:
            return []

        tasks = data.get("tasks", [])
        if not tasks and data.get("task_id"):
            tasks = [data]

        segments: List[TaskSegment] = []
        for task in tasks:
            seg = TaskSegment.from_task_dict(task)
            segments.append(seg)

        segments.sort(key=lambda s: s.priority_score, reverse=True)
        return segments


@dataclass
class TaskSegment:
    """单个 task 的结构化 segment，用于 per-task 经验提取。"""

    task_id: str = ""
    question: str = ""
    status: str = "unknown"
    passornot: bool = False
    iterations: int = 0
    errors: List[Dict[str, Any]] = field(default_factory=list)
    feedback: str = ""
    suggestions: List[str] = field(default_factory=list)
    verification_detail: str = ""
    final_code: str = ""
    final_output: str = ""
    self_correction_errors: List[Dict[str, Any]] = field(default_factory=list)
    code_unchanged: Optional[bool] = None
    error_count: int = 0
    task_time_seconds: float = 0.0
    priority_score: int = 0
    task_error: Optional[Dict[str, Any]] = None

    @classmethod
    def from_task_dict(cls, task: Dict[str, Any]) -> "TaskSegment":
        task_id = task.get("task_id", "")
        question = task.get("question", "")
        final_result = task.get("final_result") or {}
        check_result = final_result.get("check_result") or {}
        metrics = task.get("metrics") or {}

        status = final_result.get("status", "unknown")
        passornot = bool(check_result.get("passornot", False))
        iterations = final_result.get("iterations", 0) or metrics.get("iterations", 0)
        errors = list(check_result.get("errors", []))
        feedback = check_result.get("feedback", "")
        suggestions = check_result.get("suggestions", [])
        final_output = final_result.get("final_output", "") or ""

        # agent-level error (e.g. Missing 'subtasks' in response)
        task_error = task.get("error")
        if task_error and isinstance(task_error, dict):
            errors.append(
                {
                    "type": task_error.get("type", "agent_error"),
                    "description": task_error.get("message", str(task_error)),
                }
            )
            if not final_result:
                status = "error"

        # verification detail
        verif_parts: List[str] = []
        verification = check_result.get("verification", {})
        for tool_name, verif_data in verification.items():
            if isinstance(verif_data, dict):
                rd = verif_data.get("result", {})
                vtype = rd.get("type", "")
                vdetail = rd.get("detail", "")
                verif_parts.append(f"[{tool_name}] type={vtype}, detail={vdetail}")
        verification_detail = "\n".join(verif_parts)

        # extract verilog code from final_output
        final_code = cls._extract_verilog(final_output)

        # self_correction_audit
        sc_errors: List[Dict[str, Any]] = []
        code_unchanged: Optional[bool] = None
        for phase in task.get("phases", []):
            if phase.get("phase") == "self_correction_audit":
                pd = phase.get("data", {})
                sc_errors = pd.get("errors_found", [])
                code_unchanged = pd.get("code_unchanged")

        error_count = len(errors) + len(sc_errors)
        task_time_seconds = metrics.get("total_time_seconds", 0.0)

        seg = cls(
            task_id=task_id,
            question=question,
            status=status,
            passornot=passornot,
            iterations=iterations,
            errors=errors,
            feedback=feedback,
            suggestions=suggestions,
            verification_detail=verification_detail,
            final_code=final_code,
            final_output=final_output,
            self_correction_errors=sc_errors,
            code_unchanged=code_unchanged,
            error_count=error_count,
            task_time_seconds=task_time_seconds,
            task_error=task_error if isinstance(task_error, dict) else None,
        )
        seg.priority_score = seg._compute_priority()
        return seg

    def _compute_priority(self) -> int:
        score = 0
        if self.task_error:
            score += 80
        if not self.passornot:
            score += 100
        if self.verification_detail:
            vd_lower = self.verification_detail.lower()
            if "compile error" in vd_lower or "simulation error" in vd_lower:
                score += 60
        if self.errors:
            score += 40 + 10 * len(self.errors)
        if self.self_correction_errors:
            score += 25 + 5 * len(self.self_correction_errors)
        if self.iterations >= 2:
            score += 15
        if self.iterations >= 4:
            score += 15
        if self.task_time_seconds > 180:
            score += 10
        if self.task_time_seconds > 300:
            score += 10
        if self.code_unchanged is False:
            score += 10
        if self.suggestions:
            score += 5
        fb_lower = self.feedback.lower()
        for kw in ("logic", "compile", "simulation", "mismatch", "warning"):
            if kw in fb_lower:
                score += 10
                break
        return score

    def has_verification_failure_signal(self) -> bool:
        detail = (self.verification_detail or "").lower()
        keywords = (
            "compile error",
            "simulation error",
            "function error",
            "mismatch",
            "timeout",
            "failed",
        )
        return any(keyword in detail for keyword in keywords)

    def has_severity(self, levels: List[str]) -> bool:
        normalized = {level.lower() for level in levels}
        for err in self.self_correction_errors:
            severity = str(err.get("severity", "")).strip().lower()
            if severity in normalized:
                return True
        return False

    def is_clean_pass(self) -> bool:
        return (
            self.passornot
            and not self.errors
            and not self.self_correction_errors
            and not self.task_error
            and not self.has_verification_failure_signal()
        )

    def render_for_prompt(self) -> str:
        """渲染为 LLM 可读的结构化文本。"""
        parts = [
            f"=== Task: {self.task_id} ===",
            f"Status: {self.status} | Pass: {self.passornot} | "
            f"Iterations: {self.iterations} | Priority: {self.priority_score}",
        ]
        if self.task_error:
            parts.append(
                f"\n--- Agent Error (task failed before code generation) ---\n"
                f"  Type: {self.task_error.get('type', '?')}\n"
                f"  Message: {self.task_error.get('message', '?')}"
            )
        if self.question:
            parts.append(f"\n--- Question ---\n{self.question}")
        if self.errors:
            parts.append("\n--- Checker Errors ---")
            for err in self.errors:
                parts.append(
                    f"  [{err.get('type', '?')}] {err.get('description', err.get('message', ''))}"
                )
        if self.self_correction_errors:
            parts.append("\n--- Self-Correction Audit Errors ---")
            for err in self.self_correction_errors:
                parts.append(
                    f"  [{err.get('type', '?')}] (severity={err.get('severity', '?')}) "
                    f"{err.get('description', '')}"
                )
        if self.feedback:
            parts.append(f"\n--- Checker Feedback ---\n{self.feedback}")
        if self.suggestions:
            parts.append("\n--- Suggestions ---")
            for s in self.suggestions:
                parts.append(f"  - {s}")
        if self.verification_detail:
            parts.append(f"\n--- Verification ---\n{self.verification_detail}")
        if self.final_code:
            parts.append(f"\n--- Final Verilog Code ---\n{self.final_code}")
        elif self.final_output:
            parts.append(f"\n--- Final Output ---\n{self.final_output}")
        return "\n".join(parts)

    @staticmethod
    def _extract_verilog(text: str) -> str:
        if not text:
            return ""
        pattern = r"```verilog(.*?)```"
        blocks = re.findall(pattern, text, re.DOTALL)
        if blocks:
            return "\n\n".join(b.strip() for b in blocks)
        pattern2 = r"(module\s+\w+(?:\s*#\s*\([^)]*\))?\s*\([^)]*\)\s*;.*?endmodule)"
        modules = re.findall(pattern2, text, re.DOTALL)
        return "\n\n".join(modules) if modules else ""


class LLMClient:
    """LLM 客户端 - 调用大模型 API (支持 OpenAI 和火山引擎 Ark)"""

    def __init__(self, config: LLMConfig):
        self.config = config
        self._client = None
        self._use_ark = False

    def _is_ark_provider(self) -> bool:
        api_base = (self.config.api_base or "").strip().lower()
        return any(token in api_base for token in ("ark", "volcengine", "volces"))

    def ensure_provider_sdk(self) -> None:
        """在真正执行前校验 provider 所需 SDK，缺失时直接报错。"""
        if self._is_ark_provider():
            try:
                import volcenginesdkarkruntime  # noqa: F401
            except ImportError as exc:
                raise RuntimeError(
                    "检测到正在使用火山引擎 API，但未安装对应 SDK。"
                    "请先安装: pip install volcengine-python-sdk"
                ) from exc

    def _get_client(self):
        """延迟初始化客户端"""
        if self._client is None:
            self.ensure_provider_sdk()
            if self._is_ark_provider():
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

    def __init__(
        self,
        llm_config: LLMConfig,
        max_experiences: int = 10,
        max_workers: int = 8,
    ):
        self.llm_client = LLMClient(llm_config)
        self.log_parser = LogParser()
        self.qimeng_log_parser = QiMengLogParser()
        self.max_experiences = max_experiences
        self.max_workers = max_workers

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
    ) -> List[Experience]:
        """
        从日志内容提取经验。

        QiMeng 模式下走 per-segment 并发提取（每个 task 单独调 LLM），
        非 QiMeng 模式保留旧的单轮提取逻辑。

        Args:
            log_content: 原始日志内容
            source_file: 来源文件名
            use_qimeng_parser: 是否使用 QiMeng 日志解析模式

        Returns:
            提取出的经验列表
        """
        if use_qimeng_parser:
            return self._extract_qimeng_per_segment(log_content, source_file)
        return self._extract_legacy(log_content, source_file)

    # ── QiMeng per-segment 并发提取 ──────────────────────────────

    def _extract_qimeng_per_segment(
        self,
        log_content: str,
        source_file: Optional[str],
    ) -> List[Experience]:
        segments = self.qimeng_log_parser.segment_tasks(log_content)
        if not segments:
            print("警告: 未能从日志中解析出任何 task segment")
            return self._extract_legacy(log_content, source_file)

        print(
            f"[Segment] 共 {len(segments)} 个 task segment，"
            f"按 priority 排序: "
            + ", ".join(f"{s.task_id}({s.priority_score})" for s in segments[:10])
            + ("..." if len(segments) > 10 else "")
        )

        all_experiences: List[Experience] = []
        segment_budgets = [(seg, self._extraction_budget(seg)) for seg in segments]
        scheduled_segments = [(seg, budget) for seg, budget in segment_budgets if budget > 0]
        skipped_segments = [seg.task_id for seg, budget in segment_budgets if budget <= 0]
        if skipped_segments:
            print(
                f"[Segment] 跳过 {len(skipped_segments)} 个低信号 segment: "
                + ", ".join(skipped_segments[:10])
                + ("..." if len(skipped_segments) > 10 else "")
            )
        print(f"[Segment] 进入提取的 segment 数量: {len(scheduled_segments)}")

        def _extract_one(seg: TaskSegment, max_experiences: int) -> List[Experience]:
            rendered = self._render_segment_for_extraction(seg)
            prompt = SEGMENT_EXTRACTION_PROMPT.format(
                max_experiences=max_experiences,
                task_content=rendered,
                task_id=seg.task_id,
            )
            try:
                response = self.llm_client.call(prompt)
                exps = self._parse_llm_response(response, source_file)
                for exp in exps:
                    self._hydrate_experience(exp, seg)
                if len(exps) > max_experiences:
                    exps = exps[:max_experiences]
                return exps
            except Exception as e:
                print(f"  [错误] 提取 {seg.task_id} 经验失败: {e}")
                return []

        if not scheduled_segments:
            return []

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=self.max_workers
        ) as executor:
            futures = {
                executor.submit(_extract_one, seg, budget): (seg, budget)
                for seg, budget in scheduled_segments
            }
            for future in concurrent.futures.as_completed(futures):
                seg, budget = futures[future]
                try:
                    exps = future.result()
                    if exps:
                        print(
                            f"  [Segment] {seg.task_id} "
                            f"(priority={seg.priority_score}, budget={budget}): "
                            f"提取了 {len(exps)} 条经验"
                        )
                    all_experiences.extend(exps)
                except Exception as e:
                    print(f"  [错误] {seg.task_id} future 异常: {e}")

        print(f"[Segment] 共提取 {len(all_experiences)} 条候选经验")
        return all_experiences

    # ── 旧的单轮提取逻辑（非 QiMeng 日志） ────────────────────

    def _extract_legacy(
        self,
        log_content: str,
        source_file: Optional[str],
    ) -> List[Experience]:
        parsed_log = self.log_parser.parse(log_content)
        parsed_log_text = json.dumps(parsed_log, ensure_ascii=False, indent=2)
        prompt_content = (
            log_content[:12000]
            + "\n\n=== Parsed Structured Summary ===\n"
            + parsed_log_text[:3000]
        )
        prompt = EXPERIENCE_EXTRACTION_PROMPT.format(
            max_experiences=self.max_experiences,
            log_content=prompt_content,
        )
        response = self.llm_client.call(prompt)
        experiences = self._parse_llm_response(response, source_file)
        for exp in experiences:
            self._hydrate_experience(exp, None)
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
                    "evidence": grab("evidence") or None,
                    "task_id": grab("task_id") or None,
                    "experience_type": grab("experience_type") or None,
                    "root_cause_type": grab("root_cause_type") or None,
                    "task_scope": grab("task_scope") or None,
                    "confidence": self._safe_float(grab("confidence")),
                }
            )
            if exp.problem and exp.solution:
                experiences.append(exp)
        return experiences

    def _extraction_budget(self, seg: TaskSegment) -> int:
        if seg.is_clean_pass():
            return 0
        if seg.task_error:
            return 2
        if not seg.passornot:
            if seg.has_severity(["high", "critical"]) or seg.has_verification_failure_signal():
                return 2
            return 1
        if seg.errors or seg.has_severity(["medium", "high", "critical"]) or seg.has_verification_failure_signal():
            return 1
        return 0

    def _render_segment_for_extraction(self, seg: TaskSegment) -> str:
        parts = [
            f"=== Task: {seg.task_id} ===",
            (
                f"Status: {seg.status} | Pass: {seg.passornot} | "
                f"Iterations: {seg.iterations} | Priority: {seg.priority_score}"
            ),
        ]
        if seg.question:
            parts.append(f"\n--- Question ---\n{seg.question}")
        if seg.errors:
            parts.append("\n--- Checker Errors ---")
            for err in seg.errors:
                parts.append(
                    f"  [{err.get('type', '?')}] {err.get('description', err.get('message', ''))}"
                )
        if seg.self_correction_errors:
            parts.append("\n--- Self-Correction Audit Errors ---")
            for err in seg.self_correction_errors:
                parts.append(
                    f"  [{err.get('type', '?')}] (severity={err.get('severity', '?')}) "
                    f"{err.get('description', '')}"
                )
        if seg.feedback:
            parts.append(f"\n--- Checker Feedback ---\n{seg.feedback}")
        if seg.verification_detail:
            parts.append(f"\n--- Verification ---\n{seg.verification_detail}")
        code_excerpt = self._code_excerpt(seg.final_code or seg.final_output)
        if code_excerpt:
            parts.append(f"\n--- Relevant Code Excerpt ---\n{code_excerpt}")
        return "\n".join(parts)

    @staticmethod
    def _code_excerpt(text: str, head_chars: int = 1400, tail_chars: int = 400) -> str:
        if not text:
            return ""
        compact = text.strip()
        if len(compact) <= head_chars + tail_chars:
            return compact
        return compact[:head_chars] + "\n...\n" + compact[-tail_chars:]

    def _hydrate_experience(
        self,
        exp: Experience,
        seg: Optional[TaskSegment],
    ) -> None:
        if seg and not exp.task_id:
            exp.task_id = seg.task_id
        if exp.evidence and not exp.evidence_list:
            exp.evidence_list = [exp.evidence]
        if exp.confidence is not None:
            try:
                exp.confidence = max(0.0, min(float(exp.confidence), 1.0))
            except (TypeError, ValueError):
                exp.confidence = None
        exp.experience_type = self._normalize_experience_type(exp.experience_type, exp)
        exp.root_cause_type = self._normalize_root_cause_type(exp.root_cause_type, exp)
        exp.task_scope = self._normalize_task_scope(exp.task_scope, exp)
        if exp.confidence is None:
            exp.confidence = 0.8 if exp.evidence else 0.5
        if exp.canonical is None:
            exp.canonical = True

    @staticmethod
    def _normalize_experience_type(raw: Optional[str], exp: Experience) -> Optional[str]:
        normalized = str(raw or "").strip().lower()
        alias_map = {
            "specification_compliance": "spec_compliance",
            "specification compliance": "spec_compliance",
            "interface_compliance": "spec_compliance",
            "functional": "functional_bug_fix",
            "functional_logic": "functional_bug_fix",
            "functional logic": "functional_bug_fix",
            "implementation pattern": "implementation_pattern",
        }
        if normalized in alias_map:
            return alias_map[normalized]
        if normalized:
            return normalized

        category = str(exp.category or "").strip().lower()
        combined = " ".join(
            filter(None, [exp.problem or "", exp.solution or "", exp.id or "", category])
        ).lower()
        if "interface" in category or "spec" in category:
            return "spec_compliance"
        if any(token in combined for token in ("instantiate", "pattern", "architecture", "concatenation")):
            return "implementation_pattern"
        if any(token in category for token in ("language compliance", "verification", "process")):
            return "style_or_portability"
        return "functional_bug_fix"

    @staticmethod
    def _normalize_root_cause_type(raw: Optional[str], exp: Experience) -> Optional[str]:
        normalized = str(raw or "").strip().lower().replace(" ", "_")
        if normalized:
            return normalized

        combined = " ".join(filter(None, [exp.problem or "", exp.solution or "", exp.id or ""])).lower()
        keyword_map = [
            ("reset", "reset"),
            ("width", "width"),
            ("sign", "signedness"),
            ("interface", "interface"),
            ("port", "interface"),
            ("fsm", "fsm"),
            ("state", "fsm"),
            ("cdc", "cdc"),
            ("clock", "timing"),
            ("timing", "timing"),
            ("parameter", "parameterization"),
            ("handshake", "handshake"),
            ("arith", "arithmetic"),
            ("divide", "arithmetic"),
            ("overflow", "arithmetic"),
            ("syntax", "syntax"),
            ("compile", "syntax"),
            ("arch", "architecture"),
        ]
        for keyword, cause in keyword_map:
            if keyword in combined:
                return cause
        return "general_logic"

    @staticmethod
    def _normalize_task_scope(raw: Optional[str], exp: Experience) -> Optional[str]:
        normalized = str(raw or "").strip().lower()
        if normalized in {"task_specific", "cross_task"}:
            return normalized

        task_id = str(exp.task_id or "").strip().lower()
        if not task_id:
            return "cross_task"
        if any(sep in task_id for sep in ",;/ "):
            return "cross_task"
        return "task_specific"

    @staticmethod
    def _safe_float(raw: Optional[str]) -> Optional[float]:
        try:
            if raw in (None, ""):
                return None
            return float(raw)
        except (TypeError, ValueError):
            return None

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
