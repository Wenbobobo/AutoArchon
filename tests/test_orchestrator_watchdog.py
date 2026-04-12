from __future__ import annotations

from pathlib import Path

from archonlib.orchestrator_watchdog import (
    automatic_recovery_run_ids,
    build_default_orchestrator_prompt,
    campaign_progress_fingerprint,
    is_campaign_terminal,
)


def test_is_campaign_terminal_requires_all_runs_to_be_terminal():
    assert is_campaign_terminal({"runs": [{"status": "accepted"}, {"status": "blocked"}]}) is True
    assert is_campaign_terminal({"runs": [{"status": "accepted"}, {"status": "running"}]}) is False
    assert is_campaign_terminal({"runs": []}) is False


def test_campaign_progress_fingerprint_tracks_status_events_and_activity(tmp_path: Path):
    campaign_root = tmp_path / "campaign"
    events_path = campaign_root / "events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.write_text('{"event":"campaign_created"}\n{"event":"run_created"}\n', encoding="utf-8")

    lease = campaign_root / "runs" / "teacher-001" / "workspace" / ".archon" / "supervisor" / "run-lease.json"
    lease.parent.mkdir(parents=True, exist_ok=True)
    lease.write_text("{}", encoding="utf-8")

    fingerprint = campaign_progress_fingerprint(
        campaign_root,
        {
            "runs": [
                {"runId": "teacher-001", "status": "queued"},
                {"runId": "teacher-002", "status": "running"},
            ]
        },
    )

    assert fingerprint["runStatusPairs"] == [("teacher-001", "queued"), ("teacher-002", "running")]
    assert fingerprint["eventLines"] == 2
    assert fingerprint["activityFiles"] == 1


def test_build_default_orchestrator_prompt_mentions_core_control_plane_contract(tmp_path: Path):
    archon_root = tmp_path / "Archon"
    campaign_root = tmp_path / "campaign"

    prompt = build_default_orchestrator_prompt(archon_root=archon_root, campaign_root=campaign_root)

    assert "Use $archon-orchestrator to own this AutoArchon campaign." in prompt
    assert str(archon_root) in prompt
    assert str(campaign_root) in prompt
    assert "launch teachers only from runs/<id>/control/launch-teacher.sh" in prompt
    assert "autoarchon-campaign-recover" in prompt
    assert "finalize only validated proofs and accepted blocker notes" in prompt


def test_automatic_recovery_run_ids_collects_only_deterministic_recoveries():
    payload = {
        "runs": [
            {"runId": "teacher-001", "status": "queued", "recommendedRecovery": {"action": "launch_teacher"}},
            {"runId": "teacher-002", "status": "needs_relaunch", "recommendedRecovery": {"action": "relaunch_teacher"}},
            {"runId": "teacher-003", "status": "unverified", "recommendedRecovery": {"action": "recovery_only"}},
            {"runId": "teacher-004", "status": "running", "recommendedRecovery": {"action": "none"}},
            {"runId": "teacher-005", "status": "contaminated", "recommendedRecovery": {"action": "manual_rebuild"}},
        ]
    }
    assert automatic_recovery_run_ids(payload) == ["teacher-001", "teacher-002", "teacher-003"]
    assert automatic_recovery_run_ids(
        {
            "runs": [
                {"runId": "teacher-001", "status": "running", "recommendedRecovery": {"action": "none"}},
                {"runId": "teacher-002", "status": "accepted", "recommendedRecovery": {"action": "none"}},
            ]
        }
    ) == []
    assert automatic_recovery_run_ids(
        {"runs": [{"runId": "teacher-001", "status": "queued", "recommendedRecovery": {"action": "launch_teacher"}}]}
    ) == ["teacher-001"]
