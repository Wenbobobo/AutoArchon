from __future__ import annotations

import json
import os
import subprocess
import textwrap
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from archonlib.campaign import (
    _append_campaign_status_events,
    archive_campaign_postmortem,
    build_orchestrator_prompt,
    build_campaign_overview,
    campaign_is_terminal,
    claim_owner_lease,
    cleanup_stale_launch_processes,
    collect_campaign_status,
    create_campaign,
    ensure_campaign_control_root,
    execute_run_recovery,
    finalize_campaign,
    owner_lease_is_live,
    plan_campaign_shards,
    refresh_campaign_launch_assets,
    refresh_owner_lease,
    release_owner_lease,
)


ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN_RECOVER = ROOT / "scripts" / "campaign_recover.py"
CAMPAIGN_COMPARE = ROOT / "scripts" / "campaign_compare.py"
CAMPAIGN_ARCHIVE = ROOT / "scripts" / "campaign_archive.py"
CAMPAIGN_OVERVIEW = ROOT / "scripts" / "campaign_overview.py"
LAUNCH_FROM_SPEC = ROOT / "scripts" / "launch_from_spec.py"
PLAN_CAMPAIGN_SHARDS = ROOT / "scripts" / "plan_campaign_shards.py"
RUN_ORCHESTRATOR = ROOT / "scripts" / "run_orchestrator.py"
REFRESH_LAUNCH_ASSETS = ROOT / "scripts" / "refresh_launch_assets.py"


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")


def make_source_project(tmp_path: Path, *, file_count: int = 6) -> Path:
    source = tmp_path / "source-project"
    write(source / "lakefile.lean", "import Lake\n")
    write(source / "lean-toolchain", "leanprover/lean4:v4.28.0\n")
    for index in range(1, file_count + 1):
        write(source / "FATEM" / f"{index}.lean", f"theorem file_{index} : True := by\n  sorry\n")
    return source


def test_plan_campaign_shards_generates_stable_single_file_specs(tmp_path: Path):
    source = make_source_project(tmp_path, file_count=4)

    payload = plan_campaign_shards(
        source,
        run_id_prefix="teacher",
        include_regex=r"^FATEM/[1-3]\.lean$",
        shard_size=1,
        start_index=7,
    )

    assert payload == [
        {
            "allowed_files": ["FATEM/1.lean"],
            "id": "teacher-007",
            "objective_limit": 1,
            "objective_regex": "^(FATEM/1\\.lean)$",
            "scope_hint": "FATEM/1.lean",
        },
        {
            "allowed_files": ["FATEM/2.lean"],
            "id": "teacher-008",
            "objective_limit": 1,
            "objective_regex": "^(FATEM/2\\.lean)$",
            "scope_hint": "FATEM/2.lean",
        },
        {
            "allowed_files": ["FATEM/3.lean"],
            "id": "teacher-009",
            "objective_limit": 1,
            "objective_regex": "^(FATEM/3\\.lean)$",
            "scope_hint": "FATEM/3.lean",
        },
    ]


def test_plan_campaign_shards_supports_file_stem_run_ids_for_single_file_shards(tmp_path: Path):
    source = make_source_project(tmp_path, file_count=4)

    payload = plan_campaign_shards(
        source,
        run_id_prefix="teacher",
        run_id_mode="file_stem",
        include_regex=r"^FATEM/[1-3]\.lean$",
        shard_size=1,
    )

    assert payload == [
        {
            "allowed_files": ["FATEM/1.lean"],
            "id": "teacher-1",
            "objective_limit": 1,
            "objective_regex": "^(FATEM/1\\.lean)$",
            "scope_hint": "FATEM/1.lean",
        },
        {
            "allowed_files": ["FATEM/2.lean"],
            "id": "teacher-2",
            "objective_limit": 1,
            "objective_regex": "^(FATEM/2\\.lean)$",
            "scope_hint": "FATEM/2.lean",
        },
        {
            "allowed_files": ["FATEM/3.lean"],
            "id": "teacher-3",
            "objective_limit": 1,
            "objective_regex": "^(FATEM/3\\.lean)$",
            "scope_hint": "FATEM/3.lean",
        },
    ]


def test_refresh_campaign_launch_assets_regenerates_launch_script_without_overwriting_prompt_by_default(tmp_path: Path):
    source = make_source_project(tmp_path, file_count=1)
    campaign_root = tmp_path / "campaign"
    create_campaign(
        archon_root=ROOT,
        source_root=source,
        campaign_root=campaign_root,
        run_specs=[
            {"id": "teacher-001", "objective_regex": "^FATEM/1\\.lean$", "objective_limit": 1, "scope_hint": "FATEM/1.lean"},
        ],
    )

    control_root = campaign_root / "runs" / "teacher-001" / "control"
    launch_path = control_root / "launch-teacher.sh"
    prompt_path = control_root / "teacher-prompt.txt"
    write(launch_path, "#!/usr/bin/env bash\necho legacy\n")
    write(prompt_path, "custom prompt\n")

    payload = refresh_campaign_launch_assets(campaign_root)

    refreshed_launch = launch_path.read_text(encoding="utf-8")
    refreshed_prompt = prompt_path.read_text(encoding="utf-8")

    assert payload["refreshedRuns"] == [
        {
            "runId": "teacher-001",
            "launchScript": "runs/teacher-001/control/launch-teacher.sh",
            "promptRefreshed": False,
        }
    ]
    assert 'write_launch_state "bootstrap" "true" "" "$$"' in refreshed_launch
    assert "custom prompt\n" == refreshed_prompt


