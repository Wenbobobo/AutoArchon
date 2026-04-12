# Orchestrator

Use this document for the top-level Codex session that owns a whole AutoArchon campaign.

This role is intentionally separate from both the teacher and the prover:

- `orchestrator-agent`: accepts the human goal, bootstraps or resumes a campaign, launches teachers, watches campaign health, and finalizes accepted results
- `supervisor-agent`: owns one run at a time and keeps theorem fidelity under repeated supervised cycles
- `prover-agent`: edits Lean files inside the scope assigned by the teacher

If you want one higher-level owner above the orchestrator, use [manager-watchdog.md](manager-watchdog.md). For a single run, stay in [operations.md](operations.md). For teacher-only prompts and live monitoring commands, use [teacher-agents.md](teacher-agents.md).

## Quick Start

This is the recommended entrypoint for new users.

Install the repo-owned skills once:

```bash
bash scripts/install_repo_skill.sh
```

Start a fresh interactive Codex session for the top-level owner:

```bash
codex -C /path/to/AutoArchon \
  --model gpt-5.4 \
  --sandbox danger-full-access \
  --ask-for-approval never \
  -c model_reasoning_effort=xhigh
```

Paste this as the first message in that new session:

```text
Use $archon-orchestrator to own this AutoArchon campaign.

Repository root: /path/to/AutoArchon
Source root: /path/to/FATE-M
Campaign root: /path/to/campaigns/fate-m-nightly
Reuse lake from: /path/to/warmed-project
Match regex: '^FATEM/(39|42|43)\\.lean$'
Shard size: 1
Run id mode: file_stem

Mission:
- if the campaign root does not exist yet, bootstrap it yourself with `uv run --directory /path/to/AutoArchon autoarchon-plan-shards` and `uv run --directory /path/to/AutoArchon autoarchon-create-campaign`
- if the campaign root already exists, treat it as exclusive scope and do not regenerate run specs unless the user changes scope
- launch teachers only from runs/<id>/control/launch-teacher.sh
- do not inspect unrelated sibling campaigns just to choose naming or workflow patterns
- prefer deterministic recovery via `uv run --directory /path/to/AutoArchon autoarchon-campaign-recover` over ad hoc shell logic
- finalize only validated proofs and accepted blocker notes

Stop only when:
- all runs are in terminal states and reports/final/ is up to date, or
- a hard external dependency prevents safe continuation
```

## Control-Plane CLI

These are terminal CLI commands for machine-readable campaign state. They are not the web UI.

- `autoarchon-campaign-status`: recompute `campaign-status.json`
- `autoarchon-campaign-recover`: launch queued teachers, relaunch stale runs, or close interrupted runs with `--recovery-only`
- `autoarchon-campaign-compare`: build a compact benchmark-facing compare report
- `autoarchon-finalize-campaign`: copy only accepted proofs and accepted blocker notes into `reports/final/`

Typical commands from another shell:

```bash
uv run --directory /path/to/AutoArchon autoarchon-campaign-status --campaign-root /path/to/campaigns/fate-m-nightly
uv run --directory /path/to/AutoArchon autoarchon-campaign-recover --campaign-root /path/to/campaigns/fate-m-nightly --all-recoverable --execute
uv run --directory /path/to/AutoArchon autoarchon-campaign-compare --campaign-root /path/to/campaigns/fate-m-nightly
uv run --directory /path/to/AutoArchon autoarchon-finalize-campaign --campaign-root /path/to/campaigns/fate-m-nightly
```

While the orchestrator is running, inspect progress from another shell:

```bash
tail -n 40 /path/to/campaigns/fate-m-nightly/runs/teacher-a/workspace/.archon/supervisor/HOT_NOTES.md
tail -n 40 /path/to/campaigns/fate-m-nightly/runs/teacher-a/workspace/.archon/supervisor/LEDGER.md
```

For a fresh campaign where every run is still `queued`, `autoarchon-campaign-recover --all-recoverable --execute` is the fastest safe fan-out path. Detached launch writes `control/teacher-launch-state.json` before the teacher reaches `run-lease.json`, so a second owner or recovery pass sees that run as in-flight instead of launching another teacher into the same `workspace/`.

## Manual Deterministic CLI Path

Use this when you want fully explicit operator control instead of letting the first owner prompt bootstrap the campaign.

Generate stable run specs:

```bash
uv run --directory /path/to/AutoArchon autoarchon-plan-shards \
  --source-root /path/to/FATE-M \
  --run-id-prefix teacher \
  --run-id-mode file_stem \
  --match-regex '^FATEM/(39|42|43)\\.lean$' \
  --shard-size 1 \
  --output /path/to/run-specs.json
```

Create the campaign root and all per-run control assets:

