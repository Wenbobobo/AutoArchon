# Commands

Known-good command patterns:

```bash
bash scripts/install_repo_skill.sh
```

```bash
uv run --directory /path/to/AutoArchon autoarchon-create-run-workspace \
  --source-root /path/to/source \
  --run-root /path/to/run-root \
  --reuse-lake-from /path/to/warmed-project
```

```bash
uv run --directory /path/to/AutoArchon autoarchon-prewarm-project /path/to/run-root/workspace
```

If the workspace already copied a warmed `.lake/`, this command now skips `lake exe cache get` automatically and just refreshes the build.

```bash
./init.sh --objective-limit 1 /path/to/run-root/workspace
```

```bash
uv run --directory /path/to/AutoArchon autoarchon-supervised-cycle \
  --workspace /path/to/run-root/workspace \
  --source /path/to/run-root/source \
  --plan-timeout-seconds 180 \
  --prover-timeout-seconds 240 \
  --prover-idle-seconds 90 \
  --no-review
```

```bash
uv run --directory /path/to/AutoArchon autoarchon-export-run-artifacts --run-root /path/to/run-root
```

`autoarchon-export-run-artifacts` only exports changed Lean files from the run's `source/` tree; warmed `.lake/` packages are ignored, and `task_results/` notes are exported under `artifacts/task-results/`.

```bash
tail -f /path/to/run-root/workspace/.archon/supervisor/HOT_NOTES.md
tail -f /path/to/run-root/workspace/.archon/supervisor/violations.jsonl
tail -f /path/to/run-root/workspace/.archon/logs/iter-*/provers/*.jsonl
```
