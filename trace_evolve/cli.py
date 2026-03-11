#!/usr/bin/env python
"""
TraceEvolve - 命令行入口

使用方法:
    # 处理单个日志文件
    python -m trace_evolve.cli --files path/to/log1.log

    # 处理多个日志文件
    python -m trace_evolve.cli --files path/to/log1.log path/to/log2.log

    # 处理目录下的所有日志文件
    python -m trace_evolve.cli --dir path/to/logs

    # 指定经验池路径
    python -m trace_evolve.cli --dir path/to/logs --pool experience_pool.json

    # 导出经验用于 ICL
    python -m trace_evolve.cli --export --pool experience_pool.json --output experiences.txt
"""

import argparse
import os
import sys
from pathlib import Path
from typing import List

from .config import EvolveConfig, LLMConfig
from .pipeline import EvolvePipeline


def find_log_files(directory: str, pattern: str = "*.log") -> List[str]:
    """查找目录下的所有日志文件"""
    log_dir = Path(directory)
    if not log_dir.exists():
        raise FileNotFoundError(f"目录不存在: {directory}")

    log_files = list(log_dir.glob(pattern))
    return [str(f) for f in sorted(log_files)]


def find_qimeng_log_files(directory: str) -> List[str]:
    """查找 QiMeng-Agent 生成的 JSON 日志文件。"""
    log_dir = Path(directory)
    if not log_dir.exists():
        raise FileNotFoundError(f"目录不存在: {directory}")

    json_logs = sorted(log_dir.glob("run_*.json"))
    if json_logs:
        return [str(f) for f in json_logs]

    return [str(f) for f in sorted(log_dir.glob("*.json"))]


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="TraceEvolve - 编程经验提取和管理工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    # 处理日志目录
    python -m trace_evolve.cli --dir benchmarks/KernelBench/logs/level1

    # 处理 QiMeng-Agent 日志目录
    python -m trace_evolve.cli --dir /path/to/qimeng/logs --qimeng

    # 处理指定文件
    python -m trace_evolve.cli --files log1.log log2.log

    # 导出经验
    python -m trace_evolve.cli --export --output experiences.txt
        """,
    )

    # 输入选项
    input_group = parser.add_mutually_exclusive_group()
    input_group.add_argument("--files", "-f", nargs="+", help="要处理的日志文件列表")
    input_group.add_argument("--dir", "-d", help="包含日志文件的目录")

    # 输出选项
    parser.add_argument(
        "--pool",
        "-p",
        default="experience_pool.json",
        help="经验池文件路径 (默认: experience_pool.json)",
    )
    parser.add_argument(
        "--intermediate-dir",
        "-i",
        default="intermediate_results",
        help="中间结果目录 (默认: intermediate_results)",
    )

    # LLM 配置
    parser.add_argument(
        "--api-key",
        default=os.getenv("LLM_API_KEY", ""),
        help="LLM API Key (也可通过 LLM_API_KEY 环境变量设置)",
    )
    parser.add_argument(
        "--api-base",
        default=os.getenv("LLM_API_BASE", ""),
        help="LLM API Base URL (也可通过 LLM_API_BASE 环境变量设置)",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("LLM_MODEL", "gpt-4"),
        help="LLM 模型名称 (默认：gpt-4)",
    )
    parser.add_argument(
        "--max-experiences",
        type=int,
        default=10,
        help="每个日志文件最多提取的经验数 (默认：10)",
    )
    parser.add_argument(
        "--max-pool-size",
        type=int,
        default=100,
        help="经验池最大容量 (默认：100)",
    )
    parser.add_argument("--no-intermediate", action="store_true", help="不保存中间结果")
    parser.add_argument(
        "--batch-size",
        "-b",
        type=int,
        default=5,
        help="每批处理的文件数量 (默认：5)",
    )
    parser.add_argument("--export", "-e", action="store_true", help="导出经验用于 ICL")
    parser.add_argument(
        "--output",
        "-o",
        default="experiences_for_icl.txt",
        help="导出文件路径 (默认：experiences_for_icl.txt)",
    )
    parser.add_argument(
        "--export-count", type=int, default=20, help="导出的经验数量 (默认：20)"
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="详细输出")
    parser.add_argument(
        "--qimeng",
        action="store_true",
        help="使用 QiMengLogParser 解析 QiMeng-Agent 日志格式",
    )

    return parser.parse_args()


def main():
    """主函数"""
    args = parse_args()

    llm_config = LLMConfig(
        api_key=args.api_key,
        api_base=args.api_base,
        model=args.model,
    )

    config = EvolveConfig(
        extractor_llm=llm_config,
        manager_llm=llm_config,
        experience_pool_path=args.pool,
        max_experiences_per_log=args.max_experiences,
        max_pool_size=args.max_pool_size,
        save_intermediate=not args.no_intermediate,
        intermediate_dir=args.intermediate_dir,
    )

    pipeline = EvolvePipeline(config)

    if args.export:
        if not Path(args.pool).exists():
            print(f"错误：经验池文件不存在：{args.pool}")
            sys.exit(1)

        experiences = pipeline.export_experiences_for_icl(
            output_path=args.output, max_count=args.export_count
        )
        print(f"\n已导出 {args.export_count} 条经验到 {args.output}")
        return

    log_files = []
    if args.files:
        log_files = args.files
    elif args.dir:
        log_files = (
            find_qimeng_log_files(args.dir) if args.qimeng else find_log_files(args.dir)
        )
    else:
        print("错误：请指定 --files 或 --dir 参数")
        sys.exit(1)

    if not log_files:
        print("错误：没有找到日志文件")
        sys.exit(1)

    print(f"找到 {len(log_files)} 个日志文件")
    if args.verbose:
        for f in log_files:
            print(f"  - {f}")

    if not args.api_key:
        print("错误：未设置 LLM API Key。请通过 --api-key 或 LLM_API_KEY 提供。")
        sys.exit(1)

    # 处理日志文件
    results = pipeline.process_log_files(
        log_files,
        batch_size=args.batch_size,
        use_qimeng_parser=args.qimeng,
        eval_file_path=args.eval_file,
    )

    print("\n" + "=" * 60)
    print("处理完成!")
    print("=" * 60)
    print(f"处理文件数：{results['processed_files']}/{results['total_files']}")
    print(f"提取经验数：{results['total_experiences_extracted']}")
    print(f"最终经验池大小：{results['final_pool_size']}")

    if results["errors"]:
        print(f"\n遇到 {len(results['errors'])} 个错误:")
        for error in results["errors"][:5]:
            print(f"  - {error}")


if __name__ == "__main__":
    main()
