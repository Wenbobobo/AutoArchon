from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "init_operator_intake.py"


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def make_source_project(tmp_path: Path) -> Path:
    source = tmp_path / "source-project"
    write(source / "lakefile.lean", "import Lake\n")
    write(source / "lean-toolchain", "leanprover/lean4:v4.28.0\n")
    write(source / "FATEM" / "1.lean", "theorem foo : True := by\n  sorry\n")
    return source


def test_init_operator_intake_writes_control_bundle_and_validates(tmp_path: Path):
    source = make_source_project(tmp_path)
    campaign_root = tmp_path / "campaign"

    result = subprocess.run(
        [
            "python3",
            str(SCRIPT),
            "--repo-root",
            str(ROOT),
            "--campaign-root",
            str(campaign_root),
            "--source-root",
            str(source),
            "--objective",
            "Run a benchmark-faithful FATEM smoke campaign on the local warmed clone.",
            "--campaign-mode",
            "benchmark_faithful",
            "--success-criterion",
            "Produce validation-backed accepted proofs or accepted blockers for the scoped targets.",
            "--constraint",
            "Do not widen scope beyond FATEM/1.lean.",
            "--watch-item",
            "Theorem mutation or repeated no-progress loops.",
            "--match-regex",
            "^FATEM/1\\.lean$",
            "--shard-size",
            "1",
            "--run-id-prefix",
            "teacher-m",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["launchContractValidation"]["valid"] is True
    mission_brief = campaign_root / "control" / "mission-brief.md"
    operator_journal = campaign_root / "control" / "operator-journal.md"
    resolved_spec_path = campaign_root / "control" / "launch-spec.resolved.json"
    assert mission_brief.exists()
    assert operator_journal.exists()
    assert resolved_spec_path.exists()
    mission_text = mission_brief.read_text(encoding="utf-8")
    journal_text = operator_journal.read_text(encoding="utf-8")
    resolved_spec = json.loads(resolved_spec_path.read_text(encoding="utf-8"))
    assert "Run a benchmark-faithful FATEM smoke campaign" in mission_text
    assert "Produce validation-backed accepted proofs" in mission_text
    assert "Do not widen scope beyond FATEM/1.lean." in mission_text
    assert "Theorem mutation or repeated no-progress loops." in mission_text
    assert "reviewed intake scaffold" in journal_text
    assert resolved_spec["campaignMode"] == "benchmark_faithful"
    assert resolved_spec["preloadHistoricalRoutes"] is False
    assert resolved_spec["planShards"]["matchRegex"] == "^FATEM/1\\.lean$"
    assert resolved_spec["planShards"]["shardSize"] == 1
    assert resolved_spec["planShards"]["runIdPrefix"] == "teacher-m"


def test_init_operator_intake_supports_open_problem_mode_and_force_refresh(tmp_path: Path):
    source = make_source_project(tmp_path)
    campaign_root = tmp_path / "campaign"

    first = subprocess.run(
        [
            "python3",
            str(SCRIPT),
            "--repo-root",
            str(ROOT),
            "--campaign-root",
            str(campaign_root),
            "--source-root",
            str(source),
            "--objective",
            "Initial objective.",
            "--campaign-mode",
            "open_problem",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert first.returncode == 0, first.stderr

    second = subprocess.run(
        [
            "python3",
            str(SCRIPT),
            "--repo-root",
            str(ROOT),
            "--campaign-root",
            str(campaign_root),
            "--source-root",
            str(source),
            "--objective",
            "Refined open-problem objective.",
            "--campaign-mode",
            "open_problem",
            "--force",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    assert second.returncode == 0, second.stderr
    resolved_spec = json.loads((campaign_root / "control" / "launch-spec.resolved.json").read_text(encoding="utf-8"))
    mission_text = (campaign_root / "control" / "mission-brief.md").read_text(encoding="utf-8")
    assert resolved_spec["campaignMode"] == "open_problem"
    assert resolved_spec["preloadHistoricalRoutes"] is True
    assert "Refined open-problem objective." in mission_text
