# Phase 5 Roadmap: Unattended Reliability, Retention, And Fresh-Rerun Validation

This roadmap records the remaining high-ROI work after the control-plane hardening pass and the storage-hygiene cleanup.

## Completed So Far In This Phase

- stabilized the helper/runtime-config path:
  - `.archon/runtime-config.toml` is now the canonical runtime policy file
  - helper-prover tooling reads the TOML path first, with legacy JSON only as fallback
- tightened tail-scope defaults for endgame runs:
  - teacher prompts now pass tail-scope overrides for the last `1-4` objectives instead of only `1-2`
  - tail-scope can now raise both plan timeout and prover timeout, not just prover timeout
- reduced stale-state drag inside repeated tail relaunches:
  - `autoarchon-supervised-cycle` now archives accepted `task_results` that are already outside the current objective list before launching the next loop
  - this keeps old resolved notes preserved under `.archon/task_results_archived/accepted_stale/` without making the planner re-read them as live work
- reduced wide-shard cold-start cost:
  - launch-time prewarm now uses sampled scoped verification for wider shards instead of falling back straight to a full project build
  - campaign status exposes this as `scoped_verify_sample` so operators can distinguish it from narrow full-scope verify
- added lightweight observability surfaces:
  - campaign-level `control/progress-summary.md` / `progress-summary.json`
  - run-level `workspace/.archon/supervisor/progress-summary.md` / `progress-summary.json`
- added storage-hygiene tooling:
  - `autoarchon-storage-report` can audit and prune inactive run `workspace/.lake`
  - finalize/archive/watchdog/spec flows now expose explicit prune flags
  - tracked FATE full-campaign templates opt into post-terminal cache pruning
- fixed stale-lease storage accounting:
  - stale `active: true` leases without live pid no longer block cache cleanup
  - the storage report now distinguishes stale active leases from genuinely protected live runs
- extended storage auditing to legacy single-workspace roots:
  - old standalone run roots with top-level `.lake` are now surfaced as rebuildable cache candidates
- executed a real cleanup on the current machine:
  - historical run cache usage dropped from roughly `68G` under `runs/` to roughly `3G`
- closed the terminal stale-launch tail on the cleanup path:
  - cache pruning now best-effort kills stale `launch-teacher.sh` process groups before judging a campaign cache blocked
  - terminal orphan cleanup now rewrites `teacher-launch-state.json` to `cleanup_terminated` instead of leaving a misleading active marker behind
  - the watchdog also runs the same stale-launch cleanup when the campaign first reaches a terminal state
- upgraded run-level observability during long inner loops:
  - `workspace/.archon/supervisor/progress-summary.*` now refreshes while the loop is still live
  - the payload exposes `liveRuntime.phase`, current plan/prover/review status, and active prover file rows
- added a tail-scope known-route fast-path:
  - when every remaining objective already has an exact compile-checked route or a prevalidated blocker route, the supervisor exports `ARCHON_SKIP_INITIAL_PLAN=1`
  - `archon-loop.sh` then skips only the initial plan phase for that cycle instead of burning the full planner timeout before prover work starts
- promoted benchmark retention from a coarse top-level audit to a clone-aware policy surface:
  - the retention report now lists benchmark clone rows, their `.lake` bytes, and the recommended emergency action (`prune_clone_lake_only`) when disk pressure matters
- collected real rerun evidence on the hardened path:
  - a benchmark-faithful smoke campaign on `FATEM/94.lean` reached `accepted` under the watchdog path after one bounded relaunch
  - proof, task report, validation, stale-launch cleanup, artifact export, and finalize all completed on the same campaign root
- tightened the known-route fast-path to match realistic planner notes:
  - exact-route detection now recognizes practical note styles such as `Shortest route`, `Expected proof shape`, and small Lean proof blocks, not only the earlier narrow marker wording
- reduced prover cold-start stalls on exact-route tail shards:
  - the prover prompt now explicitly allows skipping the first Lean MCP diagnostics call when the file is still a bare one-sorry theorem and an exact route is already recorded
  - a fresh manual rerun then validated the full path: `plan.status = skipped_fast_path` and the same run finished `clean` in one iteration

## Current Remaining Gaps

- multi-run unattended rerun evidence is still needed
  - deterministic pytest coverage is strong now, and single-run real reruns are now validated, but a wider unattended benchmark slice still matters for transport flake, owner-restart behavior, and final acceptance quality
- run-level observability still lags during long inner loops
  - the new live surface is enough for phase/prover visibility, but a richer operator-facing dashboard can still layer on top later
- helper-prover policy is still intentionally minimal
  - provider fallback, prompt quality, and bounded-use heuristics can go further
- benchmark clone retention is now observable, but not deduplicated
  - we still do not have a canonical shared-build strategy across multiple benchmark clones that use the same toolchain/mathlib graph

## Remaining High-ROI Steps

1. run one fresh unattended multi-run rerun on the hardened path
   - prefer a real FATE slice over historical sample roots
   - validate owner lease, watchdog restarts, terminal finalize, exported proofs, and recovery quality across multiple teachers
2. validate the new sampled-prewarm path on a fresh wider rerun
   - confirm that `scoped_verify_sample` materially lowers cold-start time without increasing broken-run recovery
   - keep warmed-build compatibility checks strict and visible
3. deepen the helper-prover path without changing acceptance ownership
   - improve external-provider fallback and bounded invocation policy
   - keep helper outputs strictly advisory
4. decide whether benchmark clones should share a single warmed build substrate
   - keep the current clone-aware retention policy until there is a safe deduplication or rehydrate workflow

## Non-Goals For This Phase

- no rewrite of the core proof loop
- no new top-level manager role in the default path
- no automatic deletion of benchmark clones without a documented rehydrate path
