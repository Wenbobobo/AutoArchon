# Campaign Operator

Use this page when you are operating AutoArchon at the campaign level.

The recommended runtime path is:

`interactive campaign-operator -> mission brief + resolved spec + operator journal -> autoarchon-orchestrator-watchdog -> orchestrator-agent`

## Role Split

- `campaign-operator`: the outer user-facing owner. It interprets user intent, chooses scope, writes the campaign contract, launches or resumes one campaign, reads machine state, and decides whether to recover, finalize, or archive.
- `watchdog`: the concrete reliability wrapper. It refreshes owner lease, applies bounded restart and cooldown policy, and keeps the campaign moving after owner loss or stalls.
- `orchestrator-agent`: the inner campaign owner inside one root. It plans shards, launches teachers, runs deterministic recovery, and finalizes accepted artifacts.
- `supervisor-agent`: the one-run owner carried by a teacher Codex session.

`manager-agent` is not part of the default runtime path. The archived future note is [archive/manager-agent.md](archive/manager-agent.md).

## Operator File Contract

The operator owns three campaign-level files:

- `control/mission-brief.md`
- `control/launch-spec.resolved.json`
- `control/operator-journal.md`

They have different jobs:

- `mission-brief.md`: human-readable contract for goal, success criteria, constraints, scope, and watch items.
- `launch-spec.resolved.json`: machine-readable launch contract for source root, campaign root, shard policy, model, and watchdog settings.
- `operator-journal.md`: timestamped owner decisions for launch, recovery, archive, shard changes, and final acceptance.

Before a long unattended run, the operator should replace scaffolded placeholders in the mission brief and add an initial journal block.

## Recommended Interactive Start

Create the local helper env once:

```bash
cp /path/to/AutoArchon/examples/helper.env.example /path/to/AutoArchon/examples/helper.env
$EDITOR /path/to/AutoArchon/examples/helper.env
```

`scripts/start_campaign_operator.sh` auto-loads `examples/helper.env` when present, so the operator session inherits helper and observability defaults before it launches any campaign.
For `ARCHON_HELPER_API_KEY_ENV` and `ARCHON_HELPER_BASE_URL_ENV`, you can use either env-var names or direct inline values. Generated teacher launchers normalize inline values into provider-default env vars before `init.sh` and `codex exec`, so new runs do not need secrets copied into workspace config files.

Start Codex:

```bash
cd /path/to/AutoArchon
source examples/helper.env
codex -C /path/to/AutoArchon --model gpt-5.4 --config "model_reasoning_effort=xhigh"
```

Then fill [docs/templates/campaign-operator-prompt-template.md](templates/campaign-operator-prompt-template.md) and paste it into Codex, or start with a natural-language intake message such as:

```text
Use $archon-orchestrator to own this AutoArchon campaign.

Repository root: /path/to/AutoArchon
Source root: /path/to/benchmarks/FATE-M-upstream
Campaign root: /path/to/runs/campaigns/20260414-fate-m-full
Reuse lake from: /path/to/benchmarks/FATE-M-upstream

Real user objective:
- run a benchmark-faithful FATE-M campaign
- keep helper enabled unless the contract forbids it
- ask intake questions before launch when scope or success criteria are unclear
```

The operator should translate that intake into `control/mission-brief.md`, `control/launch-spec.resolved.json`, and `control/operator-journal.md`, then validate the launch contract before starting the watchdog:

```bash
uv run --directory /path/to/AutoArchon autoarchon-validate-launch-contract \
  --campaign-root /path/to/runs/campaigns/20260414-fate-m-full
```

If you want a deterministic intake scaffold before the interactive review step, use:

```bash
uv run --directory /path/to/AutoArchon autoarchon-init-operator-intake \
  --repo-root /path/to/AutoArchon \
  --campaign-root /path/to/runs/campaigns/20260414-fate-m-full \
  --source-root /path/to/benchmarks/FATE-M-upstream \
  --objective "Run a benchmark-faithful FATE-M campaign on the warmed local clone." \
  --campaign-mode benchmark_faithful \
  --match-regex '^FATEM/.*\\.lean$' \
  --shard-size 8 \
  --run-id-prefix teacher-m
```

That command writes the three operator-owned control files immediately, but the interactive operator should still review and refine them before unattended launch.

For comment-only/open-problem source files, the operator should now expect the inner `autoformalize` stage to leave behind `.archon/formalization/<file>.json` contracts and matching validation payloads. A run is not accepted merely because the file compiles after introducing some definitions; the formalization still has to preserve the named structures and constraints from the source bundle.

