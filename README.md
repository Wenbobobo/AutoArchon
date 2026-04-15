# AutoArchon

AutoArchon is a Codex-first Lean 4 proving system for long-running benchmark and formalization campaigns. It keeps the inner `plan -> prover -> review` loop, then adds an outer control plane for operator guidance, watchdog recovery, proof export, postmortem archiving, and lesson accumulation.

## Default Path

The recommended outer path is now interactive:

`interactive campaign-operator -> mission brief + resolved spec + operator journal -> watchdog -> orchestrator-agent -> supervisor-agent`

- `campaign-operator` is the only user-facing outer owner name.
- `watchdog` is the mechanical reliability wrapper around the orchestrator.
- `teacher` means the Codex session that carries one `supervisor-agent`; it is not a separate logical runtime role.
- `manager-agent` is not part of the default runtime path. The archived future note lives in [docs/archive/manager-agent.md](docs/archive/manager-agent.md).

## System Map

```mermaid
flowchart TD
    H[Human] --> OP[interactive campaign-operator]
    OP --> BRIEF[control/mission-brief.md]
    OP --> SPEC[control/launch-spec.resolved.json]
    OP --> JOURNAL[control/operator-journal.md]
    SPEC --> WD[watchdog]
    WD --> ORCH[orchestrator-agent]
    ORCH --> RUNS[runs/<id>/]
    RUNS --> CTRL[control/launch-teacher.sh]
    CTRL --> TEACHER[teacher Codex session]
    TEACHER --> SUP[supervisor-agent]
    SUP --> LOOP[autoarchon-supervised-cycle]
    LOOP --> PLAN[plan-agent]
    PLAN --> PROVER[prover-agent]
    PROVER --> REVIEW[review-agent]
    PROVER --> VALIDATOR[statement-validator]
    PROVER -. optional bounded help via runtime-config.toml .-> HELPER[helper-prover-agent]
    VALIDATOR --> FINAL[reports/final/]
    WD --> POST[reports/postmortem/]
    FINAL --> LESSONS[reports/final/lessons/lesson-records.jsonl]
    POST --> POSTLESSONS[reports/postmortem/lessons/lesson-records.jsonl]
    LESSONS -. future retrieval .-> MATHLIB[mathlib-agent]
```

## Install

```bash
git clone <your-fork-or-upstream-url>
cd AutoArchon
./setup.sh
uv sync --all-groups
bash scripts/install_repo_skill.sh
```

`setup.sh` verifies `uv`, `elan`, `lean`, `lake`, and `codex`. After installing repo skills, start a fresh Codex session so `$archon-orchestrator` and `$archon-supervisor` are available.

## Fastest Campaign Start

This is the main user path.

1. Prepare benchmark clones under one root, for example:

```text
/path/to/benchmarks/FATE-M-upstream
/path/to/benchmarks/FATE-H-upstream
/path/to/benchmarks/FATE-X-upstream
```

2. Start an interactive operator session:

```bash
ARCHON_ROOT=/path/to/AutoArchon \
MODEL=gpt-5.4 \
REASONING_EFFORT=xhigh \
bash /path/to/AutoArchon/scripts/start_campaign_operator.sh
```

3. Generate a paste-ready operator prompt:

```bash
uv run --directory /path/to/AutoArchon autoarchon-render-operator-prompt \
  --repo-root /path/to/AutoArchon \
  --source-root /path/to/benchmarks/FATE-M-upstream \
  --campaign-root /path/to/runs/campaigns/20260414-fate-m-full \
  --reuse-lake-from /path/to/benchmarks/FATE-M-upstream \
  --template /path/to/AutoArchon/campaign_specs/fate-m-full.json \
  --match-regex '^FATEM/.*\\.lean$' \
  --shard-size 8 \
  --run-id-mode index \
  --run-id-prefix teacher-m
```

4. Paste the rendered prompt into the interactive Codex session. It will look like:

```text
Use $archon-orchestrator to own this AutoArchon campaign.

Repository root: /path/to/AutoArchon
Source root: /path/to/benchmarks/FATE-M-upstream
Campaign root: /path/to/runs/campaigns/20260414-fate-m-full
Reuse lake from: /path/to/benchmarks/FATE-M-upstream
Match regex: '^FATEM/.*\\.lean$'
Shard size: 8
Run id mode: index

Before launching anything:
- create or refresh `control/mission-brief.md`
- create or refresh `control/launch-spec.resolved.json`
- append the initial decision to `control/operator-journal.md`

Then:
- launch or resume the watchdog
- monitor progress
- prefer deterministic recovery commands
- finalize only validation-backed proofs and blockers
```

