from __future__ import annotations

import json
import os
import subprocess
import textwrap
import time
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
from scripts.supervised_cycle import _archive_stale_accepted_task_results, _task_result_name


ROOT = Path(__file__).resolve().parents[1]
SUPERVISED_CYCLE = ROOT / "scripts" / "supervised_cycle.py"
INSTALL_REPO_SKILL = ROOT / "scripts" / "install_repo_skill.sh"


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")


def write_runtime_config(workspace: Path, *, helper_enabled: bool = False, write_progress_surface: bool = True) -> None:
    helper_enabled_literal = "true" if helper_enabled else "false"
    progress_literal = "true" if write_progress_surface else "false"
    write(
        workspace / ".archon" / "runtime-config.toml",
        f"""
        [helper]
        enabled = {helper_enabled_literal}
        provider = "openai"
        model = "gpt-5.4"
        api_key_env = "OPENAI_API_KEY"
        base_url_env = "OPENAI_BASE_URL"
        max_retries = 5
        initial_backoff_seconds = 5
        timeout_seconds = 300

        [helper.plan]
        enabled = true
        max_calls_per_iteration = 1
        trigger_on_missing_infrastructure = true
        trigger_on_external_reference = true
        trigger_on_repeated_failure = true
        notes_dir = ".archon/informal/helper"

        [helper.prover]
        enabled = true
        max_calls_per_session = 2
        trigger_on_missing_infrastructure = true
        trigger_on_lsp_timeout = true
        trigger_on_first_stuck_attempt = true
        notes_dir = ".archon/informal/helper"

        [observability]
        write_progress_surface = {progress_literal}
        """,
    )


def write_stale_planner_state(workspace: Path, rel_path: str) -> None:
    write(
        workspace / ".archon" / "PROGRESS.md",
        f"""
        # Project Progress

        ## Current Stage
        prover

        ## Stages
        - [x] init
        - [x] autoformalize
        - [ ] prover
        - [ ] polish

        ## Current Objectives

        1. **{rel_path}** — Continue the current prover task.
        """,
    )
    write(workspace / ".archon" / "task_pending.md", f"# Pending Tasks\n\n- `{rel_path}` — queued.\n")
    write(workspace / ".archon" / "task_done.md", "# Completed Tasks\n\n- None.\n")


def write_campaign_manifest(campaign_root: Path) -> None:
    write(campaign_root / "CAMPAIGN_MANIFEST.json", json.dumps({"runs": []}, sort_keys=True))


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


def test_archive_stale_accepted_task_results_moves_only_accepted_non_objective_notes(tmp_path: Path):
    workspace = tmp_path / "workspace"
    validation_root = workspace / ".archon" / "validation"
    task_results_root = workspace / ".archon" / "task_results"

    accepted_rel = "FATEM/1.lean"
    objective_rel = "FATEM/2.lean"

    write(
        validation_root / "FATEM_1.lean.json",
        json.dumps(
            {
                "relPath": accepted_rel,
                "acceptanceStatus": "accepted",
                "validationStatus": "passed",
            },
            indent=2,
        ),
    )
    write(
        validation_root / "FATEM_2.lean.json",
        json.dumps(
            {
                "relPath": objective_rel,
                "acceptanceStatus": "accepted",
                "validationStatus": "passed",
            },
            indent=2,
        ),
    )
    write(task_results_root / _task_result_name(accepted_rel), "# accepted note\n")
    write(task_results_root / _task_result_name(objective_rel), "# objective note\n")

    archived = _archive_stale_accepted_task_results(workspace, objective_files=[objective_rel])

    assert archived == [
        {
            "relPath": accepted_rel,
            "noteName": _task_result_name(accepted_rel),
            "archivePath": archived[0]["archivePath"],
        }
    ]
    assert archived[0]["archivePath"].startswith(".archon/task_results_archived/accepted_stale/")
    assert not (task_results_root / _task_result_name(accepted_rel)).exists()
    assert (task_results_root / _task_result_name(objective_rel)).exists()
    assert (workspace / archived[0]["archivePath"]).exists()


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
    assert payload["lakePackagesLinked"] is True
    assert payload["projectBuildReused"] is False
    assert payload["lakeBuildReusePath"] is None
    assert payload["scopeHint"] == "FATEM/42.lean"


