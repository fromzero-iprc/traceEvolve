# traceEvolve Knowledge Base

## Overview

traceEvolve extracts reusable lessons from agent logs and maintains a curated experience pool for later ICL use.

Current primary integration target is QiMeng-Agent:

- consume run logs or task snapshots
- extract candidate experiences with LLMs
- normalize/filter/deduplicate locally
- merge into `experience_pool.json`
- export high-value experiences for reuse

## Structure

```text
traceEvolve/
├── README.md                      # user-facing architecture and CLI guide
├── AI_HANDOFF.md                  # detailed change history from major refactors
├── .cursor/rules/project-basics.mdc
├── trace_evolve/                  # package code
├── data/                          # pools or local data artifacts
└── intermediate_results/          # extracted and merge reports
```

## Entry Points

- `trace_evolve/cli.py`: CLI entrypoint
- `trace_evolve/pipeline.py`: high-level orchestration
- `trace_evolve/manager.py`: pool merge semantics

Current important CLI modes:

- normal extract + merge
- `--extract-only` to write candidate experiences into spool
- `--merge-spool` to merge spool JSONL files into the main pool
- `--export` to produce ICL text output

## Recent Major Changes

### Wave 1: QiMeng per-task extraction

Files:

- `trace_evolve/extractor.py`
- `trace_evolve/config.py`
- `AI_HANDOFF.md`
- `.cursor/rules/project-basics.mdc`

Main changes:

- `Experience` gained `evidence` and `task_id`
- added `TaskSegment`
- moved from whole-log QiMeng extraction to per-segment extraction
- introduced `SEGMENT_EXTRACTION_PROMPT`

### Wave 2: postprocessing and candidate-based merge

Files:

- `trace_evolve/postprocessor.py`
- `trace_evolve/quality.py`
- `trace_evolve/manager.py`
- `trace_evolve/pipeline.py`
- `trace_evolve/__init__.py`
- `README.md`

Main changes:

- category normalization, filtering, and batch-local dedup before merge
- `quality_score()` for replacement and pool trimming
- candidate-level merge instead of full-pool prompt every time
- updated docs to reflect the new architecture

### Wave 3: spool-based extraction workflow for QiMeng-Agent

Files:

- `trace_evolve/cli.py`
- `trace_evolve/pipeline.py`
- `trace_evolve/manager.py`
- `trace_evolve/spool.py`
- `trace_evolve/extractor.py`

Main changes:

- task snapshots can be extracted without touching the pool
- candidate experiences are written as JSONL spool files
- pool merge is now explicitly runnable as a separate step
- processed spool files move into `merged/`

## Where To Look

| Task | Location | Notes |
|------|----------|-------|
| CLI behavior | `trace_evolve/cli.py` | all supported user commands |
| Extraction logic | `trace_evolve/extractor.py` | legacy + QiMeng per-segment paths |
| Prompt definitions | `trace_evolve/config.py` | extraction and merge prompts |
| Postprocessing | `trace_evolve/postprocessor.py` | normalization, quality, dedup |
| Quality heuristic | `trace_evolve/quality.py` | pool ranking / replace rules |
| Merge behavior | `trace_evolve/manager.py` | candidate search and pool writes |
| Orchestration | `trace_evolve/pipeline.py` | extract-only, merge-only, full pipeline |
| Spool lifecycle | `trace_evolve/spool.py` | file naming, listing, move-to-merged |

## Commands

Single snapshot to spool:

```bash
python3.8 -m trace_evolve.cli \
  --files /path/to/task.json \
  --qimeng \
  --extract-only \
  --spool /path/to/experience_spool
```

One directory of task snapshots to spool:

```bash
python3.8 -m trace_evolve.cli \
  --dir /path/to/run_x/tasks \
  --qimeng \
  --extract-only \
  --spool /path/to/experience_spool
```

Merge spool into pool:

```bash
python3.8 -m trace_evolve.cli \
  --merge-spool \
  --spool /path/to/experience_spool \
  --pool /path/to/experience_pool.json
```

## Guardrails

- Keep `experience_pool.json` top-level schema stable: `experiences`, `history`, `metadata`.
- Pool writes should stay atomic.
- Do not remove `_extract_legacy()` unless the non-QiMeng path is retired everywhere.
- Treat `AI_HANDOFF.md` as the detailed historical record; keep `AGENTS.md` concise and operational.