5. The operator should leave behind these three files before the watchdog starts:

- `control/mission-brief.md`
- `control/launch-spec.resolved.json`
- `control/operator-journal.md`

## Progress Watching

Control-plane commands are terminal commands for machine-readable state. They are not the web UI.

For a quick loop:

```bash
bash scripts/watch_campaign.sh /path/to/runs/campaigns/20260414-fate-m-full
```

For a one-shot snapshot:

```bash
uv run --directory /path/to/AutoArchon autoarchon-campaign-overview \
  --campaign-root /path/to/runs/campaigns/20260414-fate-m-full \
  --markdown
```

That command also refreshes these lightweight observability surfaces by default:

- `control/progress-summary.md`
- `control/progress-summary.json`

The optional web UI is still useful for deep inspection of one run:

```bash
bash ui/start.sh --project /path/to/run-root/workspace
```

## Shortcut: Scripted Start

If you already know the exact scope and just want a reproducible shortcut, keep using the spec path.

Generate a resolved launch spec from a tracked template:

```bash
uv run --directory /path/to/AutoArchon autoarchon-init-campaign-spec \
  --template /path/to/AutoArchon/campaign_specs/fate-m-full.json \
  --benchmark-root /path/to/benchmarks \
  --campaigns-root /path/to/runs/campaigns \
  --run-specs-root /path/to/runs/campaigns/_run_specs \
  --date-tag 20260414-nightly \
  --model gpt-5.4 \
  --reasoning-effort xhigh
```

Then launch:

```bash
uv run --directory /path/to/AutoArchon autoarchon-launch-from-spec \
  --spec-file /path/to/runs/campaigns/_run_specs/20260414-nightly-fate-m-full.launch.json
```

For the bundled nightly three-campaign shortcut:

```bash
export MODEL=gpt-5.4
export REASONING_EFFORT=xhigh
export BENCHMARK_ROOT=/path/to/benchmarks
export CAMPAIGNS_ROOT=/path/to/runs/campaigns
export RUN_SPECS_ROOT=/path/to/runs/campaigns/_run_specs
export FATE_DATE_TAG=$(date +%Y%m%d-nightly)

bash scripts/start_fate_overnight_watchdogs.sh
```

## Quick Supervisor Soak Test

For one isolated run without the full campaign layer, go straight to `$archon-supervisor` and [docs/operations.md](docs/operations.md).

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
```

Single-run progress lands in:

- `workspace/.archon/supervisor/progress-summary.md`
- `workspace/.archon/supervisor/progress-summary.json`

In the exact-route tail-scope case, those files now tell you whether the planner was skipped intentionally:

- `planFastPathApplied = true`
- `planFastPathReason = "known_routes"`
- live `phase`, `planStatus`, `proverStatus`, and active prover rows under `liveRuntime`

## Optional Helper Provider

Each initialized workspace now gets:

- `.archon/runtime-config.toml`
- `.archon/tools/archon-helper-prover-agent.py`
- `.archon/tools/archon-informal-agent.py`

`runtime-config.toml` is the canonical runtime policy file. The helper is disabled by default. To enable it during `init.sh`, set environment such as:

```bash
export ARCHON_HELPER_PROVIDER=openai
export ARCHON_HELPER_MODEL=gpt-5.4
```

The generated file includes helper policy for both the `plan` and `prover` phases, plus observability toggles such as `write_progress_surface`.

OpenAI-compatible providers such as DeepSeek should still use `provider = "openai"` in `.archon/runtime-config.toml`, then point `api_key_env` and `base_url_env` at the compatible endpoint. Legacy `.archon/helper-provider.json` is still accepted as a fallback, but new workspaces should use the TOML file.

## Where Proofs and Lessons End Up

- Mutable proof search happens only under `runs/<id>/workspace/`.
- Immutable originals stay under `runs/<id>/source/`.
- Per-run exported bundles live under `runs/<id>/artifacts/`.
- Campaign-level accepted outputs live under `reports/final/`.
- Archived stopped or degraded samples live under `reports/postmortem/`.

Concrete paths:

- live edited theorem file: `runs/<id>/workspace/<rel-path>.lean`
- live task report: `runs/<id>/workspace/.archon/task_results/<rel-path-with-slashes-replaced>.md`
- live validation record: `runs/<id>/workspace/.archon/validation/<rel-path-with-slashes-replaced>.json`
- exported per-run proof bundle: `runs/<id>/artifacts/proofs/<rel-path>`
- `reports/final/proofs/<run>/`
- `reports/final/blockers/<run>/`
- `reports/final/validation/<run>/`
- `reports/final/lessons/lesson-records.jsonl`
- `reports/final/lessons/lesson-clusters.json`
- `reports/final/lessons/lesson-clusters.md`
- `reports/final/runs/<run>/run-summary.json`
- `reports/postmortem/postmortem-summary.json`
- `reports/postmortem/lessons/lesson-records.jsonl`
- `reports/postmortem/lessons/lesson-clusters.json`
- `reports/postmortem/lessons/lesson-clusters.md`

For mathematician review, prefer exported proofs and validation-backed artifacts under `artifacts/` or `reports/final/`, not the live workspace alone.
For debugging an active teacher, inspect the live workspace first; for acceptance or human review after a run is done, prefer `artifacts/` and `reports/final/`.

To rebuild lesson hotspots manually:

```bash
uv run --directory /path/to/AutoArchon autoarchon-lesson-clusters \
  --campaign-root /path/to/runs/campaigns/20260414-fate-m-full \
  --markdown
