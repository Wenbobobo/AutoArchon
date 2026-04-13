# Orchestrator

Use this document when you want an interactive top-level owner session instead of the default tracked-spec launcher.

The interactive stack is:

- `campaign-operator`: the human or Codex session that owns one campaign objective
- `orchestrator-agent`: the skill-guided campaign owner inside that session
- `supervisor-agent`: the per-run teacher

If you only need the default unattended path, use `bash scripts/start_fate_overnight_watchdogs.sh` or `autoarchon-launch-from-spec` and skip this page.

## Recommended Interactive Start

Install repo-owned skills once:

```bash
bash scripts/install_repo_skill.sh
```

Start Codex:

```bash
codex -C /path/to/AutoArchon \
  --model gpt-5.4 \
  --sandbox danger-full-access \
  --ask-for-approval never \
  -c model_reasoning_effort=xhigh
```

First prompt:

```text
Use $archon-orchestrator to own this AutoArchon campaign.

You are also acting as the campaign-operator for this session.

Repository root: /path/to/AutoArchon
Source root: /path/to/FATE-M
Campaign root: /path/to/campaigns/fate-m-nightly
Reuse lake from: /path/to/FATE-M
Match regex: '^FATEM/(39|42|43)\\.lean$'
Shard size: 1
Run id mode: file_stem

Mission:
- if the campaign root does not exist yet, bootstrap it with `autoarchon-plan-shards` and `autoarchon-create-campaign`
- if it already exists, treat it as exclusive scope and do not regenerate run specs unless the benchmark scope changed
- launch teachers only from `runs/<id>/control/launch-teacher.sh`
- prefer deterministic single-run recovery via `autoarchon-campaign-recover --run-id <id> --execute`
- refresh `campaign-status.json`, `reports/final/compare-report.json`, and `control/orchestrator-watchdog.json` before making recovery decisions
- finalize only validated proofs and validated blocker notes
```

## CLI Control Plane

These are local terminal commands, not the web UI:

- `autoarchon-plan-shards`
- `autoarchon-create-campaign`
- `autoarchon-launch-from-spec`
- `autoarchon-campaign-status`
- `autoarchon-campaign-overview`
- `autoarchon-campaign-recover`
- `autoarchon-campaign-compare`
- `autoarchon-finalize-campaign`
- `autoarchon-campaign-archive`

Common commands:

```bash
uv run --directory /path/to/AutoArchon autoarchon-launch-from-spec \
  --spec-file /path/to/spec.json

uv run --directory /path/to/AutoArchon autoarchon-campaign-overview \
  --campaign-root /path/to/campaign-root \
  --markdown

uv run --directory /path/to/AutoArchon autoarchon-campaign-recover \
  --campaign-root /path/to/campaign-root \
  --run-id teacher-42 \
  --execute
```

These are local terminal commands, not the web UI.

## Manual Deterministic Bootstrap

Use this path when you want explicit JSON specs under version control or you are preparing a custom benchmark slice.

Generate run specs:

```bash
uv run --directory /path/to/AutoArchon autoarchon-plan-shards \
  --source-root /path/to/FATE-M \
  --run-id-prefix teacher \
  --run-id-mode file_stem \
  --match-regex '^FATEM/(39|42|43)\\.lean$' \
  --shard-size 1 \
  --output /path/to/run-specs.json
```

Create the campaign:

```bash
uv run --directory /path/to/AutoArchon autoarchon-create-campaign \
  --source-root /path/to/FATE-M \
  --campaign-root /path/to/campaign-root \
  --reuse-lake-from /path/to/FATE-M \
  --run-spec-file /path/to/run-specs.json
```

Or write a tracked launch spec and let `autoarchon-launch-from-spec` do both steps:

```bash
uv run --directory /path/to/AutoArchon autoarchon-launch-from-spec \
  --spec-file /path/to/spec.json \
  --shard-size 1
```

Generated control files include:

- `control/owner-mode.json`
- `control/launch-spec.resolved.json`
- `control/owner-lease.json`
- `runs/<id>/control/bootstrap-state.json`
- `runs/<id>/control/teacher-launch-state.json`
- `runs/<id>/control/prewarm.stdout.log`
- `runs/<id>/control/prewarm.stderr.log`
- `runs/<id>/workspace/.archon/supervisor/run-lease.json`

Important bootstrap fields:

- `prewarmRequired`
- `allowedFiles`

## Launch And Recovery Rules

- launch teachers only from `runs/<id>/control/launch-teacher.sh`
- prefer one `--run-id` recovery at a time
- do not use `--all-recoverable --execute` from an owner session
- if launch assets changed after campaign creation, run `autoarchon-refresh-launch-assets`
- if detached launch processes accumulate, use `autoarchon-clean-launchers`
- use `--recovery-only` when a run already has useful artifacts and only needs validation/finalization closure

## Closeout

Build a compare snapshot:

```bash
uv run --directory /path/to/AutoArchon autoarchon-campaign-compare \
  --campaign-root /path/to/campaign-root
```

Finalize accepted outputs:

```bash
uv run --directory /path/to/AutoArchon autoarchon-finalize-campaign \
  --campaign-root /path/to/campaign-root
```

Archive stopped or degraded campaigns before rerunning them:

```bash
uv run --directory /path/to/AutoArchon autoarchon-campaign-archive \
  --campaign-root /path/to/campaign-root
```

Per-run campaign timelines live under `reports/final/runs/<run>/timeline.json`.

## Take Over An Existing Campaign

When a network interruption or owner-session loss happens:

1. refresh truth with `autoarchon-campaign-status`
2. inspect `control/orchestrator-watchdog.json` and `control/owner-lease.json`
3. use `autoarchon-campaign-overview --markdown`
4. refresh launch assets if the runtime changed
5. resume with either a fresh interactive orchestrator session or `autoarchon-launch-from-spec`
