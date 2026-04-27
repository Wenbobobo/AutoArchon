from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence


PUBLIC_ARCHIVES = (
    "autoarchon-public-repo",
    "autoarchon-public-small-packs",
)
PRIVATE_ARCHIVES = (
    "autoarchon-private-config",
    "autoarchon-private-campaigns-metadata",
    "autoarchon-private-campaigns-workspaces",
)
CANONICAL_CAMPAIGN_IDS = (
    "20260414-rerun10-fate-m-full",
    "20260415-rerun12-fatem-42-45-94",
    "20260415-181235-fatex-natural-batch-35-42-45",
    "20260415-182323-motivic-flag-maps-q1-open-problem",
    "20260415-183503-motivic-flag-maps-q1-fixed-open-problem",
    "20260416-014938-fatex-natural-smoke",
)
TOP_LEVEL_WORKSPACE_FILES = (
    "problem-pack.json",
    "QUESTIONS.md",
    "README.md",
    "lakefile.lean",
    "lean-toolchain",
    "lake-manifest.json",
)
TOP_LEVEL_SOURCE_FILES = (
    "problem-pack.json",
    "QUESTIONS.md",
    "README.md",
    "lakefile.lean",
    "lean-toolchain",
)
BENCHMARK_TOP_LEVEL_DIR_PREFIXES = ("FATE", "FATEM", "FATEH", "FATEX")
REPO_LOCK_RELATIVE_PATHS = (
    Path("Archon-upstream"),
    Path("benchmarks") / "FATE-M-upstream",
    Path("benchmarks") / "FATE-H-upstream",
    Path("benchmarks") / "FATE-X-upstream",
    Path("benchmarks") / "FATE-upstream",
)


@dataclass(frozen=True)
class RepoSnapshot:
    name: str
    path: Path
    head: str
    remotes: list[dict[str, str]]


@dataclass(frozen=True)
class ArchiveRecord:
    name: str
    repo_id: str
    repo_type: str
    visibility: str
    parts: list[str]
    sha256: dict[str, str]
    sizes: dict[str, int]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_checked(
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        cwd=str(cwd) if cwd is not None else None,
        env=env,
        check=True,
        text=True,
        capture_output=capture_output,
    )


def git_output(repo: Path, args: Sequence[str]) -> str:
    completed = run_checked(["git", "-C", str(repo), *args], capture_output=True)
    return completed.stdout.strip()


def repo_snapshot(repo: Path) -> RepoSnapshot:
    remote_lines = git_output(repo, ["remote", "-v"]).splitlines()
    remotes: dict[tuple[str, str], dict[str, str]] = {}
    for line in remote_lines:
        parts = line.split()
        if len(parts) < 3:
            continue
        name, url, kind = parts[0], parts[1], parts[2].strip("()")
        remotes[(name, url)] = {"name": name, "url": url, "kind": kind}
    return RepoSnapshot(
        name=repo.name,
        path=repo.resolve(),
        head=git_output(repo, ["rev-parse", "--short", "HEAD"]),
        remotes=sorted(remotes.values(), key=lambda item: (item["name"], item["url"], item["kind"])),
    )


def collect_repo_snapshots(workspace_root: Path, archon_root: Path) -> list[RepoSnapshot]:
    repos = [archon_root, *[workspace_root / rel_path for rel_path in REPO_LOCK_RELATIVE_PATHS]]
    return [repo_snapshot(repo) for repo in repos if repo.exists()]


