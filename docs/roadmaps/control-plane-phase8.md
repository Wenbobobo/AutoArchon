# Phase 8 Roadmap: Helper Policy Tuning And Operator Observability Consolidation

This phase assumes the phase-7 control plane is already in place:

`interactive campaign-operator -> mission brief + resolved spec + operator journal -> watchdog -> orchestrator-agent -> supervisor-agent`

The next two high-ROI cuts are:

1. tune helper policy from real overnight evidence so helper calls become cheaper, less repetitive, and more reliable
2. consolidate the operator-facing browser view so remote progress reading becomes faster without introducing a second state store

## Scope Boundaries

Keep these constraints explicit:

- `campaign-operator` remains the only user-facing outer owner
- `watchdog` remains the reliability wrapper, not a UI layer
- `control/progress-summary.json` stays canonical for campaign state
- `workspace/.archon/supervisor/progress-summary.json` stays canonical for run state
- the browser view remains a read-only mirror over those files
- helper remains advisory only and must never become an acceptance authority

## Desired Outcomes

By the end of this phase:

- repeated `lsp_timeout`, `missing_infrastructure`, `repeated_failure`, and provider-transport failures should no longer trigger noisy helper storms
- operators should understand campaign state, slowdown causes, and likely next action within about ten seconds from one browser page
- overnight reruns should produce cleaner evidence for later lesson clustering and mathlib-agent research

## Workstream A: Helper Policy Tuning From Nightly Evidence

### Problem Statement

The helper runtime now has budgets, cooldowns, note reuse, fallback providers, and failure indexing, but the default policy is still mostly static. We need one more loop:

`nightly evidence -> failure family analysis -> policy adjustment -> rerun -> compare`

### A1. Freeze And Audit Current Evidence Surfaces

Use the current file-backed data only:

- `workspace/.archon/informal/helper/helper-index.json`
- `workspace/.archon/supervisor/progress-summary.json`
- `control/progress-summary.json`
- `reports/postmortem/`
- `reports/final/lessons/`

Questions to answer for each campaign:

- which helper reasons caused the most fresh calls?
- which reasons caused the most failed calls?
- which reasons usually led to accepted proof, accepted formalization, blocker, or nothing?
- which failures were provider-transport noise versus real mathematical dead ends?
- where did helper retry even though a fresh call had already just failed for the same reason and file?

Deliverable:

- one short evidence summary document per analyzed rerun
- one aggregated table of reason -> count -> terminal value

Acceptance:

- we can quantify helper value and helper waste by reason family instead of tuning blindly

### A2. Add Lightweight Analysis Scripts Over Existing Artifacts

Add cheap offline analysis tools instead of a new daemon.

Planned outputs:

- per-campaign helper reason histogram
- per-reason failure-to-success conversion summary
- transport-failure heatmap by provider/model/phase
- repeated identical-call clusters for `phase + file + reason + config`

Prefer:

- `uv run` entrypoints
- pure file reads over live process coupling
- export to Markdown and JSON under `reports/postmortem/helper-analysis/`

Acceptance:

- one command can summarize whether helper noise came from policy, provider instability, or genuinely hard theorems

### A3. Tune Policy By Failure Family, Not One Global Knob

Adjust policy separately for distinct failure families.

Priority families:

- `provider_transport`
- `lsp_timeout`
- `missing_infrastructure`
- `repeated_failure`
- `external_reference`

Planned tuning moves:

- stricter cooldown after transport failure
- lower fresh-call budget for low-yield repeated-failure cases
- stronger note reuse before any new provider call
- optional per-family backoff multiplier
- optional per-phase policy differences between `plan` and `prover`

Non-goals:

- do not let the helper suppress prover work too early
- do not let helper failure mark a theorem as impossible

Acceptance:

- helper call volume decreases on noisy families without reducing accepted proofs/formalizations on comparable reruns

### A4. Encode Policy In Stable Runtime Config Surfaces

Keep configuration simple and durable:

- `.archon/runtime-config.toml`
- `examples/helper.env`

If new knobs are added, they must be:

- grouped by reason family or phase
- documented once in README or operations docs
- printable through an existing `--print-effective-config` style surface where possible

Acceptance:

- operators can inspect the resolved helper policy before launch without reading code

### A5. Validate With Controlled Reruns

Validation order:

1. focused regression tests on helper runtime and helper index summarization
2. one short rerun on a known noisy benchmark slice
3. one unattended overnight rerun

Compare at least:

- fresh helper calls per finalized target
- failed helper calls per finalized target
- accepted proofs
- accepted formalizations
- accepted blockers
- median recovery count per campaign

Acceptance:

- the tuned policy reduces wasted helper traffic and does not materially degrade throughput

## Workstream B: Operator Observability Consolidation

### Problem Statement

The current browser view is useful but still reads like a raw mirror. We need a faster operator surface for:

