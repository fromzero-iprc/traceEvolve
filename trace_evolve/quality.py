"""经验质量评分 — 用于 merge 预判和 pool limit 排序。"""

import re

from .extractor import Experience

_ACTION_WORDS = re.compile(
    r"\b(use|implement|ensure|add|assign|declare|extend|instantiate|latch"
    r"|guard|toggle|reset|compare|zero-extend|sign-extend|replace|remove|align)\b",
    re.IGNORECASE,
)

_VAGUE_PATTERNS = re.compile(
    r"\b(be careful|test thoroughly|double.?check|always review|make sure"
    r"|clarify spec|readability|comment could be clearer)\b",
    re.IGNORECASE,
)

_META_PATTERNS = re.compile(
    r"\b(review|readability|comment|documentation|maintainability|tool may"
    r"|verification may|acceptable warning|style|portability)\b",
    re.IGNORECASE,
)

_CONFLICT_RISK_PATTERNS = re.compile(
    r"\b(may|might|consider|prefer|could|optional|sometimes|acceptable)\b",
    re.IGNORECASE,
)

_SPECIFIC_ROOT_CAUSES = {
    "reset",
    "width",
    "interface",
    "arithmetic",
    "fsm",
    "cdc",
    "timing",
    "syntax",
    "parameterization",
    "handshake",
    "signedness",
    "architecture",
}


def quality_score(exp: Experience) -> float:
    """计算经验质量分，分数越高越应保留。"""
    problem = exp.problem or ""
    solution = exp.solution or ""
    evidence = exp.evidence or ""
    experience_type = (exp.experience_type or "").strip().lower()
    root_cause = (exp.root_cause_type or "").strip().lower()
    confidence = _safe_confidence(exp.confidence)

    score = 0.0

    if evidence:
        score += 2.0
    if exp.evidence_list:
        score += min(1.0, 0.25 * len(exp.evidence_list))
    if exp.task_id:
        score += 1.0
    if exp.task_scope == "cross_task":
        score += 0.5

    action_hits = len(_ACTION_WORDS.findall(solution))
    if len(solution) >= 60 and action_hits >= 2:
        score += 2.0
    elif action_hits >= 1:
        score += 1.0

    if root_cause in _SPECIFIC_ROOT_CAUSES:
        score += 1.5
    elif root_cause and root_cause != "general_logic":
        score += 0.75

    if experience_type == "spec_compliance":
        score += 2.0
    elif experience_type == "functional_bug_fix":
        score += 1.8
    elif experience_type == "implementation_pattern":
        score += 1.3
    elif experience_type in {"meta_process", "style_or_portability"}:
        score -= 3.0

    if confidence is not None:
        score += confidence * 2.0

    if len(problem) < 35 or _VAGUE_PATTERNS.search(problem) or _VAGUE_PATTERNS.search(solution):
        score -= 1.5
    if _META_PATTERNS.search(problem) or _META_PATTERNS.search(solution):
        score -= 2.0

    conflict_hits = len(_CONFLICT_RISK_PATTERNS.findall(problem + " " + solution))
    if conflict_hits:
        score -= min(1.5, 0.3 * conflict_hits)

    score += {"high": 2.0, "medium": 1.0, "low": 0.0}.get(
        (exp.importance or "medium").lower(),
        1.0,
    )

    return score


def _safe_confidence(raw) -> float:
    try:
        if raw is None:
            return None
        return max(0.0, min(float(raw), 1.0))
    except (TypeError, ValueError):
        return None