def test_refresh_launch_assets_cli_can_refresh_prompt_when_requested(tmp_path: Path):
    source = make_source_project(tmp_path, file_count=1)
    campaign_root = tmp_path / "campaign"
    create_campaign(
        archon_root=ROOT,
        source_root=source,
        campaign_root=campaign_root,
        run_specs=[
            {"id": "teacher-001", "objective_regex": "^FATEM/1\\.lean$", "objective_limit": 1, "scope_hint": "FATEM/1.lean"},
        ],
    )

    control_root = campaign_root / "runs" / "teacher-001" / "control"
    prompt_path = control_root / "teacher-prompt.txt"
    write(prompt_path, "custom prompt\n")

    result = subprocess.run(
        [
            "python3",
            str(REFRESH_LAUNCH_ASSETS),
            "--campaign-root",
            str(campaign_root),
            "--run-id",
            "teacher-001",
            "--refresh-prompts",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["refreshPrompts"] is True
    assert payload["refreshedRuns"][0]["runId"] == "teacher-001"
    refreshed_prompt = prompt_path.read_text(encoding="utf-8")
    assert "Use $archon-supervisor to supervise this AutoArchon run." in refreshed_prompt


def test_cleanup_stale_launch_processes_selects_only_older_duplicate_launcher(tmp_path: Path, monkeypatch):
    source = make_source_project(tmp_path, file_count=1)
    campaign_root = tmp_path / "campaign"
    create_campaign(
        archon_root=ROOT,
        source_root=source,
        campaign_root=campaign_root,
        run_specs=[
            {"id": "teacher-001", "objective_regex": "^FATEM/1\\.lean$", "objective_limit": 1, "scope_hint": "FATEM/1.lean"},
        ],
    )
    run_root = campaign_root / "runs" / "teacher-001"
    control_root = run_root / "control"
    workspace = run_root / "workspace"
    write(workspace / ".archon" / "RUN_SCOPE.md", run_scope_markdown("FATEM/1.lean"))
    write(
        control_root / "teacher-launch-state.json",
        json.dumps(
            {
                "schemaVersion": 1,
                "active": True,
                "phase": "codex_exec",
                "updatedAt": "2026-04-13T09:00:00+00:00",
            },
            indent=2,
        ),
    )
    write(
        workspace / ".archon" / "supervisor" / "run-lease.json",
        json.dumps(
            {
                "schemaVersion": 1,
                "active": True,
                "status": "running",
                "startedAt": "2026-04-13T09:00:05+00:00",
                "updatedAt": "2026-04-13T09:00:06+00:00",
                "lastHeartbeatAt": "2026-04-13T09:00:06+00:00",
            },
            indent=2,
        ),
    )

    monkeypatch.setattr(
        "archonlib.campaign._live_launch_process_records",
        lambda _campaign_root: [
            {
                "runId": "teacher-001",
                "pid": 1001,
                "pgid": 1001,
                "elapsedSeconds": 3600,
                "command": "bash ...teacher-001/control/launch-teacher.sh",
            },
            {
                "runId": "teacher-001",
                "pid": 2002,
                "pgid": 2002,
                "elapsedSeconds": 60,
                "command": "bash ...teacher-001/control/launch-teacher.sh",
            },
        ],
    )

    payload = cleanup_stale_launch_processes(
        campaign_root,
        heartbeat_seconds=900,
        duplicate_grace_seconds=30,
        execute=False,
    )

    assert payload["candidateCount"] == 1
    assert payload["candidates"][0]["pid"] == 1001
    assert payload["candidates"][0]["reason"] == "older_duplicate_launcher"


def test_cleanup_stale_launch_processes_can_execute_terminal_orphan_cleanup(tmp_path: Path, monkeypatch):
    source = make_source_project(tmp_path, file_count=1)
    campaign_root = tmp_path / "campaign"
    create_campaign(
        archon_root=ROOT,
        source_root=source,
        campaign_root=campaign_root,
        run_specs=[
            {"id": "teacher-001", "objective_regex": "^FATEM/1\\.lean$", "objective_limit": 1, "scope_hint": "FATEM/1.lean"},
        ],
    )
    run_root = campaign_root / "runs" / "teacher-001"
    control_root = run_root / "control"
    workspace = run_root / "workspace"
    now_iso = datetime.now(timezone.utc).isoformat()
    write(workspace / ".archon" / "RUN_SCOPE.md", run_scope_markdown("FATEM/1.lean"))
    write(
        control_root / "teacher-launch-state.json",
        json.dumps(
            {
                "schemaVersion": 1,
                "active": False,
                "phase": "failed",
                "updatedAt": now_iso,
            },
            indent=2,
        ),
    )
    write(
        workspace / ".archon" / "supervisor" / "run-lease.json",
        json.dumps(
            {
                "schemaVersion": 1,
                "active": False,
                "status": "completed",
                "finalStatus": "no_progress",
                "updatedAt": now_iso,
                "completedAt": now_iso,
                "lastHeartbeatAt": now_iso,
            },
            indent=2,
        ),
    )

    monkeypatch.setattr(
        "archonlib.campaign._live_launch_process_records",
        lambda _campaign_root: [
            {
                "runId": "teacher-001",
                "pid": 3003,
                "pgid": 3003,
                "elapsedSeconds": 120,
                "command": "bash ...teacher-001/control/launch-teacher.sh",
            }
        ],
    )
    killed: list[int] = []
    monkeypatch.setattr("archonlib.campaign.os.killpg", lambda pgid, sig: killed.append(pgid))

    payload = cleanup_stale_launch_processes(
        campaign_root,
        heartbeat_seconds=900,
        duplicate_grace_seconds=30,
        execute=True,
    )

    assert payload["candidateCount"] == 1
    assert payload["executed"][0]["pgid"] == 3003
    assert payload["executed"][0]["killed"] is True
    assert killed == [3003]


def test_cleanup_stale_launch_processes_marks_terminal_stale_launch_state_inactive(tmp_path: Path, monkeypatch):
    source = make_source_project(tmp_path, file_count=1)
    campaign_root = tmp_path / "campaign"
    create_campaign(
        archon_root=ROOT,
        source_root=source,
        campaign_root=campaign_root,
        run_specs=[
            {"id": "teacher-001", "objective_regex": "^FATEM/1\\.lean$", "objective_limit": 1, "scope_hint": "FATEM/1.lean"},
        ],
    )
    run_root = campaign_root / "runs" / "teacher-001"
    control_root = run_root / "control"
    workspace = run_root / "workspace"
    write(workspace / ".archon" / "RUN_SCOPE.md", run_scope_markdown("FATEM/1.lean"))
    write(
        control_root / "teacher-launch-state.json",
        json.dumps(
            {
                "schemaVersion": 1,
                "active": True,
                "phase": "codex_exec",
                "updatedAt": "2000-01-01T00:00:00+00:00",
                "pid": 3003,
            },
            indent=2,
        ),
    )
    now_iso = datetime.now(timezone.utc).isoformat()
    write(
        workspace / ".archon" / "supervisor" / "run-lease.json",
        json.dumps(
            {
                "schemaVersion": 1,
                "active": False,
                "status": "completed",
                "finalStatus": "clean",
                "updatedAt": now_iso,
                "completedAt": now_iso,
                "lastHeartbeatAt": now_iso,
            },
            indent=2,
        ),
    )

    monkeypatch.setattr(
        "archonlib.campaign._live_launch_process_records",
        lambda _campaign_root: [
            {
                "runId": "teacher-001",
                "pid": 3003,
                "pgid": 3003,
                "elapsedSeconds": 120,
                "command": "bash ...teacher-001/control/launch-teacher.sh",
            }
        ],
    )
    killed: list[int] = []
    monkeypatch.setattr("archonlib.campaign.os.killpg", lambda pgid, sig: killed.append(pgid))

    payload = cleanup_stale_launch_processes(
        campaign_root,
        heartbeat_seconds=900,
        duplicate_grace_seconds=30,
        execute=True,
    )

    assert payload["candidateCount"] == 1
    assert payload["executed"][0]["reason"] == "stale_after_terminal_lease"
    assert killed == [3003]
    launch_state = json.loads((control_root / "teacher-launch-state.json").read_text(encoding="utf-8"))
    assert launch_state["active"] is False
    assert launch_state["phase"] == "cleanup_terminated"
    assert launch_state["launcher"] == "cleanup_stale_launch_processes"


def test_plan_campaign_shards_cli_writes_grouped_specs(tmp_path: Path):
    source = make_source_project(tmp_path, file_count=5)
    output_path = tmp_path / "run-specs.json"

    result = subprocess.run(
        [
            "python3",
            str(PLAN_CAMPAIGN_SHARDS),
            "--source-root",
            str(source),
            "--match-regex",
            r"^FATEM/[1-4]\.lean$",
            "--shard-size",
            "2",
            "--run-id-prefix",
            "micro",
            "--output",
            str(output_path),
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload == json.loads(output_path.read_text(encoding="utf-8"))
    assert payload == [
        {
            "allowed_files": ["FATEM/1.lean", "FATEM/2.lean"],
            "id": "micro-001",
            "objective_limit": 2,
            "objective_regex": "^(FATEM/1\\.lean|FATEM/2\\.lean)$",
            "scope_hint": "FATEM/1.lean, FATEM/2.lean",
        },
        {
            "allowed_files": ["FATEM/3.lean", "FATEM/4.lean"],
            "id": "micro-002",
            "objective_limit": 2,
            "objective_regex": "^(FATEM/3\\.lean|FATEM/4\\.lean)$",
            "scope_hint": "FATEM/3.lean, FATEM/4.lean",
        },
    ]


def test_campaign_is_terminal_only_when_all_runs_are_terminal():
    assert campaign_is_terminal({"runs": [{"status": "accepted"}, {"status": "blocked"}]}) is True
    assert campaign_is_terminal({"runs": [{"status": "accepted"}, {"status": "contaminated"}]}) is True
    assert campaign_is_terminal({"runs": [{"status": "accepted"}, {"status": "running"}]}) is False
    assert campaign_is_terminal({"runs": [{"status": "queued"}]}) is False


def test_build_orchestrator_prompt_includes_control_plane_contract(tmp_path: Path):
    archon_root = tmp_path / "Archon"
    campaign_root = tmp_path / "campaign"

    prompt = build_orchestrator_prompt(
        archon_root=archon_root,
        campaign_root=campaign_root,
    )

    assert "Use $archon-orchestrator to own this AutoArchon campaign." in prompt
    assert f"Repository root: {archon_root}" in prompt
    assert f"Campaign root: {campaign_root}" in prompt
    assert "launch teachers only from runs/<id>/control/launch-teacher.sh" in prompt
    assert "autoarchon-campaign-recover" in prompt
    assert "reports/final/" in prompt


def test_run_orchestrator_cli_updates_owner_mode_and_attempt_index(tmp_path: Path):
    source = make_source_project(tmp_path, file_count=1)
    campaign_root = tmp_path / "campaign"
    create_campaign(
        archon_root=ROOT,
        source_root=source,
        campaign_root=campaign_root,
        run_specs=[
            {"id": "queued-run", "objective_regex": "^FATEM/1\\.lean$", "objective_limit": 1, "scope_hint": "FATEM/1.lean"},
        ],
    )
    fake_bin = make_fake_uv_for_orchestrator(tmp_path)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["FAKE_CAMPAIGN_ROOT"] = str(campaign_root)

    result = subprocess.run(
        [
            "python3",
            str(RUN_ORCHESTRATOR),
            "--campaign-root",
            str(campaign_root),
            "--max-attempts",
            "1",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0

    owner_mode = json.loads((campaign_root / "control" / "owner-mode.json").read_text(encoding="utf-8"))
    assert owner_mode["ownerMode"] == "orchestrator"
    assert owner_mode["watchdogEnabled"] is False
    assert owner_mode["ownerEntrypoint"] == "autoarchon-run-orchestrator"

    attempt_index = (campaign_root / "control" / "orchestrator-attempts" / "attempt-index.jsonl").read_text(
        encoding="utf-8"
    )
    attempt_payload = json.loads(attempt_index.strip())
    assert attempt_payload["attempt"] == 1
    assert attempt_payload["returncode"] == 0

    status = json.loads((campaign_root / "campaign-status.json").read_text(encoding="utf-8"))
    assert status["runs"][0]["status"] == "blocked"

    event_names = [json.loads(line)["event"] for line in (campaign_root / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert "blocker_accepted" in event_names
    assert "campaign_status_refreshed" in event_names


def test_plan_campaign_shards_cli_supports_file_stem_ids(tmp_path: Path):
    source = make_source_project(tmp_path, file_count=3)

    result = subprocess.run(
        [
            "python3",
            str(PLAN_CAMPAIGN_SHARDS),
            "--source-root",
            str(source),
            "--match-regex",
            r"^FATEM/[1-3]\.lean$",
            "--shard-size",
            "1",
            "--run-id-prefix",
            "teacher",
            "--run-id-mode",
            "file_stem",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert json.loads(result.stdout) == [
        {
            "allowed_files": ["FATEM/1.lean"],
            "id": "teacher-1",
            "objective_limit": 1,
            "objective_regex": "^(FATEM/1\\.lean)$",
            "scope_hint": "FATEM/1.lean",
        },
        {
            "allowed_files": ["FATEM/2.lean"],
            "id": "teacher-2",
            "objective_limit": 1,
            "objective_regex": "^(FATEM/2\\.lean)$",
            "scope_hint": "FATEM/2.lean",
        },
        {
            "allowed_files": ["FATEM/3.lean"],
            "id": "teacher-3",
            "objective_limit": 1,
            "objective_regex": "^(FATEM/3\\.lean)$",
            "scope_hint": "FATEM/3.lean",
        },
    ]


def run_scope_markdown(rel_path: str) -> str:
    return f"""
    # Run Scope

    ## Allowed Files

    1. `{rel_path}`
    """


def write_validation(
    workspace: Path,
    *,
    rel_path: str,
    acceptance_status: str,
    validation_status: str,
    statement_fidelity: str = "preserved",
    blocker_notes: list[str] | None = None,
    workspace_changed: bool = False,
) -> None:
    validation_name = rel_path.replace("/", "_") + ".json"
    payload = {
        "schemaVersion": 1,
        "relPath": rel_path,
        "status": "clean" if acceptance_status == "accepted" else "attention",
        "acceptanceStatus": acceptance_status,
        "validationStatus": validation_status,
        "statementFidelity": statement_fidelity,
        "blockerNotes": blocker_notes or [],
        "checks": {
            "workspaceChanged": workspace_changed,
            "taskResult": {
                "present": bool(blocker_notes),
                "durable": bool(blocker_notes),
                "kind": "blocker" if blocker_notes else None,
                "path": f".archon/task_results/{blocker_notes[0]}" if blocker_notes else None,
            },
        },
    }
    write(workspace / ".archon" / "validation" / validation_name, json.dumps(payload, indent=2))


def make_fake_uv_for_orchestrator(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    script = bin_dir / "uv"
    write(
        script,
        """
        #!/usr/bin/env python3
        import json
        import os
        import sys
        from pathlib import Path

        args = sys.argv[1:]

        def arg_value(flag: str) -> str | None:
            if flag not in args:
                return None
            index = args.index(flag)
            if index + 1 >= len(args):
                return None
            return args[index + 1]

        log_path = arg_value("--log-path")
        raw_log_path = arg_value("--raw-log-path")
        for value in (log_path, raw_log_path):
            if value:
                path = Path(value)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('{"event":"session_end"}\\n', encoding="utf-8")

        campaign_root = Path(os.environ["FAKE_CAMPAIGN_ROOT"])
        workspace = campaign_root / "runs" / "queued-run" / "workspace"
        task_result = workspace / ".archon" / "task_results" / "FATEM_1.lean.md"
        task_result.parent.mkdir(parents=True, exist_ok=True)
        task_result.write_text(
            "# FATEM/1.lean\\n\\n"
            "## supervisor\\n"
            "### Attempt 1\\n"
            "- **Result:** FAILED\\n"
            "- **Concrete blocker:** The statement is false as written.\\n",
            encoding="utf-8",
        )

        validation = workspace / ".archon" / "validation" / "FATEM_1.lean.json"
        validation.parent.mkdir(parents=True, exist_ok=True)
        validation.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "relPath": "FATEM/1.lean",
                    "status": "clean",
                    "acceptanceStatus": "accepted",
                    "validationStatus": "passed",
                    "statementFidelity": "preserved",
                    "blockerNotes": ["FATEM_1.lean.md"],
                    "checks": {
                        "workspaceChanged": False,
                        "taskResult": {
                            "present": True,
                            "durable": True,
                            "kind": "blocker",
                            "path": ".archon/task_results/FATEM_1.lean.md",
                        },
                    },
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        raise SystemExit(0)
        """,
    )
    script.chmod(0o755)
    return bin_dir


def make_fake_codex(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin-codex"
    script = bin_dir / "codex"
    write(
        script,
        """
        #!/usr/bin/env python3
        import os
        import sys

        _ = sys.stdin.read()
        print("fake teacher run")
        raise SystemExit(int(os.environ.get("FAKE_CODEX_EXIT_CODE", "0")))
        """,
    )
    script.chmod(0o755)
    return bin_dir


def make_fake_uv(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin-uv"
    script = bin_dir / "uv"
    write(
        script,
        """
        #!/usr/bin/env python3
        import os
        import sys
        from pathlib import Path

        log_path = os.environ.get("FAKE_UV_LOG")
        if log_path:
            path = Path(log_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(" ".join(sys.argv[1:]) + "\\n")
        raise SystemExit(0)
        """,
    )
    script.chmod(0o755)
    return bin_dir


def make_fake_watchdog_exec(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin-watchdog"
    script = bin_dir / "fake-watchdog"
    write(
        script,
        """
        #!/usr/bin/env python3
        import os
        import sys
        import time
        from pathlib import Path

        log_path = os.environ.get("FAKE_WATCHDOG_LOG")
        if log_path:
            path = Path(log_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(" ".join(sys.argv[1:]) + "\\n")

        env_log = os.environ.get("FAKE_WATCHDOG_ENV_LOG")
        if env_log:
            keys = [item for item in os.environ.get("FAKE_WATCHDOG_ENV_KEYS", "").split(",") if item]
            path = Path(env_log)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8") as handle:
                for key in keys:
                    handle.write(f"{key}={os.environ.get(key, '<missing>')}\\n")

        sleep_seconds = float(os.environ.get("FAKE_WATCHDOG_SLEEP_SECONDS", "2"))
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

        raise SystemExit(int(os.environ.get("FAKE_WATCHDOG_EXIT_CODE", "0")))
        """,
    )
    script.chmod(0o755)
    return script


def test_create_campaign_builds_isolated_runs_and_teacher_launch_assets(tmp_path: Path):
    source = make_source_project(tmp_path, file_count=2)
    cache_project = tmp_path / "cache-project"
    write(cache_project / ".lake" / "packages" / "mathlib" / "README", "cached\n")
    campaign_root = tmp_path / "campaign"

    manifest = create_campaign(
        archon_root=ROOT,
        source_root=source,
        campaign_root=campaign_root,
        run_specs=[
            {"id": "teacher-a", "objective_regex": "^FATEM/1\\.lean$", "objective_limit": 1, "scope_hint": "FATEM/1.lean"},
            {"id": "teacher-b", "objective_regex": "^FATEM/2\\.lean$", "objective_limit": 1, "scope_hint": "FATEM/2.lean"},
        ],
        reuse_lake_from=cache_project,
    )

    assert manifest["campaignId"] == "campaign"
    assert len(manifest["runs"]) == 2
    assert manifest["runs"][0]["allowedFiles"] == ["FATEM/1.lean"]
    assert (campaign_root / "CAMPAIGN_MANIFEST.json").exists()
    assert (campaign_root / "campaign-status.json").exists()
    assert (campaign_root / "control" / "owner-mode.json").exists()
    events = (campaign_root / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(events) == 3

    run_root = campaign_root / "runs" / "teacher-a"
    assert (run_root / "source" / "FATEM" / "1.lean").exists()
    assert (run_root / "workspace" / "FATEM" / "1.lean").exists()
    assert (run_root / "workspace" / ".lake" / "packages" / "mathlib" / "README").exists()
    assert (run_root / "artifacts").exists()
    assert (run_root / "control" / "bootstrap-state.json").exists()
    bootstrap_payload = json.loads((run_root / "control" / "bootstrap-state.json").read_text(encoding="utf-8"))
    assert bootstrap_payload["allowedFiles"] == ["FATEM/1.lean"]
    assert bootstrap_payload["prewarmRequired"] is True
    assert bootstrap_payload["preloadHistoricalRoutes"] is False
    prompt = (run_root / "control" / "teacher-prompt.txt").read_text(encoding="utf-8")
    launch_script = (run_root / "control" / "launch-teacher.sh").read_text(encoding="utf-8")
    assert "Use $archon-supervisor" in prompt
    assert "AutoArchon run" in prompt
    assert "Bootstrap state:" in prompt
    assert "freshRun = true" in prompt
    assert "autoarchon-supervised-cycle" in prompt
    assert "--preload-historical-routes" not in prompt
    assert "--tail-scope-objective-threshold 4" in prompt
    assert "--tail-scope-plan-timeout-seconds 300" in prompt
    assert "--tail-scope-prover-timeout-seconds 360" in prompt
    assert "codex exec" in launch_script
    assert "--skip-mcp" in launch_script
    assert "--model gpt-5.4" in launch_script
    assert "teacher-launch-state.json" in launch_script
    assert "autoarchon-prewarm-project" in launch_script
    assert "prewarm.stdout.log" in launch_script
    assert 'ARCHON_SUPERVISOR_PRELOAD_HISTORICAL_ROUTES="${ARCHON_SUPERVISOR_PRELOAD_HISTORICAL_ROUTES:-0}"' in launch_script
    assert "teacher_launch_completed" in launch_script


def test_create_campaign_can_enable_historical_route_preload(tmp_path: Path):
    source = make_source_project(tmp_path, file_count=1)
    campaign_root = tmp_path / "campaign"
    manifest = create_campaign(
        archon_root=ROOT,
        source_root=source,
        campaign_root=campaign_root,
        run_specs=[
            {"id": "teacher-a", "objective_regex": "^FATEM/1\\.lean$", "objective_limit": 1, "scope_hint": "FATEM/1.lean"},
        ],
        preload_historical_routes=True,
    )

    assert manifest["teacherDefaults"]["preloadHistoricalRoutes"] is True
    assert manifest["runs"][0]["preloadHistoricalRoutes"] is True
    run_root = campaign_root / "runs" / "teacher-a"
    bootstrap_payload = json.loads((run_root / "control" / "bootstrap-state.json").read_text(encoding="utf-8"))
    assert bootstrap_payload["preloadHistoricalRoutes"] is True
    prompt = (run_root / "control" / "teacher-prompt.txt").read_text(encoding="utf-8")
    launch_script = (run_root / "control" / "launch-teacher.sh").read_text(encoding="utf-8")
    assert "--preload-historical-routes" in prompt
    assert "historical accepted routes are preloaded for this run" in prompt
    assert 'ARCHON_SUPERVISOR_PRELOAD_HISTORICAL_ROUTES="${ARCHON_SUPERVISOR_PRELOAD_HISTORICAL_ROUTES:-1}"' in launch_script


def test_create_campaign_marks_bootstrap_prewarm_optional_when_matching_build_is_reused(tmp_path: Path):
    source = make_source_project(tmp_path, file_count=1)
    cache_project = tmp_path / "cache-project"
    write(cache_project / "lean-toolchain", (source / "lean-toolchain").read_text(encoding="utf-8"))
    write(cache_project / "lakefile.lean", (source / "lakefile.lean").read_text(encoding="utf-8"))
    write(cache_project / ".lake" / "packages" / "mathlib" / "README", "cached\n")
    write(cache_project / ".lake" / "build" / "lib" / "placeholder", "local-build\n")
    write(cache_project / ".lake" / "config" / "manifest.json", "{}\n")
    campaign_root = tmp_path / "campaign"

    create_campaign(
        archon_root=ROOT,
        source_root=source,
        campaign_root=campaign_root,
        run_specs=[
            {"id": "teacher-a", "objective_regex": "^FATEM/1\\.lean$", "objective_limit": 1, "scope_hint": "FATEM/1.lean"},
        ],
        reuse_lake_from=cache_project,
    )

    run_root = campaign_root / "runs" / "teacher-a"
    bootstrap_payload = json.loads((run_root / "control" / "bootstrap-state.json").read_text(encoding="utf-8"))
    assert bootstrap_payload["allowedFiles"] == ["FATEM/1.lean"]
    assert bootstrap_payload["prewarmRequired"] is False
    assert "warmed local Lake build outputs were safely reused" in bootstrap_payload["initialStateSummary"]


def test_collect_campaign_status_reports_prewarm_plan_for_queued_runs(tmp_path: Path):
    source = make_source_project(tmp_path, file_count=2)
    campaign_root = tmp_path / "campaign"
    create_campaign(
        archon_root=ROOT,
        source_root=source,
        campaign_root=campaign_root,
        run_specs=[
            {
                "id": "queued-run",
                "objective_regex": "^(FATEM/1\\.lean|FATEM/2\\.lean)$",
                "objective_limit": 2,
                "scope_hint": "FATEM/1.lean, FATEM/2.lean",
            },
        ],
    )

    status = collect_campaign_status(campaign_root, heartbeat_seconds=1)

    run = status["runs"][0]
    assert run["status"] == "queued"
    assert run["configuredAllowedFiles"] == ["FATEM/1.lean", "FATEM/2.lean"]
    assert run["allowedFiles"] == []
    assert run["prewarmPlan"] == "scoped_verify"
    assert run["prewarmPending"] is True
    assert run["prewarmSummary"] == "scoped_verify, 2 files, pending"
    assert run["projectBuildReused"] is False


def test_collect_campaign_status_reports_sampled_prewarm_plan_for_wide_queued_runs(tmp_path: Path):
    source = make_source_project(tmp_path, file_count=5)
    campaign_root = tmp_path / "campaign"
    create_campaign(
        archon_root=ROOT,
        source_root=source,
        campaign_root=campaign_root,
        run_specs=[
            {
                "id": "queued-run",
                "objective_regex": "^(FATEM/1\\.lean|FATEM/2\\.lean|FATEM/3\\.lean|FATEM/4\\.lean|FATEM/5\\.lean)$",
                "objective_limit": 5,
                "scope_hint": "FATEM/1.lean, FATEM/2.lean, FATEM/3.lean, FATEM/4.lean, FATEM/5.lean",
            },
        ],
    )

    status = collect_campaign_status(campaign_root, heartbeat_seconds=1)

    run = status["runs"][0]
    assert run["status"] == "queued"
    assert run["configuredAllowedFiles"] == ["FATEM/1.lean", "FATEM/2.lean", "FATEM/3.lean", "FATEM/4.lean", "FATEM/5.lean"]
    assert run["prewarmPlan"] == "scoped_verify_sample"
    assert run["prewarmPending"] is True
    assert run["prewarmSummary"] == "scoped_verify_sample, sample 4/5 files, pending"
    assert run["projectBuildReused"] is False


def test_collect_campaign_status_reports_reused_build_prewarm_plan(tmp_path: Path):
    source = make_source_project(tmp_path, file_count=1)
    cache_project = tmp_path / "cache-project"
    write(cache_project / "lean-toolchain", (source / "lean-toolchain").read_text(encoding="utf-8"))
    write(cache_project / "lakefile.lean", (source / "lakefile.lean").read_text(encoding="utf-8"))
    write(cache_project / ".lake" / "packages" / "mathlib" / "README", "cached\n")
    write(cache_project / ".lake" / "build" / "lib" / "placeholder", "local-build\n")
    write(cache_project / ".lake" / "config" / "manifest.json", "{}\n")
    campaign_root = tmp_path / "campaign"

    create_campaign(
        archon_root=ROOT,
        source_root=source,
        campaign_root=campaign_root,
        run_specs=[
            {"id": "queued-run", "objective_regex": "^FATEM/1\\.lean$", "objective_limit": 1, "scope_hint": "FATEM/1.lean"},
        ],
        reuse_lake_from=cache_project,
    )

    status = collect_campaign_status(campaign_root, heartbeat_seconds=1)

    run = status["runs"][0]
    assert run["prewarmPlan"] == "reuse_build_outputs"
    assert run["prewarmPending"] is False
    assert run["prewarmSummary"] == "reuse_build_outputs, 1 files, ready"
    assert run["projectBuildReused"] is True
    assert run["lakePackagesLinked"] is True
    assert run["lakeBuildReusePath"] is not None


@pytest.mark.parametrize(
    ("exit_code", "phase"),
    [
        (0, "completed"),
        (17, "failed"),
    ],
)
def test_generated_launch_teacher_script_records_terminal_state_and_event_order(
    tmp_path: Path,
    exit_code: int,
    phase: str,
):
    source = make_source_project(tmp_path, file_count=1)
    campaign_root = tmp_path / "campaign"
    create_campaign(
        archon_root=ROOT,
        source_root=source,
        campaign_root=campaign_root,
        run_specs=[
            {"id": "queued-run", "objective_regex": "^FATEM/1\\.lean$", "objective_limit": 1, "scope_hint": "FATEM/1.lean"},
        ],
    )
    run_root = campaign_root / "runs" / "queued-run"
    control_root = run_root / "control"
    write(run_root / "workspace" / ".archon" / "RUN_SCOPE.md", run_scope_markdown("FATEM/1.lean"))
    fake_codex = make_fake_codex(tmp_path)

    env = os.environ.copy()
    env["PATH"] = f"{fake_codex}:{env['PATH']}"
    env["FAKE_CODEX_EXIT_CODE"] = str(exit_code)

    result = subprocess.run(
        ["bash", str(control_root / "launch-teacher.sh")],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == exit_code
    launch_state = json.loads((control_root / "teacher-launch-state.json").read_text(encoding="utf-8"))
    assert launch_state["active"] is False
    assert launch_state["phase"] == phase
    assert launch_state["exitCode"] == exit_code
    assert isinstance(launch_state["pid"], int)
    assert launch_state["pid"] > 0

    events = [json.loads(line) for line in (campaign_root / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    suffix = events[-2:]
    assert [event["event"] for event in suffix] == ["teacher_launch_started", "teacher_launch_completed"]
    assert suffix[0]["phase"] == "bootstrap"
    assert suffix[1]["phase"] == phase
    assert suffix[1]["exitCode"] == exit_code


@pytest.mark.parametrize(
    ("prewarm_required", "expected_prewarm_calls"),
    [
        (True, 1),
        (False, 0),
    ],
)
def test_generated_launch_teacher_script_respects_bootstrap_prewarm_flag(
    tmp_path: Path,
    prewarm_required: bool,
    expected_prewarm_calls: int,
):
    source = make_source_project(tmp_path, file_count=1)
    campaign_root = tmp_path / "campaign"
    create_campaign(
        archon_root=ROOT,
        source_root=source,
        campaign_root=campaign_root,
        run_specs=[
            {"id": "queued-run", "objective_regex": "^FATEM/1\\.lean$", "objective_limit": 1, "scope_hint": "FATEM/1.lean"},
        ],
    )
    run_root = campaign_root / "runs" / "queued-run"
    control_root = run_root / "control"
    bootstrap_path = control_root / "bootstrap-state.json"
    bootstrap_payload = json.loads(bootstrap_path.read_text(encoding="utf-8"))
    bootstrap_payload["prewarmRequired"] = prewarm_required
    bootstrap_path.write_text(json.dumps(bootstrap_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    fake_codex = make_fake_codex(tmp_path)
    fake_uv = make_fake_uv(tmp_path)
    uv_log = tmp_path / "uv.log"
    env = os.environ.copy()
    env["PATH"] = f"{fake_uv}:{fake_codex}:{env['PATH']}"
    env["FAKE_CODEX_EXIT_CODE"] = "0"
    env["FAKE_UV_LOG"] = str(uv_log)

    result = subprocess.run(
        ["bash", str(control_root / "launch-teacher.sh")],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    launch_state = json.loads((control_root / "teacher-launch-state.json").read_text(encoding="utf-8"))
    assert launch_state["phase"] == "completed"
    assert launch_state["active"] is False
    assert isinstance(launch_state["pid"], int)
    assert launch_state["pid"] > 0
    assert (run_root / "workspace" / ".archon" / "RUN_SCOPE.md").exists()

    uv_lines = uv_log.read_text(encoding="utf-8").splitlines() if uv_log.exists() else []
    prewarm_lines = [line for line in uv_lines if "autoarchon-prewarm-project" in line]
    assert len(prewarm_lines) == expected_prewarm_calls
    if expected_prewarm_calls:
        assert any("--verify-file FATEM/1.lean" in line for line in prewarm_lines)

    updated_bootstrap = json.loads(bootstrap_path.read_text(encoding="utf-8"))
    assert updated_bootstrap["prewarmRequired"] is False


def test_generated_launch_teacher_script_uses_scoped_verify_for_narrow_multi_file_shards(tmp_path: Path):
    source = make_source_project(tmp_path, file_count=2)
    campaign_root = tmp_path / "campaign"
    create_campaign(
        archon_root=ROOT,
        source_root=source,
        campaign_root=campaign_root,
        run_specs=[
            {
                "id": "queued-run",
                "objective_regex": "^(FATEM/1\\.lean|FATEM/2\\.lean)$",
                "objective_limit": 2,
                "scope_hint": "FATEM/1.lean, FATEM/2.lean",
                "allowed_files": ["FATEM/1.lean", "FATEM/2.lean"],
            },
        ],
    )
    run_root = campaign_root / "runs" / "queued-run"
    control_root = run_root / "control"

    fake_codex = make_fake_codex(tmp_path)
    fake_uv = make_fake_uv(tmp_path)
    uv_log = tmp_path / "uv.log"
    env = os.environ.copy()
    env["PATH"] = f"{fake_uv}:{fake_codex}:{env['PATH']}"
    env["FAKE_CODEX_EXIT_CODE"] = "0"
    env["FAKE_UV_LOG"] = str(uv_log)

    result = subprocess.run(
        ["bash", str(control_root / "launch-teacher.sh")],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    bootstrap_payload = json.loads((control_root / "bootstrap-state.json").read_text(encoding="utf-8"))
    assert bootstrap_payload["allowedFiles"] == ["FATEM/1.lean", "FATEM/2.lean"]
    prewarm_lines = [
        line
        for line in uv_log.read_text(encoding="utf-8").splitlines()
        if "autoarchon-prewarm-project" in line
    ]
    assert len(prewarm_lines) == 1
    assert "--verify-file FATEM/1.lean" in prewarm_lines[0]
    assert "--verify-file FATEM/2.lean" in prewarm_lines[0]


def test_generated_launch_teacher_script_samples_scoped_verify_for_wide_shards(tmp_path: Path):
    source = make_source_project(tmp_path, file_count=5)
    campaign_root = tmp_path / "campaign"
    create_campaign(
        archon_root=ROOT,
        source_root=source,
        campaign_root=campaign_root,
        run_specs=[
            {
                "id": "queued-run",
                "objective_regex": "^(FATEM/1\\.lean|FATEM/2\\.lean|FATEM/3\\.lean|FATEM/4\\.lean|FATEM/5\\.lean)$",
                "objective_limit": 5,
                "scope_hint": "FATEM/1.lean, FATEM/2.lean, FATEM/3.lean, FATEM/4.lean, FATEM/5.lean",
                "allowed_files": [
                    "FATEM/1.lean",
                    "FATEM/2.lean",
                    "FATEM/3.lean",
                    "FATEM/4.lean",
                    "FATEM/5.lean",
                ],
            },
        ],
    )
    run_root = campaign_root / "runs" / "queued-run"
    control_root = run_root / "control"

    fake_codex = make_fake_codex(tmp_path)
    fake_uv = make_fake_uv(tmp_path)
    uv_log = tmp_path / "uv.log"
    env = os.environ.copy()
    env["PATH"] = f"{fake_uv}:{fake_codex}:{env['PATH']}"
    env["FAKE_CODEX_EXIT_CODE"] = "0"
    env["FAKE_UV_LOG"] = str(uv_log)

    result = subprocess.run(
        ["bash", str(control_root / "launch-teacher.sh")],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    prewarm_lines = [
        line
        for line in uv_log.read_text(encoding="utf-8").splitlines()
        if "autoarchon-prewarm-project" in line
    ]
    assert len(prewarm_lines) == 1
    assert prewarm_lines[0].count("--verify-file") == 4
    assert "--verify-file FATEM/1.lean" in prewarm_lines[0]
    assert "--verify-file FATEM/5.lean" in prewarm_lines[0]


def test_collect_campaign_status_records_transition_events_for_acceptance(tmp_path: Path):
    source = make_source_project(tmp_path, file_count=1)
    campaign_root = tmp_path / "campaign"
    create_campaign(
        archon_root=ROOT,
        source_root=source,
        campaign_root=campaign_root,
        run_specs=[
            {"id": "accepted-run", "objective_regex": "^FATEM/1\\.lean$", "objective_limit": 1, "scope_hint": "FATEM/1.lean"},
        ],
    )
    workspace = campaign_root / "runs" / "accepted-run" / "workspace"
    write(workspace / ".archon" / "RUN_SCOPE.md", run_scope_markdown("FATEM/1.lean"))
    write(workspace / "FATEM" / "1.lean", "theorem file_1 : True := by\n  trivial\n")
    write_validation(
        workspace,
        rel_path="FATEM/1.lean",
        acceptance_status="accepted",
        validation_status="passed",
        workspace_changed=True,
    )

    status = collect_campaign_status(campaign_root, heartbeat_seconds=1)

    assert status["runs"][0]["status"] == "accepted"
    events = [json.loads(line) for line in (campaign_root / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    suffix = events[-3:]
    assert [event["event"] for event in suffix] == [
        "run_status_changed",
        "validation_accepted",
        "campaign_status_refreshed",
    ]
    assert suffix[0]["statusBefore"] == "queued"
    assert suffix[0]["statusAfter"] == "accepted"
    assert suffix[1]["relPath"] == "FATEM/1.lean"
    assert suffix[2]["changedRunIds"] == ["accepted-run"]
    assert suffix[2]["acceptedEvents"] == 1


def test_collect_campaign_status_records_transition_events_for_blocker_acceptance(tmp_path: Path):
    source = make_source_project(tmp_path, file_count=1)
    campaign_root = tmp_path / "campaign"
    create_campaign(
        archon_root=ROOT,
        source_root=source,
        campaign_root=campaign_root,
        run_specs=[
            {"id": "blocked-run", "objective_regex": "^FATEM/1\\.lean$", "objective_limit": 1, "scope_hint": "FATEM/1.lean"},
        ],
    )
    workspace = campaign_root / "runs" / "blocked-run" / "workspace"
    write(workspace / ".archon" / "RUN_SCOPE.md", run_scope_markdown("FATEM/1.lean"))
    write(
        workspace / ".archon" / "task_results" / "FATEM_1.lean.md",
        """
        # FATEM/1.lean

        - **Concrete blocker:** theorem is false as stated.
        """,
    )
    write_validation(
        workspace,
        rel_path="FATEM/1.lean",
        acceptance_status="accepted",
        validation_status="passed",
        blocker_notes=["FATEM_1.lean.md"],
        workspace_changed=False,
    )

    status = collect_campaign_status(campaign_root, heartbeat_seconds=1)

    assert status["runs"][0]["status"] == "blocked"
    events = [json.loads(line) for line in (campaign_root / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    suffix = events[-3:]
    assert [event["event"] for event in suffix] == [
        "run_status_changed",
        "blocker_accepted",
        "campaign_status_refreshed",
    ]
    assert suffix[0]["statusBefore"] == "queued"
    assert suffix[0]["statusAfter"] == "blocked"
    assert suffix[1]["relPath"] == "FATEM/1.lean"
    assert suffix[1]["blockerNotes"] == ["FATEM_1.lean.md"]
    assert suffix[2]["changedRunIds"] == ["blocked-run"]
    assert suffix[2]["acceptedEvents"] == 1


def test_append_campaign_status_events_deduplicates_acceptance_events_under_stale_previous_snapshot(tmp_path: Path):
    campaign_root = tmp_path / "campaign"
    campaign_root.mkdir(parents=True, exist_ok=True)

    previous_status = {
        "counts": {"queued": 1},
        "runs": [
            {
                "runId": "accepted-run",
                "status": "queued",
                "acceptedProofs": [],
                "acceptedBlockers": [],
            }
        ],
    }
    current_status = {
        "counts": {"accepted": 1},
        "runs": [
            {
                "runId": "accepted-run",
                "status": "accepted",
                "acceptedProofs": ["FATEM/1.lean"],
                "acceptedBlockers": [],
                "latestIteration": "iter-001",
                "runRoot": "runs/accepted-run",
            }
        ],
    }

    _append_campaign_status_events(
        campaign_root,
        previous_status=previous_status,
        current_status=current_status,
    )
    _append_campaign_status_events(
        campaign_root,
        previous_status=previous_status,
        current_status=current_status,
    )

    events = [json.loads(line) for line in (campaign_root / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [event["event"] for event in events].count("validation_accepted") == 1


def test_collect_campaign_status_classifies_run_health_states(tmp_path: Path):
    source = make_source_project(tmp_path)
    campaign_root = tmp_path / "campaign"
    create_campaign(
        archon_root=ROOT,
        source_root=source,
        campaign_root=campaign_root,
        run_specs=[
            {"id": "accepted-run", "objective_regex": "^FATEM/1\\.lean$", "objective_limit": 1, "scope_hint": "FATEM/1.lean"},
            {"id": "blocked-run", "objective_regex": "^FATEM/2\\.lean$", "objective_limit": 1, "scope_hint": "FATEM/2.lean"},
            {"id": "running-run", "objective_regex": "^FATEM/3\\.lean$", "objective_limit": 1, "scope_hint": "FATEM/3.lean"},
            {"id": "unverified-run", "objective_regex": "^FATEM/4\\.lean$", "objective_limit": 1, "scope_hint": "FATEM/4.lean"},
            {"id": "contaminated-run", "objective_regex": "^FATEM/5\\.lean$", "objective_limit": 1, "scope_hint": "FATEM/5.lean"},
            {"id": "relaunch-run", "objective_regex": "^FATEM/6\\.lean$", "objective_limit": 1, "scope_hint": "FATEM/6.lean"},
        ],
    )

    accepted_workspace = campaign_root / "runs" / "accepted-run" / "workspace"
    write(accepted_workspace / ".archon" / "RUN_SCOPE.md", run_scope_markdown("FATEM/1.lean"))
    write(accepted_workspace / "FATEM" / "1.lean", "theorem file_1 : True := by\n  trivial\n")
    write(
        accepted_workspace / ".archon" / "lessons" / "iter-001-clean.json",
        json.dumps(
            {
                "schemaVersion": 1,
                "status": "no_progress",
                "iteration": "iter-001",
                "signals": ["no_progress"],
                "recommendedAction": "Tighten scope or lower timeouts before the next cycle.",
                "lessons": [
                    {
                        "category": "scope_control",
                        "summary": "When a cycle produces no new changed files or task results, tighten the scope or reduce time budgets before the next attempt.",
                        "evidence": ["FATEM/1.lean"],
                    }
                ],
            },
            indent=2,
        ),
    )
    write_validation(
        accepted_workspace,
        rel_path="FATEM/1.lean",
        acceptance_status="accepted",
        validation_status="passed",
        workspace_changed=True,
    )

    blocked_workspace = campaign_root / "runs" / "blocked-run" / "workspace"
    write(blocked_workspace / ".archon" / "RUN_SCOPE.md", run_scope_markdown("FATEM/2.lean"))
    write(
        blocked_workspace / ".archon" / "task_results" / "FATEM_2.lean.md",
        """
        # FATEM/2.lean

        - **Concrete blocker:** theorem is false as stated.
        """,
    )
    write_validation(
        blocked_workspace,
        rel_path="FATEM/2.lean",
        acceptance_status="accepted",
        validation_status="passed",
        blocker_notes=["FATEM_2.lean.md"],
        workspace_changed=False,
    )

    running_workspace = campaign_root / "runs" / "running-run" / "workspace"
    write(running_workspace / ".archon" / "RUN_SCOPE.md", run_scope_markdown("FATEM/3.lean"))
    write(
        running_workspace / ".archon" / "supervisor" / "run-lease.json",
        json.dumps(
            {
                "schemaVersion": 1,
                "active": True,
                "status": "running",
                "lastHeartbeatAt": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
        ),
    )

    unverified_workspace = campaign_root / "runs" / "unverified-run" / "workspace"
    write(unverified_workspace / ".archon" / "RUN_SCOPE.md", run_scope_markdown("FATEM/4.lean"))
    write(unverified_workspace / "FATEM" / "4.lean", "theorem file_4 : True := by\n  trivial\n")

    contaminated_workspace = campaign_root / "runs" / "contaminated-run" / "workspace"
    write(contaminated_workspace / ".archon" / "RUN_SCOPE.md", run_scope_markdown("FATEM/5.lean"))
    write_validation(
        contaminated_workspace,
        rel_path="FATEM/5.lean",
        acceptance_status="rejected",
        validation_status="failed",
        statement_fidelity="violated",
    )

    relaunch_workspace = campaign_root / "runs" / "relaunch-run" / "workspace"
    write(relaunch_workspace / ".archon" / "RUN_SCOPE.md", run_scope_markdown("FATEM/6.lean"))
    write_validation(
        relaunch_workspace,
        rel_path="FATEM/6.lean",
        acceptance_status="pending",
        validation_status="attention",
    )

    status = collect_campaign_status(campaign_root, heartbeat_seconds=1)

    statuses = {run["runId"]: run["status"] for run in status["runs"]}
    recommendations = {run["runId"]: run["recommendedRecovery"]["action"] for run in status["runs"]}
    recovery_classes = {run["runId"]: run["recoveryClass"] for run in status["runs"]}
    assert statuses == {
        "accepted-run": "accepted",
        "blocked-run": "blocked",
        "running-run": "running",
        "unverified-run": "unverified",
        "contaminated-run": "contaminated",
        "relaunch-run": "needs_relaunch",
    }
    assert recommendations == {
        "accepted-run": "none",
        "blocked-run": "none",
        "running-run": "none",
        "unverified-run": "recovery_only",
        "contaminated-run": "manual_rebuild",
        "relaunch-run": "relaunch_teacher",
    }
    assert recovery_classes == {
        "accepted-run": "terminal",
        "blocked-run": "terminal",
        "running-run": "running",
        "unverified-run": "recovery_finalize",
        "contaminated-run": "manual_rebuild",
        "relaunch-run": "partial_progress_relaunch",
    }
    assert status["counts"]["accepted"] == 1
    assert status["counts"]["blocked"] == 1
    assert status["counts"]["running"] == 1
    assert status["counts"]["unverified"] == 1
    assert status["counts"]["contaminated"] == 1
    assert status["counts"]["needs_relaunch"] == 1


def test_collect_campaign_status_treats_dead_active_lease_as_needs_relaunch(tmp_path: Path):
    source = make_source_project(tmp_path, file_count=1)
    campaign_root = tmp_path / "campaign"
    create_campaign(
        archon_root=ROOT,
        source_root=source,
        campaign_root=campaign_root,
        run_specs=[
            {"id": "lease-run", "objective_regex": "^FATEM/1\\.lean$", "objective_limit": 1, "scope_hint": "FATEM/1.lean"},
        ],
    )

    workspace = campaign_root / "runs" / "lease-run" / "workspace"
    write(workspace / ".archon" / "RUN_SCOPE.md", run_scope_markdown("FATEM/1.lean"))
    write(
        workspace / ".archon" / "supervisor" / "run-lease.json",
        json.dumps(
            {
                "schemaVersion": 1,
                "active": True,
                "status": "running",
                "supervisorPid": 99999,
                "loopPid": 88888,
                "lastHeartbeatAt": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
        ),
    )

    status = collect_campaign_status(campaign_root, heartbeat_seconds=900)
    run = status["runs"][0]

    assert run["status"] == "needs_relaunch"
    assert run["runningSignal"] is False
    assert run["leaseActive"] is False
    assert run["leaseRecordedActive"] is True
    assert run["recommendedRecovery"]["action"] == "relaunch_teacher"
    assert run["recoveryClass"] == "launch_failed_retry"


def test_collect_campaign_status_treats_recent_launch_bootstrap_as_running(tmp_path: Path):
    source = make_source_project(tmp_path, file_count=1)
    campaign_root = tmp_path / "campaign"
    create_campaign(
        archon_root=ROOT,
        source_root=source,
        campaign_root=campaign_root,
        run_specs=[
            {"id": "queued-run", "objective_regex": "^FATEM/1\\.lean$", "objective_limit": 1, "scope_hint": "FATEM/1.lean"},
        ],
    )

    control_root = campaign_root / "runs" / "queued-run" / "control"
    write(
        control_root / "teacher-launch-state.json",
        json.dumps(
            {
                "schemaVersion": 1,
                "active": True,
                "phase": "bootstrap",
                "pid": os.getpid(),
                "updatedAt": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
        ),
    )

    status = collect_campaign_status(campaign_root, heartbeat_seconds=60)
    run = status["runs"][0]

    assert run["status"] == "running"
    assert run["runningSignal"] is True
    assert run["launchActive"] is True
    assert run["launchStatePresent"] is True
    assert run["recommendedRecovery"]["action"] == "none"
    assert status["counts"]["running"] == 1


def test_collect_campaign_status_restores_accepted_artifact_proofs_from_event_history(tmp_path: Path):
    source = make_source_project(tmp_path, file_count=1)
    campaign_root = tmp_path / "campaign"
    create_campaign(
        archon_root=ROOT,
        source_root=source,
        campaign_root=campaign_root,
        run_specs=[
            {"id": "accepted-run", "objective_regex": "^FATEM/1\\.lean$", "objective_limit": 1, "scope_hint": "FATEM/1.lean"},
        ],
    )

    run_root = campaign_root / "runs" / "accepted-run"
    workspace = run_root / "workspace"
    artifacts = run_root / "artifacts"
    write(workspace / "FATEM" / "1.lean", "theorem file_1 : True := by\n  trivial\n")
    write(
        workspace / ".archon" / "validation" / "FATEM_1.lean.json",
        json.dumps(
            {
                "relPath": "FATEM/1.lean",
                "acceptanceStatus": "none",
                "validationStatus": "no_progress",
                "checks": {"workspaceChanged": True},
            },
            indent=2,
        ),
    )
    write(artifacts / "proofs" / "FATEM" / "1.lean", "theorem file_1 : True := by\n  trivial\n")
    write(
        artifacts / "validation" / "FATEM_1.lean.json",
        json.dumps(
            {
                "relPath": "FATEM/1.lean",
                "acceptanceStatus": "none",
                "validationStatus": "no_progress",
                "checks": {"workspaceChanged": True},
            },
            indent=2,
        ),
    )
    with (campaign_root / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "timestamp": "2026-04-14T00:00:00+00:00",
                    "event": "validation_accepted",
                    "campaignId": campaign_root.name,
                    "runId": "accepted-run",
                    "relPath": "FATEM/1.lean",
                },
                sort_keys=True,
            )
            + "\n"
        )

    status = collect_campaign_status(campaign_root, heartbeat_seconds=60)
    run = status["runs"][0]

    assert run["status"] == "accepted"
    assert run["acceptedProofs"] == ["FATEM/1.lean"]
    assert run["remainingTargets"] == []
    assert run["recommendedRecovery"]["action"] == "none"


def test_collect_campaign_status_marks_stale_launch_as_needs_relaunch(tmp_path: Path):
    source = make_source_project(tmp_path, file_count=1)
    campaign_root = tmp_path / "campaign"
    create_campaign(
        archon_root=ROOT,
        source_root=source,
        campaign_root=campaign_root,
        run_specs=[
            {"id": "queued-run", "objective_regex": "^FATEM/1\\.lean$", "objective_limit": 1, "scope_hint": "FATEM/1.lean"},
        ],
    )

    control_root = campaign_root / "runs" / "queued-run" / "control"
    write(
        control_root / "teacher-launch-state.json",
        json.dumps(
            {
                "schemaVersion": 1,
                "active": True,
                "phase": "bootstrap",
                "updatedAt": "2000-01-01T00:00:00+00:00",
            },
            indent=2,
        ),
    )

    status = collect_campaign_status(campaign_root, heartbeat_seconds=0)
    run = status["runs"][0]

    assert run["status"] == "needs_relaunch"
    assert run["runningSignal"] is False
    assert run["launchActive"] is False
    assert run["launchStatePresent"] is True
    assert run["recommendedRecovery"]["action"] == "relaunch_teacher"
    assert status["counts"]["needs_relaunch"] == 1
    assert run["recoveryClass"] == "launch_failed_retry"
    assert run["lastLaunchExitCode"] is None


def test_collect_campaign_status_marks_dead_launch_pid_as_needs_relaunch(tmp_path: Path):
    source = make_source_project(tmp_path, file_count=1)
    campaign_root = tmp_path / "campaign"
    create_campaign(
        archon_root=ROOT,
        source_root=source,
        campaign_root=campaign_root,
        run_specs=[
            {"id": "queued-run", "objective_regex": "^FATEM/1\\.lean$", "objective_limit": 1, "scope_hint": "FATEM/1.lean"},
        ],
    )

    proc = subprocess.Popen(["bash", "-lc", "exit 0"])
    dead_pid = proc.pid
    proc.wait(timeout=5)

    control_root = campaign_root / "runs" / "queued-run" / "control"
    write(
        control_root / "teacher-launch-state.json",
        json.dumps(
            {
                "schemaVersion": 1,
                "active": True,
                "phase": "codex_exec",
                "pid": dead_pid,
                "updatedAt": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
        ),
    )

    status = collect_campaign_status(campaign_root, heartbeat_seconds=900)
    run = status["runs"][0]

    assert run["status"] == "needs_relaunch"
    assert run["runningSignal"] is False
    assert run["launchActive"] is False
    assert run["launchStatePresent"] is True
    assert run["recommendedRecovery"]["action"] == "relaunch_teacher"
    assert run["recoveryClass"] == "launch_failed_retry"


def test_collect_campaign_status_detects_live_legacy_launch_script_without_pid(tmp_path: Path):
    source = make_source_project(tmp_path, file_count=1)
    campaign_root = tmp_path / "campaign"
    create_campaign(
        archon_root=ROOT,
        source_root=source,
        campaign_root=campaign_root,
        run_specs=[
            {"id": "queued-run", "objective_regex": "^FATEM/1\\.lean$", "objective_limit": 1, "scope_hint": "FATEM/1.lean"},
        ],
    )

    control_root = campaign_root / "runs" / "queued-run" / "control"
    launch_script = control_root / "launch-teacher.sh"
    write(
        launch_script,
        """
        #!/usr/bin/env bash
        sleep 3
        """,
    )
    launch_script.chmod(0o755)
    write(
        control_root / "teacher-launch-state.json",
        json.dumps(
            {
                "schemaVersion": 1,
                "active": True,
                "phase": "codex_exec",
                "updatedAt": "2000-01-01T00:00:00+00:00",
            },
            indent=2,
        ),
    )

    proc = subprocess.Popen(["bash", str(launch_script)], cwd=str(ROOT))
    try:
        deadline = time.monotonic() + 5
        while proc.poll() is None and time.monotonic() < deadline:
            status = collect_campaign_status(campaign_root, heartbeat_seconds=0)
            run = status["runs"][0]
            if run["runningSignal"] is True:
                break
            time.sleep(0.05)
        else:
            raise AssertionError("live launch script was not detected")

        assert run["status"] == "running"
        assert run["runningSignal"] is True
        assert run["launchActive"] is True
        assert run["launchStatePresent"] is True
        assert run["recommendedRecovery"]["action"] == "none"
    finally:
        proc.wait(timeout=5)


def test_collect_campaign_status_does_not_treat_old_launch_state_as_running_forever(tmp_path: Path):
    source = make_source_project(tmp_path, file_count=1)
    campaign_root = tmp_path / "campaign"
    create_campaign(
        archon_root=ROOT,
        source_root=source,
        campaign_root=campaign_root,
        run_specs=[
            {"id": "queued-run", "objective_regex": "^FATEM/1\\.lean$", "objective_limit": 1, "scope_hint": "FATEM/1.lean"},
        ],
    )

    control_root = campaign_root / "runs" / "queued-run" / "control"
    write(
        control_root / "teacher-launch-state.json",
        json.dumps(
            {
                "schemaVersion": 1,
                "active": True,
                "phase": "codex_exec",
                "updatedAt": "2000-01-01T00:00:00+00:00",
            },
            indent=2,
        ),
    )
    write(control_root / "teacher-launch.stdout.log", "launcher wrote output\n")

    status = collect_campaign_status(campaign_root, heartbeat_seconds=900)
    run = status["runs"][0]

    assert run["status"] == "needs_relaunch"
    assert run["runningSignal"] is False
    assert run["launchStatePresent"] is True
    assert run["recommendedRecovery"]["action"] == "relaunch_teacher"
    assert run["recoveryClass"] == "launch_failed_retry"


def test_collect_campaign_status_marks_rate_limited_launches_with_retry_after(tmp_path: Path):
    source = make_source_project(tmp_path, file_count=1)
    campaign_root = tmp_path / "campaign"
    create_campaign(
        archon_root=ROOT,
        source_root=source,
        campaign_root=campaign_root,
        run_specs=[
            {"id": "queued-run", "objective_regex": "^FATEM/1\\.lean$", "objective_limit": 1, "scope_hint": "FATEM/1.lean"},
        ],
    )

    run_root = campaign_root / "runs" / "queued-run"
    control_root = run_root / "control"
    write(
        control_root / "teacher-launch-state.json",
        json.dumps(
            {
                "schemaVersion": 1,
                "active": False,
                "phase": "failed",
                "exitCode": 1,
                "updatedAt": "2026-04-13T00:00:00+00:00",
            },
            indent=2,
        ),
    )
    write(control_root / "teacher-launch.stderr.log", "ERROR: exceeded retry limit, last status: 429 Too Many Requests\n")

    status = collect_campaign_status(campaign_root, heartbeat_seconds=0)
    run = status["runs"][0]

    assert run["status"] == "needs_relaunch"
    assert run["recoveryClass"] == "rate_limited_backoff"
    assert run["lastLaunchExitCode"] == 1
    assert run["retryAfter"] == "2026-04-13T00:15:00+00:00"


def test_collect_campaign_status_respects_explicitly_inactive_lease_over_recent_activity(tmp_path: Path):
    source = make_source_project(tmp_path, file_count=1)
    campaign_root = tmp_path / "campaign"
    create_campaign(
        archon_root=ROOT,
        source_root=source,
        campaign_root=campaign_root,
        run_specs=[
            {"id": "finished-run", "objective_regex": "^FATEM/1\\.lean$", "objective_limit": 1, "scope_hint": "FATEM/1.lean"},
        ],
    )

    run_root = campaign_root / "runs" / "finished-run"
    workspace = run_root / "workspace"
    write(workspace / ".archon" / "RUN_SCOPE.md", run_scope_markdown("FATEM/1.lean"))
    write(
        workspace / ".archon" / "supervisor" / "run-lease.json",
        json.dumps(
            {
                "schemaVersion": 1,
                "active": False,
                "status": "completed",
                "finalStatus": "clean",
                "completedAt": datetime.now(timezone.utc).isoformat(),
                "lastHeartbeatAt": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
        ),
    )
    write(workspace / ".archon" / "supervisor" / ".supervised-cycle.stdout.tmp", "recent output\n")
    write(
        run_root / "control" / "teacher-launch-state.json",
        json.dumps(
            {
                "schemaVersion": 1,
                "active": True,
                "phase": "bootstrap",
                "updatedAt": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
        ),
    )

    status = collect_campaign_status(campaign_root, heartbeat_seconds=900)
    run = status["runs"][0]

    assert run["status"] == "needs_relaunch"
    assert run["runningSignal"] is False
    assert run["launchStatePresent"] is True
    assert run["recommendedRecovery"]["action"] == "relaunch_teacher"
    assert run["recoveryClass"] == "launch_failed_retry"


def test_collect_campaign_status_treats_fresh_relaunch_as_running_even_with_old_inactive_lease(tmp_path: Path):
    source = make_source_project(tmp_path, file_count=1)
    campaign_root = tmp_path / "campaign"
    create_campaign(
        archon_root=ROOT,
        source_root=source,
        campaign_root=campaign_root,
        run_specs=[
            {"id": "relaunch-run", "objective_regex": "^FATEM/1\\.lean$", "objective_limit": 1, "scope_hint": "FATEM/1.lean"},
        ],
    )

    run_root = campaign_root / "runs" / "relaunch-run"
    workspace = run_root / "workspace"
    write(workspace / ".archon" / "RUN_SCOPE.md", run_scope_markdown("FATEM/1.lean"))
    write(
        workspace / ".archon" / "supervisor" / "run-lease.json",
        json.dumps(
            {
                "schemaVersion": 1,
                "active": False,
                "status": "completed",
                "finalStatus": "no_progress",
                "completedAt": "2026-04-13T00:00:00+00:00",
                "updatedAt": "2026-04-13T00:00:00+00:00",
                "lastHeartbeatAt": "2026-04-13T00:00:00+00:00",
            },
            indent=2,
        ),
    )
    write(
        run_root / "control" / "teacher-launch-state.json",
        json.dumps(
            {
                "schemaVersion": 1,
                "active": True,
                "phase": "codex_exec",
                "updatedAt": datetime.now(timezone.utc).isoformat(),
                "pid": os.getpid(),
            },
            indent=2,
        ),
    )

    status = collect_campaign_status(campaign_root, heartbeat_seconds=900)
    run = status["runs"][0]

    assert run["status"] == "running"
    assert run["runningSignal"] is True
    assert run["launchActive"] is True
    assert run["recommendedRecovery"]["action"] == "none"
    assert run["recoveryClass"] == "running"


def test_collect_campaign_status_ignores_stale_live_launcher_after_newer_completed_lease(tmp_path: Path):
    source = make_source_project(tmp_path, file_count=1)
    campaign_root = tmp_path / "campaign"
    create_campaign(
        archon_root=ROOT,
        source_root=source,
        campaign_root=campaign_root,
        run_specs=[
            {"id": "stale-launch-run", "objective_regex": "^FATEM/1\\.lean$", "objective_limit": 1, "scope_hint": "FATEM/1.lean"},
        ],
    )

    run_root = campaign_root / "runs" / "stale-launch-run"
    workspace = run_root / "workspace"
    control_root = run_root / "control"
    launch_script = control_root / "launch-teacher.sh"
    write(
        launch_script,
        """
        #!/usr/bin/env bash
        sleep 3
        """,
    )
    launch_script.chmod(0o755)
    write(workspace / ".archon" / "RUN_SCOPE.md", run_scope_markdown("FATEM/1.lean"))
    write(
        workspace / ".archon" / "supervisor" / "run-lease.json",
        json.dumps(
            {
                "schemaVersion": 1,
                "active": False,
                "status": "completed",
                "finalStatus": "no_progress",
                "completedAt": datetime.now(timezone.utc).isoformat(),
                "updatedAt": datetime.now(timezone.utc).isoformat(),
                "lastHeartbeatAt": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
        ),
    )
    write(
        control_root / "teacher-launch-state.json",
        json.dumps(
            {
                "schemaVersion": 1,
                "active": True,
                "phase": "codex_exec",
                "updatedAt": "2000-01-01T00:00:00+00:00",
            },
            indent=2,
        ),
    )

    proc = subprocess.Popen(["bash", str(launch_script)], cwd=str(ROOT))
    try:
        time.sleep(0.2)
        status = collect_campaign_status(campaign_root, heartbeat_seconds=900)
        run = status["runs"][0]
    finally:
        proc.wait(timeout=5)

    assert run["status"] == "needs_relaunch"
    assert run["runningSignal"] is False
    assert run["launchActive"] is False
    assert run["recommendedRecovery"]["action"] == "relaunch_teacher"


def test_finalize_campaign_copies_only_accepted_proofs_and_blockers(tmp_path: Path):
    source = make_source_project(tmp_path, file_count=3)
    campaign_root = tmp_path / "campaign"
    create_campaign(
        archon_root=ROOT,
        source_root=source,
        campaign_root=campaign_root,
        run_specs=[
            {"id": "accepted-run", "objective_regex": "^FATEM/1\\.lean$", "objective_limit": 1, "scope_hint": "FATEM/1.lean"},
            {"id": "blocked-run", "objective_regex": "^FATEM/2\\.lean$", "objective_limit": 1, "scope_hint": "FATEM/2.lean"},
            {"id": "unverified-run", "objective_regex": "^FATEM/3\\.lean$", "objective_limit": 1, "scope_hint": "FATEM/3.lean"},
        ],
    )

    accepted_workspace = campaign_root / "runs" / "accepted-run" / "workspace"
    write(accepted_workspace / ".archon" / "RUN_SCOPE.md", run_scope_markdown("FATEM/1.lean"))
    write(accepted_workspace / "FATEM" / "1.lean", "theorem file_1 : True := by\n  trivial\n")
    write(
        accepted_workspace / ".archon" / "lessons" / "iter-001-clean.json",
        json.dumps(
            {
                "schemaVersion": 1,
                "status": "no_progress",
                "iteration": "iter-001",
                "signals": ["no_progress"],
                "recommendedAction": "Tighten scope or lower timeouts before the next cycle.",
                "lessons": [
                    {
                        "category": "scope_control",
                        "summary": "When a cycle produces no new changed files or task results, tighten the scope or reduce time budgets before the next attempt.",
                        "evidence": ["FATEM/1.lean"],
                    }
                ],
            },
            indent=2,
        ),
    )
    write_validation(
        accepted_workspace,
        rel_path="FATEM/1.lean",
        acceptance_status="accepted",
        validation_status="passed",
        workspace_changed=True,
    )

    blocked_workspace = campaign_root / "runs" / "blocked-run" / "workspace"
    write(blocked_workspace / ".archon" / "RUN_SCOPE.md", run_scope_markdown("FATEM/2.lean"))
    write(
        blocked_workspace / ".archon" / "task_results" / "FATEM_2.lean.md",
        """
        # FATEM/2.lean

        - **Concrete blocker:** theorem is false as stated.
        """,
    )
    write_validation(
        blocked_workspace,
        rel_path="FATEM/2.lean",
        acceptance_status="accepted",
        validation_status="passed",
        blocker_notes=["FATEM_2.lean.md"],
    )

    unverified_workspace = campaign_root / "runs" / "unverified-run" / "workspace"
    write(unverified_workspace / ".archon" / "RUN_SCOPE.md", run_scope_markdown("FATEM/3.lean"))
    write(unverified_workspace / "FATEM" / "3.lean", "theorem file_3 : True := by\n  trivial\n")

    summary = finalize_campaign(campaign_root, heartbeat_seconds=1)

    final_root = campaign_root / "reports" / "final"
    assert (final_root / "proofs" / "accepted-run" / "FATEM" / "1.lean").exists()
    assert (final_root / "diffs" / "accepted-run" / "FATEM" / "1.lean.diff").exists()
    assert (final_root / "blockers" / "blocked-run" / "FATEM_2.lean.md").exists()
    assert not (final_root / "proofs" / "unverified-run" / "FATEM" / "3.lean").exists()
    assert "accepted-run:FATEM/1.lean" in summary["acceptedProofs"]
    assert "blocked-run:FATEM_2.lean.md" in summary["acceptedBlockers"]
    final_summary = json.loads((final_root / "final-summary.json").read_text(encoding="utf-8"))
    assert final_summary["counts"]["accepted"] == 1
    assert final_summary["counts"]["blocked"] == 1
    assert final_summary["counts"]["unverified"] == 1
    compare_report = json.loads((final_root / "compare-report.json").read_text(encoding="utf-8"))
    assert compare_report["runCounts"]["accepted"] == 1
    assert compare_report["runCounts"]["blocked"] == 1
    assert compare_report["runCounts"]["unverified"] == 1
    assert compare_report["prewarmCounts"]["plans"]["scoped_verify"] == 3
    assert compare_report["prewarmCounts"]["pendingRuns"] == 3
    assert compare_report["targetCounts"]["acceptedProofs"] == 1
    assert compare_report["targetCounts"]["acceptedBlockers"] == 1
    assert compare_report["targetCounts"]["unverifiedArtifacts"] == 1
    accepted_row = next(item for item in compare_report["runs"] if item["runId"] == "accepted-run")
    assert accepted_row["prewarmPlan"] == "scoped_verify"
    assert accepted_row["prewarmSummary"] == "scoped_verify, 1 files, pending"
    assert accepted_row["recoveryClass"] == "terminal"
    assert accepted_row["timelinePath"] == "runs/accepted-run/timeline.json"
    assert accepted_row["timelineEntryCount"] >= 1
    timelines = {item["runId"]: item for item in compare_report["runTimelines"]}
    accepted_timeline = timelines["accepted-run"]
    blocked_timeline = timelines["blocked-run"]
    assert any(event["event"] == "validation_accepted" for event in accepted_timeline["events"])
    assert any(event["summary"] == "status queued -> accepted" for event in accepted_timeline["events"])
    assert any(event["event"] == "blocker_accepted" for event in blocked_timeline["events"])
    assert any("FATEM_2.lean.md" in event["summary"] for event in blocked_timeline["events"])
    accepted_timeline_file = json.loads((final_root / "runs" / "accepted-run" / "timeline.json").read_text(encoding="utf-8"))
    assert accepted_timeline_file["runId"] == "accepted-run"
    assert accepted_timeline_file["entryCount"] == accepted_row["timelineEntryCount"]
    assert any(event["event"] == "validation_accepted" for event in accepted_timeline_file["entries"])
    lesson_records = [
        json.loads(line)
        for line in (final_root / "lessons" / "lesson-records.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert (final_root / "lessons" / "lesson-clusters.json").exists()
    assert (final_root / "lessons" / "lesson-clusters.md").exists()
    assert (final_root / "lessons" / "lesson-reminders.json").exists()
    assert (final_root / "lessons" / "lesson-reminders.md").exists()
    lesson_categories = {record["category"] for record in lesson_records}
    assert "accepted_proof" in lesson_categories
    assert "accepted_blocker" in lesson_categories
    assert "scope_control" in lesson_categories
    accepted_proof_record = next(record for record in lesson_records if record["category"] == "accepted_proof")
    assert accepted_proof_record["run_id"] == "accepted-run"
    assert accepted_proof_record["theorem_id"] == "FATEM/1.lean"
    assert accepted_proof_record["accepted_state"] == "accepted"
    assert "recommended_action" in accepted_proof_record
    assert "source_status" in accepted_proof_record
    assert "signal_tags" in accepted_proof_record
    compare_markdown = (final_root / "compare-report.md").read_text(encoding="utf-8")
    assert "| run | status | class | retry_after | launch_exit | proofs | blockers |" in compare_markdown
    assert "| prewarm | recommended | timeline |" in compare_markdown
    assert "Prewarm plans" in compare_markdown
    assert "scoped_verify, 1 files, pending" in compare_markdown
    assert "## Run Timelines" in compare_markdown
    assert "- accepted-run (`accepted`):" in compare_markdown
    assert "status queued -> accepted" in compare_markdown
    assert "blocker accepted: FATEM/2.lean (FATEM_2.lean.md)" in compare_markdown


def test_finalize_campaign_can_prune_rebuildable_caches_after_export(tmp_path: Path):
    source = make_source_project(tmp_path, file_count=1)
    campaign_root = tmp_path / "campaign"
    create_campaign(
        archon_root=ROOT,
        source_root=source,
        campaign_root=campaign_root,
        run_specs=[
            {"id": "accepted-run", "objective_regex": "^FATEM/1\\.lean$", "objective_limit": 1, "scope_hint": "FATEM/1.lean"},
        ],
    )

    workspace = campaign_root / "runs" / "accepted-run" / "workspace"
    write(workspace / ".archon" / "RUN_SCOPE.md", run_scope_markdown("FATEM/1.lean"))
    write(workspace / "FATEM" / "1.lean", "theorem file_1 : True := by\n  trivial\n")
    write(workspace / ".lake" / "build" / "artifact.bin", "x" * 128)
    write(campaign_root / "tmp.mathlib" / ".lake.prewarm-broken-20260414" / "junk", "y" * 64)
    write_validation(
        workspace,
        rel_path="FATEM/1.lean",
        acceptance_status="accepted",
        validation_status="passed",
        workspace_changed=True,
    )

    summary = finalize_campaign(
        campaign_root,
        heartbeat_seconds=1,
        prune_workspace_lake=True,
        prune_broken_prewarm=True,
    )

    final_root = campaign_root / "reports" / "final"
    assert (final_root / "proofs" / "accepted-run" / "FATEM" / "1.lean").exists()
    assert summary["cachePrune"]["selectedCount"] == 2
    assert not (workspace / ".lake").exists()
    assert not (campaign_root / "tmp.mathlib" / ".lake.prewarm-broken-20260414").exists()
    final_summary = json.loads((final_root / "final-summary.json").read_text(encoding="utf-8"))
    assert final_summary["cachePrune"]["selectedCount"] == 2


def test_execute_run_recovery_runs_recovery_only_and_exports_artifacts(tmp_path: Path):
    source = make_source_project(tmp_path, file_count=1)
    campaign_root = tmp_path / "campaign"
    create_campaign(
        archon_root=ROOT,
        source_root=source,
        campaign_root=campaign_root,
        run_specs=[
            {"id": "unverified-run", "objective_regex": "^FATEM/1\\.lean$", "objective_limit": 1, "scope_hint": "FATEM/1.lean"},
        ],
    )
    workspace = campaign_root / "runs" / "unverified-run" / "workspace"
    write(workspace / ".archon" / "RUN_SCOPE.md", run_scope_markdown("FATEM/1.lean"))
    write(workspace / "FATEM" / "1.lean", "theorem file_1 : True := by\n  trivial\n")
    write(workspace / ".archon" / "logs" / "iter-001" / "meta.json", json.dumps({"iteration": 1, "prover": {"status": "done"}}))
    verify_script = tmp_path / "verify_changed.py"
    write(
        verify_script,
        """
        from pathlib import Path
        import sys

        text = Path(sys.argv[1]).read_text(encoding="utf-8")
        if "sorry" in text:
            raise SystemExit(1)
        print("verified clean")
        """,
    )

    result = execute_run_recovery(
        campaign_root,
        "unverified-run",
        execute=True,
        heartbeat_seconds=0,
        changed_file_verify_template=f"python3 {verify_script} {{file}}",
    )

    assert result["resolvedAction"] == "recovery_only"
    assert result["executed"] is True
    assert result["supervisedCycle"]["returncode"] == 0
    assert result["artifactExport"]["returncode"] == 0
    assert result["statusAfter"] == "accepted"
    assert (campaign_root / "runs" / "unverified-run" / "artifacts" / "proofs" / "FATEM" / "1.lean").exists()

    status = collect_campaign_status(campaign_root, heartbeat_seconds=0)
    assert status["runs"][0]["status"] == "accepted"


def test_execute_run_recovery_relaunches_teacher_in_foreground(tmp_path: Path):
    source = make_source_project(tmp_path, file_count=1)
    campaign_root = tmp_path / "campaign"
    create_campaign(
        archon_root=ROOT,
        source_root=source,
        campaign_root=campaign_root,
        run_specs=[
            {"id": "queued-run", "objective_regex": "^FATEM/1\\.lean$", "objective_limit": 1, "scope_hint": "FATEM/1.lean"},
        ],
    )
    control_root = campaign_root / "runs" / "queued-run" / "control"
    marker = control_root / "launch-marker.txt"
    write(
        control_root / "launch-teacher.sh",
        f"""
        #!/usr/bin/env bash
        set -euo pipefail
        echo launched > {marker}
        """,
    )
    (control_root / "launch-teacher.sh").chmod(0o755)

    result = execute_run_recovery(
        campaign_root,
        "queued-run",
        execute=True,
        heartbeat_seconds=1,
        detach_launch=False,
    )

    assert result["resolvedAction"] == "launch_teacher"
    assert result["executed"] is True
    assert result["teacherLaunch"]["detached"] is False
    assert result["teacherLaunch"]["returncode"] == 0
    assert marker.exists()


def test_execute_run_recovery_detached_launch_writes_inflight_state_and_blocks_duplicate_launch(tmp_path: Path):
    source = make_source_project(tmp_path, file_count=1)
    campaign_root = tmp_path / "campaign"
    create_campaign(
        archon_root=ROOT,
        source_root=source,
        campaign_root=campaign_root,
        run_specs=[
            {"id": "queued-run", "objective_regex": "^FATEM/1\\.lean$", "objective_limit": 1, "scope_hint": "FATEM/1.lean"},
        ],
    )
    control_root = campaign_root / "runs" / "queued-run" / "control"
    marker = control_root / "launch-marker.txt"
    write(
        control_root / "launch-teacher.sh",
        f"""
        #!/usr/bin/env bash
        set -euo pipefail
        sleep 2
        echo launched >> {marker}
        """,
    )
    (control_root / "launch-teacher.sh").chmod(0o755)

    first = execute_run_recovery(
        campaign_root,
        "queued-run",
        execute=True,
        heartbeat_seconds=60,
        detach_launch=True,
    )
    assert first["resolvedAction"] == "launch_teacher"
    assert first["executed"] is True
    assert first["statusAfter"] == "running"
    launch_state = json.loads((control_root / "teacher-launch-state.json").read_text(encoding="utf-8"))
    assert launch_state["active"] is True
    assert launch_state["phase"] == "dispatch"
    assert first["teacherLaunch"]["stateFile"] == "runs/queued-run/control/teacher-launch-state.json"

    second = execute_run_recovery(
        campaign_root,
        "queued-run",
        execute=False,
        heartbeat_seconds=60,
    )
    assert second["status"] == "running"
    assert second["resolvedAction"] == "none"

    proc = subprocess.run(["bash", "-lc", "sleep 3"], capture_output=True, text=True, check=False)
    assert proc.returncode == 0
    assert marker.read_text(encoding="utf-8").strip() == "launched"


def test_campaign_recover_cli_selects_all_recoverable_runs(tmp_path: Path):
    source = make_source_project(tmp_path, file_count=2)
    campaign_root = tmp_path / "campaign"
    create_campaign(
        archon_root=ROOT,
        source_root=source,
        campaign_root=campaign_root,
        run_specs=[
            {"id": "queued-run", "objective_regex": "^FATEM/1\\.lean$", "objective_limit": 1, "scope_hint": "FATEM/1.lean"},
            {"id": "unverified-run", "objective_regex": "^FATEM/2\\.lean$", "objective_limit": 1, "scope_hint": "FATEM/2.lean"},
        ],
    )
    workspace = campaign_root / "runs" / "unverified-run" / "workspace"
    write(workspace / ".archon" / "RUN_SCOPE.md", run_scope_markdown("FATEM/2.lean"))
    write(workspace / "FATEM" / "2.lean", "theorem file_2 : True := by\n  trivial\n")

    result = subprocess.run(
        [
            "python3",
            str(CAMPAIGN_RECOVER),
            "--campaign-root",
            str(campaign_root),
            "--all-recoverable",
            "--heartbeat-seconds",
            "0",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert [entry["runId"] for entry in payload] == ["queued-run", "unverified-run"]
    assert [entry["resolvedAction"] for entry in payload] == ["launch_teacher", "recovery_only"]


def test_campaign_compare_cli_writes_compare_report(tmp_path: Path):
    source = make_source_project(tmp_path, file_count=1)
    campaign_root = tmp_path / "campaign"
    create_campaign(
        archon_root=ROOT,
        source_root=source,
        campaign_root=campaign_root,
        run_specs=[
            {"id": "accepted-run", "objective_regex": "^FATEM/1\\.lean$", "objective_limit": 1, "scope_hint": "FATEM/1.lean"},
        ],
    )
    workspace = campaign_root / "runs" / "accepted-run" / "workspace"
    write(workspace / ".archon" / "RUN_SCOPE.md", run_scope_markdown("FATEM/1.lean"))
    write(workspace / "FATEM" / "1.lean", "theorem file_1 : True := by\n  trivial\n")
    write_validation(
        workspace,
        rel_path="FATEM/1.lean",
        acceptance_status="accepted",
        validation_status="passed",
        workspace_changed=True,
    )

    result = subprocess.run(
        [
            "python3",
            str(CAMPAIGN_COMPARE),
            "--campaign-root",
            str(campaign_root),
            "--heartbeat-seconds",
            "0",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["runCounts"]["accepted"] == 1
    assert payload["prewarmCounts"]["plans"]["scoped_verify"] == 1
    assert payload["targetCounts"]["acceptedProofs"] == 1
    assert payload["runs"][0]["prewarmPlan"] == "scoped_verify"
    assert payload["runs"][0]["timelinePath"] == "runs/accepted-run/timeline.json"
    assert payload["runs"][0]["timelineEntryCount"] >= 1
    assert payload["runTimelines"][0]["runId"] == "accepted-run"
    assert any(event["event"] == "validation_accepted" for event in payload["runTimelines"][0]["events"])
    assert (campaign_root / "reports" / "final" / "compare-report.json").exists()
    assert (campaign_root / "reports" / "final" / "runs" / "accepted-run" / "timeline.json").exists()


def test_campaign_overview_and_archive_capture_owner_lease_and_status(tmp_path: Path):
    source = make_source_project(tmp_path, file_count=1)
    campaign_root = tmp_path / "campaign"
    create_campaign(
        archon_root=ROOT,
        source_root=source,
        campaign_root=campaign_root,
        run_specs=[
            {"id": "accepted-run", "objective_regex": "^FATEM/1\\.lean$", "objective_limit": 1, "scope_hint": "FATEM/1.lean"},
        ],
    )
    workspace = campaign_root / "runs" / "accepted-run" / "workspace"
    write(workspace / ".archon" / "RUN_SCOPE.md", run_scope_markdown("FATEM/1.lean"))
    write(workspace / "FATEM" / "1.lean", "theorem file_1 : True := by\n  trivial\n")
    write_validation(
        workspace,
        rel_path="FATEM/1.lean",
        acceptance_status="accepted",
        validation_status="passed",
        workspace_changed=True,
    )

    claimed, lease = claim_owner_lease(
        campaign_root,
        owner_entrypoint="autoarchon-orchestrator-watchdog",
        owner_pid=os.getpid(),
        session_id="session-lease",
        lease_seconds=120,
        metadata={"mode": "watchdog"},
    )
    assert claimed is True
    assert owner_lease_is_live(lease) is True

    write(
        campaign_root / "control" / "orchestrator-watchdog.json",
        json.dumps(
                {
                    "watchdogStatus": "degraded",
                    "restartCount": 2,
                    "likelyCause": "likely_provider_transport",
                    "lastProgressAt": "2026-04-13T10:00:00+00:00",
                    "lastRecoveryAt": "2026-04-13T10:05:00+00:00",
                    "activeWorkRunIds": [],
                },
            indent=2,
        ),
    )

    overview = build_campaign_overview(campaign_root, heartbeat_seconds=0)
    assert overview["targetCounts"]["acceptedProofs"] == 1
    assert overview["ownerLease"]["sessionId"] == "session-lease"
    assert overview["watchdogStatus"] == "degraded"
    assert overview["progress"]["percent"] == 100
    assert overview["progress"]["completed"] == 1
    assert overview["progress"]["total"] == 1

    archive_payload = archive_campaign_postmortem(campaign_root, heartbeat_seconds=0)
    assert archive_payload["overview"]["ownerLease"]["sessionId"] == "session-lease"
    assert (campaign_root / "reports" / "postmortem" / "postmortem-summary.json").exists()
    assert (campaign_root / "reports" / "postmortem" / "postmortem-summary.md").exists()
    postmortem_records = [
        json.loads(line)
        for line in (campaign_root / "reports" / "postmortem" / "lessons" / "lesson-records.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert (campaign_root / "reports" / "postmortem" / "lessons" / "lesson-clusters.json").exists()
    assert (campaign_root / "reports" / "postmortem" / "lessons" / "lesson-clusters.md").exists()
    assert (campaign_root / "reports" / "postmortem" / "lessons" / "lesson-reminders.json").exists()
    assert (campaign_root / "reports" / "postmortem" / "lessons" / "lesson-reminders.md").exists()
    postmortem_categories = {record["category"] for record in postmortem_records}
    assert "provider_transport" in postmortem_categories
    assert "watchdog_relaunch" in postmortem_categories

    overview_result = subprocess.run(
        [
            "python3",
            str(CAMPAIGN_OVERVIEW),
            "--campaign-root",
            str(campaign_root),
            "--heartbeat-seconds",
            "0",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert overview_result.returncode == 0, overview_result.stderr
    overview_cli = json.loads(overview_result.stdout)
    assert overview_cli["ownerLease"]["sessionId"] == "session-lease"
    assert overview_cli["targetCounts"]["acceptedProofs"] == 1
    assert (campaign_root / "control" / "progress-summary.md").exists()
    assert (campaign_root / "control" / "progress-summary.json").exists()
    progress_summary = (campaign_root / "control" / "progress-summary.md").read_text(encoding="utf-8")
    assert "# Campaign Progress:" in progress_summary
    assert "100% (1/1 finalized targets)" in progress_summary
    assert "## Recent Finalized" in progress_summary
    assert "proof accepted-run:FATEM/1.lean" in progress_summary
    assert "Final summary:" in progress_summary

    archive_result = subprocess.run(
        [
            "python3",
            str(CAMPAIGN_ARCHIVE),
            "--campaign-root",
            str(campaign_root),
            "--heartbeat-seconds",
            "0",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert archive_result.returncode == 0, archive_result.stderr
    archive_cli = json.loads(archive_result.stdout)
    assert archive_cli["overview"]["ownerLease"]["sessionId"] == "session-lease"
    assert archive_cli["runCounts"]["accepted"] == 1


def test_owner_lease_operations_preserve_owner_mode_metadata(tmp_path: Path):
    source = make_source_project(tmp_path, file_count=1)
    campaign_root = tmp_path / "campaign"
    create_campaign(
        archon_root=ROOT,
        source_root=source,
        campaign_root=campaign_root,
        run_specs=[
            {"id": "teacher-1", "objective_regex": "^FATEM/1\\.lean$", "objective_limit": 1, "scope_hint": "FATEM/1.lean"},
        ],
    )
    ensure_campaign_control_root(
        campaign_root,
        owner_mode="campaign_operator",
        watchdog_enabled=True,
        manager_enabled=False,
        owner_entrypoint="autoarchon-orchestrator-watchdog",
    )

    claimed, _lease = claim_owner_lease(
        campaign_root,
        owner_entrypoint="autoarchon-orchestrator-watchdog",
        owner_pid=os.getpid(),
        session_id="session-preserve",
        lease_seconds=120,
        metadata={"mode": "watchdog"},
    )
    assert claimed is True

    refresh_owner_lease(
        campaign_root,
        owner_entrypoint="autoarchon-orchestrator-watchdog",
        owner_pid=os.getpid(),
        session_id="session-preserve",
        lease_seconds=120,
        metadata={"mode": "watchdog", "tick": 2},
    )
    release_owner_lease(
        campaign_root,
        owner_entrypoint="autoarchon-orchestrator-watchdog",
        owner_pid=os.getpid(),
        session_id="session-preserve",
        release_reason="test-finished",
    )

    owner_mode = json.loads((campaign_root / "control" / "owner-mode.json").read_text(encoding="utf-8"))
    assert owner_mode["ownerMode"] == "campaign_operator"
    assert owner_mode["watchdogEnabled"] is True
    assert owner_mode["managerEnabled"] is False
    assert owner_mode["ownerEntrypoint"] == "autoarchon-orchestrator-watchdog"


def test_build_campaign_overview_surfaces_run_progress_summary_signals(tmp_path: Path):
    source = make_source_project(tmp_path, file_count=1)
    campaign_root = tmp_path / "campaign"
    create_campaign(
        archon_root=ROOT,
        source_root=source,
        campaign_root=campaign_root,
        run_specs=[
            {"id": "teacher-001", "objective_regex": "^FATEM/1\\.lean$", "objective_limit": 1, "scope_hint": "FATEM/1.lean"},
        ],
    )

    run_root = campaign_root / "runs" / "teacher-001"
    now = datetime.now(timezone.utc).isoformat()
    write(
        run_root / "workspace" / ".archon" / "supervisor" / "run-lease.json",
        json.dumps({"active": True, "lastHeartbeatAt": now, "status": "running"}, sort_keys=True),
    )
    write(
        run_root / "workspace" / ".archon" / "supervisor" / "progress-summary.json",
        json.dumps(
            {
                "status": "running",
                "liveRuntime": {
                    "phase": "proving",
                    "iteration": "iter-003",
                    "planStatus": "done",
                    "proverStatus": "running",
                    "reviewStatus": None,
                    "activeProvers": [{"file": "FATEM/1.lean", "id": "FATEM_1", "status": "running"}],
                },
                "helper": {
                    "noteCount": 2,
                    "countsByReason": {"lsp_timeout": 1, "missing_infrastructure": 1},
                    "countsByPhase": {"prover": 2},
                },
                "taskResultsSummary": {
                    "counts": {"resolved": 0, "blocker": 1, "other": 0},
                },
            },
            sort_keys=True,
        ),
    )

    overview = build_campaign_overview(campaign_root, heartbeat_seconds=60)

    assert len(overview["runningRuns"]) == 1
    running = overview["runningRuns"][0]
    assert running["runId"] == "teacher-001"
    assert running["scopeHint"] == "FATEM/1.lean"
    assert running["latestIteration"] == "iter-003"
    assert running["livePhase"] == "proving"
    assert running["activeProverCount"] == 1
    assert running["helperNoteCount"] == 2
    assert running["helperReasonCounts"] == {"lsp_timeout": 1, "missing_infrastructure": 1}
    assert running["taskResultBlockerCount"] == 1

    overview_result = subprocess.run(
        [
            "python3",
            str(CAMPAIGN_OVERVIEW),
            "--campaign-root",
            str(campaign_root),
            "--heartbeat-seconds",
            "60",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert overview_result.returncode == 0, overview_result.stderr
    progress_markdown = (campaign_root / "control" / "progress-summary.md").read_text(encoding="utf-8")
    assert "phase=proving" in progress_markdown
    assert "helper_notes=2" in progress_markdown
    assert "blocker_notes=1" in progress_markdown
    progress_payload = json.loads((campaign_root / "control" / "progress-summary.json").read_text(encoding="utf-8"))
    assert "statusBuckets" in progress_payload
    assert "recommendedCommands" in progress_payload
    assert "recentTransitions" in progress_payload
    assert "cooldownState" in progress_payload


def test_create_campaign_scaffolds_operator_surfaces(tmp_path: Path):
    source = make_source_project(tmp_path, file_count=1)
    campaign_root = tmp_path / "campaign"
    create_campaign(
        archon_root=ROOT,
        source_root=source,
        campaign_root=campaign_root,
        run_specs=[
            {"id": "teacher-1", "objective_regex": "^FATEM/1\\.lean$", "objective_limit": 1, "scope_hint": "FATEM/1.lean"},
        ],
    )

    mission_brief = campaign_root / "control" / "mission-brief.md"
    operator_journal = campaign_root / "control" / "operator-journal.md"
    assert mission_brief.exists()
    assert operator_journal.exists()
    assert "Mission Brief" in mission_brief.read_text(encoding="utf-8")
    assert "autoarchon-create-campaign" in operator_journal.read_text(encoding="utf-8")


def test_launch_from_spec_cli_bootstraps_campaign_and_starts_watchdog(tmp_path: Path):
    source = make_source_project(tmp_path, file_count=3)
    campaign_root = tmp_path / "nightly-campaign"
    run_spec_output = tmp_path / "run-specs" / "nightly-campaign.json"
    spec_path = tmp_path / "campaign-spec.json"
    spec_path.write_text(
        json.dumps(
            {
                "sourceRoot": str(source),
                "campaignRoot": str(campaign_root),
                "reuseLakeFrom": str(source),
                "runSpecOutput": str(run_spec_output),
                "teacherModel": "teacher-model",
                "teacherReasoningEffort": "medium",
                "planShards": {
                    "runIdPrefix": "teacher-m",
                    "runIdMode": "file_stem",
                    "matchRegex": r"^FATEM/[1-2]\.lean$",
                    "shardSize": 8,
                },
                "watchdog": {
                    "enabled": True,
                    "model": "owner-model",
                    "reasoningEffort": "xhigh",
                    "pollSeconds": 5,
                    "stallSeconds": 60,
                    "maxRestarts": 1,
                    "maxActiveLaunches": 1,
                    "launchBatchSize": 1,
                    "launchCooldownSeconds": 30,
                    "finalizeOnTerminal": False,
                    "pruneWorkspaceLake": True,
                    "pruneBrokenPrewarm": True,
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    fake_watchdog = make_fake_watchdog_exec(tmp_path)
    watchdog_log = tmp_path / "watchdog.log"
    env = os.environ.copy()
    env["ARCHON_WATCHDOG_EXECUTABLE"] = str(fake_watchdog)
    env["FAKE_WATCHDOG_LOG"] = str(watchdog_log)
    env["FAKE_WATCHDOG_SLEEP_SECONDS"] = "2"

    result = subprocess.run(
        [
            "python3",
            str(LAUNCH_FROM_SPEC),
            "--spec-file",
            str(spec_path),
            "--shard-size",
            "1",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["campaignRoot"] == str(campaign_root)
    assert payload["campaignCreated"] is True
    assert payload["runSpecFile"] == str(run_spec_output)
    assert payload["runSpecCount"] == 2
    assert payload["operatorSurfaces"]["missionBriefPath"].endswith("control/mission-brief.md")
    assert payload["operatorSurfaces"]["operatorJournalPath"].endswith("control/operator-journal.md")
    assert payload["watchdog"]["status"] == "started"
    assert (campaign_root / "CAMPAIGN_MANIFEST.json").exists()
    assert (campaign_root / "control" / "launch-spec.resolved.json").exists()
    assert (campaign_root / "control" / "mission-brief.md").exists()
    assert (campaign_root / "control" / "operator-journal.md").exists()
    owner_mode = json.loads((campaign_root / "control" / "owner-mode.json").read_text(encoding="utf-8"))
    assert owner_mode["ownerMode"] == "campaign_operator"
    assert owner_mode["watchdogEnabled"] is True

    manifest = json.loads((campaign_root / "CAMPAIGN_MANIFEST.json").read_text(encoding="utf-8"))
    assert [run["id"] for run in manifest["runs"]] == ["teacher-m-1", "teacher-m-2"]
    assert json.loads(run_spec_output.read_text(encoding="utf-8"))[0]["id"] == "teacher-m-1"
    assert json.loads(spec_path.read_text(encoding="utf-8"))["planShards"]["shardSize"] == 8
    resolved_spec = json.loads((campaign_root / "control" / "launch-spec.resolved.json").read_text(encoding="utf-8"))
    assert resolved_spec["teacherModel"] == "teacher-model"
    operator_journal = (campaign_root / "control" / "operator-journal.md").read_text(encoding="utf-8")
    assert "autoarchon-launch-from-spec" in operator_journal

    watchdog_command = payload["watchdog"]["command"]
    assert watchdog_command[0] == str(fake_watchdog)
    rendered_watchdog_command = " ".join(watchdog_command)
    assert str(campaign_root) in rendered_watchdog_command
    assert "--model owner-model" in rendered_watchdog_command
    assert "--reasoning-effort xhigh" in rendered_watchdog_command
    assert "--no-finalize" in rendered_watchdog_command
    assert "--prune-workspace-lake" in rendered_watchdog_command
    assert "--prune-broken-prewarm" in rendered_watchdog_command
    assert "--campaign-root" in watchdog_log.read_text(encoding="utf-8")


def test_launch_from_spec_can_enable_historical_route_preload_in_campaign_assets(tmp_path: Path):
    source = make_source_project(tmp_path, file_count=1)
    campaign_root = tmp_path / "campaign"
    run_spec_output = tmp_path / "run-specs" / "campaign.json"
    spec_path = tmp_path / "campaign-spec.json"
    spec_path.write_text(
        json.dumps(
            {
                "sourceRoot": str(source),
                "campaignRoot": str(campaign_root),
                "reuseLakeFrom": str(source),
                "runSpecOutput": str(run_spec_output),
                "teacherModel": "teacher-model",
                "teacherReasoningEffort": "medium",
                "preloadHistoricalRoutes": True,
                "planShards": {
                    "runIdPrefix": "teacher-preload",
                    "runIdMode": "index",
                    "matchRegex": r"^FATEM/1\.lean$",
                    "shardSize": 1,
                },
                "watchdog": {
                    "enabled": False,
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            "python3",
            str(LAUNCH_FROM_SPEC),
            "--spec-file",
            str(spec_path),
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["campaignCreated"] is True
    manifest = json.loads((campaign_root / "CAMPAIGN_MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["teacherDefaults"]["preloadHistoricalRoutes"] is True
    run_root = campaign_root / "runs" / "teacher-preload-001"
    prompt = (run_root / "control" / "teacher-prompt.txt").read_text(encoding="utf-8")
    launch_script = (run_root / "control" / "launch-teacher.sh").read_text(encoding="utf-8")
    bootstrap_payload = json.loads((run_root / "control" / "bootstrap-state.json").read_text(encoding="utf-8"))
    resolved_spec = json.loads((campaign_root / "control" / "launch-spec.resolved.json").read_text(encoding="utf-8"))
    assert bootstrap_payload["preloadHistoricalRoutes"] is True
    assert "--preload-historical-routes" in prompt
    assert 'ARCHON_SUPERVISOR_PRELOAD_HISTORICAL_ROUTES="${ARCHON_SUPERVISOR_PRELOAD_HISTORICAL_ROUTES:-1}"' in launch_script
    assert resolved_spec["preloadHistoricalRoutes"] is True


def test_launch_from_spec_cli_dry_run_does_not_write_campaign_or_mutate_spec(tmp_path: Path):
    source = make_source_project(tmp_path, file_count=2)
    campaign_root = tmp_path / "dry-run-campaign"
    run_spec_output = tmp_path / "run-specs" / "dry-run.json"
    spec_path = tmp_path / "campaign-spec.json"
    original_spec = {
        "sourceRoot": str(source),
        "campaignRoot": str(campaign_root),
        "runSpecOutput": str(run_spec_output),
        "planShards": {
            "runIdPrefix": "teacher-dry",
            "runIdMode": "index",
            "matchRegex": r"^FATEM/[1-2]\.lean$",
            "shardSize": 8,
        },
        "watchdog": {
            "enabled": True,
            "model": "owner-model",
            "reasoningEffort": "xhigh",
        },
    }
    spec_path.write_text(json.dumps(original_spec, indent=2) + "\n", encoding="utf-8")

    result = subprocess.run(
        [
            "python3",
            str(LAUNCH_FROM_SPEC),
            "--spec-file",
            str(spec_path),
            "--shard-size",
            "1",
            "--dry-run",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["dryRun"] is True
    assert payload["watchdog"]["status"] == "dry_run"
    assert payload["runSpecCount"] == 2
    assert not campaign_root.exists()
    assert not run_spec_output.exists()
    assert json.loads(spec_path.read_text(encoding="utf-8")) == original_spec


def test_launch_from_spec_skips_unresolved_optional_env_placeholders(tmp_path: Path):
    source = make_source_project(tmp_path, file_count=1)
    campaign_root = tmp_path / "campaign"
    run_spec_output = tmp_path / "run-specs" / "campaign.json"
    spec_path = tmp_path / "campaign-spec.json"
    env_log = tmp_path / "watchdog-env.log"
    spec_path.write_text(
        json.dumps(
            {
                "sourceRoot": str(source),
                "campaignRoot": str(campaign_root),
                "runSpecOutput": str(run_spec_output),
                "planShards": {
                    "runIdPrefix": "teacher-env",
                    "runIdMode": "index",
                    "matchRegex": r"^FATEM/1\.lean$",
                    "shardSize": 1,
                },
                "watchdog": {
                    "enabled": True,
                    "model": "owner-model",
                    "reasoningEffort": "xhigh",
                },
                "environment": {
                    "ARCHON_CODEX_READY_RETRIES": "${ARCHON_CODEX_READY_RETRIES}",
                    "ARCHON_CODEX_READY_RETRY_DELAY_SECONDS": "${ARCHON_CODEX_READY_RETRY_DELAY_SECONDS}",
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    fake_watchdog = make_fake_watchdog_exec(tmp_path)

    env = os.environ.copy()
    env["ARCHON_WATCHDOG_EXECUTABLE"] = str(fake_watchdog)
    env["FAKE_WATCHDOG_ENV_LOG"] = str(env_log)
    env["FAKE_WATCHDOG_ENV_KEYS"] = "ARCHON_CODEX_READY_RETRIES,ARCHON_CODEX_READY_RETRY_DELAY_SECONDS"
    env["FAKE_WATCHDOG_SLEEP_SECONDS"] = "2"
    env.pop("ARCHON_CODEX_READY_RETRIES", None)
    env.pop("ARCHON_CODEX_READY_RETRY_DELAY_SECONDS", None)

    result = subprocess.run(
        [
            "python3",
            str(LAUNCH_FROM_SPEC),
            "--spec-file",
            str(spec_path),
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    deadline = time.time() + 2.0
    while time.time() < deadline and not env_log.exists():
        time.sleep(0.05)
    assert env_log.exists()
    env_payload = env_log.read_text(encoding="utf-8")
    assert "ARCHON_CODEX_READY_RETRIES=<missing>" in env_payload
    assert "ARCHON_CODEX_READY_RETRY_DELAY_SECONDS=<missing>" in env_payload


def test_launch_from_spec_applies_watchdog_env_overrides_to_command_and_resolved_spec(tmp_path: Path):
    source = make_source_project(tmp_path, file_count=1)
    campaign_root = tmp_path / "campaign"
    run_spec_output = tmp_path / "run-specs" / "campaign.json"
    spec_path = tmp_path / "campaign-spec.json"
    spec_path.write_text(
        json.dumps(
            {
                "sourceRoot": str(source),
                "campaignRoot": str(campaign_root),
                "runSpecOutput": str(run_spec_output),
                "planShards": {
                    "runIdPrefix": "teacher-override",
                    "runIdMode": "index",
                    "matchRegex": r"^FATEM/1\.lean$",
                    "shardSize": 1,
                },
                "watchdog": {
                    "enabled": True,
                    "pollSeconds": 5,
                    "maxActiveLaunches": 1,
                    "launchBatchSize": 1,
                    "launchCooldownSeconds": 90,
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    fake_watchdog = make_fake_watchdog_exec(tmp_path)
    env = os.environ.copy()
    env["ARCHON_WATCHDOG_EXECUTABLE"] = str(fake_watchdog)
    env["FAKE_WATCHDOG_SLEEP_SECONDS"] = "2"
    env["POLL_SECONDS"] = "17"
    env["MAX_ACTIVE_LAUNCHES"] = "3"
    env["LAUNCH_BATCH_SIZE"] = "2"
    env["LAUNCH_COOLDOWN_SECONDS"] = "11"
    env["OWNER_RESTART_BUDGET"] = "9"
    env["PROVIDER_COOLDOWN_BASE_SECONDS"] = "41"
    env["PROVIDER_COOLDOWN_STEP_SECONDS"] = "43"
    env["PROVIDER_COOLDOWN_MAX_SECONDS"] = "47"
    env["RESOURCE_SNAPSHOT_INTERVAL_SECONDS"] = "29"

    result = subprocess.run(
        [
            "python3",
            str(LAUNCH_FROM_SPEC),
            "--spec-file",
            str(spec_path),
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    resolved_spec = json.loads((campaign_root / "control" / "launch-spec.resolved.json").read_text(encoding="utf-8"))
    watchdog_command = payload["watchdog"]["command"]
    rendered_watchdog_command = " ".join(watchdog_command)

    assert payload["watchdog"]["status"] == "started"
    assert resolved_spec["watchdog"]["pollSeconds"] == "17"
    assert resolved_spec["watchdog"]["maxActiveLaunches"] == "3"
    assert resolved_spec["watchdog"]["launchBatchSize"] == "2"
    assert resolved_spec["watchdog"]["launchCooldownSeconds"] == "11"
    assert resolved_spec["watchdog"]["ownerRestartBudget"] == "9"
    assert resolved_spec["watchdog"]["providerCooldownBaseSeconds"] == "41"
    assert resolved_spec["watchdog"]["providerCooldownStepSeconds"] == "43"
    assert resolved_spec["watchdog"]["providerCooldownMaxSeconds"] == "47"
    assert resolved_spec["watchdog"]["resourceSnapshotIntervalSeconds"] == "29"
    assert "--poll-seconds 17" in rendered_watchdog_command
    assert "--max-active-launches 3" in rendered_watchdog_command
    assert "--launch-batch-size 2" in rendered_watchdog_command
    assert "--launch-cooldown-seconds 11" in rendered_watchdog_command
    assert "--owner-restart-budget 9" in rendered_watchdog_command
    assert "--provider-cooldown-base-seconds 41" in rendered_watchdog_command
    assert "--provider-cooldown-step-seconds 43" in rendered_watchdog_command
    assert "--provider-cooldown-max-seconds 47" in rendered_watchdog_command
    assert "--resource-snapshot-interval-seconds 29" in rendered_watchdog_command


def test_launch_from_spec_fails_when_watchdog_exits_immediately(tmp_path: Path):
    source = make_source_project(tmp_path, file_count=1)
    campaign_root = tmp_path / "campaign"
    run_spec_output = tmp_path / "run-specs" / "campaign.json"
    spec_path = tmp_path / "campaign-spec.json"
    spec_path.write_text(
        json.dumps(
            {
                "sourceRoot": str(source),
                "campaignRoot": str(campaign_root),
                "runSpecOutput": str(run_spec_output),
                "planShards": {
                    "runIdPrefix": "teacher-fail",
                    "runIdMode": "index",
                    "matchRegex": r"^FATEM/1\.lean$",
                    "shardSize": 1,
                },
                "watchdog": {
                    "enabled": True,
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    fake_watchdog = make_fake_watchdog_exec(tmp_path)
    env = os.environ.copy()
    env["ARCHON_WATCHDOG_EXECUTABLE"] = str(fake_watchdog)
    env["FAKE_WATCHDOG_EXIT_CODE"] = "7"
    env["FAKE_WATCHDOG_SLEEP_SECONDS"] = "0"

    result = subprocess.run(
        [
            "python3",
            str(LAUNCH_FROM_SPEC),
            "--spec-file",
            str(spec_path),
            "--shard-size",
            "1",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["watchdog"]["status"] == "failed"
    assert payload["watchdog"]["exitCode"] == 7
    assert not (campaign_root / "control" / "watchdog-launch.pid").exists()


def test_build_campaign_overview_flags_stale_watchdog_state(tmp_path: Path):
    source = make_source_project(tmp_path, file_count=1)
    campaign_root = tmp_path / "campaign"
    create_campaign(
        archon_root=ROOT,
        source_root=source,
        campaign_root=campaign_root,
        run_specs=[
            {"id": "teacher-001", "objective_regex": "^FATEM/1\\.lean$", "objective_limit": 1, "scope_hint": "FATEM/1.lean"},
        ],
    )
    write(
        campaign_root / "control" / "orchestrator-watchdog.json",
        json.dumps(
            {
                "watchdogStatus": "running",
                "restartCount": 1,
            },
            indent=2,
        ),
    )
    write(campaign_root / "control" / "watchdog-launch.pid", "999999\n")

    overview = build_campaign_overview(campaign_root, heartbeat_seconds=0)

    assert overview["watchdogStatus"] == "running"
    assert overview["watchdogRuntime"]["watchdogPidLive"] is False
    assert overview["watchdogRuntime"]["ownerLeaseLive"] is False
    assert overview["watchdogRuntime"]["stateLikelyStale"] is True

    archive_payload = archive_campaign_postmortem(campaign_root, heartbeat_seconds=0)
    assert "stale_watchdog_state" in archive_payload["incidentTags"]


def test_archive_campaign_postmortem_can_prune_rebuildable_caches(tmp_path: Path):
    source = make_source_project(tmp_path, file_count=1)
    campaign_root = tmp_path / "campaign"
    create_campaign(
        archon_root=ROOT,
        source_root=source,
        campaign_root=campaign_root,
        run_specs=[
            {"id": "teacher-001", "objective_regex": "^FATEM/1\\.lean$", "objective_limit": 1, "scope_hint": "FATEM/1.lean"},
        ],
    )

    workspace = campaign_root / "runs" / "teacher-001" / "workspace"
    write(workspace / ".archon" / "RUN_SCOPE.md", run_scope_markdown("FATEM/1.lean"))
    write(workspace / ".lake" / "build" / "artifact.bin", "x" * 128)
    write(campaign_root / "tmp.mathlib" / ".lake.prewarm-broken-20260414" / "junk", "y" * 64)

    archive_payload = archive_campaign_postmortem(
        campaign_root,
        heartbeat_seconds=0,
        prune_workspace_lake=True,
        prune_broken_prewarm=True,
    )

    assert archive_payload["cachePrune"]["selectedCount"] == 2
    assert not (workspace / ".lake").exists()
    assert not (campaign_root / "tmp.mathlib" / ".lake.prewarm-broken-20260414").exists()
    archived_summary = json.loads((campaign_root / "reports" / "postmortem" / "postmortem-summary.json").read_text(encoding="utf-8"))
    assert archived_summary["cachePrune"]["selectedCount"] == 2


def test_build_campaign_overview_and_postmortem_surface_watchdog_cooldown_and_cause(tmp_path: Path):
    source = make_source_project(tmp_path, file_count=1)
    campaign_root = tmp_path / "campaign"
    create_campaign(
        archon_root=ROOT,
        source_root=source,
        campaign_root=campaign_root,
        run_specs=[
            {"id": "teacher-001", "objective_regex": "^FATEM/1\\.lean$", "objective_limit": 1, "scope_hint": "FATEM/1.lean"},
        ],
    )
    write(
        campaign_root / "control" / "orchestrator-watchdog.json",
        json.dumps(
            {
                "watchdogStatus": "degraded",
                "restartCount": 3,
                "effectiveMaxActiveLaunches": 1,
                "providerCooldownUntil": "2026-04-14T12:00:00+00:00",
                "providerCooldownSeconds": 180,
                "likelyCause": "likely_provider_transport",
                "resourceSnapshot": {
                    "loadPerCpu": 0.2,
                    "memAvailableRatio": 0.52,
                    "swapUsedBytes": 0,
                },
            },
            indent=2,
        ),
    )

    overview = build_campaign_overview(campaign_root, heartbeat_seconds=0)

    assert overview["watchdogStatus"] == "degraded"
    assert overview["watchdogRuntime"]["effectiveMaxActiveLaunches"] == 1
    assert overview["watchdogRuntime"]["providerCooldownUntil"] == "2026-04-14T12:00:00+00:00"
    assert overview["watchdogRuntime"]["likelyCause"] == "likely_provider_transport"
    assert overview["watchdogRuntime"]["resourceSnapshot"]["loadPerCpu"] == 0.2

    archive_payload = archive_campaign_postmortem(campaign_root, heartbeat_seconds=0)
    assert "provider_transport_instability" in archive_payload["incidentTags"]
    assert archive_payload["overview"]["watchdogRuntime"]["effectiveMaxActiveLaunches"] == 1


def test_build_campaign_overview_uses_remaining_targets_for_fresh_campaign_eta(tmp_path: Path):
    source = make_source_project(tmp_path, file_count=2)
    campaign_root = tmp_path / "campaign"
    create_campaign(
        archon_root=ROOT,
        source_root=source,
        campaign_root=campaign_root,
        run_specs=[
            {
                "id": "teacher-001",
                "objective_regex": r"^(FATEM/1\.lean|FATEM/2\.lean)$",
                "objective_limit": 2,
                "scope_hint": "FATEM/1.lean, FATEM/2.lean",
            },
        ],
    )

    overview = build_campaign_overview(campaign_root, heartbeat_seconds=0)

    assert overview["runCounts"] == {"queued": 1}
    assert overview["targetCounts"]["pendingTargets"] == 0
    assert overview["targetCounts"]["remainingTargets"] == 2
    assert overview["progress"]["percent"] == 0
    assert overview["progress"]["completed"] == 0
    assert overview["progress"]["total"] == 2
    assert overview["eta"]["state"] == "unknown"
    assert overview["eta"]["reason"] != "No pending targets remain."
    assert overview["recoverableRuns"][0]["runId"] == "teacher-001"
    assert overview["recoverableRuns"][0]["remainingTargetCount"] == 2
