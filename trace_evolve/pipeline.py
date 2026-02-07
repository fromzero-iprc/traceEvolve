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


class EvolvePipeline:
    """经验演化流水线 - 从日志提取经验并管理经验池"""
    
    def __init__(self, config: EvolveConfig):
        self.config = config
        
        # 初始化提取器
        self.extractor = ExperienceExtractor(
            llm_config=config.extractor_llm,
            max_experiences=config.max_experiences_per_log
        )
        
        # 初始化管理器
        self.manager = ExperienceManager(
            llm_config=config.manager_llm,
            pool_path=config.experience_pool_path,
            max_pool_size=config.max_pool_size
        )
        
        # 中间结果目录
        if config.save_intermediate:
            self.intermediate_dir = Path(config.intermediate_dir)
            self.intermediate_dir.mkdir(parents=True, exist_ok=True)
        else:
            self.intermediate_dir = None
    
    def process_log_files(self, log_files: List[str], batch_size: int = 5) -> Dict[str, Any]:
        """
        处理日志文件列表，提取经验并更新经验池
        
        Args:
            log_files: 日志文件路径列表
            batch_size: 每批处理的文件数量
        
        Returns:
            处理结果摘要
        """
        results = {
            "total_files": len(log_files),
            "processed_files": 0,
            "total_experiences_extracted": 0,
            "final_pool_size": 0,
            "batches": [],
            "errors": []
        }
        
        # 分批处理
        for i in range(0, len(log_files), batch_size):
            batch = log_files[i:i + batch_size]
            batch_id = datetime.now().strftime("%Y%m%d_%H%M%S") + f"_batch{i // batch_size}"
            
            print(f"\n{'='*60}")
            print(f"处理批次 {i // batch_size + 1}/{(len(log_files) + batch_size - 1) // batch_size}")
            print(f"文件: {', '.join(Path(f).name for f in batch)}")
            print(f"{'='*60}")
            
            batch_result = self._process_batch(batch, batch_id)
            results["batches"].append(batch_result)
            results["processed_files"] += batch_result["processed_count"]
            results["total_experiences_extracted"] += batch_result["experiences_extracted"]
            
            if batch_result.get("errors"):
                results["errors"].extend(batch_result["errors"])
        
        results["final_pool_size"] = len(self.manager.get_pool())
        
        # 保存最终结果
        self._save_final_report(results)
        
        return results
    
    def _process_batch(self, log_files: List[str], batch_id: str) -> Dict[str, Any]:
        """处理单个批次"""
        batch_result = {
            "batch_id": batch_id,
            "files": log_files,
            "processed_count": 0,
            "experiences_extracted": 0,
            "merge_result": None,
            "errors": []
        }
        
        all_experiences: List[Experience] = []
        
        # 1. 从每个文件提取经验
        for log_file in log_files:
            try:
                print(f"\n[提取] 处理文件: {Path(log_file).name}")
                experiences = self.extractor.extract_from_file(log_file)
                
                print(f"  -> 提取了 {len(experiences)} 条经验")
                for exp in experiences:
                    print(f"     - [{exp.category}] {exp.problem[:50]}...")
                
                all_experiences.extend(experiences)
                batch_result["processed_count"] += 1
                
                # 保存中间结果
                if self.intermediate_dir:
                    self._save_intermediate_extraction(log_file, experiences, batch_id)
                
            except Exception as e:
                error_msg = f"处理 {log_file} 失败: {str(e)}"
                print(f"  [错误] {error_msg}")
                batch_result["errors"].append(error_msg)
        
        batch_result["experiences_extracted"] = len(all_experiences)
        
        # 2. 合并到经验池
        if all_experiences:
            print(f"\n[合并] 将 {len(all_experiences)} 条经验合并到经验池...")
            try:
                merge_result = self.manager.merge_experiences(all_experiences, batch_id)
                batch_result["merge_result"] = merge_result
                print(f"  -> 经验池大小: {merge_result['pool_size_before']} -> {merge_result['pool_size_after']}")
                
                # 保存中间结果
                if self.intermediate_dir:
                    self._save_intermediate_merge(merge_result, batch_id)
                    
            except Exception as e:
                error_msg = f"合并经验失败: {str(e)}"
                print(f"  [错误] {error_msg}")
                batch_result["errors"].append(error_msg)
        
        return batch_result
    
    def _save_intermediate_extraction(self, log_file: str, experiences: List[Experience], batch_id: str):
        """保存中间提取结果"""
        filename = Path(log_file).stem + f"_{batch_id}_extracted.json"
        output_path = self.intermediate_dir / filename
        
        data = {
            "source_file": log_file,
            "batch_id": batch_id,
            "timestamp": datetime.now().isoformat(),
            "experiences": [exp.to_dict() for exp in experiences]
        }
        
        output_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    
    def _save_intermediate_merge(self, merge_result: Dict[str, Any], batch_id: str):
        """保存中间合并结果"""
        filename = f"merge_{batch_id}.json"
        output_path = self.intermediate_dir / filename
        
        output_path.write_text(json.dumps(merge_result, ensure_ascii=False, indent=2), encoding='utf-8')
    
    def _save_final_report(self, results: Dict[str, Any]):
        """保存最终报告"""
        if self.intermediate_dir:
            report_path = self.intermediate_dir / "final_report.json"
            results["timestamp"] = datetime.now().isoformat()
            report_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
            print(f"\n最终报告已保存到: {report_path}")
    
    def get_experience_pool(self) -> ExperiencePool:
        """获取当前经验池"""
        return self.manager.get_pool()
    
    def export_experiences_for_icl(self, output_path: str, max_count: int = 20):
        """导出经验，用于 ICL (In-Context Learning)"""
        experiences_text = self.manager.get_experiences_for_prompt(max_count)
        
        Path(output_path).write_text(experiences_text, encoding='utf-8')
        print(f"经验已导出到: {output_path}")
        
        return experiences_text


def create_pipeline_from_env() -> EvolvePipeline:
    """从环境变量创建流水线"""
    config = EvolveConfig(
        extractor_llm=LLMConfig.from_env("EXTRACTOR_LLM"),
        manager_llm=LLMConfig.from_env("MANAGER_LLM"),
    )
    return EvolvePipeline(config)
