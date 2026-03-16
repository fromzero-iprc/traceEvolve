"""
Split one experience pool into core/extended pools with deterministic rules.
"""

import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .extractor import Experience


CORE_CATEGORY_ALLOWLIST = {
    "Functional Logic",
    "Interface Compliance",
    "Compilation Error",
    "Clock/Timing",
    "Arithmetic",
    "State Machine",
    "Verification",
    "Language Compliance",
    "Synthesis",
    "Output Format",
}

GENERIC_HINT_TERMS = {
    "reset",
    "width",
    "interface",
    "overflow",
    "underflow",
    "signed",
    "unsigned",
    "timing",
    "pipeline",
    "fsm",
    "counter",
}

TOKEN_RE = re.compile(r"[a-z0-9_]+")
NUMBER_RE = re.compile(r"\b\d+\b")


def _normalize_tokens(text: str) -> List[str]:
    return TOKEN_RE.findall((text or "").lower())


def _parse_task_ids(raw_task_id: Any) -> List[str]:
    text = str(raw_task_id or "").strip()
    if not text:
        return []
    parts = re.split(r"[,;/\s]+", text)
    return [part for part in parts if part]


def _contains_task_identity(exp: Experience, task_ids: List[str]) -> bool:
    if not task_ids:
        return False
    joined = " ".join(
        [
            exp.id or "",
            exp.problem or "",
            exp.solution or "",
            exp.source_file or "",
        ]
    ).lower()
    for task_id in task_ids:
        tid = task_id.lower()
        if tid and tid in joined:
            return True
    return False


def _specificity_score(exp: Experience, task_ids: List[str]) -> int:
    score = 0
    if len(task_ids) == 1:
        score += 1
    if _contains_task_identity(exp, task_ids):
        score += 2
    if exp.source_file:
        score += 1
    if len(NUMBER_RE.findall(exp.problem or "")) >= 3:
        score += 1
    return score


def _generality_score(exp: Experience) -> int:
    score = 0
    importance = (exp.importance or "medium").lower()
    if importance == "high":
        score += 3
    elif importance == "medium":
        score += 1

    if exp.category in CORE_CATEGORY_ALLOWLIST:
        score += 2

    evidence_len = len((exp.evidence or "").strip())
    if evidence_len >= 50:
        score += 1

    token_set = set(_normalize_tokens(f"{exp.problem} {exp.solution}"))
    if len(token_set & GENERIC_HINT_TERMS) >= 2:
        score += 1

    if len((exp.problem or "").strip()) >= 45 and len((exp.solution or "").strip()) >= 60:
        score += 1

    return score


def classify_experience(exp: Experience) -> Tuple[str, str]:
    task_ids = _parse_task_ids(exp.task_id)
    specificity = _specificity_score(exp, task_ids)
    generality = _generality_score(exp)
    importance = (exp.importance or "medium").lower()

    if importance == "low":
        return "extended", "low_importance"
    if exp.category not in CORE_CATEGORY_ALLOWLIST:
        return "extended", "category_not_in_core_allowlist"
    if specificity >= 3:
        return "extended", "task_specific_signal_high"
    if generality >= 6:
        return "core", "general_high_signal"
    return "extended", "general_signal_insufficient"


def _load_pool(input_pool: Path) -> List[Experience]:
    raw = json.loads(input_pool.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and "experiences" in raw:
        items = list(raw["experiences"].values())
    elif isinstance(raw, list):
        items = raw
    else:
        items = []
    return [Experience.from_dict(item) for item in items]


def _dump_pool(path: Path, experiences: List[Experience], source: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "experiences": {exp.id: exp.to_dict() for exp in experiences},
        "history": [],
        "metadata": {
            "last_updated": datetime.now().isoformat(),
            "total_experiences": len(experiences),
            "source": source,
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def split_pool_file(input_pool: str, core_pool: str, extended_pool: str) -> Dict[str, Any]:
    input_path = Path(input_pool)
    if not input_path.exists():
        raise FileNotFoundError(f"input pool not found: {input_pool}")

    experiences = _load_pool(input_path)
    core: List[Experience] = []
    extended: List[Experience] = []
    reason_counter: Counter = Counter()

    for exp in experiences:
        tier, reason = classify_experience(exp)
        reason_counter[reason] += 1
        if tier == "core":
            core.append(exp)
        else:
            extended.append(exp)

    core.sort(key=lambda exp: exp.id)
    extended.sort(key=lambda exp: exp.id)

    _dump_pool(Path(core_pool), core, source=str(input_path))
    _dump_pool(Path(extended_pool), extended, source=str(input_path))

    return {
        "input_pool": str(input_path),
        "input_size": len(experiences),
        "core_pool": str(Path(core_pool)),
        "extended_pool": str(Path(extended_pool)),
        "core_size": len(core),
        "extended_size": len(extended),
        "reasons": dict(sorted(reason_counter.items(), key=lambda kv: kv[0])),
    }
