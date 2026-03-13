"""
经验演化主流程 - 整合经验提取和管理
"""

import json
from typing import List, Dict, Any, Optional
from pathlib import Path
from datetime import datetime

from .config import EvolveConfig, LLMConfig
from .extractor import ExperienceExtractor, Experience
from .manager import ExperienceManager, ExperiencePool
from .postprocessor import ExperiencePostProcessor
from .spool import (
    file_lock,
    infer_run_id,
    list_spool_files,
    move_to_merged,
    write_candidates_jsonl,
)


class EvolvePipeline:
    """经验演化流水线 - 从日志提取经验并管理经验池"""

    def __init__(self, config: EvolveConfig):
        self.config = config

        # 初始化提取器
        self.extractor = ExperienceExtractor(
            llm_config=config.extractor_llm,
            max_experiences=config.max_experiences_per_log,
        )

        # 初始化管理器
        self.manager = ExperienceManager(
            llm_config=config.manager_llm,
            pool_path=config.experience_pool_path,
            max_pool_size=config.max_pool_size,
        )

        # 中间结果目录
        if config.save_intermediate:
            self.intermediate_dir = Path(config.intermediate_dir)
            self.intermediate_dir.mkdir(parents=True, exist_ok=True)
        else:
            self.intermediate_dir = None

    def process_log_files(
        self,
        log_files: List[str],
        batch_size: int = 5,
        use_qimeng_parser: bool = False,
        eval_file_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        处理日志文件列表，提取经验并更新经验池。

        Args:
            log_files: 日志文件路径列表
            batch_size: 每批处理的文件数量
            use_qimeng_parser: 是否使用 QiMeng 日志解析模式
            eval_file_path: deprecated, kept for CLI compatibility but no longer used

        Returns:
            处理结果摘要
        """
        if eval_file_path:
            print(
                "[警告] --eval-file 已弃用：日志中的 check_result/verification "
                "已包含更丰富的错误信息，无需额外注入 eval.jsonl"
            )

        results = {
            "total_files": len(log_files),
            "processed_files": 0,
            "total_experiences_extracted": 0,
            "final_pool_size": 0,
            "batches": [],
            "errors": [],
        }

        for i in range(0, len(log_files), batch_size):
            batch = log_files[i : i + batch_size]
            batch_id = (
                datetime.now().strftime("%Y%m%d_%H%M%S") + f"_batch{i // batch_size}"
            )

            print(f"\n{'=' * 60}")
            print(
                f"处理批次 {i // batch_size + 1}/{(len(log_files) + batch_size - 1) // batch_size}"
            )
            print(f"文件：{', '.join(Path(f).name for f in batch)}")
            print(f"{'=' * 60}")

            batch_result = self._extract_batch(
                batch,
                batch_id,
                use_qimeng_parser=use_qimeng_parser,
            )
            results["batches"].append(self._public_batch_result(batch_result))
            results["processed_files"] += batch_result["processed_count"]
            results["total_experiences_extracted"] += batch_result[
                "experiences_extracted"
            ]

            if batch_result.get("errors"):
                results["errors"].extend(batch_result["errors"])

            all_experiences = batch_result.get("experiences", [])
            if all_experiences:
                print(f"\n[合并] 将 {len(all_experiences)} 条经验合并到经验池...")
                try:
                    merge_result = self.manager.merge_experiences(
                        all_experiences, batch_id
                    )
                    batch_result["merge_result"] = merge_result
                    print(
                        f"  -> 经验池大小: {merge_result['pool_size_before']} -> {merge_result['pool_size_after']}"
                    )

                    if self.intermediate_dir:
                        self._save_intermediate_merge(merge_result, batch_id)
                except Exception as e:
                    error_msg = f"合并经验失败: {str(e)}"
                    print(f"  [错误] {error_msg}")
                    batch_result["errors"].append(error_msg)
                    results["errors"].append(error_msg)

        results["final_pool_size"] = len(self.manager.get_pool())

        self._save_final_report(results)

        return results

    def _extract_batch(
        self,
        log_files: List[str],
        batch_id: str,
        use_qimeng_parser: bool = False,
    ) -> Dict[str, Any]:
        """
        处理单个批次。

        Args:
            log_files: 当前批次的日志文件列表
            batch_id: 批次标识符
            use_qimeng_parser: 是否使用 QiMeng 日志解析模式

        Returns:
            当前批次的处理结果
        """
        batch_result = {
            "batch_id": batch_id,
            "files": log_files,
            "processed_count": 0,
            "experiences_extracted": 0,
            "merge_result": None,
            "errors": [],
            "experiences": [],
        }

        all_experiences: List[Experience] = []

        for log_file in log_files:
            try:
                print(f"\n[提取] 处理文件：{Path(log_file).name}")

                if use_qimeng_parser:
                    log_content = Path(log_file).read_text(encoding="utf-8")

                    experiences = self.extractor.extract_from_content(
                        log_content,
                        Path(log_file).name,
                        use_qimeng_parser=True,
                    )
                else:
                    experiences = self.extractor.extract_from_file(
                        log_file,
                        use_qimeng_parser=False,
                    )

                print(f"  -> 提取了 {len(experiences)} 条经验")
                for exp in experiences:
                    print(f"     - [{exp.category}] {exp.problem[:50]}...")

                all_experiences.extend(experiences)
                batch_result["processed_count"] += 1

                if self.intermediate_dir:
                    self._save_intermediate_extraction(log_file, experiences, batch_id)

            except Exception as e:
                error_msg = f"处理 {log_file} 失败：{str(e)}"
                print(f"  [错误] {error_msg}")
                batch_result["errors"].append(error_msg)

        # 2. 规则后处理（归一化、过滤、去重）
        postprocessor = ExperiencePostProcessor()
        all_experiences = postprocessor.process(all_experiences)

        batch_result["experiences_extracted"] = len(all_experiences)

        batch_result["experiences"] = all_experiences

        return batch_result

    def extract_to_spool(
        self,
        log_files: List[str],
        spool_dir: str,
        batch_size: int = 5,
        use_qimeng_parser: bool = False,
        eval_file_path: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """提取候选经验并写入 spool JSONL，不更新主经验池。"""
        if eval_file_path:
            print(
                "[警告] --eval-file 已弃用：日志中的 check_result/verification "
                "已包含更丰富的错误信息，无需额外注入 eval.jsonl"
            )

        results = {
            "total_files": len(log_files),
            "processed_files": 0,
            "total_experiences_extracted": 0,
            "batches": [],
            "errors": [],
            "spool_file": None,
        }

        all_experiences: List[Experience] = []

        for i in range(0, len(log_files), batch_size):
            batch = log_files[i : i + batch_size]
            batch_id = (
                datetime.now().strftime("%Y%m%d_%H%M%S") + f"_batch{i // batch_size}"
            )
            batch_result = self._extract_batch(
                batch,
                batch_id,
                use_qimeng_parser=use_qimeng_parser,
            )
            results["batches"].append(self._public_batch_result(batch_result))
            results["processed_files"] += batch_result["processed_count"]
            results["total_experiences_extracted"] += batch_result[
                "experiences_extracted"
            ]
            results["errors"].extend(batch_result.get("errors", []))
            all_experiences.extend(batch_result.get("experiences", []))

        if all_experiences:
            resolved_run_id = run_id or infer_run_id(log_files)
            spool_file = write_candidates_jsonl(
                all_experiences, spool_dir, resolved_run_id
            )
            results["spool_file"] = str(spool_file)
            print(f"\n[Spool] 已写入候选经验: {spool_file}")

        return results

    def merge_spool(self, spool_dir: str) -> Dict[str, Any]:
        """读取 spool 目录中的候选经验，并沿用当前 manager 规则合并入池。"""
        spool_files = list_spool_files(spool_dir)
        results = {
            "spool_dir": spool_dir,
            "spool_files": [str(path) for path in spool_files],
            "loaded_experiences": 0,
            "invalid_lines": 0,
            "merge_result": None,
            "moved_files": [],
        }

        if not spool_files:
            return results

        experiences: List[Experience] = []
        for spool_file in spool_files:
            with open(spool_file, "r", encoding="utf-8") as handle:
                for raw_line in handle:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        experiences.append(Experience.from_dict(json.loads(line)))
                        results["loaded_experiences"] += 1
                    except Exception:
                        results["invalid_lines"] += 1

        if not experiences:
            return results

        print(f"[Merge] 读取到 {len(experiences)} 条候选经验，开始合并...")
        lock_path = Path(self.config.experience_pool_path).with_suffix(".lock")
        batch_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        with file_lock(lock_path):
            results["merge_result"] = self.manager.merge_experiences(
                experiences, batch_id
            )

        for spool_file in spool_files:
            moved_path = move_to_merged(spool_file)
            results["moved_files"].append(str(moved_path))

        return results

    def _save_intermediate_extraction(
        self, log_file: str, experiences: List[Experience], batch_id: str
    ):
        """保存中间提取结果"""
        if self.intermediate_dir is None:
            return
        filename = Path(log_file).stem + f"_{batch_id}_extracted.json"
        output_path = self.intermediate_dir / filename

        data = {
            "source_file": log_file,
            "batch_id": batch_id,
            "timestamp": datetime.now().isoformat(),
            "experiences": [exp.to_dict() for exp in experiences],
        }

        output_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _save_intermediate_merge(self, merge_result: Dict[str, Any], batch_id: str):
        """保存中间合并结果"""
        if self.intermediate_dir is None:
            return
        filename = f"merge_{batch_id}.json"
        output_path = self.intermediate_dir / filename

        output_path.write_text(
            json.dumps(merge_result, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _save_final_report(self, results: Dict[str, Any]):
        """保存最终报告"""
        if self.intermediate_dir is None:
            return
        report_path = self.intermediate_dir / "final_report.json"
        results["timestamp"] = datetime.now().isoformat()
        report_path.write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n最终报告已保存到: {report_path}")

    @staticmethod
    def _public_batch_result(batch_result: Dict[str, Any]) -> Dict[str, Any]:
        public_result = dict(batch_result)
        public_result.pop("experiences", None)
        return public_result

    def get_experience_pool(self) -> ExperiencePool:
        """获取当前经验池"""
        return self.manager.get_pool()

    def export_experiences_for_icl(self, output_path: str, max_count: int = 20):
        """导出经验，用于 ICL (In-Context Learning)"""
        experiences_text = self.manager.get_experiences_for_prompt(max_count)

        Path(output_path).write_text(experiences_text, encoding="utf-8")
        print(f"经验已导出到: {output_path}")

        return experiences_text


def create_pipeline_from_env() -> EvolvePipeline:
    """从环境变量创建流水线"""
    config = EvolveConfig(
        extractor_llm=LLMConfig.from_env("EXTRACTOR_LLM"),
        manager_llm=LLMConfig.from_env("MANAGER_LLM"),
    )
    return EvolvePipeline(config)
