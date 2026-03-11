"""
经验质量评分 — 用于 merge 预判和 pool limit 排序。
"""

import re
from typing import Set

from .extractor import Experience

# 核心类别（高复用规则）
_CORE_CATEGORIES: Set[str] = {
    "Interface Compliance",
    "Functional Logic",
    "Clock/Timing",
    "Language Compliance",
}

# 题目专属信号词 — problem/solution 含任一则视为 task-specific
_TASK_SPECIFIC_KEYWORDS = re.compile(
    r"\b(johnson_counter|traffic_light|lui_upper_bits|radix4|radix4_booth"
    r"|freq_divbyeven|freq_divbyodd|freq_divbyfrac|serial2parallel"
    r"|ring_counter|asyn_fifo|float_multi|clkgenerator|mac_precision"
    r"|pipeline_enable_latency|pipeline_stage_count|clear_shift_reg"
    r"|counter_limit_off_by_one|fractional_division)\b",
    re.IGNORECASE,
)

# 动作词 — solution 含越多越具体
_ACTION_WORDS = re.compile(
    r"\b(use|implement|ensure|add|check|verify|include|assign|declare"
    r"|avoid|prefer|replace|merge|explicitly)\b",
    re.IGNORECASE,
)

# 过泛模式
_VAGUE_PATTERNS = re.compile(
    r"\b(be careful|test thoroughly|check syntax|double.?check)\b",
    re.IGNORECASE,
)


def quality_score(exp: Experience) -> float:
    """
    计算经验质量分，用于 merge 预判和 pool limit 排序。
    分数越高越应保留。
    """
    score = 0.0

    if exp.evidence:
        score += 2
    if exp.task_id:
        score += 1

    if len(exp.solution) > 80 and len(_ACTION_WORDS.findall(exp.solution)) >= 2:
        score += 2
    elif len(exp.solution) > 50:
        score += 1

    if exp.category in _CORE_CATEGORIES:
        score += 1

    if _TASK_SPECIFIC_KEYWORDS.search(exp.problem) or _TASK_SPECIFIC_KEYWORDS.search(
        exp.solution
    ):
        score -= 2

    if len(exp.problem) < 40 or _VAGUE_PATTERNS.search(exp.problem):
        score -= 1

    score += {"high": 3, "medium": 2, "low": 1}.get(exp.importance.lower(), 1)

    return score
