"""
TraceEvolve - 经验提取和管理系统

该模块从 Agent 问题解决日志中提取经验，并维护一个经验池。
主要功能：
1. 从日志文件中提取经验教训
2. 合并和去重经验
3. 维护和更新经验库

使用方法：
    # 命令行
    python -m icl_evolve.cli --dir path/to/logs
    
    # Python API
    from icl_evolve import EvolvePipeline
    pipeline = EvolvePipeline(config)
    pipeline.process_log_files(log_files)
"""

from .extractor import ExperienceExtractor, Experience, LogParser, LLMClient
from .manager import ExperienceManager, ExperiencePool, Operation, OperationType
from .pipeline import EvolvePipeline, create_pipeline_from_env
from .config import EvolveConfig, LLMConfig

__version__ = "0.1.0"

__all__ = [
    # 主要类
    'ExperienceExtractor',
    'ExperienceManager', 
    'EvolvePipeline',
    
    # 数据类
    'Experience',
    'ExperiencePool',
    'Operation',
    'OperationType',
    
    # 配置
    'EvolveConfig',
    'LLMConfig',
    
    # 辅助类
    'LogParser',
    'LLMClient',
    
    # 工厂函数
    'create_pipeline_from_env',
]
