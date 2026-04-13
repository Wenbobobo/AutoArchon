# Postmortem: 2026-04-13 Nightly FATE Samples

This document records the first three overnight `campaign-operator + watchdog + orchestrator` samples:

- `20260413-nightly-fate-m-full`
- `20260413-nightly-fate-h-full`
- `20260413-nightly-fate-x-full`

These runs are archived samples, not final benchmark results.

## Final Archived State

- `M`: `accepted=5`, `needs_relaunch=14`
- `H`: `accepted=2`, `needs_relaunch=11`
- `X`: `accepted=4`, `blocked=1`, `needs_relaunch=8`

Canonical archive artifacts:

- `reports/postmortem/postmortem-summary.json`
- `reports/postmortem/postmortem-summary.md`
- `reports/postmortem/campaign-status.snapshot.json`
- `reports/postmortem/compare-report.snapshot.json`
- `reports/postmortem/watchdog-log.tail.txt`

## Main Failure Classes

### 1. Restart Budget Exhaustion

Observed in:

- `20260413-nightly-fate-m-full`
- `20260413-nightly-fate-h-full`

Meaning:

- the watchdog did not crash
- it stopped in a controlled `degraded` state after using its restart budget
- these roots should not be resumed blindly as if they were healthy live campaigns

### 2. Owner Conflict

Observed in:

- `20260413-nightly-fate-x-full`

Meaning:

- competing owner state or overlapping control surfaces existed during the run
- this is exactly the class of problem the owner lease is now meant to expose

### 3. Stale Watchdog State

Observed in:

- `20260413-nightly-fate-x-full`

Meaning:

- `control/orchestrator-watchdog.json` still said `running`
- but the watchdog pid was already dead and `ownerLeaseLive=false`
- this is why the runtime now records `watchdogRuntime.stateLikelyStale`

### 4. Stale Or Conflicting Launch State

Observed in all three samples.

Meaning:

- detached launch bookkeeping was noisy enough that recovery decisions needed stronger cleanup and better separation between active and stale launcher state

## Why These Roots Must Be Archived, Not Reused

Do not continue these campaigns as the next benchmark baseline.

Reasons:

- they encode historical restart budget exhaustion and owner conflicts
- they were created before the latest spec-driven launch and stale-watchdog detection cleanup fully landed
- continuing them would mix control-plane debugging with benchmark measurement

The correct next step is:

1. archive the sample
2. keep it for diagnosis
3. start a fresh campaign root with the current `campaign_specs/` + `autoarchon-launch-from-spec` path

## Operational Lessons

- a dead watchdog must not be interpreted as a live campaign just because old JSON still says `running`
- owner lease and watchdog pid liveness need to be checked together
- compare snapshots should be refreshed before the campaign is archived so the final postmortem remains consistent
- postmortem artifacts should be written before any rerun so the failed sample remains inspectable

## Next Rerun Rules

- always rerun on a fresh campaign root
- keep the old root read-only except for postmortem archiving
- use `autoarchon-launch-from-spec` instead of ad hoc shell bootstrap
- treat `reports/postmortem/` as the diagnostic source of truth for failed nightlies
- only treat `reports/final/` as benchmark evidence when the campaign reached a real terminal accepted/blocked closure without stale-owner ambiguity