- nightly remote checking
- quick progress triage
- spotting cooldown or helper failure storms
- deciding whether to ask the interactive `campaign-operator` to intervene

This must still render from the existing file-backed summaries.

### B1. Define The Canonical User Story

The intended path should be:

1. launch or resume a campaign through the interactive `campaign-operator`
2. when remote viewing is needed, run `autoarchon-campaign-observe`
3. inspect one browser page backed by `control/progress-summary.{json,md,html}`
4. if deeper detail is needed, open run-level `workspace/.archon/supervisor/progress-summary.{json,md}`
5. if intervention is needed, return to the interactive operator session

Acceptance:

- the browser page is clearly a viewing surface, not a second owner

### B2. Redesign The Browser Page Around Decisions, Not Raw Fields

The page should answer these questions immediately:

- Is the campaign making progress?
- How many runs are active, stalled, or attention-worthy?
- Is slowdown caused by provider cooldown, helper failure storms, or theorem difficulty?
- Which run should the operator inspect first?
- What command should the operator run next?

Priority UI blocks:

- top summary strip: progress, ETA, restart count, active runs, remaining targets
- risk strip: cooldown active, provider cooldown until, helper failed calls, recoverable runs
- active runs table with scope, phase, iteration, remaining targets, helper failures, blocker notes
- recent finalized targets
- recommended commands
- direct paths to final reports and run summaries

Acceptance:

- one screen answers the main triage questions without opening raw JSON

### B3. Add Compact Run Ranking Heuristics

Sort active or attention runs by operator urgency.

Candidate urgency signals:

- recoverable action exists
- helper failed-call count is rising
- cooldown is active
- activity age is high
- remaining-target count is low but unchanged across refreshes

Keep ranking simple and explainable.

Acceptance:

- the first row in the table is usually the one an operator would check manually

### B4. Keep The HTML Layer Thin

Do not add:

- a websocket layer
- a database
- a second cache
- a separate API server with its own state model

Do add:

- better grouping and labels
- compact badges for helper failures and cooldown state
- clearer path links and copy-ready commands
- lightweight auto-refresh

Acceptance:

- `autoarchon-campaign-observe` remains a thin file-backed viewer

### B5. Expose Only High-Signal Helper Fields

Promote these helper signals into the main operator view:

- helper fresh calls
- helper failed calls
- top helper failed reasons
- helper cooldown active reasons

Do not flood the main page with full note metadata. Keep the deep detail in:

- `control/progress-summary.json`
- `workspace/.archon/supervisor/progress-summary.json`
- helper note files

Acceptance:

- operators can spot helper instability quickly without reading note bodies

### B6. Validate With Real Remote Use

Validation order:

1. snapshot tests or string-contract tests for HTML and Markdown
2. local manual run over one active campaign
3. remote/nightly use on one unattended campaign

Acceptance:

- the page remains readable on desktop and mobile
- the operator can identify the next action from the browser page alone

## Recommended Execution Order

1. analyze the latest completed reruns and write helper evidence summaries
2. add offline helper-analysis tooling over existing artifacts
3. tune helper policy defaults and add focused regression tests
4. redesign campaign HTML around operator decisions using the existing overview payload
5. run one short rerun to validate helper and UI changes together
6. run one unattended overnight rerun and archive the postmortem

## Concrete TODOs

### TODO 1: Helper Evidence Pass

- inspect the latest rerun campaigns
- export helper reason/failure summaries
- classify each noisy helper family as transport noise, policy waste, or theorem hardness

### TODO 2: Helper Analysis Tooling

- add a `uv run` analysis entrypoint
- write JSON plus Markdown outputs under postmortem reports
- cover repeated identical-call clusters and conversion summaries

### TODO 3: Policy Adjustment

- update runtime config defaults or resolution logic
- add focused tests for cooldown, budget, reuse, and transport-failure backoff
- document any new knobs

### TODO 4: Browser View Consolidation

- redesign the HTML layout around operator triage
- show helper failure and cooldown summary prominently
- rank active runs by urgency

### TODO 5: Rerun Validation

- run a short benchmark slice
- run one unattended overnight campaign
- compare helper waste and finalized-target throughput before and after

## Definition Of Done

This phase is done only if all of the following are true:

- helper failure analysis can be produced from existing artifacts with one command
- helper policy tuning is backed by real rerun evidence, not guesses
- the operator browser view remains file-backed and clearly canonical-source-compatible
- the browser page surfaces helper instability and cooldowns prominently
- at least one rerun shows lower helper waste without harming accepted-target output

## Explicitly Deferred

Not part of this phase:

- runtime integration of `mathlib-agent`
- adding a new always-on manager above `campaign-operator`
- replacing the interactive operator with a static config-only flow
- introducing a database-backed or websocket-heavy dashboard
