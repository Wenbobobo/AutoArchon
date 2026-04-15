# Campaign Operator Prompt Template

Paste this into an interactive Codex session after replacing the placeholder values.

```text
Use $archon-orchestrator to own this AutoArchon campaign.

Repository root: /path/to/AutoArchon
Source root: /path/to/source-or-benchmark-clone
Campaign root: /path/to/runs/campaigns/20260416-my-campaign
Reuse lake from: /path/to/source-or-benchmark-clone

Real user objective:
- explain the actual campaign goal in plain language
- say whether this must remain benchmark-faithful, formalization-oriented, or open-problem oriented
- state what counts as success
- state what counts as acceptable blockers or postmortem-only output
- ask intake questions if scope, regex, run-id policy, helper policy, or monitoring expectations are still unclear
```

Expected operator behavior:

- write or refresh `control/mission-brief.md`
- write or refresh `control/launch-spec.resolved.json`
- append the initial reviewed decision to `control/operator-journal.md`
- run `autoarchon-validate-launch-contract` before launch
- launch or resume the watchdog only after the control bundle is coherent