```bash
uv run --directory /path/to/AutoArchon autoarchon-create-campaign \
  --source-root /path/to/FATE-M \
  --campaign-root /path/to/campaigns/fate-m-nightly \
  --reuse-lake-from /path/to/warmed-project \
  --run-spec-file /path/to/run-specs.json
```

`run-specs.json` is a JSON array:

```json
[
  {
    "id": "teacher-39",
    "objective_regex": "^(FATEM/39\\.lean)$",
    "objective_limit": 1,
    "scope_hint": "FATEM/39.lean"
  },
  {
    "id": "teacher-42",
    "objective_regex": "^(FATEM/42\\.lean)$",
    "objective_limit": 1,
    "scope_hint": "FATEM/42.lean"
  }
]
```

For single-file micro-shards, `--run-id-mode file_stem` is the recommended default because it keeps run ids human-readable without requiring the orchestrator to inspect old campaigns.

This creates:

- `CAMPAIGN_MANIFEST.json`
- `campaign-status.json`
- `events.jsonl`
- `runs/<id>/source`
- `runs/<id>/workspace`
- `runs/<id>/artifacts`
- `runs/<id>/control/run-config.json`
- `runs/<id>/control/teacher-prompt.txt`
- `runs/<id>/control/launch-teacher.sh`

## Launch Teachers

Start each teacher from its generated control script:

```bash
bash /path/to/campaign-root/runs/teacher-a/control/launch-teacher.sh
bash /path/to/campaign-root/runs/teacher-b/control/launch-teacher.sh
```

The launch script:

- prewarms the run workspace when needed
- initializes `.archon/` if the run has not been initialized yet
- starts a fresh `codex exec` session using `$archon-supervisor`

Do not hand-edit the launch prompt unless you also update the stored control files. The campaign root is the control plane source of truth.

## Recover A Run

Preview the deterministic recovery command:

```bash
uv run --directory /path/to/AutoArchon autoarchon-campaign-recover \
  --campaign-root /path/to/campaign-root \
  --run-id teacher-a
```

Execute it:

```bash
uv run --directory /path/to/AutoArchon autoarchon-campaign-recover \
  --campaign-root /path/to/campaign-root \
  --run-id teacher-a \
  --execute
```

For bulk recovery:

```bash
uv run --directory /path/to/AutoArchon autoarchon-campaign-recover \
  --campaign-root /path/to/campaign-root \
  --all-recoverable \
  --execute
```

Key statuses:

- `queued`: no live work has started
- `running`: recent supervisor or prover activity exists
- `accepted`: the run closed with accepted proof artifacts
- `blocked`: the run closed with accepted blocker notes
- `unverified`: changed files or task results exist without full acceptance closure
- `needs_relaunch`: the run has partial state but no active progress
- `contaminated`: validation rejected theorem fidelity or other run integrity

Key state files:

- `runs/<id>/control/teacher-launch-state.json`: detached launch pre-lease marker
- `runs/<id>/workspace/.archon/supervisor/run-lease.json`: authoritative teacher ownership and heartbeat

## Closeout

Build a compact compare report:

```bash
uv run --directory /path/to/AutoArchon autoarchon-campaign-compare --campaign-root /path/to/campaign-root
```

Then finalize:

```bash
uv run --directory /path/to/AutoArchon autoarchon-finalize-campaign --campaign-root /path/to/campaign-root
```

Review:

- `reports/final/compare-report.json`
- `reports/final/compare-report.md`
- `reports/final/final-summary.json`
- `reports/final/proofs/`
- `reports/final/blockers/`
- `reports/final/validation/`

## Take Over An Existing Campaign

Use this when the campaign root already exists and you want a fresh owner session to resume after a network interruption or owner-session loss.

Recompute truth first:

```bash
uv run --directory /path/to/AutoArchon autoarchon-campaign-status --campaign-root /path/to/campaign-root
```

Start a fresh interactive Codex session:

```bash
codex -C /path/to/AutoArchon \
  --model gpt-5.4 \
  --sandbox danger-full-access \
  --ask-for-approval never \
  -c model_reasoning_effort=xhigh
```

Paste this as the first message:

```text
Use $archon-orchestrator to own this existing AutoArchon campaign.

Repository root: /path/to/AutoArchon
Campaign root: /path/to/campaign-root

Mission:
- treat this campaign root as exclusive scope
- inspect CAMPAIGN_MANIFEST.json, campaign-status.json, and recommendedRecovery before acting
- do not regenerate run specs unless the user explicitly changes scope
- do not inspect unrelated sibling campaigns just to choose naming or workflow patterns
- apply recommendedRecovery deterministically before inventing custom shell logic
- finalize only validated proofs and accepted blocker notes

Stop only when:
- all runs are in terminal states and reports/final/ is current, or
- a hard external dependency prevents safe continuation
```

For interrupted campaigns, this takeover flow is safer than rebuilding runs or hand-launching teachers blindly.
