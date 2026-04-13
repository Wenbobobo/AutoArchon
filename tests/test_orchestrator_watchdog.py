from __future__ import annotations

import io
import json
import runpy
import sys
import threading
from pathlib import Path

from archonlib.orchestrator_watchdog import (
    _stop_output_mirror,
    automatic_recovery_run_ids,
    build_default_orchestrator_prompt,
    build_watchdog_resume_prompt,
    campaign_has_live_work,
    campaign_progress_fingerprint,
    is_campaign_terminal,
    launch_codex_session,
    run_watchdog,
    select_automatic_recovery_run_ids,
    watchdog_campaign_snapshot,
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

    launch_state = campaign_root / "runs" / "teacher-001" / "control" / "teacher-launch-state.json"
    launch_state.parent.mkdir(parents=True, exist_ok=True)
    launch_state.write_text('{"active": true, "phase": "codex_exec"}', encoding="utf-8")
    lease = campaign_root / "runs" / "teacher-001" / "workspace" / ".archon" / "supervisor" / "run-lease.json"
    lease.parent.mkdir(parents=True, exist_ok=True)
    lease.write_text("{}", encoding="utf-8")

    fingerprint = campaign_progress_fingerprint(
        campaign_root,
        {
            "runs": [
                {
                    "runId": "teacher-001",
                    "runRoot": "runs/teacher-001",
                    "status": "queued",
                    "latestIteration": "iter-001",
                    "runningSignal": True,
                    "acceptedProofs": [],
                    "acceptedBlockers": [],
                },
                {
                    "runId": "teacher-002",
                    "runRoot": "runs/teacher-002",
                    "status": "running",
                    "latestIteration": None,
                    "runningSignal": False,
                    "acceptedProofs": ["FATEM/2.lean"],
                    "acceptedBlockers": [],
                },
            ]
        },
    )

    assert [item["runId"] for item in fingerprint["runFingerprints"]] == ["teacher-001", "teacher-002"]
    assert fingerprint["eventLines"] == 2
    assert fingerprint["runFingerprints"][0]["launchPhase"] == "codex_exec"
    assert fingerprint["runFingerprints"][0]["launchActive"] is True
    assert fingerprint["runFingerprints"][0]["latestActivityNs"] is not None
    assert fingerprint["runFingerprints"][1]["acceptedProofCount"] == 1


def test_build_default_orchestrator_prompt_mentions_core_control_plane_contract(tmp_path: Path):
    archon_root = tmp_path / "Archon"
    campaign_root = tmp_path / "campaign"

    prompt = build_default_orchestrator_prompt(
        archon_root=archon_root,
        campaign_root=campaign_root,
        max_active_launches=1,
        launch_batch_size=1,
    )

    assert "Use $archon-orchestrator to own this AutoArchon campaign." in prompt
    assert str(archon_root) in prompt
    assert str(campaign_root) in prompt
    assert "launch teachers only from runs/<id>/control/launch-teacher.sh" in prompt
    assert "autoarchon-campaign-recover" in prompt
    assert "--run-id <run-id> --execute" in prompt
    assert "prefer deterministic recovery via" not in prompt
    assert "outer orchestrator_watchdog.py process is your expected wrapper" in prompt
    assert "never use --all-recoverable --execute from the owner session" in prompt
    assert "launch or recover at most 1 run(s) per decision" in prompt
    assert "finalize only validated proofs and accepted blocker notes" in prompt
    assert "do not stop to ask the user about ownership" in prompt


def test_build_watchdog_resume_prompt_discourages_owner_questions():
    normal = build_watchdog_resume_prompt(stalled=False)
    stalled = build_watchdog_resume_prompt(stalled=True)

    assert "orchestrator_watchdog.py process is your wrapper" in normal
    assert "Do not stop to ask the user about ownership" in normal
    assert "If all runs are already terminal, finalize the campaign." in normal
    assert "after a stalled outer session" in stalled
    assert "orchestrator_watchdog.py process is your wrapper" in stalled
    assert "do not stop to ask the user about ownership" in stalled


def test_launch_codex_session_keeps_draining_owner_output_after_session_id(monkeypatch, tmp_path: Path):
    prompt_path = tmp_path / "prompt.txt"
    prompt_path.write_text("prompt\n", encoding="utf-8")
    log_path = tmp_path / "watchdog.log"

    class FakeProcess:
        def __init__(self) -> None:
            self.stdout = io.StringIO(
                "OpenAI Codex v0.118.0\n"
                "session id: 019d82a0-7485-7210-927c-0fd9110e27a3\n"
                "owner line 1\n"
                "owner line 2\n"
            )
            self.pid = 999

        def poll(self) -> int | None:
            return 0

    monkeypatch.setattr("archonlib.orchestrator_watchdog.subprocess.Popen", lambda *args, **kwargs: FakeProcess())

    launch = launch_codex_session(
        archon_root=tmp_path,
        prompt_path=prompt_path,
        log_path=log_path,
        model="gpt-5.4",
        reasoning_effort="xhigh",
    )
    _stop_output_mirror(launch)
    launch.log_handle.close()

    log_text = log_path.read_text(encoding="utf-8")
    assert launch.session_id == "019d82a0-7485-7210-927c-0fd9110e27a3"
    assert "owner line 1" in log_text
    assert "owner line 2" in log_text
    assert launch.output_state.last_output_at is not None


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


def test_automatic_recovery_run_ids_skips_runs_still_in_retry_backoff():
    payload = {
        "runs": [
            {
                "runId": "teacher-001",
                "status": "needs_relaunch",
                "recommendedRecovery": {"action": "relaunch_teacher"},
                "retryAfter": "2999-01-01T00:00:00+00:00",
            },
            {
                "runId": "teacher-002",
                "status": "unverified",
                "recommendedRecovery": {"action": "recovery_only"},
                "retryAfter": "2000-01-01T00:00:00+00:00",
            },
        ]
    }

    assert automatic_recovery_run_ids(payload) == ["teacher-002"]


def test_select_automatic_recovery_run_ids_respects_priority_and_launch_budget():
    payload = {
        "runs": [
            {
                "runId": "teacher-001",
                "recommendedRecovery": {"action": "relaunch_teacher"},
                "recoveryClass": "partial_progress_relaunch",
                "launchUpdatedAt": "2000-01-01T00:00:00+00:00",
            },
            {
                "runId": "teacher-002",
                "recommendedRecovery": {"action": "recovery_only"},
                "recoveryClass": "recovery_finalize",
            },
            {
                "runId": "teacher-003",
                "recommendedRecovery": {"action": "launch_teacher"},
                "recoveryClass": "queued_launch",
                "launchUpdatedAt": "2000-01-01T00:00:00+00:00",
            },
            {
                "runId": "teacher-004",
                "recommendedRecovery": {"action": "launch_teacher"},
                "recoveryClass": "launch_failed_retry",
                "launchActive": True,
            },
            {
                "runId": "teacher-005",
                "recommendedRecovery": {"action": "relaunch_teacher"},
                "recoveryClass": "rate_limited_backoff",
                "retryAfter": "2999-01-01T00:00:00+00:00",
                "launchUpdatedAt": "2000-01-01T00:00:00+00:00",
            },
        ]
    }

    assert select_automatic_recovery_run_ids(
        payload,
        max_active_launches=2,
        launch_batch_size=2,
        launch_cooldown_seconds=90,
    ) == ["teacher-002", "teacher-001"]


def test_select_automatic_recovery_run_ids_treats_live_running_work_as_budget_occupancy():
    payload = {
        "runs": [
            {
                "runId": "teacher-live",
                "status": "running",
                "runningSignal": True,
                "launchActive": False,
                "recommendedRecovery": {"action": "none"},
            },
            {
                "runId": "teacher-queued",
                "status": "queued",
                "recommendedRecovery": {"action": "launch_teacher"},
                "recoveryClass": "queued_launch",
                "launchUpdatedAt": "2000-01-01T00:00:00+00:00",
            },
        ]
    }

    assert select_automatic_recovery_run_ids(
        payload,
        max_active_launches=1,
        launch_batch_size=1,
        launch_cooldown_seconds=90,
    ) == []


def test_watchdog_campaign_snapshot_summarizes_status_and_prewarm_state():
    snapshot = watchdog_campaign_snapshot(
        {
            "counts": {"queued": 1, "running": 1, "accepted": 1},
            "runs": [
                {
                    "runId": "teacher-001",
                    "status": "queued",
                    "recommendedRecovery": {"action": "launch_teacher"},
                    "prewarmPlan": "scoped_verify",
                    "prewarmPending": True,
                    "acceptedProofs": [],
                    "acceptedBlockers": [],
                },
                {
                    "runId": "teacher-002",
                    "status": "running",
                    "recommendedRecovery": {"action": "none"},
                    "prewarmPlan": "reuse_build_outputs",
                    "prewarmPending": False,
                    "launchActive": True,
                    "launchPhase": "codex_exec",
                    "acceptedProofs": ["FATEM/2.lean"],
                    "acceptedBlockers": [],
                },
                {
                    "runId": "teacher-003",
                    "status": "accepted",
                    "recommendedRecovery": {"action": "none"},
                    "prewarmPlan": "full_build",
                    "prewarmPending": False,
                    "acceptedProofs": [],
                    "acceptedBlockers": ["FATEM/3.lean"],
                },
            ],
        }
    )

    assert snapshot["runCounts"] == {"queued": 1, "running": 1, "accepted": 1}
    assert snapshot["statusRunIds"] == {
        "accepted": ["teacher-003"],
        "queued": ["teacher-001"],
        "running": ["teacher-002"],
    }
    assert snapshot["recoverableRunIds"] == ["teacher-001"]
    assert snapshot["prewarmPlanCounts"] == {
        "full_build": 1,
        "reuse_build_outputs": 1,
        "scoped_verify": 1,
    }
    assert snapshot["prewarmPendingRunIds"] == ["teacher-001"]
    assert snapshot["activeLaunches"] == [{"runId": "teacher-002", "phase": "codex_exec"}]
    assert snapshot["acceptedProofCount"] == 1
    assert snapshot["acceptedBlockerCount"] == 1
    assert snapshot["terminal"] is False


def test_campaign_has_live_work_ignores_historical_progress_without_live_runs():
    assert (
        campaign_has_live_work(
            {
                "runs": [
                    {
                        "runId": "teacher-001",
                        "status": "needs_relaunch",
                        "runningSignal": False,
                        "launchActive": False,
                        "leaseActive": False,
                        "latestActivityAt": "2026-04-13T00:00:00+00:00",
                    }
                ]
            }
        )
        is False
    )
    assert (
        campaign_has_live_work(
            {
                "runs": [
                    {
                        "runId": "teacher-001",
                        "status": "needs_relaunch",
                        "runningSignal": False,
                        "launchActive": True,
                        "leaseActive": False,
                    }
                ]
            }
        )
        is True
    )


def test_orchestrator_watchdog_cli_writes_owner_mode_and_uses_control_paths(tmp_path: Path, monkeypatch):
    campaign_root = tmp_path / "campaign"
    campaign_root.mkdir(parents=True, exist_ok=True)
    captured: dict[str, object] = {}

    def fake_run_watchdog(**kwargs):
        captured.update(kwargs)
        return {"schemaVersion": 1, "terminal": False, "restartCount": 0}

    monkeypatch.setattr("archonlib.orchestrator_watchdog.run_watchdog", fake_run_watchdog)
    monkeypatch.setattr("archonlib.orchestrator_watchdog.build_default_orchestrator_prompt", lambda **_: "prompt text")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "orchestrator_watchdog.py",
            "--campaign-root",
            str(campaign_root),
        ],
    )

    try:
        runpy.run_path(
            "/home/daism/Wenbo/math/Archon/scripts/orchestrator_watchdog.py",
            run_name="__main__",
        )
    except SystemExit as exc:
        assert exc.code == 0

    owner_mode = json.loads((campaign_root / "control" / "owner-mode.json").read_text(encoding="utf-8"))
    assert owner_mode["ownerMode"] == "orchestrator"
    assert owner_mode["watchdogEnabled"] is True
    assert owner_mode["ownerEntrypoint"] == "autoarchon-orchestrator-watchdog"

    control_root = campaign_root / "control"
    assert captured["campaign_root"] == campaign_root
    assert captured["prompt_path"] == control_root / "orchestrator-prompt.txt"
    assert captured["state_path"] == control_root / "orchestrator-watchdog.json"
    assert captured["log_path"] == control_root / "orchestrator-watchdog.log"


