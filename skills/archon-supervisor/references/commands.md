# Commands

Known-good command patterns:

```bash
bash scripts/install_repo_skill.sh
```

```bash
python3 scripts/create_run_workspace.py \
  --source-root /path/to/source \
  --run-root /path/to/run-root \
  --reuse-lake-from /path/to/warmed-project
```

```bash
python3 scripts/prewarm_project.py /path/to/run-root/workspace
```

If the workspace already copied a warmed `.lake/`, this command now skips `lake exe cache get` automatically and just refreshes the build.

```bash
./init.sh --objective-limit 1 /path/to/run-root/workspace
```

```bash
python3 scripts/supervised_cycle.py \
  --workspace /path/to/run-root/workspace \
  --source /path/to/run-root/source \
  --plan-timeout-seconds 180 \
  --prover-timeout-seconds 240 \
  --prover-idle-seconds 90 \
  --no-review
```

```bash
python3 scripts/export_run_artifacts.py --run-root /path/to/run-root
```

`export_run_artifacts.py` only exports changed Lean files from the run's `source/` tree; warmed `.lake/` packages are ignored, and `task_results/` notes are exported under `artifacts/task-results/`.

```bash
tail -f /path/to/run-root/workspace/.archon/supervisor/HOT_NOTES.md
tail -f /path/to/run-root/workspace/.archon/supervisor/violations.jsonl
tail -f /path/to/run-root/workspace/.archon/logs/iter-*/provers/*.jsonl
```
