# Phase 4 Roadmap: Control-Plane Hardening And Owner Contract Cleanup

This roadmap saves the control-plane hardening phase after the first real orchestrator campaign postmortem (`lean35/42/45`) and the follow-up nightly FATE samples.

## Completed In This Phase

- corrected the Codex CLI runtime contract:
  - `archon-loop.sh` and `review.sh` now default to `--config model_reasoning_effort=xhigh`
  - removed the stale `--c` default that caused empty prover failures
- made campaign-level control roots explicit and restart-safe:
  - every new campaign now creates `campaign/control/owner-mode.json`
  - watchdog and orchestrator wrappers both update the same owner-mode surface
- added cold-start bootstrap metadata for each run:
  - `runs/<id>/control/bootstrap-state.json`
  - teacher prompts now point to bootstrap metadata first instead of rediscovering missing notes and leases from scratch
- hardened detached teacher launch observability:
  - `runs/<id>/control/teacher-launch-state.json` now records terminal `completed` / `failed`
  - launch now emits `teacher_launch_started` and `teacher_launch_completed` events
  - prewarm/build logs are split into `prewarm.stdout.log` and `prewarm.stderr.log`
- made prewarm compatible with ordinary Lean projects that do not provide `lake exe cache`
  - `autoarchon-prewarm-project` now skips cache download instead of failing hard when the `cache` executable is unavailable
- reduced one repeated cold-start failure mode:
  - `bootstrap-state.json.prewarmRequired` is now a real control bit instead of dead metadata
  - teacher relaunches no longer rerun prewarm after a successful prewarm step already completed once for that run
- safely reused warmed project-local build outputs when compatible:
  - isolated runs now reuse `.lake/build` and `.lake/config` only when the cache source matches the run source on `lean-toolchain` and Lake project metadata
  - bootstrap metadata now marks those runs as `prewarmRequired = false`
- reduced cold-start cost for narrow micro-shards:
  - `autoarchon-prewarm-project` now accepts repeated `--verify-file <relative/path.lean>` flags
  - generated `launch-teacher.sh` now reads `bootstrap-state.json.allowedFiles` and uses scoped `lake env lean <file>` verification for narrow shards instead of unconditional full-project `lake build`
- expanded campaign event coverage:
  - `recovery_planned`
  - `run_status_changed`
  - `campaign_status_refreshed`
  - `validation_accepted`
  - `blocker_accepted`
  - `artifact_exported`
- fixed exporter/report semantics:
  - `artifact-index.json` now separates `resolvedNotes` from `blockerNotes`
  - resolved task-result notes are no longer mislabeled as blockers
- strengthened watchdog progress fingerprints:
  - fingerprints now include launch phase, lease state, latest iteration, accepted result counts, and latest activity timestamps
  - watchdog state now records `stallReason`, `lastStatusRefreshAt`, `lastProgressAt`, and `lastRecoveryAt`
- closed supervisor terminal-state drift for planner-facing files:
  - terminal accepted / blocked scopes now rewrite `workspace/.archon/PROGRESS.md`
  - `workspace/.archon/task_pending.md` is cleared on full terminal closure
  - `workspace/.archon/task_done.md` now records accepted proof vs accepted blocker closure instead of leaving stale placeholders
- added stronger owner/watchdog runtime coverage:
  - generated `launch-teacher.sh` is now smoke-tested for both successful and failing `codex exec` exits
  - campaign event ordering is regression-tested for both accepted proofs and accepted blockers
  - `autoarchon-run-orchestrator` now has wrapper coverage for `owner-mode.json` and attempt indexing
  - `autoarchon-orchestrator-watchdog` now has wrapper + bootstrap-recovery coverage for control-path wiring and persisted watchdog state
- improved benchmark-facing reporting:
  - `compare-report.json` now includes `runTimelines` derived from `events.jsonl`
  - `compare-report.md` now includes a compact per-run transition timeline section
  - each run now also gets `reports/final/runs/<id>/timeline.json` for direct chronology inspection
- improved cold-start observability for future soak analysis:
  - `campaign-status.json` now records `configuredAllowedFiles`, `prewarmPlan`, `prewarmPending`, `prewarmSummary`, and warmed-build reuse fields per run
  - `compare-report.json` now aggregates `prewarmCounts` and carries row-level prewarm summaries for benchmark review
- updated docs and contract tests so the public story matches the runtime:
  - `campaign-operator` is now the default outer owner path
  - `autoarchon-launch-from-spec` is the default launch path
  - watchdog is a reliability wrapper, not a separate math agent
  - manager stays optional and future-facing
- archived the first three nightly FATE samples as postmortem-only runs:
  - `20260413-nightly-fate-m-full`
  - `20260413-nightly-fate-h-full`
  - `20260413-nightly-fate-x-full`
  - postmortem summaries now capture `incidentTags`, `watchdogRuntime`, and stale-watchdog detection
- validated the new control-plane contract with a real CLI smoke on a temporary Lean project:
  - `owner-mode.json`
  - `bootstrap-state.json`
  - split prewarm logs
  - terminal `teacher-launch-state.json`
  - appended `events.jsonl` transition chain

## Current Control Policy

- `campaign-operator` is the default outer owner path.
- `autoarchon-launch-from-spec` is the default campaign bootstrap and relaunch entrypoint.
- `watchdog` is the reliability wrapper above the orchestrator.
- `manager-agent` is not part of the default runtime path. Only add it for multi-campaign scheduling, long-horizon policy, or human-facing rollups.
- `supervisor-agent` still owns one run and one micro-scope at a time.
- accepted final outputs still come only from validation-backed proofs or validation-backed blocker notes.

## Known Remaining Gaps

- multi-file shards without a safely reusable warmed build still spend too much time in full-project `lake build`
- long-running real campaign soak coverage is still mostly manual even though the main control-plane contracts now have deterministic pytest coverage
- restart-budget and owner-conflict failures are now observable, but we still need fresh-campaign rerun data to show the new launch path actually reduces them
- stale detached launch state still appears often enough that future cleanup heuristics and launch-state simplification remain worth doing

## Next High-ROI Steps

1. reduce cold-start waste
   - tune the current narrow-shard scoped verification policy so it covers more profitable shard shapes without over-verifying wide runs
   - keep warmed-build compatibility checks strict and observable so reuse never masks stale project state
2. keep one manual long-run soak in the loop
   - use it for runtime cost/cold-start profiling and network-flake recovery behavior that deterministic tests cannot simulate
   - compare fresh runs against the archived `20260413` postmortem samples, not against old live roots
3. only after the outer loop is stable, consider higher-level industrial additions
   - acceptance/auditor agents
   - proposition preflight or theorem falsity checkers
   - cross-campaign manager workflows

## Non-Goals For This Phase

- no scheduler rewrite
- no direct orchestrator proof editing
- no promotion of `manager-agent` into the default user path before orchestrator + watchdog are operationally boring
