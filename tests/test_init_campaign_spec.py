from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "init_campaign_spec.py"


def test_init_campaign_spec_writes_resolved_launch_spec(tmp_path: Path):
    benchmark_root = tmp_path / "benchmarks"
    campaigns_root = tmp_path / "campaigns"
    run_specs_root = tmp_path / "run-specs"
    benchmark_root.mkdir()
    campaigns_root.mkdir()
    run_specs_root.mkdir()

    result = subprocess.run(
        [
            "python3",
            str(SCRIPT),
            "--template",
            str(ROOT / "campaign_specs" / "fate-m-full.json"),
            "--benchmark-root",
            str(benchmark_root),
            "--campaigns-root",
            str(campaigns_root),
            "--run-specs-root",
            str(run_specs_root),
            "--date-tag",
            "20260414-nightly",
            "--model",
            "gpt-5.4",
            "--reasoning-effort",
            "xhigh",
            "--shard-size",
            "4",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    output_path = run_specs_root / "20260414-nightly-fate-m-full.launch.json"
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["campaignRoot"] == str((campaigns_root / "20260414-nightly-fate-m-full").resolve())
    assert payload["sourceRoot"] == str((benchmark_root / "FATE-M-upstream").resolve())
    assert payload["runSpecOutput"] == str((run_specs_root / "20260414-nightly-fate-m-full.json").resolve())
    assert payload["teacherModel"] == "gpt-5.4"
    assert payload["teacherReasoningEffort"] == "xhigh"
    assert payload["planShards"]["shardSize"] == 4
    assert payload["watchdog"]["pruneWorkspaceLake"] is True
    assert payload["watchdog"]["pruneBrokenPrewarm"] is True
    mission_brief = campaigns_root / "20260414-nightly-fate-m-full" / "control" / "mission-brief.md"
    operator_journal = campaigns_root / "20260414-nightly-fate-m-full" / "control" / "operator-journal.md"
    assert mission_brief.exists()
    assert operator_journal.exists()
    assert "Mission Brief" in mission_brief.read_text(encoding="utf-8")
    assert "autoarchon-init-campaign-spec" in operator_journal.read_text(encoding="utf-8")


def test_init_campaign_spec_drops_unresolved_optional_environment_entries(tmp_path: Path):
    run_specs_root = tmp_path / "run-specs"
    result = subprocess.run(
        [
            "python3",
            str(SCRIPT),
            "--template",
            str(ROOT / "campaign_specs" / "fate-m-full.json"),
            "--benchmark-root",
            str(tmp_path / "benchmarks"),
            "--campaigns-root",
            str(tmp_path / "campaigns"),
            "--run-specs-root",
            str(run_specs_root),
            "--date-tag",
            "20260414-nightly",
            "--dry-run",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert "environment" not in payload
    assert not (tmp_path / "campaigns" / "20260414-nightly-fate-m-full" / "control" / "mission-brief.md").exists()


def test_init_campaign_spec_supports_generic_source_roots_and_formalization_template(tmp_path: Path):
    source_roots_root = tmp_path / "sources"
    campaigns_root = tmp_path / "campaigns"
    run_specs_root = tmp_path / "run-specs"
    source_roots_root.mkdir()
    campaigns_root.mkdir()
    run_specs_root.mkdir()

    result = subprocess.run(
        [
            "python3",
            str(SCRIPT),
            "--template",
            str(ROOT / "campaign_specs" / "formalization-default.json"),
            "--source-roots-root",
            str(source_roots_root),
            "--campaigns-root",
            str(campaigns_root),
            "--run-specs-root",
            str(run_specs_root),
            "--date-tag",
            "20260415-open",
            "--dry-run",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["sourceRoot"] == str((source_roots_root / "formalization-upstream").resolve())
    assert payload["reuseLakeFrom"] == str((source_roots_root / "formalization-upstream").resolve())
    assert payload["preloadHistoricalRoutes"] is True


def test_init_campaign_spec_allows_generic_campaign_slug_and_source_subdir_overrides(tmp_path: Path):
    source_roots_root = tmp_path / "sources"
    campaigns_root = tmp_path / "campaigns"
    run_specs_root = tmp_path / "run-specs"
    source_roots_root.mkdir()
    campaigns_root.mkdir()
    run_specs_root.mkdir()

    result = subprocess.run(
        [
            "python3",
            str(SCRIPT),
            "--template",
            str(ROOT / "campaign_specs" / "formalization-default.json"),
            "--source-roots-root",
            str(source_roots_root),
            "--campaigns-root",
            str(campaigns_root),
            "--run-specs-root",
            str(run_specs_root),
            "--date-tag",
            "20260415-open",
            "--campaign-slug",
            "riemann-local-formalization",
            "--source-subdir",
            "riemann-upstream",
            "--dry-run",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["campaignRoot"] == str((campaigns_root / "20260415-open-riemann-local-formalization").resolve())
    assert payload["runSpecOutput"] == str((run_specs_root / "20260415-open-riemann-local-formalization.json").resolve())
    assert payload["sourceRoot"] == str((source_roots_root / "riemann-upstream").resolve())
    assert payload["reuseLakeFrom"] == str((source_roots_root / "riemann-upstream").resolve())


def test_init_campaign_spec_supports_open_problem_template(tmp_path: Path):
    source_roots_root = tmp_path / "sources"
    campaigns_root = tmp_path / "campaigns"
    run_specs_root = tmp_path / "run-specs"
    source_roots_root.mkdir()
    campaigns_root.mkdir()
    run_specs_root.mkdir()

    result = subprocess.run(
        [
            "python3",
            str(SCRIPT),
            "--template",
            str(ROOT / "campaign_specs" / "open-problem-default.json"),
            "--source-roots-root",
            str(source_roots_root),
            "--campaigns-root",
            str(campaigns_root),
            "--run-specs-root",
            str(run_specs_root),
            "--date-tag",
            "20260416-riemann",
            "--source-subdir",
            "riemann-upstream",
            "--dry-run",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["campaignMode"] == "open_problem"
    assert payload["campaignRoot"] == str((campaigns_root / "20260416-riemann-open-problem-default").resolve())
    assert payload["sourceRoot"] == str((source_roots_root / "riemann-upstream").resolve())
    assert payload["reuseLakeFrom"] == str((source_roots_root / "riemann-upstream").resolve())
    assert payload["preloadHistoricalRoutes"] is True
    assert payload["planShards"]["runIdPrefix"] == "teacher-o"
