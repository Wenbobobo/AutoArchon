# Campaign Operator, Watchdog, And Optional Manager

This document explains the outer reliability stack above a single proving run.

The default runtime path is:

`human -> campaign-operator -> autoarchon-launch-from-spec -> autoarchon-orchestrator-watchdog -> orchestrator-agent`

`manager-agent` is still optional and future-facing.

## Role Split

- `campaign-operator` is the default outer role. It converts intent into a tracked spec, launches or resumes a campaign, reads progress, and decides when a stopped campaign should be archived and rerun from a fresh root.
- `watchdog` is the concrete reliability wrapper. It restarts the orchestrator after owner loss or campaign stalls, refreshes compare snapshots, enforces bounded recovery, and writes the authoritative owner lease.
- `orchestrator-agent` is still the campaign owner inside one root. It chooses which run to recover, launches teachers, and finalizes accepted outputs.
- `manager-agent` should only appear when multiple campaigns need one higher-level scheduler or one human-facing portfolio summary.

In other words: `watchdog` is the concrete reliability wrapper, while `manager-agent` is only a possible future policy layer.

## State Surfaces

The reliability stack writes these campaign-level files:

- `control/owner-mode.json`
- `control/owner-lease.json`
- `control/orchestrator-watchdog.json`
- `control/orchestrator-watchdog.log`
- `control/launch-spec.resolved.json`

The most important watchdog fields are:

- `sessionId`
- `watchdogStatus`
- `restartCount`
- `runCounts`
- `statusRunIds`
- `recoverableRunIds`
- `prewarmPlanCounts`
- `prewarmPendingRunIds`
- `activeLaunches`
- `activeWorkRunIds`
- `launchBudget`
- `lastStatusRefreshAt`
- `lastProgressAt`
- `lastRecoveryAt`
- `lastCompareReportAt`
- `ownerLastLogAt`
- `stallReason`
- `budgetExhausted`
- `reportFreshness`
- `ownerLease`

These fields are the intended operator surface. They are more trustworthy than ad hoc terminal output.

## Recommended Launch

The normal launch path is spec-driven:

```bash
uv run --directory /path/to/AutoArchon autoarchon-launch-from-spec \
  --spec-file /path/to/AutoArchon/campaign_specs/fate-m-full.json \
  --shard-size 8
```

That command:

- creates the campaign if it does not exist yet
- writes `control/launch-spec.resolved.json`
- updates `control/owner-mode.json`
- starts `autoarchon-orchestrator-watchdog`

The underlying watchdog entrypoint is still available directly:

```bash
uv run --directory /path/to/AutoArchon autoarchon-orchestrator-watchdog \
  --campaign-root /path/to/campaign-root \
  --model gpt-5.4 \
  --reasoning-effort xhigh \
  --poll-seconds 30 \
  --stall-seconds 300 \
  --owner-silence-seconds 1200 \
  --bootstrap-launch-after-seconds 45 \
  --max-active-launches 2 \
  --launch-batch-size 1 \
  --launch-cooldown-seconds 90
```

## What The Watchdog Actually Owns

- owner lease acquisition and refresh through `control/owner-lease.json`
- restart budget and degraded stop behavior
- bounded bootstrap recovery for queued or relaunchable runs
- stale launch cleanup before deterministic recovery
- compare report refresh and `reportFreshness`
- owner-session log capture into `control/orchestrator-watchdog.log`

It does not:

- edit benchmark `.lean` files
- accept proofs by itself
- replace the orchestrator's campaign judgment

## Practical Inspection

```bash
uv run --directory /path/to/AutoArchon autoarchon-campaign-overview \
  --campaign-root /path/to/campaign-root \
  --markdown

uv run --directory /path/to/AutoArchon autoarchon-campaign-archive \
  --campaign-root /path/to/campaign-root

cat /path/to/campaign-root/control/orchestrator-watchdog.json
tail -n 80 /path/to/campaign-root/control/orchestrator-watchdog.log
cat /path/to/campaign-root/control/owner-lease.json
```

Interpretation hints:

- `reportFreshness.compareIsFresh = false` usually means the compare snapshot is stale relative to `campaign-status.json`.
- `ownerLease.active = true` with a live `ownerPid` or `childPid` means another owner still holds the campaign.
- `budgetExhausted = true` plus `watchdogStatus = degraded` means the wrapper stopped cleanly instead of crashing.
- `recoverableRunIds` should be read together with per-run `recoveryClass`, `retryAfter`, and `lastLaunchExitCode`.

Archived samples live under `reports/postmortem/`.

## Optional Future Manager Layer

`manager-agent` is still a proposed role. If we add it later, it should own:

- campaign portfolio selection
- restart-budget policy across multiple campaigns
- human-facing rollups or ablation reports
- optional delegation to several `campaign-operator` sessions

It should not absorb the existing watchdog mechanics or the orchestrator's proof-facing responsibilities.
