from __future__ import annotations

import difflib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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


def _resolve_cache_project_root(path: Path | None) -> Path | None:
    if path is None:
        return None
    candidate = path.resolve()
    if candidate.name == ".lake":
        return candidate.parent
    return candidate


def _copy_tree(source_root: Path, destination_root: Path, *, include_lake: bool) -> None:
    shutil.copytree(
        source_root,
        destination_root,
        ignore=_copy_ignore(include_lake=include_lake),
    )


def _same_optional_text(left: Path, right: Path) -> bool:
    left_exists = left.exists()
    right_exists = right.exists()
    if left_exists != right_exists:
        return False
    if not left_exists:
        return True
    return left.read_text(encoding="utf-8") == right.read_text(encoding="utf-8")


def _can_reuse_project_build_outputs(source_root: Path, cache_project_root: Path | None) -> bool:
    if cache_project_root is None:
        return False
    for rel_path in ("lean-toolchain", "lakefile.lean", "lakefile.toml", "lake-manifest.json"):
        if not _same_optional_text(source_root / rel_path, cache_project_root / rel_path):
            return False
    return True


def _reuse_lake_cache(source_root: Path, lake_cache: Path, destination_root: Path) -> dict[str, object]:
    destination_root.mkdir(parents=True, exist_ok=True)
    shared_packages = lake_cache / "packages"
    packages_linked = False
    if shared_packages.exists():
        (destination_root / "packages").symlink_to(shared_packages, target_is_directory=True)
        packages_linked = True
    else:
        shutil.copytree(lake_cache, destination_root, dirs_exist_ok=True)

    cache_project_root = _resolve_cache_project_root(lake_cache)
    build_reused = False
    if _can_reuse_project_build_outputs(source_root, cache_project_root):
        for name in ("config", "build"):
            source_path = lake_cache / name
            destination_path = destination_root / name
            if not source_path.exists():
                continue
            if source_path.is_dir():
                shutil.copytree(source_path, destination_path, dirs_exist_ok=True)
            else:
                destination_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_path, destination_path)
            if name == "build":
                build_reused = True

    return {
        "packagesLinked": packages_linked,
        "projectBuildReused": build_reused,
        "buildSourcePath": str(lake_cache / "build") if build_reused else None,
    }


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
    lake_reuse_summary: dict[str, object] = {
        "packagesLinked": False,
        "projectBuildReused": False,
        "buildSourcePath": None,
    }
    if lake_cache is not None:
        lake_reuse_summary = _reuse_lake_cache(source_root, lake_cache, workspace_root / ".lake")

    manifest = {
        "schemaVersion": SCHEMA_VERSION,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "sourceOriginPath": str(source_root),
        "sourceSnapshotPath": "source",
        "workspacePath": "workspace",
        "artifactsPath": "artifacts",
        "lakeReuseSourcePath": str(lake_cache) if lake_cache is not None else None,
        "lakePackagesLinked": bool(lake_reuse_summary["packagesLinked"]),
        "lakeBuildReusePath": lake_reuse_summary["buildSourcePath"],
        "projectBuildReused": bool(lake_reuse_summary["projectBuildReused"]),
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


def _read_json_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _should_preserve_exported_validation(
    previous_payload: dict[str, Any] | None,
    current_payload: dict[str, Any] | None,
    *,
    proof_exists: bool,
) -> bool:
    if not proof_exists:
        return False
    if not isinstance(previous_payload, dict):
        return False
    if previous_payload.get("acceptanceStatus") != "accepted":
        return False
    if previous_payload.get("validationStatus") != "passed":
        return False
    if not isinstance(current_payload, dict):
        return True
    if current_payload.get("acceptanceStatus") == "accepted":
        return False
    if current_payload.get("acceptanceStatus") == "rejected":
        return False
    if current_payload.get("acceptedKind") == "blocker":
        return False
    if current_payload.get("validationStatus") in {"failed", "attention"}:
        return False
    checks = current_payload.get("checks")
    if not isinstance(checks, dict):
        return True
    if checks.get("headerDrift") not in {None, "none"}:
        return False
    if checks.get("proverError") is True:
        return False
    return True


def _classified_task_results(validation_root: Path) -> tuple[list[str], list[str]]:
    resolved_notes: set[str] = set()
    blocker_notes: set[str] = set()

    if not validation_root.exists():
        return [], []

    for path in sorted(validation_root.glob("*.json")):
        payload = _read_json_if_exists(path)
        if payload is None:
            continue
        checks = payload.get("checks")
        if not isinstance(checks, dict):
            continue
        task_result = checks.get("taskResult")
        if not isinstance(task_result, dict):
            continue
        task_result_path = task_result.get("path")
        if not isinstance(task_result_path, str) or not task_result_path:
            continue
        note_name = Path(task_result_path).name
        kind = task_result.get("kind")
        if kind == "resolved":
            resolved_notes.add(note_name)
        elif kind == "blocker":
            blocker_notes.add(note_name)

    return sorted(resolved_notes), sorted(blocker_notes)


def _append_campaign_export_event(run_root: Path, summary: dict[str, object]) -> None:
    campaign_root = run_root.parent.parent
    if run_root.parent.name != "runs" or not (campaign_root / "CAMPAIGN_MANIFEST.json").exists():
        return

    events_path = campaign_root / "events.jsonl"
    payload = {
        "schemaVersion": SCHEMA_VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": "artifact_exported",
        "campaignId": campaign_root.name,
        "runId": run_root.name,
        "changedFileCount": len(summary.get("changedFiles", [])),
        "taskResultCount": len(summary.get("taskResults", [])),
        "resolvedNoteCount": len(summary.get("resolvedNotes", [])),
        "blockerNoteCount": len(summary.get("blockerNotes", [])),
        "validationFileCount": len(summary.get("validationFiles", [])),
    }
    events_path.parent.mkdir(parents=True, exist_ok=True)
    with events_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


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
            destination = exported_validation_root / path.name
            current_payload = _read_json_if_exists(path)
            previous_payload = _read_json_if_exists(destination)
            rel_path = current_payload.get("relPath") if isinstance(current_payload, dict) else None
            proof_exists = isinstance(rel_path, str) and (proofs_root / rel_path).exists()
            if _should_preserve_exported_validation(previous_payload, current_payload, proof_exists=proof_exists):
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(json.dumps(previous_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            else:
                _copy_file(path, destination)
    resolved_notes, blocker_notes = _classified_task_results(validation_root)

    lesson_files: list[str] = []
    if lessons_root.exists():
        for path in sorted(lessons_root.glob("*")):
            if not path.is_file():
                continue
            lesson_files.append(path.name)
            _copy_file(path, exported_lessons_root / path.name)

    supervisor_files: list[str] = []
    if supervisor_root.exists():
        for name in ("HOT_NOTES.md", "LEDGER.md", "violations.jsonl", "progress-summary.md", "progress-summary.json"):
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
        "resolvedNotes": resolved_notes,
        "blockerNotes": blocker_notes,
        "validationFiles": validation_files,
        "lessonFiles": lesson_files,
        "supervisorFiles": supervisor_files,
        "manifestPresent": manifest_path.exists(),
    }
    (artifacts_root / "artifact-index.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _append_campaign_export_event(run_root, summary)
    return summary
