from __future__ import annotations

import json
import subprocess
import textwrap
from datetime import datetime, timezone
from pathlib import Path

from archonlib.campaign import (
    build_orchestrator_prompt,
    campaign_is_terminal,
    collect_campaign_status,
    create_campaign,
    execute_run_recovery,
    finalize_campaign,
    plan_campaign_shards,
)


ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN_RECOVER = ROOT / "scripts" / "campaign_recover.py"
CAMPAIGN_COMPARE = ROOT / "scripts" / "campaign_compare.py"
PLAN_CAMPAIGN_SHARDS = ROOT / "scripts" / "plan_campaign_shards.py"


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
            "id": "teacher-007",
            "objective_limit": 1,
            "objective_regex": "^(FATEM/1\\.lean)$",
            "scope_hint": "FATEM/1.lean",
        },
        {
            "id": "teacher-008",
            "objective_limit": 1,
            "objective_regex": "^(FATEM/2\\.lean)$",
            "scope_hint": "FATEM/2.lean",
        },
        {
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
            "id": "teacher-1",
            "objective_limit": 1,
            "objective_regex": "^(FATEM/1\\.lean)$",
            "scope_hint": "FATEM/1.lean",
        },
        {
            "id": "teacher-2",
            "objective_limit": 1,
            "objective_regex": "^(FATEM/2\\.lean)$",
            "scope_hint": "FATEM/2.lean",
        },
        {
            "id": "teacher-3",
            "objective_limit": 1,
            "objective_regex": "^(FATEM/3\\.lean)$",
            "scope_hint": "FATEM/3.lean",
        },
    ]


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
            "id": "micro-001",
            "objective_limit": 2,
            "objective_regex": "^(FATEM/1\\.lean|FATEM/2\\.lean)$",
            "scope_hint": "FATEM/1.lean, FATEM/2.lean",
        },
        {
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
            "id": "teacher-1",
            "objective_limit": 1,
            "objective_regex": "^(FATEM/1\\.lean)$",
            "scope_hint": "FATEM/1.lean",
        },
        {
            "id": "teacher-2",
            "objective_limit": 1,
            "objective_regex": "^(FATEM/2\\.lean)$",
            "scope_hint": "FATEM/2.lean",
        },
        {
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
    assert (campaign_root / "CAMPAIGN_MANIFEST.json").exists()
    assert (campaign_root / "campaign-status.json").exists()
    events = (campaign_root / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(events) == 3

    run_root = campaign_root / "runs" / "teacher-a"
    assert (run_root / "source" / "FATEM" / "1.lean").exists()
    assert (run_root / "workspace" / "FATEM" / "1.lean").exists()
    assert (run_root / "workspace" / ".lake" / "packages" / "mathlib" / "README").exists()
    assert (run_root / "artifacts").exists()
    prompt = (run_root / "control" / "teacher-prompt.txt").read_text(encoding="utf-8")
    launch_script = (run_root / "control" / "launch-teacher.sh").read_text(encoding="utf-8")
    assert "Use $archon-supervisor" in prompt
    assert "AutoArchon run" in prompt
    assert "autoarchon-supervised-cycle" in prompt
    assert "codex exec" in launch_script
    assert "--skip-mcp" in launch_script
    assert "--model gpt-5.4" in launch_script
    assert "teacher-launch-state.json" in launch_script
    assert "autoarchon-prewarm-project" in launch_script


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
                "supervisorPid": 99999,
                "loopPid": 88888,
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
    assert status["counts"]["accepted"] == 1
    assert status["counts"]["blocked"] == 1
    assert status["counts"]["running"] == 1
    assert status["counts"]["unverified"] == 1
    assert status["counts"]["contaminated"] == 1
    assert status["counts"]["needs_relaunch"] == 1


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
                "updatedAt": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
        ),
    )

    status = collect_campaign_status(campaign_root, heartbeat_seconds=60)
    run = status["runs"][0]

    assert run["status"] == "running"
    assert run["runningSignal"] is True
    assert run["launchStatePresent"] is True
    assert run["recommendedRecovery"]["action"] == "none"
    assert status["counts"]["running"] == 1


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
    assert run["launchStatePresent"] is True
    assert run["recommendedRecovery"]["action"] == "relaunch_teacher"
    assert status["counts"]["needs_relaunch"] == 1


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
    assert compare_report["targetCounts"]["acceptedProofs"] == 1
    assert compare_report["targetCounts"]["acceptedBlockers"] == 1
    assert compare_report["targetCounts"]["unverifiedArtifacts"] == 1
    compare_markdown = (final_root / "compare-report.md").read_text(encoding="utf-8")
    assert "| run | status | proofs | blockers | unverified |" in compare_markdown


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
    assert payload["targetCounts"]["acceptedProofs"] == 1
    assert (campaign_root / "reports" / "final" / "compare-report.json").exists()
