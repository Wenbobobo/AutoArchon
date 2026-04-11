# Startup Brief

Read this before touching the run.

- Confirm the run uses `source / workspace / artifacts`, not a mixed benchmark folder.
- Treat `workspace/.archon/RUN_SCOPE.md` as a hard boundary.
- Read `workspace/.archon/supervisor/HOT_NOTES.md` first if it exists.
- Compare changed Lean files against `source/` before trusting a “solved” file.
- Watch `workspace/.archon/task_results/`, `workspace/.archon/logs/`, and `workspace/.archon/supervisor/violations.jsonl`.
- Reject theorem mutation immediately. Restoring fidelity is more important than preserving a compiled but contaminated result.
- Prefer single-cycle supervision with `scripts/supervised_cycle.py` over long blind loops.
- Export milestone artifacts with `scripts/export_run_artifacts.py`.
