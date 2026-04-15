from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARCHON_LOOP = ROOT / "archon-loop.sh"


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")


def make_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    state = project / ".archon"
    (state / "logs").mkdir(parents=True, exist_ok=True)
    (state / "task_results").mkdir(parents=True, exist_ok=True)
    (state / "proof-journal" / "sessions").mkdir(parents=True, exist_ok=True)

    write(
        project / "Foo.lean",
        """
        import Mathlib

        theorem foo : True := by
          trivial
        """,
    )
    write(
        state / "PROGRESS.md",
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

        1. **Foo.lean** — Recheck the only scoped target.
        """,
    )
    write(
        state / "RUN_SCOPE.md",
        """
        # Run Scope

        Treat this file as a hard constraint.
        Plan and prover agents must stay within the allowed files listed below.

        - Include regex: `Foo`
        - Objective limit: `1`

        ## Allowed Files

        1. `Foo.lean`
        """,
    )
    write(state / "AGENTS.md", "# Test agents\n")
    write(state / "USER_HINTS.md", "# User Hints\n\nNo pending user hints.\n")
    write(state / "task_pending.md", "# Pending Tasks\n\n- `Foo.lean` — queued.\n")
    write(state / "task_done.md", "# Completed Tasks\n\n- None.\n")

    return project


def make_fake_codex(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    script = bin_dir / "codex"
    write(
        script,
        r"""
        #!/usr/bin/env python3
        import json
        import os
        import pathlib
        import sys

        args = sys.argv[1:]
        if args[:2] == ["exec", "--help"]:
            print("Usage: codex exec [--search]")
            raise SystemExit(0)

        prompt = sys.stdin.read()
        if not prompt:
            prompt = args[-1] if args else ""
        if "plan agent" in prompt:
            role = "plan"
        elif "prover agent" in prompt:
            role = "prover"
        elif "review agent" in prompt:
            role = "review"
        else:
            role = "other"

        exit_code = int(os.environ.get(f"FAKE_{role.upper()}_EXIT_CODE", "0"))
        if role == "other":
            sequence = os.environ.get("FAKE_OTHER_EXIT_SEQUENCE")
            counter_file = os.environ.get("FAKE_OTHER_COUNTER_FILE")
            if sequence and counter_file:
                codes = [int(chunk.strip()) for chunk in sequence.split(",") if chunk.strip()]
                counter_path = pathlib.Path(counter_file)
                current = int(counter_path.read_text(encoding="utf-8")) if counter_path.exists() else 0
                if codes:
                    exit_code = codes[min(current, len(codes) - 1)]
                counter_path.write_text(str(current + 1), encoding="utf-8")

        calls_log = os.environ.get("FAKE_CODEX_CALLS_LOG")
        if calls_log:
            with open(calls_log, "a", encoding="utf-8") as handle:
                handle.write(f"{role}\n")

        if role == "plan":
            run_scope_path = os.environ.get("FAKE_PLAN_RUN_SCOPE_PATH")
            run_scope_content = os.environ.get("FAKE_PLAN_RUN_SCOPE_CONTENT")
            if run_scope_path and run_scope_content is not None:
                pathlib.Path(run_scope_path).write_text(run_scope_content, encoding="utf-8")

        print(json.dumps({"type": "thread.started", "thread_id": "test-thread"}))
        print(
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": f"{role} completed"},
                }
            )
        )
        print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}}))
        raise SystemExit(exit_code)
        """,
    )
    script.chmod(0o755)
    return bin_dir


def run_archon(project: Path, fake_bin_dir: Path, *args: str, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin_dir}:{env['PATH']}"
    env["ARCHON_CODEX_MODEL"] = "fake-model"
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["bash", str(ARCHON_LOOP), "--no-review", *args, str(project)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_dry_run_exits_after_one_iteration(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    fake_bin_dir = make_fake_codex(tmp_path)

    result = run_archon(project, fake_bin_dir, "--dry-run", "--max-iterations", "3")

    assert result.returncode == 0
    assert "Iteration 1/" in result.stdout
    assert "Iteration 2/" not in result.stdout


def test_dry_run_prompt_prioritizes_local_state_before_role_contracts(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    fake_bin_dir = make_fake_codex(tmp_path)

    result = run_archon(project, fake_bin_dir, "--dry-run", "--max-iterations", "1")

    assert result.returncode == 0
    assert f"Start with {project}/.archon/PROGRESS.md and {project}/.archon/RUN_SCOPE.md to recover the active scoped objectives." in result.stdout
    assert "Only consult" in result.stdout


def test_scope_change_during_plan_is_preserved_and_blocks_prover(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    fake_bin_dir = make_fake_codex(tmp_path)
    run_scope_path = project / ".archon" / "RUN_SCOPE.md"
    original_scope = run_scope_path.read_text(encoding="utf-8")
    updated_scope = original_scope.replace("Objective limit: `1`", "Objective limit: `99`")
    calls_log = tmp_path / "codex-calls.log"

    result = run_archon(
        project,
        fake_bin_dir,
        "--max-iterations",
        "1",
        env_extra={
            "FAKE_CODEX_CALLS_LOG": str(calls_log),
            "FAKE_PLAN_RUN_SCOPE_PATH": str(run_scope_path),
            "FAKE_PLAN_RUN_SCOPE_CONTENT": updated_scope,
        },
    )

    combined_output = f"{result.stdout}\n{result.stderr}"

    assert result.returncode == 0
    assert "RUN_SCOPE.md changed during plan phase" in combined_output
    assert "Skipping prover phase because the plan phase did not complete successfully." in combined_output
    assert run_scope_path.read_text(encoding="utf-8") == updated_scope
    assert calls_log.read_text(encoding="utf-8").splitlines() == ["other", "plan"]


def test_plan_failure_falls_back_to_existing_objectives_when_no_live_results(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    fake_bin_dir = make_fake_codex(tmp_path)
    calls_log = tmp_path / "codex-calls.log"

    result = run_archon(
        project,
        fake_bin_dir,
        "--max-iterations",
        "1",
        env_extra={
            "FAKE_CODEX_CALLS_LOG": str(calls_log),
            "FAKE_PLAN_EXIT_CODE": "1",
        },
    )

    combined_output = f"{result.stdout}\n{result.stderr}"

    assert result.returncode == 0
    assert "Plan agent exited with an error" in combined_output
    assert "Continuing to prover with the current PROGRESS.md." in combined_output
    assert "Skipping prover phase because the plan phase did not complete successfully." not in combined_output
    assert calls_log.read_text(encoding="utf-8").splitlines() == ["other", "plan", "prover"]


def test_skip_initial_plan_fast_path_goes_straight_to_prover(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    fake_bin_dir = make_fake_codex(tmp_path)
    calls_log = tmp_path / "codex-calls.log"

    result = run_archon(
        project,
        fake_bin_dir,
        "--max-iterations",
        "1",
        env_extra={
            "FAKE_CODEX_CALLS_LOG": str(calls_log),
            "ARCHON_SKIP_INITIAL_PLAN": "1",
            "ARCHON_SKIP_INITIAL_PLAN_REASON": "known_routes",
        },
    )

    combined_output = f"{result.stdout}\n{result.stderr}"

    assert result.returncode == 0
    assert "Skipping initial plan phase (known_routes)" in combined_output
    assert calls_log.read_text(encoding="utf-8").splitlines() == ["other", "prover"]


def test_plan_failure_with_live_task_results_still_blocks_prover(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    fake_bin_dir = make_fake_codex(tmp_path)
    calls_log = tmp_path / "codex-calls.log"
    write(project / ".archon" / "task_results" / "Foo.lean.md", "# Result\n\nPending merge.\n")

    result = run_archon(
        project,
        fake_bin_dir,
        "--max-iterations",
        "1",
        env_extra={
            "FAKE_CODEX_CALLS_LOG": str(calls_log),
            "FAKE_PLAN_EXIT_CODE": "1",
        },
    )

    combined_output = f"{result.stdout}\n{result.stderr}"

    assert result.returncode == 0
    assert "Plan agent exited with an error" in combined_output
    assert "Continuing to prover with the current PROGRESS.md." not in combined_output
    assert "Skipping prover phase because the plan phase did not complete successfully." in combined_output
    assert calls_log.read_text(encoding="utf-8").splitlines() == ["other", "plan"]


def test_preflight_retries_transient_codex_failure(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    fake_bin_dir = make_fake_codex(tmp_path)
    calls_log = tmp_path / "codex-calls.log"
    counter_file = tmp_path / "other-counter.txt"

    result = run_archon(
        project,
        fake_bin_dir,
        "--max-iterations",
        "1",
        env_extra={
            "FAKE_CODEX_CALLS_LOG": str(calls_log),
            "FAKE_OTHER_EXIT_SEQUENCE": "1,0",
            "FAKE_OTHER_COUNTER_FILE": str(counter_file),
            "ARCHON_CODEX_READY_RETRY_DELAY_SECONDS": "0",
        },
    )

    combined_output = f"{result.stdout}\n{result.stderr}"

    assert result.returncode == 0
    assert "Codex readiness check failed (attempt 1/4)" in combined_output
    assert "Codex readiness recovered on attempt 2/4" in combined_output
    assert calls_log.read_text(encoding="utf-8").splitlines() == ["other", "other", "plan", "prover"]


def test_preflight_exhausts_retries_and_exits(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    fake_bin_dir = make_fake_codex(tmp_path)
    calls_log = tmp_path / "codex-calls.log"
    counter_file = tmp_path / "other-counter.txt"

    result = run_archon(
        project,
        fake_bin_dir,
        "--max-iterations",
        "1",
        env_extra={
            "FAKE_CODEX_CALLS_LOG": str(calls_log),
            "FAKE_OTHER_EXIT_SEQUENCE": "1,1,1",
            "FAKE_OTHER_COUNTER_FILE": str(counter_file),
            "ARCHON_CODEX_READY_RETRIES": "2",
            "ARCHON_CODEX_READY_RETRY_DELAY_SECONDS": "0",
        },
    )

    combined_output = f"{result.stdout}\n{result.stderr}"

    assert result.returncode == 1
    assert "Codex cannot run after 3 attempt(s)." in combined_output
    assert calls_log.read_text(encoding="utf-8").splitlines() == ["other", "other", "other"]


def test_parallel_prover_ignores_explanatory_paths_outside_numbered_objectives(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    fake_bin_dir = make_fake_codex(tmp_path)
    calls_log = tmp_path / "codex-calls.log"
    state = project / ".archon"

    write(
        project / "Bar.lean",
        """
        import Mathlib

        theorem bar : True := by
          sorry
        """,
    )
    write(
        state / "RUN_SCOPE.md",
        """
        # Run Scope

        Treat this file as a hard constraint.
        Plan and prover agents must stay within the allowed files listed below.

        - Include regex: `Foo|Bar`
        - Objective limit: `2`

        ## Allowed Files

        1. `Foo.lean`
        2. `Bar.lean`
        """,
    )
    write(
        state / "PROGRESS.md",
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

        Scheduler guard: only the numbered line below is a live prover target.
        - Deferred blocker: `Bar.lean` is frozen and must not be relaunched.
        1. **Foo.lean** — Recheck the only active scoped target.
        """,
    )

    result = run_archon(
        project,
        fake_bin_dir,
        "--max-iterations",
        "1",
        env_extra={"FAKE_CODEX_CALLS_LOG": str(calls_log)},
    )

    combined_output = f"{result.stdout}\n{result.stderr}"

    assert result.returncode == 0
    assert "Found 1 file(s)" not in combined_output
    assert calls_log.read_text(encoding="utf-8").splitlines() == ["other", "plan", "prover"]


