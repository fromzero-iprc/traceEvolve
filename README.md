# TraceEvolve - Programming Experience Extraction for Agent Logs

## Overview

TraceEvolve extracts reusable lessons from solution logs and maintains a curated experience pool for later ICL use.

The current pipeline is optimized for QiMeng-Agent benchmark logs:

- split one run into per-task `TaskSegment`s
- extract experiences from each segment with concurrent LLM calls
- normalize, filter, and deduplicate candidates locally
- merge each new experience against only the most relevant pool candidates
- keep the pool sorted by overall experience quality

## Installation

```bash
pip install openai
```

Optional Ark support:

```bash
pip install volcengine-python-sdk
```

## Environment

```bash
export LLM_API_KEY="your-api-key"
export LLM_API_BASE="https://your-compatible-endpoint/v1"
export LLM_MODEL="gpt-4"
```

## Quick Start

### CLI

```bash
# Process plain log files
python -m trace_evolve.cli --files log1.log log2.log

# Process one QiMeng-Agent JSON log
python -m trace_evolve.cli \
  --files /path/to/run_20260311_160600.json \
  --qimeng \
  --pool experience_pool.json

# Process a QiMeng-Agent log directory
python -m trace_evolve.cli \
  --dir /path/to/qimeng/logs \
  --qimeng \
  --pool experience_pool.json \
  --batch-size 4 \
  --intermediate-dir intermediate_results

# Export experiences for ICL
python -m trace_evolve.cli \
  --export \
  --pool experience_pool.json \
  --output experiences.txt
```

Notes:

- `--qimeng` enables QiMeng-Agent JSON parsing and per-segment extraction.
- directory mode prefers `run_*.json` before generic `*.json` when `--qimeng` is set.
- `--eval-file` is no longer part of the CLI. The current pipeline uses information already present in the QiMeng log.

### Python API

```python
from trace_evolve import EvolvePipeline
from trace_evolve.config import EvolveConfig, LLMConfig

config = EvolveConfig(
    extractor_llm=LLMConfig(api_key="your-api-key", model="gpt-4"),
    manager_llm=LLMConfig(api_key="your-api-key", model="gpt-4"),
    experience_pool_path="experience_pool.json",
    max_experiences_per_log=10,
    max_pool_size=500,
)

pipeline = EvolvePipeline(config)

results = pipeline.process_log_files(
    ["/path/to/run_20260311_160600.json"],
    batch_size=1,
    use_qimeng_parser=True,
)

pipeline.export_experiences_for_icl("experiences_for_icl.txt")
```

## Current Architecture

```text
trace_evolve/
├── __init__.py        # package exports
├── cli.py             # command-line entrypoint
├── config.py          # dataclasses and prompt templates
├── extractor.py       # Experience, TaskSegment, parsers, concurrent extraction
├── postprocessor.py   # normalization, filtering, batch-local dedup
├── quality.py         # quality_score for replace/pool ranking
├── manager.py         # candidate-level merge and pool management
├── pipeline.py        # extract -> postprocess -> merge orchestration
├── utils.py           # JSON helpers and simple similarity utilities
└── examples.py        # usage examples
```

### Workflow

```text
QiMeng JSON log
  -> QiMengLogParser.segment_tasks()
  -> TaskSegment list sorted by priority_score
  -> ExperienceExtractor._extract_qimeng_per_segment()
  -> ExperiencePostProcessor.process()
  -> ExperienceManager.merge_experiences()
  -> ExperiencePool.save()
```

For non-QiMeng logs, the extractor still supports the legacy single-prompt path.

## Experience Model

```json
{
  "id": "explicit_reset_state_transition",
  "category": "Functional Logic",
  "problem": "FSM state transitions ignore reset gating in one branch.",
  "solution": "Implement an explicit reset branch and keep next-state defaults before conditional overrides.",
  "importance": "high",
  "source_file": "run_20260311_160600.json",
  "evidence": "simulation error: state mismatch after reset",
  "task_id": "fsm_task"
}
```

`code_pattern` is optional and may also be present.

## Extraction and Merge Behavior

### QiMeng extraction

- `TaskSegment` keeps structured task context such as question, checker errors, feedback, verification details, self-correction audit results, and final Verilog code.
- segments are extracted concurrently with `ThreadPoolExecutor`
- if segment parsing fails, the extractor falls back to the legacy whole-log path

### Postprocessing

`ExperiencePostProcessor` runs before merge and performs:

- category normalization
- ID cleanup and importance normalization
- evidence-aware importance downgrade
- quality filtering for overly short or vague experiences
- category-grouped deduplication using combined problem/solution Jaccard similarity

### Candidate-level merge

`ExperienceManager` no longer sends the full pool plus all new experiences to one LLM call.

Instead, for each new experience it:

1. normalizes pool categories with `normalize_pool()`
2. finds same-category top-k candidates using problem Jaccard similarity
3. directly `REPLACE`s a candidate when the new experience is clearly better by rule
4. otherwise calls `SINGLE_EXPERIENCE_MERGE_PROMPT` for a local merge decision
5. trims the pool by `quality_score`

## Configuration Defaults

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_experiences_per_log` | 10 | Maximum experiences extracted per input log in the legacy path |
| `max_pool_size` | 500 | Maximum retained experiences in the pool |
| `save_intermediate` | `True` | Whether to save extraction and merge artifacts |
| `batch_size` | 5 | Number of files processed together before one merge pass |

## Prompt Templates

Prompt templates live in `trace_evolve/config.py`:

- `EXPERIENCE_EXTRACTION_PROMPT` for the legacy whole-log path
- `SEGMENT_EXTRACTION_PROMPT` for single-task QiMeng extraction
- `SINGLE_EXPERIENCE_MERGE_PROMPT` for local candidate-level merge
- `EXPERIENCE_MERGE_PROMPT` for the older batch-style merge schema and compatibility

## Output Files

- `experience_pool.json`: persistent pool with top-level keys `experiences`, `history`, and `metadata`
- `intermediate_results/`: optional intermediate artifacts
  - `*_extracted.json`: raw extracted experiences per processed file
  - `merge_*.json`: merge decision summaries per batch
  - `final_report.json`: batch-level summary report

## Verification Notes

- pool JSON shape is intentionally stable because QiMeng-Agent reads it downstream
- prompt templates use Python `.format()`, so literal JSON braces in prompts must stay escaped
- `TaskSegment.render_for_prompt()` and `_extract_legacy()` are intentionally retained as full-context paths

## License

MIT License