## Optional Wrapper

If you prefer a thin wrapper that auto-loads `examples/helper.env` and pins the repo defaults, you can still use:

```bash
ARCHON_ROOT=/path/to/AutoArchon \
MODEL=gpt-5.4 \
REASONING_EFFORT=xhigh \
bash /path/to/AutoArchon/scripts/start_campaign_operator.sh
```

`scripts/start_campaign_operator.sh` is now a convenience wrapper, not the primary user-facing path.

## Advanced: Rendered Prompt Path

Render a paste-ready operator prompt when you want a fully rendered handoff instead of free-form intake:

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

Paste the rendered prompt into Codex. It should look like:

```text
Use $archon-orchestrator to own this AutoArchon campaign.

Repository root: /path/to/AutoArchon
Source root: /path/to/benchmarks/FATE-M-upstream
Campaign root: /path/to/runs/campaigns/20260414-fate-m-full
Reuse lake from: /path/to/benchmarks/FATE-M-upstream
Helper env file: /path/to/AutoArchon/examples/helper.env
Match regex: '^FATEM/.*\\.lean$'
Shard size: 8
Run id mode: index

Before launching anything:
- create or refresh `control/mission-brief.md`
- create or refresh `control/launch-spec.resolved.json`
- append the initial decision to `control/operator-journal.md`
- keep helper enabled by default unless the run contract explicitly forbids it

Then:
- launch or resume the watchdog
- monitor progress
- prefer deterministic recovery commands
- finalize only validation-backed proofs and blockers
```

## Detailed TODO

The operator should follow this checklist in order:

1. Confirm `Repository root`, `Source root`, `Campaign root`, and warmed `.lake` reuse path.
2. Create or refresh `control/mission-brief.md`.
3. Create or refresh `control/launch-spec.resolved.json`.
4. Append the starting decision to `control/operator-journal.md`.
5. Run `autoarchon-validate-launch-contract` before the watchdog.
6. Launch or resume the watchdog.
7. Use `autoarchon-campaign-status` and `autoarchon-campaign-overview` as the primary truth surfaces.
8. Prefer `autoarchon-campaign-recover --run-id <id> --execute` over ad hoc recovery.
9. Record every recovery, archive, scope change, and finalization decision in `control/operator-journal.md`.
10. Finalize or archive only after machine state and exported artifacts agree.

## Progress Watching

These are local terminal commands, not the web UI:

```bash
bash scripts/watch_campaign.sh /path/to/campaign-root

uv run --directory /path/to/AutoArchon autoarchon-campaign-overview \
  --campaign-root /path/to/campaign-root \
  --markdown

uv run --directory /path/to/AutoArchon autoarchon-campaign-status \
  --campaign-root /path/to/campaign-root

uv run --directory /path/to/AutoArchon autoarchon-campaign-observe \
  --campaign-root /path/to/campaign-root \
  --bind 0.0.0.0 \
  --port 8765
```

For the fastest newcomer-facing snapshot, open either of these files after `autoarchon-campaign-overview` runs:

- `control/progress-summary.md`
- `control/progress-summary.json`
- `control/progress-summary.html`

Treat those file-backed summaries as the canonical observability surface. `control/progress-summary.json` stays canonical, `control/progress-summary.md` is the terminal-friendly mirror, and `control/progress-summary.html` is the low-friction browser mirror generated from the same overview payload. `autoarchon-campaign-observe` only refreshes and serves those same files for remote viewing. The browser UI is optional supplementary inspection for one run when you need deeper browsing.
Each run also writes `runs/<id>/control/helper-effective-config.json` when its launcher starts, which is the quickest way to confirm helper provider/model/env binding drift before debugging prover notes.

Trust these campaign-level files before reacting to terminal noise:

- `control/mission-brief.md`
- `control/launch-spec.resolved.json`
- `control/operator-journal.md`
- `control/owner-mode.json`
- `control/owner-lease.json`
- `control/orchestrator-watchdog.json`
- `control/progress-summary.md`
- `control/progress-summary.json`
- `control/progress-summary.html`
- `campaign-status.json`
- `reports/final/compare-report.json`
- `reports/postmortem/postmortem-summary.json`

For final acceptance review, use:

- `reports/final/proofs/`
- `reports/final/blockers/`
- `reports/final/validation/`

