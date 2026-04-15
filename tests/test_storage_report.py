from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from archonlib.storage import build_retention_report, build_storage_report, prune_storage_candidates


ROOT = Path(__file__).resolve().parents[1]
STORAGE_REPORT = ROOT / "scripts" / "storage_report.py"


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def make_run_root(
    root: Path,
    name: str,
    *,
    active: bool,
    supervisor_pid: int | None = None,
    loop_pid: int | None = None,
    heartbeat_age_seconds: int | None = None,
) -> Path:
    run_root = root / name
    write(run_root / "RUN_MANIFEST.json", "{}\n")
    write(run_root / "workspace" / ".lake" / "build" / "artifact.bin", "x" * 128)
    last_heartbeat = None
    started_at = None
    if heartbeat_age_seconds is not None:
        last_heartbeat = (datetime.now(timezone.utc) - timedelta(seconds=heartbeat_age_seconds)).isoformat()
        started_at = last_heartbeat
    lease_payload = {
        "active": active,
        "supervisorPid": supervisor_pid if supervisor_pid is not None else (999999 if active else None),
        "loopPid": loop_pid,
        "lastHeartbeatAt": last_heartbeat,
        "startedAt": started_at,
        "workspace": str(run_root / "workspace"),
    }
    write(
        run_root / "workspace" / ".archon" / "supervisor" / "run-lease.json",
        json.dumps(lease_payload, indent=2) + "\n",
    )
    return run_root


def test_build_storage_report_finds_workspace_lake_and_broken_prewarm(tmp_path: Path):
    make_run_root(tmp_path, "run-a", active=False)
    make_run_root(tmp_path, "run-b", active=False)
    write(tmp_path / "tmp.mathlib" / ".lake.prewarm-broken-20260414" / "junk", "y" * 64)

    payload = build_storage_report(tmp_path)

    assert payload["workspaceLakeCount"] == 2
    assert payload["legacyWorkspaceLakeCount"] == 0
    assert payload["brokenPrewarmCount"] == 1
    assert payload["candidateCount"] == 3
    assert payload["reclaimableBytes"] > 0
    assert payload["reclaimableCount"] == 3
    assert payload["staleActiveLeaseCount"] == 0
    assert any(item["kind"] == "workspace_lake" for item in payload["candidates"])
    assert any(item["kind"] == "broken_prewarm" for item in payload["candidates"])


def test_prune_storage_candidates_removes_selected_safe_directories(tmp_path: Path):
    inactive_run = make_run_root(tmp_path, "run-a", active=False)
    active_run = make_run_root(tmp_path, "run-b", active=True, heartbeat_age_seconds=60)
    broken = tmp_path / "cache" / ".lake.prewarm-broken-20260414"
    write(broken / "junk", "z" * 64)

    payload = prune_storage_candidates(
        tmp_path,
        prune_workspace_lake=True,
        prune_broken_prewarm=True,
        execute=True,
    )

    assert payload["selectedCount"] == 2
    assert payload["reclaimedBytes"] > 0
    assert not (inactive_run / "workspace" / ".lake").exists()
    assert (active_run / "workspace" / ".lake").exists()
    assert not broken.exists()


def test_storage_report_treats_stale_active_lease_without_live_pid_as_reclaimable(tmp_path: Path):
    stale_run = make_run_root(tmp_path, "run-a", active=True, heartbeat_age_seconds=7200)
    recent_run = make_run_root(tmp_path, "run-b", active=True, heartbeat_age_seconds=60)

    payload = build_storage_report(tmp_path)
    candidates = {Path(item["run_root"]).name: item for item in payload["candidates"] if item.get("run_root")}

    assert candidates["run-a"]["safe_to_prune"] is True
    assert candidates["run-a"]["reason"] == "stale active lease without live pid"
    assert candidates["run-b"]["safe_to_prune"] is False
    assert candidates["run-b"]["reason"] == "recent active lease without live pid"
    assert payload["staleActiveLeaseCount"] == 1
    assert payload["protectedActiveCount"] == 1
    assert payload["reclaimableCount"] == 1
    assert not (stale_run / "workspace" / ".lake").samefile(recent_run / "workspace" / ".lake")


def test_storage_report_protects_run_cache_when_live_process_references_run_root(tmp_path: Path, monkeypatch):
    run_root = make_run_root(tmp_path, "run-a", active=False)
    monkeypatch.setattr("archonlib.storage._path_referenced_by_live_process", lambda path, *, hints=None: "run-a" in str(path))

    payload = build_storage_report(tmp_path)
    candidate = next(item for item in payload["candidates"] if item.get("run_root") == str(run_root))

    assert candidate["safe_to_prune"] is False
    assert candidate["reason"] == "live process references run root"


def test_storage_report_detects_legacy_workspace_lake_candidates(tmp_path: Path, monkeypatch):
    legacy_root = tmp_path / "legacy-workspace"
    write(legacy_root / ".archon" / "PROGRESS.md", "done\n")
    write(legacy_root / ".lake" / "build" / "artifact.bin", "x" * 128)
    monkeypatch.setattr("archonlib.storage._path_referenced_by_live_process", lambda path, *, hints=None: False)

    payload = build_storage_report(tmp_path)

    assert payload["legacyWorkspaceLakeCount"] == 1
    candidate = next(item for item in payload["candidates"] if item["kind"] == "legacy_workspace_lake")
    assert candidate["safe_to_prune"] is True
    assert candidate["reason"] == "inactive legacy workspace cache"


