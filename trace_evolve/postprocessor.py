"""
经验后处理器 — 在 LLM 提取之后、manager merge 之前执行结构化清洗。

职责：
- category / type / root cause 归一化
- 主池质量过滤（拒绝 meta/style/process）
- same-batch 近重复去重
- 为 merge 阶段补齐 evidence_list / merged_from 等元数据
"""

import re
from typing import Dict, List, Optional, Set, Tuple

from .extractor import Experience
from .quality import quality_score


_CATEGORY_ALIASES: Dict[str, str] = {
    "functional_design": "Functional Logic",
    "functional design": "Functional Logic",
    "functional logic": "Functional Logic",
    "functional logic bugs": "Functional Logic",
    "functional_logic": "Functional Logic",
    "functional_logic_bugs": "Functional Logic",
    "functional": "Functional Logic",
    "design error": "Functional Logic",
    "task-specific design errors": "Functional Logic",
    "logic errors": "Functional Logic",
    "control logic": "Functional Logic",
    "design pattern": "Functional Logic",
    "fifo design error": "Functional Logic",
    "data_path_design": "Functional Logic",
    "control_logic_design": "Functional Logic",
    "implementation_pattern": "Functional Logic",
    "specification/interface mismatch": "Interface Compliance",
    "specification mismatch": "Interface Compliance",
    "interface/specification": "Interface Compliance",
    "specification compliance": "Interface Compliance",
    "specification interpretation": "Interface Compliance",
    "interface/implementation": "Interface Compliance",
    "interface": "Interface Compliance",
    "interface_compliance": "Interface Compliance",
    "specification/interface_mismatches": "Interface Compliance",
    "specification_interface": "Interface Compliance",
    "specification/interface": "Interface Compliance",
    "specification_compliance": "Interface Compliance",
    "specification_mismatch": "Interface Compliance",
    "interface_specification": "Interface Compliance",
    "interface_design": "Interface Compliance",
    "functional_specification": "Interface Compliance",
    "module_interface": "Interface Compliance",
    "port_mismatch": "Interface Compliance",
    "compile error": "Compilation Error",
    "compilation error": "Compilation Error",
    "syntax errors": "Compilation Error",
    "syntax": "Compilation Error",
    "syntax_error": "Compilation Error",
    "state machine error": "State Machine",
    "state machine": "State Machine",
    "arithmetic design error": "Arithmetic",
    "arithmetic": "Arithmetic",
    "bit width management": "Arithmetic",
    "clocking": "Clock/Timing",
    "clock domain crossing": "Clock/Timing",
    "pipeline synchronization": "Clock/Timing",
    "pipeline design": "Clock/Timing",
    "output-format": "Output Format",
    "output format error": "Output Format",
    "output format": "Output Format",
    "process improvement": "Process/Methodology",
    "verification": "Verification",
    "verification/functional": "Verification",
    "synthesis": "Synthesis",
    "synthesis error": "Synthesis",
    "synthesis warnings": "Synthesis",
    "language compliance": "Language Compliance",
    "coding_style": "Language Compliance",
    "coding style": "Language Compliance",
    "syntax_and_language_compliance": "Language Compliance",
    "syntax/compatibility": "Language Compliance",
    "code_quality": "Language Compliance",
    "documentation": "Language Compliance",
    "portability": "Language Compliance",
    "synthesis_compatibility": "Language Compliance",
    "stack memory": "Functional Logic",
    "simulation_termination": "Verification",
    "simulation termination": "Verification",
    "declaration": "Compilation Error",
    "data_path": "Functional Logic",
    "arithmetic safety": "Arithmetic",
    "arithmetic_safety": "Arithmetic",
    "spec_compliance": "Interface Compliance",
    "logic": "Functional Logic",
    "functional_correctness": "Functional Logic",
    "functional logic bug": "Functional Logic",
    "functional_logic_bug": "Functional Logic",
    "logic_design": "Functional Logic",
    "arithmetic_logic": "Functional Logic",
}

