"""
经验管理模块 - 管理经验池，执行经验合并操作
"""

import concurrent.futures
import json
import os
import re
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, asdict
from pathlib import Path
from enum import Enum
from datetime import datetime

from .config import LLMConfig, EXPERIENCE_MERGE_PROMPT, SINGLE_EXPERIENCE_MERGE_PROMPT
from .extractor import Experience, LLMClient
from .postprocessor import ExperiencePostProcessor
from .quality import quality_score
from .utils import calculate_similarity


class OperationType(Enum):
    """操作类型"""

    INSERT = "INSERT"
    DELETE = "DELETE"
    REPLACE = "REPLACE"
    MERGE = "MERGE"
    SKIP = "SKIP"


@dataclass
class Operation:
    """操作记录"""

    action: OperationType
    target_ids: List[str]
    new_experience: Optional[Experience]
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action.value,
            "target_ids": self.target_ids,
            "new_experience": self.new_experience.to_dict()
            if self.new_experience
            else None,
            "reason": self.reason,
        }


class ExperiencePool:
    """经验池 - 存储和管理经验"""

    def __init__(self, pool_path: Optional[str] = None):
        self.pool_path = Path(pool_path) if pool_path else None
        self.experiences: Dict[str, Experience] = {}
        self.history: List[Dict[str, Any]] = []  # 操作历史
        # history 控制：
        # - TRACE_EVOLVE_POOL_HISTORY_MAX：内联保留的 history 条数上限（0 表示不内联保存）
        # - TRACE_EVOLVE_POOL_HISTORY_ARCHIVE：是否把被裁剪的 history 追加写入归档 jsonl（默认 1）
        self.history_max = int(os.getenv("TRACE_EVOLVE_POOL_HISTORY_MAX", "2000"))
        self.history_archive = os.getenv(
            "TRACE_EVOLVE_POOL_HISTORY_ARCHIVE", "1"
        ) not in {
            "0",
            "false",
            "False",
            "no",
        }

        if self.pool_path and self.pool_path.exists():
            self.load()

    def load(self):
        """从文件加载经验池"""
        if not self.pool_path or not self.pool_path.exists():
            return

        try:
            content = self.pool_path.read_text(encoding="utf-8").strip()
            if not content:
                print("经验池文件为空，将创建新的经验池")
                return

            data = json.loads(content)
            self.experiences = {
                exp_id: Experience.from_dict(exp_data)
                for exp_id, exp_data in data.get("experiences", {}).items()
            }
            self.history = data.get("history", [])

            print(f"已加载经验池，共 {len(self.experiences)} 条经验")
        except json.JSONDecodeError as e:
            print(f"经验池文件格式错误 ({e})，将创建新的经验池")
            self.experiences = {}
            self.history = []

    def save(self):
        """保存经验池到文件"""
        if not self.pool_path:
            return

        self.pool_path.parent.mkdir(parents=True, exist_ok=True)

        self._normalize_and_deduplicate_experiences()

        data = {
            "experiences": {
                exp_id: exp.to_dict() for exp_id, exp in self.experiences.items()
            },
            "history": [],
            "metadata": {
                "last_updated": datetime.now().isoformat(),
                "total_experiences": len(self.experiences),
            },
        }

        tmp_path = self.pool_path.with_suffix(self.pool_path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp_path, self.pool_path)
        print(f"经验池已保存，共 {len(self.experiences)} 条经验")

    def _normalize_and_deduplicate_experiences(self) -> None:
        """
        对整个经验池做一次结构归一化 + 全局去重。
        """
        if not self.experiences:
            return

        pp = ExperiencePostProcessor()
        normalized = []
        for exp in self.get_all():
            normalized.append(pp._normalize(exp))

        deduped = pp.deduplicate(normalized)
        new_experiences: Dict[str, Experience] = {}
        removed = len(self.experiences) - len(deduped)
        for exp in deduped:
            new_experiences[exp.id] = exp

        if removed > 0:
            print(
                f"[PoolDedup] 池内去重：由 {len(self.experiences)} 条压缩为 {len(new_experiences)} 条 "
                f"(移除 {removed})"
            )
        self.experiences = new_experiences

    def _maybe_trim_and_archive_history(self) -> None:
        """
        控制 history 体积，避免 experience_pool.json 被 history 撑爆。
        兼容性策略：仍保留顶层 history 字段，但只保留最近 N 条。
        可选：把被裁剪的历史写入 pool 同目录的 *.history.jsonl 归档文件（append-only）。
        """
        if self.history_max < 0:
            return

        max_keep = max(0, self.history_max)
        overflow = len(self.history) - max_keep
        if overflow <= 0:
            return

        to_archive = self.history[:overflow]
        self.history = self.history[overflow:]

        if not (self.history_archive and self.pool_path):
            return

        archive_path = self.pool_path.with_suffix(
            self.pool_path.suffix + ".history.jsonl"
        )
        try:
            with open(archive_path, "a", encoding="utf-8") as handle:
                for item in to_archive:
                    handle.write(json.dumps(item, ensure_ascii=False) + "\n")
        except Exception as e:
            print(f"[History] 归档写入失败，将仅裁剪内联 history: {e}")

    def insert(self, experience: Experience) -> bool:
        """插入新经验"""
        if experience.id in self.experiences:
            print(f"警告: 经验 {experience.id} 已存在，将被覆盖")

        self.experiences[experience.id] = experience
        return True

    def delete(self, exp_id: str) -> bool:
        """删除经验"""
        if exp_id in self.experiences:
            del self.experiences[exp_id]
            return True
        return False

    def replace(self, old_id: str, new_experience: Experience) -> bool:
        """替换经验"""
        self.delete(old_id)
        return self.insert(new_experience)

    def get(self, exp_id: str) -> Optional[Experience]:
        """获取经验"""
        return self.experiences.get(exp_id)

    def get_all(self) -> List[Experience]:
        """获取所有经验"""
        return list(self.experiences.values())

    def to_json(self) -> str:
        """转换为 JSON 字符串"""
        return json.dumps(
            [exp.to_dict() for exp in self.experiences.values()],
            ensure_ascii=False,
            indent=2,
        )

    def record_operation(self, operation: Operation, batch_id: str):
        """记录操作历史（当前已禁用，history 占 pool 文件 60%+ 且暂不使用）"""
        # self.history.append(
        #     {
        #         "batch_id": batch_id,
        #         "timestamp": datetime.now().isoformat(),
        #         "operation": operation.to_dict(),
        #     }
        # )
        pass

    def __len__(self):
        return len(self.experiences)