def test_run_watchdog_bootstraps_recovery_once_and_persists_runtime_state(tmp_path: Path, monkeypatch):
    campaign_root = tmp_path / "campaign"
    prompt_path = tmp_path / "prompt.txt"
    state_path = tmp_path / "watchdog-state.json"
    log_path = tmp_path / "watchdog.log"
    recoveries: list[str] = []
    clock = {"now": 0.0}

    class FakeProcess:
        pid = 4242

        def __init__(self) -> None:
            self.stdout = io.StringIO("")

        def poll(self) -> None:
            return None

    statuses = iter(
        [
            {
                "runs": [
                    {"runId": "teacher-001", "status": "queued", "recommendedRecovery": {"action": "launch_teacher"}}
                ],
                "counts": {"queued": 1},
            },
            {
                "runs": [
                    {"runId": "teacher-001", "status": "queued", "recommendedRecovery": {"action": "launch_teacher"}}
                ],
                "counts": {"queued": 1},
            },
            {
                "runs": [
                    {"runId": "teacher-001", "status": "accepted", "recommendedRecovery": {"action": "none"}}
                ],
                "counts": {"accepted": 1},
            },
            {
                "runs": [
                    {"runId": "teacher-001", "status": "accepted", "recommendedRecovery": {"action": "none"}}
                ],
                "counts": {"accepted": 1},
            },
        ]
    )
    fingerprints = iter(
        [
            {"runFingerprints": [{"runId": "teacher-001", "latestActivityNs": None}], "eventLines": 1},
            {"runFingerprints": [{"runId": "teacher-001", "latestActivityNs": None}], "eventLines": 1},
            {"runFingerprints": [{"runId": "teacher-001", "latestActivityNs": 123}], "eventLines": 2},
        ]
    )

    def fake_launch_codex_session(**kwargs):
        log_handle = Path(kwargs["log_path"]).open("a", encoding="utf-8")
        output_state = type(
            "OutputState",
            (),
            {
                "session_id": "session-123",
                "last_output_at": "2026-01-01T00:00:00+00:00",
                "last_output_monotonic": clock["now"],
                "lock": threading.Lock(),
            },
        )()
        output_thread = type(
            "OutputThread",
            (),
            {
                "is_alive": staticmethod(lambda: False),
                "join": staticmethod(lambda timeout=None: None),
            },
        )()
        return type(
            "FakeLaunch",
            (),
            {
                "process": FakeProcess(),
                "log_handle": log_handle,
                "session_id": "session-123",
                "mode": "start",
                "command": ["codex", "exec"],
                "output_state": output_state,
                "output_thread": output_thread,
            },
        )()

    def fake_collect_campaign_status(_campaign_root: Path):
        return next(statuses)

    def fake_campaign_progress_fingerprint(_campaign_root: Path, _status_payload: dict):
        return next(fingerprints)

    def fake_execute_run_recovery(_campaign_root: Path, run_id: str, execute: bool = True):
        assert execute is True
        recoveries.append(run_id)
        return {"runId": run_id, "executed": execute}

    def fake_finalize_campaign(_campaign_root: Path):
        final_root = _campaign_root / "reports" / "final"
        final_root.mkdir(parents=True, exist_ok=True)
        (final_root / "final-summary.json").write_text("{}", encoding="utf-8")

    def fake_compare_report(_campaign_root: Path, *, heartbeat_seconds: int):
        final_root = _campaign_root / "reports" / "final"
        final_root.mkdir(parents=True, exist_ok=True)
        (final_root / "compare-report.json").write_text("{}", encoding="utf-8")
        return {"generatedAt": "2026-01-01T00:00:00+00:00"}

    monkeypatch.setattr("archonlib.orchestrator_watchdog.launch_codex_session", fake_launch_codex_session)
    monkeypatch.setattr("archonlib.orchestrator_watchdog.collect_campaign_status", fake_collect_campaign_status)
    monkeypatch.setattr("archonlib.orchestrator_watchdog.campaign_progress_fingerprint", fake_campaign_progress_fingerprint)
    monkeypatch.setattr("archonlib.orchestrator_watchdog.execute_run_recovery", fake_execute_run_recovery)
    monkeypatch.setattr("archonlib.orchestrator_watchdog.finalize_campaign", fake_finalize_campaign)
    monkeypatch.setattr("archonlib.orchestrator_watchdog.build_campaign_compare_report", fake_compare_report)
    monkeypatch.setattr("archonlib.orchestrator_watchdog.terminate_process_group", lambda *args, **kwargs: None)
    monkeypatch.setattr("archonlib.orchestrator_watchdog.time.sleep", lambda seconds: clock.__setitem__("now", clock["now"] + seconds))
    monkeypatch.setattr("archonlib.orchestrator_watchdog.time.monotonic", lambda: clock["now"])

    result = run_watchdog(
        archon_root=tmp_path,
        campaign_root=campaign_root,
        prompt_path=prompt_path,
        state_path=state_path,
        log_path=log_path,
        model="gpt-5.4",
        reasoning_effort="xhigh",
        poll_seconds=5,
        stall_seconds=60,
        bootstrap_launch_after_seconds=5,
        max_restarts=1,
        finalize_on_terminal=True,
    )

    assert result["terminal"] is True
    assert result["finalized"] is True
    assert result["restartCount"] == 0
    assert result["runCounts"] == {"accepted": 1}
    assert result["statusRunIds"] == {"accepted": ["teacher-001"]}
    assert result["recoverableRunIds"] == []
    assert result["prewarmPlanCounts"] == {}
    assert result["prewarmPendingRunIds"] == []
    assert result["activeLaunches"] == []
    assert result["lastRecoveryAt"] is not None
    assert recoveries == ["teacher-001"]

    state_payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert state_payload["sessionId"] == "session-123"
    assert state_payload["runCounts"] == {"accepted": 1}
    assert state_payload["statusRunIds"] == {"accepted": ["teacher-001"]}
    assert state_payload["recoverableRunIds"] == []
    assert state_payload["campaignSnapshot"]["terminal"] is True
    assert state_payload["lastRecoveryAt"] is not None
    assert state_payload["stallReason"] is None
    assert state_payload["watchdogStatus"] == "terminal"