_TYPE_ALIASES: Dict[str, str] = {
    "specification_compliance": "spec_compliance",
    "specification compliance": "spec_compliance",
    "interface_compliance": "spec_compliance",
    "interface compliance": "spec_compliance",
    "functional": "functional_bug_fix",
    "functional_logic": "functional_bug_fix",
    "functional logic": "functional_bug_fix",
    "implementation pattern": "implementation_pattern",
}

_ROOT_CAUSE_ALIASES: Dict[str, str] = {
    "state_machine": "fsm",
    "signed_unsigned": "signedness",
    "sign": "signedness",
    "parameter": "parameterization",
    "clocking": "timing",
    "clock": "timing",
    "compile": "syntax",
    "spec": "interface",
}

_VALID_IMPORTANCE: Set[str] = {"high", "medium", "low"}
_MAIN_EXPERIENCE_TYPES: Set[str] = {
    "spec_compliance",
    "functional_bug_fix",
    "implementation_pattern",
}
_VALID_TASK_SCOPE: Set[str] = {"task_specific", "cross_task"}

_IMPORTANCE_DOWNGRADE = {"high": "medium", "medium": "low", "low": "low"}

_VAGUE_PATTERNS = re.compile(
    r"\b(be careful|test thoroughly|check syntax|double.?check|always review"
    r"|make sure|clarify spec)\b",
    re.IGNORECASE,
)
_META_PATTERNS = re.compile(
    r"\b(readability|comment|documentation|maintainability|reviewer"
    r"|tool may|verification may|acceptable warning|style|portability"
    r"|always verify|review checker output)\b",
    re.IGNORECASE,
)
_SPECULATIVE_PATTERNS = re.compile(
    r"\b(default to|testbench expectations?|verification environment expects?"
    r"|many verification environments|when .* unspecified|if unspecified)\b",
    re.IGNORECASE,
)
_WORKAROUND_PATTERNS = re.compile(
    r"\b(simulation timeout|cycle limit|allow verification completion"
    r"|error recovery in verification|verification framework)\b",
    re.IGNORECASE,
)
_ACTION_PATTERNS = re.compile(
    r"\b(use|implement|ensure|assign|declare|extend|instantiate|add|remove"
    r"|guard|toggle|reset|compare|latch|align|replace|zero-extend|sign-extend)\b",
    re.IGNORECASE,
)
_EVIDENCE_PATTERNS = re.compile(
    r"\b(error|mismatch|failed|warning|detail|compile|simulation|timeout"
    r"|assert|checker|verification|function error|stale)\b",
    re.IGNORECASE,
)
_ROOT_CAUSE_HINTS = [
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
    ("underflow", "arithmetic"),
    ("syntax", "syntax"),
    ("compile", "syntax"),
    ("arch", "architecture"),
]
_SNAKE_RE = re.compile(r"[^a-z0-9_]")


