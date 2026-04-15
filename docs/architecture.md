# Architecture

This is the system-level map for AutoArchon after the control-plane hardening pass. The recommended outer path is `interactive campaign-operator -> mission brief + resolved spec + operator journal -> watchdog -> orchestrator-agent -> supervisor-agent`.

## Global Workflow

```mermaid
flowchart TD
    U[User] --> CO[campaign-operator]
    CO --> BRIEF[control/mission-brief.md]
    CO --> SPEC[control/launch-spec.resolved.json]
    CO --> JOURNAL[control/operator-journal.md]
    SPEC --> LAUNCH[watchdog launch]
    LAUNCH --> OWNER[control/owner-mode.json]
    LAUNCH --> LEASE[control/owner-lease.json]
    LAUNCH --> WD[watchdog wrapper]
    WD --> ORCH[orchestrator-agent]
    ORCH --> SHARDS[autoarchon-plan-shards]
    ORCH --> CREATE[autoarchon-create-campaign]
    ORCH --> STATUS[autoarchon-campaign-status]
    ORCH --> OVERVIEW[autoarchon-campaign-overview]
    ORCH --> REC[autoarchon-campaign-recover]
    ORCH --> FIN[autoarchon-finalize-campaign]
    ORCH --> ARC[autoarchon-campaign-archive]
    CREATE --> CAMP[campaign root]
    CAMP --> FINAL[reports/final/]
    CAMP --> POST[reports/postmortem/]
    CAMP --> RUNS[runs/<id>/]

    RUNS --> SRC[source/]
    RUNS --> WS[workspace/]
    RUNS --> ART[artifacts/]
    RUNS --> CTRL[control/launch-teacher.sh]
    CTRL --> TEACHER[teacher Codex session]
    TEACHER --> SUP[supervisor-agent]
    SUP --> CYCLE[autoarchon-supervised-cycle]
    CYCLE --> PLAN[plan-agent]
    PLAN --> PROVER[prover-agent]
    PROVER --> REVIEW[review-agent]
    PROVER --> STMT[statement-validator]
    PROVER --> LSP[archon-lean-lsp]
    PROVER -. optional bounded hints via runtime-config.toml .-> HELPER[helper-prover-agent]
    CYCLE --> LESSONFILES[workspace/.archon/lessons/]
    FINAL --> FINALLESSONS[reports/final/lessons/lesson-records.jsonl]
    POST --> POSTLESSONS[reports/postmortem/lessons/lesson-records.jsonl]
    FINALLESSONS -. clustered retrieval input .-> MATHLIB[mathlib-agent]
```

## Role Split

- `campaign-operator` is the default outer owner. It interprets user intent, writes or reviews `mission-brief.md`, `launch-spec.resolved.json`, and `operator-journal.md`, then launches or resumes a campaign and decides whether to recover, finalize, or archive.
- `watchdog` is the concrete reliability wrapper. It owns restart budget, owner lease refresh, cooldown handling, stale-launch cleanup, and `reportFreshness`.
- `orchestrator-agent` owns one campaign root at a time. It plans shards, creates runs, launches teachers, applies bounded deterministic recovery, and finalizes accepted results. It does not directly edit benchmark `.lean` files.
- `supervisor-agent` owns one run root at a time, guards theorem fidelity, leaves restart-safe notes, and drives repeated monitored cycles.
- `plan-agent`, `prover-agent`, `review-agent`, and `statement-validator` remain the inner proof loop.
- `helper-prover-agent` is now a minimal runtime surface: `.archon/runtime-config.toml` plus `.archon/tools/archon-helper-prover-agent.py` give runs one bounded side-model wrapper without changing acceptance ownership. The legacy `.archon/helper-provider.json` path remains a compatibility fallback only, the TOML path supports ordered `[[helper.fallbacks]]` providers for bounded transport failover, and phase-aware `--write-note auto` routing keeps helper notes in the configured planner/prover note lanes.
- `mathlib-agent` remains a future retrieval role, now fed by `lesson-records.jsonl` plus derived `lesson-clusters.json` / `lesson-clusters.md`.

## Artifact Boundaries

- `source/` is the immutable baseline.
- `workspace/` is the only mutable proof-search area.
- `artifacts/` is the per-run export bundle for mathematician review.
- `reports/final/` is the campaign-level accepted surface.
- `reports/postmortem/` is the archived diagnostic surface for stopped, degraded, or intentionally non-final samples.

New machine-readable lesson surfaces:

- `reports/final/lessons/lesson-records.jsonl`
- `reports/postmortem/lessons/lesson-records.jsonl`

These JSONL files are the stable upstream for future error clustering, retrieval, and skill extraction.

## State Contract

The most important operator-facing files are:

- `control/mission-brief.md`
- `campaign-status.json`
- `control/owner-mode.json`
- `control/owner-lease.json`
- `control/launch-spec.resolved.json`
- `control/operator-journal.md`
- `control/orchestrator-watchdog.json`
- `runs/<id>/control/bootstrap-state.json`
- `runs/<id>/control/teacher-launch-state.json`
- `workspace/.archon/supervisor/run-lease.json`
- `workspace/.archon/logs/iter-*/meta.json`
- prover logs with `input_tokens` and `output_tokens` when available

These are the files the outer owner should trust before relaunching, archiving, or finalizing.

## Observability

Campaign-level observability should answer four questions quickly:

1. Is the campaign making progress?
2. Is the owner healthy?
3. Are duplicate or stale launches being contained?
4. Is the sample final, or should it be archived as postmortem only?

The current answers live in:

- `autoarchon-campaign-overview`
- `control/progress-summary.md`
- `control/progress-summary.json`
- `workspace/.archon/supervisor/progress-summary.md`
- `workspace/.archon/supervisor/progress-summary.json`
- `campaign-status.json`
- `reports/final/compare-report.json`
- `reports/postmortem/postmortem-summary.json`
- `control/orchestrator-watchdog.log`

`control/progress-summary.md` is the lightweight campaign surface: a one-screen progress bar, active runs, restart count, ETA, recent finalized targets, and direct paths to final reports and exports. `workspace/.archon/supervisor/progress-summary.md` is the matching single-run surface with scope completion, new task results, and helper-note visibility. The dashboard is still useful for one run, but the summary surfaces are intentionally file-backed and cheap to refresh.

## Extension Points

The next profitable extensions are around verification and knowledge accumulation rather than many loosely coupled proof agents at once.

- add task-class-specific helper prompt packs and reuse policy on top of the now phase-aware note-routing surface
- cluster repeated failures from `lesson-records.jsonl`
- build retrieval packs for a future `mathlib-agent` on top of the clustered lesson artifacts
- improve acceptance and audit downstream of `statement-validator`

The rule stays the same: keep the control plane boring first, then add more intelligence against explicit file contracts.
