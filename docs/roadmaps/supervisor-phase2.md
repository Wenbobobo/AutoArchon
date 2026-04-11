# Phase 2 Roadmap: Supervisor Skill And Isolated Runs

## Summary

This phase makes the supervisor loop first-class.

The main goals are:

- keep benchmark and source fidelity under long-running Codex execution
- separate immutable `source / workspace / artifacts` roles cleanly
- give a fresh Codex session enough compact guidance to supervise a run for hours
- preserve a short startup brief and a longer archival ledger for each run

## Core Additions

- `skills/archon-supervisor/`
  - compact supervisor skill
  - startup brief
  - runbook
  - failure taxonomy
  - artifact map
- `scripts/create_run_workspace.py`
  - builds `run-root/source`, `run-root/workspace`, `run-root/artifacts`
- `scripts/supervised_cycle.py`
  - runs one monitored Archon cycle and records violations
- `scripts/export_run_artifacts.py`
  - exports changed Lean files, diffs, blocker notes, and supervisor notes
- `workspace/.archon/supervisor/`
  - `HOT_NOTES.md`
  - `LEDGER.md`
  - `violations.jsonl`

## Source / Workspace / Artifacts

- `source/` is the immutable benchmark or source snapshot used for validation.
- `workspace/` is the actual Lean project that Archon edits in place.
- `artifacts/` is the human-facing export bundle for later review, math validation, and reporting.

This boundary is mandatory for benchmark-faithful work. Teacher supervision alone is not sufficient evidence.

## Supervisor Policy

The supervisor is responsible for process integrity, not just completion.

It must:

- watch logs, task results, diff surfaces, and validation notes
- reject theorem-header mutation and hidden benchmark repair
- detect copied `.archon/` history and stale process contamination
- keep shrinking scope or restarting from a fresh isolated run when needed
- continue until the scope is solved, a blocker is validated, or an external stop condition is hit

It must not:

- stop to give an interim report once the task has started
- widen the run scope without an explicit user instruction
- count a repaired theorem as a benchmark success

## Soak Test Target

The canonical soak test is a fresh isolated FATE slice run driven by `$archon-supervisor`.

Success means:

- multiple supervised cycles complete without losing control of the run
- theorem headers remain benchmark-faithful
- violations are recorded and corrected
- proof and blocker artifacts are exported cleanly

The full launch procedure lives in [operations.md](../operations.md).
