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

## License

MIT License
