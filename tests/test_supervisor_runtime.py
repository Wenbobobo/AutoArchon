from __future__ import annotations

import json
import os
import subprocess
import textwrap
from pathlib import Path

from archonlib.run_workspace import create_isolated_run, export_run_artifacts
from archonlib.supervisor import (
    classify_header_mutation,
    collect_header_drifts,
    parse_allowed_files,
)


ROOT = Path(__file__).resolve().parents[1]
SUPERVISED_CYCLE = ROOT / "scripts" / "supervised_cycle.py"
INSTALL_REPO_SKILL = ROOT / "scripts" / "install_repo_skill.sh"


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")


def make_source_project(tmp_path: Path) -> Path:
    source = tmp_path / "source-project"
    write(source / "lakefile.lean", "import Lake\n")
    write(source / "lean-toolchain", "leanprover/lean4:v4.28.0\n")
    write(
        source / "FATEM" / "42.lean",
        """
        import Mathlib

        theorem orderOf_prod_lt_orderOf_mul (G H : Type*) [Group G] [Group H] (c : G) (d : H)
            (h : (orderOf c).gcd (orderOf d) > 1) :
            orderOf (c, d) < (orderOf c) * (orderOf d) := by
          sorry
        """,
    )
    write(source / ".archon" / "should-not-copy.txt", "ignore me\n")
    return source