def test_parallel_prover_skips_when_current_objectives_has_no_numbered_lean_target(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    fake_bin_dir = make_fake_codex(tmp_path)
    calls_log = tmp_path / "codex-calls.log"
    state = project / ".archon"

    write(
        project / "Bar.lean",
        """
        import Mathlib

        theorem bar : True := by
          sorry
        """,
    )
    write(
        state / "RUN_SCOPE.md",
        """
        # Run Scope

        Treat this file as a hard constraint.
        Plan and prover agents must stay within the allowed files listed below.

        - Include regex: `Foo|Bar`
        - Objective limit: `2`

        ## Allowed Files

        1. `Foo.lean`
        2. `Bar.lean`
        """,
    )
    write(
        state / "PROGRESS.md",
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

        1. **No active proof-search target.**
           - Stop condition reached for this scoped batch.
           - Frozen blockers: `Foo.lean`, `Bar.lean`.
           - Do not relaunch proof search unless the user changes scope.
        """,
    )

    result = run_archon(
        project,
        fake_bin_dir,
        "--max-iterations",
        "1",
        env_extra={"FAKE_CODEX_CALLS_LOG": str(calls_log)},
    )

    combined_output = f"{result.stdout}\n{result.stderr}"

    assert result.returncode == 0
    assert "No files parsed from PROGRESS.md ## Current Objectives." in combined_output
    assert calls_log.read_text(encoding="utf-8").splitlines() == ["other", "plan"]
