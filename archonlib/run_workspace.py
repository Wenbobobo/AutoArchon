from __future__ import annotations

import difflib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


SCHEMA_VERSION = 1
IGNORED_DIRS = {".archon", ".git", "build", "lake-packages", "__pycache__"}
IGNORED_EXPORT_DIRS = IGNORED_DIRS | {".lake"}


def _copy_ignore(include_lake: bool):
    ignored = set(IGNORED_DIRS)
    if not include_lake:
        ignored.add(".lake")

    def _ignore(_root: str, names: list[str]) -> set[str]:
        return {name for name in names if name in ignored}

    return _ignore


def _resolve_cache_root(path: Path | None) -> Path | None:
    if path is None:
        return None
    candidate = path.resolve()
    if candidate.name == ".lake":
        return candidate
    lake_dir = candidate / ".lake"
    if lake_dir.exists():
        return lake_dir
    return None


def _copy_tree(source_root: Path, destination_root: Path, *, include_lake: bool) -> None:
    shutil.copytree(
        source_root,
        destination_root,
        ignore=_copy_ignore(include_lake=include_lake),
    )


def _reuse_lake_cache(lake_cache: Path, destination_root: Path) -> None:
    destination_root.mkdir(parents=True, exist_ok=True)
    shared_packages = lake_cache / "packages"
    if shared_packages.exists():
        (destination_root / "packages").symlink_to(shared_packages, target_is_directory=True)
        return
    shutil.copytree(lake_cache, destination_root, dirs_exist_ok=True)


def create_isolated_run(
    source_root: Path,
    run_root: Path,
    *,
    reuse_lake_from: Path | None = None,
    scope_hint: str | None = None,
) -> dict[str, object]:
    source_root = source_root.resolve()
    run_root = run_root.resolve()

    if not source_root.exists():
        raise FileNotFoundError(f"source project not found: {source_root}")
    if run_root.exists():
        existing = list(run_root.iterdir())
        if existing:
            raise FileExistsError(f"run root already exists and is not empty: {run_root}")
    else:
        run_root.mkdir(parents=True, exist_ok=True)

    source_snapshot = run_root / "source"
    workspace_root = run_root / "workspace"
    artifacts_root = run_root / "artifacts"

    _copy_tree(source_root, source_snapshot, include_lake=False)
    _copy_tree(source_root, workspace_root, include_lake=False)
    artifacts_root.mkdir(parents=True, exist_ok=True)

    lake_cache = _resolve_cache_root(reuse_lake_from)
    if lake_cache is not None:
        _reuse_lake_cache(lake_cache, workspace_root / ".lake")

    manifest = {
        "schemaVersion": SCHEMA_VERSION,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "sourceOriginPath": str(source_root),
        "sourceSnapshotPath": "source",
        "workspacePath": "workspace",
        "artifactsPath": "artifacts",
        "lakeReuseSourcePath": str(lake_cache) if lake_cache is not None else None,
        "scopeHint": scope_hint,
    }
    (run_root / "RUN_MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def _iter_relative_lean_files(root: Path) -> set[str]:
    files: set[str] = set()
    for path in root.rglob("*.lean"):
        if any(part in IGNORED_EXPORT_DIRS for part in path.parts):
            continue
        files.add(path.relative_to(root).as_posix())
    return files


def _read_text_if_exists(path: Path) -> str | None:
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def _copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def export_run_artifacts(run_root: Path) -> dict[str, object]:
    run_root = run_root.resolve()
    source_root = run_root / "source"
    workspace_root = run_root / "workspace"
    artifacts_root = run_root / "artifacts"
    manifest_path = run_root / "RUN_MANIFEST.json"
    task_results_root = workspace_root / ".archon" / "task_results"
    validation_root = workspace_root / ".archon" / "validation"
    lessons_root = workspace_root / ".archon" / "lessons"
    supervisor_root = workspace_root / ".archon" / "supervisor"

    if not source_root.exists() or not workspace_root.exists():
        raise FileNotFoundError("run root must contain both source/ and workspace/")

    artifacts_root.mkdir(parents=True, exist_ok=True)
    proofs_root = artifacts_root / "proofs"
    diffs_root = artifacts_root / "diffs"
    exported_task_results_root = artifacts_root / "task-results"
    exported_validation_root = artifacts_root / "validation"
    exported_lessons_root = artifacts_root / "lessons"
    exported_supervisor_root = artifacts_root / "supervisor"

    changed_files: list[str] = []
    for rel_path in sorted(_iter_relative_lean_files(source_root) | _iter_relative_lean_files(workspace_root)):
        source_text = _read_text_if_exists(source_root / rel_path)
        workspace_text = _read_text_if_exists(workspace_root / rel_path)
        if source_text == workspace_text or workspace_text is None:
            continue

        changed_files.append(rel_path)
        _copy_file(workspace_root / rel_path, proofs_root / rel_path)
        diff_lines = difflib.unified_diff(
            (source_text or "").splitlines(keepends=True),
            workspace_text.splitlines(keepends=True),
            fromfile=f"source/{rel_path}",
            tofile=f"workspace/{rel_path}",
        )
        diff_path = diffs_root / f"{rel_path}.diff"
        diff_path.parent.mkdir(parents=True, exist_ok=True)
        diff_path.write_text("".join(diff_lines), encoding="utf-8")

    task_results: list[str] = []
    if task_results_root.exists():
        for path in sorted(task_results_root.glob("*.md")):
            task_results.append(path.name)
            _copy_file(path, exported_task_results_root / path.name)

    validation_files: list[str] = []
    if validation_root.exists():
        for path in sorted(validation_root.glob("*.json")):
            validation_files.append(path.name)
            _copy_file(path, exported_validation_root / path.name)

    lesson_files: list[str] = []
    if lessons_root.exists():
        for path in sorted(lessons_root.glob("*")):
            if not path.is_file():
                continue
            lesson_files.append(path.name)
            _copy_file(path, exported_lessons_root / path.name)

    supervisor_files: list[str] = []
    if supervisor_root.exists():
        for name in ("HOT_NOTES.md", "LEDGER.md", "violations.jsonl"):
            source_path = supervisor_root / name
            if source_path.exists():
                supervisor_files.append(name)
                _copy_file(source_path, exported_supervisor_root / name)

    if manifest_path.exists():
        _copy_file(manifest_path, artifacts_root / "RUN_MANIFEST.json")

    summary = {
        "schemaVersion": SCHEMA_VERSION,
        "exportedAt": datetime.now(timezone.utc).isoformat(),
        "changedFiles": changed_files,
        "taskResults": task_results,
        "blockerNotes": task_results,
        "validationFiles": validation_files,
        "lessonFiles": lesson_files,
        "supervisorFiles": supervisor_files,
        "manifestPresent": manifest_path.exists(),
    }
    (artifacts_root / "artifact-index.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary
