# TraceEvolve

TraceEvolve extracts reusable implementation lessons from agent execution logs
and evolves a curated experience pool for later in-context reuse.

The current implementation is optimized for `QiMeng-Agent` task snapshots and
benchmark logs.

## Current Version

The current pipeline is designed as a conservative canonicalization system
rather than a "collect as many experiences as possible" system.

The practical pipeline has four stages:

1. parse QiMeng logs or task snapshots into task-local segments
2. extract typed experience candidates with evidence and root-cause fields
3. normalize, filter, consolidate, and deduplicate candidates locally
4. merge into a single main pool, or defer merge through spool JSONL files

Current design goals:

- favor high-signal, evidence-backed experiences over broad noisy recall
- reduce duplicated root-cause fragments before they reach the pool
- keep the main pool usable for runtime retrieval in `QiMeng-Agent`
- support deferred merge so extraction and curation can be decoupled

Recommended workflow:

- read a QiMeng run log or one-task snapshot JSON
- split QiMeng data into `TaskSegment`s
- extract candidate experiences with concurrent LLM calls
- run local post-processing:
  - normalize category / type / root cause
  - reject meta / style / speculative candidates
  - consolidate same-batch duplicates
- either merge immediately into the main pool or write to spool JSONL
- later merge spool files into the pool explicitly

This version also supports QiMeng-Agent's embedded async extraction worker:

- QiMeng-Agent writes `logs/run_x/tasks/*.json`
- the worker calls `python3.8 -m trace_evolve.cli --extract-only`
- candidates are written into `QiMeng-Agent/data/experience_spool/*.jsonl`
- pool updates happen later through `--merge-spool`

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

If you are following the current QiMeng-Agent integration, use `python3.8` when running `traceEvolve` commands.

## Quick Start

### 1. Full extraction + immediate merge

Process one QiMeng run log and update a single main pool immediately:

```bash
python3.8 -m trace_evolve.cli \
  --files /path/to/run_20260311_160600.json \
  --qimeng \
  --pool /path/to/experience_pool.json
```

Process a directory of QiMeng logs and merge directly:

```bash
python3.8 -m trace_evolve.cli \
  --dir /path/to/qimeng/logs \
  --qimeng \
  --pool /path/to/experience_pool.json \
  --batch-size 4 \
  --intermediate-dir intermediate_results
```

### 2. Extract candidates only to spool

Process one task snapshot without touching the pool:

```bash
python3.8 -m trace_evolve.cli \
  --files /path/to/run_x/tasks/task_001.json \
  --qimeng \
  --extract-only \
  --spool /path/to/experience_spool
```

Process one task-snapshot directory without touching the pool:

```bash
python3.8 -m trace_evolve.cli \
  --dir /path/to/run_x/tasks \
  --qimeng \
  --extract-only \
  --spool /path/to/experience_spool
```

### 3. Merge spool into the main pool later

```bash
python3.8 -m trace_evolve.cli \
  --merge-spool \
  --spool /path/to/experience_spool \
  --pool /path/to/experience_pool.json
```

### 4. Optional: split one pool into core/extended

```bash
python3.8 -m trace_evolve.cli \
  --split-pool \
  --pool /path/to/experience_pool.json \
  --core-pool /path/to/experience_pool_core.json \
  --extended-pool /path/to/experience_pool_extended.json
```

### 5. Export experiences for ICL

```bash
python3.8 -m trace_evolve.cli \
  --export \
  --pool /path/to/experience_pool.json \
  --output experiences.txt
```

## CLI Modes

Main CLI modes in `trace_evolve/cli.py`:

- default mode: extract and merge immediately
- `--extract-only`: extract candidate experiences and write one spool JSONL file
- `--merge-spool`: read all spool JSONL files, merge them into the pool, then move processed files to `merged/`
- `--split-pool`: optional offline utility to derive `core/extended` views from one pool
- `--export`: export top experiences for ICL

Important notes:

- `--qimeng` enables QiMeng-Agent JSON parsing and per-segment extraction.
- directory mode prefers `run_*.json` before generic `*.json` when `--qimeng` is set.
- task snapshot directories still work because the CLI falls back to generic `*.json` when no `run_*.json` exists.
- `--eval-file` is no longer part of the CLI.
- the CLI requires `LLM_API_KEY` for extraction paths, but not for `--merge-spool` or `--export`.
- `--pool` is the simplest and recommended way to work with a single main pool.
- `--core-pool` / `--extended-pool` are still supported for compatibility and optional split-pool workflows.

## Python API

```python
from trace_evolve import EvolvePipeline
from trace_evolve.config import EvolveConfig, LLMConfig

config = EvolveConfig(
    extractor_llm=LLMConfig(api_key="your-api-key", model="gpt-4"),
    manager_llm=LLMConfig(api_key="your-api-key", model="gpt-4"),
    experience_pool_path="experience_pool.json",
    max_experiences_per_log=10,
    max_pool_size=450,
)

pipeline = EvolvePipeline(config)

# Full processing
pipeline.process_log_files(
    ["/path/to/run_20260311_160600.json"],
    batch_size=1,
    use_qimeng_parser=True,
)

# Extract-only to spool
pipeline.extract_to_spool(
    ["/path/to/run_x/tasks/task_001.json"],
    spool_dir="/path/to/experience_spool",
    use_qimeng_parser=True,
)

# Merge-only from spool
pipeline.merge_spool("/path/to/experience_spool")

pipeline.export_experiences_for_icl("experiences_for_icl.txt")
```

## Architecture

```text
trace_evolve/
├── __init__.py        # package exports
├── cli.py             # command-line entrypoint
├── config.py          # dataclasses and prompt templates
├── extractor.py       # Experience schema, TaskSegment, parsers, concurrent extraction
├── postprocessor.py   # normalization, structural filtering, batch-local dedup
├── quality.py         # quality_score for retain/replace/pool ranking
├── manager.py         # merge-first pool management and candidate decisions
├── pipeline.py        # full pipeline + extract-only + merge-spool + candidate audit
├── pool_splitter.py   # optional deterministic split from one pool into core/extended
├── spool.py           # spool JSONL helpers and merged-file movement
├── utils.py           # JSON helpers and similarity utilities
└── examples.py        # usage examples
```

### Current workflow variants

Full pipeline:

```text
QiMeng run log or task snapshot
  -> QiMengLogParser.segment_tasks()
  -> TaskSegment list sorted by priority_score
  -> ExperienceExtractor._extract_qimeng_per_segment()
  -> ExperiencePostProcessor.prepare()
  -> same-batch consolidation
  -> ExperiencePostProcessor.deduplicate()
  -> ExperienceManager.merge_experiences()
  -> ExperiencePool.save()
```

Deferred merge pipeline:

```text
QiMeng task snapshot(s)
  -> ExperienceExtractor
  -> ExperiencePostProcessor
  -> write_candidates_jsonl(...)
  -> spool/*.jsonl
  -> merge_spool(...)
  -> ExperienceManager.merge_experiences()
  -> experience_pool.json
```

