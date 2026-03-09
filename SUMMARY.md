# TraceEvolve Summary

## Current Branch

- Branch: `wch_add_qimeng`

## What Was Added

This branch adds practical support for extracting experiences from QiMeng-Agent benchmark logs.

Main changes:

1. QiMeng CLI support
   - `trace_evolve/cli.py`
   - `--qimeng` mode supports `run_*.json` logs
   - directory mode now searches for QiMeng JSON logs

2. QiMeng log parsing
   - `trace_evolve/extractor.py`
   - `QiMengLogParser` now parses the full benchmark run instead of only the first task
   - aggregated fields now include:
     - `task_count`
     - `task_ids`
     - `tasks`
     - `status_counts`
     - aggregated `errors`

3. Eval-aware extraction
   - `trace_evolve/pipeline.py`
   - `eval.jsonl` feedback is passed as supplemental prompt context
   - raw QiMeng JSON logs are no longer corrupted by appending eval text directly

4. Prompt and merge cleanup
   - `trace_evolve/config.py`
   - extraction and merge prompts were normalized toward English benchmark lessons
   - hardcoded default credentials were removed

5. Merge failure handling
   - `trace_evolve/manager.py`
   - invalid merge responses now raise explicit errors instead of silently dropping experiences

6. Repository hygiene
   - `.gitignore`
   - local artifacts, session notes, generated pools, intermediate outputs, and virtualenv files are ignored
   - `README.md` documents QiMeng usage

## Important Bug Fixes Already Included

These were result-affecting bugs and are already fixed on this branch:

1. Missing API key no longer pretends to use a demo mode
2. QiMeng parsing no longer summarizes only the first task in a multi-task run log
3. Merge parse failures no longer silently drop extracted experiences

## Recommended Command

Run a single QiMeng benchmark log like this:

```bash
python3.8 -m trace_evolve.cli \
  --qimeng \
  --files /path/to/run_YYYYMMDD_HHMMSS.json \
  --pool /path/to/experience_pool.json \
  --api-key "$LLM_API_KEY" \
  --api-base "$LLM_API_BASE" \
  --model "$LLM_MODEL"
```

If environment variables are already set, the CLI arguments can be omitted.

## Expected Behavior

- Experience count can go up, stay flat, or go down
- This is normal because the merge stage supports:
  - `INSERT`
  - `MERGE`
  - `REPLACE`
  - `DELETE`
  - `SKIP`
- A smaller pool is not automatically a bug; it may mean the pool was deduplicated or refined

## Files Most Relevant For Future Work

- `trace_evolve/cli.py`
- `trace_evolve/pipeline.py`
- `trace_evolve/extractor.py`
- `trace_evolve/manager.py`
- `trace_evolve/config.py`
- `README.md`

## Things To Re-Check In Future Sessions

If results regress again, check these first:

1. whether merge operations are too aggressive and delete useful experiences
2. whether the extracted experiences are benchmark-specific enough
3. whether the configured model/base URL still match the actual runtime environment
4. whether `eval.jsonl` is being attached for QiMeng benchmark runs

## Push / PR Reminder

This repo should be pushed from branch `wch_add_qimeng`, not from `main`.
