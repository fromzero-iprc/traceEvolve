"""
经验后处理器 — 在 LLM 提取之后、manager merge 之前执行规则化清洗。

职责：category 归一化、ID 清洗、importance 修正、evidence 门控、
      质量过滤（过短/过泛）、同类组内去重。
"""

import re
from typing import List, Dict, Set

from .extractor import Experience


# ── 标准分类映射表 ──────────────────────────────────────────
# key: 小写原始分类 → value: 归一化后的标准分类
_CATEGORY_ALIASES: Dict[str, str] = {
    # Functional Logic
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
    "functional_logic_bugs": "Functional Logic",
    # Interface Compliance
    "specification/interface mismatch": "Interface Compliance",
    "specification mismatch": "Interface Compliance",
    "interface/specification": "Interface Compliance",
    "specification compliance": "Interface Compliance",
    "specification interpretation": "Interface Compliance",
    "interface/implementation": "Interface Compliance",
    "interface": "Interface Compliance",
    "interface_compliance": "Interface Compliance",
    "specification/interface_mismatches": "Interface Compliance",
    # Compilation Error
    "compile error": "Compilation Error",
    "compilation error": "Compilation Error",
    "syntax errors": "Compilation Error",
    # State Machine
    "state machine error": "State Machine",
    "state machine": "State Machine",
    # Arithmetic
    "arithmetic design error": "Arithmetic",
    "arithmetic": "Arithmetic",
    "bit width management": "Arithmetic",
    # Clock / Timing
    "clocking": "Clock/Timing",
    "clock domain crossing": "Clock/Timing",
    "pipeline synchronization": "Clock/Timing",
    "pipeline design": "Clock/Timing",
    # Output Format
    "output-format": "Output Format",
    "output format error": "Output Format",
    "output format": "Output Format",
    # Process / Methodology
    "process improvement": "Process/Methodology",
    "verification": "Process/Methodology",
    # Synthesis
    "synthesis": "Synthesis",
    "synthesis error": "Synthesis",
    "synthesis warnings": "Synthesis",
    # Language Compliance
    "language compliance": "Language Compliance",
    "coding_style": "Language Compliance",
    "coding style": "Language Compliance",
    "syntax_and_language_compliance": "Language Compliance",
    "syntax/compatibility": "Language Compliance",

    # Verification / Process
    "verification": "Verification",
    "verification/functional": "Verification",
}

_VALID_IMPORTANCE: Set[str] = {"high", "medium", "low"}

_IMPORTANCE_DOWNGRADE = {"high": "medium", "medium": "low", "low": "low"}

# 空泛/低信号模式词 — problem 或 solution 中匹配任一即丢弃
_VAGUE_PATTERNS = re.compile(
    r"\b(be careful|test thoroughly|check syntax|double.?check|verify carefully"
    r"|always review|make sure to check)\b",
    re.IGNORECASE,
)

_SNAKE_RE = re.compile(r"[^a-z0-9_]")


