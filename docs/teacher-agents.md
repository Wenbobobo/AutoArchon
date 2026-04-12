# Teacher Agent Playbook

Use this document when you want to launch multiple long-running teacher agents in parallel and let each one supervise a separate FATE-level run from a clean Codex context.

This is an operations handoff, not a benchmark report. Each teacher must own its own isolated `run-root`.

If one higher-level Codex session is going to create the runs, launch the teachers, and aggregate their outcomes, use [orchestrator.md](orchestrator.md) for that outer layer. This document is only about the teacher layer.

## One Run Per Teacher

- Teacher A owns one `run-root`
- Teacher B owns one `run-root`
- Teacher C owns one `run-root`
- do not share `.archon/` state across teachers
- do not point two teachers at the same `workspace/`
- compare outcomes from exported `artifacts/`, not from mixed live workspaces

## Prepare Each Run

Run these once per teacher, replacing the source path, run path, and scope for that benchmark level or slice.

```bash
uv run --directory /path/to/AutoArchon autoarchon-create-run-workspace \
  --source-root /path/to/fate-level-source \
  --run-root /path/to/run-root \
  --reuse-lake-from /path/to/warmed-project \
  --scope-hint 'FATEM/...'

uv run --directory /path/to/AutoArchon autoarchon-prewarm-project /path/to/run-root/workspace

./init.sh \
  --objective-limit 5 \
  --objective-regex '^FATEM/(39|40|41|42|43)\\.lean$' \
  /path/to/run-root/workspace
```

If you already know a blocker route or a theorem-fidelity warning, write it into `workspace/.archon/USER_HINTS.md` before the teacher starts.

## Teacher Startup Prompt

Use this as the teacher agent prompt body. Replace the paths and scope details.

```text
Use $archon-supervisor to supervise this AutoArchon run.

Repository root: /abs/path/to/AutoArchon
Run root: /abs/path/to/run-root
Source root: /abs/path/to/run-root/source
Workspace root: /abs/path/to/run-root/workspace

Mission:
- keep theorem headers faithful to source
- supervise repeated plan/prover cycles until the scoped objectives are solved, or a blocker is validated, or an external stop condition is hit
- prefer `uv run --directory /abs/path/to/AutoArchon autoarchon-supervised-cycle --workspace /abs/path/to/run-root/workspace --source /abs/path/to/run-root/source --plan-timeout-seconds 180 --prover-timeout-seconds 240 --prover-idle-seconds 90 --no-review`
- export milestone artifacts with `uv run --directory /abs/path/to/AutoArchon autoarchon-export-run-artifacts --run-root /abs/path/to/run-root`

Rules:
- do not widen scope unless the user changes it
- do not trust prover self-reports without checking source/workspace/task_results
- if the theorem is false as written, keep the original theorem frozen and accept a durable blocker note
- if theorem mutation appears, restore fidelity before counting progress
- do not stop to give an interim report; keep writing workspace/.archon/supervisor/HOT_NOTES.md and workspace/.archon/supervisor/LEDGER.md instead

Stop only when:
- the scoped files are solved and verified, or
- the remaining target is a validated blocker with a written note, or
- a hard external dependency is missing and the run cannot continue safely
```

## Launch Command

Run one Codex session per teacher.

```bash
export ARCHON_CODEX_READY_RETRIES=6
export ARCHON_CODEX_READY_RETRY_DELAY_SECONDS=10

codex exec \
  --skip-git-repo-check \
  --sandbox danger-full-access \
  -c approval_policy=never \
  -c model_reasoning_effort=xhigh \
  --model gpt-5.4 \
  - <<'EOF'
Use $archon-supervisor to supervise this AutoArchon run.

Repository root: /abs/path/to/AutoArchon
Run root: /abs/path/to/run-root
Source root: /abs/path/to/run-root/source
Workspace root: /abs/path/to/run-root/workspace

Mission:
- keep theorem headers faithful to source
- supervise repeated plan/prover cycles until the scoped objectives are solved, or a blocker is validated, or an external stop condition is hit
- prefer `uv run --directory /abs/path/to/AutoArchon autoarchon-supervised-cycle --workspace /abs/path/to/run-root/workspace --source /abs/path/to/run-root/source --plan-timeout-seconds 180 --prover-timeout-seconds 240 --prover-idle-seconds 90 --no-review`
- export milestone artifacts with `uv run --directory /abs/path/to/AutoArchon autoarchon-export-run-artifacts --run-root /abs/path/to/run-root`

Rules:
- do not widen scope unless the user changes it
- do not trust prover self-reports without checking source/workspace/task_results
- if the theorem is false as written, keep the original theorem frozen and accept a durable blocker note
- if theorem mutation appears, restore fidelity before counting progress
- do not stop to give an interim report; keep writing workspace/.archon/supervisor/HOT_NOTES.md and workspace/.archon/supervisor/LEDGER.md instead

Stop only when:
- the scoped files are solved and verified, or
- the remaining target is a validated blocker with a written note, or
- a hard external dependency is missing and the run cannot continue safely
EOF
```

If the run root was created by `autoarchon-create-campaign`, prefer the generated control script instead of pasting the launch command by hand:

```bash
bash /path/to/campaign-root/runs/teacher-a/control/launch-teacher.sh
```

## Watch Progress

Use these from another shell:

```bash
tail -f /path/to/run-root/workspace/.archon/supervisor/HOT_NOTES.md
tail -f /path/to/run-root/workspace/.archon/supervisor/LEDGER.md
tail -f /path/to/run-root/workspace/.archon/supervisor/violations.jsonl
tail -f /path/to/run-root/workspace/.archon/logs/iter-*/provers/*.jsonl
watch -n10 'ls -lt /path/to/run-root/workspace/.archon/task_results/'
bash ui/start.sh --project /path/to/run-root/workspace
```

What to watch for:

- `HOT_NOTES.md`: current status, latest iteration, idle/prover errors, theorem-fidelity warnings
- `LEDGER.md`: chronological cycle summary
- `violations.jsonl`: machine-readable header drift, idle timeout, prover error, recovery events
- `task_results/`: resolved proof notes or validated blocker notes
- `logs/iter-*/provers/*.jsonl`: whether the prover is actually moving or silently stalling

## Where To Read Results

- generated proofs: `run-root/workspace/FATEM/*.lean`
- immutable originals: `run-root/source/FATEM/*.lean`
- blocker and handoff notes: `run-root/workspace/.archon/task_results/*.md`
- deterministic validation verdicts: `run-root/workspace/.archon/validation/*.json`
- lessons and recovery summaries: `run-root/workspace/.archon/lessons/*.json`
- exported proofs for review: `run-root/artifacts/proofs/`
- exported blocker notes: `run-root/artifacts/task-results/`
- exported validation verdicts: `run-root/artifacts/validation/`
- exported lessons: `run-root/artifacts/lessons/`
- supervisor summaries: `run-root/artifacts/supervisor/`

For mathematician review, prefer the exported files under `artifacts/`.

## Minimal Teacher Rules

- a faithful blocker is a success state; do not force a false theorem into a solved count
- if a prover writes a durable blocker note or a faithful changed file and then times out, preserve that artifact
- if a run gets contaminated by theorem mutation or copied `.archon/` history, rebuild the run instead of patching the contaminated evidence forever
- if three teachers are running in parallel, keep their benchmark scopes disjoint so comparison remains interpretable
