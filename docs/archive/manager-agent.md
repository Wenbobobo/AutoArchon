# Manager Agent

`manager-agent` is an archived future idea for multi-campaign policy, portfolio scheduling, or human-facing rollups above several `campaign-operator` sessions.

It is not part of the default runtime path.

Today, the default path is:

`campaign-operator -> autoarchon-init-campaign-spec -> autoarchon-launch-from-spec -> watchdog -> orchestrator-agent`

If we ever revive `manager-agent`, it should stay above `campaign-operator` and should not absorb existing watchdog mechanics or the orchestrator's proof-facing responsibilities.
