# Phase 7 Roadmap: Operator Intake, Launch Contract Validation, Reminder Layers, And Mathlib Research

This phase starts from the phase-6 control plane after `campaign-operator`, watchdog, orchestrator, and supervisor are already usable on real campaigns.

Default outer path:

`interactive campaign-operator -> mission brief + resolved spec + operator journal -> watchdog -> orchestrator-agent -> supervisor-agent`

## Status Snapshot

Implemented in the current branch:

- direct interactive `campaign-operator` is now the default user path, with `scripts/start_campaign_operator.sh` kept as an optional wrapper only
- launch-contract validation exists through `autoarchon-validate-launch-contract`
- campaign observability stays file-backed, and now exposes `control/progress-summary.json` plus Markdown and static HTML mirrors generated from the same overview payload
- reminder artifacts already export as `lesson-clusters.*` and `lesson-reminders.*`
- helper transport moved to `.archon/runtime-config.toml` with bounded fallback providers, per-reason budgets, cooldowns, and note reuse
- non-benchmark campaigns can bootstrap from formalization/open-problem defaults instead of only FATE-shaped inputs
- `analysis/mathlib-agent/` exists as an explicit research track outside the default runtime path

Still open or only partially complete:

- operator-facing remote viewing is still intentionally lightweight; `progress-summary.html` is a local/static mirror, not yet a richer served kanban
- helper policy is implemented, but large-sample tuning from repeated overnight campaigns is still pending
- open-problem readiness still needs more end-to-end natural-language intake samples and theorem-review ergonomics beyond benchmark clones
- `mathlib-agent` remains research only, with no runtime integration

## Objectives

1. make interactive operator intake the primary user path
2. add explicit preflight validation through `autoarchon-validate-launch-contract`
3. expose richer machine state through the existing `progress-summary.json` surfaces instead of adding a second control-plane state store
4. layer lesson aggregation into both archival clusters and launch-time reminders such as `lesson-reminders.json`
5. prepare a separate research surface under `analysis/mathlib-agent/` without making `mathlib-agent` part of the default runtime path
6. improve formalization and open-problem readiness without weakening benchmark-faithful boundaries

## Workstream 1: Interactive Operator Intake

The primary path should be an interactive `campaign-operator` session that converts a human goal into:

- `control/mission-brief.md`
- `control/launch-spec.resolved.json`
- `control/operator-journal.md`

Required behavior:

- ask intake questions when the real user objective is underspecified
- make benchmark-faithful vs formalization intent explicit
- keep helper enabled by default unless the mission contract forbids it
- keep rendered prompts as an advanced path, not the main onboarding path

## Workstream 2: Launch Contract Validation

Add an explicit validator that runs before watchdog launch:

```bash
uv run autoarchon-validate-launch-contract --campaign-root <campaign-root>
```

Validation must cover:

- scaffolded or missing operator surfaces
- invalid or stale resolved spec paths
- source-root integrity and regex scope sanity
- benchmark-faithful rejection of `preloadHistoricalRoutes`
- helper env availability when helper is not explicitly disabled

Acceptance signal:

- invalid launch contracts fail before detached long-running work starts

## Workstream 3: File-Backed Kanban And Cooldown State

Keep the file-backed control plane canonical:

- `control/progress-summary.md`
- `control/progress-summary.json`
- `workspace/.archon/supervisor/progress-summary.md`
- `workspace/.archon/supervisor/progress-summary.json`

Extend those payloads with:

- status buckets
- recent transitions
- recommended recovery and watch commands
- helper cooldown state and provider cooldown state

This stays intentionally read-only. Any later browser kanban should render from these files instead of inventing a second state source.

## Workstream 4: Lesson Clusters And Reminder Layers

The lesson system should branch into two layers:

- archival clusters for postmortem and historical analysis
- short reminder surfaces for next-launch guidance

Artifacts:

- `lesson-clusters.json`
- `lesson-clusters.md`
- `lesson-reminders.json`
- `lesson-reminders.md`

Reminder records should preserve:

- `recommended_action`
- `source_status`
- `signal_tags`

## Workstream 5: Helper Policy V2

The helper remains advisory only, but it should become cheaper and less repetitive.

Planned controls:

- per-reason helper budgets
- per-reason cooldown windows
- note reuse before fresh provider calls
- explicit helper event indexing for provider calls, reuse, and blocked calls

Acceptance signal:

- repeated `lsp_timeout`, `missing_infrastructure`, and `repeated_failure` states stop spamming identical side-model calls

## Workstream 6: Formalization And Open-Problem Readiness

Keep benchmark-faithful evaluation clean, but improve the generic path for formalization and open-problem campaigns.

Requirements:

- `formalization-default.json` remains the non-benchmark template
- route reuse is allowed only on non benchmark-faithful paths
- artifact boundaries stay clear for mathematician review
- operator intake must work even when the source root is not a FATE-style benchmark clone

## Workstream 7: Independent Mathlib Research Track

`mathlib-agent` stays separate from the runtime proving loop for now.

Research outputs live under:

- `analysis/mathlib-agent/`

Initial topics:

- repeated missing-lemma and missing-abstraction patterns
- how to mine lesson and validation records into reusable mathlib hint packs
- how to support future formalization and theorem-translation work without coupling this directly to benchmark runs

## Recommended Order

1. interactive operator intake plus launch contract validation
2. richer `progress-summary.json` payloads
3. helper budget and cooldown indexing
4. reminder-layer generation
5. formalization/open-problem template polish
6. `analysis/mathlib-agent/` research skeleton and follow-up experiments

## Next High-ROI Cuts

1. gather evidence from the next unattended rerun and tune helper budgets/cooldowns using real repeated-failure distributions
2. make the operator-facing browser view easier to consume remotely without introducing a second state store
3. add one or two real open-problem or natural-language formalization smoke tests so the non-benchmark path is exercised continuously
