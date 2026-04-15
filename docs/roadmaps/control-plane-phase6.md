# Phase 6 Roadmap: Operator Productization, Helper Policy, And Knowledge Capture

This roadmap starts after the phase-5 hardening pass. The default unattended path is now usable:

`interactive campaign-operator -> mission brief + resolved spec + operator journal -> watchdog -> orchestrator-agent -> supervisor-agent`

The next phase is not about adding many new runtime roles. It is about making the existing path easier to launch, easier to trust, and easier to learn from.

## Entry State

- the control plane is stable enough for fresh benchmark-faithful reruns and formalization-style campaigns
- file-backed summaries are now the canonical observability surface
- helper transport, fallback, note routing, prompt packs, and note reuse are in place
- the remaining gaps are mostly policy, productization, and knowledge accumulation gaps rather than core runtime breakage

## Phase Goals

1. make `campaign-operator` the clear user-facing product entrypoint
2. deepen helper behavior without changing proof acceptance ownership
3. improve observability without replacing the file-backed source of truth
4. turn archived lessons into reusable retrieval and planning inputs
5. prepare the system for open-problem and formalization workflows, not only benchmark reruns

## Workstream 1: Operator Productization

The outer owner should stay interactive and context-aware. The main need is less manual setup, not a new permanent runtime role.

Planned work:

- add a lightweight operator bootstrap path from a short natural-language mission to:
  - `control/mission-brief.md`
  - `control/launch-spec.resolved.json`
  - `control/operator-journal.md`
- keep `campaign-operator` as the default name instead of reviving `manager-agent`
- prefer config-backed defaults and template selection over bespoke shell editing
- keep helper enabled by default through local `examples/helper.env`
- make launch-time validation fail early when helper env, benchmark root, or template scope is inconsistent

Acceptance signal:

- a new user can start one campaign from a short mission with one documented primary path
- generated launch assets stay benchmark-faithful when required and non-benchmark-friendly when intended

## Workstream 2: Helper Policy Above The Transport Layer

The helper transport is good enough for the current phase. The next gains are in when to call it, when not to call it, and how to reuse what it already produced.

Planned work:

- add stronger cross-iteration helper trigger heuristics in `.archon/runtime-config.toml`
- add per-reason cooldown and retry budgeting so repeated `lsp_timeout` or `missing_infrastructure` failures do not spam side-model calls
- bias helper usage toward:
  - non-formal sketch generation
  - tactic or lemma suggestions
  - external-reference condensation
- keep helper output advisory only
- document simple provider mixes:
  - OpenAI-compatible primary
  - optional Gemini fallback
  - optional DeepSeek via OpenAI-compatible base URL

Acceptance signal:

- helper call volume drops on repeated identical failure states
- useful helper notes become easier to reuse across iterations and reruns
- acceptance still depends only on the normal validation-backed path

## Workstream 3: Low-Cost Observability

The browser dashboard should not become the source of truth. The goal is a better read-only surface over the existing files.

Planned work:

- keep these canonical:
  - `control/progress-summary.md`
  - `control/progress-summary.json`
  - `workspace/.archon/supervisor/progress-summary.md`
  - `workspace/.archon/supervisor/progress-summary.json`
- add optional thin views on top of those JSON files rather than adding a second state store
- prioritize:
  - campaign ETA and active-run health
  - restart and cooldown visibility
  - accepted/blocker/export paths
  - helper-note and task-result counts
- keep `watch_campaign.sh` and `watch_run.sh` as the cheap default surfaces

Acceptance signal:

- operators can understand campaign state in seconds without opening raw logs
- any richer UI remains optional and read-only relative to the file-backed summaries

## Workstream 4: Knowledge Capture And Retrieval Prep

The system now writes lessons, but it does not yet exploit them deeply.

Planned work:

- stabilize lesson clustering around:
  - repeated proof failure modes
  - repeated benchmark blockers
  - repeated missing-mathlib or missing-lemma patterns
- derive small retrieval packs from:
  - `reports/final/lessons/lesson-records.jsonl`
  - `reports/postmortem/lessons/lesson-records.jsonl`
  - `lesson-clusters.json`
- prepare the input contract for a future `mathlib-agent`
- keep the first version offline and advisory rather than inserting a new default runtime dependency

Acceptance signal:

- operators and supervisors can inspect clustered failure families
- future retrieval roles can be added against explicit file contracts instead of ad hoc prompt lore

## Workstream 5: Open-Problem Readiness

The system should keep benchmark-faithful behavior when requested, but the next product target is broader formalization work.

Planned work:

- keep `formalization-default.json` as the non-benchmark starting point
- deepen `preloadHistoricalRoutes` policy for non benchmark-faithful campaigns
- make artifact boundaries even clearer for mathematician review:
  - immutable `source/`
  - mutable `workspace/`
  - exported `artifacts/`
  - campaign-level `reports/final/`
- prepare room for future statement-translation and statement-check helpers without putting them in the default runtime path yet

Acceptance signal:

- the system can launch formalization-style campaigns with minimal manual configuration
- review artifacts stay cleanly separated from mutable working state

## Deferred Items

These remain explicitly out of scope for the first phase-6 slice:

- no rewrite of the core proof loop
- no new always-on manager layer above `campaign-operator`
- no mandatory browser UI
- no mandatory `mathlib-agent` in the default path
- no automatic benchmark-clone dedup strategy until rehydrate policy is explicit

## Recommended Implementation Order

1. operator bootstrap and launch-contract validation
2. helper trigger and cooldown policy
3. read-only dashboard polish over progress-summary JSON
4. lesson clustering and retrieval-pack generation
5. formalization/open-problem template refinement
