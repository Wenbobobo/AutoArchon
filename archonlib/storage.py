from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


STALE_LEASE_SECONDS = 900


@dataclass(frozen=True)
class StorageCandidate:
    kind: str
    path: str
    size_bytes: int
    safe_to_prune: bool
    reason: str
    run_root: str | None = None
    campaign_root: str | None = None
    lease_path: str | None = None
    heartbeat_age_seconds: float | None = None


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _dir_size_bytes(path: Path) -> int:
    total = 0
    for child in path.rglob("*"):
        if not child.exists() or child.is_symlink():
            continue
        if child.is_file():
            total += child.stat().st_size
    return total


def _top_level_sizes(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for child in sorted(root.iterdir(), key=lambda item: item.name):
        if not child.exists():
            continue
        if child.is_symlink():
            continue
        if child.is_file():
            size_bytes = child.stat().st_size
        elif child.is_dir():
            size_bytes = _dir_size_bytes(child)
        else:
            continue
        rows.append(
            {
                "name": child.name,
                "path": str(child),
                "sizeBytes": size_bytes,
            }
        )
    rows.sort(key=lambda row: int(row["sizeBytes"]), reverse=True)
    return rows


def _parse_iso_datetime(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _heartbeat_age_seconds(payload: dict[str, Any]) -> float | None:
    heartbeat = _parse_iso_datetime(payload.get("lastHeartbeatAt"))
    if heartbeat is None:
        heartbeat = _parse_iso_datetime(payload.get("updatedAt"))
    if heartbeat is None:
        heartbeat = _parse_iso_datetime(payload.get("startedAt"))
    if heartbeat is None:
        return None
    return max(0.0, datetime.now(timezone.utc).timestamp() - heartbeat.timestamp())


def _coerce_pid(value: object) -> int | None:
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.isdigit():
        parsed = int(value)
        return parsed if parsed > 0 else None
    return None


def _pid_is_live(pid: int | None) -> bool:
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _pid_command(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return ""
    return raw.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()


def _pid_cwd(pid: int) -> str:
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return ""


def _pid_matches_workspace(pid: int, workspace: Path) -> bool:
    workspace_str = str(workspace.resolve())
    command = _pid_command(pid)
    if workspace_str and workspace_str in command:
        return True
    cwd = _pid_cwd(pid)
    return bool(cwd) and workspace_str in cwd


def _lease_activity(lease_path: Path, *, workspace: Path) -> tuple[bool, str, float | None]:
    payload = _read_json(lease_path)
    if not isinstance(payload, dict) or payload.get("active") is not True:
        return False, "inactive run lease", None

    heartbeat_age = _heartbeat_age_seconds(payload)
    live_matching_pid = False
    live_mismatched_pid = False
    for key in ("supervisorPid", "loopPid"):
        pid = _coerce_pid(payload.get(key))
        if not _pid_is_live(pid):
            continue
        if _pid_matches_workspace(pid, workspace):
            live_matching_pid = True
        else:
            live_mismatched_pid = True

    if live_matching_pid:
        return True, "active run lease", heartbeat_age
    if heartbeat_age is not None and heartbeat_age <= STALE_LEASE_SECONDS:
        if live_mismatched_pid:
            return True, "recent active lease with mismatched live pid", heartbeat_age
        return True, "recent active lease without live pid", heartbeat_age
    if live_mismatched_pid:
        return False, "stale active lease with mismatched live pid", heartbeat_age
    return False, "stale active lease without live pid", heartbeat_age


def _live_process_path_hints() -> tuple[str, ...]:
    hints: list[str] = []
    try:
        result = subprocess.run(
            ["ps", "-ewwo", "args="],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        result = None
    if result is not None and result.returncode == 0:
        hints.extend(line.strip() for line in result.stdout.splitlines() if line.strip())

    proc_root = Path("/proc")
    if proc_root.exists():
        for child in proc_root.iterdir():
            if not child.name.isdigit():
                continue
            try:
                cwd = os.readlink(child / "cwd")
            except OSError:
                continue
            if cwd:
                hints.append(cwd)
    return tuple(hints)


def _path_referenced_by_live_process(path: Path, *, hints: tuple[str, ...] | None = None) -> bool:
    target = str(path.resolve())
    if not target:
        return False
    haystack = hints if hints is not None else _live_process_path_hints()
    return any(target in hint for hint in haystack)


def _run_storage_candidates(root: Path, *, process_hints: tuple[str, ...] | None = None) -> list[StorageCandidate]:
    candidates: list[StorageCandidate] = []
    for manifest_path in root.rglob("RUN_MANIFEST.json"):
        run_root = manifest_path.parent
        workspace_lake = run_root / "workspace" / ".lake"
        if not workspace_lake.exists() or not workspace_lake.is_dir():
            continue
        lease_path = run_root / "workspace" / ".archon" / "supervisor" / "run-lease.json"
        active, reason, heartbeat_age = _lease_activity(lease_path, workspace=run_root / "workspace")
        if _path_referenced_by_live_process(run_root, hints=process_hints) or _path_referenced_by_live_process(run_root / "workspace", hints=process_hints):
            active = True
            reason = "live process references run root"
        campaign_root = None
        if run_root.parent.name == "runs" and (run_root.parent.parent / "CAMPAIGN_MANIFEST.json").exists():
            campaign_root = str(run_root.parent.parent)
        candidates.append(
            StorageCandidate(
                kind="workspace_lake",
                path=str(workspace_lake),
                size_bytes=_dir_size_bytes(workspace_lake),
                safe_to_prune=not active,
                reason=reason,
                run_root=str(run_root),
                campaign_root=campaign_root,
                lease_path=str(lease_path) if lease_path.exists() else None,
                heartbeat_age_seconds=heartbeat_age,
            )
        )
    return sorted(candidates, key=lambda item: item.size_bytes, reverse=True)


def _legacy_workspace_lake_candidates(root: Path) -> list[StorageCandidate]:
    candidates: list[StorageCandidate] = []
    process_hints = _live_process_path_hints()
    for child in root.iterdir():
        if not child.is_dir() or child.is_symlink():
            continue
        if (child / "RUN_MANIFEST.json").exists():
            continue
        archon_root = child / ".archon"
        lake_root = child / ".lake"
        if not archon_root.is_dir() or not lake_root.is_dir():
            continue
        referenced = _path_referenced_by_live_process(child, hints=process_hints)
        candidates.append(
            StorageCandidate(
                kind="legacy_workspace_lake",
                path=str(lake_root),
                size_bytes=_dir_size_bytes(lake_root),
                safe_to_prune=not referenced,
                reason="inactive legacy workspace cache" if not referenced else "live process references legacy workspace",
                run_root=str(child),
                campaign_root=None,
                lease_path=None,
                heartbeat_age_seconds=None,
            )
        )
    return sorted(candidates, key=lambda item: item.size_bytes, reverse=True)


def _broken_prewarm_candidates(root: Path) -> list[StorageCandidate]:
    candidates: list[StorageCandidate] = []
    for path in root.rglob(".lake.prewarm-*"):
        if not path.is_dir():
            continue
        candidates.append(
            StorageCandidate(
                kind="broken_prewarm",
                path=str(path),
                size_bytes=_dir_size_bytes(path),
                safe_to_prune=True,
                reason="failed or abandoned prewarm cache",
            )
        )
    return sorted(candidates, key=lambda item: item.size_bytes, reverse=True)


def build_storage_report(root: Path) -> dict[str, Any]:
    root = root.resolve()
    if not root.exists():
        raise FileNotFoundError(root)
    top_level = _top_level_sizes(root)
    process_hints = _live_process_path_hints()
    workspace_lake = _run_storage_candidates(root, process_hints=process_hints)
    legacy_workspace_lake = _legacy_workspace_lake_candidates(root)
    broken_prewarm = _broken_prewarm_candidates(root)
    candidates = workspace_lake + legacy_workspace_lake + broken_prewarm
    candidates.sort(key=lambda item: item.size_bytes, reverse=True)
    reclaimable = sum(item.size_bytes for item in candidates if item.safe_to_prune)
    reclaimable_count = sum(1 for item in candidates if item.safe_to_prune)
    stale_active_count = sum(1 for item in workspace_lake if item.reason.startswith("stale active lease"))
    protected_active_count = sum(1 for item in workspace_lake if item.reason.startswith("active run lease") or item.reason.startswith("recent active lease"))

    return {
        "root": str(root),
        "topLevel": top_level,
        "workspaceLakeCount": len(workspace_lake),
        "legacyWorkspaceLakeCount": len(legacy_workspace_lake),
        "brokenPrewarmCount": len(broken_prewarm),
        "candidateCount": len(candidates),
        "reclaimableBytes": reclaimable,
        "reclaimableCount": reclaimable_count,
        "staleActiveLeaseCount": stale_active_count,
        "protectedActiveCount": protected_active_count,
        "candidates": [asdict(item) for item in candidates],
    }


def _retention_kind(path: Path) -> str:
    if path.name == "runs":
        return "runs_root"
    if path.name == "benchmarks":
        return "benchmarks_root"
    if path.name in {"cache", "tmp"}:
        return "ephemeral_root"
    if (path / "CAMPAIGN_MANIFEST.json").exists() or ((path / "runs").is_dir() and (path / "control").is_dir()):
        return "campaign_root"
    if (path / ".archon").is_dir() and (path / ".lake").is_dir():
        return "legacy_workspace_root"
    if (path / "lean-toolchain").exists() and ((path / "lakefile.lean").exists() or (path / "lakefile.toml").exists()):
        return "lean_project"
    return "generic_directory"


def _retention_action(
    *,
    kind: str,
    active_reference: bool,
    has_lake: bool,
    has_final_summary: bool,
    has_postmortem_summary: bool,
) -> tuple[str, str]:
    if kind == "runs_root":
        return "inspect_children", "inspect child run roots rather than deleting the whole runs root"
    if kind == "benchmarks_root":
        return "inspect_children", "benchmark clones are shared inputs; inspect child clones individually"
    if active_reference:
        return "keep_live", "a live process still references this root"
    if kind == "legacy_workspace_root":
        if has_lake:
            return "prune_cache_only", "legacy single-workspace run; .lake is rebuildable and dominates disk usage"
        return "archive_or_delete_root", "legacy workspace root no longer has build cache; keep only if the .archon notes remain useful"
    if kind == "campaign_root":
        if has_lake:
            return "prune_cache_only", "campaign root still has rebuildable Lake caches"
        if has_final_summary or has_postmortem_summary:
            return "archive_or_delete_root", "campaign already exported reports; keep only if the remaining run traces are still needed"
        return "review_campaign_root", "campaign root has no live reference but still needs manual review before deletion"
    if kind == "lean_project":
        return "keep_shared_clone", "this looks like a reusable Lean project clone rather than a disposable run root"
    if kind == "ephemeral_root":
        return "review_ephemeral", "temporary cache root; review contents and active references before deleting"
    return "manual_review", "generic directory; no AutoArchon-specific retention rule matched"


def _benchmark_clone_root(root: Path) -> Path | None:
    if root.name == "benchmarks" and root.is_dir():
        return root
    candidate = root / "benchmarks"
    if candidate.is_dir():
        return candidate
    return None


def _benchmark_clone_action(*, active_reference: bool, has_lake: bool) -> tuple[str, str, str | None, str]:
    if active_reference:
        return (
            "keep_live",
            "shared benchmark clone is currently referenced by a live process",
            None,
            "wait for the active run to finish before changing this clone",
        )
    if has_lake:
        return (
            "keep_shared_clone",
            "shared benchmark clone; keep the source tree and treat `.lake` as the emergency reclaim knob only if you can afford a rewarm",
            "prune_clone_lake_only",
            "delete only `.lake/`, then rerun autoarchon-prewarm-project before the next campaign",
        )
    return (
        "keep_shared_clone",
        "shared benchmark clone without a local Lake cache",
        None,
        "rehydrate by re-cloning or rerunning the benchmark bootstrap if you intentionally remove it later",
    )


def _benchmark_clone_rows(root: Path, *, process_hints: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
    benchmark_root = _benchmark_clone_root(root)
    if benchmark_root is None:
        return []

    rows: list[dict[str, Any]] = []
    for child in sorted(benchmark_root.iterdir(), key=lambda item: item.name):
        if not child.is_dir() or child.is_symlink():
            continue
        if not (child / "lean-toolchain").exists():
            continue
        if not ((child / "lakefile.lean").exists() or (child / "lakefile.toml").exists()):
            continue
        active_reference = _path_referenced_by_live_process(child, hints=process_hints)
        lake_root = child / ".lake"
        git_root = child / ".git"
        has_lake = lake_root.is_dir()
        suggested_action, reason, emergency_action, rehydrate_hint = _benchmark_clone_action(
            active_reference=active_reference,
            has_lake=has_lake,
        )
        rows.append(
            {
                "name": child.name,
                "path": str(child),
                "sizeBytes": _dir_size_bytes(child),
                "activeReference": active_reference,
                "hasLake": has_lake,
                "lakeBytes": _dir_size_bytes(lake_root) if has_lake else 0,
                "gitBytes": _dir_size_bytes(git_root) if git_root.is_dir() else 0,
                "suggestedAction": suggested_action,
                "reason": reason,
                "emergencyAction": emergency_action,
                "rehydrateHint": rehydrate_hint,
            }
        )
    rows.sort(key=lambda row: int(row["sizeBytes"]), reverse=True)
    return rows


def build_retention_report(root: Path) -> dict[str, Any]:
    root = root.resolve()
    if not root.exists():
        raise FileNotFoundError(root)
    process_hints = _live_process_path_hints()
    rows: list[dict[str, Any]] = []
    for row in _top_level_sizes(root):
        path = Path(str(row["path"]))
        kind = _retention_kind(path)
        active_reference = _path_referenced_by_live_process(path, hints=process_hints)
        has_lake = (path / ".lake").is_dir()
        has_archon = (path / ".archon").is_dir()
        has_final_summary = (path / "reports" / "final" / "final-summary.json").exists()
        has_postmortem_summary = (path / "reports" / "postmortem" / "postmortem-summary.json").exists()
        suggested_action, reason = _retention_action(
            kind=kind,
            active_reference=active_reference,
            has_lake=has_lake,
            has_final_summary=has_final_summary,
            has_postmortem_summary=has_postmortem_summary,
        )
        rows.append(
            {
                **row,
                "kind": kind,
                "activeReference": active_reference,
                "hasLake": has_lake,
                "hasArchon": has_archon,
                "hasFinalSummary": has_final_summary,
                "hasPostmortemSummary": has_postmortem_summary,
                "suggestedAction": suggested_action,
                "reason": reason,
            }
        )

    action_counts: dict[str, int] = {}
    for row in rows:
        action = str(row["suggestedAction"])
        action_counts[action] = action_counts.get(action, 0) + 1

    benchmark_clone_rows = _benchmark_clone_rows(root, process_hints=process_hints)

    return {
        "root": str(root),
        "entryCount": len(rows),
        "actionCounts": action_counts,
        "rows": rows,
        "benchmarkCloneCount": len(benchmark_clone_rows),
        "benchmarkLakeBytes": sum(int(row.get("lakeBytes", 0) or 0) for row in benchmark_clone_rows),
        "benchmarkCloneRows": benchmark_clone_rows,
    }


def render_storage_report_markdown(report: dict[str, Any], *, limit: int = 20) -> str:
    lines = [
        "# Storage Report",
        "",
        f"- Root: `{report.get('root', 'unknown')}`",
        f"- Workspace `.lake` candidates: `{report.get('workspaceLakeCount', 0)}`",
        f"- Legacy workspace `.lake` candidates: `{report.get('legacyWorkspaceLakeCount', 0)}`",
        f"- Broken prewarm candidates: `{report.get('brokenPrewarmCount', 0)}`",
        f"- Stale active leases: `{report.get('staleActiveLeaseCount', 0)}`",
        f"- Protected active candidates: `{report.get('protectedActiveCount', 0)}`",
        f"- Reclaimable candidates: `{report.get('reclaimableCount', 0)}`",
        f"- Reclaimable bytes: `{report.get('reclaimableBytes', 0)}`",
        "",
        "## Top Level",
        "",
    ]
    top_level = report.get("topLevel", [])
    if isinstance(top_level, list) and top_level:
        for row in top_level[:10]:
            if not isinstance(row, dict):
                continue
            lines.append(f"- `{row.get('name')}`: `{row.get('sizeBytes', 0)}` bytes")
    else:
        lines.append("- none")
    lines.extend(["", "## Largest Candidates", ""])
    candidates = report.get("candidates", [])
    if isinstance(candidates, list) and candidates:
        for row in candidates[:limit]:
            if not isinstance(row, dict):
                continue
            heartbeat_age = row.get("heartbeat_age_seconds")
            heartbeat_suffix = ""
            if isinstance(heartbeat_age, (int, float)):
                heartbeat_suffix = f" heartbeat_age_seconds=`{round(float(heartbeat_age), 1)}`"
            lines.append(
                f"- `{row.get('kind')}` `{row.get('path')}` size=`{row.get('size_bytes', 0)}` safe=`{row.get('safe_to_prune')}` reason=`{row.get('reason')}`{heartbeat_suffix}"
            )
    else:
        lines.append("- none")
    lines.append("")
    return "\n".join(lines)


def render_retention_report_markdown(report: dict[str, Any], *, limit: int = 20) -> str:
    lines = [
        "# Retention Report",
        "",
        f"- Root: `{report.get('root', 'unknown')}`",
        f"- Entries: `{report.get('entryCount', 0)}`",
        f"- Action counts: `{json.dumps(report.get('actionCounts', {}), sort_keys=True)}`",
        f"- Benchmark clones: `{report.get('benchmarkCloneCount', 0)}`",
        f"- Benchmark `.lake` bytes: `{report.get('benchmarkLakeBytes', 0)}`",
        "",
        "## Largest Entries",
        "",
    ]
    rows = report.get("rows", [])
    if isinstance(rows, list) and rows:
        for row in rows[:limit]:
            if not isinstance(row, dict):
                continue
            lines.append(
                "- "
                f"`{row.get('name')}` kind=`{row.get('kind')}` size=`{row.get('sizeBytes', 0)}` "
                f"active=`{row.get('activeReference')}` action=`{row.get('suggestedAction')}` reason=`{row.get('reason')}`"
            )
    else:
        lines.append("- none")
    benchmark_rows = report.get("benchmarkCloneRows", [])
    lines.extend(["", "## Benchmark Clones", ""])
    if isinstance(benchmark_rows, list) and benchmark_rows:
        for row in benchmark_rows[:limit]:
            if not isinstance(row, dict):
                continue
            emergency = row.get("emergencyAction") or "none"
            lines.append(
                "- "
                f"`{row.get('name')}` size=`{row.get('sizeBytes', 0)}` lake=`{row.get('lakeBytes', 0)}` "
                f"active=`{row.get('activeReference')}` action=`{row.get('suggestedAction')}` emergency=`{emergency}` reason=`{row.get('reason')}`"
            )
    else:
        lines.append("- none")
    lines.append("")
    return "\n".join(lines)


def prune_storage_candidates(
    root: Path,
    *,
    prune_workspace_lake: bool,
    prune_broken_prewarm: bool,
    execute: bool,
) -> dict[str, Any]:
    report = build_storage_report(root)
    selected: list[dict[str, Any]] = []
    reclaimed = 0
    for candidate in report["candidates"]:
        if not isinstance(candidate, dict):
            continue
        kind = candidate.get("kind")
        if kind in {"workspace_lake", "legacy_workspace_lake"} and not prune_workspace_lake:
            continue
        if kind == "broken_prewarm" and not prune_broken_prewarm:
            continue
        if candidate.get("safe_to_prune") is not True:
            continue
        selected.append(candidate)
        if execute:
            path = Path(str(candidate["path"]))
            if path.exists():
                size_bytes = int(candidate.get("size_bytes", 0) or 0)
                shutil.rmtree(path)
                reclaimed += size_bytes
    return {
        "root": report["root"],
        "execute": execute,
        "pruneWorkspaceLake": prune_workspace_lake,
        "pruneBrokenPrewarm": prune_broken_prewarm,
        "selectedCount": len(selected),
        "reclaimedBytes": reclaimed if execute else sum(int(item.get("size_bytes", 0) or 0) for item in selected),
        "selected": selected,
    }
