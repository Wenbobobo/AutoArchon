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
- strengthened the helper-prover transport surface:
  - `.archon/runtime-config.toml` now supports ordered `[[helper.fallbacks]]` entries
  - the helper wrapper can fail over to the next configured provider without changing proof acceptance ownership
- upgraded the campaign summary surface:
  - `control/progress-summary.*` now carries restart count, ETA, recent finalized targets, and direct final-report/export paths instead of only the coarse progress bar
- collected fresh multi-run rerun evidence on the hardened path:
  - `20260415-rerun12-fatem-42-45-94` reached a real terminal closeout with `accepted = 2`, `blocked = 1`
  - `teacher-45` and `teacher-94` exported accepted proofs, `teacher-42` exported an accepted blocker note, and `reports/final/final-summary.json` was written cleanly
  - this rerun confirmed the main remaining bottleneck is durable artifact discipline plus provider transport stability, not theorem-search quality
- validated sampled prewarm on a fresh wide shard without warmed-build reuse:
  - the dedicated root `20260415-prewarm-validate-fatem5` reported `prewarmPlan = scoped_verify_sample` with `sample 4/5 files`
  - the generated prewarm command completed successfully in `1:43.23` using scoped verification on `FATEM/3.lean`, `FATEM/11.lean`, `FATEM/42.lean`, and `FATEM/94.lean`
  - `lake exe cache get --repo` recovered cleanly by retrying without `--repo`, so the fallback path is now validated on a real root too

## Current Remaining Gaps

- run-level observability still lags during long inner loops
  - the new live surface is enough for phase/prover visibility, but a richer operator-facing dashboard can still layer on top later
- helper-prover policy is still intentionally minimal above the transport layer
  - prompt quality, trigger heuristics, and note-routing policy can go further even though provider fallback is now present
- historical blocker/proof route reuse is still reactive rather than proactive
  - `autoarchon-supervised-cycle --preload-historical-routes` now gives an opt-in proactive preload path for experience-reuse runs
  - the remaining gap is policy integration: benchmark-faithful templates should stay off, while non-benchmark campaigns still need a cleaner operator-level way to opt in
- benchmark clone retention is now observable, but not deduplicated
  - we still do not have a canonical shared-build strategy across multiple benchmark clones that use the same toolchain/mathlib graph

## Original Four TODO Status

1. Fresh unattended multi-run rerun on the hardened path: completed
   - `20260415-rerun12-fatem-42-45-94` exercised owner lease, deterministic recovery, artifact export, blocker acceptance, and finalization across three teachers on a fresh root
2. Sampled-prewarm validation on a wider shard: completed
   - `20260415-prewarm-validate-fatem5` proved the wide-run planning surface selects `scoped_verify_sample`, and the sampled prewarm finished successfully on a real workspace without falling back to full-project `lake build`
3. Helper-prover deepening without changing acceptance ownership: completed for the current phase
   - fallback transports now live in `.archon/runtime-config.toml`, helper failures can roll to the next provider, and the prover prompt now prioritizes durable `task_results` over optional cleanup
   - richer helper prompting and trigger heuristics remain a next-phase quality task, not a phase-5 blocker
4. Shared-build substrate decision: completed for the current phase
   - keep one warmed benchmark clone under `benchmarks/` and reuse it via `--reuse-lake-from`
   - do not deduplicate clone `.lake` directories until a documented rehydrate workflow exists

## Next High-ROI Follow-Ups

1. preload historical accepted blocker/proof routes into fresh relaunches before the next planner pass
   - the opt-in supervisor path now exists; the next step is deciding where the campaign operator or templates should surface it without contaminating benchmark-faithful runs
2. deepen helper-prover prompt policy above the now-stable transport layer
   - focus on bounded invocation heuristics, note routing, and optional external provider mixes such as Gemini or DeepSeek through the existing OpenAI-compatible surface
3. add a richer operator-facing dashboard without weakening the file-backed source of truth
   - campaign and supervisor `progress-summary.*` should remain canonical even if a later web surface is added

## Non-Goals For This Phase

- no rewrite of the core proof loop
- no new top-level manager role in the default path
- no automatic deletion of benchmark clones without a documented rehydrate path
