from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib


ROOT = Path(__file__).resolve().parents[1]


def test_pyproject_declares_core_autoarchon_entrypoints():
    payload = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = payload["project"]["scripts"]

    assert {
        "autoarchon-campaign-status",
        "autoarchon-campaign-recover",
        "autoarchon-campaign-compare",
        "autoarchon-finalize-campaign",
        "autoarchon-orchestrator-watchdog",
        "autoarchon-run-orchestrator",
        "autoarchon-supervised-cycle",
        "autoarchon-export-run-artifacts",
        "autoarchon-plan-shards",
        "autoarchon-create-campaign",
    } <= set(scripts)


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is required for AutoArchon public entrypoint smoke tests")
def test_uv_run_help_smokes_for_core_entrypoints():
    commands = [
        ("autoarchon-campaign-status", "campaign-root"),
        ("autoarchon-campaign-recover", "campaign-root"),
        ("autoarchon-orchestrator-watchdog", "campaign-root"),
        ("autoarchon-supervised-cycle", "workspace"),
    ]

    for command_name, expected_flag in commands:
        result = subprocess.run(
            ["uv", "run", "--directory", str(ROOT), command_name, "--help"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 0, result.stderr
        assert expected_flag in result.stdout
