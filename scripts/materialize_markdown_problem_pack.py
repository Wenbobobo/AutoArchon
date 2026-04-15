#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path
from typing import Any

from scripts.materialize_problem_pack import (
    _default_library_name,
    _mathlib_ref,
    _render_lakefile,
    _toolchain_from_version,
)


CELL_BREAK_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
MARKDOWN_EMPHASIS_RE = re.compile(r"[*`]+")
MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
HTML_TAG_RE = re.compile(r"<[^>]+>")
LEADING_INDEX_RE = re.compile(r"^\s*(\d+)\s*[.)、:]?\s*(.*)$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Materialize a Lean source root from a markdown question table. "
            "Generated `.lean` files contain informal comments only, so Archon starts in autoformalize stage."
        )
    )
    parser.add_argument("--questions-markdown", required=True, help="Markdown table file that lists open problems")
    parser.add_argument("--output-root", required=True, help="Destination Lean source root")
    parser.add_argument(
        "--problem-id",
        action="append",
        default=[],
        help="Problem id to include; may be repeated. Defaults to the first --max-problems rows.",
    )
    parser.add_argument(
        "--max-problems",
        type=int,
        default=3,
        help="How many rows to materialize when --problem-id is omitted",
    )
    parser.add_argument("--package-name", help="Optional Lake package name override")
    parser.add_argument("--library-name", help="Optional Lean library directory/module prefix override")
    parser.add_argument("--source-name", help="Optional human-readable source name override")
    parser.add_argument(
        "--toolchain",
        help="Optional Lean toolchain override, for example leanprover/lean4:v4.28.0",
    )
    parser.add_argument(
        "--mathlib-ref",
        help="Optional mathlib git ref override; defaults to v4.28.0",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite an existing output root")
    return parser.parse_args()


def _split_table_row(line: str) -> list[str]:
    stripped = line.strip()
    if not (stripped.startswith("|") and stripped.endswith("|")):
        return []
    content = stripped[1:-1]
    cells: list[str] = []
    current: list[str] = []
    in_code = False
    in_math = False
    i = 0
    while i < len(content):
        char = content[i]
        if char == "\\" and i + 1 < len(content):
            current.append(char)
            current.append(content[i + 1])
            i += 2
            continue
        if char == "`" and not in_math:
            in_code = not in_code
            current.append(char)
            i += 1
            continue
        if char == "$" and not in_code:
            in_math = not in_math
            current.append(char)
            i += 1
            continue
        if char == "|" and not in_code and not in_math:
            cells.append("".join(current).strip())
            current = []
            i += 1
            continue
        current.append(char)
        i += 1
    cells.append("".join(current).strip())
    return cells


def _is_delimiter_row(cells: list[str]) -> bool:
    if not cells:
        return False
    for cell in cells:
        stripped = cell.strip()
        if not stripped:
            continue
        compact = stripped.replace(":", "").replace("-", "")
        if compact:
            return False
    return True


def _normalize_cell(cell: str) -> str:
    text = CELL_BREAK_RE.sub("\n", cell)
    text = MARKDOWN_LINK_RE.sub(r"\1", text)
    text = MARKDOWN_EMPHASIS_RE.sub("", text)
    text = HTML_TAG_RE.sub("", text)
    text = re.sub(r"\s+\n", "\n", text)
    text = re.sub(r"\n\s+", "\n", text)
    return text.strip()


def _extract_problem_id(title_cell: str, *, fallback_index: int) -> tuple[str, str]:
    normalized = _normalize_cell(title_cell)
    first_line = normalized.splitlines()[0] if normalized else ""
    match = LEADING_INDEX_RE.match(first_line)
    if match:
        problem_id = match.group(1)
        title = match.group(2).strip() or normalized
        return problem_id, title
    return str(fallback_index), normalized or f"Question {fallback_index}"


def _modules_from_cell(cell: str) -> list[str]:
    normalized = _normalize_cell(cell)
    modules = [line.strip() for line in normalized.splitlines() if line.strip()]
    return modules


def _load_records(path: Path, *, source_name: str | None) -> list[dict[str, Any]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    table_rows = [_split_table_row(line) for line in lines if line.strip().startswith("|")]
    table_rows = [row for row in table_rows if row]
    if len(table_rows) < 3:
        raise ValueError(f"markdown question table was not found in {path}")

    header = table_rows[0]
    body_rows = table_rows[2:] if _is_delimiter_row(table_rows[1]) else table_rows[1:]
    if len(header) < 2:
        raise ValueError("markdown question table must contain at least two columns")

    resolved_source = source_name or path.parent.name or path.stem
    records: list[dict[str, Any]] = []
    for index, row in enumerate(body_rows, start=1):
        padded = row + [""] * (4 - len(row))
        problem_id, title = _extract_problem_id(padded[0], fallback_index=index)
        record = {
            "id": problem_id,
            "title": title,
            "informal_statement": _normalize_cell(padded[1]),
            "mathlib_modules": _modules_from_cell(padded[2]),
            "notes": _normalize_cell(padded[3]),
            "source": resolved_source,
            "originPath": str(path.resolve()),
        }
        records.append(record)

    if not records:
        raise ValueError(f"markdown question table did not contain any problem rows: {path}")
    return records


def _natural_sort_key(problem_id: str) -> tuple[int, str]:
    if problem_id.isdigit():
        return (0, f"{int(problem_id):09d}")
    return (1, problem_id)


def _select_records(records: list[dict[str, Any]], *, problem_ids: list[str], max_problems: int) -> list[dict[str, Any]]:
    indexed = {str(record["id"]).strip(): record for record in records}
    if problem_ids:
        selected: list[dict[str, Any]] = []
        missing: list[str] = []
        for raw in problem_ids:
            problem_id = str(raw).strip()
            record = indexed.get(problem_id)
            if record is None:
                missing.append(problem_id)
            else:
                selected.append(record)
        if missing:
            raise KeyError(f"problem ids not found in markdown table: {', '.join(missing)}")
        return sorted(selected, key=lambda record: _natural_sort_key(str(record["id"])))
    if max_problems <= 0:
        raise ValueError("--max-problems must be positive when no --problem-id is provided")
    return sorted(records, key=lambda record: _natural_sort_key(str(record["id"])))[:max_problems]


def _render_readme(*, package_name: str, library_name: str, source_path: Path, selected: list[dict[str, Any]]) -> str:
    lines = [
        f"# {package_name}",
        "",
        "Generated by `autoarchon-materialize-markdown-problem-pack`.",
        "",
        f"- Source markdown: `{source_path}`",
        f"- Lean library: `{library_name}`",
        f"- Problem count: `{len(selected)}`",
        "",
        "## Included Problems",
        "",
    ]
    for record in selected:
        lines.append(f"- `{record['id']}`: {record['title']}")
    lines.append("")
    return "\n".join(lines)


def _render_questions_markdown(*, library_name: str, selected: list[dict[str, Any]]) -> str:
    lines = [
        "# Questions",
        "",
        "| ID | Lean file | Title | Informal statement | Suggested Mathlib modules |",
        "| :--- | :--- | :--- | :--- | :--- |",
    ]
    for record in selected:
        modules = ", ".join(record["mathlib_modules"]) if record["mathlib_modules"] else "-"
        title = str(record["title"]).replace("|", "\\|")
        informal = str(record["informal_statement"]).replace("\n", " ").replace("|", "\\|") or "-"
        lines.append(
            f"| `{record['id']}` | `{library_name}/{record['id']}.lean` | {title} | {informal} | {modules.replace('|', '\\|')} |"
        )
    lines.append("")
    return "\n".join(lines)


def _render_stub_lean(record: dict[str, Any]) -> str:
    lines = [
        "import Mathlib",
        "",
        "/-!",
        "Open-problem formalization scaffold generated by `autoarchon-materialize-markdown-problem-pack`.",
        "",
        f"Question id: {record['id']}",
        f"Title: {record['title']}",
        f"Source: {record['source']}",
        "",
        "Informal objective:",
        record["informal_statement"] or "(missing informal statement)",
        "",
    ]
    modules = record.get("mathlib_modules") or []
    if modules:
        lines.extend(["Suggested Mathlib modules:"] + [f"- {module}" for module in modules] + [""])
    notes = str(record.get("notes", "")).strip()
    if notes:
        lines.extend(["Notes:", notes, ""])
    lines.extend(
        [
            "This file intentionally contains no Lean declaration yet.",
            "AutoArchon should start in `autoformalize` stage and formalize a declaration inside this file before proof search.",
            "-/",
            "",
        ]
    )
    return "\n".join(lines)


def materialize_markdown_problem_pack(
    questions_markdown: Path,
    output_root: Path,
    *,
    problem_ids: list[str],
    max_problems: int,
    package_name: str | None,
    library_name: str | None,
    source_name: str | None,
    toolchain: str | None,
    mathlib_ref: str | None,
    force: bool,
) -> dict[str, Any]:
    records = _load_records(questions_markdown, source_name=source_name)
    selected = _select_records(records, problem_ids=problem_ids, max_problems=max_problems)
    resolved_source_name = str(selected[0]["source"]) if len({str(record["source"]) for record in selected}) == 1 else (source_name or "OpenProblem")
    package_name = package_name or resolved_source_name
    library_name = library_name or _default_library_name(resolved_source_name)
    toolchain = toolchain or _toolchain_from_version(None)
    mathlib_ref = mathlib_ref or _mathlib_ref(None)

    output_root = output_root.resolve()
    if output_root.exists():
        if not force:
            raise FileExistsError(f"output root already exists: {output_root}")
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    library_root = output_root / library_name
    library_root.mkdir(parents=True, exist_ok=True)

    for record in selected:
        (library_root / f"{record['id']}.lean").write_text(_render_stub_lean(record), encoding="utf-8")

    (output_root / "lakefile.lean").write_text(
        _render_lakefile(package_name=package_name, library_name=library_name, mathlib_ref=mathlib_ref),
        encoding="utf-8",
    )
    (output_root / "lean-toolchain").write_text(toolchain.rstrip() + "\n", encoding="utf-8")
    (output_root / "README.md").write_text(
        _render_readme(package_name=package_name, library_name=library_name, source_path=questions_markdown.resolve(), selected=selected),
        encoding="utf-8",
    )
    (output_root / "QUESTIONS.md").write_text(
        _render_questions_markdown(library_name=library_name, selected=selected),
        encoding="utf-8",
    )
    (output_root / "problem-pack.json").write_text(
        json.dumps(selected, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    return {
        "questionsMarkdown": str(questions_markdown.resolve()),
        "outputRoot": str(output_root),
        "packageName": package_name,
        "libraryName": library_name,
        "toolchain": toolchain,
        "mathlibRef": mathlib_ref,
        "problemCount": len(selected),
        "problemIds": [str(record["id"]) for record in selected],
        "questionsPath": str((output_root / "QUESTIONS.md").resolve()),
        "manifestPath": str((output_root / "problem-pack.json").resolve()),
    }


def main() -> int:
    args = parse_args()
    payload = materialize_markdown_problem_pack(
        Path(args.questions_markdown),
        Path(args.output_root),
        problem_ids=args.problem_id,
        max_problems=args.max_problems,
        package_name=args.package_name,
        library_name=args.library_name,
        source_name=args.source_name,
        toolchain=args.toolchain,
        mathlib_ref=args.mathlib_ref,
        force=args.force,
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
