# Operations

Use this document for repeatable long-running Archon execution. The top-level `README.md` only keeps the short entrypoints.

## Preconditions

- Lean and Codex are already installed via `./setup.sh`
- any previous compare run logs are treated as archival, not live state
- the benchmark or source project is available as a pristine checkout

## Create An Isolated Run

```bash
python3 scripts/create_run_workspace.py \
  --source-root /path/to/benchmark-or-project \
  --run-root /path/to/run-root \
  --reuse-lake-from /path/to/warmed-project \
  --scope-hint 'FATEM/(39|40|41|42|43).lean'
```

This creates:

- `/path/to/run-root/source`
- `/path/to/run-root/workspace`
- `/path/to/run-root/artifacts`
- `/path/to/run-root/RUN_MANIFEST.json`

Only `workspace/` is mutable.

## Prewarm And Init

```bash
python3 scripts/prewarm_project.py /path/to/run-root/workspace

./init.sh \
  --objective-limit 5 \
  --objective-regex '^FATEM/(39|40|41|42|43)\\.lean$' \
  /path/to/run-root/workspace
```

If `workspace/.lake/` was copied from a warmed project, `scripts/prewarm_project.py` now detects the existing mathlib cache and skips `lake exe cache get` automatically before running `lake build`.

## Install The Supervisor Skill

```bash
bash scripts/install_repo_skill.sh
```

This installs a symlink into `$CODEX_HOME/skills/archon-supervisor`. Launch a fresh `codex exec` session after that so the skill is picked up.

## Full Supervisor Soak Test

Run this from the repository root:

```bash
codex exec \
  --skip-git-repo-check \
  --sandbox danger-full-access \
  -c approval_policy=never \
  --model gpt-5.4 \
  - <<'EOF'
Use $archon-supervisor to supervise this Archon run.

Repository root: /home/daism/Wenbo/math/Archon
Run root: /path/to/run-root
Source root: /path/to/run-root/source
Workspace root: /path/to/run-root/workspace

Goals:
- keep theorem headers faithful to source
- run repeated supervised cycles until the scoped objectives are solved, a blocker is validated, or an external stop condition is hit
- do not stop to give an interim report; keep writing progress into workspace/.archon/supervisor/HOT_NOTES.md and workspace/.archon/supervisor/LEDGER.md instead
- if you detect theorem mutation, copied .archon history, stale runtime processes, or no-progress loops, correct the issue and continue

Preferred command pattern:
- use python3 scripts/supervised_cycle.py --workspace /path/to/run-root/workspace --source /path/to/run-root/source --plan-timeout-seconds 180 --prover-timeout-seconds 240 --prover-idle-seconds 90 --no-review
- use python3 scripts/export_run_artifacts.py --run-root /path/to/run-root whenever a clean milestone is reached
EOF
```

For a detached shell, wrap the same command in `tmux new -s archon-supervisor` or your own job runner.

For long unattended runs, set a small Codex preflight retry budget so transient network failures do not abort a cycle before `iter-*` logs are created:

```bash
export ARCHON_CODEX_READY_RETRIES=6
export ARCHON_CODEX_READY_RETRY_DELAY_SECONDS=10
```

For focused single-file or blocker-check runs, prefer explicit stage budgets so the supervisor can cut off unproductive search and immediately start the next correction cycle:

```bash
python3 scripts/supervised_cycle.py \
  --workspace /path/to/run-root/workspace \
  --source /path/to/run-root/source \
  --plan-timeout-seconds 180 \
  --prover-timeout-seconds 240 \
  --prover-idle-seconds 90 \
  --no-review
```

`--prover-idle-seconds` watches prover logs, scoped `.lean` files, and live `task_results/`; if none of them move while the prover is marked `running`, the supervisor kills the whole loop and records a `prover_idle_timeout` event.

## Monitoring

Use these from another shell while the supervisor is running:

```bash
tail -f /path/to/run-root/workspace/.archon/supervisor/HOT_NOTES.md
tail -f /path/to/run-root/workspace/.archon/supervisor/LEDGER.md
tail -f /path/to/run-root/workspace/.archon/supervisor/violations.jsonl
tail -f /path/to/run-root/workspace/.archon/logs/iter-*/provers/*.jsonl
watch -n10 'ls -lt /path/to/run-root/workspace/.archon/task_results/'
```

## Recovery Rules

- If theorem headers drift from `source/`, do not keep patching the contaminated file in place. Recopy the file from `source/` or rebuild a fresh `workspace/`.
- If `.archon/` history was copied from another run, discard the workspace and rebuild a fresh isolated run root.
- If repeated stale `archon-loop.sh`, `codex exec`, or `lake serve` processes remain, stop trusting the current run until the supervisor has recorded the contamination and restarted cleanly.
- If the prover keeps a process alive but stops emitting log or file activity, rerun with `--prover-idle-seconds` so the supervisor can cut the loop and preserve a concrete idle-timeout record instead of waiting indefinitely.
- If a supervised cycle fails before a new `iter-*` directory appears, inspect `workspace/.archon/supervisor/last_loop.stderr.log` first. This usually means a transient Codex/network preflight failure rather than a mathematical blocker.
- If the scope keeps spinning with no Lean-file changes and no blocker notes, shrink the scope and continue from a single-file supervised cycle.

## Export Artifacts

When the run hits a trustworthy milestone:

```bash
python3 scripts/export_run_artifacts.py --run-root /path/to/run-root
```

This exports:

- changed Lean files from the run `source/` tree only under `artifacts/proofs/`
- unified diffs under `artifacts/diffs/`
- blocker notes under `artifacts/blockers/`
- supervisor notes under `artifacts/supervisor/`

The artifact index is written to `artifacts/artifact-index.json`.
