# Mathlib Agent Research

This directory is the phase-7 research surface for `mathlib-agent`.

It is intentionally outside the default runtime path.

Current purpose:

- document the future role cleanly before integrating it
- collect repeated missing-lemma and missing-abstraction signals from campaigns
- design retrieval and contribution workflows that can help formalization and open-problem work later

The default runtime remains:

`campaign-operator -> watchdog -> orchestrator-agent -> supervisor-agent -> plan/prover/review`

`mathlib-agent` should only become an optional sidecar after its inputs, outputs, and acceptance boundaries are explicit.

Planned contents:

- `research-plan.md`: role definition and implementation order
- future hint packs, experiments, and mined datasets