def ensure_clean_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_json(path: Path, payload: Any) -> None:
    write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def copy_file(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.is_symlink():
        target = os.readlink(src)
        if dest.exists() or dest.is_symlink():
            dest.unlink()
        os.symlink(target, dest)
        return
    shutil.copy2(src, dest)


def copy_tree(src: Path, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest, symlinks=True)


def tracked_files(repo: Path) -> list[Path]:
    output = git_output(repo, ["ls-files", "-z"])
    if not output:
        return []
    return [repo / entry for entry in output.split("\0") if entry]


def export_tracked_tree(repo: Path, dest: Path) -> None:
    for src_path in tracked_files(repo):
        rel_path = src_path.relative_to(repo)
        copy_file(src_path, dest / rel_path)


def create_git_bundle(repo: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    run_checked(["git", "-C", str(repo), "bundle", "create", str(dest), "--all"])


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pack_directory(source_dir: Path, dest_prefix: Path, *, passphrase: str) -> Path:
    tar_zst_path = dest_prefix.with_name(f"{dest_prefix.name}.tar.zst")
    encrypted_path = dest_prefix.with_name(f"{dest_prefix.name}.tar.zst.gpg")
    if tar_zst_path.exists():
        tar_zst_path.unlink()
    if encrypted_path.exists():
        encrypted_path.unlink()
    run_checked(
        [
            "tar",
            "--use-compress-program=zstd -T0 -10",
            "-cf",
            str(tar_zst_path),
            "-C",
            str(source_dir.parent),
            source_dir.name,
        ]
    )
    subprocess.run(
        [
            "gpg",
            "--batch",
            "--yes",
            "--pinentry-mode",
            "loopback",
            "--passphrase-fd",
            "0",
            "--symmetric",
            "--cipher-algo",
            "AES256",
            "--output",
            str(encrypted_path),
            str(tar_zst_path),
        ],
        input=f"{passphrase}\n",
        text=True,
        check=True,
    )
    tar_zst_path.unlink()
    return encrypted_path


def split_archive(path: Path, *, chunk_bytes: int) -> list[Path]:
    if path.stat().st_size <= chunk_bytes:
        return [path]
    prefix = Path(f"{path}.part")
    for old_part in path.parent.glob(f"{path.name}.part*"):
        old_part.unlink()
    run_checked(
        [
            "split",
            "-b",
            str(chunk_bytes),
            "-d",
            "-a",
            "4",
            str(path),
            str(prefix),
        ]
    )
    path.unlink()
    return sorted(path.parent.glob(f"{path.name}.part*"))


def collect_validation_rel_paths(validation_root: Path) -> list[str]:
    rel_paths: list[str] = []
    if not validation_root.exists():
        return rel_paths
    for path in sorted(validation_root.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        rel_path = payload.get("relPath") if isinstance(payload, dict) else None
        if isinstance(rel_path, str) and rel_path:
            rel_paths.append(rel_path)
    return sorted(dict.fromkeys(rel_paths))


def is_benchmark_top_level_dir(name: str) -> bool:
    return any(name.upper().startswith(prefix) for prefix in BENCHMARK_TOP_LEVEL_DIR_PREFIXES)


def find_small_custom_top_level_dirs(root: Path, *, size_limit_bytes: int) -> list[Path]:
    candidates: list[Path] = []
    if not root.exists():
        return candidates
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if child.name in {".archon", ".lake", "lake-packages"}:
            continue
        if is_benchmark_top_level_dir(child.name):
            continue
        size = subtree_size(child)
        if size <= size_limit_bytes:
            candidates.append(child)
    return candidates


def subtree_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for child in path.rglob("*"):
        if child.is_file():
            total += child.stat().st_size
    return total


def copy_if_exists(src: Path, dest: Path) -> None:
    if src.exists():
        copy_file(src, dest)


def iter_canonical_campaigns(workspace_root: Path) -> Iterator[Path]:
    campaigns_root = workspace_root / "runs" / "campaigns"
    for campaign_id in CANONICAL_CAMPAIGN_IDS:
        candidate = campaigns_root / campaign_id
        if candidate.exists():
            yield candidate


def copy_campaign_metadata(campaign_root: Path, dest_root: Path) -> None:
    dest_campaign = dest_root / campaign_root.name
    for rel_name in ("CAMPAIGN_MANIFEST.json", "campaign-status.json", "events.jsonl"):
        copy_if_exists(campaign_root / rel_name, dest_campaign / rel_name)
    for rel_name in ("control", "reports/final", "reports/postmortem"):
        src = campaign_root / rel_name
        if src.exists():
            copy_tree(src, dest_campaign / rel_name)


def copy_run_workspace_snapshot(run_root: Path, dest_root: Path) -> None:
    dest_run = dest_root / run_root.name
    copy_if_exists(run_root / "RUN_MANIFEST.json", dest_run / "RUN_MANIFEST.json")
    if (run_root / "control").exists():
        copy_tree(run_root / "control", dest_run / "control")
    if (run_root / "artifacts").exists():
        copy_tree(run_root / "artifacts", dest_run / "artifacts")

    workspace = run_root / "workspace"
    source = run_root / "source"
    if (workspace / ".archon").exists():
        copy_tree(workspace / ".archon", dest_run / "workspace" / ".archon")

    for file_name in TOP_LEVEL_WORKSPACE_FILES:
        copy_if_exists(workspace / file_name, dest_run / "workspace" / file_name)
    for file_name in TOP_LEVEL_SOURCE_FILES:
        copy_if_exists(source / file_name, dest_run / "source" / file_name)

    for rel_path in collect_validation_rel_paths(workspace / ".archon" / "validation"):
        copy_if_exists(workspace / rel_path, dest_run / "workspace" / rel_path)
        copy_if_exists(source / rel_path, dest_run / "source" / rel_path)

    for custom_dir in find_small_custom_top_level_dirs(workspace, size_limit_bytes=64 * 1024 * 1024):
        copy_tree(custom_dir, dest_run / "workspace" / custom_dir.name)
    for custom_dir in find_small_custom_top_level_dirs(source, size_limit_bytes=64 * 1024 * 1024):
        copy_tree(custom_dir, dest_run / "source" / custom_dir.name)


def build_workspace_map(workspace_root: Path, archon_root: Path) -> dict[str, Any]:
    return {
        "generatedAt": utc_now(),
        "workspaceRoot": str(workspace_root),
        "archonRoot": str(archon_root),
        "layout": {
            "workspaceKind": "meta-workspace",
            "description": (
                "The local /math directory is a multi-repo working area containing the main "
                "AutoArchon fork, upstream clones, benchmark clones, and accumulated run artifacts."
            ),
            "roots": {
                "archon": str(archon_root),
                "archonUpstream": str(workspace_root / "Archon-upstream"),
                "benchmarks": str(workspace_root / "benchmarks"),
                "campaigns": str(workspace_root / "runs" / "campaigns"),
                "migrationDoc": str(workspace_root / "docs" / "archon-codex-execution-plan.md"),
            },
        },
    }


def build_restore_public(repo_lock: list[RepoSnapshot], toolchain: dict[str, str]) -> str:
    lines = [
        "# Restore Public",
        "",
        "This archive set restores the public AutoArchon code and small benchmark/problem packs.",
        "",
        "## Toolchain Snapshot",
        "",
    ]
    for key, value in sorted(toolchain.items()):
        lines.append(f"- {key}: `{value}`")
    lines.extend(
        [
            "",
            "## Restore Steps",
            "",
            "1. Reassemble split parts if needed:",
            "   `cat archives/<name>.part* > <name>.tar.zst.gpg`",
            "2. Decrypt:",
            "   `gpg --batch --yes --pinentry-mode loopback --passphrase 0000 -o archive.tar.zst -d <name>.tar.zst.gpg`",
            "3. Extract:",
            "   `tar --use-compress-program=unzstd -xf archive.tar.zst`",
            "4. Restore the AutoArchon Git history from the `.bundle` if GitHub is unavailable:",
            "   `git clone AutoArchon.bundle AutoArchon-restored` or `git fetch AutoArchon.bundle`",
            "5. Re-clone large benchmark repos using `repo-lock.json` and check out the recorded commits.",
            "",
            "## Recorded Repositories",
            "",
        ]
    )
    for snapshot in repo_lock:
        lines.append(f"- {snapshot.name}: `{snapshot.head}`")
        for remote in snapshot.remotes:
            lines.append(f"  remote `{remote['name']}` ({remote['kind']}): `{remote['url']}`")
    lines.append("")
    return "\n".join(lines)


def build_restore_private() -> str:
    return "\n".join(
        [
            "# Restore Private",
            "",
            "This archive set restores local sensitive config plus curated campaign metadata and run workspaces.",
            "",
            "## Restore Steps",
            "",
            "1. Reassemble split parts if needed:",
            "   `cat archives/<name>.part* > <name>.tar.zst.gpg`",
            "2. Decrypt with password `0000`:",
            "   `gpg --batch --yes --pinentry-mode loopback --passphrase 0000 -o archive.tar.zst -d <name>.tar.zst.gpg`",
            "3. Extract:",
            "   `tar --use-compress-program=unzstd -xf archive.tar.zst`",
            "4. Restore `examples/helper.env` only on a trusted host.",
            "5. Rehydrate benchmark clones separately using `repo-lock.json`; private run snapshots intentionally exclude `.lake` caches.",
            "",
        ]
    )


def build_dataset_card(title: str, description: str, *, archive_names: Iterable[str]) -> str:
    lines = [
        "---",
        "license: other",
        "task_categories:",
        "- other",
        "---",
        "",
        f"# {title}",
        "",
        description,
        "",
        "## Contents",
        "",
    ]
    for archive_name in archive_names:
        lines.append(f"- `archives/{archive_name}`")
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- Archives are encrypted with GPG symmetric AES256.",
            "- Large archives may be split into `.part0000`, `.part0001`, ... files.",
            "- See `RESTORE_PUBLIC.md` or `RESTORE_PRIVATE.md` plus `manifests/archive-index.json`.",
            "",
        ]
    )
    return "\n".join(lines)
