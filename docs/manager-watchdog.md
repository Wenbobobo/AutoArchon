# Manager And Watchdog

Use this document when one higher-level owner is responsible for long unattended benchmark progress, not just a single campaign control pass.

This layer sits above the orchestrator:

- `manager-agent` is a proposed role that decides campaign scope, restart budget, monitoring policy, and human-facing summaries
- `orchestrator-agent` owns one campaign root at a time
- `supervisor-agent` owns one run root at a time

## When To Use It

Use the manager/watchdog layer when you need any of the following:

- overnight or multi-hour benchmark ownership
- automatic restart after owner-session loss or orchestrator stalls
- one place to read restart state, progress fingerprint, and intervention count
- ablation testing across different owner policies

If you only need one interactive owner session, stay in [orchestrator.md](orchestrator.md).

## Current Runtime Boundary

Today the runtime entrypoint is `autoarchon-orchestrator-watchdog`. It is the concrete watchdog implementation. `manager-agent` is documented now as a proposed long-term owner contract so future Codex sessions can inherit the same policy surface.

Watchdog state lives under the campaign control root:

- `control/orchestrator-watchdog.json`
- `control/orchestrator-watchdog.log`
- `control/orchestrator-prompt.txt`

## Recommended Launch

First create or identify the campaign root. Then run:

```bash
uv run --directory /path/to/AutoArchon autoarchon-orchestrator-watchdog \
  --campaign-root /path/to/campaigns/fate-m-nightly \
  --model gpt-5.4 \
  --reasoning-effort xhigh \
  --poll-seconds 30 \
  --stall-seconds 300 \
  --bootstrap-launch-after-seconds 45
```

What this does:

- writes a default orchestrator prompt if one does not exist
- launches a fresh `codex exec` owner session
- polls `campaign-status.json`
- fingerprints progress from run statuses, `events.jsonl`, launch logs, and supervisor state
- restarts or resumes the owner session when progress stalls
- finalizes the campaign once every run is terminal, unless `--no-finalize` is set

## Observability Contract

The higher-level owner should track at least these fields:

- `campaignId`
- `sessionId`
- `restartCount`
- `stallSeconds`
- `lastFingerprint`
- `acceptedProofCount`
- `acceptedBlockerCount`
- `manualInterventions`

Practical inspection commands:

```bash
uv run --directory /path/to/AutoArchon autoarchon-campaign-status --campaign-root /path/to/campaign-root
tail -n 80 /path/to/campaign-root/control/orchestrator-watchdog.log
cat /path/to/campaign-root/control/orchestrator-watchdog.json
tail -n 40 /path/to/campaign-root/runs/teacher-a/workspace/.archon/supervisor/HOT_NOTES.md
```

## Manager Responsibilities

The future `manager-agent` should own:

- campaign selection and sharding policy
- watchdog restart budget
- human update cadence
- acceptance of final campaign reports
- cross-campaign ablation comparisons

It should not:

- directly edit benchmark `.lean` files
- bypass orchestrator control files
- hide blocked theorems as solved

## Ablation Protocol

When evaluating reliability or quality, compare these modes on the same micro-shard set:

1. supervisor-only
2. orchestrator-only
3. orchestrator + watchdog
4. future manager + watchdog

Record at least:

- terminal closure rate
- false acceptance count
- duplicate-teacher incidents
- stale-run detection latency
- manual interventions
- finalization latency

This keeps the next industrialization phase honest: the manager layer must improve control and reliability, not just add more prompts.
