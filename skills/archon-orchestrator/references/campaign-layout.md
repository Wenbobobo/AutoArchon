# Campaign Layout

`create_campaign.py` creates this structure:

```text
campaign-root/
├── CAMPAIGN_MANIFEST.json
├── campaign-status.json
├── control/
│   ├── mission-brief.md
│   ├── launch-spec.resolved.json
│   ├── operator-journal.md
│   ├── progress-summary.json
│   ├── progress-summary.md
│   └── progress-summary.html
├── events.jsonl
├── reports/final/
└── runs/
    └── <run-id>/
        ├── RUN_MANIFEST.json
        ├── source/
        ├── workspace/
        ├── artifacts/
        └── control/
            ├── run-config.json
            ├── teacher-prompt.txt
            └── launch-teacher.sh
```

Rules:

- `source/` stays immutable
- `workspace/` is teacher-owned
- `artifacts/` is the accepted export surface
- `control/` is orchestrator-owned

Read `campaign-status.json` for current run-state truth and `control/progress-summary.json` for the cheap campaign-level snapshot. `progress-summary.md` and `progress-summary.html` are read-only mirrors of the same overview payload. Use `events.jsonl` as the append-only chronology.
