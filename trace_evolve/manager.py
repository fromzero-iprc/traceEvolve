"""
经验管理模块 - 管理经验池，执行经验合并操作
"""

import json
import re
from typing import List, Dict, Any, Optional
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

        data = {
            "experiences": {
                exp_id: exp.to_dict() for exp_id, exp in self.experiences.items()
            },
            "history": self.history,
            "metadata": {
                "last_updated": datetime.now().isoformat(),
                "total_experiences": len(self.experiences),
            },
        }

        self.pool_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"经验池已保存，共 {len(self.experiences)} 条经验")

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
        """记录操作历史"""
        self.history.append(
            {
                "batch_id": batch_id,
                "timestamp": datetime.now().isoformat(),
                "operation": operation.to_dict(),
            }
        )

    def __len__(self):
        return len(self.experiences)


class ExperienceManager:
    """经验管理器 - 处理经验合并逻辑"""

    def __init__(
        self,
        llm_config: LLMConfig,
        pool_path: Optional[str] = None,
        max_pool_size: int = 100,
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

        # 候选级局部 merge：每条新经验先找 top-k 旧候选，再局部 LLM 或规则预判
        pool_size_before = len(self.pool)
        operations: List[Operation] = []

        for new_exp in new_experiences:
            op = self._merge_single(new_exp, batch_id)
            if op:
                operations.append(op)
                self._execute_operation(op, batch_id)

        self._enforce_pool_limit()
        self.pool.save()

        return {
            "batch_id": batch_id,
            "operations": [op.to_dict() for op in operations],
            "pool_size_before": pool_size_before,
            "pool_size_after": len(self.pool),
            "summary": f"执行了 {len(operations)} 个操作",
        }

    def _find_candidates(self, new_exp: Experience, top_k: int = 5) -> List[Experience]:
        """按 category 过滤 + problem Jaccard 相似度取 top-k 旧候选。"""
        same_cat = [
            e for e in self.pool.get_all()
            if e.category == new_exp.category and e.id != new_exp.id
        ]
        if not same_cat:
            return []

        scored = [
            (e, calculate_similarity(new_exp.problem, e.problem))
            for e in same_cat
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [e for e, _ in scored[:top_k]]

    def _merge_single(
        self, new_exp: Experience, batch_id: str
    ) -> Optional[Operation]:
        """
        对单条新经验做合并决策：无候选则 INSERT；有候选则规则预判或 LLM。
        """
        candidates = self._find_candidates(new_exp, top_k=5)

        if not candidates:
            return Operation(
                action=OperationType.INSERT,
                target_ids=[],
                new_experience=new_exp,
                reason="No similar candidate in pool",
            )

        # 规则预判：新经验明显优于某旧候选则直接 REPLACE
        best_cand = candidates[0]
        sim = calculate_similarity(new_exp.problem, best_cand.problem)
        if sim >= 0.5:
            q_new = quality_score(new_exp)
            q_old = quality_score(best_cand)
            if q_new - q_old >= 3:
                return Operation(
                    action=OperationType.REPLACE,
                    target_ids=[best_cand.id],
                    new_experience=new_exp,
                    reason=f"Rule REPLACE: quality_score new={q_new:.1f} old={q_old:.1f}",
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
                return ops[0]
            # 解析成功但无操作，fallback INSERT
            return Operation(
                action=OperationType.INSERT,
                target_ids=[],
                new_experience=new_exp,
                reason="LLM returned no operation, fallback INSERT",
            )
        except Exception as e:
            print(f"[Merge] LLM parse failed for {new_exp.id}: {e}, fallback INSERT")
            return Operation(
                action=OperationType.INSERT,
                target_ids=[],
                new_experience=new_exp,
                reason=f"Fallback INSERT after LLM error: {e}",
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
        experiences = self.pool.get_all()

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
