#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from archonlib.backup_bundle import (
    ArchiveRecord,
    CANONICAL_CAMPAIGN_IDS,
    PRIVATE_ARCHIVES,
    PUBLIC_ARCHIVES,
    build_dataset_card,
    build_restore_private,
    build_restore_public,
    build_workspace_map,
    collect_repo_snapshots,
    copy_campaign_metadata,
    copy_file,
    copy_run_workspace_snapshot,
    copy_tree,
    create_git_bundle,
    ensure_clean_dir,
    export_tracked_tree,
    iter_canonical_campaigns,
    pack_directory,
    sha256_path,
    split_archive,
    utc_now,
    write_json,
    write_text,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create encrypted migration bundles and upload them to Hugging Face dataset repos.")
    parser.add_argument("--workspace-root", required=True, help="Root of the local multi-repo math workspace")
    parser.add_argument("--archon-root", required=True, help="Path to the main AutoArchon repo")
    parser.add_argument("--public-repo-id", required=True, help="HF dataset repo for public archives")
    parser.add_argument("--private-repo-id", required=True, help="HF dataset repo for private archives")
    parser.add_argument("--output-root", required=True, help="Local temporary output root for staging and publish folders")
    parser.add_argument("--date-tag", help="Archive date tag, defaults to UTC timestamp")
    parser.add_argument("--hf-token-env", default="HF_TOKEN", help="Environment variable containing the HF token")
    parser.add_argument("--passphrase-env", default="BACKUP_PASSPHRASE", help="Environment variable containing the archive passphrase")
    parser.add_argument("--repo-type", default="dataset", choices=["dataset"], help="HF repo type")
    parser.add_argument("--skip-upload", action="store_true", help="Build archives locally but do not upload them")
    parser.add_argument("--num-workers", type=int, default=8, help="Upload worker count")
    parser.add_argument(
        "--chunk-size-mib",
        type=int,
        default=1900,
        help="Split encrypted archives larger than this size into parts",
    )
    return parser.parse_args()


