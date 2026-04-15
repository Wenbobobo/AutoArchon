# Benchmarking

This document records how to run benchmark slices cleanly and how to interpret the existing FATE-M comparison artifacts.

## Fresh-Run Hygiene

Use this workflow for any benchmark result you plan to cite:

1. Start from a pristine benchmark source tree such as `benchmarks/FATE-M-upstream`.
2. Create a new isolated run root with `uv run --directory /path/to/AutoArchon autoarchon-create-run-workspace`.
3. Use `run-root/source/` as the immutable baseline and `run-root/workspace/` as the mutable project.
4. You may reuse a warmed `.lake/` cache or copied package directory.
5. do not reuse another run's `.archon/` state.
6. do not enable `autoarchon-supervised-cycle --preload-historical-routes` for benchmark-faithful runs.
7. Re-run `./init.sh` on `run-root/workspace/` with the intended objective regex and limit.
8. Count a file as solved only after direct Lean verification against the actual run workspace.

The key rule is simple: cache reuse is fine, state reuse is not.

## Benchmark Result Classes

- `benchmark-faithful`: theorem headers are unchanged, solved files compile, and blockers remain documented as blockers.
- `contaminated`: the run history is still useful for debugging, but not for reporting benchmark outcomes.
- `non-runnable baseline`: useful for dependency comparison, but not for solved-count comparison.

## Current FATE-M Compare Slice

The current 5-file slice is `FATEM/39.lean` through `FATEM/43.lean`.

### Benchmark-faithful result

The current benchmark-faithful comparison artifact is the original compare run:

- worktree: `runs/fate-m-compare-codex`
- accepted iteration: `iter-002`
- solved files: `39`, `40`, `41`, `43`
- validated blocker: `FATEM/42.lean`

`FATEM/42.lean` is treated as a blocker because the unrestricted statement is false in Mathlib's `orderOf` convention for infinite-order elements. The blocker explanation lives in `.archon/task_results/FATEM_42.lean.md`.

### Latest fresh rerun after the no-mutation fix

A fresh rerun was completed in a new worktree:

- worktree: `runs/fate-m-compare-codex-rerun-20260411`
- slice rerun: `iter-001`
- focused blocker closure: `iter-002`
- final interpretation: still `4 solved + 1 validated blocker`

What changed in the rerun:

- `39`, `40`, `41`, and `43` were solved again and verified with direct `lake env lean`.
- `42` remained frozen on the original benchmark theorem.
- instead of mutating the theorem header, the rerun added helper theorems that formalize the obstruction and wrote `.archon/task_results/FATEM_42.lean.md`.
- the focused second pass proved a named counterexample theorem showing `gcd > 1` while the target strict inequality fails.

This rerun is stronger evidence than the earlier contaminated history because it confirms the same benchmark conclusion under the tightened no-statement-mutation contract.

### Contaminated follow-up history

The same worktree also contains a later follow-up:

- iteration: `iter-003`
- status: contaminated

That iteration exposed a real workflow bug: the prover made `FATEM/42.lean` compile by adding extra hypotheses to the original theorem. This is useful regression evidence, but it is not benchmark-faithful and must not be counted as a solved benchmark theorem.

### Clean-source but contaminated-history copy

`runs/fate-m-compare-codex-clean` should currently be treated as a source baseline only.

- Its `FATEM/39..43` source files are back to the original benchmark statements.
- Its historical `.archon/logs/` contain copied or mixed history from other runs.
- Do not cite those logs as the result of a fresh benchmark rerun.

If you want a new authoritative comparison after the no-theorem-mutation fixes, create a brand-new worktree from the upstream benchmark and rerun there.

## Acceptance Checklist For Future Reruns

Before you publish or compare a rerun:

- theorem headers must match the benchmark source verbatim
- the compared source of truth must be `run-root/source/`, not a memory of the previous run
- solved files must compile in the run worktree
- blocker files must stay on the original statement and emit a blocker report instead of a repaired theorem
- the reported metrics must come from that fresh run's `.archon/logs/`
- copied `.archon/` history from another run invalidates the result
- `--preload-historical-routes` invalidates benchmark-faithful reporting for that run

## Upstream Comparison Boundary

The upstream Archon repository is still a `non-runnable baseline` in this environment because its init and loop scripts hard-depend on `claude` and `CLAUDE.md`. Until that dependency is removed or a real Claude environment is available, the comparison against upstream is:

- meaningful for runtime portability
- not meaningful for wall-time or solved-count A/B measurement
