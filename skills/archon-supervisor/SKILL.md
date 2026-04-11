---
name: archon-supervisor
description: Supervise long-running Archon proof runs from a clean Codex context. Use when Codex should manage repeated plan/prover cycles, watch logs and diffs, reject theorem mutation, record lessons, and keep going until the scope is solved, a blocker is validated, or an external stop condition is hit.
---

# Archon Supervisor

Use this skill when Codex is acting as the teacher or supervisor for an Archon run, especially for benchmark slices, soak tests, or any run where process integrity matters as much as solved count.

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
3. Inspect current diffs against `source/` before trusting any apparent success.
4. Run `python3 scripts/supervised_cycle.py --workspace <workspace> --source <source> --no-review` unless you have a concrete reason to change flags.
5. After each cycle, inspect `HOT_NOTES.md`, `violations.jsonl`, `task_results/`, and relevant prover logs.
6. If the cycle is trustworthy and worth preserving, run `python3 scripts/export_run_artifacts.py --run-root <run-root>`.
7. Continue until a stop condition is reached.

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
- stale `archon-loop.sh`, `codex exec`, or `lake serve` processes
- repeated no-progress cycles
- blocker files that were not written even though the theorem is false or underspecified

## Stop Conditions

Stop only when one of these is true:

- the scoped files are solved and verified
- the remaining target is a validated blocker with a written note
- an external dependency is missing and the run cannot continue safely

If a stop condition is hit, leave the workspace in a state that the next clean Codex session can resume from immediately.
