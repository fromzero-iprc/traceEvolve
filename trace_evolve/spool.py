import json
import os
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterable, List
from uuid import uuid4

from .extractor import Experience


def infer_run_id(log_files: List[str]) -> str:
    if not log_files:
        return f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    first = Path(log_files[0])
    if first.parent.name == "tasks" and first.parent.parent.name:
        return first.parent.parent.name
    if first.stem.startswith("run_"):
        return first.stem
    return f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def write_candidates_jsonl(
    experiences: Iterable[Experience], spool_dir: str, run_id: str
) -> Path:
    spool_path = Path(spool_dir)
    spool_path.mkdir(parents=True, exist_ok=True)

    filename = f"{run_id}.{os.getpid()}.{uuid4().hex[:8]}.jsonl"
    final_path = spool_path / filename
    tmp_path = final_path.with_suffix(final_path.suffix + ".tmp")

    with open(tmp_path, "w", encoding="utf-8") as handle:
        for exp in experiences:
            handle.write(json.dumps(exp.to_dict(), ensure_ascii=False) + "\n")

    os.replace(tmp_path, final_path)
    return final_path


def list_spool_files(spool_dir: str) -> List[Path]:
    spool_path = Path(spool_dir)
    if not spool_path.exists():
        return []
    return sorted(
        path
        for path in spool_path.glob("*.jsonl")
        if path.is_file() and path.parent.name != "merged"
    )


def move_to_merged(spool_file: Path) -> Path:
    merged_dir = spool_file.parent / "merged"
    merged_dir.mkdir(parents=True, exist_ok=True)
    target_path = merged_dir / spool_file.name
    os.replace(spool_file, target_path)
    return target_path


@contextmanager
def file_lock(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w", encoding="utf-8") as handle:
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            try:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
