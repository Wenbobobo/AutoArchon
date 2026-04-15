# Postmortem: 2026-04-15 Fresh FATE-M Rerun Slice

This document records the fresh benchmark-faithful rerun:

- `20260415-rerun12-fatem-42-45-94`

Unlike the archived `20260413` nightlies, this root reached a real terminal closeout and was finalized under `reports/final/`.

## Final State

- `accepted = 2`
- `blocked = 1`
- accepted proofs:
  - `teacher-45:FATEM/45.lean`
  - `teacher-94:FATEM/94.lean`
- accepted blocker:
  - `teacher-42:FATEM_42.lean.md`

Canonical artifacts:

- `reports/final/final-summary.json`
- `reports/final/compare-report.json`
- `reports/final/proofs/`
- `reports/final/blockers/`
- `reports/final/validation/`
- `reports/final/lessons/lesson-records.jsonl`

## Run-By-Run Outcome

### `teacher-45`

- terminal status: `accepted`
- theorem: `FATEM/45.lean`
- main lesson: the proof itself was found, but the run nearly lost its durable note during late cleanup pressure; final acceptance depended on validation-backed recovery instead of trusting the transient session state

### `teacher-94`

- terminal status: `accepted`
- theorem: `FATEM/94.lean`
- exported proof route:

```lean
simp [Subgroup.centralizer_eq_top_iff_subset, Set.singleton_subset_iff]
```

- main lesson: this shard showed the same pattern as `45`; the theorem-search quality was already enough, but durable export discipline mattered more than another search cycle

### `teacher-42`

- terminal status: `blocked`
- theorem: `FATEM/42.lean`
- accepted blocker note: `reports/final/blockers/teacher-42/FATEM_42.lean.md`
- validated conclusion: the theorem is false as written because `orderOf x = 0` for infinite-order elements, and the counterexample using `Multiplicative.ofAdd (1 : Int)` and `Multiplicative.ofAdd (1 : ZMod 2)` was Lean-checked before acceptance

## What This Rerun Proved

- the hardened `campaign-operator -> watchdog -> orchestrator-agent -> supervisor-agent` path can now reach a clean terminal closeout on a fresh campaign root
- owner lease, deterministic recovery, artifact export, lesson clustering, and finalization all completed without stale-owner ambiguity
- `reuse_build_outputs` worked cleanly across all three teachers
- the main remaining bottleneck is not theorem-search quality; it is durable artifact discipline plus provider transport stability

## Main Failure And Recovery Pattern

The two accepted proofs and the accepted blocker all converged on the same operational lesson:

1. produce or preserve a durable artifact first
2. validate it independently
3. only then trust terminal acceptance

That means:

- prover errors are not trustworthy on their own
- late cleanup or linter work must not outrank `task_results`
- when a theorem is false, the correct output is a validation-backed blocker note, not a mutated statement

## Remaining High-ROI Follow-Ups

- seed historical accepted blocker/proof routes into fresh relaunches before the next planner pass
- keep strengthening bounded retry and fallback behavior for provider transport issues
- add a richer operator-facing dashboard only after the file-backed surfaces remain the canonical source of truth