def make_workspace_pair(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "source"
    workspace = tmp_path / "workspace"

    write(
        source / "FATEM" / "42.lean",
        """
        import Mathlib

        theorem orderOf_prod_lt_orderOf_mul (G H : Type*) [Group G] [Group H] (c : G) (d : H)
            (h : (orderOf c).gcd (orderOf d) > 1) :
            orderOf (c, d) < (orderOf c) * (orderOf d) := by
          sorry
        """,
    )
    write(
        workspace / "FATEM" / "42.lean",
        """
        import Mathlib

        theorem orderOf_prod_lt_orderOf_mul (G H : Type*) [Group G] [Group H] (c : G) (d : H)
            (hc : 0 < orderOf c) (hd : 0 < orderOf d) (h : (orderOf c).gcd (orderOf d) > 1) :
            orderOf (c, d) < (orderOf c) * (orderOf d) := by
          exact orderOf_prod_lt_orderOf_mul G H c d hc hd h
        """,
    )
    return source, workspace


def test_parse_allowed_files_reads_run_scope_markdown():
    scope = """
    # Run Scope

    ## Allowed Files

    1. `FATEM/39.lean`
    2. `FATEM/42.lean`
    """

    assert parse_allowed_files(scope) == ["FATEM/39.lean", "FATEM/42.lean"]


def test_classify_header_mutation_flags_added_hypotheses():
    source = """
    theorem orderOf_prod_lt_orderOf_mul (G H : Type*) [Group G] [Group H] (c : G) (d : H)
        (h : (orderOf c).gcd (orderOf d) > 1) :
        orderOf (c, d) < (orderOf c) * (orderOf d) := by
    """
    workspace = """
    theorem orderOf_prod_lt_orderOf_mul (G H : Type*) [Group G] [Group H] (c : G) (d : H)
        (hc : 0 < orderOf c) (hd : 0 < orderOf d) (h : (orderOf c).gcd (orderOf d) > 1) :
        orderOf (c, d) < (orderOf c) * (orderOf d) := by
    """

    assert classify_header_mutation(source, workspace) == "added_hypothesis"


def test_collect_header_drifts_reports_theorem_mutation(tmp_path: Path):
    source, workspace = make_workspace_pair(tmp_path)

    drifts = collect_header_drifts(source, workspace, allowed_files=["FATEM/42.lean"])

    assert len(drifts) == 1
    assert drifts[0].rel_path == "FATEM/42.lean"
    assert drifts[0].declaration_name == "orderOf_prod_lt_orderOf_mul"
    assert drifts[0].mutation_class == "added_hypothesis"


def test_create_isolated_run_copies_source_and_workspace_without_archon(tmp_path: Path):
    source = make_source_project(tmp_path)
    cache_project = tmp_path / "cache-project"
    write(cache_project / ".lake" / "packages" / "mathlib" / "README", "cached\n")

    manifest = create_isolated_run(
        source,
        tmp_path / "run-root",
        reuse_lake_from=cache_project,
        scope_hint="FATEM/42.lean",
    )

    assert manifest["sourceOriginPath"] == str(source.resolve())
    assert (tmp_path / "run-root" / "source" / "FATEM" / "42.lean").exists()
    assert (tmp_path / "run-root" / "workspace" / "FATEM" / "42.lean").exists()
    assert not (tmp_path / "run-root" / "source" / ".archon").exists()
    assert not (tmp_path / "run-root" / "workspace" / ".archon").exists()
    assert (tmp_path / "run-root" / "workspace" / ".lake" / "packages" / "mathlib" / "README").exists()

    payload = json.loads((tmp_path / "run-root" / "RUN_MANIFEST.json").read_text(encoding="utf-8"))
    assert payload["schemaVersion"] == 1
    assert payload["scopeHint"] == "FATEM/42.lean"


def test_export_run_artifacts_writes_diff_proof_blocker_and_supervisor_snapshot(tmp_path: Path):
    run_root = tmp_path / "run-root"
    source = run_root / "source"
    workspace = run_root / "workspace"
    artifacts = run_root / "artifacts"

    write(source / "FATEM" / "39.lean", "theorem foo : True := by\n  sorry\n")
    write(workspace / "FATEM" / "39.lean", "theorem foo : True := by\n  trivial\n")
    write(workspace / ".archon" / "task_results" / "FATEM_42.lean.md", "# blocker\n")
    write(workspace / ".archon" / "supervisor" / "HOT_NOTES.md", "# hot\n")
    write(workspace / ".archon" / "supervisor" / "LEDGER.md", "# ledger\n")
    write(run_root / "RUN_MANIFEST.json", json.dumps({"schemaVersion": 1}, indent=2))
    artifacts.mkdir(parents=True, exist_ok=True)

    summary = export_run_artifacts(run_root)

    assert summary["changedFiles"] == ["FATEM/39.lean"]
    assert summary["blockerNotes"] == ["FATEM_42.lean.md"]
    assert (artifacts / "proofs" / "FATEM" / "39.lean").exists()
    assert (artifacts / "diffs" / "FATEM" / "39.lean.diff").exists()
    assert (artifacts / "blockers" / "FATEM_42.lean.md").exists()
    assert (artifacts / "supervisor" / "HOT_NOTES.md").exists()
    assert (artifacts / "artifact-index.json").exists()


def test_install_repo_skill_symlinks_into_codex_home(tmp_path: Path):
    codex_home = tmp_path / "codex-home"
    env = os.environ.copy()
    env["CODEX_HOME"] = str(codex_home)

    result = subprocess.run(
        ["bash", str(INSTALL_REPO_SKILL)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    installed = codex_home / "skills" / "archon-supervisor"
    assert installed.is_symlink()
    assert installed.resolve() == (ROOT / "skills" / "archon-supervisor").resolve()


def test_supervised_cycle_records_header_violation_and_writes_hot_notes(tmp_path: Path):
    source, workspace = make_workspace_pair(tmp_path)
    write(
        workspace / ".archon" / "RUN_SCOPE.md",
        """
        # Run Scope

        ## Allowed Files

        1. `FATEM/42.lean`
        """,
    )
    fake_loop = tmp_path / "fake-archon-loop.sh"
    write(
        fake_loop,
        """
        #!/usr/bin/env bash
        exit 0
        """,
    )
    fake_loop.chmod(0o755)

    result = subprocess.run(
        [
            "python3",
            str(SUPERVISED_CYCLE),
            "--workspace",
            str(workspace),
            "--source",
            str(source),
            "--archon-loop",
            str(fake_loop),
            "--skip-process-check",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    hot_notes = (workspace / ".archon" / "supervisor" / "HOT_NOTES.md").read_text(encoding="utf-8")
    assert "policy_violation" in hot_notes
    assert "added_hypothesis" in hot_notes

    violations = (workspace / ".archon" / "supervisor" / "violations.jsonl").read_text(encoding="utf-8")
    assert "header_mutation" in violations
    assert "added_hypothesis" in violations
