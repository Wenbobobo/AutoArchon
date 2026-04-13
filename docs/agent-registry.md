# Agent Registry

The canonical AutoArchon role registry lives in `agents/*.json`.

This registry is deliberately lightweight. The runtime is still file-based, not plugin-based, so the registry exists to make role boundaries explicit and testable before we add more automation.

Each registry entry records:

- `status`
- `kind`
- `summary`
- read surface
- write surface
- outputs
- handoff targets
- observability fields

## Active Runtime Roles

- `campaign-operator`: default outer operator that prepares spec-driven campaigns, launches watchdogs, reads overviews, and archives postmortems.
- `orchestrator-agent`: owns one campaign root, launches teachers, runs deterministic recovery, and finalizes accepted outputs.
- `supervisor-agent`: owns one run root and one supervised loop at a time.
- `plan-agent`: scopes next work from `.archon/PROGRESS.md` and durable task results.
- `prover-agent`: edits scoped Lean files and emits theorem-level results.
- `review-agent`: summarizes runs and writes longer-horizon notes.
- `informal-agent`: auxiliary mathematical sketch helper.
- `statement-validator`: deterministic benchmark-fidelity and acceptance checker.

## Reliability Wrapper

- `watchdog` is the concrete reliability wrapper around the orchestrator. It is intentionally not an `agents/*.json` entry because it is a mechanical wrapper, not a proof-search or judgment role.

Its observable surfaces are:

- `control/orchestrator-watchdog.json`
- `control/orchestrator-watchdog.log`
- `control/owner-lease.json`
- `reports/final/compare-report.json`

## Proposed Future Role

- `manager-agent`: optional future owner above `campaign-operator` for cross-campaign scheduling, human-facing rollups, or portfolio-level policy. It is not part of the default path today.

## Boundary With Vendored Lean4 Material

The files under `.archon-src/skills/lean4/agents/` are reference material inherited from vendored Lean4 support. They are useful examples, but they are **not the canonical runtime registry**.

When updating AutoArchon itself, use these as the source of truth:

- `agents/*.json`
- `.archon-src/archon-template/AGENTS.md`
- `docs/architecture.md`
- `docs/orchestrator.md`
- `docs/operations.md`

Treat vendored Lean4 agent files as reference material, not scheduler truth.

## Integration Rule

For new permanent roles:

1. add `agents/*.json`
2. update docs
3. add or update contract tests
4. wire runtime behavior only after the acceptance signal is explicit