def test_run_watchdog_bootstraps_recovery_despite_historical_activity(tmp_path: Path, monkeypatch):
    campaign_root = tmp_path / "campaign"
    prompt_path = tmp_path / "prompt.txt"
    state_path = tmp_path / "watchdog-state.json"
    log_path = tmp_path / "watchdog.log"
    recoveries: list[str] = []
    clock = {"now": 0.0}

    class FakeProcess:
        pid = 5252

        def __init__(self) -> None:
            self.stdout = io.StringIO("")

        def poll(self) -> None:
            return None

    statuses = iter(
        [
            {
                "runs": [
                    {
                        "runId": "teacher-001",
                        "status": "needs_relaunch",
                        "runningSignal": False,
                        "launchActive": False,
                        "recommendedRecovery": {"action": "relaunch_teacher"},
                    }
                ],
                "counts": {"needs_relaunch": 1},
            },
            {
                "runs": [
                    {
                        "runId": "teacher-001",
                        "status": "needs_relaunch",
                        "runningSignal": False,
                        "launchActive": False,
                        "recommendedRecovery": {"action": "relaunch_teacher"},
                    }
                ],
                "counts": {"needs_relaunch": 1},
            },
            {
                "runs": [
                    {"runId": "teacher-001", "status": "accepted", "recommendedRecovery": {"action": "none"}}
                ],
                "counts": {"accepted": 1},
            },
            {
                "runs": [
                    {"runId": "teacher-001", "status": "accepted", "recommendedRecovery": {"action": "none"}}
                ],
                "counts": {"accepted": 1},
            },
        ]
    )
    fingerprints = iter(
        [
            {"runFingerprints": [{"runId": "teacher-001", "latestActivityNs": 123}], "eventLines": 1},
            {"runFingerprints": [{"runId": "teacher-001", "latestActivityNs": 123}], "eventLines": 1},
            {"runFingerprints": [{"runId": "teacher-001", "latestActivityNs": 456}], "eventLines": 2},
        ]
    )

    def fake_launch_codex_session(**kwargs):
        log_handle = Path(kwargs["log_path"]).open("a", encoding="utf-8")
        output_state = type(
            "OutputState",
            (),
            {
                "session_id": "session-789",
                "last_output_at": "2026-01-01T00:00:00+00:00",
                "last_output_monotonic": clock["now"],
                "lock": threading.Lock(),
            },
        )()
        output_thread = type(
            "OutputThread",
            (),
            {
                "is_alive": staticmethod(lambda: False),
                "join": staticmethod(lambda timeout=None: None),
            },
        )()
        return type(
            "FakeLaunch",
            (),
            {
                "process": FakeProcess(),
                "log_handle": log_handle,
                "session_id": "session-789",
                "mode": "start",
                "command": ["codex", "exec"],
                "output_state": output_state,
                "output_thread": output_thread,
            },
        )()

    def fake_collect_campaign_status(_campaign_root: Path):
        return next(statuses)

    def fake_campaign_progress_fingerprint(_campaign_root: Path, _status_payload: dict):
        return next(fingerprints)

    def fake_execute_run_recovery(_campaign_root: Path, run_id: str, execute: bool = True):
        assert execute is True
        recoveries.append(run_id)
        return {"runId": run_id, "executed": execute}

    def fake_finalize_campaign(_campaign_root: Path):
        final_root = _campaign_root / "reports" / "final"
        final_root.mkdir(parents=True, exist_ok=True)
        (final_root / "final-summary.json").write_text("{}", encoding="utf-8")

    def fake_compare_report(_campaign_root: Path, *, heartbeat_seconds: int):
        final_root = _campaign_root / "reports" / "final"
        final_root.mkdir(parents=True, exist_ok=True)
        (final_root / "compare-report.json").write_text("{}", encoding="utf-8")
        return {"generatedAt": "2026-01-01T00:00:00+00:00"}

    monkeypatch.setattr("archonlib.orchestrator_watchdog.launch_codex_session", fake_launch_codex_session)
    monkeypatch.setattr("archonlib.orchestrator_watchdog.collect_campaign_status", fake_collect_campaign_status)
    monkeypatch.setattr("archonlib.orchestrator_watchdog.campaign_progress_fingerprint", fake_campaign_progress_fingerprint)
    monkeypatch.setattr("archonlib.orchestrator_watchdog.execute_run_recovery", fake_execute_run_recovery)
    monkeypatch.setattr("archonlib.orchestrator_watchdog.finalize_campaign", fake_finalize_campaign)
    monkeypatch.setattr("archonlib.orchestrator_watchdog.build_campaign_compare_report", fake_compare_report)
    monkeypatch.setattr("archonlib.orchestrator_watchdog.terminate_process_group", lambda *args, **kwargs: None)
    monkeypatch.setattr("archonlib.orchestrator_watchdog.time.sleep", lambda seconds: clock.__setitem__("now", clock["now"] + seconds))
    monkeypatch.setattr("archonlib.orchestrator_watchdog.time.monotonic", lambda: clock["now"])

    result = run_watchdog(
        archon_root=tmp_path,
        campaign_root=campaign_root,
        prompt_path=prompt_path,
        state_path=state_path,
        log_path=log_path,
        model="gpt-5.4",
        reasoning_effort="xhigh",
        poll_seconds=5,
        stall_seconds=60,
        bootstrap_launch_after_seconds=5,
        max_restarts=1,
        finalize_on_terminal=True,
    )

    assert result["terminal"] is True
    assert recoveries == ["teacher-001"]
    state_payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert state_payload["lastRecoveryAt"] is not None
    assert state_payload["watchdogStatus"] == "terminal"


