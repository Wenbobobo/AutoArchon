#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Materialize a Lean source root from a JSON problem pack that carries "
            "informal_statement and formal_statement fields."
        )
    )
    parser.add_argument("--input-json", required=True, help="Problem-pack JSON file")
    parser.add_argument("--output-root", required=True, help="Destination Lean source root")
    parser.add_argument(
        "--problem-id",
        action="append",
        default=[],
        help="Problem id to include; may be repeated. Defaults to the first --max-problems records.",
    )
    parser.add_argument(
        "--max-problems",
        type=int,
        default=3,
        help="How many records to materialize when --problem-id is omitted",
    )
    parser.add_argument("--package-name", help="Optional Lake package name override")
    parser.add_argument("--library-name", help="Optional Lean library directory/module prefix override")
    parser.add_argument(
        "--toolchain",
        help="Optional Lean toolchain override, for example leanprover/lean4:v4.28.0",
    )
    parser.add_argument(
        "--mathlib-ref",
        help="Optional mathlib git ref override; defaults to the shared version tag from the selected records",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite an existing output root")
    return parser.parse_args()


def _load_records(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        records = [item for item in payload if isinstance(item, dict)]
    elif isinstance(payload, dict):
        nested = payload.get("problems") or payload.get("items") or payload.get("theorems") or []
        records = [item for item in nested if isinstance(item, dict)] if isinstance(nested, list) else []
    else:
        records = []
    if not records:
        raise ValueError(f"problem pack did not contain any object records: {path}")
    return records


def _problem_id(record: dict[str, Any]) -> str:
    value = record.get("id")
    if value is None:
        raise ValueError("each problem record must contain an id field")
    rendered = str(value).strip()
    if not rendered:
        raise ValueError("problem id must not be empty")
    return rendered


def _natural_sort_key(problem_id: str) -> tuple[int, str]:
    if problem_id.isdigit():
        return (0, f"{int(problem_id):09d}")
    return (1, problem_id)


def _select_records(records: list[dict[str, Any]], *, problem_ids: list[str], max_problems: int) -> list[dict[str, Any]]:
    indexed = {_problem_id(record): record for record in records}
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
            raise KeyError(f"problem ids not found in pack: {', '.join(missing)}")
        return sorted(selected, key=lambda record: _natural_sort_key(_problem_id(record)))
    if max_problems <= 0:
        raise ValueError("--max-problems must be positive when no --problem-id is provided")
    return sorted(records, key=lambda record: _natural_sort_key(_problem_id(record)))[:max_problems]


def _shared_source(selected: list[dict[str, Any]]) -> str:
    values = {str(record.get("source", "")).strip() for record in selected if str(record.get("source", "")).strip()}
    if len(values) == 1:
        return next(iter(values))
    return "ProblemPack"


def _default_library_name(source: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9]+", "", source)
    return sanitized or "ProblemPack"


def _shared_version(selected: list[dict[str, Any]]) -> str | None:
    values = {str(record.get("version", "")).strip() for record in selected if str(record.get("version", "")).strip()}
    if len(values) == 1:
        return next(iter(values))
    return None


def _toolchain_from_version(version: str | None) -> str:
    if version:
        return version if version.startswith("leanprover/lean4:") else f"leanprover/lean4:{version}"
    return "leanprover/lean4:v4.28.0"


def _mathlib_ref(version: str | None) -> str:
    return version or "v4.28.0"


def _render_lakefile(*, package_name: str, library_name: str, mathlib_ref: str) -> str:
    return "\n".join(
        [
            "import Lake",
            "open Lake DSL",
            "",
            f"package «{package_name}» where",
            "  leanOptions := #[",
            "    ⟨`pp.unicode.fun, true⟩,",
            "    ⟨`pp.proofs.withType, false⟩",
            "  ]",
            "",
            "require mathlib from git",
            f'  "https://github.com/leanprover-community/mathlib4.git" @ "{mathlib_ref}"',
            "",
            "@[default_target]",
            f"lean_lib «{library_name}» where",
            "  -- generated by autoarchon-materialize-problem-pack",
            "",
        ]
    )


def _render_readme(*, package_name: str, library_name: str, source_path: Path, selected: list[dict[str, Any]]) -> str:
    lines = [
        f"# {package_name}",
        "",
        "Generated by `autoarchon-materialize-problem-pack`.",
        "",
        f"- Source pack: `{source_path}`",
        f"- Lean library: `{library_name}`",
        f"- Problem count: `{len(selected)}`",
        "",
        "## Included Problems",
        "",
    ]
    for record in selected:
        problem_id = _problem_id(record)
        informal = str(record.get("informal_statement", "")).strip().replace("\n", " ")
        lines.append(f"- `{problem_id}`: {informal or '(missing informal statement)'}")
    lines.append("")
    return "\n".join(lines)


def _render_questions_markdown(*, library_name: str, selected: list[dict[str, Any]]) -> str:
    lines = [
        "# Questions",
        "",
        "| ID | Lean file | Informal statement | Tags |",
        "| :--- | :--- | :--- | :--- |",
    ]
    for record in selected:
        problem_id = _problem_id(record)
        lean_file = f"{library_name}/{problem_id}.lean"
        informal = str(record.get("informal_statement", "")).strip().replace("\n", " ")
        tags = record.get("tag")
        rendered_tags = ", ".join(str(item).strip() for item in tags if str(item).strip()) if isinstance(tags, list) else ""
        informal_cell = informal.replace("|", "\\|")
        tags_cell = rendered_tags.replace("|", "\\|") or "-"
        lines.append(f"| `{problem_id}` | `{lean_file}` | {informal_cell or '-'} | {tags_cell} |")
    lines.append("")
    return "\n".join(lines)


def materialize_problem_pack(
    input_json: Path,
    output_root: Path,
    *,
    problem_ids: list[str],
    max_problems: int,
    package_name: str | None,
    library_name: str | None,
    toolchain: str | None,
    mathlib_ref: str | None,
    force: bool,
) -> dict[str, Any]:
    records = _load_records(input_json)
    selected = _select_records(records, problem_ids=problem_ids, max_problems=max_problems)
    source_name = _shared_source(selected)
    package_name = package_name or source_name
    library_name = library_name or _default_library_name(source_name)
    version = _shared_version(selected)
    toolchain = toolchain or _toolchain_from_version(version)
    mathlib_ref = mathlib_ref or _mathlib_ref(version)

    output_root = output_root.resolve()
    if output_root.exists():
        if not force:
            raise FileExistsError(f"output root already exists: {output_root}")
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    library_root = output_root / library_name
    library_root.mkdir(parents=True, exist_ok=True)

    for record in selected:
        problem_id = _problem_id(record)
        formal_statement = record.get("formal_statement")
        if not isinstance(formal_statement, str) or not formal_statement.strip():
            raise ValueError(f"record {problem_id} is missing formal_statement")
        (library_root / f"{problem_id}.lean").write_text(formal_statement.rstrip() + "\n", encoding="utf-8")

    (output_root / "lakefile.lean").write_text(
        _render_lakefile(package_name=package_name, library_name=library_name, mathlib_ref=mathlib_ref),
        encoding="utf-8",
    )
    (output_root / "lean-toolchain").write_text(toolchain.rstrip() + "\n", encoding="utf-8")
    (output_root / "README.md").write_text(
        _render_readme(package_name=package_name, library_name=library_name, source_path=input_json.resolve(), selected=selected),
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
        "inputJson": str(input_json.resolve()),
        "outputRoot": str(output_root),
        "packageName": package_name,
        "libraryName": library_name,
        "toolchain": toolchain,
        "mathlibRef": mathlib_ref,
        "problemCount": len(selected),
        "problemIds": [_problem_id(record) for record in selected],
        "questionsPath": str((output_root / "QUESTIONS.md").resolve()),
        "manifestPath": str((output_root / "problem-pack.json").resolve()),
    }


def main() -> int:
    args = parse_args()
    payload = materialize_problem_pack(
        Path(args.input_json),
        Path(args.output_root),
        problem_ids=list(args.problem_id),
        max_problems=args.max_problems,
        package_name=args.package_name,
        library_name=args.library_name,
        toolchain=args.toolchain,
        mathlib_ref=args.mathlib_ref,
        force=args.force,
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
