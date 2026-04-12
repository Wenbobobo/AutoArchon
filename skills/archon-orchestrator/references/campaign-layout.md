# Campaign Layout

`create_campaign.py` creates this structure:

```text
campaign-root/
├── CAMPAIGN_MANIFEST.json
├── campaign-status.json
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

Read `campaign-status.json` for the current truth. Use `events.jsonl` as the append-only chronology.
