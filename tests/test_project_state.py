from pathlib import Path

from archonlib.project_state import (
    build_objectives,
    build_task_done_markdown,
    build_task_pending_markdown,
    build_run_scope_markdown,
    detect_stage,
    stage_markdown,
)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_detect_stage_with_sorries(tmp_path: Path):
    write(tmp_path / "lakefile.lean", "import Lake\n")
    write(
        tmp_path / "Foo.lean",
        "import Mathlib\n\ntheorem foo : True := by\n  sorry\n",
    )
    assert detect_stage(tmp_path) == "prover"


def test_build_objectives_respects_limit_and_regex(tmp_path: Path):
    write(tmp_path / "lakefile.lean", "import Lake\n")
    write(tmp_path / "A.lean", "theorem a : True := by\n  sorry\n")
    write(tmp_path / "B.lean", "theorem b : True := by\n  sorry\n")
    write(tmp_path / "C.lean", "theorem c : True := by\n  trivial\n")

    objectives = build_objectives(tmp_path, stage="prover", limit=1, include_regex="A|B")
    assert len(objectives) == 1
    assert objectives[0].rel_path == "A.lean"
    assert objectives[0].theorem_name == "a"


def test_stage_markdown_marks_skipped_autoformalize():
    markdown = stage_markdown("prover", autoformalize_skipped=True)
    assert "- [x] autoformalize" in markdown
    assert "- [ ] prover" in markdown


def test_build_objectives_uses_natural_sort(tmp_path: Path):
    write(tmp_path / "lakefile.lean", "import Lake\n")
    for name in ("FATEM/1.lean", "FATEM/2.lean", "FATEM/10.lean"):
        theorem_name = Path(name).stem
        write(tmp_path / name, f"theorem t{theorem_name} : True := by\n  sorry\n")

    objectives = build_objectives(tmp_path, stage="prover", limit=3)
    assert [objective.rel_path for objective in objectives] == [
        "FATEM/1.lean",
        "FATEM/2.lean",
        "FATEM/10.lean",
    ]


def test_build_objectives_ignores_archon_state_files(tmp_path: Path):
    write(tmp_path / "lakefile.lean", "import Lake\n")
    write(tmp_path / "FATEM/1.lean", "theorem live : True := by\n  sorry\n")
    write(tmp_path / ".archon/logs/iter-001/snapshots/FATEM_1/baseline.lean", "theorem stale : True := by\n  sorry\n")

    objectives = build_objectives(tmp_path, stage="prover")

    assert [objective.rel_path for objective in objectives] == ["FATEM/1.lean"]


def test_build_run_scope_markdown_lists_allowed_files(tmp_path: Path):
    write(tmp_path / "lakefile.lean", "import Lake\n")
    write(tmp_path / "FATEM/1.lean", "theorem a : True := by\n  sorry\n")
    write(tmp_path / "FATEM/2.lean", "theorem b : True := by\n  sorry\n")

    markdown = build_run_scope_markdown(
        tmp_path,
        stage="prover",
        limit=1,
        include_regex="FATEM/",
    )

    assert "# Run Scope" in markdown
    assert "1. `FATEM/1.lean`" in markdown
    assert "FATEM/2.lean" not in markdown


def test_build_task_pending_markdown_matches_objectives(tmp_path: Path):
    write(tmp_path / "lakefile.lean", "import Lake\n")
    write(tmp_path / "FATEM/2.lean", "theorem t2 : True := by\n  sorry\n")

    objectives = build_objectives(tmp_path, stage="prover")
    markdown = build_task_pending_markdown(objectives)

    assert "# Pending Tasks" in markdown
    assert "`FATEM/2.lean` — `t2` at line 2; 1 sorry remains." in markdown


def test_build_task_done_markdown_defaults_to_empty_scope():
    markdown = build_task_done_markdown()

    assert "# Completed Tasks" in markdown
    assert "None completed in the current run scope yet." in markdown


def test_build_objectives_ignores_archon_snapshots(tmp_path: Path):
    write(tmp_path / "lakefile.lean", "import Lake\n")
    write(tmp_path / "FATEM/1.lean", "theorem t1 : True := by\n  sorry\n")
    write(
        tmp_path / ".archon/logs/iter-001/snapshots/FATEM_1/baseline.lean",
        "theorem baseline : True := by\n  sorry\n",
    )

    objectives = build_objectives(tmp_path, stage="prover")
    assert [objective.rel_path for objective in objectives] == ["FATEM/1.lean"]