class ExperiencePostProcessor:
    """规则化后处理器，对 LLM 提取的候选经验做清洗。"""

    def __init__(
        self,
        *,
        min_problem_len: int = 20,
        min_solution_len: int = 30,
        dedup_threshold: float = 0.5,
    ):
        self.min_problem_len = min_problem_len
        self.min_solution_len = min_solution_len
        self.dedup_threshold = dedup_threshold

    # ── 公开入口 ────────────────────────────────────────────

    def process(self, experiences: List[Experience]) -> List[Experience]:
        """依次执行：归一化 → 修正 → 门控 → 过滤 → 去重。"""
        if not experiences:
            return experiences

        before = len(experiences)
        exps = [self._normalize(e) for e in experiences]
        exps = [e for e in exps if self._passes_quality(e)]
        exps = self._deduplicate(exps)

        removed = before - len(exps)
        if removed:
            print(
                f"[PostProcessor] 清洗前 {before} 条 → 清洗后 {len(exps)} 条 "
                f"(移除 {removed})"
            )
        return exps

    # ── 归一化 ──────────────────────────────────────────────

    def _normalize(self, exp: Experience) -> Experience:
        exp.category = self._normalize_category(exp.category)
        exp.id = self._normalize_id(exp.id)
        exp.importance = self._normalize_importance(exp.importance)
        if not exp.evidence:
            exp.importance = _IMPORTANCE_DOWNGRADE.get(exp.importance, exp.importance)
        return exp

    @staticmethod
    def _normalize_category(raw: str) -> str:
        return _CATEGORY_ALIASES.get(raw.lower().strip(), raw.strip())

    @staticmethod
    def normalize_pool(pool) -> None:
        """
        对经验池中所有经验的 category 做归一化（in-place）。
        供 manager 在 merge 前调用，确保池中旧经验与 postprocessor 输出格式一致。
        """
        for exp in pool.get_all():
            exp.category = ExperiencePostProcessor._normalize_category(exp.category)

    @staticmethod
    def _normalize_id(raw: str) -> str:
        cleaned = _SNAKE_RE.sub("_", raw.lower().strip())
        cleaned = re.sub(r"_+", "_", cleaned).strip("_")
        return cleaned or "unknown"

    @staticmethod
    def _normalize_importance(raw: str) -> str:
        val = raw.lower().strip()
        return val if val in _VALID_IMPORTANCE else "medium"

    # ── 质量过滤 ────────────────────────────────────────────

    def _passes_quality(self, exp: Experience) -> bool:
        if len(exp.problem) < self.min_problem_len:
            return False
        if len(exp.solution) < self.min_solution_len:
            return False
        if _VAGUE_PATTERNS.search(exp.problem) or _VAGUE_PATTERNS.search(exp.solution):
            return False
        return True

    # ── 组内去重 ────────────────────────────────────────────

    def _deduplicate(self, exps: List[Experience]) -> List[Experience]:
        groups: Dict[str, List[Experience]] = {}
        for e in exps:
            groups.setdefault(e.category, []).append(e)

        result: List[Experience] = []
        for group in groups.values():
            result.extend(self._dedup_group(group))
        return result

    def _dedup_group(self, group: List[Experience]) -> List[Experience]:
        if len(group) <= 1:
            return group

        importance_rank = {"high": 0, "medium": 1, "low": 2}
        group.sort(key=lambda e: importance_rank.get(e.importance, 1))

        # 按 category 使用不同阈值：高频大类更严格，task-specific 更激进
        threshold = self._dedup_threshold_for_category(
            group[0].category if group else ""
        )

        kept: List[Experience] = []
        for exp in group:
            is_dup = False
            for existing in kept:
                # 使用 problem + solution 综合相似度
                sim = _jaccard_combined(exp.problem, exp.solution, existing.problem, existing.solution)
                if sim >= threshold:
                    is_dup = True
                    break
            if not is_dup:
                kept.append(exp)
        return kept

    def _dedup_threshold_for_category(self, category: str) -> float:
        """高频大类阈值略高，task-specific 略低。"""
        high_freq = {"Interface Compliance", "Output Format", "Functional Logic"}
        if category in high_freq:
            return min(0.55, self.dedup_threshold + 0.05)
        return max(0.45, self.dedup_threshold - 0.05)


def _jaccard(text1: str, text2: str) -> float:
    w1 = set(text1.lower().split())
    w2 = set(text2.lower().split())
    if not w1 or not w2:
        return 0.0
    return len(w1 & w2) / len(w1 | w2)


def _jaccard_combined(
    p1: str, s1: str, p2: str, s2: str, problem_weight: float = 0.7
) -> float:
    """problem 与 solution 加权 Jaccard。"""
    jp = _jaccard(p1, p2)
    js = _jaccard(s1, s2)
    return problem_weight * jp + (1 - problem_weight) * js
