# Operations

Use this document for repeatable long-running AutoArchon execution. The top-level `README.md` only keeps the short entrypoints.

If you want to launch three separate teacher agents against disjoint FATE slices, use [docs/teacher-agents.md](teacher-agents.md) as the operator handoff. If you want one top-level Codex session to own those teachers as a campaign, use [docs/orchestrator.md](orchestrator.md). This file stays focused on the single-run operational baseline.

## Preconditions

- Lean and Codex are already installed via `./setup.sh`
- any previous compare run logs are treated as archival, not live state
- the benchmark or source project is available as a pristine checkout

## Create An Isolated Run

```bash
uv run --directory /path/to/AutoArchon autoarchon-create-run-workspace \
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
uv run --directory /path/to/AutoArchon autoarchon-prewarm-project /path/to/run-root/workspace

./init.sh \
  --objective-limit 5 \
  --objective-regex '^FATEM/(39|40|41|42|43)\\.lean$' \
  /path/to/run-root/workspace
```

If `workspace/.lake/` was copied from a warmed project, `autoarchon-prewarm-project` now detects the existing mathlib cache and skips `lake exe cache get` automatically before running `lake build`.

## Install Repo-Owned Skills

```bash
bash scripts/install_repo_skill.sh
```

This installs symlinks into `$CODEX_HOME/skills/` for both `archon-supervisor` and `archon-orchestrator`. Launch a fresh Codex session after that so the skills are picked up.

## Full Supervisor Soak Test

Run this from the repository root:

```bash
codex exec \
  --skip-git-repo-check \
  --sandbox danger-full-access \
  -c approval_policy=never \
  -c model_reasoning_effort=xhigh \
  --model gpt-5.4 \
  - <<'EOF'
Use $archon-supervisor to supervise this AutoArchon run.

Repository root: /path/to/AutoArchon
Run root: /path/to/run-root
Source root: /path/to/run-root/source
Workspace root: /path/to/run-root/workspace

Goals:
- keep theorem headers faithful to source
- run repeated supervised cycles until the scoped objectives are solved, a blocker is validated, or an external stop condition is hit
- do not stop to give an interim report; keep writing progress into workspace/.archon/supervisor/HOT_NOTES.md and workspace/.archon/supervisor/LEDGER.md instead
- if you detect theorem mutation, copied .archon history, stale runtime processes, or no-progress loops, correct the issue and continue

Preferred command pattern:
- use `uv run --directory /path/to/AutoArchon autoarchon-supervised-cycle --workspace /path/to/run-root/workspace --source /path/to/run-root/source --plan-timeout-seconds 180 --prover-timeout-seconds 240 --prover-idle-seconds 90 --no-review`
- use `uv run --directory /path/to/AutoArchon autoarchon-export-run-artifacts --run-root /path/to/run-root` whenever a clean milestone is reached
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
uv run --directory /path/to/AutoArchon autoarchon-supervised-cycle \
  --workspace /path/to/run-root/workspace \
  --source /path/to/run-root/source \
  --plan-timeout-seconds 180 \
  --prover-timeout-seconds 240 \
  --prover-idle-seconds 90 \
  --no-review
```

`--prover-idle-seconds` watches prover logs, scoped `.lean` files, and live `task_results/`; if none of them move while the prover is marked `running`, the supervisor kills the whole loop and records a `prover_idle_timeout` event.

If the idle cutoff happens after the prover has already written a clean changed file or a durable `task_results/` note, `autoarchon-supervised-cycle` now re-verifies that artifact before deciding the final status. Use `--changed-file-verify-template` when the default `timeout 30s lake env lean {file}` check is not the right verifier for your project.

Each supervised run now writes `workspace/.archon/supervisor/run-lease.json`. Treat this as the authoritative run-local ownership signal. Do not infer contamination from unrelated host-level `codex exec`, `archon-loop.sh`, or `lean-lsp-mcp` processes that are not tied to the run root.

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
- If `workspace/.archon/supervisor/run-lease.json` shows another live supervisor or loop still owns the run, do not start a second teacher on the same workspace.
- If the prover keeps a process alive but stops emitting log or file activity, rerun with `--prover-idle-seconds` so the supervisor can cut the loop and preserve a concrete idle-timeout record instead of waiting indefinitely.
- If a supervised cycle fails before a new `iter-*` directory appears, inspect `workspace/.archon/supervisor/last_loop.stderr.log` first. This usually means a transient Codex/network preflight failure rather than a mathematical blocker.
- If a teacher session disappears after writing useful changed files or blocker notes, run:

```bash
uv run --directory /path/to/AutoArchon autoarchon-supervised-cycle \
  --workspace /path/to/run-root/workspace \
  --source /path/to/run-root/source \
  --recovery-only \
  --skip-process-check
```

This finalizes validation, lessons, supervisor notes, and the run lease without rerunning the proof search.
- If the scope keeps spinning with no Lean-file changes and no task results, shrink the scope and continue from a single-file supervised cycle.

## Export Artifacts

When the run hits a trustworthy milestone:

```bash
uv run --directory /path/to/AutoArchon autoarchon-export-run-artifacts --run-root /path/to/run-root
```

This exports:

- changed Lean files from the run `source/` tree only under `artifacts/proofs/`
- unified diffs under `artifacts/diffs/`
- exported task-result notes under `artifacts/task-results/`
- deterministic validation verdicts under `artifacts/validation/`
- lesson summaries under `artifacts/lessons/`
- supervisor notes under `artifacts/supervisor/`

The artifact index is written to `artifacts/artifact-index.json`.
