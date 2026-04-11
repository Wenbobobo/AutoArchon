# Runbook

Use this file when you need the full operating sequence.

## Standard Cycle

1. Confirm `run-root/source`, `run-root/workspace`, and `run-root/artifacts` all exist.
2. Read:
   - `workspace/.archon/RUN_SCOPE.md`
   - `workspace/.archon/PROGRESS.md`
   - `workspace/.archon/supervisor/HOT_NOTES.md`
   - latest `workspace/.archon/task_results/*.md`
3. Run one cycle:

```bash
python3 scripts/supervised_cycle.py \
  --workspace /path/to/run-root/workspace \
  --source /path/to/run-root/source \
  --no-review
```

4. Inspect:
   - `workspace/.archon/supervisor/HOT_NOTES.md`
   - `workspace/.archon/supervisor/violations.jsonl`
   - `workspace/.archon/task_results/`
   - latest `workspace/.archon/logs/iter-*/provers/*.jsonl`
5. If the state is trustworthy, export:

```bash
python3 scripts/export_run_artifacts.py --run-root /path/to/run-root
```

## Escalation Rules

- If theorem headers drift, restore from `source/` or rebuild a fresh `workspace/`.
- If `.archon/` history is copied or mixed, discard the workspace and recreate the run.
- If no progress is visible for repeated cycles, reduce to a single-file run scope.
- If the theorem is false as written, keep the original statement frozen and require a blocker note.