For non-QiMeng logs, the extractor still keeps the legacy whole-log path.

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
  "task_id": "fsm_task",
  "experience_type": "functional_bug_fix",
  "root_cause_type": "reset",
  "task_scope": "task_specific",
  "confidence": 0.82,
  "canonical": true,
  "evidence_list": [
    "simulation error: state mismatch after reset"
  ]
}
```

Optional fields such as `code_pattern` and `merged_from` may also be present.

The current main-pool target experience types are:

- `spec_compliance`
- `functional_bug_fix`
- `implementation_pattern`

Meta/style/process candidates may still be extracted as intermediate candidates,
but they are expected to be filtered out before entering the main pool.

## What Changed In Recent Versions

### 1. Typed experience schema

- `Experience` now carries `experience_type`, `root_cause_type`, `task_scope`,
  `confidence`, `canonical`, `evidence_list`, and `merged_from`
- this makes later filtering and canonicalization much more reliable

### 2. Stronger local cleanup before merge

- normalize category / type / root cause aliases
- reject meta / style / speculative / workaround-like candidates
- require stronger actionability and evidence signals

### 3. Same-batch consolidation

- reduce duplicate root-cause fragments before pool merge
- expose candidate audit counts at each stage

### 4. Merge-first pool management

- prefer `MERGE` / `REPLACE` / `SKIP` over uncontrolled `INSERT`
- normalize and deduplicate the pool again before save

### 5. Optional split-pool support

- keep one main pool as the default working set
- support deterministic offline derivation of `core` and `extended` views when needed

## Recommended Usage for QiMeng-Agent

For the current QiMeng-Agent integration, the recommended workflow is:

1. runtime writes task snapshots into `logs/run_x/tasks/*.json`
2. `traceEvolve` runs in `--extract-only` mode and writes spool JSONL
3. candidate quality is inspected if needed
4. spool is merged explicitly into one main pool
5. runtime retrieval later consumes the merged pool

Recommended commands:

```bash
python3.8 -m trace_evolve.cli \
  --dir /path/to/qimeng/logs/run_x/tasks \
  --qimeng \
  --extract-only \
  --spool /path/to/experience_spool
```

```bash
python3.8 -m trace_evolve.cli \
  --merge-spool \
  --spool /path/to/experience_spool \
  --pool /path/to/experience_pool.json
```

This keeps extraction, inspection, and pool updates decoupled, which is the
recommended path when pool quality matters.

- postprocessing now normalizes categories, filters weak experiences, and removes near-duplicates before merge
- merge quality uses `quality_score()` and candidate-level selection instead of full-pool prompting every time

Main files:

- `trace_evolve/postprocessor.py`
- `trace_evolve/quality.py`
- `trace_evolve/manager.py`
- `trace_evolve/pipeline.py`

### 3. Spool-based extraction workflow

- extract-only and merge-only were split into explicit CLI/API paths
- spool files are written as one completed JSONL file per extraction batch
- processed spool files move into `merged/`

Main files:

- `trace_evolve/cli.py`
- `trace_evolve/pipeline.py`
- `trace_evolve/spool.py`
- `trace_evolve/manager.py`

### 4. Concurrent merge (two-phase)

Pool merge was previously serial: each new experience called the LLM one at a time (~30s per call), so merging N experiences took N×30s. This was replaced with a two-phase concurrent approach:

- **Phase 1 (concurrent):** All new experiences are evaluated against the pool in parallel using `ThreadPoolExecutor(max_workers=min(N, 8))`. Each worker calls `_merge_single()` to get an LLM merge decision (INSERT / REPLACE / MERGE / SKIP). This phase is read-only against the pool.
- **Phase 2 (serial):** Decisions are executed sequentially with conflict resolution. If a REPLACE or MERGE target was already removed by a prior operation in the same batch, the decision is downgraded to INSERT.

This yields roughly 5× speedup for typical batches (e.g. 10 experiences: ~300s → ~60s).

Main files:

- `trace_evolve/manager.py`

### 5. History recording disabled

The `history` list in `experience_pool.json` was consuming ~60% of file size (200+ KB of 340 KB total) and growing with every merge. History recording has been disabled: `record_operation()` is a no-op and `save()` writes `"history": []`. The pool JSON schema is preserved (top-level keys `experiences`, `history`, `metadata` are all still present) so downstream consumers are unaffected.

Main files:

- `trace_evolve/manager.py`

## Output Files

- `experience_pool.json`: persistent pool with top-level keys `experiences`, `history`, and `metadata`
- `experience_spool/*.jsonl`: extracted candidate experiences waiting to be merged
- `experience_spool/merged/*.jsonl`: already merged spool files
- `intermediate_results/`: optional extraction and merge reports
  - `*_extracted.json`: raw extracted experiences per processed file
  - `merge_*.json`: merge decision summaries per batch
  - `final_report.json`: batch-level summary report

## Verification Notes

- pool JSON shape is intentionally stable because QiMeng-Agent reads it downstream
- prompt templates use Python `.format()`, so literal JSON braces in prompts must stay escaped
- `TaskSegment.render_for_prompt()` and `_extract_legacy()` are intentionally retained as full-context paths
- a successful worker subprocess does not guarantee a spool file; no spool file is written when postprocessed experience count is zero
- when debugging worker extraction, inspect the per-task traceEvolve subprocess logs first

## Related Docs

- `.cursor/rules/project-basics.mdc`: concise project conventions and current architecture summary (local only, not tracked in git)

## License

MIT License
