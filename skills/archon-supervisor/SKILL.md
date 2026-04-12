---
name: archon-supervisor
description: Supervise a long-running external AutoArchon run from a clean Codex context. Use only when the user explicitly asks for the AutoArchon supervisor or teacher role across repeated plan/prover cycles, with log watching, theorem-fidelity checks, and restart-state management. Do not use for inner Archon plan/prover/review sessions launched by `archon-loop.sh`.
---

# Archon Supervisor

Use this skill when Codex is acting as the teacher or supervisor for an AutoArchon run, especially for benchmark slices, soak tests, or any run where process integrity matters as much as solved count.

## Hard Exclusion

Do not use this skill for inner Archon plan/prover/review sessions.

If the prompt already assigns you to the Archon `plan agent`, `prover agent`, or `review agent`, or tells you to read `.archon/prompts/plan.md`, `.archon/prompts/prover-*.md`, or `.archon/prompts/review.md`, stop here and follow the local Archon role instructions instead of this supervisor workflow.

This skill is only for the outer supervisor/teacher session that manages repeated cycles from outside the runtime.

## Load Order

1. Read [references/startup-brief.md](references/startup-brief.md) first.
2. Read [references/artifact-map.md](references/artifact-map.md) before judging progress.
3. Read [references/failure-taxonomy.md](references/failure-taxonomy.md) when you see drift, timeout, or suspicious success.
4. Read [references/runbook.md](references/runbook.md) only when you need full command templates or recovery steps.
5. Read [references/commands.md](references/commands.md) when you need known-good shell patterns.

## Mission

- Keep the run moving for hours without losing control of state.
- Prefer one supervised cycle at a time over blind long loops.
- Protect theorem fidelity, scope integrity, and artifact hygiene.
- Record short state for the next restart in `workspace/.archon/supervisor/HOT_NOTES.md`.
- Record fuller chronology in `workspace/.archon/supervisor/LEDGER.md`.

## Required Workflow

1. Identify the immutable `source/`, mutable `workspace/`, and exported `artifacts/` roots.
2. Read `workspace/.archon/RUN_SCOPE.md`, `workspace/.archon/PROGRESS.md`, the latest supervisor notes, and the latest live `task_results/`.
3. Read `workspace/.archon/supervisor/run-lease.json` if it exists; trust this run-local lease over host-wide process-name guesses.
4. Inspect current diffs against `source/` before trusting any apparent success.
5. Run `uv run --directory <repo-root> autoarchon-supervised-cycle --workspace <workspace> --source <source> --no-review` unless you have a concrete reason to change flags.
6. If a previous teacher disappeared after writing useful workspace state, prefer `uv run --directory <repo-root> autoarchon-supervised-cycle --workspace <workspace> --source <source> --recovery-only --skip-process-check` before rerunning proof search.
7. After each cycle, inspect `HOT_NOTES.md`, `violations.jsonl`, `task_results/`, and relevant prover logs.
8. If the cycle is trustworthy and worth preserving, run `uv run --directory <repo-root> autoarchon-export-run-artifacts --run-root <run-root>`.
9. Continue until a stop condition is reached.

## Guardrails

- Do not stop to give an interim report once the task has started. Keep supervising until the scoped task is complete, a blocker is validated, or a hard external stop condition is hit.
- Do not count a theorem as solved if its header drifted from `source/`.
- Do not let copied `.archon/` state from another run contaminate benchmark evidence.
- Do not widen scope without an explicit user instruction.
- Do not prefer “compiled” over “faithful”. A repaired theorem is still a benchmark failure.

## When To Intervene

Intervene immediately if you detect any of the following:

- theorem mutation
- added assumptions
- weakened or changed conclusion
- copied `.archon/` history
- an active run-local lease that belongs to another supervisor or an orphaned loop recorded in `run-lease.json`
- repeated no-progress cycles
- blocker files that were not written even though the theorem is false or underspecified

## Stop Conditions

Stop only when one of these is true:

- the scoped files are solved and verified
- the remaining target is a validated blocker with a written note
- an external dependency is missing and the run cannot continue safely

If a stop condition is hit, leave the workspace in a state that the next clean Codex session can resume from immediately.
