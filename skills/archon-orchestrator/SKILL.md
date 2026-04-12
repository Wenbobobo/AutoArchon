---
name: archon-orchestrator
description: Coordinate a multi-run Archon campaign from a top-level Codex session. Use when you need campaign setup, run sharding, teacher deployment, cross-run monitoring, recovery decisions, and final acceptance without directly editing Lean proofs.
---

# Archon Orchestrator

Use this skill only for the outermost campaign owner session.

This role is above the teacher/supervisor layer:

- `orchestrator-agent`: owns campaign scope, run creation, teacher deployment, monitoring, recovery, and final reporting
- `supervisor-agent`: owns one run and one micro-scope at a time
- `prover-agent`: owns theorem search and Lean edits inside one assigned file

## Hard Exclusion

Do not use this skill for:

- inner `plan-agent`, `prover-agent`, or `review-agent` sessions
- a teacher session that is already supervising one run with `$archon-supervisor`
- ad hoc proof editing

The orchestrator does not directly edit `.lean` files. If proof work is needed, deploy or restart a teacher, or dispatch a separate helper session.

## Load Order

1. Read [references/startup-brief.md](references/startup-brief.md) first.
2. Read [references/campaign-layout.md](references/campaign-layout.md) before touching campaign state.
3. Read [references/runbook.md](references/runbook.md) when creating runs, launching teachers, or finalizing a campaign.

## Mission

- translate a human goal into isolated runs and micro-shards
- keep teachers disjoint and interpretable
- watch for early exits, stalled runs, contamination, and incomplete acceptance closure
- accept only exported proofs and blocker notes that passed validation
- leave behind a campaign state that a fresh Codex session can resume immediately

## Required Workflow

1. Read `CAMPAIGN_MANIFEST.json`, `campaign-status.json`, and `events.jsonl` if the campaign already exists.
2. If the prompt gives a `Source root` and the `Campaign root` does not exist yet, bootstrap the campaign yourself with `uv run --directory <repo-root> autoarchon-plan-shards` and `uv run --directory <repo-root> autoarchon-create-campaign`.
3. For single-file micro-shards, prefer `--run-id-mode file_stem` so run ids stay human-readable without inspecting old campaigns.
4. If the campaign already exists, do not regenerate run specs unless the user changes scope.
5. For each run, inspect `runs/<id>/control/run-config.json`, `teacher-prompt.txt`, and `launch-teacher.sh`.
6. Launch teachers from the generated control assets; do not handwrite divergent prompts unless you also update the stored control files.
7. Recompute truth with `uv run --directory <repo-root> autoarchon-campaign-status --campaign-root <campaign-root>` before making recovery decisions.
8. If a run is `needs_relaunch`, `unverified`, or `contaminated`, inspect `recommendedRecovery` and prefer `uv run --directory <repo-root> autoarchon-campaign-recover --campaign-root <campaign-root> --run-id <run-id>` over hand-written recovery commands.
9. If a run still needs human judgment after the deterministic recovery plan, then shrink the shard, quarantine the run, or dispatch a helper session.
10. Build or refresh the nightly-facing summary with `uv run --directory <repo-root> autoarchon-campaign-compare --campaign-root <campaign-root>` when you need a compact benchmark report before final closeout.
11. Finalize with `uv run --directory <repo-root> autoarchon-finalize-campaign --campaign-root <campaign-root>` and review `reports/final/final-summary.json`.

## Guardrails

- Do not widen benchmark scope without explicit user instruction.
- If the prompt gives a `Campaign root`, treat that root as exclusive scope. Do not inspect unrelated campaign directories just to choose naming or workflow patterns.
- Do not count live workspace files as final evidence when `artifacts/` or `validation/` disagree.
- Do not let two teachers write the same run root.
- Do not collapse orchestrator, teacher, and prover into one session during a long benchmark campaign.
- Do not directly repair theorem mutations from the orchestrator layer; contain or replace the affected run.

## Stop Conditions

Stop only when one of these is true:

- all campaign runs are in terminal states and the final report is written
- a hard external dependency is missing and campaign execution cannot continue safely
- the user changes the benchmark scope or campaign objective
