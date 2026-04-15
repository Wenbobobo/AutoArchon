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
For wider multi-file shards, generated launch assets now sample up to 4 representative `--verify-file` paths and expose that as `scoped_verify_sample` in campaign status, instead of defaulting straight to a full `lake build`.

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
  --tail-scope-objective-threshold 4 \
  --tail-scope-plan-timeout-seconds 300 \
  --tail-scope-prover-timeout-seconds 360 \
  --prover-idle-seconds 90 \
  --no-review

uv run --directory /path/to/AutoArchon autoarchon-export-run-artifacts \
  --run-root /path/to/run-root
```

The tail-scope override is deliberate: once a run has been narrowed to the last 1-4 files, the supervisor gives both the planner and each prover more wall-clock time instead of clipping those final attempts at the bulk-run timeout.

## Monitor

```bash
tail -f /path/to/run-root/workspace/.archon/supervisor/HOT_NOTES.md
tail -f /path/to/run-root/workspace/.archon/supervisor/LEDGER.md
tail -f /path/to/run-root/workspace/.archon/supervisor/progress-summary.md
tail -f /path/to/run-root/workspace/.archon/supervisor/violations.jsonl
watch -n10 'ls -lt /path/to/run-root/workspace/.archon/task_results/'
```

`workspace/.archon/supervisor/progress-summary.md` and `progress-summary.json` are the lightweight single-run observability surfaces. They summarize scope completion, new task results, latest iteration, and observed helper notes without opening the full campaign layer.
During a live long-running cycle, the same files now refresh with `liveRuntime` fields such as current phase, prover status, and active prover files, so you do not need to wait for the cycle to finish before seeing whether planning or proving is still moving.
If the supervisor detects that every remaining tail-scope objective already has a recorded exact route or prevalidated blocker route, the same surface will show:

- `planFastPathApplied = true`
- `planFastPathReason = "known_routes"`

That means the initial planner pass was skipped on purpose and the cycle moved straight into prover work.

For one accepted run, the main evidence paths are:

- live edited theorem: `workspace/<rel-path>.lean`
- live task report: `workspace/.archon/task_results/<file>.md`
- live validation: `workspace/.archon/validation/<file>.json`
- exported bundle: `artifacts/`

## Campaign Helpers

```bash
uv run --directory /path/to/AutoArchon autoarchon-refresh-launch-assets \
  --campaign-root /path/to/campaign-root

uv run --directory /path/to/AutoArchon autoarchon-campaign-overview \
  --campaign-root /path/to/campaign-root \
  --markdown

uv run --directory /path/to/AutoArchon autoarchon-campaign-archive \
  --campaign-root /path/to/campaign-root \
  --prune-workspace-lake \
  --prune-broken-prewarm
```

Before starting another teacher on an existing run, inspect `workspace/.archon/supervisor/run-lease.json`.

## Storage Hygiene

When disk usage spikes, inspect the run cache layer before deleting benchmark clones or reports. This now covers both campaign-style `runs/**/workspace/.lake` caches and older standalone run roots that keep a top-level `.lake`.

```bash
uv run --directory /path/to/AutoArchon autoarchon-storage-report \
  --root /path/to/math/runs \
  --markdown
```

For a broader retention pass over `runs`, `benchmarks`, and temp roots:

```bash
uv run --directory /path/to/AutoArchon autoarchon-storage-report \
  --root /path/to/math \
  --retention \
  --markdown
```

In the current policy, canonical benchmark clones under `benchmarks/` are treated as shared reusable inputs. The retention report will mark them as `keep_shared_clone` unless you intentionally adopt a different rehydrate strategy later.
It now also breaks out each benchmark clone's total size and `.lake` size. The intended policy is:

- keep the clone itself as a shared input
- treat clone-local `.lake/` as the emergency reclaim knob only when disk pressure matters more than warm-start latency
- if you prune a benchmark clone's `.lake/`, rerun `autoarchon-prewarm-project` before the next campaign that depends on it

The main reclaim target is inactive `runs/**/workspace/.lake`. To dry-run a reclaim plan:

```bash
uv run --directory /path/to/AutoArchon autoarchon-storage-report \
  --root /path/to/math/runs \
  --prune-workspace-lake \
  --prune-broken-prewarm
```

Add `--execute` only after reviewing the candidate list. This keeps source snapshots, workspaces, artifacts, and final reports, and removes only rebuildable cache directories.
The report distinguishes stale active leases from truly protected live runs so operators can see why a cache is still blocked.

For unattended terminal cleanup, the same two flags are available on:

- `autoarchon-orchestrator-watchdog`
- `autoarchon-finalize-campaign`
- `autoarchon-campaign-archive`

The tracked FATE full-campaign templates now enable those prune flags explicitly, while ad-hoc campaigns stay conservative unless you set them yourself.
