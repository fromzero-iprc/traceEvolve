# TraceEvolve - Agent Programming Experience Extraction and Management System

## Overview

TraceEvolve is an automated programming experience extraction and management system, designed to extract valuable programming experiences from solution logs and maintain a high-quality experience pool.

### Core Features

1. **Experience Extraction**
   - Parse problem-solving logs to identify error types, correction processes, and final solutions
   - Call Large Language Models (LLMs) to extract general, transferable programming experiences

2. **Experience Management**
   - Automatic deduplication: identify semantically similar experiences
   - Smart merging: consolidate related experiences
   - Version updates: replace outdated experiences with better versions
   - Capacity control: keep the experience pool lean and efficient

3. **Experience Export**
   - Export formatted experience text for In-Context Learning (ICL)
   - Support sorting by importance

## Installation

```bash
# Install dependencies
pip install openai
```

## Quick Start

### 1. Set API Key

```bash
export LLM_API_KEY='your-openai-api-key'
export LLM_API_BASE='****'
export LLM_MODEL='******'
```

### 2. Command Line Usage

```bash
# Process a log directory
python -m trace_evolve.cli --dir benchmarks/KernelBench/logs/level1

# Process specified files
python -m trace_evolve.cli --files log1.log log2.log

# Process QiMeng-Agent JSON logs
python -m trace_evolve.cli --files /path/to/run_20260307_225128.json --qimeng

# Process a QiMeng-Agent log directory
python -m trace_evolve.cli --dir /path/to/qimeng/logs --qimeng

# Attach eval.jsonl feedback when extracting from QiMeng-Agent runs
python -m trace_evolve.cli \
  --files /path/to/run_20260307_225128.json \
  --qimeng \
  --eval-file /path/to/results/20260307_225128_iter10/eval.jsonl

# Specify experience pool path
python -m trace_evolve.cli --dir logs --pool my_experience_pool.json

# Export experiences for ICL
python -m trace_evolve.cli --export --output experiences.txt
```

### 3. Python API Usage

```python
from trace_evolve import EvolvePipeline
from trace_evolve.config import EvolveConfig, LLMConfig

# Configuration
config = EvolveConfig(
    extractor_llm=LLMConfig(
        api_key="your-api-key",
        model="gpt-4",
    ),
    manager_llm=LLMConfig(
        api_key="your-api-key",
        model="gpt-4",
    ),
    experience_pool_path="experience_pool.json",
    max_experiences_per_log=10,
    max_pool_size=100,
)

# Create pipeline
pipeline = EvolvePipeline(config)

# Process log files
log_files = ["log1.log", "log2.log", "log3.log"]
results = pipeline.process_log_files(log_files)

# Export experiences
pipeline.export_experiences_for_icl("experiences_for_icl.txt")
```

## Architecture

```
trace_evolve/
├── __init__.py           # Module entry
├── config.py             # Configuration and prompt templates
├── extractor.py          # Experience extraction module
├── manager.py            # Experience management module
├── pipeline.py           # Main pipeline module
├── cli.py                # Command-line tool
└── examples.py           # Usage examples
```

### Workflow

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   Log Files     │────▶│   Extractor     │────▶│ New Experiences │
│   (Log Files)   │     │  (Extractor)    │     │  (Experiences)  │
└─────────────────┘     └─────────────────┘     └─────────────────┘
                                                        │
                                                        ▼
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│ Experience Pool │◀────│    Manager      │◀────│ Merge Decision  │
│(Experience Pool)│     │   (Manager)     │     │ (Merge Decision)│
└─────────────────┘     └─────────────────┘     └─────────────────┘
                                                        ▲
                                                        │
                                                ┌─────────────────┐
                                                │ Existing Pool   │
                                                │ (Existing Pool) │
                                                └─────────────────┘
```

## Experience Data Structure

```json
{
    "id": "exp_001",
    "category": "Memory Management",
    "problem": "NRAM space insufficient causing memory out-of-bounds",
    "solution": "Process large data in chunks to ensure each batch does not exceed NRAM capacity",
    "code_pattern": "const uint32_t block_size = NRAM_SIZE / 2;",
    "importance": "high",
    "source_file": "19_ReLU_mlu.log"
}
```

## Configuration Options

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_experiences_per_log` | 10 | Maximum number of experiences to extract per log file |
| `max_pool_size` | 100 | Maximum experience pool size |
| `save_intermediate` | True | Whether to save intermediate results |
| `batch_size` | 5 | Number of files to process per batch |

## Custom Prompts

Edit the templates in `trace_evolve/config.py`:

- `EXPERIENCE_EXTRACTION_PROMPT`: Experience extraction prompt
- `EXPERIENCE_MERGE_PROMPT`: Experience merge prompt

## Output Files

- `experience_pool.json`: Experience pool file
- `intermediate_results/`: Intermediate results directory
  - `*_extracted.json`: Extraction results per file
  - `merge_*.json`: Merge operation records
  - `final_report.json`: Final report

## QiMeng-Agent Support

This repo now supports extracting experiences from QiMeng-Agent benchmark runs.

- `--qimeng` enables QiMeng log parsing
- QiMeng directory mode searches for `run_*.json` first
- `--eval-file` lets the extractor see benchmark failure details without corrupting the original JSON log structure

Recommended usage:

```bash
python -m trace_evolve.cli \
  --files /path/to/run_20260307_225128.json \
  --qimeng \
  --eval-file /path/to/results/20260307_225128_iter10/eval.jsonl \
  --pool /path/to/experience_pool.json \
  --batch-size 1 \
  --intermediate-dir intermediate_results \
  --verbose
```

Set `LLM_API_KEY`, `LLM_API_BASE`, and `LLM_MODEL` through environment variables or pass them explicitly from the CLI.

## License

MIT License