```

## Storage Hygiene

If the working tree grows very large, the usual culprit is rebuildable Lean caches under `runs/**/workspace/.lake` or older standalone run roots with a top-level `.lake`, not the repo itself.

Audit first:

```bash
uv run --directory /path/to/AutoArchon autoarchon-storage-report \
  --root /path/to/math/runs \
  --markdown
```

For a top-level retention view across `runs`, `benchmarks`, temp roots, and legacy workspaces:

```bash
uv run --directory /path/to/AutoArchon autoarchon-storage-report \
  --root /path/to/math \
  --retention \
  --markdown
```

By default, the retention report treats canonical benchmark clones under `benchmarks/` as shared Lean projects to keep, not disposable run roots.

Dry-run a safe reclaim plan:

```bash
uv run --directory /path/to/AutoArchon autoarchon-storage-report \
  --root /path/to/math/runs \
  --prune-workspace-lake \
  --prune-broken-prewarm
```

Execute the same plan:

```bash
uv run --directory /path/to/AutoArchon autoarchon-storage-report \
  --root /path/to/math/runs \
  --prune-workspace-lake \
  --prune-broken-prewarm \
  --execute
```

This only targets cache-heavy `workspace/.lake` directories on inactive runs plus broken `.lake.prewarm-*` directories. It does not delete exported proofs, validation artifacts, or final reports.
The report now also distinguishes stale active leases from genuinely protected live runs, which makes overnight cleanup much easier to trust.

For future unattended runs, the terminal path can prune automatically too:

```bash
uv run --directory /path/to/AutoArchon autoarchon-orchestrator-watchdog \
  --campaign-root /path/to/campaign-root \
  --prune-workspace-lake \
  --prune-broken-prewarm
```

`autoarchon-finalize-campaign` and `autoarchon-campaign-archive` expose the same two flags. The tracked FATE full-campaign templates now opt into that explicit post-terminal prune so overnight runs do not keep historical `workspace/.lake` caches by default.

## Repository Layout

```text
AutoArchon/
├── agents/
├── archonlib/
├── campaign_specs/
├── docs/
├── scripts/
├── skills/
├── tests/
└── ui/
```

- `agents/`: explicit runtime and future role contracts.
- `archonlib/`: control-plane and runtime library code.
- `campaign_specs/`: tracked benchmark launch templates.
- `scripts/`: public `uv run` entrypoints and operator shell wrappers.
- `skills/`: repo-owned Codex skills for outer-owner and teacher sessions.
- `docs/`: architecture, operations, operator workflow, and archive notes.
- `tests/`: runtime, CLI, watchdog, docs-contract, and registry coverage.

## Docs

- [docs/campaign-operator.md](docs/campaign-operator.md): recommended interactive operator workflow, control files, recovery, and closeout.
- [docs/architecture.md](docs/architecture.md): global workflow, state surfaces, artifact boundaries, and extension points.
- [docs/operations.md](docs/operations.md): single-run operational baseline, prewarm, soak-test commands.
- [docs/teacher-agents.md](docs/teacher-agents.md): one-run-per-teacher launch and monitoring.
- [docs/agent-registry.md](docs/agent-registry.md): runtime role contracts and future role notes.
- [docs/roadmaps/control-plane-phase5.md](docs/roadmaps/control-plane-phase5.md): current unattended-phase roadmap and remaining high-ROI work.
- [docs/archive/manager-agent.md](docs/archive/manager-agent.md): archived note for the future multi-campaign policy role.
