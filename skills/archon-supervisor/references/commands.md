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

```bash
./init.sh --objective-limit 1 /path/to/run-root/workspace
```

```bash
python3 scripts/supervised_cycle.py \
  --workspace /path/to/run-root/workspace \
  --source /path/to/run-root/source \
  --no-review
```

```bash
python3 scripts/export_run_artifacts.py --run-root /path/to/run-root
```

```bash
tail -f /path/to/run-root/workspace/.archon/supervisor/HOT_NOTES.md
tail -f /path/to/run-root/workspace/.archon/supervisor/violations.jsonl
tail -f /path/to/run-root/workspace/.archon/logs/iter-*/provers/*.jsonl
```
