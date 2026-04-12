# Agent Registry

This fork keeps the canonical agent registry in `agents/*.json`.

The goal is to make agent boundaries explicit without turning Archon into a heavier runtime plugin system too early. Each registry entry records:

- current status: `active` or `proposed`
- role summary
- read and write surface
- output artifacts
- handoff targets
- observability fields

## Why This Exists

Archon already has a stable file-based runtime contract. The fastest way to improve extensibility is to document that contract and test it, not to redesign the scheduler first.

This gives us:

- a single place to explain what each agent owns
- a safer path for introducing future agents such as validators or supervisors
- a cheap way to keep runtime, tests, and docs aligned

## Current Runtime Agents

- `plan-agent`: scopes work, merges results, and prepares informal notes
- `prover-agent`: edits assigned Lean files and emits theorem-level results
- `review-agent`: summarizes attempts and writes project-level status
- `informal-agent`: auxiliary proof-sketch helper
- `statement-validator`: writes deterministic theorem-fidelity verdicts under `.archon/validation/`
- `supervisor-agent`: writes acceptance and lessons artifacts under `.archon/supervisor/` and `.archon/lessons/`

## Current Outer Control Agent

- `orchestrator-agent`: owns campaign setup, run sharding, teacher deployment, cross-run monitoring, and final reporting under `reports/final/`

## Proposed Higher-Level Owner

- `manager-agent`: owns watchdog policy, restart budgets, human-facing summaries, and campaign-level accountability above the orchestrator

## Boundary With Vendored Lean4 Material

The files under `.archon-src/skills/lean4/agents/` are reference material inherited with the vendored Lean4 plugin. They are useful examples and support tooling, but they are **not the canonical runtime registry**.

When documenting or extending Archon itself, prefer the contracts in:

- `agents/*.json`
- `.archon-src/archon-template/AGENTS.md`
- `.archon-src/prompts/*.md`
- `docs/architecture.md`
- `docs/operations.md`

Treat the vendored Lean4 agent files as reference material, not as the source of truth for Archon's scheduler.

`orchestrator-agent` is also outside the vendored Lean4 materials. It is part of this fork's explicit control-plane contract, not a vendored helper.

## Integration Rule

New agents should land in this order:

1. registry entry in `agents/*.json`
2. documentation update
3. tests for the new contract
4. runtime wiring only after the agent has a clear acceptance signal
