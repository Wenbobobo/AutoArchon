# Operator Surfaces

The outer owner must maintain three campaign-level control files:

- `control/mission-brief.md`
- `control/launch-spec.resolved.json`
- `control/operator-journal.md`

## Mission Brief

`mission-brief.md` is the human-readable contract for the campaign.

It should contain:

- real objective
- success criteria
- constraints and forbidden shortcuts
- benchmark scope
- watch items such as theorem mutation, no-progress loops, provider instability, or launch conflicts

Before a long unattended run, replace any scaffolded placeholders.

## Resolved Spec

`launch-spec.resolved.json` is the machine-readable campaign launch contract.

It should match the actual campaign root, source root, shard policy, model, and watchdog settings the owner intends to run. If the owner changes launch policy, update this file before restarting the watchdog.

## Operator Journal

`operator-journal.md` is the durable outer-owner decision log.

Append a timestamped block whenever you:

- create or revise campaign scope
- launch or relaunch the watchdog
- shrink or split shards
- archive a degraded campaign
- finalize accepted outputs

Each journal block should state:

- why you made the decision
- what command or file changed
- what the next expected check is
