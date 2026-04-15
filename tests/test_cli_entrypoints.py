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
        "autoarchon-campaign-archive",
        "autoarchon-campaign-observe",
        "autoarchon-campaign-status",
        "autoarchon-campaign-overview",
        "autoarchon-campaign-recover",
        "autoarchon-campaign-compare",
        "autoarchon-clean-launchers",
        "autoarchon-finalize-campaign",
        "autoarchon-init-campaign-spec",
        "autoarchon-init-operator-intake",
        "autoarchon-lesson-clusters",
        "autoarchon-launch-from-spec",
        "autoarchon-materialize-problem-pack",
        "autoarchon-orchestrator-watchdog",
        "autoarchon-run-orchestrator",
        "autoarchon-supervised-cycle",
        "autoarchon-export-run-artifacts",
        "autoarchon-plan-shards",
        "autoarchon-create-campaign",
        "autoarchon-prewarm-project",
        "autoarchon-refresh-launch-assets",
        "autoarchon-render-operator-prompt",
        "autoarchon-storage-report",
        "autoarchon-validate-launch-contract",
    } <= set(scripts)


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is required for AutoArchon public entrypoint smoke tests")
def test_uv_run_help_smokes_for_core_entrypoints():
    commands = [
        ("autoarchon-campaign-archive", "prune-workspace-lake"),
        ("autoarchon-campaign-observe", "refresh-seconds"),
        ("autoarchon-campaign-overview", "markdown"),
        ("autoarchon-campaign-status", "campaign-root"),
        ("autoarchon-campaign-recover", "campaign-root"),
        ("autoarchon-clean-launchers", "duplicate-grace-seconds"),
        ("autoarchon-finalize-campaign", "prune-workspace-lake"),
        ("autoarchon-init-campaign-spec", "template"),
        ("autoarchon-init-operator-intake", "objective"),
        ("autoarchon-lesson-clusters", "campaign-root"),
        ("autoarchon-launch-from-spec", "dry-run"),
        ("autoarchon-materialize-problem-pack", "input-json"),
        ("autoarchon-orchestrator-watchdog", "prune-workspace-lake"),
        ("autoarchon-render-operator-prompt", "repo-root"),
        ("autoarchon-refresh-launch-assets", "refresh-prompts"),
        ("autoarchon-storage-report", "retention"),
        ("autoarchon-supervised-cycle", "workspace"),
        ("autoarchon-prewarm-project", "verify-file"),
        ("autoarchon-validate-launch-contract", "campaign-root"),
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