Run-level `artifacts/proofs/` can contain partial-progress edits that were useful during recovery but were not ultimately accepted as final proofs.
For comment-only/open-problem runs, also inspect `runs/<id>/workspace/.archon/formalization/` and `runs/<id>/workspace/.archon/informal/*-autoformalize.md`. If the live workspace weakened the source object, relaunch now resets the stale live state and restarts from the formalization contract plus regenerated route note instead of inheriting the fake proof closure.

## Shortcut: Scripted Bootstrap

Use this only when the scope is already fully known and you want a reproducible shortcut.

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

For non-benchmark formalization or open-problem campaigns, prefer the generic template:

```bash
uv run --directory /path/to/AutoArchon autoarchon-init-campaign-spec \
  --template /path/to/AutoArchon/campaign_specs/formalization-default.json \
  --source-roots-root /path/to/source-roots \
  --source-subdir riemann-upstream \
  --campaign-slug riemann-local-formalization \
  --campaigns-root /path/to/runs/campaigns \
  --run-specs-root /path/to/runs/campaigns/_run_specs \
  --date-tag 20260414-open \
  --model gpt-5.4 \
  --reasoning-effort xhigh
```

For a dedicated open-problem run, prefer the explicit template:

```bash
uv run --directory /path/to/AutoArchon autoarchon-init-campaign-spec \
  --template /path/to/AutoArchon/campaign_specs/open-problem-default.json \
  --source-roots-root /path/to/source-roots \
  --source-subdir riemann-upstream \
  --campaign-slug riemann-open-problem \
  --campaigns-root /path/to/runs/campaigns \
  --run-specs-root /path/to/runs/campaigns/_run_specs \
  --date-tag 20260414-open \
  --model gpt-5.4 \
  --reasoning-effort xhigh
```

`--source-subdir` selects the warmed source clone under the generic source-roots directory, and `--campaign-slug` keeps the campaign naming independent from the tracked template filename.

If the upstream source arrives as a JSON problem pack with `informal_statement` and `formal_statement` fields, materialize a normal Lean source root before intake:

```bash
uv run --directory /path/to/AutoArchon autoarchon-materialize-problem-pack \
  --input-json /path/to/benchmarks/FATE-X-upstream/FATE-X.json \
  --output-root /path/to/benchmarks/Natural-language/fatex-natural-smoke \
  --problem-id 1 \
  --problem-id 2 \
  --force
```

Then treat `/path/to/benchmarks/Natural-language/fatex-natural-smoke` as the `Source root` for the operator or the `--source-subdir` target under a shared source-roots directory.

If the upstream source is a markdown question table from an open-problem note pack, materialize a declaration-free Lean source root first:

```bash
uv run --directory /path/to/AutoArchon autoarchon-materialize-markdown-problem-pack \
  --questions-markdown /path/to/benchmarks/Open-problem/motivic-flag-maps/Questions.md \
  --output-root /path/to/benchmarks/Open-problem-generated/motivic-flag-maps-q1 \
  --problem-id 1 \
  --force
```

The generated `.lean` files contain comments only and no declarations, so `detect_stage(...)` resolves them to `autoformalize`. This is the honest bridge for note packs that do not yet have formal theorem statements. After materialization, you can use the normal `open-problem-default.json` template or direct operator intake against that generated source root.

For experience-reuse campaigns, the resolved spec can also carry:

```json
{
  "preloadHistoricalRoutes": true
}
```

That makes generated teacher prompts and launch assets enable historical accepted route preloading automatically. Keep this field absent or `false` for benchmark-faithful campaigns. The bundled `formalization-default.json` template turns it on by default because long-horizon non benchmark work benefits from reusing the system's own accepted blocker and proof routes.

Bundled nightly shortcut:

```bash
bash scripts/start_fate_overnight_watchdogs.sh
```

The scripted path still scaffolds `mission-brief.md` and `operator-journal.md`, but a real interactive operator should review them before long unattended campaigns.

## Recovery And Closeout

Useful commands:

```bash
uv run --directory /path/to/AutoArchon autoarchon-campaign-recover \
  --campaign-root /path/to/campaign-root \
  --run-id teacher-42 \
  --execute

uv run --directory /path/to/AutoArchon autoarchon-finalize-campaign \
  --campaign-root /path/to/campaign-root

uv run --directory /path/to/AutoArchon autoarchon-campaign-archive \
  --campaign-root /path/to/campaign-root
```

The most important output files are:

- `reports/final/final-summary.json`
- `reports/final/compare-report.json`
- `reports/final/lessons/lesson-records.jsonl`
- `reports/postmortem/postmortem-summary.json`
- `reports/postmortem/lessons/lesson-records.jsonl`