class ExperienceManager:
    """经验管理器 - 处理经验合并逻辑"""

    def __init__(
        self,
        llm_config: LLMConfig,
        pool_path: Optional[str] = None,
        max_pool_size: int = 450,
    ):
        self.llm_client = LLMClient(llm_config)
        self.pool = ExperiencePool(pool_path)
        self.max_pool_size = max_pool_size

    def merge_experiences(
        self, new_experiences: List[Experience], batch_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """将新经验合并到经验池"""
        if batch_id is None:
            batch_id = datetime.now().strftime("%Y%m%d_%H%M%S")

        # 归一化池中旧经验的 category
        if len(self.pool) > 0:
            ExperiencePostProcessor.normalize_pool(self.pool)

        # 如果经验池为空，直接插入所有新经验
        if len(self.pool) == 0:
            for exp in new_experiences:
                self.pool.insert(exp)
                self.pool.record_operation(
                    Operation(
                        action=OperationType.INSERT,
                        target_ids=[],
                        new_experience=exp,
                        reason="Initial insert because the experience pool was empty",
                    ),
                    batch_id,
                )

            result = {
                "batch_id": batch_id,
                "operations": [{"action": "INSERT", "count": len(new_experiences)}],
                "pool_size_before": 0,
                "pool_size_after": len(self.pool),
                "summary": f"Initialized the experience pool with {len(new_experiences)} experiences",
            }

            self.pool.save()
            return result

        pool_size_before = len(self.pool)

        # Phase 1: 基于当前 pool 快照并发获取所有 LLM 决策
        decisions = self._decide_all_concurrent(new_experiences, batch_id)

        # Phase 2: 串行执行决策，处理冲突
        operations: List[Operation] = []
        for op in decisions:
            if op is None:
                continue
            resolved = self._resolve_conflicts(op)
            operations.append(resolved)
            self._execute_operation(resolved, batch_id)

        self._enforce_pool_limit()
        self.pool.save()

        return {
            "batch_id": batch_id,
            "operations": [op.to_dict() for op in operations],
            "pool_size_before": pool_size_before,
            "pool_size_after": len(self.pool),
            "summary": f"执行了 {len(operations)} 个操作",
        }

    def _decide_all_concurrent(
        self, new_experiences: List[Experience], batch_id: str
    ) -> List[Optional[Operation]]:
        max_workers = min(len(new_experiences), 8)
        if max_workers <= 1:
            return [self._merge_single(exp, batch_id) for exp in new_experiences]

        results: List[Optional[Operation]] = [None] * len(new_experiences)

        def _decide(idx: int, exp: Experience) -> Tuple[int, Optional[Operation]]:
            return idx, self._merge_single(exp, batch_id)

        total = len(new_experiences)
        completed_count = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(_decide, i, exp): i
                for i, exp in enumerate(new_experiences)
            }
            for future in concurrent.futures.as_completed(futures):
                idx, op = future.result()
                results[idx] = op
                completed_count += 1
                if completed_count % 20 == 0 or completed_count == total:
                    print(f"[Merge] 已决策 {completed_count}/{total} 条经验")

        return results

    def _resolve_conflicts(self, op: Operation) -> Operation:
        if op.action in (
            OperationType.REPLACE,
            OperationType.MERGE,
            OperationType.DELETE,
        ):
            missing = [tid for tid in op.target_ids if self.pool.get(tid) is None]
            if missing:
                print(
                    f"[Conflict] {op.action.value} targets {missing} already gone, "
                    f"downgrading to SKIP for {op.new_experience.id if op.new_experience else '?'}"
                )
                return Operation(
                    action=OperationType.SKIP,
                    target_ids=[],
                    new_experience=None,
                    reason=(
                        f"Conflict: original {op.action.value} targets {missing} "
                        "already removed by prior op"
                    ),
                )
        return op

    @staticmethod
    def _same_task(a: Experience, b: Experience) -> bool:
        task_a_raw = str(a.task_id or "").strip().lower()
        task_b_raw = str(b.task_id or "").strip().lower()
        if not task_a_raw or not task_b_raw:
            return False

        split_pattern = r"[,;/\s]+"
        task_a = {tok for tok in re.split(split_pattern, task_a_raw) if tok}
        task_b = {tok for tok in re.split(split_pattern, task_b_raw) if tok}
        if not task_a or not task_b:
            return False
        return bool(task_a & task_b)

    @staticmethod
    def _same_category(a: Experience, b: Experience) -> bool:
        return str(a.category or "").strip() == str(b.category or "").strip()

    @staticmethod
    def _same_root_cause(a: Experience, b: Experience) -> bool:
        return str(a.root_cause_type or "").strip() == str(b.root_cause_type or "").strip()

    @staticmethod
    def _same_experience_type(a: Experience, b: Experience) -> bool:
        return str(a.experience_type or "").strip() == str(b.experience_type or "").strip()

    def _same_cluster(self, a: Experience, b: Experience) -> bool:
        return self._same_root_cause(a, b) and (
            self._same_task(a, b)
            or self._same_experience_type(a, b)
            or self._combined_similarity(a, b) >= 0.40
        )

    def _combined_similarity(self, new_exp: Experience, old_exp: Experience) -> float:
        problem_sim = calculate_similarity(new_exp.problem, old_exp.problem)
        solution_sim = calculate_similarity(new_exp.solution, old_exp.solution)
        evidence_sim = 0.0
        if new_exp.evidence and old_exp.evidence:
            evidence_sim = calculate_similarity(new_exp.evidence, old_exp.evidence)

        score = problem_sim * 0.52 + solution_sim * 0.33 + evidence_sim * 0.15
        if self._same_task(new_exp, old_exp):
            score += 0.12
        if self._same_root_cause(new_exp, old_exp):
            score += 0.10
        if self._same_experience_type(new_exp, old_exp):
            score += 0.06
        if self._same_category(new_exp, old_exp):
            score += 0.08
        return min(1.0, score)

    def _find_candidates(self, new_exp: Experience, top_k: int = 12) -> List[Experience]:
        """
        候选召回（严格 INSERT 前置）：
        - 同 task_id / root_cause / experience_type 优先
        - 语义相似（problem+solution+evidence）补充
        """
        all_existing = [e for e in self.pool.get_all() if e.id != new_exp.id]
        if not all_existing:
            return []

        scored: List[Tuple[Experience, float, bool, bool, float]] = []
        for old_exp in all_existing:
            sim = self._combined_similarity(new_exp, old_exp)
            same_task = self._same_task(new_exp, old_exp)
            same_cluster = self._same_cluster(new_exp, old_exp)
            if not (same_task or same_cluster or sim >= 0.26):
                continue
            scored.append(
                (old_exp, sim, same_task, same_cluster, quality_score(old_exp))
            )

        if not scored:
            for old_exp in all_existing:
                sim = self._combined_similarity(new_exp, old_exp)
                if sim >= 0.20:
                    scored.append(
                        (
                            old_exp,
                            sim,
                            self._same_task(new_exp, old_exp),
                            self._same_cluster(new_exp, old_exp),
                            quality_score(old_exp),
                        )
                    )

        if not scored:
            return []

        scored.sort(
            key=lambda item: (
                item[2],  # same_task
                item[3],  # same_cluster
                item[1],  # similarity
                item[4],  # candidate quality
            ),
            reverse=True,
        )
        return [item[0] for item in scored[:top_k]]

    def _strict_fallback_operation(
        self,
        new_exp: Experience,
        best_cand: Optional[Experience],
        sim: float,
        q_new: float,
        q_old: float,
        reason_prefix: str,
    ) -> Operation:
        has_task = bool(str(new_exp.task_id or "").strip())
        if str(new_exp.experience_type or "").strip() not in {
            "spec_compliance",
            "functional_bug_fix",
            "implementation_pattern",
        }:
            return Operation(
                action=OperationType.SKIP,
                target_ids=[],
                new_experience=None,
                reason=f"{reason_prefix}: non-main experience type {new_exp.experience_type}",
            )
        if best_cand is None:
            if q_new <= 2 and not has_task:
                return Operation(
                    action=OperationType.SKIP,
                    target_ids=[],
                    new_experience=None,
                    reason=f"{reason_prefix}: low-signal experience without task_id",
                )
            return Operation(
                action=OperationType.INSERT,
                target_ids=[],
                new_experience=new_exp,
                reason=f"{reason_prefix}: no candidate found",
            )

        if self._same_cluster(new_exp, best_cand) and sim >= 0.34:
            merged = self._merge_experience_pair(best_cand, new_exp)
            if not self._passes_merge_quality_gate(merged):
                return Operation(
                    action=OperationType.SKIP,
                    target_ids=[],
                    new_experience=None,
                    reason=f"{reason_prefix}: rejected speculative or low-signal merged candidate",
                )
            return Operation(
                action=OperationType.MERGE,
                target_ids=[best_cand.id],
                new_experience=merged,
                reason=(
                    f"{reason_prefix}: merge-first for same cluster "
                    f"(sim={sim:.2f}, new={q_new:.1f}, old={q_old:.1f})"
                ),
            )

        if sim >= 0.45:
            if q_new >= q_old + 2:
                return Operation(
                    action=OperationType.REPLACE,
                    target_ids=[best_cand.id],
                    new_experience=new_exp,
                    reason=(
                        f"{reason_prefix}: overlap candidate exists, replace by quality "
                        f"(new={q_new:.1f}, old={q_old:.1f})"
                    ),
                )
            return Operation(
                action=OperationType.SKIP,
                target_ids=[],
                new_experience=None,
                reason=(
                    f"{reason_prefix}: overlap candidate exists "
                    f"(sim={sim:.2f}, new={q_new:.1f}, old={q_old:.1f})"
                ),
            )

        if q_new <= 1 and not has_task:
            return Operation(
                action=OperationType.SKIP,
                target_ids=[],
                new_experience=None,
                reason=f"{reason_prefix}: very low quality and no task linkage",
            )

        return Operation(
            action=OperationType.INSERT,
            target_ids=[],
            new_experience=new_exp,
            reason=f"{reason_prefix}: low overlap candidate",
        )

    def _sanitize_llm_operation(
        self,
        op: Operation,
        new_exp: Experience,
        best_cand: Optional[Experience],
        sim: float,
        q_new: float,
        q_old: float,
    ) -> Operation:
        if op.action in {OperationType.MERGE, OperationType.REPLACE} and (
            not op.new_experience or not op.target_ids
        ):
            return self._strict_fallback_operation(
                new_exp=new_exp,
                best_cand=best_cand,
                sim=sim,
                q_new=q_new,
                q_old=q_old,
                reason_prefix="InvalidLLMOpFallback",
            )
        repaired_new_exp = None
        if op.new_experience:
            repaired_new_exp = self._repair_proposed_experience(
                op.new_experience, new_exp, best_cand
            )
            if not self._passes_merge_quality_gate(repaired_new_exp):
                return Operation(
                    action=OperationType.SKIP,
                    target_ids=[],
                    new_experience=None,
                    reason=(
                        "Rejected after merge-quality gate: "
                        f"{repaired_new_exp.id or new_exp.id}"
                    ),
                )
            op = Operation(
                action=op.action,
                target_ids=op.target_ids,
                new_experience=repaired_new_exp,
                reason=op.reason,
            )
        if op.action != OperationType.INSERT:
            return op
        return self._strict_fallback_operation(
            new_exp=repaired_new_exp or new_exp,
            best_cand=best_cand,
            sim=sim,
            q_new=q_new,
            q_old=q_old,
            reason_prefix="InsertGuard",
        )

    def _merge_single(self, new_exp: Experience, batch_id: str) -> Optional[Operation]:
        """
        对单条新经验做合并决策：无候选则 INSERT；有候选则规则预判或 LLM。
        """
        candidates = self._find_candidates(new_exp, top_k=12)
        q_new = quality_score(new_exp)

        if not candidates:
            return self._strict_fallback_operation(
                new_exp=new_exp,
                best_cand=None,
                sim=0.0,
                q_new=q_new,
                q_old=0.0,
                reason_prefix="NoCandidate",
            )

        # 规则预判：高重叠场景优先 REPLACE / SKIP，减少 INSERT 噪声。
        best_cand = candidates[0]
        q_old = quality_score(best_cand)
        sim = self._combined_similarity(new_exp, best_cand)
        same_task = self._same_task(new_exp, best_cand)
        same_cluster = self._same_cluster(new_exp, best_cand)
        if same_cluster and sim >= 0.48:
            merged = self._merge_experience_pair(best_cand, new_exp)
            if not self._passes_merge_quality_gate(merged):
                return Operation(
                    action=OperationType.SKIP,
                    target_ids=[],
                    new_experience=None,
                    reason=(
                        f"Rule SKIP: speculative or low-signal same-cluster merge "
                        f"with {best_cand.id}"
                    ),
                )
            if q_new >= q_old + 2:
                return Operation(
                    action=OperationType.REPLACE,
                    target_ids=[best_cand.id],
                    new_experience=merged,
                    reason=(
                        f"Rule REPLACE: stronger same-cluster candidate {best_cand.id} "
                        f"(sim={sim:.2f}, new={q_new:.1f}, old={q_old:.1f})"
                    ),
                )
            return Operation(
                action=OperationType.MERGE,
                target_ids=[best_cand.id],
                new_experience=merged,
                reason=(
                    f"Rule MERGE: same-cluster consolidation with {best_cand.id} "
                    f"(sim={sim:.2f}, new={q_new:.1f}, old={q_old:.1f})"
                ),
            )
        if sim >= 0.82 and q_new <= q_old + 1:
            return Operation(
                action=OperationType.SKIP,
                target_ids=[],
                new_experience=None,
                reason=(
                    f"Rule SKIP: near-duplicate with {best_cand.id} "
                    f"(sim={sim:.2f}, new={q_new:.1f}, old={q_old:.1f})"
                ),
            )
        if (sim >= 0.68 or (same_task and sim >= 0.55)) and q_new >= q_old + 2:
            return Operation(
                action=OperationType.REPLACE,
                target_ids=[best_cand.id],
                new_experience=new_exp,
                reason=(
                    f"Rule REPLACE: stronger overlap candidate {best_cand.id} "
                    f"(sim={sim:.2f}, new={q_new:.1f}, old={q_old:.1f})"
                ),
            )
        if sim >= 0.60 and q_new <= q_old:
            return Operation(
                action=OperationType.SKIP,
                target_ids=[],
                new_experience=None,
                reason=(
                    f"Rule SKIP: overlap candidate {best_cand.id} not improved "
                    f"(sim={sim:.2f}, new={q_new:.1f}, old={q_old:.1f})"
                ),
            )

        # LLM 局部 merge
        prompt = SINGLE_EXPERIENCE_MERGE_PROMPT.format(
            existing_candidates=json.dumps(
                [e.to_dict() for e in candidates], ensure_ascii=False, indent=2
            ),
            new_experience=json.dumps(new_exp.to_dict(), ensure_ascii=False, indent=2),
        )
        try:
            response = self.llm_client.call(prompt)
            ops = self._parse_merge_response(response)
            if ops:
                return self._sanitize_llm_operation(
                    ops[0], new_exp, best_cand, sim, q_new, q_old
                )
            return self._strict_fallback_operation(
                new_exp=new_exp,
                best_cand=best_cand,
                sim=sim,
                q_new=q_new,
                q_old=q_old,
                reason_prefix="LLMEmptyFallback",
            )
        except Exception as e:
            print(
                f"[Merge] LLM parse failed for {new_exp.id}: {e}, "
                "fallback strict decision"
            )
            return self._strict_fallback_operation(
                new_exp=new_exp,
                best_cand=best_cand,
                sim=sim,
                q_new=q_new,
                q_old=q_old,
                reason_prefix=f"LLMErrorFallback({type(e).__name__})",
            )

    def _parse_merge_response(self, response: str) -> List[Operation]:
        """解析 LLM 合并响应"""
        operations = []

        json_str = self._extract_json_payload(response)

        try:
            data = json.loads(json_str)
            op_list = data.get("operations", [])

            for op_data in op_list:
                action = OperationType(op_data.get("action", "SKIP"))
                target_ids = op_data.get("target_ids", [])
                reason = op_data.get("reason", "")

                new_exp = None
                if op_data.get("new_experience"):
                    new_exp = Experience.from_dict(op_data["new_experience"])

                operations.append(
                    Operation(
                        action=action,
                        target_ids=target_ids,
                        new_experience=new_exp,
                        reason=reason,
                    )
                )

        except (json.JSONDecodeError, ValueError) as e:
            raise ValueError(f"Failed to parse merge response: {e}") from e

        return operations

    def _extract_json_payload(self, response: str) -> str:
        stripped = response.strip()
        if stripped.startswith("```json"):
            first_newline = stripped.find("\n")
            last_fence = stripped.rfind("\n```")
            if first_newline != -1 and last_fence != -1 and last_fence > first_newline:
                return stripped[first_newline + 1 : last_fence]
        return stripped

    def _merge_experience_pair(self, old_exp: Experience, new_exp: Experience) -> Experience:
        if quality_score(new_exp) > quality_score(old_exp):
            primary = new_exp
            secondary = old_exp
        else:
            primary = old_exp
            secondary = new_exp

        evidence_list: List[str] = []
        for item in (primary.evidence_list or []) + ([primary.evidence] if primary.evidence else []):
            if item and item not in evidence_list:
                evidence_list.append(item)
        for item in (secondary.evidence_list or []) + ([secondary.evidence] if secondary.evidence else []):
            if item and item not in evidence_list:
                evidence_list.append(item)

        merged_from: List[str] = []
        for item in (primary.merged_from or []) + [secondary.id] + (secondary.merged_from or []):
            if item and item not in merged_from and item != primary.id:
                merged_from.append(item)

        merged = Experience.from_dict(primary.to_dict())
        merged.evidence_list = evidence_list
        merged.merged_from = merged_from
        merged.confidence = max(primary.confidence or 0.0, secondary.confidence or 0.0)
        merged.importance = self._higher_importance(primary.importance, secondary.importance)
        if not merged.evidence and secondary.evidence:
            merged.evidence = secondary.evidence
        if not merged.task_id:
            merged.task_id = old_exp.task_id or new_exp.task_id
        if not merged.experience_type:
            merged.experience_type = old_exp.experience_type or new_exp.experience_type
        if not merged.root_cause_type:
            merged.root_cause_type = old_exp.root_cause_type or new_exp.root_cause_type
        if not merged.task_scope:
            merged.task_scope = old_exp.task_scope or new_exp.task_scope
        return self._repair_proposed_experience(merged, new_exp, old_exp)

    @staticmethod
    def _higher_importance(a: str, b: str) -> str:
        rank = {"high": 3, "medium": 2, "low": 1}
        return a if rank.get(a, 1) >= rank.get(b, 1) else b

    def _repair_proposed_experience(
        self,
        proposed: Experience,
        new_exp: Experience,
        best_cand: Optional[Experience],
    ) -> Experience:
        repaired = Experience.from_dict(proposed.to_dict())
        fallback_task_id = new_exp.task_id or (best_cand.task_id if best_cand else None)
        fallback_type = new_exp.experience_type or (
            best_cand.experience_type if best_cand else None
        )
        fallback_root = new_exp.root_cause_type or (
            best_cand.root_cause_type if best_cand else None
        )

        if not repaired.task_id:
            repaired.task_id = fallback_task_id
        if not repaired.experience_type:
            repaired.experience_type = fallback_type
        if not repaired.root_cause_type:
            repaired.root_cause_type = fallback_root
        if not repaired.evidence:
            repaired.evidence = new_exp.evidence or (best_cand.evidence if best_cand else None)
        if repaired.confidence is None:
            repaired.confidence = max(
                new_exp.confidence or 0.0,
                (best_cand.confidence or 0.0) if best_cand else 0.0,
                repaired.confidence or 0.0,
            )

        evidence_list: List[str] = []
        for item in (repaired.evidence_list or []) + ([repaired.evidence] if repaired.evidence else []):
            if item and item not in evidence_list:
                evidence_list.append(item)
        for item in (new_exp.evidence_list or []) + ([new_exp.evidence] if new_exp.evidence else []):
            if item and item not in evidence_list:
                evidence_list.append(item)
        if best_cand:
            for item in (best_cand.evidence_list or []) + ([best_cand.evidence] if best_cand.evidence else []):
                if item and item not in evidence_list:
                    evidence_list.append(item)
        repaired.evidence_list = evidence_list or None

        merged_from: List[str] = []
        merged_seed = [best_cand.id] if best_cand else []
        for item in (repaired.merged_from or []) + [new_exp.id] + (
            (best_cand.merged_from or []) if best_cand else []
        ):
            if item and item not in merged_from and item != repaired.id:
                merged_from.append(item)
        for item in merged_seed:
            if item and item not in merged_from and item != repaired.id:
                merged_from.append(item)
        repaired.merged_from = merged_from or None

        repaired = ExperiencePostProcessor()._normalize(repaired)
        if repaired.task_id and not any(sep in repaired.task_id for sep in ",;/ "):
            repaired.task_scope = "task_specific"
        return repaired

    def _passes_merge_quality_gate(self, exp: Experience) -> bool:
        pp = ExperiencePostProcessor()
        normalized = pp._normalize(Experience.from_dict(exp.to_dict()))
        return pp._passes_quality(normalized)

    def _execute_operation(self, operation: Operation, batch_id: str):
        """执行单个操作"""
        if operation.action == OperationType.INSERT:
            if operation.new_experience:
                self.pool.insert(operation.new_experience)
                print(f"[INSERT] Added experience: {operation.new_experience.id}")

        elif operation.action == OperationType.DELETE:
            for exp_id in operation.target_ids:
                if self.pool.delete(exp_id):
                    print(f"[DELETE] Removed experience: {exp_id}")

        elif operation.action == OperationType.REPLACE:
            for exp_id in operation.target_ids:
                self.pool.delete(exp_id)
            if operation.new_experience:
                self.pool.insert(operation.new_experience)
                print(
                    f"[REPLACE] Replaced {operation.target_ids} -> {operation.new_experience.id}"
                )

        elif operation.action == OperationType.MERGE:
            for exp_id in operation.target_ids:
                self.pool.delete(exp_id)
            if operation.new_experience:
                self.pool.insert(operation.new_experience)
                print(
                    f"[MERGE] Merged {operation.target_ids} -> {operation.new_experience.id}"
                )

        elif operation.action == OperationType.SKIP:
            print(f"[SKIP] Skipped: {operation.reason}")

        self.pool.record_operation(operation, batch_id)

    def _enforce_pool_limit(self):
        """确保经验池不超过最大容量，按 quality_score 综合排序裁剪。"""
        if len(self.pool) <= self.max_pool_size:
            return

        experiences = self.pool.get_all()
        experiences.sort(key=lambda x: quality_score(x), reverse=True)

        to_keep = set(exp.id for exp in experiences[: self.max_pool_size])
        to_remove = [exp.id for exp in experiences if exp.id not in to_keep]

        for exp_id in to_remove:
            self.pool.delete(exp_id)
            print(f"[LIMIT] Removed due to pool limit: {exp_id}")

    def get_pool(self) -> ExperiencePool:
        """获取经验池"""
        return self.pool

    def get_experiences_for_prompt(self, max_count: int = 20) -> str:
        """获取经验，用于构建提示词"""
        ExperiencePostProcessor.normalize_pool(self.pool)
        experiences = [
            exp
            for exp in self.pool.get_all()
            if str(exp.experience_type or "").strip()
            in {"spec_compliance", "functional_bug_fix", "implementation_pattern"}
        ] or self.pool.get_all()

        # 按 quality_score 排序
        experiences.sort(key=lambda x: quality_score(x), reverse=True)

        # 取前 max_count 条
        selected = experiences[:max_count]

        # 格式化输出
        output_lines = []
        for i, exp in enumerate(selected, 1):
            output_lines.append(f"{i}. [{exp.category}] {exp.problem}")
            output_lines.append(f"   Solution: {exp.solution}")
            if exp.code_pattern:
                output_lines.append(f"   Pattern: {exp.code_pattern[:200]}")
            output_lines.append("")

        return "\n".join(output_lines)
