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

- `campaign-operator`: default interactive outer operator that interprets user intent, maintains `mission-brief.md` / `launch-spec.resolved.json` / `operator-journal.md`, launches watchdogs, reads overviews, and archives postmortems.
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

## Proposed Extension Roles

- `helper-prover-agent`: bounded side-model helper that may propose lemmas, tactics, or informal proof sketches through the runtime surfaces `.archon/runtime-config.toml` and `.archon/tools/archon-helper-prover-agent.py`, but does not own final acceptance.
- `mathlib-agent`: future retrieval and clustering role driven by `reports/final/lessons/lesson-records.jsonl`, `reports/postmortem/lessons/lesson-records.jsonl`, and their derived `lesson-clusters.json` / `lesson-clusters.md`.
- `manager-agent`: archived future portfolio role. See [archive/manager-agent.md](archive/manager-agent.md). It is not part of the default runtime path.

## Helper Provider Contract

The canonical `helper-prover-agent` transport contract now lives in `.archon/runtime-config.toml`:

```toml
[helper]
enabled = true
provider = "openai"
model = "gpt-5.4"
api_key_env = "OPENAI_API_KEY"
base_url_env = "OPENAI_BASE_URL"
max_retries = 5
initial_backoff_seconds = 5
timeout_seconds = 300

[[helper.fallbacks]]
provider = "gemini"
model = "gemini-3.1-pro-preview"

[helper.plan]
enabled = true
max_calls_per_iteration = 1
trigger_on_missing_infrastructure = true
trigger_on_external_reference = true
trigger_on_repeated_failure = true
notes_dir = ".archon/informal/helper"

[helper.prover]
enabled = true
max_calls_per_session = 2
trigger_on_missing_infrastructure = true
trigger_on_lsp_timeout = true
trigger_on_first_stuck_attempt = true
notes_dir = ".archon/informal/helper"

[observability]
write_progress_surface = true
```

`provider` resolves through the existing informal-agent transport and is validated against `openai`, `gemini`, and `openrouter`. OpenAI-compatible providers such as DeepSeek should be wired through `api_key_env` and `base_url_env` rather than a separate runtime role. Optional `[[helper.fallbacks]]` entries give the helper one bounded failover chain when the primary provider transport fails.

Each initialized workspace gets:

- `.archon/runtime-config.toml`
- `.archon/tools/archon-helper-prover-agent.py`
- `.archon/tools/archon-informal-agent.py`

The helper wrapper prefers the config-backed transport when `enabled` is true and otherwise leaves the older informal tool available as a compatibility fallback. Legacy `.archon/helper-provider.json` is still accepted for compatibility, but new workspaces should use the TOML file.

By default, helper output should be written under `.archon/informal/helper/` and then referenced from `PROGRESS.md` or `task_results/<file>.md`, rather than mixed directly into the durable task-result note namespace.

The preferred helper call shape is phase-aware and note-routed:

```bash
.archon/tools/archon-helper-prover-agent.py \
  --phase plan|prover \
  --rel-path <file> \
  --reason <trigger> \
  --prompt-pack auto \
  --write-note auto \
  "<prompt>"
```

`--prompt-pack auto` selects a task-class helper template from the phase and reason, while `--write-note auto` writes a metadata-backed Markdown note into the phase-specific `notes_dir` (`[helper.plan].notes_dir` or `[helper.prover].notes_dir`). An explicit path still keeps the older bare-text compatibility behavior. `--print-effective-config` now exposes both policy blocks plus prompt-pack selection so operators can inspect the resolved helper limits before launch.

## Boundary With Vendored Lean4 Material

The files under `.archon-src/skills/lean4/agents/` are reference material inherited from vendored Lean4 support. They are useful examples, but they are **not the canonical runtime registry**.

When updating AutoArchon itself, use these as the source of truth:

- `agents/*.json`
- `.archon-src/archon-template/AGENTS.md`
- `docs/architecture.md`
- `docs/campaign-operator.md`
- `docs/operations.md`

Treat vendored Lean4 agent files as reference material, not scheduler truth.

## Integration Rule

For new permanent roles:

1. add `agents/*.json`
2. update docs
3. add or update contract tests
4. wire runtime behavior only after the acceptance signal is explicit