def toolchain_inventory() -> dict[str, str]:
    def _cmd(args: list[str]) -> str:
        completed = subprocess.run(args, capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            return f"unavailable (exit {completed.returncode})"
        output = (completed.stdout or completed.stderr).strip().splitlines()
        return output[0] if output else "unknown"

    return {
        "python": platform.python_version(),
        "uv": _cmd(["uv", "--version"]),
        "lean": _cmd(["lean", "--version"]),
        "lake": _cmd(["lake", "--version"]),
        "codex": _cmd(["codex", "--version"]),
    }


def repo_lock(workspace_root: Path, archon_root: Path) -> list[dict[str, Any]]:
    snapshots = collect_repo_snapshots(workspace_root, archon_root)
    return [
        {
            "name": snapshot.name,
            "path": str(snapshot.path),
            "head": snapshot.head,
            "remotes": snapshot.remotes,
        }
        for snapshot in snapshots
    ]


def build_public_staging(
    workspace_root: Path,
    archon_root: Path,
    staging_root: Path,
    *,
    toolchain: dict[str, str],
    repo_lock_payload: list[dict[str, Any]],
    snapshots: list[Any],
) -> None:
    repo_stage = staging_root / PUBLIC_ARCHIVES[0]
    small_packs_stage = staging_root / PUBLIC_ARCHIVES[1]
    ensure_clean_dir(repo_stage)
    ensure_clean_dir(small_packs_stage)

    repo_export_root = repo_stage / "AutoArchon-export"
    bundle_root = repo_stage / "git-bundles"
    export_tracked_tree(archon_root, repo_export_root)
    create_git_bundle(archon_root, bundle_root / "AutoArchon.bundle")
    migration_doc = workspace_root / "docs" / "archon-codex-execution-plan.md"
    if migration_doc.exists():
        copy_file(migration_doc, repo_stage / "external-docs" / migration_doc.name)

    for pack_name in ("Natural-language", "Open-problem", "Open-problem-generated"):
        pack_root = workspace_root / "benchmarks" / pack_name
        if pack_root.exists():
            copy_tree(pack_root, small_packs_stage / "benchmarks" / pack_name)

    write_json(repo_stage / "workspace-map.json", build_workspace_map(workspace_root, archon_root))
    write_json(repo_stage / "repo-lock.json", repo_lock_payload)
    write_json(repo_stage / "toolchain-inventory.json", toolchain)
    write_text(repo_stage / "RESTORE_PUBLIC.md", build_restore_public(snapshots, toolchain))


def build_private_staging(workspace_root: Path, archon_root: Path, staging_root: Path) -> None:
    config_stage = staging_root / PRIVATE_ARCHIVES[0]
    metadata_stage = staging_root / PRIVATE_ARCHIVES[1]
    workspaces_stage = staging_root / PRIVATE_ARCHIVES[2]
    ensure_clean_dir(config_stage)
    ensure_clean_dir(metadata_stage)
    ensure_clean_dir(workspaces_stage)

    helper_env = archon_root / "examples" / "helper.env"
    if helper_env.exists():
        copy_file(helper_env, config_stage / "Archon" / "examples" / "helper.env")
    write_text(config_stage / "RESTORE_PRIVATE.md", build_restore_private())

    campaigns_root = workspace_root / "runs" / "campaigns"
    for campaign_root in iter_canonical_campaigns(workspace_root):
        copy_campaign_metadata(campaign_root, metadata_stage / "campaigns")
        runs_root = campaign_root / "runs"
        if not runs_root.exists():
            continue
        for run_root in sorted(path for path in runs_root.iterdir() if path.is_dir()):
            copy_run_workspace_snapshot(run_root, workspaces_stage / "campaigns" / campaign_root.name / "runs")

    write_json(
        metadata_stage / "canonical-campaigns.json",
        {
            "generatedAt": utc_now(),
            "campaignIds": list(CANONICAL_CAMPAIGN_IDS),
        },
    )


def create_publish_tree(
    staging_root: Path,
    publish_root: Path,
    *,
    repo_id: str,
    repo_type: str,
    visibility: str,
    passphrase: str,
    chunk_size_bytes: int,
) -> list[ArchiveRecord]:
    ensure_clean_dir(publish_root)
    archives_root = publish_root / "archives"
    manifests_root = publish_root / "manifests"
    archives_root.mkdir(parents=True, exist_ok=True)
    manifests_root.mkdir(parents=True, exist_ok=True)

    records: list[ArchiveRecord] = []
    for source_dir in sorted(path for path in staging_root.iterdir() if path.is_dir()):
        encrypted_path = pack_directory(source_dir, archives_root / source_dir.name, passphrase=passphrase)
        parts = split_archive(encrypted_path, chunk_bytes=chunk_size_bytes)
        sha256 = {path.name: sha256_path(path) for path in parts}
        sizes = {path.name: path.stat().st_size for path in parts}
        records.append(
            ArchiveRecord(
                name=source_dir.name,
                repo_id=repo_id,
                repo_type=repo_type,
                visibility=visibility,
                parts=[path.name for path in parts],
                sha256=sha256,
                sizes=sizes,
            )
        )

    archive_index = {
        "generatedAt": utc_now(),
        "repoId": repo_id,
        "repoType": repo_type,
        "visibility": visibility,
        "archives": [
            {
                "name": record.name,
                "parts": record.parts,
                "sha256": record.sha256,
                "sizes": record.sizes,
            }
            for record in records
        ],
    }
    write_json(manifests_root / "archive-index.json", archive_index)

    sha_lines: list[str] = []
    for record in records:
        for part in record.parts:
            sha_lines.append(f"{record.sha256[part]}  archives/{part}")
    write_text(publish_root / "SHA256SUMS", "\n".join(sha_lines) + "\n")
    return records


def upload_publish_tree(
    publish_root: Path,
    *,
    repo_id: str,
    repo_type: str,
    token: str | None,
    private: bool,
    num_workers: int,
) -> None:
    from huggingface_hub import HfApi

    api = HfApi(token=token)
    api.create_repo(repo_id=repo_id, repo_type=repo_type, private=private, exist_ok=True)
    kwargs = {
        "repo_id": repo_id,
        "repo_type": repo_type,
        "folder_path": str(publish_root),
        "num_workers": num_workers,
    }
    if hasattr(api, "upload_large_folder"):
        api.upload_large_folder(**kwargs)
    else:
        api.upload_folder(repo_id=repo_id, repo_type=repo_type, folder_path=str(publish_root), multi_commits=True)


def verify_remote(
    *,
    repo_id: str,
    repo_type: str,
    token: str | None,
    expected_files: list[str],
) -> None:
    from huggingface_hub import HfApi

    api = HfApi(token=token)
    remote_files = set(api.list_repo_files(repo_id=repo_id, repo_type=repo_type))
    missing = [path for path in expected_files if path not in remote_files]
    if missing:
        raise RuntimeError(f"missing remote files for {repo_id}: {missing}")


def add_dataset_docs(publish_root: Path, *, title: str, description: str, archive_names: list[str], restore_name: str) -> None:
    write_text(publish_root / "README.md", build_dataset_card(title, description, archive_names=archive_names))
    source_restore = publish_root.parent / "staging_combined" / restore_name
    if source_restore.exists():
        copy_file(source_restore, publish_root / restore_name)


def main() -> int:
    args = parse_args()
    workspace_root = Path(args.workspace_root).resolve()
    archon_root = Path(args.archon_root).resolve()
    output_root = Path(args.output_root).resolve()
    date_tag = args.date_tag or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    token = os.environ.get(args.hf_token_env, "").strip() or None
    passphrase = os.environ.get(args.passphrase_env, "").strip()
    if not passphrase:
        raise SystemExit(f"Missing backup passphrase in env var {args.passphrase_env}")

    chunk_size_bytes = args.chunk_size_mib * 1024 * 1024
    run_root = output_root / f"backup-{date_tag}"
    public_stage_root = run_root / "staging" / "public"
    private_stage_root = run_root / "staging" / "private"
    combined_stage_root = run_root / "staging_combined"
    public_publish_root = run_root / "publish" / "public"
    private_publish_root = run_root / "publish" / "private"

    ensure_clean_dir(run_root)
    ensure_clean_dir(public_stage_root)
    ensure_clean_dir(private_stage_root)
    ensure_clean_dir(combined_stage_root)

    toolchain = toolchain_inventory()
    snapshots = collect_repo_snapshots(workspace_root, archon_root)
    repo_lock_payload = repo_lock(workspace_root, archon_root)

    build_public_staging(
        workspace_root,
        archon_root,
        public_stage_root,
        toolchain=toolchain,
        repo_lock_payload=repo_lock_payload,
        snapshots=snapshots,
    )
    build_private_staging(workspace_root, archon_root, private_stage_root)

    write_json(combined_stage_root / "workspace-map.json", build_workspace_map(workspace_root, archon_root))
    write_json(combined_stage_root / "repo-lock.json", repo_lock_payload)
    write_json(combined_stage_root / "toolchain-inventory.json", toolchain)
    write_text(combined_stage_root / "RESTORE_PUBLIC.md", build_restore_public(snapshots, toolchain))
    write_text(combined_stage_root / "RESTORE_PRIVATE.md", build_restore_private())

    public_records = create_publish_tree(
        public_stage_root,
        public_publish_root,
        repo_id=args.public_repo_id,
        repo_type=args.repo_type,
        visibility="public",
        passphrase=passphrase,
        chunk_size_bytes=chunk_size_bytes,
    )
    private_records = create_publish_tree(
        private_stage_root,
        private_publish_root,
        repo_id=args.private_repo_id,
        repo_type=args.repo_type,
        visibility="private",
        passphrase=passphrase,
        chunk_size_bytes=chunk_size_bytes,
    )

    copy_file(combined_stage_root / "RESTORE_PUBLIC.md", public_publish_root / "RESTORE_PUBLIC.md")
    copy_file(combined_stage_root / "repo-lock.json", public_publish_root / "manifests" / "repo-lock.json")
    copy_file(combined_stage_root / "toolchain-inventory.json", public_publish_root / "manifests" / "toolchain-inventory.json")
    copy_file(combined_stage_root / "workspace-map.json", public_publish_root / "manifests" / "workspace-map.json")

    copy_file(combined_stage_root / "RESTORE_PRIVATE.md", private_publish_root / "RESTORE_PRIVATE.md")
    copy_file(combined_stage_root / "repo-lock.json", private_publish_root / "manifests" / "repo-lock.json")
    copy_file(combined_stage_root / "toolchain-inventory.json", private_publish_root / "manifests" / "toolchain-inventory.json")
    copy_file(combined_stage_root / "workspace-map.json", private_publish_root / "manifests" / "workspace-map.json")

    add_dataset_docs(
        public_publish_root,
        title="AutoArchon Public Migration Backup",
        description="Encrypted public migration archives for the AutoArchon workspace: git bundle, tracked source export, small public problem packs, and restore metadata.",
        archive_names=[part for record in public_records for part in record.parts],
        restore_name="RESTORE_PUBLIC.md",
    )
    add_dataset_docs(
        private_publish_root,
        title="AutoArchon Private Migration Backup",
        description="Encrypted private migration archives for the AutoArchon workspace: sensitive local config plus curated campaign metadata and workspace snapshots.",
        archive_names=[part for record in private_records for part in record.parts],
        restore_name="RESTORE_PRIVATE.md",
    )

    summary = {
        "generatedAt": utc_now(),
        "dateTag": date_tag,
        "workspaceRoot": str(workspace_root),
        "archonRoot": str(archon_root),
        "publicRepoId": args.public_repo_id,
        "privateRepoId": args.private_repo_id,
        "publicArchives": [
            {"name": record.name, "parts": record.parts, "sizes": record.sizes}
            for record in public_records
        ],
        "privateArchives": [
            {"name": record.name, "parts": record.parts, "sizes": record.sizes}
            for record in private_records
        ],
    }
    write_json(run_root / "backup-summary.json", summary)

    if not args.skip_upload:
        os.environ.setdefault("HF_HUB_ENABLE_HF_TRANSFER", "1")
        upload_publish_tree(
            public_publish_root,
            repo_id=args.public_repo_id,
            repo_type=args.repo_type,
            token=token,
            private=False,
            num_workers=args.num_workers,
        )
        upload_publish_tree(
            private_publish_root,
            repo_id=args.private_repo_id,
            repo_type=args.repo_type,
            token=token,
            private=True,
            num_workers=args.num_workers,
        )
        verify_remote(
            repo_id=args.public_repo_id,
            repo_type=args.repo_type,
            token=token,
            expected_files=[
                "README.md",
                "RESTORE_PUBLIC.md",
                "SHA256SUMS",
                "manifests/archive-index.json",
                "manifests/repo-lock.json",
                "manifests/toolchain-inventory.json",
                "manifests/workspace-map.json",
                *[f"archives/{part}" for record in public_records for part in record.parts],
            ],
        )
        verify_remote(
            repo_id=args.private_repo_id,
            repo_type=args.repo_type,
            token=token,
            expected_files=[
                "README.md",
                "RESTORE_PRIVATE.md",
                "SHA256SUMS",
                "manifests/archive-index.json",
                "manifests/repo-lock.json",
                "manifests/toolchain-inventory.json",
                "manifests/workspace-map.json",
                *[f"archives/{part}" for record in private_records for part in record.parts],
            ],
        )

    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