def test_prune_storage_candidates_removes_legacy_workspace_lake(tmp_path: Path, monkeypatch):
    legacy_root = tmp_path / "legacy-workspace"
    write(legacy_root / ".archon" / "PROGRESS.md", "done\n")
    write(legacy_root / ".lake" / "build" / "artifact.bin", "x" * 128)
    monkeypatch.setattr("archonlib.storage._path_referenced_by_live_process", lambda path, *, hints=None: False)

    payload = prune_storage_candidates(
        tmp_path,
        prune_workspace_lake=True,
        prune_broken_prewarm=False,
        execute=True,
    )

    assert payload["selectedCount"] == 1
    assert payload["selected"][0]["kind"] == "legacy_workspace_lake"
    assert not (legacy_root / ".lake").exists()
    assert (legacy_root / ".archon").exists()


def test_build_retention_report_classifies_top_level_roots(tmp_path: Path, monkeypatch):
    legacy_root = tmp_path / "legacy-workspace"
    write(legacy_root / ".archon" / "PROGRESS.md", "done\n")
    write(legacy_root / ".lake" / "build" / "artifact.bin", "x" * 128)
    campaign_root = tmp_path / "campaign-a"
    write(campaign_root / "CAMPAIGN_MANIFEST.json", "{}\n")
    write(campaign_root / "reports" / "final" / "final-summary.json", "{}\n")
    benchmarks_root = tmp_path / "benchmarks"
    write(benchmarks_root / "FATE-M-upstream" / "lean-toolchain", "leanprover/lean4:v4.28.0\n")
    write(benchmarks_root / "FATE-M-upstream" / "lakefile.lean", "import Lake\n")
    monkeypatch.setattr("archonlib.storage._path_referenced_by_live_process", lambda path, *, hints=None: False)

    payload = build_retention_report(tmp_path)
    rows = {row["name"]: row for row in payload["rows"]}

    assert rows["legacy-workspace"]["kind"] == "legacy_workspace_root"
    assert rows["legacy-workspace"]["suggestedAction"] == "prune_cache_only"
    assert rows["campaign-a"]["kind"] == "campaign_root"
    assert rows["campaign-a"]["suggestedAction"] == "archive_or_delete_root"
    assert rows["benchmarks"]["kind"] == "benchmarks_root"
    assert rows["benchmarks"]["suggestedAction"] == "inspect_children"
    assert payload["benchmarkCloneCount"] == 1
    assert payload["benchmarkCloneRows"][0]["name"] == "FATE-M-upstream"
    assert payload["benchmarkCloneRows"][0]["suggestedAction"] == "keep_shared_clone"
    assert payload["benchmarkCloneRows"][0]["lakeBytes"] == 0


def test_build_retention_report_surfaces_benchmark_clone_lake_bytes(tmp_path: Path, monkeypatch):
    benchmarks_root = tmp_path / "benchmarks"
    write(benchmarks_root / "FATE-M-upstream" / "lean-toolchain", "leanprover/lean4:v4.28.0\n")
    write(benchmarks_root / "FATE-M-upstream" / "lakefile.lean", "import Lake\n")
    write(benchmarks_root / "FATE-M-upstream" / ".lake" / "build" / "artifact.bin", "x" * 256)
    monkeypatch.setattr("archonlib.storage._path_referenced_by_live_process", lambda path, *, hints=None: False)

    payload = build_retention_report(tmp_path)

    assert payload["benchmarkCloneCount"] == 1
    clone = payload["benchmarkCloneRows"][0]
    assert clone["name"] == "FATE-M-upstream"
    assert clone["lakeBytes"] >= 256
    assert clone["hasLake"] is True
    assert clone["emergencyAction"] == "prune_clone_lake_only"


def test_storage_report_cli_prune_dry_run_outputs_selected_candidates(tmp_path: Path):
    make_run_root(tmp_path, "run-a", active=False)

    result = subprocess.run(
        [
            "python3",
            str(STORAGE_REPORT),
            "--root",
            str(tmp_path),
            "--prune-workspace-lake",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["execute"] is False
    assert payload["selectedCount"] == 1


def test_storage_report_cli_retention_outputs_top_level_rows(tmp_path: Path):
    legacy_root = tmp_path / "legacy-workspace"
    write(legacy_root / ".archon" / "PROGRESS.md", "done\n")
    write(legacy_root / ".lake" / "build" / "artifact.bin", "x" * 128)

    result = subprocess.run(
        [
            "python3",
            str(STORAGE_REPORT),
            "--root",
            str(tmp_path),
            "--retention",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["entryCount"] == 1
    assert payload["rows"][0]["kind"] == "legacy_workspace_root"


def test_storage_report_cli_execute_markdown_renders_post_prune_report(tmp_path: Path):
    run_root = make_run_root(tmp_path, "run-a", active=False)

    result = subprocess.run(
        [
            "python3",
            str(STORAGE_REPORT),
            "--root",
            str(tmp_path),
            "--prune-workspace-lake",
            "--execute",
            "--markdown",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "# Storage Prune" in result.stdout
    assert "## Post-Prune Report" in result.stdout
    assert str(run_root / "workspace" / ".lake") in result.stdout
    assert "## Largest Candidates\n\n- none" in result.stdout
    assert not (run_root / "workspace" / ".lake").exists()