def test_create_isolated_run_reuses_matching_project_build_outputs(tmp_path: Path):
    source = make_source_project(tmp_path)
    cache_project = tmp_path / "cache-project"
    shared_packages = cache_project / ".lake" / "packages"
    write(shared_packages / "mathlib" / "README", "cached\n")
    write(cache_project / "lean-toolchain", (source / "lean-toolchain").read_text(encoding="utf-8"))
    write(cache_project / "lakefile.lean", (source / "lakefile.lean").read_text(encoding="utf-8"))
    write(cache_project / ".lake" / "config" / "manifest.json", "{}\n")
    write(cache_project / ".lake" / "build" / "lib" / "placeholder", "local-build\n")

    manifest = create_isolated_run(
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
    assert (workspace_lake / "config" / "manifest.json").read_text(encoding="utf-8") == "{}\n"
    assert (workspace_lake / "build" / "lib" / "placeholder").read_text(encoding="utf-8") == "local-build\n"
    assert manifest["lakePackagesLinked"] is True
    assert manifest["projectBuildReused"] is True
    assert manifest["lakeBuildReusePath"] == str((cache_project / ".lake" / "build").resolve())


def test_create_isolated_run_skips_project_build_reuse_when_cache_project_is_incompatible(tmp_path: Path):
    source = make_source_project(tmp_path)
    cache_project = tmp_path / "cache-project"
    shared_packages = cache_project / ".lake" / "packages"
    write(shared_packages / "mathlib" / "README", "cached\n")
    write(cache_project / "lean-toolchain", "leanprover/lean4:v4.99.0\n")
    write(cache_project / "lakefile.lean", (source / "lakefile.lean").read_text(encoding="utf-8"))
    write(cache_project / ".lake" / "config" / "manifest.json", "{}\n")
    write(cache_project / ".lake" / "build" / "lib" / "placeholder", "local-build\n")

    manifest = create_isolated_run(
        source,
        tmp_path / "run-root",
        reuse_lake_from=cache_project,
        scope_hint="FATEM/42.lean",
    )

    workspace_lake = tmp_path / "run-root" / "workspace" / ".lake"
    packages_link = workspace_lake / "packages"

    assert packages_link.is_symlink()
    assert packages_link.resolve() == shared_packages.resolve()
    assert not (workspace_lake / "config").exists()
    assert not (workspace_lake / "build").exists()
    assert manifest["projectBuildReused"] is False
    assert manifest["lakeBuildReusePath"] is None


def test_export_run_artifacts_writes_diff_proof_task_results_and_supervisor_snapshot(tmp_path: Path):
    run_root = tmp_path / "run-root"
    source = run_root / "source"
    workspace = run_root / "workspace"
    artifacts = run_root / "artifacts"

    write(source / "FATEM" / "39.lean", "theorem foo : True := by\n  sorry\n")
    write(workspace / "FATEM" / "39.lean", "theorem foo : True := by\n  trivial\n")
    write(workspace / ".lake" / "packages" / "mathlib" / "Mathlib" / "Ignored.lean", "theorem ignored : True := by\n  trivial\n")
    write(workspace / ".archon" / "task_results" / "FATEM_42.lean.md", "# blocker\n")
    write(
        workspace / ".archon" / "validation" / "FATEM_39.lean.json",
        json.dumps(
            {
                "relPath": "FATEM/39.lean",
                "checks": {
                    "taskResult": {
                        "path": ".archon/task_results/FATEM_42.lean.md",
                        "kind": "blocker",
                    }
                },
            }
        ),
    )
    write(workspace / ".archon" / "lessons" / "iter-001-clean.json", json.dumps({"status": "clean"}))
    write(workspace / ".archon" / "supervisor" / "HOT_NOTES.md", "# hot\n")
    write(workspace / ".archon" / "supervisor" / "LEDGER.md", "# ledger\n")
    write(workspace / ".archon" / "supervisor" / "progress-summary.md", "# progress\n")
    write(workspace / ".archon" / "supervisor" / "progress-summary.json", json.dumps({"status": "clean"}))
    write(run_root / "RUN_MANIFEST.json", json.dumps({"schemaVersion": 1}, indent=2))
    artifacts.mkdir(parents=True, exist_ok=True)

    summary = export_run_artifacts(run_root)

    assert summary["changedFiles"] == ["FATEM/39.lean"]
    assert summary["taskResults"] == ["FATEM_42.lean.md"]
    assert summary["resolvedNotes"] == []
    assert summary["blockerNotes"] == ["FATEM_42.lean.md"]
    assert summary["validationFiles"] == ["FATEM_39.lean.json"]
    assert summary["lessonFiles"] == ["iter-001-clean.json"]
    assert (artifacts / "proofs" / "FATEM" / "39.lean").exists()
    assert (artifacts / "diffs" / "FATEM" / "39.lean.diff").exists()
    assert (artifacts / "task-results" / "FATEM_42.lean.md").exists()
    assert (artifacts / "validation" / "FATEM_39.lean.json").exists()
    assert (artifacts / "lessons" / "iter-001-clean.json").exists()
    assert (artifacts / "supervisor" / "HOT_NOTES.md").exists()
    assert (artifacts / "supervisor" / "progress-summary.md").exists()
    assert (artifacts / "supervisor" / "progress-summary.json").exists()
    assert (artifacts / "artifact-index.json").exists()
    assert not (artifacts / "proofs" / ".lake" / "packages" / "mathlib" / "Mathlib" / "Ignored.lean").exists()


def test_export_run_artifacts_preserves_prior_accepted_validation_snapshot(tmp_path: Path):
    run_root = tmp_path / "run-root"
    source = run_root / "source"
    workspace = run_root / "workspace"
    artifacts = run_root / "artifacts"

    write(source / "FATEM" / "39.lean", "theorem foo : True := by\n  sorry\n")
    write(workspace / "FATEM" / "39.lean", "theorem foo : True := by\n  trivial\n")
    write(
        workspace / ".archon" / "validation" / "FATEM_39.lean.json",
        json.dumps(
            {
                "relPath": "FATEM/39.lean",
                "acceptanceStatus": "none",
                "validationStatus": "no_progress",
                "checks": {"headerDrift": "none", "proverError": False, "workspaceChanged": True},
            },
            indent=2,
        ),
    )
    write(
        artifacts / "validation" / "FATEM_39.lean.json",
        json.dumps(
            {
                "relPath": "FATEM/39.lean",
                "acceptanceStatus": "accepted",
                "validationStatus": "passed",
                "checks": {"headerDrift": "none", "proverError": False, "workspaceChanged": True},
            },
            indent=2,
        ),
    )
    write(artifacts / "proofs" / "FATEM" / "39.lean", "theorem foo : True := by\n  trivial\n")

    export_run_artifacts(run_root)

    payload = json.loads((artifacts / "validation" / "FATEM_39.lean.json").read_text(encoding="utf-8"))
    assert payload["acceptanceStatus"] == "accepted"
    assert payload["validationStatus"] == "passed"


def test_shell_runtime_defaults_use_codex_config_flag():
    loop_script = (ROOT / "archon-loop.sh").read_text(encoding="utf-8")
    review_script = (ROOT / "review.sh").read_text(encoding="utf-8")

    assert "--config model_reasoning_effort=xhigh" in loop_script
    assert "--config model_reasoning_effort=xhigh" in review_script
    assert "--c model_reasoning_effort=xhigh" not in loop_script
    assert "--c model_reasoning_effort=xhigh" not in review_script


def test_shell_runtime_codex_extra_args_assignments_expand_correctly():
    for script_path in (ROOT / "archon-loop.sh", ROOT / "review.sh"):
        lines = script_path.read_text(encoding="utf-8").splitlines()
        start = next(
            index
            for index, line in enumerate(lines)
            if line.strip() == 'if [[ -n "${ARCHON_CODEX_EXEC_ARGS:-}" ]]; then'
        )
        end = next(index for index in range(start, len(lines)) if lines[index].strip() == "fi")
        assignment_block = "\n".join(lines[start : end + 1])

        default_result = subprocess.run(
            [
                "bash",
                "-lc",
                "\n".join(
                    [
                        "unset ARCHON_CODEX_EXEC_ARGS",
                        assignment_block,
                        'printf "%s" "$CODEX_EXTRA_ARGS"',
                    ]
                ),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert default_result.returncode == 0
        assert default_result.stdout == "--config model_reasoning_effort=xhigh"

        override_result = subprocess.run(
            [
                "bash",
                "-lc",
                "\n".join(
                    [
                        'export ARCHON_CODEX_EXEC_ARGS="--search --config model_reasoning_effort=medium"',
                        assignment_block,
                        'printf "%s" "$CODEX_EXTRA_ARGS"',
                    ]
                ),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert override_result.returncode == 0
        assert override_result.stdout == "--search --config model_reasoning_effort=medium"


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
    write_stale_planner_state(workspace, "FATEM/42.lean")
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

    progress_text = (workspace / ".archon" / "PROGRESS.md").read_text(encoding="utf-8")
    assert "## Current Stage\nCOMPLETE" in progress_text
    assert "Accepted blocker note `FATEM_42.lean.md` validated" in progress_text

    pending_text = (workspace / ".archon" / "task_pending.md").read_text(encoding="utf-8")
    assert "No pending tasks in the current scope." in pending_text

    done_text = (workspace / ".archon" / "task_done.md").read_text(encoding="utf-8")
    assert "`FATEM/42.lean`" in done_text
    assert "`FATEM_42.lean.md`" in done_text
    assert "Accepted blocker note" in done_text


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


def test_supervised_cycle_raises_prover_timeout_for_tail_scope(tmp_path: Path):
    source = tmp_path / "source"
    workspace = tmp_path / "workspace"
    write(source / "FATEM" / "2.lean", "theorem foo : True := by\n  sorry\n")
    write(source / "FATEM" / "3.lean", "theorem bar : True := by\n  sorry\n")
    write(workspace / "FATEM" / "2.lean", "theorem foo : True := by\n  sorry\n")
    write(workspace / "FATEM" / "3.lean", "theorem bar : True := by\n  sorry\n")
    write(
        workspace / ".archon" / "RUN_SCOPE.md",
        """
        # Run Scope

        ## Allowed Files

        1. `FATEM/2.lean`
        2. `FATEM/3.lean`
        """,
    )
    write(
        workspace / ".archon" / "PROGRESS.md",
        """
        # Project Progress

        ## Current Stage
        prover

        ## Stages
        - [x] init
        - [x] autoformalize
        - [ ] prover
        - [ ] polish

        ## Current Objectives

        1. **FATEM/2.lean** — Continue prover work.
        2. **FATEM/3.lean** — Continue prover work.
        """,
    )
    env_dump = tmp_path / "tail-env.json"
    fake_loop = tmp_path / "fake-archon-loop.sh"
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
            "--tail-scope-objective-threshold",
            "2",
            "--tail-scope-plan-timeout-seconds",
            "300",
            "--tail-scope-prover-timeout-seconds",
            "360",
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
        "ARCHON_PLAN_TIMEOUT_SECONDS": "300",
        "ARCHON_PROVER_TIMEOUT_SECONDS": "360",
        "ARCHON_REVIEW_TIMEOUT_SECONDS": None,
    }
    hot_notes = (workspace / ".archon" / "supervisor" / "HOT_NOTES.md").read_text(encoding="utf-8")
    assert "Tail-scope runtime override: raised plan timeout to 300s and prover timeout to 360s for 2 current objectives" in hot_notes


def test_supervised_cycle_enables_known_route_fast_path_for_tail_scope(tmp_path: Path):
    source = tmp_path / "source"
    workspace = tmp_path / "workspace"
    write(source / "FATEM" / "94.lean", "theorem foo : True := by\n  sorry\n")
    write(workspace / "FATEM" / "94.lean", "theorem foo : True := by\n  sorry\n")
    write(
        workspace / ".archon" / "RUN_SCOPE.md",
        """
        # Run Scope

        ## Allowed Files

        1. `FATEM/94.lean`
        """,
    )
    write(
        workspace / ".archon" / "PROGRESS.md",
        """
        # Project Progress

        ## Current Stage
        prover

        ## Current Objectives

        1. **FATEM/94.lean** — Apply the exact compile-checked route from `.archon/informal/FATEM_94.md`.
        """,
    )
    write(
        workspace / ".archon" / "informal" / "FATEM_94.md",
        """
        # FATEM/94

        Exact compile-checked route:

        ```lean
        trivial
        ```
        """,
    )
    env_dump = tmp_path / "fast-path-env.json"
    fake_loop = tmp_path / "fake-archon-loop.sh"
    write(
        fake_loop,
        f"""
        #!/usr/bin/env bash
        python3 - <<'EOF'
        import json
        import os
        from pathlib import Path

        Path("{env_dump}").write_text(json.dumps({{
            "ARCHON_SKIP_INITIAL_PLAN": os.environ.get("ARCHON_SKIP_INITIAL_PLAN"),
            "ARCHON_SKIP_INITIAL_PLAN_REASON": os.environ.get("ARCHON_SKIP_INITIAL_PLAN_REASON"),
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
            "--skip-process-check",
            "--tail-scope-objective-threshold",
            "1",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 4
    payload = json.loads(env_dump.read_text(encoding="utf-8"))
    assert payload == {
        "ARCHON_SKIP_INITIAL_PLAN": "1",
        "ARCHON_SKIP_INITIAL_PLAN_REASON": "known_routes",
    }
    hot_notes = (workspace / ".archon" / "supervisor" / "HOT_NOTES.md").read_text(encoding="utf-8")
    assert "Plan fast-path: skipped the initial plan phase because every tail-scope objective already had a known route" in hot_notes


def test_supervised_cycle_enables_known_route_fast_path_for_realistic_shortest_route_note(tmp_path: Path):
    source = tmp_path / "source"
    workspace = tmp_path / "workspace"
    write(source / "FATEM" / "94.lean", "theorem foo : True := by\n  sorry\n")
    write(workspace / "FATEM" / "94.lean", "theorem foo : True := by\n  sorry\n")
    write(
        workspace / ".archon" / "RUN_SCOPE.md",
        """
        # Run Scope

        ## Allowed Files

        1. `FATEM/94.lean`
        """,
    )
    write(
        workspace / ".archon" / "PROGRESS.md",
        """
        # Project Progress

        ## Current Stage
        prover

        ## Current Objectives

        1. **FATEM/94.lean** — Fill the remaining sorry in `foo`. This should be a short rewrite, not a search-heavy proof. Informal note: `.archon/informal/FATEM_94.md`.
        """,
    )
    write(
        workspace / ".archon" / "task_pending.md",
        """
        # Pending Tasks

        - `FATEM/94.lean` — 1 sorry remains. Exact rewrite route in `.archon/informal/FATEM_94.md`.
        """,
    )
    write(
        workspace / ".archon" / "informal" / "FATEM_94.md",
        """
        # FATEM/94

        Shortest route:

        1. Rewrite the goal directly.
        2. Use the existing theorem and simplify.

        Expected proof shape is essentially:

        ```lean
        simpa using trivial
        ```
        """,
    )
    env_dump = tmp_path / "realistic-fast-path-env.json"
    fake_loop = tmp_path / "fake-archon-loop.sh"
    write(
        fake_loop,
        f"""
        #!/usr/bin/env bash
        python3 - <<'EOF'
        import json
        import os
        from pathlib import Path

        Path("{env_dump}").write_text(json.dumps({{
            "ARCHON_SKIP_INITIAL_PLAN": os.environ.get("ARCHON_SKIP_INITIAL_PLAN"),
            "ARCHON_SKIP_INITIAL_PLAN_REASON": os.environ.get("ARCHON_SKIP_INITIAL_PLAN_REASON"),
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
            "--skip-process-check",
            "--tail-scope-objective-threshold",
            "1",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 4
    payload = json.loads(env_dump.read_text(encoding="utf-8"))
    assert payload == {
        "ARCHON_SKIP_INITIAL_PLAN": "1",
        "ARCHON_SKIP_INITIAL_PLAN_REASON": "known_routes",
    }


def test_supervised_cycle_can_preload_historical_exact_routes_opt_in(tmp_path: Path):
    campaigns_root = tmp_path / "campaigns"
    current_campaign = campaigns_root / "20260415-current"
    historical_campaign = campaigns_root / "20260414-history"
    source = current_campaign / "runs" / "teacher-94" / "source"
    workspace = current_campaign / "runs" / "teacher-94" / "workspace"
    write_campaign_manifest(current_campaign)

    write(source / "FATEM" / "94.lean", "theorem foo : True := by\n  sorry\n")
    write(workspace / "FATEM" / "94.lean", "theorem foo : True := by\n  sorry\n")
    write(
        workspace / ".archon" / "RUN_SCOPE.md",
        """
        # Run Scope

        ## Allowed Files

        1. `FATEM/94.lean`
        """,
    )
    write(
        historical_campaign / "reports" / "final" / "proofs" / "teacher-old" / "FATEM" / "94.lean",
        """
        theorem foo : True := by
          trivial
        """,
    )

    env_dump = tmp_path / "historical-proof-env.json"
    fake_loop = tmp_path / "fake-archon-loop.sh"
    write(
        fake_loop,
        f"""
        #!/usr/bin/env bash
        python3 - <<'EOF'
        import json
        import os
        from pathlib import Path

        Path("{env_dump}").write_text(json.dumps({{
            "ARCHON_SKIP_INITIAL_PLAN": os.environ.get("ARCHON_SKIP_INITIAL_PLAN"),
            "ARCHON_SKIP_INITIAL_PLAN_REASON": os.environ.get("ARCHON_SKIP_INITIAL_PLAN_REASON"),
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
            "--skip-process-check",
            "--tail-scope-objective-threshold",
            "1",
            "--preload-historical-routes",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 4
    payload = json.loads(env_dump.read_text(encoding="utf-8"))
    assert payload == {
        "ARCHON_SKIP_INITIAL_PLAN": "1",
        "ARCHON_SKIP_INITIAL_PLAN_REASON": "known_routes",
    }

    historical_routes = (workspace / ".archon" / "HISTORICAL_ROUTES.md").read_text(encoding="utf-8")
    assert "Historical accepted proof route preloaded" in historical_routes
    assert "Exact compile-checked route" in historical_routes
    assert "teacher-old" in historical_routes

    note_path = workspace / ".archon" / "informal" / "historical_routes" / "fatem_94_accepted_proof.md"
    assert note_path.exists()
    assert "trivial" in note_path.read_text(encoding="utf-8")

    manifest_payload = json.loads(
        (workspace / ".archon" / "supervisor" / "historical-routes.json").read_text(encoding="utf-8")
    )
    assert manifest_payload["records"][0]["kind"] == "proof"

    progress_payload = json.loads(
        (workspace / ".archon" / "supervisor" / "progress-summary.json").read_text(encoding="utf-8")
    )
    assert progress_payload["historicalRoutes"]["enabled"] is True
    assert progress_payload["historicalRoutes"]["count"] == 1
    assert progress_payload["planFastPathApplied"] is True

    hot_notes = (workspace / ".archon" / "supervisor" / "HOT_NOTES.md").read_text(encoding="utf-8")
    assert "Historical routes preloaded: 1" in hot_notes
    assert "FATEM/94.lean [proof]" in hot_notes


def test_supervised_cycle_can_preload_historical_blocker_routes_opt_in(tmp_path: Path):
    campaigns_root = tmp_path / "campaigns"
    current_campaign = campaigns_root / "20260415-current"
    historical_campaign = campaigns_root / "20260414-history"
    source = current_campaign / "runs" / "teacher-42" / "source"
    workspace = current_campaign / "runs" / "teacher-42" / "workspace"
    write_campaign_manifest(current_campaign)

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
        historical_campaign / "reports" / "final" / "blockers" / "teacher-old" / "FATEM_42.lean.md",
        """
        # FATEM/42.lean

        - **Concrete blocker:** The theorem is false as written.
        - Lean-validated evidence: the obstruction is real.
        """,
    )

    env_dump = tmp_path / "historical-blocker-env.json"
    fake_loop = tmp_path / "fake-archon-loop.sh"
    write(
        fake_loop,
        f"""
        #!/usr/bin/env bash
        python3 - <<'EOF'
        import json
        import os
        from pathlib import Path

        Path("{env_dump}").write_text(json.dumps({{
            "ARCHON_SKIP_INITIAL_PLAN": os.environ.get("ARCHON_SKIP_INITIAL_PLAN"),
            "ARCHON_SKIP_INITIAL_PLAN_REASON": os.environ.get("ARCHON_SKIP_INITIAL_PLAN_REASON"),
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
            "--skip-process-check",
            "--tail-scope-objective-threshold",
            "1",
            "--preload-historical-routes",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 4
    payload = json.loads(env_dump.read_text(encoding="utf-8"))
    assert payload == {
        "ARCHON_SKIP_INITIAL_PLAN": "1",
        "ARCHON_SKIP_INITIAL_PLAN_REASON": "known_routes",
    }

    historical_routes = (workspace / ".archon" / "HISTORICAL_ROUTES.md").read_text(encoding="utf-8")
    assert "Historical accepted blocker route preloaded" in historical_routes
    assert "Lean-validated blocker route: false as written." in historical_routes

    note_path = workspace / ".archon" / "informal" / "historical_routes" / "fatem_42_accepted_blocker.md"
    note_text = note_path.read_text(encoding="utf-8")
    assert "Lean-validated blocker route" in note_text
    assert "false as written" in note_text

    progress_payload = json.loads(
        (workspace / ".archon" / "supervisor" / "progress-summary.json").read_text(encoding="utf-8")
    )
    assert progress_payload["historicalRoutes"]["enabled"] is True
    assert progress_payload["historicalRoutes"]["count"] == 1
    assert progress_payload["planFastPathApplied"] is True


def test_supervised_cycle_clears_stale_historical_routes_when_opt_in_is_off(tmp_path: Path):
    source = tmp_path / "source"
    workspace = tmp_path / "workspace"
    write(source / "FATEM" / "94.lean", "theorem foo : True := by\n  sorry\n")
    write(workspace / "FATEM" / "94.lean", "theorem foo : True := by\n  sorry\n")
    write(
        workspace / ".archon" / "RUN_SCOPE.md",
        """
        # Run Scope

        ## Allowed Files

        1. `FATEM/94.lean`
        """,
    )
    write(
        workspace / ".archon" / "HISTORICAL_ROUTES.md",
        """
        # Historical Routes

        - `FATEM/94.lean` — Exact compile-checked route in `.archon/informal/historical_routes/fatem_94_accepted_proof.md`.
        """,
    )
    write(
        workspace / ".archon" / "informal" / "historical_routes" / "fatem_94_accepted_proof.md",
        """
        # stale

        Exact compile-checked route:

        ```lean
        trivial
        ```
        """,
    )
    write(
        workspace / ".archon" / "supervisor" / "historical-routes.json",
        json.dumps({"schemaVersion": 1, "records": [{"relPath": "FATEM/94.lean"}]}, sort_keys=True),
    )

    env_dump = tmp_path / "historical-cleared-env.json"
    fake_loop = tmp_path / "fake-archon-loop.sh"
    write(
        fake_loop,
        f"""
        #!/usr/bin/env bash
        python3 - <<'EOF'
        import json
        import os
        from pathlib import Path

        Path("{env_dump}").write_text(json.dumps({{
            "ARCHON_SKIP_INITIAL_PLAN": os.environ.get("ARCHON_SKIP_INITIAL_PLAN"),
            "ARCHON_SKIP_INITIAL_PLAN_REASON": os.environ.get("ARCHON_SKIP_INITIAL_PLAN_REASON"),
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
            "--skip-process-check",
            "--tail-scope-objective-threshold",
            "1",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 4
    payload = json.loads(env_dump.read_text(encoding="utf-8"))
    assert payload == {
        "ARCHON_SKIP_INITIAL_PLAN": None,
        "ARCHON_SKIP_INITIAL_PLAN_REASON": None,
    }
    assert not (workspace / ".archon" / "HISTORICAL_ROUTES.md").exists()
    assert not (workspace / ".archon" / "informal" / "historical_routes").exists()
    assert not (workspace / ".archon" / "supervisor" / "historical-routes.json").exists()


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


def test_supervised_cycle_clears_stale_terminal_lease_fields_when_starting_new_cycle(tmp_path: Path):
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
                "active": False,
                "status": "completed",
                "workspace": str(workspace),
                "source": str(source),
                "supervisorPid": 123,
                "loopPid": None,
                "updatedAt": "2026-04-12T00:00:00+00:00",
                "lastHeartbeatAt": "2026-04-12T00:00:00+00:00",
                "startedAt": "2026-04-12T00:00:00+00:00",
                "completedAt": "2026-04-12T00:01:00+00:00",
                "finalStatus": "clean",
                "loopExitCode": 0,
                "recoveryEvent": "verified_in_recovery",
                "validationFiles": ["FATEM_2.lean.json"],
                "lessonFile": "iter-001-clean.json",
            },
            indent=2,
        ),
    )
    fake_loop = tmp_path / "fake-archon-loop.sh"
    write(
        fake_loop,
        """
        #!/usr/bin/env bash
        sleep 2
        exit 0
        """,
    )
    fake_loop.chmod(0o755)

    proc = subprocess.Popen(
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
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        lease_path = workspace / ".archon" / "supervisor" / "run-lease.json"
        deadline = time.monotonic() + 5
        observed_payload = None
        while time.monotonic() < deadline:
            if lease_path.exists():
                payload = json.loads(lease_path.read_text(encoding="utf-8"))
                if payload.get("status") == "starting" and payload.get("startedAt") != "2026-04-12T00:00:00+00:00":
                    observed_payload = payload
                    break
            time.sleep(0.05)

        assert observed_payload is not None
        assert "completedAt" not in observed_payload
        assert "finalStatus" not in observed_payload
        assert "loopExitCode" not in observed_payload
        assert "recoveryEvent" not in observed_payload
        assert "validationFiles" not in observed_payload
        assert "lessonFile" not in observed_payload
    finally:
        stdout, stderr = proc.communicate(timeout=10)

    assert proc.returncode == 4, (stdout, stderr)


def test_supervised_cycle_recovery_only_verifies_existing_artifacts_and_closes_lease(tmp_path: Path):
    source = tmp_path / "source"
    workspace = tmp_path / "workspace"
    write(source / "FATEM" / "2.lean", "theorem foo : True\n    := by\n  sorry\n")
    write(workspace / "FATEM" / "2.lean", "theorem foo : True\n    := by\n  trivial\n")
    write_stale_planner_state(workspace, "FATEM/2.lean")
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

    progress_text = (workspace / ".archon" / "PROGRESS.md").read_text(encoding="utf-8")
    assert "## Current Stage\nCOMPLETE" in progress_text
    assert "Accepted proof validated" in progress_text

    pending_text = (workspace / ".archon" / "task_pending.md").read_text(encoding="utf-8")
    assert "No pending tasks in the current scope." in pending_text

    done_text = (workspace / ".archon" / "task_done.md").read_text(encoding="utf-8")
    assert "`FATEM/2.lean`" in done_text
    assert "Accepted proof validated" in done_text

    lease_payload = json.loads((workspace / ".archon" / "supervisor" / "run-lease.json").read_text(encoding="utf-8"))
    assert lease_payload["active"] is False
    assert lease_payload["finalStatus"] == "clean"
    assert lease_payload["recoveryEvent"] == "verified_in_recovery"


def test_supervised_cycle_focuses_remaining_scope_when_only_part_of_run_is_accepted(tmp_path: Path):
    source = tmp_path / "source"
    workspace = tmp_path / "workspace"
    write(source / "FATEM" / "1.lean", "theorem foo : True := by\n  sorry\n")
    write(source / "FATEM" / "2.lean", "theorem bar : True := by\n  sorry\n")
    write(workspace / "FATEM" / "1.lean", "theorem foo : True := by\n  trivial\n")
    write(workspace / "FATEM" / "2.lean", "theorem bar : True := by\n  sorry\n")
    write_stale_planner_state(workspace, "FATEM/1.lean")
    write(
        workspace / ".archon" / "task_pending.md",
        """
        # Pending Tasks

        - `FATEM/1.lean` — queued.
        - `FATEM/2.lean` — queued.
        """,
    )
    write(
        workspace / ".archon" / "RUN_SCOPE.md",
        """
        # Run Scope

        ## Allowed Files

        1. `FATEM/1.lean`
        2. `FATEM/2.lean`
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
    progress_text = (workspace / ".archon" / "PROGRESS.md").read_text(encoding="utf-8")
    assert "## Current Stage\nprover" in progress_text
    assert "**FATEM/2.lean**" in progress_text
    assert "**FATEM/1.lean**" not in progress_text

    pending_text = (workspace / ".archon" / "task_pending.md").read_text(encoding="utf-8")
    assert "`FATEM/2.lean`" in pending_text
    assert "`FATEM/1.lean`" not in pending_text

    done_text = (workspace / ".archon" / "task_done.md").read_text(encoding="utf-8")
    assert "`FATEM/1.lean`" in done_text
    assert "Accepted proof validated" in done_text

    hot_notes = (workspace / ".archon" / "supervisor" / "HOT_NOTES.md").read_text(encoding="utf-8")
    assert "removed accepted targets from the next-cycle objective list" in hot_notes


def test_supervised_cycle_writes_run_progress_summary_with_helper_note_observability(tmp_path: Path):
    source = tmp_path / "source"
    workspace = tmp_path / "workspace"
    write(source / "FATEM" / "2.lean", "theorem foo : True := by\n  sorry\n")
    write(workspace / "FATEM" / "2.lean", "theorem foo : True := by\n  trivial\n")
    write_runtime_config(workspace, helper_enabled=True, write_progress_surface=True)
    write(workspace / ".archon" / "informal" / "helper" / "route.md", "# helper route\n")
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
    markdown_path = workspace / ".archon" / "supervisor" / "progress-summary.md"
    json_path = workspace / ".archon" / "supervisor" / "progress-summary.json"
    assert markdown_path.exists()
    assert json_path.exists()

    markdown = markdown_path.read_text(encoding="utf-8")
    payload = json.loads(json_path.read_text(encoding="utf-8"))

    assert "# Run Progress" in markdown
    assert "100% (1/1 closed targets)" in markdown
    assert "Helper notes observed: `1`" in markdown
    assert payload["status"] == "clean"
    assert payload["helper"]["enabled"] is True
    assert payload["helper"]["noteCount"] == 1
    assert payload["progress"]["percent"] == 100


def test_supervised_cycle_updates_run_progress_surface_during_live_loop(tmp_path: Path):
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

    proc = subprocess.Popen(
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
            "3",
            "--monitor-poll-seconds",
            "0.1",
        ],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        json_path = workspace / ".archon" / "supervisor" / "progress-summary.json"
        deadline = time.time() + 4
        live_payload = None
        while time.time() < deadline:
            if json_path.exists():
                payload = json.loads(json_path.read_text(encoding="utf-8"))
                live = payload.get("liveRuntime")
                if payload.get("status") == "running" and isinstance(live, dict) and live.get("proverStatus") == "running":
                    live_payload = payload
                    break
            time.sleep(0.1)
        assert live_payload is not None
        assert live_payload["liveRuntime"]["iteration"] == "iter-001"
        assert live_payload["liveRuntime"]["activeProvers"][0]["file"] == "FATEM/2.lean"
    finally:
        stdout, stderr = proc.communicate(timeout=10)

    assert proc.returncode == 5, (stdout, stderr)


def test_supervised_cycle_can_disable_run_progress_surface_via_runtime_config(tmp_path: Path):
    source = tmp_path / "source"
    workspace = tmp_path / "workspace"
    write(source / "FATEM" / "2.lean", "theorem foo : True := by\n  sorry\n")
    write(workspace / "FATEM" / "2.lean", "theorem foo : True := by\n  trivial\n")
    write_runtime_config(workspace, write_progress_surface=False)
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
    assert not (workspace / ".archon" / "supervisor" / "progress-summary.md").exists()
    assert not (workspace / ".archon" / "supervisor" / "progress-summary.json").exists()


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
