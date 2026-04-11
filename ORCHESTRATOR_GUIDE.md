# Orchestrator Guide

This guide is for advanced users who want to drive Archon manually through Codex sessions instead of using the stock `archon-loop.sh` scheduler.

For normal setup, isolated-run workflow, and result inspection, start with [README.md](README.md) and [docs/operations.md](docs/operations.md).

## When To Use This Guide

Use manual orchestration only when you need one of these:

- custom external scheduling
- a nonstandard plan/prover/review ordering
- controlled prompt injection for debugging
- selective replays against an existing `.archon/` state directory

## Preconditions

Before running any stage, confirm:

```bash
test -f .archon/PROGRESS.md
test -f .archon/AGENTS.md
test -f .archon/RUN_SCOPE.md
test -d .archon/prompts
test -L .archon/lean4
test -L .archon/tools/archon-informal-agent.py
```

If those files are missing:

```bash
cd /path/to/Archon
./init.sh /path/to/lean-project
```

## Command Template

Every non-interactive stage is a single Codex session:

```bash
codex exec \
  --json \
  --skip-git-repo-check \
  --sandbox danger-full-access \
  -c approval_policy=never \
  --model gpt-5.4 \
  -
```

Run it from the project directory and feed the full prompt on stdin.

## Prompt Composition

Plan stage:

```text
You are the plan agent for project '<name>'. Current stage: <stage>.
Project directory: <project>
Project state directory: <project>/.archon
Read .archon/AGENTS.md for your role, then read .archon/prompts/plan.md, .archon/PROGRESS.md, and .archon/RUN_SCOPE.md.
Lean workflow references are vendored under .archon/lean4/. Read .archon/lean4/skills/lean4/SKILL.md before acting.
```

Prover stage:

```text
You are the prover agent for project '<name>'. Current stage: <stage>.
Project directory: <project>
Project state directory: <project>/.archon
Read .archon/AGENTS.md for your role, then read .archon/prompts/prover-<stage>.md, .archon/PROGRESS.md, and .archon/RUN_SCOPE.md.
Lean workflow references are vendored under .archon/lean4/. Read .archon/lean4/skills/lean4/SKILL.md before acting.
```

Review stage:

```text
You are the review agent for project '<name>'. Current stage: <stage>.
Project directory: <project>
Project state directory: <project>/.archon
Read .archon/AGENTS.md for your role, then read .archon/prompts/review.md.
Lean workflow references are vendored under .archon/lean4/. Read .archon/lean4/skills/lean4/SKILL.md before acting.
```

## Scheduling Logic

Recommended order:

1. Run plan.
2. Run prover on each target file.
3. Run review if enabled.
4. Re-read `.archon/PROGRESS.md`, `.archon/task_results/`, and the latest proof journal.
5. Repeat until the stage reaches `COMPLETE`.

## State Files

- `.archon/PROGRESS.md`
- `.archon/RUN_SCOPE.md`
- `.archon/task_pending.md`
- `.archon/task_done.md`
- `.archon/task_results/`
- `.archon/informal/`
- `.archon/PROJECT_STATUS.md`
- `.archon/proof-journal/sessions/`

## Benchmark Hygiene

For benchmark reruns:

1. Start from a fresh benchmark worktree.
2. Reuse `.lake/` caches if needed.
3. Do not reuse another run's `.archon/` state.
4. Keep theorem headers frozen when a target is false or underspecified.
5. Treat copied or mixed `.archon/logs/` history as contaminated and do not cite it.

## Notes

- Do not mutate `USER_HINTS.md` from an external orchestrator. Inject temporary guidance directly into the prompt.
- Keep prompts self-contained. Each Codex execution is a fresh session.
- Prefer explicit file ownership when launching multiple prover workers in parallel.
- The authoritative solved artifact is always the target `.lean` file in the run worktree.
