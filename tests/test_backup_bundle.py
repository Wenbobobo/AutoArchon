from __future__ import annotations

import json
from pathlib import Path

from archonlib.backup_bundle import (
    collect_validation_rel_paths,
    copy_run_workspace_snapshot,
    find_small_custom_top_level_dirs,
    is_benchmark_top_level_dir,
)


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_collect_validation_rel_paths_deduplicates_and_skips_invalid_json(tmp_path: Path):
    validation_root = tmp_path / ".archon" / "validation"
    write(validation_root / "a.json", json.dumps({"relPath": "Foo/1.lean"}))
    write(validation_root / "b.json", json.dumps({"relPath": "Foo/1.lean"}))
    write(validation_root / "c.json", json.dumps({"relPath": "Foo/2.lean"}))
    write(validation_root / "broken.json", "{not-json")

    assert collect_validation_rel_paths(validation_root) == ["Foo/1.lean", "Foo/2.lean"]


def test_is_benchmark_top_level_dir_matches_expected_prefixes():
    assert is_benchmark_top_level_dir("FATE")
    assert is_benchmark_top_level_dir("FATEXBench")
    assert is_benchmark_top_level_dir("fatem-local")
    assert not is_benchmark_top_level_dir("MyProblemPack")


def test_find_small_custom_top_level_dirs_skips_benchmark_and_lake_roots(tmp_path: Path):
    small = tmp_path / "custom-pack"
    benchmark_like = tmp_path / "FATEXExamples"
    lake_root = tmp_path / ".lake"
    write(small / "A.lean", "theorem t : True := trivial\n")
    write(benchmark_like / "B.lean", "theorem u : True := trivial\n")
    write(lake_root / "junk", "x")

    candidates = find_small_custom_top_level_dirs(tmp_path, size_limit_bytes=1024)

    assert [path.name for path in candidates] == ["custom-pack"]


def test_copy_run_workspace_snapshot_keeps_curated_files_without_workspace_lake(tmp_path: Path):
    run_root = tmp_path / "runs" / "teacher-1"
    write(run_root / "RUN_MANIFEST.json", "{}\n")
    write(run_root / "control" / "events.jsonl", "{}\n")
    write(run_root / "artifacts" / "summary.json", "{}\n")
    write(run_root / "workspace" / ".archon" / "validation" / "one.json", json.dumps({"relPath": "MyPack/1.lean"}))
    write(run_root / "workspace" / "problem-pack.json", "[]\n")
    write(run_root / "workspace" / "MyPack" / "1.lean", "theorem foo : True := trivial\n")
    write(run_root / "workspace" / ".lake" / "build" / "artifact.bin", "x" * 64)
    write(run_root / "source" / "README.md", "source\n")
    write(run_root / "source" / "MyPack" / "1.lean", "-- source copy\n")

    dest_root = tmp_path / "snapshot-root"
    copy_run_workspace_snapshot(run_root, dest_root)

    snapshot_run = dest_root / "teacher-1"
    assert (snapshot_run / "RUN_MANIFEST.json").exists()
    assert (snapshot_run / "control" / "events.jsonl").exists()
    assert (snapshot_run / "artifacts" / "summary.json").exists()
    assert (snapshot_run / "workspace" / ".archon" / "validation" / "one.json").exists()
    assert (snapshot_run / "workspace" / "problem-pack.json").exists()
    assert (snapshot_run / "workspace" / "MyPack" / "1.lean").exists()
    assert (snapshot_run / "source" / "README.md").exists()
    assert not (snapshot_run / "workspace" / ".lake").exists()
