# Artifact Map

Authoritative locations inside an isolated run:

- `run-root/source/`
  - immutable comparison baseline
- `run-root/workspace/`
  - actual Lean files edited by Archon
- `run-root/workspace/.archon/logs/`
  - live plan and prover logs
- `run-root/workspace/.archon/task_results/`
  - per-file blocker or progress notes
- `run-root/workspace/.archon/supervisor/HOT_NOTES.md`
  - short restart summary
- `run-root/workspace/.archon/supervisor/LEDGER.md`
  - fuller chronological ledger
- `run-root/workspace/.archon/supervisor/violations.jsonl`
  - machine-readable policy violations
- `run-root/artifacts/proofs/`
  - exported changed Lean files
- `run-root/artifacts/diffs/`
  - unified diffs against `source/`
- `run-root/artifacts/blockers/`
  - exported blocker notes

The dashboard is a browser over workspace artifacts. It is not a hidden source of truth.
