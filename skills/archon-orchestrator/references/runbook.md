# Runbook

## Interactive Operator Checklist

1. Create or verify `control/mission-brief.md`.
2. Create or verify `control/launch-spec.resolved.json`.
3. Append the starting decision to `control/operator-journal.md`.
4. Only then launch or resume the watchdog.
5. After every recovery, archive, or finalize action, append another journal block.

## Create A Campaign

Generate stable run specs:

```bash
uv run --directory /path/to/AutoArchon autoarchon-plan-shards \
  --source-root /path/to/FATE-M \
  --match-regex '^FATEM/(1|2|3|4)\\.lean$' \
  --run-id-mode file_stem \
  --shard-size 1 \
  --output /path/to/run-specs.json
```

```bash
uv run --directory /path/to/AutoArchon autoarchon-create-campaign \
  --source-root /path/to/FATE-M \
  --campaign-root /path/to/campaigns/fate-m-nightly \
  --reuse-lake-from /path/to/warmed-project \
  --run-spec-file /path/to/run-specs.json
```

`run-specs.json` is a JSON array of objects like:

```json
[
  {
    "id": "teacher-1",
    "objective_regex": "^FATEM/1\\.lean$",
    "objective_limit": 1,
    "scope_hint": "FATEM/1.lean"
  }
]
```

For existing campaigns, stay inside the given `campaign-root/`. Do not inspect unrelated sibling campaigns just to infer run naming conventions.

## Launch A Teacher

```bash
bash /path/to/campaign-root/runs/teacher-a/control/launch-teacher.sh
```

The launch script prewarms the workspace, initializes `.archon/` when needed, and starts a fresh `codex exec` teacher with `$archon-supervisor`.

For a fresh campaign where all runs are `queued`, prefer this bulk launch:

```bash
uv run --directory /path/to/AutoArchon autoarchon-campaign-recover --campaign-root /path/to/campaign-root --all-recoverable --execute
```

This writes `control/teacher-launch-state.json` before detached launch so another owner or recovery pass can see that the run is already in flight.

## Refresh Campaign Truth

```bash
uv run --directory /path/to/AutoArchon autoarchon-campaign-status --campaign-root /path/to/campaign-root
```

For the cheapest human-facing snapshot, also refresh:

```bash
uv run --directory /path/to/AutoArchon autoarchon-campaign-overview --campaign-root /path/to/campaign-root --markdown
```

Inspect:

- `campaign-status.json`
- `control/progress-summary.json`
- `control/progress-summary.md`
- `control/progress-summary.html`
- `runs/*/workspace/.archon/supervisor/HOT_NOTES.md`
- `runs/*/workspace/.archon/supervisor/LEDGER.md`
- `runs/*/workspace/.archon/validation/*.json`

Treat `control/progress-summary.json` as canonical. The Markdown and HTML files are mirrors generated from the same overview payload.

## Recover A Run

Preview the deterministic recovery command:

```bash
uv run --directory /path/to/AutoArchon autoarchon-campaign-recover --campaign-root /path/to/campaign-root --run-id teacher-a
```

Execute it:

```bash
uv run --directory /path/to/AutoArchon autoarchon-campaign-recover --campaign-root /path/to/campaign-root --run-id teacher-a --execute
```

For bulk recovery:

```bash
uv run --directory /path/to/AutoArchon autoarchon-campaign-recover --campaign-root /path/to/campaign-root --all-recoverable --execute
```

## Finalize

For a compact benchmark-style report before or after finalization:

```bash
uv run --directory /path/to/AutoArchon autoarchon-campaign-compare --campaign-root /path/to/campaign-root
```

Review:

- `reports/final/compare-report.json`
- `reports/final/compare-report.md`

Then finalize:

```bash
uv run --directory /path/to/AutoArchon autoarchon-finalize-campaign --campaign-root /path/to/campaign-root
```

Review:

- `reports/final/final-summary.json`
- `reports/final/proofs/`
- `reports/final/blockers/`
- `reports/final/validation/`
