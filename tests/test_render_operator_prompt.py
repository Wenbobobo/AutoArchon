from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "render_operator_prompt.py"


def test_render_operator_prompt_contains_operator_file_contract(tmp_path: Path):
    result = subprocess.run(
        [
            "python3",
            str(SCRIPT),
            "--repo-root",
            str(ROOT),
            "--source-root",
            str(tmp_path / "benchmarks" / "FATE-M-upstream"),
            "--campaign-root",
            str(tmp_path / "runs" / "campaigns" / "fate-m"),
            "--template",
            str(ROOT / "campaign_specs" / "fate-m-full.json"),
            "--match-regex",
            "^FATEM/.*\\.lean$",
            "--shard-size",
            "8",
            "--run-id-mode",
            "index",
            "--run-id-prefix",
            "teacher-m",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    prompt = result.stdout
    assert "Use $archon-orchestrator to own this AutoArchon campaign." in prompt
    assert "control/mission-brief.md" in prompt
    assert "control/launch-spec.resolved.json" in prompt
    assert "control/operator-journal.md" in prompt
    assert "Match regex: ^FATEM/.*\\.lean$" in prompt
    assert "Run id prefix: teacher-m" in prompt
