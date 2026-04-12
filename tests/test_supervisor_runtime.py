from __future__ import annotations

import json
import os
import subprocess
import textwrap
from pathlib import Path

from archonlib.agent_registry import load_agent_registry_map
from archonlib.run_workspace import create_isolated_run, export_run_artifacts
from archonlib.supervisor import (
    classify_header_mutation,
    collect_header_drifts,
    collect_meta_prover_errors,
    latest_iteration_meta,
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


def test_classify_header_mutation_ignores_same_header_with_different_inline_proof_body():
    source = "theorem int_eq_five_seven_span : ∀ z : ℤ, ∃ a b : ℤ, z = a * 5 + b * 7 := by sorry"
    workspace = (
        "theorem int_eq_five_seven_span : ∀ z : ℤ, ∃ a b : ℤ, z = a * 5 + b * 7 := by "
        "intro z; refine ⟨-4 * z, 3 * z, ?_⟩; ring"
    )

    assert classify_header_mutation(source, workspace) == "none"


def test_collect_header_drifts_reports_theorem_mutation(tmp_path: Path):
    source, workspace = make_workspace_pair(tmp_path)

    drifts = collect_header_drifts(source, workspace, allowed_files=["FATEM/42.lean"])

    assert len(drifts) == 1
    assert drifts[0].rel_path == "FATEM/42.lean"
    assert drifts[0].declaration_name == "orderOf_prod_lt_orderOf_mul"
    assert drifts[0].mutation_class == "added_hypothesis"


def test_collect_header_drifts_ignores_single_line_theorem_with_only_proof_body_change(tmp_path: Path):
    source = tmp_path / "source"
    workspace = tmp_path / "workspace"

    write(source / "FATEM" / "40.lean", "theorem int_eq_five_seven_span : ∀ z : ℤ, ∃ a b : ℤ, z = a * 5 + b * 7 := by sorry\n")
    write(
        workspace / "FATEM" / "40.lean",
        "theorem int_eq_five_seven_span : ∀ z : ℤ, ∃ a b : ℤ, z = a * 5 + b * 7 := by intro z; refine ⟨-4 * z, 3 * z, ?_⟩; ring\n",
    )

    drifts = collect_header_drifts(source, workspace, allowed_files=["FATEM/40.lean"])

    assert drifts == []


def test_latest_iteration_meta_reads_highest_iter_directory(tmp_path: Path):
    workspace = tmp_path / "workspace"
    write(workspace / ".archon" / "logs" / "iter-001" / "meta.json", json.dumps({"iteration": 1}))
    write(workspace / ".archon" / "logs" / "iter-003" / "meta.json", json.dumps({"iteration": 3}))

    iter_name, payload = latest_iteration_meta(workspace)

    assert iter_name == "iter-003"
    assert payload == {"iteration": 3}


def test_collect_meta_prover_errors_returns_failing_files():
    failures = collect_meta_prover_errors(
        {
            "provers": {
                "Foo": {"file": "FATEM/2.lean", "status": "error"},
                "Bar": {"file": "FATEM/3.lean", "status": "done"},
            }
        }
    )

    assert failures == ["FATEM/2.lean"]


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


def test_create_isolated_run_links_shared_lake_packages_instead_of_copying_whole_cache(tmp_path: Path):
    source = make_source_project(tmp_path)
    cache_project = tmp_path / "cache-project"
    shared_packages = cache_project / ".lake" / "packages"
    write(shared_packages / "mathlib" / "README", "cached\n")
    write(cache_project / ".lake" / "config" / "manifest.json", "{}\n")
    write(cache_project / ".lake" / "build" / "lib" / "placeholder", "local-build\n")

    create_isolated_run(
        source,
        tmp_path / "run-root",
        reuse_lake_from=cache_project,
        scope_hint="FATEM/42.lean",
    )

    workspace_lake = tmp_path / "run-root" / "workspace" / ".lake"
    packages_link = workspace_lake / "packages"

    assert workspace_lake.is_dir()
    assert packages_link.is_symlink()
    assert packages_link.resolve() == shared_packages.resolve()
    assert (packages_link / "mathlib" / "README").read_text(encoding="utf-8") == "cached\n"
    assert not (workspace_lake / "config").exists()
    assert not (workspace_lake / "build").exists()


def test_export_run_artifacts_writes_diff_proof_task_results_and_supervisor_snapshot(tmp_path: Path):
    run_root = tmp_path / "run-root"
    source = run_root / "source"
    workspace = run_root / "workspace"
    artifacts = run_root / "artifacts"

    write(source / "FATEM" / "39.lean", "theorem foo : True := by\n  sorry\n")
    write(workspace / "FATEM" / "39.lean", "theorem foo : True := by\n  trivial\n")
    write(workspace / ".lake" / "packages" / "mathlib" / "Mathlib" / "Ignored.lean", "theorem ignored : True := by\n  trivial\n")
    write(workspace / ".archon" / "task_results" / "FATEM_42.lean.md", "# blocker\n")
    write(workspace / ".archon" / "validation" / "FATEM_39.lean.json", json.dumps({"relPath": "FATEM/39.lean"}))
    write(workspace / ".archon" / "lessons" / "iter-001-clean.json", json.dumps({"status": "clean"}))
    write(workspace / ".archon" / "supervisor" / "HOT_NOTES.md", "# hot\n")
    write(workspace / ".archon" / "supervisor" / "LEDGER.md", "# ledger\n")
    write(run_root / "RUN_MANIFEST.json", json.dumps({"schemaVersion": 1}, indent=2))
    artifacts.mkdir(parents=True, exist_ok=True)

    summary = export_run_artifacts(run_root)

    assert summary["changedFiles"] == ["FATEM/39.lean"]
    assert summary["taskResults"] == ["FATEM_42.lean.md"]
    assert summary["blockerNotes"] == ["FATEM_42.lean.md"]
    assert summary["validationFiles"] == ["FATEM_39.lean.json"]
    assert summary["lessonFiles"] == ["iter-001-clean.json"]
    assert (artifacts / "proofs" / "FATEM" / "39.lean").exists()
    assert (artifacts / "diffs" / "FATEM" / "39.lean.diff").exists()
    assert (artifacts / "task-results" / "FATEM_42.lean.md").exists()
    assert (artifacts / "validation" / "FATEM_39.lean.json").exists()
    assert (artifacts / "lessons" / "iter-001-clean.json").exists()
    assert (artifacts / "supervisor" / "HOT_NOTES.md").exists()
    assert (artifacts / "artifact-index.json").exists()
    assert not (artifacts / "proofs" / ".lake" / "packages" / "mathlib" / "Mathlib" / "Ignored.lean").exists()


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
    supervisor = codex_home / "skills" / "archon-supervisor"
    orchestrator = codex_home / "skills" / "archon-orchestrator"
    assert supervisor.is_symlink()
    assert orchestrator.is_symlink()
    assert supervisor.resolve() == (ROOT / "skills" / "archon-supervisor").resolve()
    assert orchestrator.resolve() == (ROOT / "skills" / "archon-orchestrator").resolve()


def test_runtime_agent_registry_loads_from_canonical_agents_directory():
    registry = load_agent_registry_map(ROOT)

    assert {
        "plan-agent",
        "prover-agent",
        "review-agent",
        "informal-agent",
        "statement-validator",
        "supervisor-agent",
        "orchestrator-agent",
    } <= set(registry)
    assert registry["statement-validator"]["kind"] == "validator"
    assert registry["supervisor-agent"]["status"] in {"active", "proposed"}
    assert registry["orchestrator-agent"]["kind"] == "orchestrator"


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

    validation_files = sorted((workspace / ".archon" / "validation").glob("*.json"))
    assert [path.name for path in validation_files] == ["FATEM_42.lean.json"]
    validation_payload = json.loads(validation_files[0].read_text(encoding="utf-8"))
    assert validation_payload["status"] == "policy_violation"
    assert validation_payload["validationStatus"] == "failed"
    assert validation_payload["statementFidelity"] == "violated"
    assert validation_payload["headerDrifts"][0]["mutation_class"] == "added_hypothesis"

    lesson_files = sorted((workspace / ".archon" / "lessons").glob("*.json"))
    assert len(lesson_files) == 1
    lesson_payload = json.loads(lesson_files[0].read_text(encoding="utf-8"))
    categories = {entry["category"] for entry in lesson_payload["lessons"]}
    assert "theorem_fidelity" in categories


def test_supervised_cycle_surfaces_prover_failures_from_iteration_meta(tmp_path: Path):
    source = tmp_path / "source"
    workspace = tmp_path / "workspace"
    write(source / "FATEM" / "2.lean", "theorem foo : True := by\n  sorry\n")
    write(workspace / "FATEM" / "2.lean", "theorem foo : True := by\n  sorry\n")
    write(
        workspace / ".archon" / "RUN_SCOPE.md",
        """
        # Run Scope

        ## Allowed Files

        1. `FATEM/2.lean`
        """,
    )
    fake_loop = tmp_path / "fake-archon-loop.sh"
    write(
        fake_loop,
        f"""
        #!/usr/bin/env bash
        mkdir -p "{workspace}/.archon/logs/iter-001"
        cat > "{workspace}/.archon/logs/iter-001/meta.json" <<'EOF'
        {{
          "iteration": 1,
          "plan": {{"status": "done"}},
          "prover": {{"status": "done"}},
          "provers": {{
            "FATEM_2": {{"file": "FATEM/2.lean", "status": "error"}}
          }}
        }}
        EOF
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

    assert result.returncode == 3
    hot_notes = (workspace / ".archon" / "supervisor" / "HOT_NOTES.md").read_text(encoding="utf-8")
    assert "prover_failed" in hot_notes
    assert "Prover errors: FATEM/2.lean" in hot_notes

    violations = (workspace / ".archon" / "supervisor" / "violations.jsonl").read_text(encoding="utf-8")
    assert "prover_error" in violations


def test_supervised_cycle_writes_clean_validation_and_recovery_lesson(tmp_path: Path):
    source = tmp_path / "source"
    workspace = tmp_path / "workspace"
    write(source / "FATEM" / "42.lean", "theorem foo : True := by\n  sorry\n")
    write(workspace / "FATEM" / "42.lean", "theorem foo : True := by\n  sorry\n")
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
        f"""
        #!/usr/bin/env bash
        mkdir -p "{workspace}/.archon/logs/iter-001/provers"
        mkdir -p "{workspace}/.archon/task_results"
        cat > "{workspace}/.archon/logs/iter-001/meta.json" <<'EOF'
        {{
          "iteration": 1,
          "plan": {{"status": "done"}},
          "prover": {{"status": "done"}},
          "provers": {{
            "FATEM_42": {{"file": "FATEM/42.lean", "status": "error"}}
          }}
        }}
        EOF
        cat > "{workspace}/.archon/task_results/FATEM_42.lean.md" <<'EOF'
        # FATEM/42.lean

        ## foo
        ### Attempt 1
        - **Result:** FAILED
        - **Concrete blocker:** This theorem is false as stated.
        EOF
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

    assert result.returncode == 0
    validation_path = workspace / ".archon" / "validation" / "FATEM_42.lean.json"
    assert validation_path.exists()
    validation_payload = json.loads(validation_path.read_text(encoding="utf-8"))
    assert validation_payload["status"] == "clean"
    assert validation_payload["acceptanceStatus"] == "accepted"
    assert validation_payload["validationStatus"] == "passed"
    assert validation_payload["checks"]["taskResult"]["durable"] is True
    assert validation_payload["checks"]["proverError"] is True

    lesson_files = sorted((workspace / ".archon" / "lessons").glob("*.json"))
    assert len(lesson_files) == 1
    lesson_payload = json.loads(lesson_files[0].read_text(encoding="utf-8"))
    assert lesson_payload["status"] == "clean"
    assert "verified_after_stall" in lesson_payload["signals"]
    categories = {entry["category"] for entry in lesson_payload["lessons"]}
    assert "idle_recovery" in categories
    assert "blocker_discipline" in categories


def test_supervised_cycle_records_loop_failure_before_new_iteration(tmp_path: Path):
    source = tmp_path / "source"
    workspace = tmp_path / "workspace"
    write(source / "FATEM" / "2.lean", "theorem foo : True := by\n  sorry\n")
    write(workspace / "FATEM" / "2.lean", "theorem foo : True := by\n  sorry\n")
    write(workspace / ".archon" / "logs" / "iter-001" / "meta.json", json.dumps({"iteration": 1}))
    write(
        workspace / ".archon" / "RUN_SCOPE.md",
        """
        # Run Scope

        ## Allowed Files

        1. `FATEM/2.lean`
        """,
    )
    fake_loop = tmp_path / "fake-archon-loop.sh"
    write(
        fake_loop,
        """
        #!/usr/bin/env bash
        echo "transient network failure" >&2
        exit 1
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

    assert result.returncode == 1
    hot_notes = (workspace / ".archon" / "supervisor" / "HOT_NOTES.md").read_text(encoding="utf-8")
    assert "loop_failed" in hot_notes
    assert "No new iteration metadata was created during this cycle" in hot_notes
    assert "transient network failure" in hot_notes

    stderr_log = (workspace / ".archon" / "supervisor" / "last_loop.stderr.log").read_text(encoding="utf-8")
    assert "transient network failure" in stderr_log


def test_supervised_cycle_passes_timeout_env_to_archon_loop(tmp_path: Path):
    source = tmp_path / "source"
    workspace = tmp_path / "workspace"
    write(source / "FATEM" / "2.lean", "theorem foo : True := by\n  sorry\n")
    write(workspace / "FATEM" / "2.lean", "theorem foo : True := by\n  sorry\n")
    write(
        workspace / ".archon" / "RUN_SCOPE.md",
        """
        # Run Scope

        ## Allowed Files

        1. `FATEM/2.lean`
        """,
    )
    fake_loop = tmp_path / "fake-archon-loop.sh"
    env_dump = tmp_path / "env.json"
    write(
        fake_loop,
        f"""
        #!/usr/bin/env bash
        python3 - <<'EOF'
        import json
        import os
        from pathlib import Path

        Path("{env_dump}").write_text(json.dumps({{
            "ARCHON_PLAN_TIMEOUT_SECONDS": os.environ.get("ARCHON_PLAN_TIMEOUT_SECONDS"),
            "ARCHON_PROVER_TIMEOUT_SECONDS": os.environ.get("ARCHON_PROVER_TIMEOUT_SECONDS"),
            "ARCHON_REVIEW_TIMEOUT_SECONDS": os.environ.get("ARCHON_REVIEW_TIMEOUT_SECONDS"),
        }}, sort_keys=True), encoding="utf-8")
        EOF
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
            "--plan-timeout-seconds",
            "180",
            "--prover-timeout-seconds",
            "240",
            "--review-timeout-seconds",
            "60",
            "--skip-process-check",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 4
    payload = json.loads(env_dump.read_text(encoding="utf-8"))
    assert payload == {
        "ARCHON_PLAN_TIMEOUT_SECONDS": "180",
        "ARCHON_PROVER_TIMEOUT_SECONDS": "240",
        "ARCHON_REVIEW_TIMEOUT_SECONDS": "60",
    }


def test_supervised_cycle_refuses_to_start_when_run_local_lease_is_active(tmp_path: Path):
    source = tmp_path / "source"
    workspace = tmp_path / "workspace"
    write(source / "FATEM" / "2.lean", "theorem foo : True := by\n  sorry\n")
    write(workspace / "FATEM" / "2.lean", "theorem foo : True := by\n  sorry\n")
    write(
        workspace / ".archon" / "RUN_SCOPE.md",
        """
        # Run Scope

        ## Allowed Files

        1. `FATEM/2.lean`
        """,
    )
    write(
        workspace / ".archon" / "supervisor" / "run-lease.json",
        json.dumps(
            {
                "schemaVersion": 1,
                "active": True,
                "status": "running",
                "workspace": str(workspace),
                "source": str(source),
                "supervisorPid": os.getpid(),
                "loopPid": None,
                "updatedAt": "2026-04-12T00:00:00+00:00",
                "lastHeartbeatAt": "2026-04-12T00:00:00+00:00",
            },
            indent=2,
        ),
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
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 6
    hot_notes = (workspace / ".archon" / "supervisor" / "HOT_NOTES.md").read_text(encoding="utf-8")
    assert "run_busy" in hot_notes
    assert "active run-local lease" in hot_notes

    violations = (workspace / ".archon" / "supervisor" / "violations.jsonl").read_text(encoding="utf-8")
    assert "active_supervisor_lease" in violations


def test_supervised_cycle_recovery_only_verifies_existing_artifacts_and_closes_lease(tmp_path: Path):
    source = tmp_path / "source"
    workspace = tmp_path / "workspace"
    write(source / "FATEM" / "2.lean", "theorem foo : True\n    := by\n  sorry\n")
    write(workspace / "FATEM" / "2.lean", "theorem foo : True\n    := by\n  trivial\n")
    write(
        workspace / ".archon" / "RUN_SCOPE.md",
        """
        # Run Scope

        ## Allowed Files

        1. `FATEM/2.lean`
        """,
    )
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

    result = subprocess.run(
        [
            "python3",
            str(SUPERVISED_CYCLE),
            "--workspace",
            str(workspace),
            "--source",
            str(source),
            "--recovery-only",
            "--skip-process-check",
            "--changed-file-verify-template",
            f"python3 {verify_script} {{file}}",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    hot_notes = (workspace / ".archon" / "supervisor" / "HOT_NOTES.md").read_text(encoding="utf-8")
    assert "- Status: clean" in hot_notes
    assert "Lease file:" in hot_notes
    assert "recovery-only pass" in hot_notes

    validation_payload = json.loads(
        (workspace / ".archon" / "validation" / "FATEM_2.lean.json").read_text(encoding="utf-8")
    )
    assert validation_payload["acceptanceStatus"] == "accepted"
    assert validation_payload["recoveryEvent"] == "verified_in_recovery"

    lease_payload = json.loads((workspace / ".archon" / "supervisor" / "run-lease.json").read_text(encoding="utf-8"))
    assert lease_payload["active"] is False
    assert lease_payload["finalStatus"] == "clean"
    assert lease_payload["recoveryEvent"] == "verified_in_recovery"


def test_supervised_cycle_kills_idle_prover_and_records_hot_notes(tmp_path: Path):
    source = tmp_path / "source"
    workspace = tmp_path / "workspace"
    write(source / "FATEM" / "2.lean", "theorem foo : True := by\n  sorry\n")
    write(workspace / "FATEM" / "2.lean", "theorem foo : True := by\n  sorry\n")
    write(
        workspace / ".archon" / "RUN_SCOPE.md",
        """
        # Run Scope

        ## Allowed Files

        1. `FATEM/2.lean`
        """,
    )
    fake_loop = tmp_path / "fake-archon-loop.sh"
    write(
        fake_loop,
        f"""
        #!/usr/bin/env bash
        mkdir -p "{workspace}/.archon/logs/iter-001/provers"
        cat > "{workspace}/.archon/logs/iter-001/meta.json" <<'EOF'
        {{
          "iteration": 1,
          "plan": {{"status": "done"}},
          "prover": {{"status": "running"}},
          "provers": {{
            "FATEM_2": {{"file": "FATEM/2.lean", "status": "running"}}
          }}
        }}
        EOF
        cat > "{workspace}/.archon/logs/iter-001/provers/FATEM_2.jsonl" <<'EOF'
        {{"ts":"2026-04-11T00:00:00Z","event":"text","content":"starting"}}
        EOF
        sleep 30
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
            "--prover-idle-seconds",
            "1",
            "--monitor-poll-seconds",
            "0.1",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert result.returncode == 5
    hot_notes = (workspace / ".archon" / "supervisor" / "HOT_NOTES.md").read_text(encoding="utf-8")
    assert "prover_idle" in hot_notes
    assert "Idle timeout triggered" in hot_notes

    violations = (workspace / ".archon" / "supervisor" / "violations.jsonl").read_text(encoding="utf-8")
    assert "prover_idle_timeout" in violations


def test_supervised_cycle_does_not_count_preexisting_artifacts_as_new_progress(tmp_path: Path):
    source = tmp_path / "source"
    workspace = tmp_path / "workspace"
    write(source / "FATEM" / "2.lean", "theorem foo : True := by\n  sorry\n")
    write(workspace / "FATEM" / "2.lean", "/- preexisting workspace note -/\n\ntheorem foo : True := by\n  sorry\n")
    write(workspace / ".archon" / "task_results" / "FATEM_2.lean.md", "# old blocker\n")
    write(
        workspace / ".archon" / "RUN_SCOPE.md",
        """
        # Run Scope

        ## Allowed Files

        1. `FATEM/2.lean`
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

    assert result.returncode == 4
    hot_notes = (workspace / ".archon" / "supervisor" / "HOT_NOTES.md").read_text(encoding="utf-8")
    assert "no_progress" in hot_notes
    assert "New changed files: (none)" in hot_notes
    assert "New task results: (none)" in hot_notes


def test_supervised_cycle_recovers_verified_changed_file_after_idle_timeout(tmp_path: Path):
    source = tmp_path / "source"
    workspace = tmp_path / "workspace"
    write(source / "FATEM" / "2.lean", "theorem foo : True\n    := by\n  sorry\n")
    write(workspace / "FATEM" / "2.lean", "theorem foo : True\n    := by\n  sorry\n")
    write(
        workspace / ".archon" / "RUN_SCOPE.md",
        """
        # Run Scope

        ## Allowed Files

        1. `FATEM/2.lean`
        """,
    )
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
    fake_loop = tmp_path / "fake-archon-loop.sh"
    write(
        fake_loop,
        f"""
        #!/usr/bin/env bash
        mkdir -p "{workspace}/.archon/logs/iter-001/provers"
        cat > "{workspace}/.archon/logs/iter-001/meta.json" <<'EOF'
        {{
          "iteration": 1,
          "plan": {{"status": "done"}},
          "prover": {{"status": "running"}},
          "provers": {{
            "FATEM_2": {{"file": "FATEM/2.lean", "status": "running"}}
          }}
        }}
        EOF
        cat > "{workspace}/.archon/logs/iter-001/provers/FATEM_2.jsonl" <<'EOF'
        {{"ts":"2026-04-11T00:00:00Z","event":"text","content":"starting"}}
        EOF
        sleep 1
        cat > "{workspace}/FATEM/2.lean" <<'EOF'
        theorem foo : True
            := by
          trivial
        EOF
        sleep 30
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
            "--prover-idle-seconds",
            "1",
            "--monitor-poll-seconds",
            "0.1",
            "--changed-file-verify-template",
            f"python3 {verify_script} {{file}}",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert result.returncode == 0
    hot_notes = (workspace / ".archon" / "supervisor" / "HOT_NOTES.md").read_text(encoding="utf-8")
    assert "- Status: clean" in hot_notes
    assert "Recovered after prover idle" in hot_notes

    violations = (workspace / ".archon" / "supervisor" / "violations.jsonl").read_text(encoding="utf-8")
    assert "prover_idle_timeout" in violations
    assert "verified_after_idle" in violations


def test_supervised_cycle_recovers_durable_task_result_after_idle_timeout(tmp_path: Path):
    source = tmp_path / "source"
    workspace = tmp_path / "workspace"
    write(source / "FATEM" / "2.lean", "theorem foo : True := by\n  sorry\n")
    write(workspace / "FATEM" / "2.lean", "theorem foo : True := by\n  sorry\n")
    write(
        workspace / ".archon" / "RUN_SCOPE.md",
        """
        # Run Scope

        ## Allowed Files

        1. `FATEM/2.lean`
        """,
    )
    fake_loop = tmp_path / "fake-archon-loop.sh"
    write(
        fake_loop,
        f"""
        #!/usr/bin/env bash
        mkdir -p "{workspace}/.archon/logs/iter-001/provers"
        mkdir -p "{workspace}/.archon/task_results"
        cat > "{workspace}/.archon/logs/iter-001/meta.json" <<'EOF'
        {{
          "iteration": 1,
          "plan": {{"status": "done"}},
          "prover": {{"status": "running"}},
          "provers": {{
            "FATEM_2": {{"file": "FATEM/2.lean", "status": "running"}}
          }}
        }}
        EOF
        cat > "{workspace}/.archon/logs/iter-001/provers/FATEM_2.jsonl" <<'EOF'
        {{"ts":"2026-04-11T00:00:00Z","event":"text","content":"starting"}}
        EOF
        sleep 1
        cat > "{workspace}/.archon/task_results/FATEM_2.lean.md" <<'EOF'
        # FATEM/2.lean

        ## foo
        ### Attempt 1
        - **Result:** FAILED
        - **Concrete blocker:** This theorem is false as stated.
        EOF
        sleep 30
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
            "--prover-idle-seconds",
            "1",
            "--monitor-poll-seconds",
            "0.1",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert result.returncode == 0
    hot_notes = (workspace / ".archon" / "supervisor" / "HOT_NOTES.md").read_text(encoding="utf-8")
    assert "- Status: clean" in hot_notes
    assert "durable task results already existed" in hot_notes


def test_supervised_cycle_synthesizes_blocker_note_after_idle_when_route_is_prevalidated(tmp_path: Path):
    source = tmp_path / "source"
    workspace = tmp_path / "workspace"
    write(source / "FATEM" / "42.lean", "theorem foo : True := by\n  sorry\n")
    write(workspace / "FATEM" / "42.lean", "theorem foo : True := by\n  sorry\n")
    write(
        workspace / ".archon" / "RUN_SCOPE.md",
        """
        # Run Scope

        ## Allowed Files

        1. `FATEM/42.lean`
        """,
    )
    write(
        workspace / ".archon" / "PROGRESS.md",
        """
        ## Current Objectives

        1. **FATEM/42.lean** — Keep the theorem header frozen. Write `.archon/task_results/FATEM_42.lean.md` immediately with a durable blocker report. The obstruction route is already Lean-validated.
        """,
    )
    write(
        workspace / ".archon" / "task_pending.md",
        """
        - `FATEM/42.lean` — Lean-validated blocker route: the statement is false as written. Notes in `.archon/informal/fatem_42_blocker.md`.
        """,
    )
    write(
        workspace / ".archon" / "informal" / "fatem_42_blocker.md",
        """
        # FATEM/42 Blocker Notes

        The theorem is false as written.

        Lean-validated obstruction route:
        - `c := Multiplicative.ofAdd (1 : ℤ)`
        - `d := Multiplicative.ofAdd (1 : ZMod 2)`
        """,
    )
    fake_loop = tmp_path / "fake-archon-loop.sh"
    write(
        fake_loop,
        f"""
        #!/usr/bin/env bash
        mkdir -p "{workspace}/.archon/logs/iter-001/provers"
        cat > "{workspace}/.archon/logs/iter-001/meta.json" <<'EOF'
        {{
          "iteration": 1,
          "plan": {{"status": "done"}},
          "prover": {{"status": "running"}},
          "provers": {{
            "FATEM_42": {{"file": "FATEM/42.lean", "status": "running"}}
          }}
        }}
        EOF
        cat > "{workspace}/.archon/logs/iter-001/provers/FATEM_42.jsonl" <<'EOF'
        {{"ts":"2026-04-11T00:00:00Z","event":"text","content":"starting"}}
        EOF
        sleep 30
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
            "--prover-idle-seconds",
            "1",
            "--monitor-poll-seconds",
            "0.1",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert result.returncode == 0
    note_path = workspace / ".archon" / "task_results" / "FATEM_42.lean.md"
    assert note_path.exists()
    note_text = note_path.read_text(encoding="utf-8")
    assert "Supervisor Recovery" in note_text
    assert "**Concrete blocker:**" in note_text
    assert ".archon/informal/fatem_42_blocker.md" in note_text

    hot_notes = (workspace / ".archon" / "supervisor" / "HOT_NOTES.md").read_text(encoding="utf-8")
    assert "- Status: clean" in hot_notes
    assert "synthesized durable blocker note" in hot_notes

    violations = (workspace / ".archon" / "supervisor" / "violations.jsonl").read_text(encoding="utf-8")
    assert "prover_idle_timeout" in violations
    assert "synthesized_blocker_after_idle" in violations

    validation_files = sorted((workspace / ".archon" / "validation").glob("*.json"))
    assert [path.name for path in validation_files] == ["FATEM_42.lean.json"]
    validation_payload = json.loads(validation_files[0].read_text(encoding="utf-8"))
    assert validation_payload["status"] == "clean"
    assert validation_payload["recoveryEvent"] == "synthesized_blocker_after_idle"
    assert validation_payload["blockerNotes"] == ["FATEM_42.lean.md"]
    assert validation_payload["taskResultKinds"]["FATEM_42.lean.md"] == "blocker"

    lesson_files = sorted((workspace / ".archon" / "lessons").glob("*.json"))
    assert len(lesson_files) == 1
    lesson_payload = json.loads(lesson_files[0].read_text(encoding="utf-8"))
    categories = {entry["category"] for entry in lesson_payload["lessons"]}
    assert "idle_recovery" in categories
    assert "blocker_discipline" in categories


def test_supervised_cycle_recovers_durable_task_result_after_prover_error(tmp_path: Path):
    source = tmp_path / "source"
    workspace = tmp_path / "workspace"
    write(source / "FATEM" / "42.lean", "theorem foo : True := by\n  sorry\n")
    write(workspace / "FATEM" / "42.lean", "theorem foo : True := by\n  sorry\n")
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
        f"""
        #!/usr/bin/env bash
        mkdir -p "{workspace}/.archon/logs/iter-001/provers"
        mkdir -p "{workspace}/.archon/task_results"
        cat > "{workspace}/.archon/logs/iter-001/meta.json" <<'EOF'
        {{
          "iteration": 1,
          "plan": {{"status": "done"}},
          "prover": {{"status": "done"}},
          "provers": {{
            "FATEM_42": {{"file": "FATEM/42.lean", "status": "error"}}
          }}
        }}
        EOF
        cat > "{workspace}/.archon/task_results/FATEM_42.lean.md" <<'EOF'
        # FATEM/42.lean

        ## foo
        ### Attempt 1
        - **Result:** FAILED
        - **Concrete blocker:** This theorem is false as stated.
        EOF
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

    assert result.returncode == 0
    hot_notes = (workspace / ".archon" / "supervisor" / "HOT_NOTES.md").read_text(encoding="utf-8")
    assert "- Status: clean" in hot_notes
    assert "Recovered after prover stall: durable task results already existed" in hot_notes

    violations = (workspace / ".archon" / "supervisor" / "violations.jsonl").read_text(encoding="utf-8")
    assert "prover_error" in violations
    assert "verified_after_stall" in violations


def test_supervised_cycle_does_not_recover_nondurable_task_result_after_idle_timeout(tmp_path: Path):
    source = tmp_path / "source"
    workspace = tmp_path / "workspace"
    write(source / "FATEM" / "2.lean", "theorem foo : True := by\n  sorry\n")
    write(workspace / "FATEM" / "2.lean", "theorem foo : True := by\n  sorry\n")
    write(
        workspace / ".archon" / "RUN_SCOPE.md",
        """
        # Run Scope

        ## Allowed Files

        1. `FATEM/2.lean`
        """,
    )
    fake_loop = tmp_path / "fake-archon-loop.sh"
    write(
        fake_loop,
        f"""
        #!/usr/bin/env bash
        mkdir -p "{workspace}/.archon/logs/iter-001/provers"
        mkdir -p "{workspace}/.archon/task_results"
        cat > "{workspace}/.archon/logs/iter-001/meta.json" <<'EOF'
        {{
          "iteration": 1,
          "plan": {{"status": "done"}},
          "prover": {{"status": "running"}},
          "provers": {{
            "FATEM_2": {{"file": "FATEM/2.lean", "status": "running"}}
          }}
        }}
        EOF
        cat > "{workspace}/.archon/logs/iter-001/provers/FATEM_2.jsonl" <<'EOF'
        {{"ts":"2026-04-11T00:00:00Z","event":"text","content":"starting"}}
        EOF
        sleep 1
        cat > "{workspace}/.archon/task_results/FATEM_2.lean.md" <<'EOF'
        # FATEM/2.lean

        ## foo
        ### Attempt 1
        - **Result:** IN PROGRESS
        - **Next step:** Try a different induction route.
        EOF
        sleep 30
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
            "--prover-idle-seconds",
            "1",
            "--monitor-poll-seconds",
            "0.1",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert result.returncode == 5
    hot_notes = (workspace / ".archon" / "supervisor" / "HOT_NOTES.md").read_text(encoding="utf-8")
    assert "- Status: prover_idle" in hot_notes
    assert "Verification after idle failed for task result FATEM_2.lean.md" in hot_notes
