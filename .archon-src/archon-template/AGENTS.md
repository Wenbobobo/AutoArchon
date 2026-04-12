# Archon Project

You are either the plan agent, a prover agent, or the review agent. Read `PROGRESS.md` to determine your role and current objectives. Keep the workspace tidy. Prefer the Lean LSP MCP tools when available.

## Priority Rule

Project-local state under `.archon/` takes precedence over global Archon source files.

## Runtime Assets

- Structured agent contracts live under `.archon/agents/`
- Lean references and helper scripts live under `.archon/lean4/`
- The informal reasoning tool lives at `.archon/tools/archon-informal-agent.py`
- Lean LSP MCP is expected to be configured as `archon-lean-lsp`

## Key Files and Permissions

| File | Plan Agent | Prover Agent | Review Agent | User |
|------|-----------|-------------|-------------|------|
| `.archon/PROGRESS.md` | read + write | read only | read only | read |
| `.archon/RUN_SCOPE.md` | read only | read only | read only | read |
| `.archon/USER_HINTS.md` | read then clear | do not read | do not read | write |
| `.archon/task_pending.md` | read + write | read only | read only | read |
| `.archon/task_done.md` | read + write | read only | read only | read |
| `.archon/task_results/<file>.md` | read | write own file only | read only | read |
| `.archon/informal/` | read + write | read only | read only | read |
| `.archon/proof-journal/` | read | do not access | write | read |
| `.archon/PROJECT_STATUS.md` | read | do not access | write | read |
| `.lean` files | do not edit | write own assigned file only | do not edit | write |

## Agent Roles

### Plan Agent

- Read `.archon/prompts/plan.md`
- Read `.archon/RUN_SCOPE.md` and treat it as a hard constraint
- Read and clear `.archon/USER_HINTS.md`
- Merge `.archon/task_results/` into project state
- Write shared informal notes under `.archon/informal/` when needed
- Write the next objective set to `.archon/PROGRESS.md`
- Do not edit `.lean` files

### Prover Agent

- Read `.archon/prompts/prover-autoformalize.md`, `.archon/prompts/prover-prover.md`, or `.archon/prompts/prover-polish.md` based on the current stage
- Read `.archon/RUN_SCOPE.md` and stay within the allowed files
- Write only your assigned `.lean` file(s) and `.archon/task_results/<file>.md`
- Check for `/- USER: ... -/` comments inside your assigned file

### Review Agent

- Read `.archon/prompts/review.md`
- Read `.archon/proof-journal/current_session/attempts_raw.jsonl`
- Write `.archon/proof-journal/sessions/session_N/` and `.archon/PROJECT_STATUS.md`
- Do not edit `.lean` files or `.archon/PROGRESS.md`
