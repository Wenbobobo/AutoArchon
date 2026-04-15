from __future__ import annotations

import json
import subprocess
from pathlib import Path

from archonlib.lesson_clusters import build_lesson_clusters


ROOT = Path(__file__).resolve().parents[1]


def test_build_lesson_clusters_groups_by_category_theorem_and_action():
    payload = build_lesson_clusters(
        [
            {
                "campaign_id": "camp",
                "run_id": "run-1",
                "theorem_id": "FATEM/1.lean",
                "category": "scope_control",
                "summary": "Tighten the scope before retrying.",
                "action_taken": "tighten_scope",
                "accepted_state": "accepted",
            },
            {
                "campaign_id": "camp",
                "run_id": "run-2",
                "theorem_id": "FATEM/1.lean",
                "category": "scope_control",
                "summary": "Tighten the scope before retrying.",
                "action_taken": "tighten_scope",
                "accepted_state": "blocked",
            },
            {
                "campaign_id": "camp",
                "run_id": "run-3",
                "theorem_id": "FATEM/2.lean",
                "category": "blocker_discipline",
                "summary": "Freeze the false statement and export a blocker.",
                "action_taken": "export_final_blocker",
                "accepted_state": "blocked",
            },
        ],
        source_paths=["/tmp/lesson-records.jsonl"],
        top_n=5,
    )

    assert payload["recordCount"] == 3
    assert payload["sourcePaths"] == ["/tmp/lesson-records.jsonl"]
    assert payload["categoryClusters"][0]["category"] == "scope_control"
    assert payload["categoryClusters"][0]["count"] == 2
    assert payload["categoryClusters"][0]["topTheorems"][0] == {"value": "FATEM/1.lean", "count": 2}
    assert payload["theoremClusters"][0]["theoremId"] == "FATEM/1.lean"
    assert payload["theoremClusters"][0]["count"] == 2
    assert payload["actionClusters"][0]["action"] == "tighten_scope"
    assert payload["actionClusters"][0]["count"] == 2


def test_lesson_clusters_cli_discovers_campaign_inputs_and_writes_default_files(tmp_path: Path):
    campaign_root = tmp_path / "campaign"
    final_lessons = campaign_root / "reports" / "final" / "lessons"
    postmortem_lessons = campaign_root / "reports" / "postmortem" / "lessons"
    final_lessons.mkdir(parents=True)
    postmortem_lessons.mkdir(parents=True)

    (final_lessons / "lesson-records.jsonl").write_text(
        json.dumps(
            {
                "campaign_id": "camp",
                "run_id": "run-1",
                "theorem_id": "FATEM/1.lean",
                "category": "accepted_proof",
                "summary": "Accepted proof exported.",
                "action_taken": "export_final_proof",
                "accepted_state": "accepted",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (postmortem_lessons / "lesson-records.jsonl").write_text(
        json.dumps(
            {
                "campaign_id": "camp",
                "run_id": None,
                "theorem_id": None,
                "category": "provider_transport",
                "summary": "Provider transport cooled down.",
                "action_taken": "apply_provider_cooldown_and_archive",
                "accepted_state": "postmortem",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            "python3",
            str(ROOT / "scripts" / "lesson_clusters.py"),
            "--campaign-root",
            str(campaign_root),
            "--markdown",
            "--write-default-files",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "# Lesson Clusters" in result.stdout
    assert "accepted_proof" in result.stdout
    assert "provider_transport" in result.stdout
    assert (final_lessons / "lesson-clusters.json").exists()
    assert (final_lessons / "lesson-clusters.md").exists()
    assert (final_lessons / "lesson-reminders.json").exists()
    assert (final_lessons / "lesson-reminders.md").exists()
    assert (postmortem_lessons / "lesson-clusters.json").exists()
    assert (postmortem_lessons / "lesson-clusters.md").exists()
    assert (postmortem_lessons / "lesson-reminders.json").exists()
    assert (postmortem_lessons / "lesson-reminders.md").exists()
