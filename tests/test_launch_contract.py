from __future__ import annotations

import json
import subprocess
from pathlib import Path

from archonlib.launch_contract import validate_launch_contract


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate_launch_contract.py"


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def make_source_project(tmp_path: Path, *, with_fatem_scope: bool = True) -> Path:
    source = tmp_path / "source-project"
    write(source / "lakefile.lean", "import Lake\n")
    write(source / "lean-toolchain", "leanprover/lean4:v4.28.0\n")
    if with_fatem_scope:
        write(source / "FATEM" / "42.lean", "theorem file_42 : True := by\n  sorry\n")
    else:
        write(source / "Example" / "Intro.lean", "theorem example_intro : True := by\n  sorry\n")
    return source


def make_campaign_with_control_files(tmp_path: Path, *, spec: dict[str, object], mission_brief: str, journal: str) -> Path:
    campaign_root = Path(str(spec["campaignRoot"]))
    control_root = campaign_root / "control"
    control_root.mkdir(parents=True, exist_ok=True)
    write(control_root / "launch-spec.resolved.json", json.dumps(spec, indent=2, sort_keys=True) + "\n")
    write(control_root / "mission-brief.md", mission_brief)
    write(control_root / "operator-journal.md", journal)
    return campaign_root


def test_validate_launch_contract_reports_scaffold_warning_and_helper_env_warning(tmp_path: Path):
    source = make_source_project(tmp_path)
    campaign_root = tmp_path / "campaign"
    repo_root = tmp_path / "repo"
    (repo_root / "examples").mkdir(parents=True)
    spec = {
        "campaignRoot": str(campaign_root),
        "sourceRoot": str(source),
        "campaignMode": "benchmark_faithful",
        "planShards": {"matchRegex": r"^FATEM/.*\.lean$"},
    }
    make_campaign_with_control_files(
        tmp_path,
        spec=spec,
        mission_brief="# Mission Brief\n\n> Replace the placeholders below before long unattended campaigns.\n",
        journal="# Operator Journal\n\n- Append a new timestamped block for every launch.\n",
    )

    result = subprocess.run(
        ["python3", str(SCRIPT), "--campaign-root", str(campaign_root), "--repo-root", str(repo_root)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["valid"] is True
    assert payload["detectedMode"] == "benchmark_faithful"
    warning_codes = {item["code"] for item in payload["warnings"]}
    assert "mission_brief_scaffolded" in warning_codes
    assert "helper_env_missing" in warning_codes


def test_validate_launch_contract_rejects_preload_routes_for_benchmark_mode(tmp_path: Path):
    source = make_source_project(tmp_path)
    campaign_root = tmp_path / "campaign"
    spec = {
        "campaignRoot": str(campaign_root),
        "sourceRoot": str(source),
        "campaignMode": "benchmark_faithful",
        "preloadHistoricalRoutes": True,
        "planShards": {"matchRegex": r"^FATEM/.*\.lean$"},
    }
    make_campaign_with_control_files(
        tmp_path,
        spec=spec,
        mission_brief="# Mission Brief\n\nReal objective.\n",
        journal="# Operator Journal\n\n## 2026-04-15T00:00:00+00:00\n\n- Decision: reviewed.\n",
    )

    result = subprocess.run(
        ["python3", str(SCRIPT), "--campaign-root", str(campaign_root), "--repo-root", str(ROOT)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload["valid"] is False
    assert {item["code"] for item in payload["errors"]} == {"historical_routes_forbidden"}


def test_validate_launch_contract_rejects_scope_mismatch_when_regex_matches_no_files(tmp_path: Path):
    source = make_source_project(tmp_path, with_fatem_scope=False)
    campaign_root = tmp_path / "campaign"
    spec = {
        "campaignRoot": str(campaign_root),
        "sourceRoot": str(source),
        "campaignMode": "benchmark_faithful",
        "planShards": {"matchRegex": r"^FATEM/.*\.lean$"},
    }
    make_campaign_with_control_files(
        tmp_path,
        spec=spec,
        mission_brief="# Mission Brief\n\nReal objective.\n",
        journal="# Operator Journal\n\n## 2026-04-15T00:00:00+00:00\n\n- Decision: reviewed.\n",
    )

    result = subprocess.run(
        ["python3", str(SCRIPT), "--campaign-root", str(campaign_root), "--repo-root", str(ROOT)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert "scope_regex_matches_no_files" in {item["code"] for item in payload["errors"]}


def test_validate_launch_contract_detects_formalization_mode_and_accepts_preload_routes(tmp_path: Path):
    source = make_source_project(tmp_path, with_fatem_scope=False)
    campaign_root = tmp_path / "campaign"
    spec = {
        "campaignRoot": str(campaign_root),
        "sourceRoot": str(source),
        "campaignMode": "formalization",
        "preloadHistoricalRoutes": True,
        "planShards": {"matchRegex": r"^.*\.lean$"},
        "environment": {"ARCHON_HELPER_ENABLE": "0"},
    }
    make_campaign_with_control_files(
        tmp_path,
        spec=spec,
        mission_brief="# Mission Brief\n\nFormalize the target statements without benchmark-faithful restrictions.\n",
        journal="# Operator Journal\n\n## 2026-04-15T00:00:00+00:00\n\n- Decision: reviewed formalization run.\n",
    )

    result = subprocess.run(
        ["python3", str(SCRIPT), "--campaign-root", str(campaign_root), "--repo-root", str(ROOT)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["valid"] is True
    assert payload["detectedMode"] == "formalization"
    assert {item["code"] for item in payload["warnings"]} == set()


def test_validate_launch_contract_warns_on_helper_model_provider_mismatch(tmp_path: Path):
    source = make_source_project(tmp_path)
    campaign_root = tmp_path / "campaign"
    repo_root = tmp_path / "repo"
    (repo_root / "examples").mkdir(parents=True)
    write(
        repo_root / "examples" / "helper.env",
        "\n".join(
            [
                "ARCHON_HELPER_ENABLE=1",
                "ARCHON_HELPER_PROVIDER=openai",
                "ARCHON_HELPER_MODEL=gemini-2.5-pro",
            ]
        )
        + "\n",
    )
    spec = {
        "campaignRoot": str(campaign_root),
        "sourceRoot": str(source),
        "campaignMode": "formalization",
        "planShards": {"matchRegex": r"^FATEM/.*\.lean$"},
    }
    make_campaign_with_control_files(
        tmp_path,
        spec=spec,
        mission_brief="# Mission Brief\n\nReal objective.\n",
        journal="# Operator Journal\n\n## 2026-04-15T00:00:00+00:00\n\n- Decision: reviewed.\n",
    )

    payload = validate_launch_contract(campaign_root, repo_root=repo_root)

    assert payload["valid"] is True
    assert "helper_model_provider_mismatch" in {item["code"] for item in payload["warnings"]}


def test_validate_launch_contract_can_probe_helper_transport(monkeypatch, tmp_path: Path):
    source = make_source_project(tmp_path)
    campaign_root = tmp_path / "campaign"
    repo_root = tmp_path / "repo"
    (repo_root / "examples").mkdir(parents=True)
    write(
        repo_root / "examples" / "helper.env",
        "\n".join(
            [
                "ARCHON_HELPER_ENABLE=1",
                "ARCHON_HELPER_PROVIDER=openai",
                "ARCHON_HELPER_MODEL=gpt-5.4",
            ]
        )
        + "\n",
    )
    spec = {
        "campaignRoot": str(campaign_root),
        "sourceRoot": str(source),
        "campaignMode": "formalization",
        "planShards": {"matchRegex": r"^FATEM/.*\.lean$"},
    }
    make_campaign_with_control_files(
        tmp_path,
        spec=spec,
        mission_brief="# Mission Brief\n\nReal objective.\n",
        journal="# Operator Journal\n\n## 2026-04-15T00:00:00+00:00\n\n- Decision: reviewed.\n",
    )

    monkeypatch.setattr(
        "archonlib.launch_contract.probe_helper_transport",
        lambda **kwargs: {
            "status": "failed",
            "classification": "invalid_credentials",
            "message": "Incorrect API key provided: [redacted]",
            "provider": "openai",
            "model": "gpt-5.4",
            "enabled": True,
        },
    )

    payload = validate_launch_contract(
        campaign_root,
        repo_root=repo_root,
        probe_helper=True,
        helper_probe_timeout_seconds=9,
    )

    assert payload["valid"] is True
    assert payload["helperProbe"]["classification"] == "invalid_credentials"
    assert "helper_probe_failed" in {item["code"] for item in payload["warnings"]}
