# Archon

Archon is an agentic Lean 4 orchestration system for repository-scale formalization. This fork runs on **Codex CLI** while preserving the original `plan -> prover -> review` workflow and `.archon/` state layout.

## Status

- Codex runtime migration is in place across setup, init, loop, review, prompts, and state templates.
- Lean 4.28.0 is the pinned benchmark toolchain.
- The upstream repository is still hard-wired to `claude`, so this fork is the runnable baseline in the current environment.

## Quick Start

```bash
git clone <your-public-fork-url>
cd Archon
./setup.sh
```

`setup.sh` verifies or installs:

- `git`
- `python3`
- `uv`
- `elan`, `lean`, `lake`
- `codex`

It prefers Lean 4.28.0 to match FATE. If `elan` downloads are unreliable, it falls back to a direct Lean release archive install.

## Repository Layout

```text
Archon/
├── archon-loop.sh
├── archonlib/
├── docs/
├── scripts/
├── skills/archon-supervisor/
├── tests/
└── ui/
```

## Workflow Snapshot

```mermaid
flowchart LR
    S[source] --> W[workspace]
    W --> A[.archon state]
    A --> P[plan]
    P --> R[prover pool]
    R --> T[task_results and logs]
    T --> V[supervisor checks]
    V --> E[artifacts export]
```

## Install The Supervisor Skill

```bash
bash scripts/install_repo_skill.sh
```

This installs the repo-owned skill into `$CODEX_HOME/skills/archon-supervisor`. Launch a fresh `codex exec` session after installing it.

## Quick Supervisor Soak Test

Create an isolated run root, prewarm it, initialize the scoped workspace, and then start a fresh Codex session that explicitly invokes `$archon-supervisor`.

```bash
python3 scripts/create_run_workspace.py \
  --source-root /path/to/benchmark-or-project \
  --run-root /path/to/run-root \
  --reuse-lake-from /path/to/warmed-project

python3 scripts/prewarm_project.py /path/to/run-root/workspace

./init.sh --objective-limit 5 --objective-regex '^FATEM/(39|40|41|42|43)\\.lean$' \
  /path/to/run-root/workspace

codex exec --skip-git-repo-check --sandbox danger-full-access \
  -c approval_policy=never --model gpt-5.4 - <<'EOF'
Use $archon-supervisor to supervise this Archon run.
Run root: /path/to/run-root
Source root: /path/to/run-root/source
Workspace root: /path/to/run-root/workspace
Do not stop to give an interim report; keep updating workspace/.archon/supervisor/HOT_NOTES.md and LEDGER.md instead.
EOF
```

The full long-run procedure, monitoring commands, and recovery rules live in [docs/operations.md](docs/operations.md).

## Initialize A Lean Project

```bash
./init.sh /path/to/your-lean-project
```

Useful options:

```bash
./init.sh --objective-limit 1 /path/to/project
./init.sh --objective-limit 5 --objective-regex '^FATEM/(39|40|41|42|43)\\.lean$' /path/to/project
```

Init creates and links:

- `.archon/PROGRESS.md`
- `.archon/AGENTS.md`
- `.archon/RUN_SCOPE.md`
- `.archon/prompts/`
- `.archon/lean4/`
- `.archon/tools/archon-informal-agent.py`
- `.archon/logs/`

## Run The Loop

```bash
./archon-loop.sh /path/to/your-lean-project
```

Useful options:

```bash
./archon-loop.sh --dry-run /path/to/project
./archon-loop.sh --max-iterations 1 /path/to/project
./archon-loop.sh --max-parallel 4 /path/to/project
./archon-loop.sh --no-review /path/to/project
```

The loop reads the scoped objectives from `.archon/PROGRESS.md`, launches one prover process per target file by default, and writes structured logs under `.archon/logs/iter-*`.

## Review

```bash
./review.sh /path/to/your-lean-project
```

This extracts attempt data from the latest prover log, runs the review agent through Codex, and validates the generated proof journal.

## Where To Find Generated Proofs

The source of truth is always the isolated run itself, not the dashboard and not the planner notes.

- Final generated proofs live in the target `.lean` files inside `run-root/workspace/`. Example: `runs/fate-m-compare-codex-rerun-20260411/FATEM/39.lean`.
- Immutable originals live in `run-root/source/`.
- Iteration logs live under `.archon/logs/iter-*`, with per-file prover logs in `.archon/logs/iter-*/provers/*.jsonl`.
- Snapshot diffs for replay live under `.archon/logs/iter-*/snapshots/`.
- Unresolved blockers or theorem-level failure reports live under `.archon/task_results/`.
- Review summaries, milestones, and recommendations live under `.archon/proof-journal/`.
- Supervisor summaries live under `.archon/supervisor/`.
- Exported review bundles live under `run-root/artifacts/`.

To inspect a run in the UI:

```bash
bash ui/start.sh --project /path/to/run-root/workspace
```

The dashboard reads directly from the run's `.archon/` directory. It is a browser for the artifacts above, not a separate source of truth.

## FATE Workflow

Recommended execution order:

1. Keep one pristine benchmark checkout, for example `benchmarks/FATE-M-upstream`.
2. Create a fresh isolated run with `python3 scripts/create_run_workspace.py`.
3. Optionally reuse a warmed `.lake/` cache, but do not reuse another run's `.archon/` state.
4. Prewarm `run-root/workspace/` with `python3 scripts/prewarm_project.py`.
5. Run `./init.sh` with a narrow scope against `run-root/workspace/`.
6. Prefer supervised cycles over blind long loops.
7. Export milestone artifacts with `python3 scripts/export_run_artifacts.py --run-root /path/to/run-root`.
8. Verify solved files with `lake env lean` or the fixed Lake binary before counting them as benchmark results.

For the current FATE-M benchmark notes and result hygiene rules, see [docs/benchmarking.md](docs/benchmarking.md).

## Runtime Notes

- Main model: `ARCHON_CODEX_MODEL` (default `gpt-5.4`)
- Extra Codex flags: `ARCHON_CODEX_EXEC_ARGS`
- Optional search toggle: `ARCHON_CODEX_ENABLE_SEARCH=1`
- Informal provider default: OpenAI via `.archon/tools/archon-informal-agent.py`
- `scripts/prewarm_project.py` removes broken manifest package checkouts before retrying `lake exe cache get` and `lake build`

## Docs Map

- [docs/architecture.md](docs/architecture.md): system workflow, Mermaid, state flow, observability, and extension points
- [docs/benchmarking.md](docs/benchmarking.md): benchmark workflow, artifact hygiene, and current compare-run interpretation
- [docs/agent-registry.md](docs/agent-registry.md): lightweight agent contract registry and proposed future agents
- [docs/operations.md](docs/operations.md): isolated-run workflow, supervisor soak-test commands, monitoring, and recovery
- [docs/roadmaps/supervisor-phase2.md](docs/roadmaps/supervisor-phase2.md): saved implementation roadmap for the supervisor phase
- [ORCHESTRATOR_GUIDE.md](ORCHESTRATOR_GUIDE.md): manual stage orchestration for advanced users
- [ui/README.md](ui/README.md): dashboard behavior and API surface

## Safety

This fork uses `codex exec --dangerously-bypass-approvals-and-sandbox` for unattended execution. Run it only in a workspace you are prepared to fully trust, snapshot, and restore.
