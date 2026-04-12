from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


DECL_RE = re.compile(r"^\s*(theorem|lemma|example|def|structure|class|inductive)\b", re.M)
THEOREM_RE = re.compile(r"^\s*(theorem|lemma|example)\s+([^\s:(]+)", re.M)
SORRY_RE = re.compile(r"\bsorry\b")


def natural_key(path: Path) -> tuple:
    rel = str(path)
    parts = re.split(r"(\d+)", rel)
    key = []
    for part in parts:
        if part.isdigit():
            key.append(int(part))
        else:
            key.append(part)
    return tuple(key)


def iter_lean_files(project_path: Path) -> list[Path]:
    files: list[Path] = []
    for path in project_path.rglob("*.lean"):
        if any(part in {".archon", ".lake", "lake-packages"} for part in path.parts):
            continue
        files.append(path)
    return sorted(files, key=natural_key)


def has_lean_project(project_path: Path) -> bool:
    return any((project_path / name).exists() for name in ("lakefile.lean", "lakefile.toml", "lean-toolchain"))


@dataclass(frozen=True)
class Objective:
    rel_path: str
    theorem_name: str
    line_no: int

    def to_markdown(self, index: int) -> str:
        theorem = f"`{self.theorem_name}`" if self.theorem_name else "the remaining declaration"
        return f"{index}. **{self.rel_path}** — Fill the remaining sorry in {theorem} (line {self.line_no})."

    def to_task_markdown(self) -> str:
        theorem = f"`{self.theorem_name}`" if self.theorem_name else "the remaining declaration"
        return f"- `{self.rel_path}` — {theorem} at line {self.line_no}; 1 sorry remains."


def detect_stage(project_path: Path) -> str:
    lean_files = iter_lean_files(project_path)
    if not lean_files:
        return "autoformalize"

    any_decl = False
    any_sorry = False
    for path in lean_files:
        text = path.read_text(encoding="utf-8", errors="replace")
        if DECL_RE.search(text):
            any_decl = True
        if SORRY_RE.search(text):
            any_sorry = True
    if not any_decl:
        return "autoformalize"
    if any_sorry:
        return "prover"
    return "polish"


def objective_for_file(project_path: Path, file_path: Path) -> Objective:
    text = file_path.read_text(encoding="utf-8", errors="replace")
    match = THEOREM_RE.search(text)
    theorem_name = match.group(2) if match else ""
    line_no = 1
    for idx, line in enumerate(text.splitlines(), start=1):
        if "sorry" in line:
            line_no = idx
            break
    return Objective(
        rel_path=str(file_path.relative_to(project_path)),
        theorem_name=theorem_name,
        line_no=line_no,
    )


def build_objectives(
    project_path: Path,
    *,
    stage: str,
    limit: int | None = None,
    include_regex: str | None = None,
) -> list[Objective]:
    matcher = re.compile(include_regex) if include_regex else None
    files = []
    for path in iter_lean_files(project_path):
        rel_path = str(path.relative_to(project_path))
        if matcher and not matcher.search(rel_path):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if stage == "prover" and "sorry" not in text:
            continue
        files.append(path)
    if limit is not None:
        files = files[:limit]
    return [objective_for_file(project_path, path) for path in files]


def build_run_scope_markdown(
    project_path: Path,
    *,
    stage: str,
    limit: int | None = None,
    include_regex: str | None = None,
) -> str:
    lines = [
        "# Run Scope",
        "",
        "Treat this file as a hard constraint.",
        "Plan and prover agents must stay within the allowed files listed below.",
        "",
        f"- Include regex: `{include_regex}`" if include_regex else "- Include regex: unrestricted",
        f"- Objective limit: `{limit}`" if limit is not None else "- Objective limit: unrestricted",
        "",
        "## Allowed Files",
        "",
    ]
    objectives = build_objectives(
        project_path,
        stage=stage,
        limit=limit,
        include_regex=include_regex,
    )
    if objectives:
        for index, objective in enumerate(objectives, start=1):
            lines.append(f"{index}. `{objective.rel_path}`")
    else:
        lines.append("1. No files matched the current scope filters.")
    lines.append("")
    return "\n".join(lines)


def build_task_pending_markdown(objectives: list[Objective]) -> str:
    lines = ["# Pending Tasks", ""]
    if objectives:
        for objective in objectives:
            lines.append(objective.to_task_markdown())
    else:
        lines.append("- No pending tasks in the current scope.")
    lines.append("")
    return "\n".join(lines)


def build_task_done_markdown() -> str:
    return "\n".join(
        [
            "# Completed Tasks",
            "",
            "- None completed in the current run scope yet.",
            "",
        ]
    )


def stage_markdown(stage: str, *, autoformalize_skipped: bool) -> str:
    marks = {
        "init": "[x]",
        "autoformalize": "[x]" if autoformalize_skipped or stage in {"prover", "polish", "COMPLETE"} else "[ ]",
        "prover": "[x]" if stage in {"polish", "COMPLETE"} else "[ ]",
        "polish": "[x]" if stage == "COMPLETE" else "[ ]",
    }
    return "\n".join(
        [
            "## Stages",
            f"- {marks['init']} init",
            f"- {marks['autoformalize']} autoformalize",
            f"- {marks['prover']} prover",
            f"- {marks['polish']} polish",
        ]
    )
