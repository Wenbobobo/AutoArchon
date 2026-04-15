# Teacher Agent Playbook

Use this document when one teacher Codex session owns one run root.

## Preferred Launch

If the run came from a campaign, prefer the generated launcher:

```bash
bash /path/to/campaign-root/runs/teacher-a/control/launch-teacher.sh
```

That launcher uses:

- `RUN_MANIFEST.json`
- `bootstrap-state.json`
- `teacher-launch-state.json`
- `prewarmRequired`
- `allowedFiles`

## Manual Launch

For narrow shards, prewarm with explicit scope:

```bash
uv run --directory /path/to/AutoArchon autoarchon-prewarm-project \
  /path/to/run-root/workspace \
  --verify-file FATEM/42.lean
```

One manual `codex exec` session per teacher:

```bash
codex exec \
  --skip-git-repo-check \
  --sandbox danger-full-access \
  -c approval_policy=never \
  -c model_reasoning_effort=xhigh \
  --model gpt-5.4 \
  - <<'EOF'
Use $archon-supervisor to supervise this AutoArchon run.
EOF
```

## Monitor

Watch:

- `workspace/.archon/supervisor/HOT_NOTES.md`
- `workspace/.archon/supervisor/LEDGER.md`
- `workspace/.archon/supervisor/progress-summary.md`
- `workspace/.archon/supervisor/progress-summary.json`
- `control/progress-summary.html`
- `workspace/.archon/task_results/`
- `workspace/.archon/logs/iter-*/`

Per-run campaign timelines are exported under `reports/final/runs/<run>/timeline.json`.
`control/progress-summary.html` is the campaign-level browser mirror; keep trusting the matching `progress-summary.json` files as canonical if terminal output and browser output ever disagree.
If `progress-summary.json` shows `planFastPathApplied = true` and `planFastPathReason = "known_routes"`, the supervisor intentionally skipped the initial planner pass and reused an already-recorded exact route or blocker route for the tail-scope objective set.

## Results

- live theorem file under edit: `workspace/<rel-path>.lean`
- live notes: `workspace/.archon/task_results/`
- live validation: `workspace/.archon/validation/`
- run progress snapshot: `workspace/.archon/supervisor/progress-summary.md`
- exported bundle: `artifacts/`
- accepted campaign proofs: `reports/final/proofs/<run>/`

For final human review, prefer `artifacts/` plus `reports/final/validation/<run>/` over the mutable workspace alone.
