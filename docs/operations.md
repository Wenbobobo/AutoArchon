# Operations

Use this document for repeatable single-run operation outside a live chat transcript. For multi-run campaigns, start from [orchestrator.md](orchestrator.md).

## Install Once

```bash
./setup.sh
uv sync --all-groups
bash scripts/install_repo_skill.sh
```

## Create One Run

```bash
uv run --directory /path/to/AutoArchon autoarchon-create-run-workspace \
  --source-root /path/to/source-root \
  --run-root /path/to/run-root \
  --reuse-lake-from /path/to/warmed-project \
  --scope-hint 'FATEM/42.lean'
```

Important metadata:

- `RUN_MANIFEST.json`
- `projectBuildReused`
- `prewarmRequired`
- `allowedFiles`

## Prewarm

For a narrow shard:

```bash
uv run --directory /path/to/AutoArchon autoarchon-prewarm-project \
  /path/to/run-root/workspace \
  --verify-file FATEM/42.lean
```

The goal is scoped `lake env lean` verification instead of unconditional full-project rebuilds.

## Manual Supervisor Flow

```bash
codex exec \
  --skip-git-repo-check \
  --sandbox danger-full-access \
  -c approval_policy=never \
  -c model_reasoning_effort=xhigh \
  --model gpt-5.4 \
  - <<'EOF'
Use $archon-supervisor to supervise this AutoArchon run.
EOF
```

Preferred command pattern:

```bash
uv run --directory /path/to/AutoArchon autoarchon-supervised-cycle \
  --workspace /path/to/run-root/workspace \
  --source /path/to/run-root/source \
  --plan-timeout-seconds 180 \
  --prover-timeout-seconds 240 \
  --tail-scope-objective-threshold 2 \
  --tail-scope-prover-timeout-seconds 360 \
  --prover-idle-seconds 90 \
  --no-review

uv run --directory /path/to/AutoArchon autoarchon-export-run-artifacts \
  --run-root /path/to/run-root
```

The tail-scope override is deliberate: once a run has been narrowed to the last 1-2 files, the supervisor gives each prover more wall-clock time instead of clipping those final attempts at the bulk-run timeout.

## Monitor

```bash
tail -f /path/to/run-root/workspace/.archon/supervisor/HOT_NOTES.md
tail -f /path/to/run-root/workspace/.archon/supervisor/LEDGER.md
tail -f /path/to/run-root/workspace/.archon/supervisor/violations.jsonl
watch -n10 'ls -lt /path/to/run-root/workspace/.archon/task_results/'
```

## Campaign Helpers

```bash
uv run --directory /path/to/AutoArchon autoarchon-refresh-launch-assets \
  --campaign-root /path/to/campaign-root

uv run --directory /path/to/AutoArchon autoarchon-campaign-overview \
  --campaign-root /path/to/campaign-root \
  --markdown

uv run --directory /path/to/AutoArchon autoarchon-campaign-archive \
  --campaign-root /path/to/campaign-root
```

Before starting another teacher on an existing run, inspect `workspace/.archon/supervisor/run-lease.json`.