class ExperiencePostProcessor:
    """规则化后处理器，对候选经验做结构化清洗。"""

    def __init__(
        self,
        *,
        min_problem_len: int = 24,
        min_solution_len: int = 36,
        dedup_threshold: float = 0.52,
        min_confidence: float = 0.45,
    ):
        self.min_problem_len = min_problem_len
        self.min_solution_len = min_solution_len
        self.dedup_threshold = dedup_threshold
        self.min_confidence = min_confidence

    def process(self, experiences: List[Experience]) -> List[Experience]:
        prepared = self.prepare(experiences)
        return self.deduplicate(prepared)

    def prepare(self, experiences: List[Experience]) -> List[Experience]:
        if not experiences:
            return experiences

        before = len(experiences)
        prepared = []
        for exp in experiences:
            normalized = self._normalize(exp)
            if self._passes_quality(normalized):
                prepared.append(normalized)

        removed = before - len(prepared)
        if removed:
            print(
                f"[PostProcessor] 结构过滤前 {before} 条 -> 过滤后 {len(prepared)} 条 "
                f"(移除 {removed})"
            )
        return prepared

    def deduplicate(self, exps: List[Experience]) -> List[Experience]:
        if not exps:
            return exps

        groups: Dict[Tuple[str, str, str], List[Experience]] = {}
        for exp in exps:
            groups.setdefault(self._dedup_key(exp), []).append(exp)

        result: List[Experience] = []
        for group in groups.values():
            result.extend(self._dedup_group(group))

        if len(result) != len(exps):
            print(
                f"[PostProcessor] 簇内去重前 {len(exps)} 条 -> 去重后 {len(result)} 条 "
                f"(移除 {len(exps) - len(result)})"
            )
        return result

    def _normalize(self, exp: Experience) -> Experience:
        exp.category = self._normalize_category(exp.category)
        exp.id = self._normalize_id(exp.id)
        exp.importance = self._normalize_importance(exp.importance)
        exp.experience_type = self._normalize_experience_type(exp)
        exp.root_cause_type = self._normalize_root_cause(exp)
        exp.task_scope = self._normalize_task_scope(exp)
        exp.confidence = self._normalize_confidence(exp.confidence, exp)
        exp.canonical = True if exp.canonical is None else bool(exp.canonical)
        exp.evidence = (exp.evidence or "").strip() or None
        evidence_list = self._normalize_str_list(exp.evidence_list, exp.evidence)
        merged_from = self._normalize_str_list(exp.merged_from)
        exp.evidence_list = evidence_list or None
        exp.merged_from = merged_from or None
        if not exp.evidence:
            exp.importance = _IMPORTANCE_DOWNGRADE.get(exp.importance, exp.importance)
        return exp

    @staticmethod
    def _normalize_category(raw: str) -> str:
        normalized = (raw or "").lower().strip()
        if not normalized:
            return "Functional Logic"

        alias = _CATEGORY_ALIASES.get(normalized)
        if alias:
            return alias

        bag = normalized.replace("_", " ").replace("-", " ")
        if (
            "interface" in bag
            or "specification" in bag
            or "module name" in bag
            or "port mismatch" in bag
        ):
            return "Interface Compliance"
        if "syntax" in bag or "compile" in bag:
            return "Compilation Error"
        if (
            "language" in bag
            or "code quality" in bag
            or "portability" in bag
            or "documentation" in bag
        ):
            return "Language Compliance"
        if "synthesis" in bag:
            return "Synthesis"
        if "clock" in bag or "timing" in bag or "pipeline" in bag:
            return "Clock/Timing"
        if "verify" in bag or "test" in bag:
            return "Verification"
        if "logic" in bag or "functional" in bag or "design" in bag:
            return "Functional Logic"
        return raw.strip()

    @staticmethod
    def normalize_pool(pool) -> None:
        pp = ExperiencePostProcessor()
        for exp in pool.get_all():
            pp._normalize(exp)

    @staticmethod
    def _normalize_id(raw: str) -> str:
        cleaned = _SNAKE_RE.sub("_", (raw or "").lower().strip())
        cleaned = re.sub(r"_+", "_", cleaned).strip("_")
        return cleaned or "unknown"

    @staticmethod
    def _normalize_importance(raw: str) -> str:
        val = (raw or "medium").lower().strip()
        return val if val in _VALID_IMPORTANCE else "medium"

    def _normalize_experience_type(self, exp: Experience) -> str:
        normalized = str(exp.experience_type or "").strip().lower()
        normalized = _TYPE_ALIASES.get(normalized, normalized)
        if normalized:
            return normalized

        category = str(exp.category or "").strip().lower()
        combined = " ".join(
            filter(None, [exp.problem or "", exp.solution or "", exp.id or "", category])
        ).lower()
        if "interface" in category or "spec" in category:
            return "spec_compliance"
        if any(
            token in combined
            for token in ("pattern", "instantiate", "architecture", "concatenation")
        ):
            return "implementation_pattern"
        if any(
            token in combined
            for token in (
                "readability",
                "comment",
                "documentation",
                "maintainability",
                "review",
                "style",
                "portability",
            )
        ):
            return "style_or_portability"
        if any(
            token in combined
            for token in ("verify", "checker", "process", "review checker", "workflow")
        ):
            return "meta_process"
        return "functional_bug_fix"

    def _normalize_root_cause(self, exp: Experience) -> str:
        normalized = str(exp.root_cause_type or "").strip().lower().replace(" ", "_")
        normalized = _ROOT_CAUSE_ALIASES.get(normalized, normalized)
        if normalized:
            return normalized

        combined = " ".join(filter(None, [exp.problem or "", exp.solution or "", exp.id or ""])).lower()
        for keyword, root_cause in _ROOT_CAUSE_HINTS:
            if keyword in combined:
                return root_cause
        return "general_logic"

    @staticmethod
    def _normalize_task_scope(exp: Experience) -> str:
        task_id = str(exp.task_id or "").strip().lower()
        if not task_id:
            return "cross_task"
        if any(sep in task_id for sep in ",;/ "):
            return "cross_task"

        normalized = str(exp.task_scope or "").strip().lower()
        combined = " ".join(
            filter(None, [exp.problem or "", exp.solution or "", exp.evidence or ""])
        ).lower()
        conservative_cross_task = (
            normalized == "cross_task"
            and exp.root_cause_type in {"syntax", "width", "parameterization", "signedness"}
            and any(
                cue in combined
                for cue in (
                    "always declare",
                    "declare every",
                    "all signals",
                    "all nets",
                    "module level",
                    "every signal",
                    "verilog syntax",
                )
            )
        )
        if conservative_cross_task:
            return "cross_task"
        return "task_specific"

    def _normalize_confidence(self, raw, exp: Experience) -> float:
        try:
            if raw is None or raw == "":
                raise ValueError
            return max(0.0, min(float(raw), 1.0))
        except (TypeError, ValueError):
            if exp.evidence:
                return 0.8
            return 0.55

    @staticmethod
    def _normalize_str_list(values: Optional[List[str]], extra: Optional[str] = None) -> List[str]:
        items: List[str] = []
        for value in values or []:
            text = str(value or "").strip()
            if text and text not in items:
                items.append(text)
        if extra:
            extra = extra.strip()
            if extra and extra not in items:
                items.append(extra)
        return items

    def _passes_quality(self, exp: Experience) -> bool:
        problem = exp.problem or ""
        solution = exp.solution or ""
        evidence = exp.evidence or ""
        combined = " ".join(filter(None, [exp.category, problem, solution, evidence])).lower()

        if exp.experience_type not in _MAIN_EXPERIENCE_TYPES:
            return False
        if len(problem) < self.min_problem_len:
            return False
        if len(solution) < self.min_solution_len:
            return False
        if exp.confidence < self.min_confidence:
            return False
        if not evidence or len(evidence) < 8:
            return False
        if _VAGUE_PATTERNS.search(problem) or _VAGUE_PATTERNS.search(solution):
            return False
        if _META_PATTERNS.search(combined):
            return False
        if _SPECULATIVE_PATTERNS.search(combined):
            return False
        if _WORKAROUND_PATTERNS.search(problem) or _WORKAROUND_PATTERNS.search(solution):
            return False
        if len(_ACTION_PATTERNS.findall(solution)) == 0:
            return False
        if len(_EVIDENCE_PATTERNS.findall(evidence)) == 0 and len(evidence) < 20:
            return False
        if exp.root_cause_type in {"process", "style"}:
            return False
        if "always verify" in combined or "clarify spec" in combined:
            return False
        return True

    def _dedup_key(self, exp: Experience) -> Tuple[str, str, str]:
        task_bucket = self._task_bucket(exp.task_id, exp.task_scope)
        return (task_bucket, exp.experience_type or "", exp.root_cause_type or "")

    @staticmethod
    def _task_bucket(task_id: Optional[str], task_scope: Optional[str]) -> str:
        if task_scope == "cross_task" or not task_id:
            return ""
        tokens = [token for token in re.split(r"[,;/\s]+", str(task_id).lower()) if token]
        return ",".join(sorted(tokens))

    def _dedup_group(self, group: List[Experience]) -> List[Experience]:
        if len(group) <= 1:
            return group

        ranked = sorted(group, key=self._rank_key, reverse=True)
        kept: List[Experience] = []
        for exp in ranked:
            merged = False
            for idx, existing in enumerate(kept):
                if self._is_duplicate(existing, exp):
                    kept[idx] = self._merge_duplicate(existing, exp)
                    merged = True
                    break
            if not merged:
                kept.append(exp)
        return kept

    def _rank_key(self, exp: Experience) -> Tuple[float, int, int, int]:
        return (
            quality_score(exp),
            len(exp.evidence_list or []),
            len(exp.solution or ""),
            len(exp.problem or ""),
        )

    def _is_duplicate(self, existing: Experience, incoming: Experience) -> bool:
        threshold = self._threshold_for_pair(existing, incoming)
        similarity = self._combined_similarity(existing, incoming)
        return similarity >= threshold

    def _threshold_for_pair(self, a: Experience, b: Experience) -> float:
        same_task = self._task_bucket(a.task_id, a.task_scope) == self._task_bucket(
            b.task_id, b.task_scope
        )
        if same_task and a.root_cause_type == b.root_cause_type:
            return max(0.42, self.dedup_threshold - 0.10)
        if a.experience_type == b.experience_type and a.root_cause_type == b.root_cause_type:
            return max(0.48, self.dedup_threshold - 0.04)
        return self.dedup_threshold

    def _combined_similarity(self, a: Experience, b: Experience) -> float:
        problem_sim = _jaccard(a.problem or "", b.problem or "")
        solution_sim = _jaccard(a.solution or "", b.solution or "")
        evidence_sim = _jaccard(a.evidence or "", b.evidence or "")

        score = problem_sim * 0.55 + solution_sim * 0.35 + evidence_sim * 0.10
        if a.root_cause_type == b.root_cause_type:
            score += 0.05
        if a.experience_type == b.experience_type:
            score += 0.03
        if self._task_bucket(a.task_id, a.task_scope) == self._task_bucket(
            b.task_id, b.task_scope
        ):
            score += 0.05
        return min(1.0, score)

    def _merge_duplicate(self, existing: Experience, incoming: Experience) -> Experience:
        if quality_score(incoming) > quality_score(existing):
            primary = incoming
            secondary = existing
        else:
            primary = existing
            secondary = incoming

        primary.evidence_list = self._normalize_str_list(
            (primary.evidence_list or []) + (secondary.evidence_list or []),
            secondary.evidence,
        ) or None
        primary.merged_from = self._normalize_str_list(
            (primary.merged_from or []) + [secondary.id] + (secondary.merged_from or [])
        ) or None
        primary.confidence = max(primary.confidence or 0.0, secondary.confidence or 0.0)
        primary.importance = self._higher_importance(primary.importance, secondary.importance)
        if not primary.evidence and secondary.evidence:
            primary.evidence = secondary.evidence
        return primary

    @staticmethod
    def _higher_importance(a: str, b: str) -> str:
        rank = {"high": 3, "medium": 2, "low": 1}
        return a if rank.get(a, 1) >= rank.get(b, 1) else b


def _jaccard(text1: str, text2: str) -> float:
    w1 = set(text1.lower().split())
    w2 = set(text2.lower().split())
    if not w1 or not w2:
        return 0.0
    return len(w1 & w2) / len(w1 | w2)