def test_run_watchdog_degrades_instead_of_throwing_when_restart_budget_is_exhausted(tmp_path: Path, monkeypatch):
    campaign_root = tmp_path / "campaign"
    prompt_path = tmp_path / "prompt.txt"
    state_path = tmp_path / "watchdog-state.json"
    log_path = tmp_path / "watchdog.log"
    clock = {"now": 0.0}

    class FakeProcess:
        pid = 7331

        def __init__(self) -> None:
            self.stdout = io.StringIO("")

        def poll(self) -> int | None:
            return 1

    def fake_launch_codex_session(**kwargs):
        log_handle = Path(kwargs["log_path"]).open("a", encoding="utf-8")
        output_state = type(
            "OutputState",
            (),
            {
                "session_id": "session-456",
                "last_output_at": None,
                "last_output_monotonic": None,
                "lock": threading.Lock(),
            },
        )()
        output_thread = type(
            "OutputThread",
            (),
            {
                "is_alive": staticmethod(lambda: False),
                "join": staticmethod(lambda timeout=None: None),
            },
        )()
        return type(
            "FakeLaunch",
            (),
            {
                "process": FakeProcess(),
                "log_handle": log_handle,
                "session_id": "session-456",
                "mode": "start",
                "command": ["codex", "exec"],
                "output_state": output_state,
                "output_thread": output_thread,
            },
        )()

    def fake_collect_campaign_status(_campaign_root: Path):
        return {
            "campaignId": "budget-test",
            "runs": [{"runId": "teacher-001", "status": "needs_relaunch", "recommendedRecovery": {"action": "relaunch_teacher"}}],
            "counts": {"needs_relaunch": 1},
        }

    def fake_campaign_progress_fingerprint(_campaign_root: Path, _status_payload: dict):
        return {"runFingerprints": [{"runId": "teacher-001", "latestActivityNs": None}], "eventLines": 1}

    def fake_compare_report(_campaign_root: Path, *, heartbeat_seconds: int):
        final_root = _campaign_root / "reports" / "final"
        final_root.mkdir(parents=True, exist_ok=True)
        (final_root / "compare-report.json").write_text("{}", encoding="utf-8")
        return {"generatedAt": "2026-01-01T00:00:00+00:00"}

    monkeypatch.setattr("archonlib.orchestrator_watchdog.launch_codex_session", fake_launch_codex_session)
    monkeypatch.setattr("archonlib.orchestrator_watchdog.collect_campaign_status", fake_collect_campaign_status)
    monkeypatch.setattr("archonlib.orchestrator_watchdog.campaign_progress_fingerprint", fake_campaign_progress_fingerprint)
    monkeypatch.setattr("archonlib.orchestrator_watchdog.build_campaign_compare_report", fake_compare_report)
    monkeypatch.setattr("archonlib.orchestrator_watchdog.terminate_process_group", lambda *args, **kwargs: None)
    monkeypatch.setattr("archonlib.orchestrator_watchdog.time.sleep", lambda seconds: clock.__setitem__("now", clock["now"] + seconds))
    monkeypatch.setattr("archonlib.orchestrator_watchdog.time.monotonic", lambda: clock["now"])

    result = run_watchdog(
        archon_root=tmp_path,
        campaign_root=campaign_root,
        prompt_path=prompt_path,
        state_path=state_path,
        log_path=log_path,
        model="gpt-5.4",
        reasoning_effort="xhigh",
        poll_seconds=5,
        stall_seconds=60,
        max_restarts=0,
        finalize_on_terminal=False,
    )

    assert result["watchdogStatus"] == "degraded"
    assert result["budgetExhausted"] is True
    assert result["terminal"] is False
    assert result["stallReason"] == "owner_exit"
    state_payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert state_payload["watchdogStatus"] == "degraded"
    assert state_payload["budgetExhausted"] is True
