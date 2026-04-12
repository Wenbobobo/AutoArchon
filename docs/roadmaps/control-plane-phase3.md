# Phase 3 Roadmap: Campaign Control Plane And Teacher Stability

This roadmap saves the current phase plan after the initial Codex migration and the first orchestrator landing.

## Completed In This Phase

- migrated the outer control plane to explicit `orchestrator-agent` contracts
- added campaign state and launch assets:
  - `CAMPAIGN_MANIFEST.json`
  - `campaign-status.json`
  - `events.jsonl`
  - `runs/*/control/teacher-prompt.txt`
  - `runs/*/control/launch-teacher.sh`
- added campaign scripts:
  - `scripts/plan_campaign_shards.py`
  - `scripts/create_campaign.py`
  - `scripts/campaign_status.py`
  - `scripts/campaign_recover.py`
  - `scripts/campaign_compare.py`
  - `scripts/finalize_campaign.py`
- added `archon-orchestrator` skill and docs for top-level Codex supervision
- kept `single-file / micro-shard` as the default teacher policy
- added deterministic recovery planning and execution:
  - `campaign-status.json` now includes `recommendedRecovery`
  - `scripts/campaign_recover.py` can execute `launch_teacher`, `relaunch_teacher`, or `recovery_only`
- added nightly-friendly compare reporting:
  - `reports/final/compare-report.json`
  - `reports/final/compare-report.md`
  - `scripts/campaign_compare.py`
- added deterministic shard planning:
  - `scripts/plan_campaign_shards.py`
  - stable run ids and grouped `objective_regex` generation from source-root + regex + shard-size

## Stabilization Work Now In Scope

- make teacher ownership run-local with `workspace/.archon/supervisor/run-lease.json`
- stop relying on host-wide generic process names as evidence of contamination
- add `--recovery-only` closure so a fresh supervisor can finalize an interrupted run without blindly rerunning it
- make the campaign layer prefer lease heartbeat over ad hoc log-file heuristics
- preserve deterministic acceptance:
  - theorem fidelity from `validation/`
  - durable blocker notes from `task_results/`
  - accepted export surface from `artifacts/`

## Default Operating Policy

- orchestrator owns campaign setup, teacher deployment, and final reporting
- teacher owns one run root and one micro-scope at a time
- prover owns proof search inside one assigned file
- orchestrator does not directly edit benchmark `.lean` files
- teachers do not widen scope without explicit user instruction
- final campaign reports include only accepted proofs and accepted blocker notes

## Next High-ROI Steps

1. only after the outer loop is stable, consider adding more permanent agents such as:
   - acceptance/auditor agents
   - formalization or statement-check helpers
   - mathlib exploration helpers

## Non-Goals For This Phase

- no plugin-style scheduler rewrite
- no direct orchestrator proof editing
- no broadening from micro-shards back to full-file or full-benchmark blind loops
