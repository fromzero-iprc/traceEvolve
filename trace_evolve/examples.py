#!/usr/bin/env python
"""
使用示例 - 展示如何使用 TraceEvolve 系统

这个脚本展示了三种使用方式：
1. 使用命令行工具
2. 使用 Python API
3. 使用自定义配置
"""

import os
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from trace_evolve import ExperienceExtractor, ExperienceManager, EvolvePipeline
from trace_evolve.config import EvolveConfig, LLMConfig


def example_basic_usage():
    """基本用法：从日志文件提取经验并管理"""
    print("=" * 60)
    print("示例 1: 基本用法")
    print("=" * 60)

    # 1. 配置 LLM
    llm_config = LLMConfig(
        api_key=os.getenv("LLM_API_KEY", "your-api-key"),
        api_base=os.getenv("LLM_API_BASE", ""),
        model="gpt-4",
        temperature=0.7,
        max_tokens=40960,
    )

    # 2. 创建经验提取器
    extractor = ExperienceExtractor(llm_config, max_experiences=5)

    # 3. 从单个文件提取经验
    log_file = "benchmarks/KernelBench/logs/level1/19_ReLU_mlu.log"
    if Path(log_file).exists():
        experiences = extractor.extract_from_file(log_file)

        print(f"\n从 {log_file} 提取了 {len(experiences)} 条经验:")
        for exp in experiences:
            print(f"  - [{exp.category}] {exp.problem}")
            print(f"    解决方案: {exp.solution}")
            print()
    else:
        print(f"日志文件不存在: {log_file}")


def example_pipeline_usage():
    """流水线用法：批量处理日志文件"""
    print("=" * 60)
    print("示例 2: 流水线用法")
    print("=" * 60)

    # 1. 创建完整配置
    config = EvolveConfig(
        extractor_llm=LLMConfig(
            api_key=os.getenv("LLM_API_KEY", "your-api-key"),
            model="gpt-4",
        ),
        manager_llm=LLMConfig(
            api_key=os.getenv("LLM_API_KEY", "your-api-key"),
            model="gpt-4",
        ),
        experience_pool_path="experience_pool.json",
        max_experiences_per_log=5,
        max_pool_size=500,
        save_intermediate=True,
        intermediate_dir="intermediate_results",
    )

    # 2. 创建流水线
    pipeline = EvolvePipeline(config)

    # 3. 获取日志文件列表
    log_dir = Path("benchmarks/KernelBench/logs/level1")
    if log_dir.exists():
        log_files = sorted(log_dir.glob("*.log"))[:5]  # 只处理前5个文件作为演示
        log_files = [str(f) for f in log_files]

        if log_files:
            print(f"\n将处理 {len(log_files)} 个日志文件:")
            for f in log_files:
                print(f"  - {Path(f).name}")

            # 4. 处理日志文件
            results = pipeline.process_log_files(log_files, batch_size=2)

            # 5. 输出结果
            print(f"\n处理结果:")
            print(f"  - 处理文件数: {results['processed_files']}")
            print(f"  - 提取经验数: {results['total_experiences_extracted']}")
            print(f"  - 最终经验池大小: {results['final_pool_size']}")
        else:
            print("没有找到日志文件")
    else:
        print(f"日志目录不存在: {log_dir}")


def example_export_usage():
    """导出用法：将经验导出用于 ICL"""
    print("=" * 60)
    print("示例 3: 导出经验用于 ICL")
    print("=" * 60)

    config = EvolveConfig(
        extractor_llm=LLMConfig(api_key="dummy"),
        manager_llm=LLMConfig(api_key="dummy"),
        experience_pool_path="experience_pool.json",
    )

    pipeline = EvolvePipeline(config)

    pool_path = Path("experience_pool.json")
    if pool_path.exists():
        # 导出经验
        experiences_text = pipeline.export_experiences_for_icl(
            output_path="experiences_for_icl.txt", max_count=20
        )

        print("\n导出的经验内容预览:")
        print(
            experiences_text[:500] + "..."
            if len(experiences_text) > 500
            else experiences_text
        )
    else:
        print(f"经验池文件不存在: {pool_path}")
        print("请先运行流水线生成经验池")


def example_custom_prompts():
    """自定义提示词示例"""
    print("=" * 60)
    print("示例 4: 自定义提示词")
    print("=" * 60)

    # 可以通过修改 config.py 中的提示模板来自定义
    from trace_evolve.config import EXPERIENCE_EXTRACTION_PROMPT

    print("\n当前经验提取提示词模板:")
    print(EXPERIENCE_EXTRACTION_PROMPT[:500] + "...")

    print("\n要自定义提示词，请编辑 trace_evolve/config.py 中的模板")


def main():
    """运行所有示例"""
    print("\n" + "=" * 60)
    print("TraceEvolve 使用示例")
    print("=" * 60 + "\n")

    # 检查 API Key
    if not os.getenv("LLM_API_KEY"):
        print("警告: 未设置 LLM_API_KEY 环境变量")
        print("      请设置后重新运行示例")
        print("      export LLM_API_KEY='your-api-key'")
        print()

    # 运行示例 (实际需要 API Key)
    # example_basic_usage()
    # example_pipeline_usage()
    # example_export_usage()
    example_custom_prompts()


if __name__ == "__main__":
    main()
