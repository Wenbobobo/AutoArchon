from __future__ import annotations

import html
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from archonlib.operator_surfaces import ensure_operator_surfaces
from archonlib.lesson_clusters import write_lesson_cluster_artifacts
from archonlib.run_workspace import export_run_artifacts, create_isolated_run
from archonlib.storage import prune_storage_candidates
from archonlib.supervisor import collect_changed_files, latest_iteration_meta, read_allowed_files


SCHEMA_VERSION = 1
DEFAULT_HEARTBEAT_SECONDS = 900
LAUNCH_GRACE_SECONDS = 120
DEFAULT_LAUNCH_RETRY_AFTER_SECONDS = 300
RATE_LIMIT_RETRY_AFTER_SECONDS = 900
DEFAULT_OWNER_LEASE_SECONDS = 900
MAX_SCOPED_PREWARM_FILES = 4
DEFAULT_TAIL_SCOPE_OBJECTIVE_THRESHOLD = 4
IGNORED_SHARD_DIRS = {".archon", ".git", ".lake", "build", "lake-packages", "__pycache__"}
TERMINAL_RUN_STATUSES = {"accepted", "blocked", "contaminated"}
_UNSET = object()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _coerce_pid(value: object) -> int | None:
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.isdigit():
        parsed = int(value)
        return parsed if parsed > 0 else None
    return None


def _pid_is_live(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _relative(path: Path, *, start: Path) -> str:
    return path.resolve().relative_to(start.resolve()).as_posix()


def _event(payload: dict[str, Any], *, kind: str) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "timestamp": _utc_now(),
        "event": kind,
        **payload,
    }


def _status_index(payload: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, Mapping):
        return {}
    runs = payload.get("runs")
    if not isinstance(runs, list):
        return {}
    indexed: dict[str, dict[str, Any]] = {}
    for run in runs:
        if isinstance(run, dict) and isinstance(run.get("runId"), str):
            indexed[str(run["runId"])] = run
    return indexed


def _iso_from_timestamp(timestamp: float | None) -> str | None:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


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


def _tail_text(path: Path, *, max_bytes: int = 131072) -> str:
    if not path.exists() or not path.is_file():
        return ""
    with path.open("rb") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        handle.seek(max(0, size - max_bytes))
        data = handle.read()
    return data.decode("utf-8", errors="replace")


def _scoped_prewarm_verify_files(allowed_files: list[str]) -> list[str]:
    if not allowed_files:
        return []
    if len(allowed_files) <= MAX_SCOPED_PREWARM_FILES:
        return list(allowed_files)

    last_index = len(allowed_files) - 1
    selected_indices = {
        int(offset * last_index / (MAX_SCOPED_PREWARM_FILES - 1))
        for offset in range(MAX_SCOPED_PREWARM_FILES)
    }
    return [allowed_files[index] for index in sorted(selected_indices)]


def ensure_campaign_control_root(
    campaign_root: Path,
    *,
    owner_mode: str | object = _UNSET,
    watchdog_enabled: bool | object = _UNSET,
    manager_enabled: bool | object = _UNSET,
    owner_entrypoint: str | None | object = _UNSET,
) -> Path:
    control_root = campaign_root / "control"
    control_root.mkdir(parents=True, exist_ok=True)
    owner_mode_path = control_root / "owner-mode.json"
    existing = _read_json(owner_mode_path) or {}
    created_at = existing.get("createdAt")
    if not isinstance(created_at, str) or not created_at:
        created_at = _utc_now()
    resolved_owner_mode = owner_mode if owner_mode is not _UNSET else existing.get("ownerMode", "orchestrator")
    if not isinstance(resolved_owner_mode, str) or not resolved_owner_mode:
        resolved_owner_mode = "orchestrator"
    resolved_watchdog_enabled = watchdog_enabled if watchdog_enabled is not _UNSET else existing.get("watchdogEnabled", False)
    if not isinstance(resolved_watchdog_enabled, bool):
        resolved_watchdog_enabled = False
    resolved_manager_enabled = manager_enabled if manager_enabled is not _UNSET else existing.get("managerEnabled", False)
    if not isinstance(resolved_manager_enabled, bool):
        resolved_manager_enabled = False
    if owner_entrypoint is _UNSET:
        resolved_owner_entrypoint = existing.get("ownerEntrypoint") if "ownerEntrypoint" in existing else None
    else:
        resolved_owner_entrypoint = owner_entrypoint
    if resolved_owner_entrypoint is not None and not isinstance(resolved_owner_entrypoint, str):
        resolved_owner_entrypoint = None
    payload = {
        "schemaVersion": SCHEMA_VERSION,
        "campaignId": campaign_root.name,
        "createdAt": created_at,
        "updatedAt": _utc_now(),
        "ownerMode": resolved_owner_mode,
        "watchdogEnabled": resolved_watchdog_enabled,
        "managerEnabled": resolved_manager_enabled,
        "ownerEntrypoint": resolved_owner_entrypoint,
    }
    _write_json(owner_mode_path, payload)
    return control_root


def owner_lease_path(campaign_root: Path) -> Path:
    return campaign_root / "control" / "owner-lease.json"


def read_owner_lease(campaign_root: Path) -> dict[str, Any] | None:
    return _read_json(owner_lease_path(campaign_root))


def _read_pid_file(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except ValueError:
        return None


def owner_lease_is_live(
    payload: Mapping[str, Any] | None,
    *,
    now: datetime | None = None,
) -> bool:
    if not isinstance(payload, Mapping) or payload.get("active") is not True:
        return False
    owner_pid = _coerce_pid(payload.get("ownerPid"))
    child_pid = _coerce_pid(payload.get("childPid"))
    if _pid_is_live(owner_pid) or _pid_is_live(child_pid):
        return True
    lease_seconds_raw = payload.get("leaseSeconds")
    lease_seconds = lease_seconds_raw if isinstance(lease_seconds_raw, int) and lease_seconds_raw > 0 else DEFAULT_OWNER_LEASE_SECONDS
    heartbeat_at = _parse_iso_datetime(payload.get("lastHeartbeatAt"))
    if heartbeat_at is None:
        return False
    now_dt = now or datetime.now(timezone.utc)
    return (now_dt - heartbeat_at).total_seconds() <= lease_seconds


def claim_owner_lease(
    campaign_root: Path,
    *,
    owner_entrypoint: str,
    owner_pid: int,
    session_id: str | None = None,
    child_pid: int | None = None,
    lease_seconds: int = DEFAULT_OWNER_LEASE_SECONDS,
    metadata: Mapping[str, Any] | None = None,
) -> tuple[bool, dict[str, Any]]:
    control_root = ensure_campaign_control_root(campaign_root)
    lease_path = control_root / "owner-lease.json"
    existing = _read_json(lease_path) or {}
    now_iso = _utc_now()
    created_at = existing.get("createdAt")
    if not isinstance(created_at, str) or not created_at:
        created_at = now_iso

    same_owner = False
    existing_owner_pid = _coerce_pid(existing.get("ownerPid"))
    existing_session_id = existing.get("sessionId")
    if existing_owner_pid is not None and existing_owner_pid == owner_pid:
        same_owner = True
    elif isinstance(session_id, str) and session_id and existing_session_id == session_id:
        same_owner = True

    if owner_lease_is_live(existing) and not same_owner:
        return False, existing

    payload: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "campaignId": campaign_root.name,
        "createdAt": created_at,
        "updatedAt": now_iso,
        "active": True,
        "ownerEntrypoint": owner_entrypoint,
        "ownerPid": owner_pid,
        "childPid": child_pid,
        "sessionId": session_id,
        "leaseSeconds": lease_seconds,
        "acquiredAt": existing.get("acquiredAt") if same_owner and isinstance(existing.get("acquiredAt"), str) else now_iso,
        "lastHeartbeatAt": now_iso,
        "releaseReason": None,
        "releasedAt": None,
        "metadata": dict(metadata) if isinstance(metadata, Mapping) else {},
    }
    _write_json(lease_path, payload)
    return True, payload


def refresh_owner_lease(
    campaign_root: Path,
    *,
    owner_entrypoint: str,
    owner_pid: int,
    session_id: str | None = None,
    child_pid: int | None = None,
    lease_seconds: int = DEFAULT_OWNER_LEASE_SECONDS,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    claimed, payload = claim_owner_lease(
        campaign_root,
        owner_entrypoint=owner_entrypoint,
        owner_pid=owner_pid,
        session_id=session_id,
        child_pid=child_pid,
        lease_seconds=lease_seconds,
        metadata=metadata,
    )
    if not claimed:
        raise RuntimeError("owner lease refresh lost ownership")
    return payload


def release_owner_lease(
    campaign_root: Path,
    *,
    owner_entrypoint: str,
    owner_pid: int,
    session_id: str | None = None,
    child_pid: int | None = None,
    release_reason: str,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    control_root = ensure_campaign_control_root(campaign_root)
    lease_path = control_root / "owner-lease.json"
    existing = _read_json(lease_path) or {}
    created_at = existing.get("createdAt")
    if not isinstance(created_at, str) or not created_at:
        created_at = _utc_now()
    payload: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "campaignId": campaign_root.name,
        "createdAt": created_at,
        "updatedAt": _utc_now(),
        "active": False,
        "ownerEntrypoint": owner_entrypoint,
        "ownerPid": owner_pid,
        "childPid": child_pid,
        "sessionId": session_id,
        "leaseSeconds": existing.get("leaseSeconds") if isinstance(existing.get("leaseSeconds"), int) else DEFAULT_OWNER_LEASE_SECONDS,
        "acquiredAt": existing.get("acquiredAt"),
        "lastHeartbeatAt": existing.get("lastHeartbeatAt"),
        "releaseReason": release_reason,
        "releasedAt": _utc_now(),
        "metadata": dict(metadata) if isinstance(metadata, Mapping) else (existing.get("metadata") if isinstance(existing.get("metadata"), dict) else {}),
    }
    _write_json(lease_path, payload)
    return payload


def _normalize_run_spec(raw: dict[str, Any]) -> dict[str, Any]:
    run_id = raw.get("id")
    objective_regex = raw.get("objective_regex")
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("each run spec must contain a non-empty string id")
    if not isinstance(objective_regex, str) or not objective_regex.strip():
        raise ValueError(f"run spec {run_id!r} must contain objective_regex")
    objective_limit = raw.get("objective_limit", 1)
    if not isinstance(objective_limit, int) or objective_limit <= 0:
        raise ValueError(f"run spec {run_id!r} objective_limit must be a positive integer")
    scope_hint = raw.get("scope_hint")
    if scope_hint is not None and (not isinstance(scope_hint, str) or not scope_hint.strip()):
        raise ValueError(f"run spec {run_id!r} scope_hint must be a non-empty string when provided")
    allowed_files = _normalize_allowed_files(raw.get("allowed_files"), scope_hint=scope_hint)
    if allowed_files and len(allowed_files) != objective_limit:
        raise ValueError(f"run spec {run_id!r} allowed_files must match objective_limit")
    return {
        "id": run_id.strip(),
        "objectiveRegex": objective_regex,
        "objectiveLimit": objective_limit,
        "scopeHint": scope_hint.strip() if isinstance(scope_hint, str) else None,
        "allowedFiles": allowed_files,
    }


def _allowed_file_candidates_from_scope_hint(scope_hint: str | None) -> list[str]:
    if not isinstance(scope_hint, str) or not scope_hint.strip():
        return []
    candidates = [item.strip() for item in scope_hint.split(",")]
    normalized: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or not candidate.endswith(".lean") or candidate.startswith("/"):
            return []
        if candidate in seen:
            return []
        seen.add(candidate)
        normalized.append(candidate)
    return normalized


def _normalize_allowed_files(raw: object, *, scope_hint: str | None) -> list[str]:
    if raw is None:
        return _allowed_file_candidates_from_scope_hint(scope_hint)
    if not isinstance(raw, list):
        raise ValueError("allowed_files must be a JSON array of relative .lean paths")
    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            raise ValueError("allowed_files entries must be strings")
        candidate = item.strip()
        if not candidate or not candidate.endswith(".lean") or candidate.startswith("/"):
            raise ValueError("allowed_files entries must be non-empty relative .lean paths")
        if candidate in seen:
            raise ValueError("allowed_files entries must be unique")
        seen.add(candidate)
        normalized.append(candidate)
    return normalized


def _iter_campaign_source_files(source_root: Path) -> list[str]:
    files: list[str] = []
    for path in sorted(source_root.rglob("*.lean")):
        if any(part in IGNORED_SHARD_DIRS for part in path.parts):
            continue
        files.append(path.relative_to(source_root).as_posix())
    return files


def _chunked(items: list[str], size: int) -> list[list[str]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def _slugify_run_id_fragment(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").lower()
    if not slug:
        raise ValueError(f"unable to derive a run id fragment from {value!r}")
    return slug


def _build_shard_run_id(
    shard: list[str],
    *,
    offset: int,
    run_id_prefix: str,
    run_id_mode: str,
) -> str:
    if run_id_mode == "index":
        return f"{run_id_prefix}-{offset:03d}"
    if run_id_mode == "file_stem":
        if len(shard) != 1:
            raise ValueError("run_id_mode=file_stem requires shard_size=1")
        return f"{run_id_prefix}-{_slugify_run_id_fragment(Path(shard[0]).stem)}"
    raise ValueError(f"unsupported run_id_mode: {run_id_mode}")


def plan_campaign_shards(
    source_root: Path,
    *,
    run_id_prefix: str = "teacher",
    run_id_mode: str = "index",
    include_regex: str | None = None,
    limit: int | None = None,
    shard_size: int = 1,
    start_index: int = 1,
) -> list[dict[str, Any]]:
    source_root = source_root.resolve()
    if shard_size <= 0:
        raise ValueError("shard_size must be positive")
    if start_index <= 0:
        raise ValueError("start_index must be positive")
    if limit is not None and limit <= 0:
        raise ValueError("limit must be positive when provided")
    if run_id_mode not in {"index", "file_stem"}:
        raise ValueError("run_id_mode must be 'index' or 'file_stem'")

    matched = _iter_campaign_source_files(source_root)
    if include_regex:
        pattern = re.compile(include_regex)
        matched = [path for path in matched if pattern.search(path)]
    if limit is not None:
        matched = matched[:limit]

    shard_specs: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for offset, shard in enumerate(_chunked(matched, shard_size), start=start_index):
        escaped = [re.escape(path) for path in shard]
        objective_regex = "^(" + "|".join(escaped) + ")$"
        run_id = _build_shard_run_id(
            shard,
            offset=offset,
            run_id_prefix=run_id_prefix,
            run_id_mode=run_id_mode,
        )
        if run_id in seen_ids:
            raise ValueError(f"generated duplicate run id: {run_id}")
        seen_ids.add(run_id)
        shard_specs.append(
            {
                "id": run_id,
                "objective_regex": objective_regex,
                "objective_limit": len(shard),
                "scope_hint": shard[0] if len(shard) == 1 else ", ".join(shard),
                "allowed_files": list(shard),
            }
        )
    return shard_specs


def _uv_run_command(archon_root: Path, cli_name: str, *args: str) -> list[str]:
    return ["uv", "run", "--directory", str(archon_root), cli_name, *args]


def _uv_run_rendered(archon_root: Path, cli_name: str, *args: str) -> str:
    return _command_rendered(_uv_run_command(archon_root, cli_name, *args))


def _build_teacher_prompt(
    *,
    archon_root: Path,
    run_root: Path,
    source_root: Path,
    workspace_root: Path,
    teacher_model: str,
    teacher_reasoning_effort: str,
    plan_timeout_seconds: int,
    prover_timeout_seconds: int,
    prover_idle_seconds: int,
    preload_historical_routes: bool,
) -> str:
    bootstrap_state_path = run_root / "control" / "bootstrap-state.json"
    supervised_cycle_args = [
        "--workspace",
        str(workspace_root),
        "--source",
        str(source_root),
        "--plan-timeout-seconds",
        str(plan_timeout_seconds),
        "--prover-timeout-seconds",
        str(prover_timeout_seconds),
        "--tail-scope-objective-threshold",
        str(DEFAULT_TAIL_SCOPE_OBJECTIVE_THRESHOLD),
        "--tail-scope-plan-timeout-seconds",
        str(max(plan_timeout_seconds, 300)),
        "--tail-scope-prover-timeout-seconds",
        str(max(prover_timeout_seconds, 360)),
        "--prover-idle-seconds",
        str(prover_idle_seconds),
        "--no-review",
    ]
    if preload_historical_routes:
        supervised_cycle_args.append("--preload-historical-routes")
    supervised_cycle_cmd = _uv_run_rendered(
        archon_root,
        "autoarchon-supervised-cycle",
        *supervised_cycle_args,
    )
    export_cmd = _uv_run_rendered(
        archon_root,
        "autoarchon-export-run-artifacts",
        "--run-root",
        str(run_root),
    )
    return "\n".join(
        [
            "Use $archon-supervisor to supervise this AutoArchon run.",
            "",
            f"Repository root: {archon_root}",
            f"Run root: {run_root}",
            f"Source root: {source_root}",
            f"Workspace root: {workspace_root}",
            f"Bootstrap state: {bootstrap_state_path}",
            "",
            "Mission:",
            "- keep theorem headers faithful to source",
            "- supervise repeated plan/prover cycles until the scoped objectives are solved, or a blocker is validated, or an external stop condition is hit",
            f"- read {bootstrap_state_path} first; if it says `freshRun = true`, do one source/workspace fidelity check and launch a supervised cycle instead of spending turns rediscovering missing notes or leases",
            f"- prefer {supervised_cycle_cmd}",
            (
                "- historical accepted routes are preloaded for this run; use them as bounded experience-reuse hints, "
                "not as proof of benchmark-faithful freshness"
            )
            if preload_historical_routes
            else "- historical route preloading is disabled for this run unless the operator explicitly enabled it",
            f"- use model `{teacher_model}` with reasoning effort `{teacher_reasoning_effort}`",
            f"- export milestone artifacts with {export_cmd}",
            "",
            "Rules:",
            "- do not widen scope unless the user changes it",
            "- do not trust prover self-reports without checking source/workspace/task_results",
            "- if the theorem is false as written, keep the original theorem frozen and accept a durable blocker note",
            "- if theorem mutation appears, restore fidelity before counting progress",
            "- do not stop to give an interim report; keep writing workspace/.archon/supervisor/HOT_NOTES.md and workspace/.archon/supervisor/LEDGER.md instead",
            "",
            "Stop only when:",
            "- the scoped files are solved and verified, or",
            "- the remaining target is a validated blocker with a written note, or",
            "- a hard external dependency is missing and the run cannot continue safely",
            "",
        ]
    )


def build_orchestrator_prompt(
    *,
    archon_root: Path,
    campaign_root: Path,
) -> str:
    recover_cmd = _uv_run_rendered(
        archon_root,
        "autoarchon-campaign-recover",
        "--campaign-root",
        str(campaign_root),
        "--all-recoverable",
        "--execute",
    )
    return "\n".join(
        [
            "Use $archon-orchestrator to own this AutoArchon campaign.",
            "",
            f"Repository root: {archon_root}",
            f"Campaign root: {campaign_root}",
            "",
            "Mission:",
            "- treat the campaign root as the control plane source of truth",
            "- inspect CAMPAIGN_MANIFEST.json, campaign-status.json, events.jsonl, and recommendedRecovery before acting",
            "- if the campaign is still fully queued, do one status refresh and then bulk launch recoverable runs instead of rereading every teacher prompt",
            f"- prefer {recover_cmd} for the first bulk launch when every run is queued",
            "- launch teachers only from runs/<id>/control/launch-teacher.sh or the deterministic recovery command that resolves to those scripts",
            "- keep teachers on disjoint run roots",
            "- prefer deterministic recovery via uv-run control-plane commands over ad hoc shell logic",
            "- do not spend the session printing large prompt or launch-script bodies unless a specific run looks corrupted",
            "- finalize only validated proofs and accepted blocker notes into reports/final/",
            "",
            "Stop only when:",
            "- all runs are in terminal states and reports/final/ is up to date, or",
            "- a hard external dependency prevents safe continuation",
        ]
    )


def campaign_is_terminal(status_payload: Mapping[str, Any]) -> bool:
    runs = status_payload.get("runs")
    if not isinstance(runs, list) or not runs:
        return False
    terminal = {"accepted", "blocked", "contaminated"}
    for run in runs:
        if not isinstance(run, Mapping):
            return False
        if run.get("status") not in terminal:
            return False
    return True


def _build_launch_script(
    *,
    archon_root: Path,
    workspace_root: Path,
    source_root: Path,
    run_root: Path,
    teacher_model: str,
    teacher_reasoning_effort: str,
    objective_limit: int,
    objective_regex: str,
    scope_hint: str | None,
    allowed_files: list[str],
    prompt_path: Path,
    preload_historical_routes: bool,
) -> str:
    archon_rendered = shlex.quote(str(archon_root))
    workspace_rendered = shlex.quote(str(workspace_root))
    prompt_rendered = shlex.quote(str(prompt_path))
    control_rendered = shlex.quote(str(run_root / "control"))
    events_rendered = shlex.quote(str(run_root.parent.parent / "events.jsonl"))
    campaign_id_rendered = shlex.quote(run_root.parent.parent.name)
    run_id_rendered = shlex.quote(run_root.name)
    prewarm_stdout_rendered = shlex.quote(str(run_root / "control" / "prewarm.stdout.log"))
    prewarm_stderr_rendered = shlex.quote(str(run_root / "control" / "prewarm.stderr.log"))
    init_cmd = (
        f"{shlex.quote(str(archon_root / 'init.sh'))} --skip-mcp "
        f"--objective-limit {objective_limit} --objective-regex {shlex.quote(objective_regex)} {workspace_rendered}"
    )
    prewarm_args = [str(workspace_root)]
    for rel_path in _scoped_prewarm_verify_files(allowed_files):
        prewarm_args.extend(["--verify-file", rel_path])
    prewarm_cmd = _command_rendered(_uv_run_command(archon_root, "autoarchon-prewarm-project", *prewarm_args))
    return "\n".join(
        [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            "",
            f"ARCHON_ROOT={archon_rendered}",
            f"RUN_ROOT={shlex.quote(str(run_root))}",
            f"WORKSPACE_ROOT={workspace_rendered}",
            f"SOURCE_ROOT={shlex.quote(str(source_root))}",
            f"CONTROL_ROOT={control_rendered}",
            f"PROMPT_FILE={prompt_rendered}",
            f"EVENTS_FILE={events_rendered}",
            f"CAMPAIGN_ID={campaign_id_rendered}",
            f"RUN_ID={run_id_rendered}",
            f"PREWARM_STDOUT_LOG={prewarm_stdout_rendered}",
            f"PREWARM_STDERR_LOG={prewarm_stderr_rendered}",
            "LAUNCH_FINALIZED=0",
            'LAUNCH_STATE_FILE="${CONTROL_ROOT}/teacher-launch-state.json"',
            'BOOTSTRAP_STATE_FILE="${CONTROL_ROOT}/bootstrap-state.json"',
            "",
            "write_launch_state() {",
            '  python3 - "$LAUNCH_STATE_FILE" "$1" "$2" "${3:-}" "${4:-}" <<'"'"'PY'"'"'',
            "import json",
            "import sys",
            "from datetime import datetime, timezone",
            "from pathlib import Path",
            "",
            "path = Path(sys.argv[1])",
            "phase = sys.argv[2]",
            'active = sys.argv[3].lower() == "true"',
            'exit_code_raw = sys.argv[4] if len(sys.argv) > 4 else ""',
            'pid_raw = sys.argv[5] if len(sys.argv) > 5 else ""',
            "payload = {",
            f'    "schemaVersion": {SCHEMA_VERSION},',
            '    "active": active,',
            '    "phase": phase,',
            '    "launcher": "launch-teacher.sh",',
            '    "updatedAt": datetime.now(timezone.utc).isoformat(),',
            "}",
            'if exit_code_raw:',
            '    payload["exitCode"] = int(exit_code_raw)',
            'if pid_raw:',
            '    payload["pid"] = int(pid_raw)',
            'path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\\n", encoding="utf-8")',
            "PY",
            "}",
            "",
            "bootstrap_prewarm_required() {",
            '  python3 - "$BOOTSTRAP_STATE_FILE" <<'"'"'PY'"'"'',
            "import json",
            "import sys",
            "from pathlib import Path",
            "",
            "path = Path(sys.argv[1])",
            "if not path.exists():",
            "    print('1')",
            "    raise SystemExit(0)",
            "try:",
            "    payload = json.loads(path.read_text(encoding='utf-8'))",
            "except json.JSONDecodeError:",
            "    print('1')",
            "    raise SystemExit(0)",
            "if not isinstance(payload, dict):",
            "    print('1')",
            "    raise SystemExit(0)",
            "print('1' if payload.get('prewarmRequired', True) else '0')",
            "PY",
            "}",
            "",
            "mark_bootstrap_prewarm_complete() {",
            '  python3 - "$BOOTSTRAP_STATE_FILE" <<'"'"'PY'"'"'',
            "import json",
            "import sys",
            "from datetime import datetime, timezone",
            "from pathlib import Path",
            "",
            "path = Path(sys.argv[1])",
            "payload = {}",
            "if path.exists():",
            "    try:",
            "        loaded = json.loads(path.read_text(encoding='utf-8'))",
            "    except json.JSONDecodeError:",
            "        loaded = {}",
            "    if isinstance(loaded, dict):",
            "        payload = loaded",
            "payload['prewarmRequired'] = False",
            "payload['updatedAt'] = datetime.now(timezone.utc).isoformat()",
            "path.write_text(json.dumps(payload, indent=2, sort_keys=True) + '\\n', encoding='utf-8')",
            "PY",
            "}",
            "",
            "append_event() {",
            '  python3 - "$EVENTS_FILE" "$CAMPAIGN_ID" "$RUN_ID" "$1" "${2:-}" "${3:-}" <<'"'"'PY'"'"'',
            "import json",
            "import sys",
            "from datetime import datetime, timezone",
            "from pathlib import Path",
            "",
            "path = Path(sys.argv[1])",
            "campaign_id = sys.argv[2]",
            "run_id = sys.argv[3]",
            "kind = sys.argv[4]",
            'phase = sys.argv[5] if len(sys.argv) > 5 else ""',
            'exit_code_raw = sys.argv[6] if len(sys.argv) > 6 else ""',
            "payload = {",
            f'    "schemaVersion": {SCHEMA_VERSION},',
            '    "timestamp": datetime.now(timezone.utc).isoformat(),',
            '    "event": kind,',
            '    "campaignId": campaign_id,',
            '    "runId": run_id,',
            "}",
            'if phase:',
            '    payload["phase"] = phase',
            'if exit_code_raw:',
            '    payload["exitCode"] = int(exit_code_raw)',
            'path.parent.mkdir(parents=True, exist_ok=True)',
            'with path.open("a", encoding="utf-8") as handle:',
            '    handle.write(json.dumps(payload, sort_keys=True) + "\\n")',
            "PY",
            "}",
            "",
            "on_exit() {",
            "  local exit_code=$?",
            '  if [[ ${exit_code} -ne 0 && "${LAUNCH_FINALIZED}" != "1" ]]; then',
            '    write_launch_state "failed" "false" "${exit_code}" "$$"',
            '    append_event "teacher_launch_completed" "failed" "${exit_code}"',
            "  fi",
            "}",
            'trap on_exit EXIT',
            "",
            'write_launch_state "bootstrap" "true" "" "$$"',
            'append_event "teacher_launch_started" "bootstrap"',
            "",
            'if [[ ! -f "${WORKSPACE_ROOT}/.archon/RUN_SCOPE.md" ]]; then',
            '  if [[ "$(bootstrap_prewarm_required)" == "1" ]]; then',
            '    write_launch_state "prewarm" "true" "" "$$"',
            f'    {prewarm_cmd} > "${{PREWARM_STDOUT_LOG}}" 2> "${{PREWARM_STDERR_LOG}}"',
            '    mark_bootstrap_prewarm_complete',
            "  fi",
            '  write_launch_state "init" "true" "" "$$"',
            f"  {init_cmd}",
            "fi",
            "",
            'write_launch_state "codex_exec" "true" "" "$$"',
            "",
            'export ARCHON_CODEX_READY_RETRIES="${ARCHON_CODEX_READY_RETRIES:-6}"',
            'export ARCHON_CODEX_READY_RETRY_DELAY_SECONDS="${ARCHON_CODEX_READY_RETRY_DELAY_SECONDS:-10}"',
            (
                'export ARCHON_SUPERVISOR_PRELOAD_HISTORICAL_ROUTES="${ARCHON_SUPERVISOR_PRELOAD_HISTORICAL_ROUTES:-1}"'
                if preload_historical_routes
                else 'export ARCHON_SUPERVISOR_PRELOAD_HISTORICAL_ROUTES="${ARCHON_SUPERVISOR_PRELOAD_HISTORICAL_ROUTES:-0}"'
            ),
            'cd "${ARCHON_ROOT}"',
            "if codex exec \\",
            "  --skip-git-repo-check \\",
            "  --sandbox danger-full-access \\",
            "  -c approval_policy=never \\",
            f"  -c model_reasoning_effort={shlex.quote(teacher_reasoning_effort)} \\",
            f"  --model {shlex.quote(teacher_model)} \\",
            '  - < "${PROMPT_FILE}"; then',
            '  LAUNCH_FINALIZED=1',
            '  write_launch_state "completed" "false" "0" "$$"',
            '  append_event "teacher_launch_completed" "completed" "0"',
            "else",
            "  exit_code=$?",
            '  LAUNCH_FINALIZED=1',
            '  write_launch_state "failed" "false" "${exit_code}" "$$"',
            '  append_event "teacher_launch_completed" "failed" "${exit_code}"',
            '  exit "${exit_code}"',
            "fi",
            "",
        ]
    )


def _load_validation_payloads(validation_root: Path) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for path in sorted(validation_root.glob("*.json")):
        payload = _read_json(path)
        if payload is not None:
            payloads.append(payload)
    return payloads


def _combine_validation_summaries(
    workspace_summary: Mapping[str, Any],
    artifact_summary: Mapping[str, Any],
) -> dict[str, Any]:
    artifact_closed = set(artifact_summary.get("acceptedProofs", [])) | set(artifact_summary.get("acceptedBlockers", [])) | set(
        artifact_summary.get("rejectedTargets", [])
    )
    workspace_closed = set(workspace_summary.get("acceptedProofs", [])) | set(
        workspace_summary.get("acceptedBlockers", [])
    ) | set(workspace_summary.get("rejectedTargets", []))
    closed_targets = workspace_closed | artifact_closed

    validation_by_path: dict[str, dict[str, Any]] = {}
    artifact_by_path = artifact_summary.get("validationByPath", {})
    workspace_by_path = workspace_summary.get("validationByPath", {})
    if isinstance(artifact_by_path, Mapping):
        for rel_path, payload in artifact_by_path.items():
            if isinstance(rel_path, str) and isinstance(payload, dict):
                validation_by_path[rel_path] = payload
    if isinstance(workspace_by_path, Mapping):
        for rel_path, payload in workspace_by_path.items():
            if isinstance(rel_path, str) and isinstance(payload, dict):
                validation_by_path[rel_path] = payload

    pending_targets = set(workspace_summary.get("pendingTargets", [])) - closed_targets
    attention_targets = set(workspace_summary.get("attentionTargets", [])) - closed_targets

    return {
        "acceptedProofs": sorted(set(workspace_summary.get("acceptedProofs", [])) | set(artifact_summary.get("acceptedProofs", []))),
        "acceptedBlockers": sorted(
            set(workspace_summary.get("acceptedBlockers", [])) | set(artifact_summary.get("acceptedBlockers", []))
        ),
        "rejectedTargets": sorted(
            set(workspace_summary.get("rejectedTargets", [])) | set(artifact_summary.get("rejectedTargets", []))
        ),
        "pendingTargets": sorted(pending_targets),
        "attentionTargets": sorted(attention_targets),
        "validationByPath": validation_by_path,
    }


def _accepted_proof_events_by_run(campaign_root: Path) -> dict[str, set[str]]:
    accepted: dict[str, set[str]] = {}
    for event in _read_event_log(campaign_root / "events.jsonl"):
        if event.get("event") != "validation_accepted":
            continue
        run_id = event.get("runId")
        rel_path = event.get("relPath")
        if not isinstance(run_id, str) or not run_id or not isinstance(rel_path, str) or not rel_path:
            continue
        accepted.setdefault(run_id, set()).add(rel_path)
    return accepted


def _merge_sticky_artifact_acceptance(
    validation_summary: dict[str, Any],
    *,
    artifacts_root: Path,
    accepted_from_events: set[str] | None,
) -> dict[str, Any]:
    if not accepted_from_events:
        return validation_summary

    accepted_proofs = set(validation_summary.get("acceptedProofs", []))
    rejected_targets = set(validation_summary.get("rejectedTargets", []))
    pending_targets = set(validation_summary.get("pendingTargets", []))
    attention_targets = set(validation_summary.get("attentionTargets", []))

    changed = False
    for rel_path in accepted_from_events:
        if rel_path in accepted_proofs or rel_path in rejected_targets:
            continue
        if not (artifacts_root / "proofs" / rel_path).exists():
            continue
        accepted_proofs.add(rel_path)
        pending_targets.discard(rel_path)
        attention_targets.discard(rel_path)
        changed = True

    if not changed:
        return validation_summary

    return {
        **validation_summary,
        "acceptedProofs": sorted(accepted_proofs),
        "pendingTargets": sorted(pending_targets),
        "attentionTargets": sorted(attention_targets),
    }


def _list_file_names(root: Path, pattern: str) -> list[str]:
    if not root.exists():
        return []
    return sorted(path.name for path in root.glob(pattern) if path.is_file())


def _heartbeat_age_from_iso(timestamp: object) -> float | None:
    if not isinstance(timestamp, str) or not timestamp:
        return None
    try:
        parsed = datetime.fromisoformat(timestamp)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, datetime.now(timezone.utc).timestamp() - parsed.timestamp())


def _live_launch_script_paths() -> set[Path]:
    try:
        result = subprocess.run(
            ["ps", "-ewwo", "args="],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return set()
    if result.returncode != 0:
        return set()

    paths: set[Path] = set()
    pattern = re.compile(r"(/[^\s]+/launch-teacher\.sh)\b")
    for line in result.stdout.splitlines():
        if "launch-teacher.sh" not in line:
            continue
        for match in pattern.findall(line):
            paths.add(Path(match).resolve())
    return paths


def _live_launch_process_records(campaign_root: Path) -> list[dict[str, Any]]:
    try:
        result = subprocess.run(
            ["ps", "-ewwo", "pid=,pgid=,etimes=,args="],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return []
    if result.returncode != 0:
        return []

    campaign_root = campaign_root.resolve()
    pattern = re.compile(re.escape(str(campaign_root)) + r"/runs/([^\s]+)/control/launch-teacher\.sh")
    records: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        match = pattern.search(line)
        if not match:
            continue
        parts = line.strip().split(None, 3)
        if len(parts) != 4:
            continue
        pid_raw, pgid_raw, etimes_raw, args = parts
        if not (pid_raw.isdigit() and pgid_raw.isdigit() and etimes_raw.isdigit()):
            continue
        run_id = match.group(1)
        records.append(
            {
                "runId": run_id,
                "pid": int(pid_raw),
                "pgid": int(pgid_raw),
                "elapsedSeconds": int(etimes_raw),
                "command": args,
            }
        )
    return records


def _effective_launch_activity(
    launch_state: Mapping[str, Any] | None,
    *,
    heartbeat_seconds: int,
    launch_script: Path | None = None,
    live_launch_scripts: set[Path] | None = None,
) -> tuple[bool | None, float | None]:
    if not isinstance(launch_state, Mapping):
        return None, None
    heartbeat_age = _heartbeat_age_from_iso(launch_state.get("updatedAt"))
    active = launch_state.get("active")
    if active is False:
        return False, heartbeat_age
    if active is not True:
        return None, heartbeat_age

    pid = _coerce_pid(launch_state.get("pid"))
    if pid is not None:
        return _pid_is_live(pid), heartbeat_age
    if launch_script is not None and live_launch_scripts is not None and launch_script.resolve() in live_launch_scripts:
        return True, heartbeat_age
    if heartbeat_age is None:
        return True, None
    return heartbeat_age <= min(heartbeat_seconds, LAUNCH_GRACE_SECONDS), heartbeat_age


def _effective_lease_activity(
    lease: Mapping[str, Any] | None,
    *,
    heartbeat_seconds: int,
) -> tuple[bool | None, float | None]:
    if not isinstance(lease, Mapping):
        return None, None
    heartbeat_age = _heartbeat_age_from_iso(lease.get("lastHeartbeatAt"))
    active = lease.get("active")
    if active is False:
        return False, heartbeat_age
    if active is not True:
        return None, heartbeat_age

    supervisor_pid = _coerce_pid(lease.get("supervisorPid"))
    loop_pid = _coerce_pid(lease.get("loopPid"))
    pid_candidates = [pid for pid in (supervisor_pid, loop_pid) if pid is not None]
    if pid_candidates:
        if any(_pid_is_live(pid) for pid in pid_candidates):
            return True, heartbeat_age
        return False, heartbeat_age
    if heartbeat_age is None:
        return True, None
    return heartbeat_age <= heartbeat_seconds, heartbeat_age


def _launch_has_live_identity(
    launch_state: Mapping[str, Any] | None,
    *,
    launch_script: Path,
    live_launch_scripts: set[Path] | None,
) -> bool:
    if not isinstance(launch_state, Mapping):
        return False
    pid = _coerce_pid(launch_state.get("pid"))
    if pid is not None and _pid_is_live(pid):
        return True
    return live_launch_scripts is not None and launch_script.resolve() in live_launch_scripts


def _lease_terminal_timestamp(lease: Mapping[str, Any] | None) -> datetime | None:
    if not isinstance(lease, Mapping):
        return None
    return (
        _parse_iso_datetime(lease.get("updatedAt"))
        or _parse_iso_datetime(lease.get("completedAt"))
        or _parse_iso_datetime(lease.get("lastHeartbeatAt"))
    )


def _select_stale_launch_processes_for_run(
    *,
    run_summary: Mapping[str, Any],
    launch_state: Mapping[str, Any] | None,
    lease: Mapping[str, Any] | None,
    launchers: list[dict[str, Any]],
    now_ts: float,
    duplicate_grace_seconds: int,
) -> list[dict[str, Any]]:
    if not launchers:
        return []
    sorted_launchers = sorted(launchers, key=lambda item: int(item["elapsedSeconds"]))
    newest = sorted_launchers[0]
    launch_updated = _parse_iso_datetime(launch_state.get("updatedAt")) if isinstance(launch_state, Mapping) else None
    lease_started = _parse_iso_datetime(lease.get("startedAt")) if isinstance(lease, Mapping) else None
    lease_terminal = _lease_terminal_timestamp(lease)
    newest_started_ts = now_ts - int(newest["elapsedSeconds"])
    reference_dts = [dt for dt in (launch_updated, lease_started, lease_terminal) if dt is not None]
    newest_reference_ts = max((dt.timestamp() for dt in reference_dts), default=None)

    candidates: list[dict[str, Any]] = []
    for launcher in sorted_launchers[1:]:
        started_ts = now_ts - int(launcher["elapsedSeconds"])
        stale_reason: str | None = None
        if newest_reference_ts is not None and started_ts + duplicate_grace_seconds < newest_reference_ts:
            stale_reason = "superseded_by_newer_launch"
        elif started_ts + duplicate_grace_seconds < newest_started_ts:
            stale_reason = "older_duplicate_launcher"
        if stale_reason is None:
            continue
        candidates.append(
            {
                **launcher,
                "reason": stale_reason,
                "runStatus": run_summary.get("status"),
            }
        )

    if candidates:
        return candidates

    primary = newest
    primary_started_ts = now_ts - int(primary["elapsedSeconds"])
    if (
        run_summary.get("runningSignal") is False
        and run_summary.get("launchActive") is False
        and run_summary.get("status") != "running"
    ):
        if lease_terminal is not None and primary_started_ts + duplicate_grace_seconds < lease_terminal.timestamp():
            return [
                {
                    **primary,
                    "reason": "stale_after_terminal_lease",
                    "runStatus": run_summary.get("status"),
                }
            ]
        if isinstance(launch_state, Mapping) and launch_state.get("active") is False and launch_updated is not None:
            if primary_started_ts + duplicate_grace_seconds < launch_updated.timestamp():
                return [
                    {
                        **primary,
                        "reason": "launch_marked_inactive",
                        "runStatus": run_summary.get("status"),
                    }
                ]
    return []


def _effective_launch_activity_with_lease(
    *,
    launch_state: Mapping[str, Any] | None,
    lease: Mapping[str, Any] | None,
    heartbeat_seconds: int,
    launch_script: Path,
    live_launch_scripts: set[Path] | None,
) -> tuple[bool | None, float | None]:
    launch_active, launch_heartbeat_age = _effective_launch_activity(
        launch_state,
        heartbeat_seconds=heartbeat_seconds,
        launch_script=launch_script,
        live_launch_scripts=live_launch_scripts,
    )
    if not isinstance(lease, Mapping) or lease.get("active") is not False:
        return launch_active, launch_heartbeat_age

    launch_live_identity = _launch_has_live_identity(
        launch_state,
        launch_script=launch_script,
        live_launch_scripts=live_launch_scripts,
    )
    lease_updated_at = _lease_terminal_timestamp(lease)
    launch_updated_at = _parse_iso_datetime(launch_state.get("updatedAt")) if isinstance(launch_state, Mapping) else None
    if launch_active is True and launch_live_identity and (
        lease_updated_at is None or (launch_updated_at is not None and launch_updated_at > lease_updated_at)
    ):
        return True, launch_heartbeat_age
    return False, launch_heartbeat_age


def _is_running_signal(
    run_root: Path,
    *,
    heartbeat_seconds: int,
    live_launch_scripts: set[Path] | None = None,
) -> tuple[bool, float | None]:
    launch_script = run_root / "control" / "launch-teacher.sh"
    launch_state = _read_json(run_root / "control" / "teacher-launch-state.json")
    lease = _read_json(run_root / "workspace" / ".archon" / "supervisor" / "run-lease.json")
    launch_active, launch_heartbeat_age = _effective_launch_activity_with_lease(
        launch_state=launch_state,
        lease=lease,
        heartbeat_seconds=heartbeat_seconds,
        launch_script=launch_script,
        live_launch_scripts=live_launch_scripts,
    )
    if lease is not None:
        lease_active, heartbeat_age = _effective_lease_activity(
            lease,
            heartbeat_seconds=heartbeat_seconds,
        )
        if lease_active is True:
            if heartbeat_age is None:
                return True, None
            return heartbeat_age <= heartbeat_seconds, heartbeat_age
        if lease_active is False or lease.get("active") is False:
            if launch_active is True:
                return True, launch_heartbeat_age
            # A completed or explicitly inactive supervisor lease is authoritative.
            # Do not resurrect the run as "running" from fresh log mtimes or a stale
            # pre-lease launch marker.
            return False, heartbeat_age

    heartbeat_age = launch_heartbeat_age
    if launch_active is True:
        return True, heartbeat_age
    if launch_state is not None and launch_state.get("active") in {True, False}:
        return False, heartbeat_age

    tracked_paths: list[Path] = []
    supervisor_root = run_root / "workspace" / ".archon" / "supervisor"
    tracked_paths.extend(
        supervisor_root / name for name in ("HOT_NOTES.md", "LEDGER.md", ".supervised-cycle.stdout.tmp", ".supervised-cycle.stderr.tmp")
    )
    latest_iter, _ = latest_iteration_meta(run_root / "workspace")
    if latest_iter is not None:
        iter_root = run_root / "workspace" / ".archon" / "logs" / latest_iter
        tracked_paths.extend(iter_root.glob("provers/*.jsonl"))
        tracked_paths.append(iter_root / "meta.json")
    mtimes = [path.stat().st_mtime for path in tracked_paths if path.exists()]
    if not mtimes:
        return False, None
    heartbeat_age = max(0.0, datetime.now(timezone.utc).timestamp() - max(mtimes))
    tmp_files_present = any(path.exists() for path in tracked_paths if path.name.startswith(".supervised-cycle."))
    return tmp_files_present or heartbeat_age <= heartbeat_seconds, heartbeat_age


def _validation_summary(payloads: list[dict[str, Any]]) -> dict[str, Any]:
    accepted_proofs: list[str] = []
    accepted_blockers: list[str] = []
    rejected: list[str] = []
    pending: list[str] = []
    attention: list[str] = []
    validation_by_path: dict[str, dict[str, Any]] = {}

    for payload in payloads:
        rel_path = payload.get("relPath")
        if not isinstance(rel_path, str) or not rel_path:
            continue
        validation_by_path[rel_path] = payload
        acceptance_status = payload.get("acceptanceStatus")
        blocker_notes = payload.get("blockerNotes")
        blocker_present = isinstance(blocker_notes, list) and bool(blocker_notes)
        checks = payload.get("checks")
        workspace_changed = isinstance(checks, dict) and checks.get("workspaceChanged") is True
        if acceptance_status == "accepted":
            if blocker_present and not workspace_changed:
                accepted_blockers.append(rel_path)
            else:
                accepted_proofs.append(rel_path)
            continue
        if acceptance_status == "rejected":
            rejected.append(rel_path)
            continue
        if acceptance_status == "pending":
            pending.append(rel_path)
            continue
        if payload.get("validationStatus") in {"attention", "failed"}:
            attention.append(rel_path)

    return {
        "acceptedProofs": sorted(set(accepted_proofs)),
        "acceptedBlockers": sorted(set(accepted_blockers)),
        "rejectedTargets": sorted(set(rejected)),
        "pendingTargets": sorted(set(pending)),
        "attentionTargets": sorted(set(attention)),
        "validationByPath": validation_by_path,
    }


def _unverified_rel_paths(
    *,
    changed_files: list[str],
    validation_paths: set[str],
    allowed_files: list[str],
    task_results: list[str],
    validation_payloads: list[dict[str, Any]],
) -> list[str]:
    referenced_task_results: set[str] = set()
    for payload in validation_payloads:
        checks = payload.get("checks")
        if not isinstance(checks, dict):
            continue
        task_result = checks.get("taskResult")
        if not isinstance(task_result, dict):
            continue
        path = task_result.get("path")
        if isinstance(path, str) and path:
            referenced_task_results.add(Path(path).name)

    unverified = {rel_path for rel_path in changed_files if rel_path not in validation_paths}
    if [name for name in task_results if name not in referenced_task_results]:
        unverified.add("task_results")
    return sorted(item for item in unverified if item)


def _remaining_targets(
    *,
    allowed_files: list[str],
    configured_allowed_files: list[str],
    changed_files: list[str],
    validation_summary: Mapping[str, Any],
    unverified_rel_paths: list[str],
) -> list[str]:
    accepted_proofs = validation_summary.get("acceptedProofs", [])
    accepted_blockers = validation_summary.get("acceptedBlockers", [])
    rejected_targets = validation_summary.get("rejectedTargets", [])
    pending_targets = validation_summary.get("pendingTargets", [])
    attention_targets = validation_summary.get("attentionTargets", [])
    closed_targets = set(accepted_proofs) | set(accepted_blockers) | set(rejected_targets)
    objective_targets = set(allowed_files or configured_allowed_files or changed_files)
    unverified_targets = {item for item in unverified_rel_paths if item != "task_results"}
    remaining_targets = (objective_targets | set(pending_targets) | set(attention_targets) | unverified_targets) - closed_targets
    return sorted(item for item in remaining_targets if item)


def _classify_run_status(
    *,
    allowed_files: list[str],
    changed_files: list[str],
    validation_summary: dict[str, Any],
    task_results: list[str],
    running_signal: bool,
    unverified_rel_paths: list[str],
    has_supervisor_state: bool,
    has_launch_state: bool,
) -> str:
    accepted_proofs = validation_summary["acceptedProofs"]
    accepted_blockers = validation_summary["acceptedBlockers"]
    rejected_targets = validation_summary["rejectedTargets"]
    pending_targets = validation_summary["pendingTargets"]
    attention_targets = validation_summary["attentionTargets"]
    closed_targets = set(accepted_proofs) | set(accepted_blockers) | set(rejected_targets)
    expected_targets = set(allowed_files) if allowed_files else set(closed_targets) | set(changed_files)

    if rejected_targets:
        return "contaminated"
    if expected_targets and expected_targets.issubset(closed_targets) and not pending_targets and not unverified_rel_paths:
        if closed_targets and set(closed_targets).issubset(set(accepted_blockers)):
            return "blocked"
        return "accepted"
    if running_signal:
        return "running"
    if unverified_rel_paths:
        return "unverified"
    if pending_targets or attention_targets or changed_files or task_results or has_supervisor_state or has_launch_state:
        return "needs_relaunch"
    return "queued"


def _configured_allowed_files(run_payload: Mapping[str, Any], bootstrap_payload: Mapping[str, Any] | None) -> list[str]:
    if isinstance(bootstrap_payload, Mapping):
        raw = bootstrap_payload.get("allowedFiles")
        if isinstance(raw, list) and all(isinstance(item, str) for item in raw):
            return [str(item) for item in raw]
    raw = run_payload.get("allowedFiles")
    if isinstance(raw, list) and all(isinstance(item, str) for item in raw):
        return [str(item) for item in raw]
    return []


def _planned_prewarm_mode(*, configured_allowed_files: list[str], project_build_reused: bool) -> str:
    if project_build_reused:
        return "reuse_build_outputs"
    verify_files = _scoped_prewarm_verify_files(configured_allowed_files)
    if not verify_files:
        return "full_build"
    if len(verify_files) < len(configured_allowed_files):
        return "scoped_verify_sample"
    if 0 < len(configured_allowed_files) <= MAX_SCOPED_PREWARM_FILES:
        return "scoped_verify"
    return "full_build"


def _prewarm_summary(
    *,
    plan: str,
    configured_allowed_files: list[str],
    prewarm_pending: bool | None,
) -> str:
    parts = [plan]
    if plan == "scoped_verify_sample" and configured_allowed_files:
        parts.append(f"sample {len(_scoped_prewarm_verify_files(configured_allowed_files))}/{len(configured_allowed_files)} files")
    elif configured_allowed_files:
        parts.append(f"{len(configured_allowed_files)} files")
    if prewarm_pending is not None:
        parts.append("pending" if prewarm_pending else "ready")
    return ", ".join(parts)


def campaign_is_terminal(status_payload: dict[str, Any]) -> bool:
    runs = status_payload.get("runs")
    if not isinstance(runs, list) or not runs:
        return False
    for run in runs:
        if not isinstance(run, dict):
            return False
        if run.get("status") not in TERMINAL_RUN_STATUSES:
            return False
    return True


def _command_rendered(parts: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in parts)


def _write_teacher_launch_state(
    path: Path,
    *,
    active: bool,
    phase: str,
    launcher: str | None = None,
    pid: int | None = None,
    exit_code: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "active": active,
        "phase": phase,
        "updatedAt": _utc_now(),
    }
    if launcher:
        payload["launcher"] = launcher
    if pid is not None:
        payload["pid"] = pid
    if exit_code is not None:
        payload["exitCode"] = exit_code
    _write_json(path, payload)
    return payload


def _load_campaign_manifest(campaign_root: Path) -> dict[str, Any]:
    manifest = _read_json(campaign_root / "CAMPAIGN_MANIFEST.json")
    if manifest is None:
        raise FileNotFoundError(f"missing CAMPAIGN_MANIFEST.json under {campaign_root}")
    runs = manifest.get("runs")
    if not isinstance(runs, list):
        raise ValueError("CAMPAIGN_MANIFEST.json must contain a runs list")
    return manifest


def _manifest_run_index(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    runs = manifest.get("runs")
    if not isinstance(runs, list):
        raise ValueError("CAMPAIGN_MANIFEST.json must contain a runs list")
    indexed: dict[str, dict[str, Any]] = {}
    for run in runs:
        if isinstance(run, dict) and isinstance(run.get("id"), str):
            indexed[str(run["id"])] = run
    return indexed


def _recommended_recovery(
    *,
    archon_root: Path,
    campaign_root: Path,
    run_summary: dict[str, Any],
) -> dict[str, Any]:
    run_root = campaign_root / str(run_summary["runRoot"])
    workspace_root = run_root / "workspace"
    source_root = run_root / "source"
    launch_script = campaign_root / str(run_summary["teacherLaunchScript"])
    recovery_only_cmd = _uv_run_command(
        archon_root,
        "autoarchon-supervised-cycle",
        "--workspace",
        str(workspace_root),
        "--source",
        str(source_root),
        "--recovery-only",
        "--skip-process-check",
    )
    launch_cmd = ["bash", str(launch_script)]
    status = str(run_summary["status"])
    if status == "queued":
        return {
            "action": "launch_teacher",
            "reason": "The run has not started yet.",
            "command": _command_rendered(launch_cmd),
        }
    if status == "unverified":
        return {
            "action": "recovery_only",
            "reason": "Changed files or durable notes exist without full validation closure.",
            "command": _command_rendered(recovery_only_cmd),
        }
    if status == "needs_relaunch":
        return {
            "action": "relaunch_teacher",
            "reason": "The run has partial state but no active teacher and no accepted closure.",
            "command": _command_rendered(launch_cmd),
        }
    if status == "contaminated":
        return {
            "action": "manual_rebuild",
            "reason": "Validation rejected the run. Rebuild or quarantine the run instead of patching evidence in place.",
            "command": None,
        }
    return {
        "action": "none",
        "reason": "No recovery action is needed for this run state.",
        "command": None,
    }


def _launch_failure_summary(control_root: Path, launch_state: Mapping[str, Any] | None) -> dict[str, Any]:
    stderr_tail = _tail_text(control_root / "teacher-launch.stderr.log")
    launch_exit_code = None
    if isinstance(launch_state, Mapping):
        raw_exit_code = launch_state.get("exitCode")
        if isinstance(raw_exit_code, int):
            launch_exit_code = raw_exit_code
    rate_limited = "429 Too Many Requests" in stderr_tail or "rate limit" in stderr_tail.lower()
    timed_out = "codex exec timed out after" in stderr_tail
    reconnecting = "ERROR: Reconnecting..." in stderr_tail
    return {
        "lastLaunchExitCode": launch_exit_code,
        "rateLimited": rate_limited,
        "timedOut": timed_out,
        "reconnecting": reconnecting,
    }


def _retry_after(
    *,
    recovery_class: str,
    launch_state: Mapping[str, Any] | None,
) -> str | None:
    if recovery_class not in {"rate_limited_backoff", "launch_failed_retry"}:
        return None
    updated_at = launch_state.get("updatedAt") if isinstance(launch_state, Mapping) else None
    updated_dt = _parse_iso_datetime(updated_at)
    if updated_dt is None:
        return None
    seconds = (
        RATE_LIMIT_RETRY_AFTER_SECONDS
        if recovery_class == "rate_limited_backoff"
        else DEFAULT_LAUNCH_RETRY_AFTER_SECONDS
    )
    return (updated_dt + timedelta(seconds=seconds)).isoformat()


def _recovery_class(
    *,
    status: str,
    changed_files: list[str],
    task_results: list[str],
    accepted_proofs: list[str],
    accepted_blockers: list[str],
    pending_targets: list[str],
    attention_targets: list[str],
    latest_iteration: str | None,
    launch_failure: Mapping[str, Any],
) -> str:
    if status in {"accepted", "blocked"}:
        return "terminal"
    if status == "contaminated":
        return "manual_rebuild"
    if status == "running":
        return "running"
    if status == "queued":
        return "queued_launch"
    if status == "unverified":
        return "recovery_finalize"
    if status != "needs_relaunch":
        return "manual_review"
    if bool(launch_failure.get("rateLimited")):
        return "rate_limited_backoff"
    has_partial_progress = bool(
        changed_files
        or task_results
        or accepted_proofs
        or accepted_blockers
        or pending_targets
        or attention_targets
        or latest_iteration
    )
    if has_partial_progress:
        return "partial_progress_relaunch"
    return "launch_failed_retry"


def _latest_run_activity_timestamp(run_root: Path, latest_iteration: str | None) -> str | None:
    tracked_paths: list[Path] = []
    tracked_paths.extend(
        [
            run_root / "control" / "teacher-launch-state.json",
            run_root / "control" / "teacher-launch.stdout.log",
            run_root / "control" / "teacher-launch.stderr.log",
            run_root / "workspace" / ".archon" / "supervisor" / "run-lease.json",
            run_root / "workspace" / ".archon" / "supervisor" / "HOT_NOTES.md",
            run_root / "workspace" / ".archon" / "supervisor" / "LEDGER.md",
        ]
    )
    validation_root = run_root / "workspace" / ".archon" / "validation"
    task_results_root = run_root / "workspace" / ".archon" / "task_results"
    if validation_root.exists():
        tracked_paths.extend(path for path in validation_root.glob("*.json") if path.is_file())
    if task_results_root.exists():
        tracked_paths.extend(path for path in task_results_root.glob("*.md") if path.is_file())
    if latest_iteration:
        iter_root = run_root / "workspace" / ".archon" / "logs" / latest_iteration
        tracked_paths.append(iter_root / "meta.json")
        tracked_paths.extend(path for path in iter_root.glob("provers/*.jsonl") if path.is_file())
    mtimes = [path.stat().st_mtime for path in tracked_paths if path.exists()]
    return _iso_from_timestamp(max(mtimes) if mtimes else None)


def _append_campaign_status_events(
    campaign_root: Path,
    *,
    previous_status: Mapping[str, Any] | None,
    current_status: Mapping[str, Any],
) -> None:
    previous_runs = _status_index(previous_status)
    current_runs = _status_index(current_status)
    changed_run_ids: list[str] = []
    accepted_events = 0
    existing_proof_acceptances: set[tuple[str, str]] = set()
    existing_blocker_acceptances: set[tuple[str, str]] = set()

    for event in _read_event_log(campaign_root / "events.jsonl"):
        run_id = event.get("runId")
        rel_path = event.get("relPath")
        if not isinstance(run_id, str) or not run_id or not isinstance(rel_path, str) or not rel_path:
            continue
        kind = event.get("event")
        if kind == "validation_accepted":
            existing_proof_acceptances.add((run_id, rel_path))
        elif kind == "blocker_accepted":
            existing_blocker_acceptances.add((run_id, rel_path))

    for run_id, current_run in current_runs.items():
        previous_run = previous_runs.get(run_id, {})
        previous_state = previous_run.get("status")
        current_state = current_run.get("status")
        if previous_state != current_state:
            changed_run_ids.append(run_id)
            _append_jsonl(
                campaign_root / "events.jsonl",
                _event(
                    {
                        "campaignId": campaign_root.name,
                        "runId": run_id,
                        "statusBefore": previous_state,
                        "statusAfter": current_state,
                        "latestIteration": current_run.get("latestIteration"),
                    },
                    kind="run_status_changed",
                ),
            )

        previous_proofs = set(previous_run.get("acceptedProofs", [])) if isinstance(previous_run.get("acceptedProofs"), list) else set()
        current_proofs = set(current_run.get("acceptedProofs", [])) if isinstance(current_run.get("acceptedProofs"), list) else set()
        for rel_path in sorted(current_proofs - previous_proofs):
            acceptance_key = (run_id, rel_path)
            if acceptance_key in existing_proof_acceptances:
                continue
            accepted_events += 1
            _append_jsonl(
                campaign_root / "events.jsonl",
                _event(
                    {
                        "campaignId": campaign_root.name,
                        "runId": run_id,
                        "relPath": rel_path,
                    },
                    kind="validation_accepted",
                ),
            )
            existing_proof_acceptances.add(acceptance_key)

        previous_blockers = set(previous_run.get("acceptedBlockers", [])) if isinstance(previous_run.get("acceptedBlockers"), list) else set()
        current_blockers = set(current_run.get("acceptedBlockers", [])) if isinstance(current_run.get("acceptedBlockers"), list) else set()
        for rel_path in sorted(current_blockers - previous_blockers):
            blocker_key = (run_id, rel_path)
            if blocker_key in existing_blocker_acceptances:
                continue
            accepted_events += 1
            validation_name = rel_path.replace("/", "_") + ".json"
            validation_payload = _read_json(campaign_root / str(current_run["runRoot"]) / "workspace" / ".archon" / "validation" / validation_name) or {}
            blocker_notes = validation_payload.get("blockerNotes")
            _append_jsonl(
                campaign_root / "events.jsonl",
                _event(
                    {
                        "campaignId": campaign_root.name,
                        "runId": run_id,
                        "relPath": rel_path,
                        "blockerNotes": blocker_notes if isinstance(blocker_notes, list) else [],
                    },
                    kind="blocker_accepted",
                ),
            )
            existing_blocker_acceptances.add(blocker_key)

    previous_counts = previous_status.get("counts", {}) if isinstance(previous_status, Mapping) else {}
    current_counts = current_status.get("counts", {})
    if changed_run_ids or accepted_events or previous_counts != current_counts:
        _append_jsonl(
            campaign_root / "events.jsonl",
            _event(
                {
                    "campaignId": campaign_root.name,
                    "changedRunIds": changed_run_ids,
                    "acceptedEvents": accepted_events,
                    "counts": current_counts,
                },
                kind="campaign_status_refreshed",
            ),
        )


def _read_event_log(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                payload = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            events.append({"lineNumber": line_number, **payload})
    return events


def _summarize_run_event(event: Mapping[str, Any]) -> str:
    kind = str(event.get("event") or "unknown")
    if kind == "run_created":
        scope_hint = event.get("scopeHint")
        return f"run created{f'; scope {scope_hint}' if isinstance(scope_hint, str) and scope_hint else ''}"
    if kind == "teacher_launch_started":
        phase = event.get("phase")
        return f"teacher launch started{f' ({phase})' if isinstance(phase, str) and phase else ''}"
    if kind == "teacher_launch_completed":
        phase = event.get("phase")
        exit_code = event.get("exitCode")
        rendered = f"teacher launch completed{f' ({phase})' if isinstance(phase, str) and phase else ''}"
        if isinstance(exit_code, int):
            rendered += f"; exit {exit_code}"
        return rendered
    if kind == "recovery_planned":
        action = event.get("resolvedAction")
        return f"recovery planned{f': {action}' if isinstance(action, str) and action else ''}"
    if kind == "run_recovery_executed":
        action = event.get("action")
        detached = event.get("detached")
        rendered = f"recovery executed{f': {action}' if isinstance(action, str) and action else ''}"
        if isinstance(detached, bool):
            rendered += "; detached" if detached else "; foreground"
        return rendered
    if kind == "run_status_changed":
        before = event.get("statusBefore")
        after = event.get("statusAfter")
        before_rendered = before if isinstance(before, str) and before else "unknown"
        after_rendered = after if isinstance(after, str) and after else "unknown"
        return f"status {before_rendered} -> {after_rendered}"
    if kind == "validation_accepted":
        rel_path = event.get("relPath")
        return f"proof accepted{f': {rel_path}' if isinstance(rel_path, str) and rel_path else ''}"
    if kind == "blocker_accepted":
        rel_path = event.get("relPath")
        blocker_notes = event.get("blockerNotes")
        rendered = f"blocker accepted{f': {rel_path}' if isinstance(rel_path, str) and rel_path else ''}"
        if isinstance(blocker_notes, list) and blocker_notes:
            rendered += f" ({', '.join(str(item) for item in blocker_notes)})"
        return rendered
    if kind == "artifact_exported":
        changed_count = event.get("changedFileCount")
        task_result_count = event.get("taskResultCount")
        validation_count = event.get("validationFileCount")
        return (
            "artifacts exported"
            f"; changed={changed_count if isinstance(changed_count, int) else 0}"
            f", task_results={task_result_count if isinstance(task_result_count, int) else 0}"
            f", validations={validation_count if isinstance(validation_count, int) else 0}"
        )
    return kind


def _build_run_timelines(campaign_root: Path, status_payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    by_run: dict[str, list[dict[str, Any]]] = {}
    for event in _read_event_log(campaign_root / "events.jsonl"):
        run_id = event.get("runId")
        if not isinstance(run_id, str) or not run_id:
            continue
        by_run.setdefault(run_id, []).append(
            {
                "lineNumber": event.get("lineNumber"),
                "timestamp": event.get("timestamp"),
                "event": event.get("event"),
                "summary": _summarize_run_event(event),
            }
        )

    timelines: list[dict[str, Any]] = []
    runs = status_payload.get("runs")
    if not isinstance(runs, list):
        return timelines
    for run in runs:
        if not isinstance(run, dict):
            continue
        run_id = run.get("runId")
        if not isinstance(run_id, str) or not run_id:
            continue
        events = by_run.get(run_id, [])
        timelines.append(
            {
                "runId": run_id,
                "status": run.get("status"),
                "eventCount": len(events),
                "lastEventAt": events[-1].get("timestamp") if events else None,
                "events": events,
            }
        )
    return timelines


def _timeline_markdown_lines(run_timelines: list[dict[str, Any]]) -> list[str]:
    lines = [
        "## Run Timelines",
        "",
    ]
    if not run_timelines:
        lines.append("- No run timelines available.")
        lines.append("")
        return lines
    for timeline in run_timelines:
        run_id = timeline.get("runId")
        status = timeline.get("status")
        events = timeline.get("events")
        rendered = " -> ".join(
            str(item.get("summary"))
            for item in events
            if isinstance(item, dict) and isinstance(item.get("summary"), str) and item.get("summary")
        )
        if not rendered:
            rendered = "(no run-scoped events yet)"
        lines.append(f"- {run_id} (`{status}`): {rendered}")
    lines.append("")
    return lines


def _campaign_target_counts(status_payload: Mapping[str, Any]) -> dict[str, int]:
    accepted_proofs = 0
    accepted_blockers = 0
    unverified_artifacts = 0
    pending_targets = 0
    remaining_targets = 0
    attention_targets = 0
    rejected_targets = 0
    changed_files = 0
    task_results = 0
    runs = status_payload.get("runs")
    if isinstance(runs, list):
        for run in runs:
            if not isinstance(run, dict):
                continue
            accepted_proofs += len(run.get("acceptedProofs", [])) if isinstance(run.get("acceptedProofs"), list) else 0
            accepted_blockers += len(run.get("acceptedBlockers", [])) if isinstance(run.get("acceptedBlockers"), list) else 0
            unverified_artifacts += len(run.get("unverifiedArtifacts", [])) if isinstance(run.get("unverifiedArtifacts"), list) else 0
            pending_targets += len(run.get("pendingTargets", [])) if isinstance(run.get("pendingTargets"), list) else 0
            remaining_targets += len(run.get("remainingTargets", [])) if isinstance(run.get("remainingTargets"), list) else 0
            attention_targets += len(run.get("attentionTargets", [])) if isinstance(run.get("attentionTargets"), list) else 0
            rejected_targets += len(run.get("rejectedTargets", [])) if isinstance(run.get("rejectedTargets"), list) else 0
            changed_files += len(run.get("changedFiles", [])) if isinstance(run.get("changedFiles"), list) else 0
            task_results += len(run.get("taskResults", [])) if isinstance(run.get("taskResults"), list) else 0
    return {
        "acceptedProofs": accepted_proofs,
        "acceptedBlockers": accepted_blockers,
        "unverifiedArtifacts": unverified_artifacts,
        "pendingTargets": pending_targets,
        "remainingTargets": remaining_targets,
        "attentionTargets": attention_targets,
        "rejectedTargets": rejected_targets,
        "changedFiles": changed_files,
        "taskResults": task_results,
    }


def _campaign_prewarm_counts(status_payload: Mapping[str, Any]) -> dict[str, Any]:
    plans: dict[str, int] = {}
    pending_runs = 0
    runs = status_payload.get("runs")
    if isinstance(runs, list):
        for run in runs:
            if not isinstance(run, dict):
                continue
            prewarm_plan = run.get("prewarmPlan")
            if isinstance(prewarm_plan, str) and prewarm_plan:
                plans[prewarm_plan] = plans.get(prewarm_plan, 0) + 1
            if run.get("prewarmPending") is True:
                pending_runs += 1
    return {
        "plans": plans,
        "pendingRuns": pending_runs,
    }


def compare_report_freshness(
    status_payload: Mapping[str, Any],
    compare_report: Mapping[str, Any] | None,
) -> dict[str, Any]:
    status_generated_at = status_payload.get("generatedAt")
    compare_generated_at = compare_report.get("generatedAt") if isinstance(compare_report, Mapping) else None
    status_dt = _parse_iso_datetime(status_generated_at)
    compare_dt = _parse_iso_datetime(compare_generated_at)
    counts_match = (
        isinstance(compare_report, Mapping)
        and compare_report.get("runCounts") == status_payload.get("counts")
    )
    campaign_id_match = (
        isinstance(compare_report, Mapping)
        and compare_report.get("campaignId") == status_payload.get("campaignId")
    )
    compare_is_fresh = bool(
        isinstance(compare_report, Mapping)
        and status_dt is not None
        and compare_dt is not None
        and compare_dt >= status_dt
        and counts_match
        and campaign_id_match
    )
    return {
        "statusGeneratedAt": status_generated_at,
        "compareGeneratedAt": compare_generated_at,
        "compareIsFresh": compare_is_fresh,
        "countsMatch": bool(counts_match),
        "campaignIdMatch": bool(campaign_id_match),
    }


def _format_duration_seconds(total_seconds: int | None) -> str:
    if total_seconds is None:
        return "unknown"
    if total_seconds <= 0:
        return "0m"
    minutes, _seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes or not parts:
        parts.append(f"{minutes}m")
    return " ".join(parts)


def estimate_campaign_eta(campaign_root: Path, *, pending_targets: int, non_terminal_run_count: int = 0) -> dict[str, Any]:
    if pending_targets <= 0 and non_terminal_run_count <= 0:
        return {
            "state": "complete",
            "etaSeconds": 0,
            "etaText": "0m",
            "reason": "No pending targets remain.",
        }
    if pending_targets <= 0:
        return {
            "state": "unknown",
            "etaSeconds": None,
            "etaText": "unknown",
            "reason": "Non-terminal runs remain, but remaining file targets are not materialized yet.",
        }

    acceptance_times: list[datetime] = []
    for event in _read_event_log(campaign_root / "events.jsonl"):
        if event.get("event") not in {"validation_accepted", "blocker_accepted"}:
            continue
        parsed = _parse_iso_datetime(event.get("timestamp"))
        if parsed is not None:
            acceptance_times.append(parsed)
    acceptance_times.sort()
    if len(acceptance_times) < 3:
        return {
            "state": "unknown",
            "etaSeconds": None,
            "etaText": "unknown",
            "reason": "Not enough accepted-target events to estimate throughput.",
        }

    span_seconds = int((acceptance_times[-1] - acceptance_times[0]).total_seconds())
    if span_seconds < 1800:
        return {
            "state": "unknown",
            "etaSeconds": None,
            "etaText": "unknown",
            "reason": "Accepted-target window is too short to estimate ETA conservatively.",
        }

    rate = len(acceptance_times) / span_seconds
    if rate <= 0:
        return {
            "state": "unknown",
            "etaSeconds": None,
            "etaText": "unknown",
            "reason": "Accepted-target throughput is zero.",
        }

    eta_seconds = int(pending_targets / rate)
    return {
        "state": "estimated",
        "etaSeconds": eta_seconds,
        "etaText": _format_duration_seconds(eta_seconds),
        "reason": f"Estimated from {len(acceptance_times)} accepted-target events.",
    }


def build_campaign_overview(
    campaign_root: Path,
    *,
    heartbeat_seconds: int = DEFAULT_HEARTBEAT_SECONDS,
    refresh_status: bool = True,
) -> dict[str, Any]:
    campaign_root = campaign_root.resolve()
    status_payload = (
        collect_campaign_status(campaign_root, heartbeat_seconds=heartbeat_seconds)
        if refresh_status
        else (_read_json(campaign_root / "campaign-status.json") or collect_campaign_status(campaign_root, heartbeat_seconds=heartbeat_seconds))
    )
    compare_report = _read_json(campaign_root / "reports" / "final" / "compare-report.json")
    watchdog_state = _read_json(campaign_root / "control" / "orchestrator-watchdog.json")
    owner_mode = _read_json(campaign_root / "control" / "owner-mode.json")
    lease = read_owner_lease(campaign_root)
    watchdog_pid = _read_pid_file(campaign_root / "control" / "watchdog-launch.pid")
    watchdog_pid_live = _pid_is_live(watchdog_pid)
    owner_lease_live = owner_lease_is_live(lease)
    watchdog_state_likely_stale = bool(
        isinstance(watchdog_state, Mapping)
        and watchdog_state.get("watchdogStatus") == "running"
        and not watchdog_pid_live
        and not owner_lease_live
    )
    target_counts = _campaign_target_counts(status_payload)
    prewarm_counts = _campaign_prewarm_counts(status_payload)

    runs = status_payload.get("runs")
    terminal_run_count = 0
    live_run_count = 0
    running_rows: list[dict[str, Any]] = []
    recoverable_rows: list[dict[str, Any]] = []
    if isinstance(runs, list):
        for run in runs:
            if not isinstance(run, dict):
                continue
            if run.get("status") in TERMINAL_RUN_STATUSES:
                terminal_run_count += 1
            if run.get("status") == "running":
                live_run_count += 1
                running_rows.append(
                    {
                        "runId": run.get("runId"),
                        "scopeHint": run.get("scopeHint"),
                        "latestIteration": run.get("latestIteration"),
                        "latestActivityAt": run.get("latestActivityAt"),
                        "livePhase": run.get("livePhase"),
                        "activeProverCount": run.get("activeProverCount"),
                        "helperNoteCount": run.get("helperNoteCount"),
                        "helperReasonCounts": run.get("helperReasonCounts"),
                        "taskResultBlockerCount": run.get("taskResultBlockerCount"),
                        "acceptedProofCount": len(run.get("acceptedProofs", [])) if isinstance(run.get("acceptedProofs"), list) else 0,
                        "acceptedBlockerCount": len(run.get("acceptedBlockers", [])) if isinstance(run.get("acceptedBlockers"), list) else 0,
                        "pendingTargetCount": len(run.get("pendingTargets", [])) if isinstance(run.get("pendingTargets"), list) else 0,
                        "remainingTargetCount": len(run.get("remainingTargets", [])) if isinstance(run.get("remainingTargets"), list) else 0,
                        "helperCooldownState": run.get("helperCooldownState"),
                    }
                )
            recovery = run.get("recommendedRecovery")
            if isinstance(recovery, dict) and recovery.get("action") != "none":
                recoverable_rows.append(
                    {
                        "runId": run.get("runId"),
                        "scopeHint": run.get("scopeHint"),
                        "status": run.get("status"),
                        "recoveryClass": run.get("recoveryClass"),
                        "action": recovery.get("action"),
                        "retryAfter": run.get("retryAfter"),
                        "acceptedProofCount": len(run.get("acceptedProofs", [])) if isinstance(run.get("acceptedProofs"), list) else 0,
                        "acceptedBlockerCount": len(run.get("acceptedBlockers", [])) if isinstance(run.get("acceptedBlockers"), list) else 0,
                        "pendingTargetCount": len(run.get("pendingTargets", [])) if isinstance(run.get("pendingTargets"), list) else 0,
                        "remainingTargetCount": len(run.get("remainingTargets", [])) if isinstance(run.get("remainingTargets"), list) else 0,
                    }
                )
    running_rows.sort(key=lambda item: str(item["runId"]))
    recoverable_rows.sort(key=lambda item: str(item["runId"]))
    report_freshness = compare_report_freshness(status_payload, compare_report)
    eta = estimate_campaign_eta(
        campaign_root,
        pending_targets=target_counts.get("remainingTargets", target_counts["pendingTargets"]),
        non_terminal_run_count=max(0, sum(status_payload.get("counts", {}).values()) - terminal_run_count)
        if isinstance(status_payload.get("counts"), Mapping)
        else max(0, len(running_rows) + len(recoverable_rows) - terminal_run_count),
    )
    progress = build_campaign_progress_payload(
        target_counts=target_counts,
        run_counts=status_payload.get("counts", {}) if isinstance(status_payload.get("counts"), Mapping) else {},
    )
    accepted_targets: list[dict[str, Any]] = []
    if isinstance(runs, list):
        for run in runs:
            if not isinstance(run, dict):
                continue
            run_id = run.get("runId")
            for rel_path in run.get("acceptedProofs", []) if isinstance(run.get("acceptedProofs"), list) else []:
                accepted_targets.append({"kind": "proof", "runId": run_id, "relPath": rel_path})
            for rel_path in run.get("acceptedBlockers", []) if isinstance(run.get("acceptedBlockers"), list) else []:
                accepted_targets.append({"kind": "blocker", "runId": run_id, "relPath": rel_path})

    return {
        "schemaVersion": SCHEMA_VERSION,
        "campaignId": status_payload.get("campaignId", campaign_root.name),
        "generatedAt": _utc_now(),
        "heartbeatSeconds": heartbeat_seconds,
        "runCounts": status_payload.get("counts", {}),
        "targetCounts": target_counts,
        "prewarmCounts": prewarm_counts,
        "terminalRunCount": terminal_run_count,
        "liveRunCount": live_run_count,
        "runningRuns": running_rows,
        "recoverableRuns": recoverable_rows,
        "watchdogStatus": watchdog_state.get("watchdogStatus") if isinstance(watchdog_state, Mapping) else None,
        "restartCount": watchdog_state.get("restartCount") if isinstance(watchdog_state, Mapping) else None,
        "activeWorkRunIds": (
            watchdog_state.get("activeWorkRunIds")
            if isinstance(watchdog_state, Mapping) and isinstance(watchdog_state.get("activeWorkRunIds"), list)
            else [row["runId"] for row in running_rows]
        ),
        "lastProgressAt": watchdog_state.get("lastProgressAt") if isinstance(watchdog_state, Mapping) else None,
        "lastRecoveryAt": watchdog_state.get("lastRecoveryAt") if isinstance(watchdog_state, Mapping) else None,
        "reportFreshness": report_freshness,
        "watchdogRuntime": {
            "watchdogPid": watchdog_pid,
            "watchdogPidLive": watchdog_pid_live,
            "ownerLeaseLive": owner_lease_live,
            "stateLikelyStale": watchdog_state_likely_stale,
            "effectiveMaxActiveLaunches": watchdog_state.get("effectiveMaxActiveLaunches")
            if isinstance(watchdog_state, Mapping)
            else None,
            "providerCooldownUntil": watchdog_state.get("providerCooldownUntil") if isinstance(watchdog_state, Mapping) else None,
            "providerCooldownSeconds": watchdog_state.get("providerCooldownSeconds")
            if isinstance(watchdog_state, Mapping)
            else None,
            "likelyCause": watchdog_state.get("likelyCause") if isinstance(watchdog_state, Mapping) else None,
            "resourceSnapshot": watchdog_state.get("resourceSnapshot") if isinstance(watchdog_state, Mapping) else None,
        },
        "progress": progress,
        "eta": eta,
        "statusBuckets": _status_buckets(
            status_payload.get("counts", {}) if isinstance(status_payload.get("counts"), Mapping) else {}
        ),
        "recentTransitions": _recent_transitions(campaign_root),
        "recommendedCommands": _recommended_commands(
            campaign_root,
            running_rows=running_rows,
            recoverable_rows=recoverable_rows,
        ),
        "cooldownState": _cooldown_state(
            runs=[run for run in runs if isinstance(run, dict)] if isinstance(runs, list) else [],
            watchdog_runtime={
                "providerCooldownUntil": watchdog_state.get("providerCooldownUntil") if isinstance(watchdog_state, Mapping) else None,
                "providerCooldownSeconds": watchdog_state.get("providerCooldownSeconds") if isinstance(watchdog_state, Mapping) else None,
            },
        ),
        "recentFinalizedTargets": accepted_targets[:12],
        "ownerMode": owner_mode,
        "ownerLease": lease,
        "paths": {
            "campaignRoot": str(campaign_root),
            "statusPath": str(campaign_root / "campaign-status.json"),
            "compareReportPath": str(campaign_root / "reports" / "final" / "compare-report.json"),
            "finalSummaryPath": str(campaign_root / "reports" / "final" / "final-summary.json"),
            "finalProofsRoot": str(campaign_root / "reports" / "final" / "proofs"),
            "finalBlockersRoot": str(campaign_root / "reports" / "final" / "blockers"),
            "watchdogStatePath": str(campaign_root / "control" / "orchestrator-watchdog.json"),
            "progressSummaryPath": str(campaign_root / "control" / "progress-summary.md"),
            "progressSummaryJsonPath": str(campaign_root / "control" / "progress-summary.json"),
            "progressSummaryHtmlPath": str(campaign_root / "control" / "progress-summary.html"),
        },
    }


def build_campaign_progress_payload(
    *,
    target_counts: Mapping[str, Any],
    run_counts: Mapping[str, Any],
) -> dict[str, Any]:
    accepted_proofs = int(target_counts.get("acceptedProofs", 0) or 0)
    accepted_blockers = int(target_counts.get("acceptedBlockers", 0) or 0)
    remaining_targets = int(target_counts.get("remainingTargets", 0) or 0)
    unverified_artifacts = int(target_counts.get("unverifiedArtifacts", 0) or 0)
    finalized_targets = accepted_proofs + accepted_blockers
    total_targets = finalized_targets + remaining_targets + unverified_artifacts

    if total_targets > 0:
        percent = int(round((finalized_targets / total_targets) * 100))
        completed = finalized_targets
        total = total_targets
        label = "finalized targets"
    else:
        accepted_runs = int(run_counts.get("accepted", 0) or 0)
        blocked_runs = int(run_counts.get("blocked", 0) or 0)
        contaminated_runs = int(run_counts.get("contaminated", 0) or 0)
        completed = accepted_runs + blocked_runs + contaminated_runs
        total = sum(int(value or 0) for value in run_counts.values()) if run_counts else 0
        percent = int(round((completed / total) * 100)) if total > 0 else 0
        label = "terminal runs"

    bar_width = 20
    filled = max(0, min(bar_width, int(round((percent / 100) * bar_width))))
    bar = "[" + "#" * filled + "-" * (bar_width - filled) + "]"
    return {
        "completed": completed,
        "total": total,
        "percent": percent,
        "bar": bar,
        "label": label,
    }


def _status_buckets(run_counts: Mapping[str, Any]) -> dict[str, Any]:
    normalized = {str(key): int(value or 0) for key, value in run_counts.items()}
    terminal_total = sum(normalized.get(status, 0) for status in TERMINAL_RUN_STATUSES)
    active_total = normalized.get("running", 0)
    queued_total = normalized.get("queued", 0)
    attention_total = sum(
        count
        for status, count in normalized.items()
        if status not in TERMINAL_RUN_STATUSES and status not in {"running", "queued"}
    )
    return {
        "terminal": {
            "total": terminal_total,
            "accepted": normalized.get("accepted", 0),
            "blocked": normalized.get("blocked", 0),
            "contaminated": normalized.get("contaminated", 0),
        },
        "active": {
            "total": active_total,
            "running": normalized.get("running", 0),
        },
        "queued": {
            "total": queued_total,
            "queued": normalized.get("queued", 0),
        },
        "attention": {
            "total": attention_total,
            "statuses": {
                status: count
                for status, count in sorted(normalized.items())
                if status not in TERMINAL_RUN_STATUSES and status not in {"running", "queued"}
            },
        },
    }


def _recent_transitions(campaign_root: Path, *, limit: int = 12) -> list[dict[str, Any]]:
    transitions: list[dict[str, Any]] = []
    for event in _read_event_log(campaign_root / "events.jsonl"):
        if event.get("event") not in {
            "run_status_changed",
            "validation_accepted",
            "blocker_accepted",
            "run_recovery_executed",
            "recovery_planned",
            "campaign_status_refreshed",
        }:
            continue
        transitions.append(
            {
                "timestamp": event.get("timestamp"),
                "event": event.get("event"),
                "runId": event.get("runId"),
                "summary": _summarize_run_event(event),
            }
        )
    return transitions[-limit:]


def _recommended_commands(
    campaign_root: Path,
    *,
    running_rows: list[dict[str, Any]],
    recoverable_rows: list[dict[str, Any]],
) -> list[dict[str, str]]:
    commands = [
        {
            "label": "Watch campaign",
            "command": f"bash scripts/watch_campaign.sh {shlex.quote(str(campaign_root))}",
        },
        {
            "label": "Refresh overview",
            "command": f"uv run autoarchon-campaign-overview --campaign-root {shlex.quote(str(campaign_root))}",
        },
    ]
    if recoverable_rows:
        commands.append(
            {
                "label": "Dry-run recovery",
                "command": f"uv run autoarchon-campaign-recover --campaign-root {shlex.quote(str(campaign_root))} --dry-run",
            }
        )
    if running_rows:
        commands.append(
            {
                "label": "Watch first active run",
                "command": f"bash scripts/watch_run.sh {shlex.quote(str(campaign_root / 'runs' / str(running_rows[0]['runId']) / 'workspace'))}",
            }
        )
    return commands


def _cooldown_state(
    *,
    runs: list[dict[str, Any]],
    watchdog_runtime: Mapping[str, Any],
) -> dict[str, Any]:
    helper_cooldowns: list[dict[str, Any]] = []
    for run in runs:
        cooldown_state = run.get("helperCooldownState")
        if not isinstance(cooldown_state, Mapping):
            continue
        active_reasons = cooldown_state.get("activeReasons")
        if not isinstance(active_reasons, list) or not active_reasons:
            continue
        helper_cooldowns.append(
            {
                "runId": run.get("runId"),
                "activeReasons": active_reasons,
            }
        )
    return {
        "watchdogProviderCooldownUntil": watchdog_runtime.get("providerCooldownUntil"),
        "watchdogProviderCooldownSeconds": watchdog_runtime.get("providerCooldownSeconds"),
        "runHelperCooldowns": helper_cooldowns,
    }


def render_campaign_overview_markdown(overview: Mapping[str, Any]) -> str:
    campaign_id = overview.get("campaignId", "unknown")
    generated_at = overview.get("generatedAt", "unknown")
    run_counts = json.dumps(overview.get("runCounts", {}), sort_keys=True)
    target_counts = json.dumps(overview.get("targetCounts", {}), sort_keys=True)
    prewarm_counts = json.dumps(overview.get("prewarmCounts", {}), sort_keys=True)
    watchdog_status = overview.get("watchdogStatus") or "unknown"
    restart_count = overview.get("restartCount")
    active_work_run_ids = overview.get("activeWorkRunIds")
    active_work_text = ", ".join(active_work_run_ids) if isinstance(active_work_run_ids, list) and active_work_run_ids else "none"
    report_freshness = overview.get("reportFreshness") if isinstance(overview.get("reportFreshness"), Mapping) else {}
    eta = overview.get("eta") if isinstance(overview.get("eta"), Mapping) else {}
    paths = overview.get("paths") if isinstance(overview.get("paths"), Mapping) else {}
    watchdog_runtime = overview.get("watchdogRuntime") if isinstance(overview.get("watchdogRuntime"), Mapping) else {}
    progress = overview.get("progress") if isinstance(overview.get("progress"), Mapping) else {}
    running_runs = overview.get("runningRuns") if isinstance(overview.get("runningRuns"), list) else []
    recoverable_runs = overview.get("recoverableRuns") if isinstance(overview.get("recoverableRuns"), list) else []

    lines = [
        f"# Campaign Overview: {campaign_id}",
        "",
        f"- Generated at: `{generated_at}`",
        f"- Run counts: `{run_counts}`",
        f"- Target counts: `{target_counts}`",
        f"- Prewarm counts: `{prewarm_counts}`",
        f"- Watchdog status: `{watchdog_status}`",
        f"- Progress: `{progress.get('bar', '[--------------------]')} {progress.get('percent', 0)}% ({progress.get('completed', 0)}/{progress.get('total', 0)} {progress.get('label', 'items')})`",
        f"- Restart count: `{restart_count if restart_count is not None else 'unknown'}`",
        f"- Active work runs: `{active_work_text}`",
        f"- Last progress at: `{overview.get('lastProgressAt') or 'unknown'}`",
        f"- Last recovery at: `{overview.get('lastRecoveryAt') or 'none'}`",
        f"- Likely cause: `{watchdog_runtime.get('likelyCause') or 'unknown'}`",
        f"- Effective launch cap: `{watchdog_runtime.get('effectiveMaxActiveLaunches')}`",
        f"- Provider cooldown until: `{watchdog_runtime.get('providerCooldownUntil') or 'none'}`",
        f"- Compare fresh: `{report_freshness.get('compareIsFresh')}`",
        f"- ETA: `{eta.get('etaText', 'unknown')}`",
        "",
        "## Running Runs",
        "",
    ]
    if running_runs:
        for row in running_runs:
            lines.append(
                f"- `{row.get('runId')}` iter={row.get('latestIteration')} pending={row.get('pendingTargetCount')} "
                f"phase={row.get('livePhase') or 'unknown'} active_provers={row.get('activeProverCount')} "
                f"remaining={row.get('remainingTargetCount')} accepted_proofs={row.get('acceptedProofCount')} "
                f"accepted_blockers={row.get('acceptedBlockerCount')} helper_notes={row.get('helperNoteCount')} "
                f"blocker_notes={row.get('taskResultBlockerCount')}"
            )
    else:
        lines.append("- none")
    lines.extend(["", "## Recoverable Runs", ""])
    if recoverable_runs:
        for row in recoverable_runs[:16]:
            lines.append(
                f"- `{row.get('runId')}` status={row.get('status')} action={row.get('action')} "
                f"class={row.get('recoveryClass')} pending={row.get('pendingTargetCount')} "
                f"remaining={row.get('remainingTargetCount')}"
            )
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Paths",
            "",
            f"- Campaign root: `{paths.get('campaignRoot', 'unknown')}`",
            f"- Status path: `{paths.get('statusPath', 'unknown')}`",
            f"- Compare report path: `{paths.get('compareReportPath', 'unknown')}`",
            f"- Watchdog state path: `{paths.get('watchdogStatePath', 'unknown')}`",
            f"- Progress summary path: `{paths.get('progressSummaryPath', 'unknown')}`",
            f"- Progress summary JSON path: `{paths.get('progressSummaryJsonPath', 'unknown')}`",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


def render_campaign_progress_markdown(overview: Mapping[str, Any]) -> str:
    campaign_id = overview.get("campaignId", "unknown")
    progress = overview.get("progress") if isinstance(overview.get("progress"), Mapping) else {}
    target_counts = overview.get("targetCounts") if isinstance(overview.get("targetCounts"), Mapping) else {}
    running_runs = overview.get("runningRuns") if isinstance(overview.get("runningRuns"), list) else []
    recoverable_runs = overview.get("recoverableRuns") if isinstance(overview.get("recoverableRuns"), list) else []
    watchdog_runtime = overview.get("watchdogRuntime") if isinstance(overview.get("watchdogRuntime"), Mapping) else {}
    eta = overview.get("eta") if isinstance(overview.get("eta"), Mapping) else {}
    paths = overview.get("paths") if isinstance(overview.get("paths"), Mapping) else {}
    recent_finalized = overview.get("recentFinalizedTargets") if isinstance(overview.get("recentFinalizedTargets"), list) else []
    status_buckets = overview.get("statusBuckets") if isinstance(overview.get("statusBuckets"), Mapping) else {}
    recommended_commands = overview.get("recommendedCommands") if isinstance(overview.get("recommendedCommands"), list) else []
    cooldown_state = overview.get("cooldownState") if isinstance(overview.get("cooldownState"), Mapping) else {}

    lines = [
        f"# Campaign Progress: {campaign_id}",
        "",
        f"- Updated at: `{overview.get('generatedAt', 'unknown')}`",
        f"- Progress: `{progress.get('bar', '[--------------------]')} {progress.get('percent', 0)}% ({progress.get('completed', 0)}/{progress.get('total', 0)} {progress.get('label', 'items')})`",
        f"- Active work runs: `{', '.join(overview.get('activeWorkRunIds', [])) if isinstance(overview.get('activeWorkRunIds'), list) and overview.get('activeWorkRunIds') else 'none'}`",
        f"- Remaining targets: `{target_counts.get('remainingTargets', 0)}`",
        f"- Accepted proofs: `{target_counts.get('acceptedProofs', 0)}`",
        f"- Accepted blockers: `{target_counts.get('acceptedBlockers', 0)}`",
        f"- Watchdog status: `{overview.get('watchdogStatus') or 'unknown'}`",
        f"- Likely cause: `{watchdog_runtime.get('likelyCause') or 'unknown'}`",
        f"- Restart count: `{overview.get('restartCount') if overview.get('restartCount') is not None else 'unknown'}`",
        f"- ETA: `{eta.get('etaText', 'unknown')}`",
        f"- Last progress at: `{overview.get('lastProgressAt') or 'unknown'}`",
        f"- Last recovery at: `{overview.get('lastRecoveryAt') or 'none'}`",
        f"- Status buckets: `{json.dumps(status_buckets, sort_keys=True)}`",
        f"- Cooldown state: `{json.dumps(cooldown_state, sort_keys=True)}`",
        "",
        "## Running",
        "",
    ]
    if running_runs:
        for row in running_runs[:8]:
            lines.append(
                f"- `{row.get('runId')}` scope={row.get('scopeHint') or 'unknown'} iter={row.get('latestIteration')} "
                f"phase={row.get('livePhase') or 'unknown'} remaining={row.get('remainingTargetCount')} "
                f"accepted_proofs={row.get('acceptedProofCount')} accepted_blockers={row.get('acceptedBlockerCount')} "
                f"helper_notes={row.get('helperNoteCount')} blocker_notes={row.get('taskResultBlockerCount')}"
            )
    else:
        lines.append("- none")

    lines.extend(["", "## Recent Finalized", ""])
    if recent_finalized:
        for row in recent_finalized[:8]:
            lines.append(f"- `{row.get('kind')} {row.get('runId')}:{row.get('relPath')}`")
    else:
        lines.append("- none")

    lines.extend(["", "## Attention", ""])
    if recoverable_runs:
        for row in recoverable_runs[:8]:
            lines.append(
                f"- `{row.get('runId')}` scope={row.get('scopeHint') or 'unknown'} status={row.get('status')} "
                f"action={row.get('action')} class={row.get('recoveryClass')}"
            )
    else:
        lines.append("- none")
    lines.extend(["", "## Recommended Commands", ""])
    if recommended_commands:
        for row in recommended_commands[:8]:
            if not isinstance(row, Mapping):
                continue
            lines.append(f"- `{row.get('label', 'command')}`: `{row.get('command', '')}`")
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Paths",
            "",
            f"- Compare report: `{paths.get('compareReportPath', 'unknown')}`",
            f"- Final summary: `{paths.get('finalSummaryPath', 'unknown')}`",
            f"- Proof exports: `{paths.get('finalProofsRoot', 'unknown')}`",
            f"- Blocker exports: `{paths.get('finalBlockersRoot', 'unknown')}`",
        ]
    )
    return "\n".join(lines) + "\n"


def _html_text(value: object) -> str:
    return html.escape(str(value))


def _html_code(value: object) -> str:
    return f"<code>{_html_text(value)}</code>"


def _html_list_items(rows: list[str], *, empty: str = "none") -> str:
    if not rows:
        return f"<li>{_html_text(empty)}</li>"
    return "".join(f"<li>{row}</li>" for row in rows)


def render_campaign_progress_html(overview: Mapping[str, Any]) -> str:
    campaign_id = str(overview.get("campaignId", "unknown"))
    progress = overview.get("progress") if isinstance(overview.get("progress"), Mapping) else {}
    target_counts = overview.get("targetCounts") if isinstance(overview.get("targetCounts"), Mapping) else {}
    running_runs = overview.get("runningRuns") if isinstance(overview.get("runningRuns"), list) else []
    recoverable_runs = overview.get("recoverableRuns") if isinstance(overview.get("recoverableRuns"), list) else []
    watchdog_runtime = overview.get("watchdogRuntime") if isinstance(overview.get("watchdogRuntime"), Mapping) else {}
    eta = overview.get("eta") if isinstance(overview.get("eta"), Mapping) else {}
    paths = overview.get("paths") if isinstance(overview.get("paths"), Mapping) else {}
    recent_finalized = overview.get("recentFinalizedTargets") if isinstance(overview.get("recentFinalizedTargets"), list) else []
    recent_transitions = overview.get("recentTransitions") if isinstance(overview.get("recentTransitions"), list) else []
    recommended_commands = overview.get("recommendedCommands") if isinstance(overview.get("recommendedCommands"), list) else []
    cooldown_state = overview.get("cooldownState") if isinstance(overview.get("cooldownState"), Mapping) else {}
    status_buckets = overview.get("statusBuckets") if isinstance(overview.get("statusBuckets"), Mapping) else {}

    running_rows: list[str] = []
    for row in running_runs[:12]:
        if not isinstance(row, Mapping):
            continue
        running_rows.append(
            " ".join(
                [
                    _html_code(row.get("runId", "unknown")),
                    f"scope={_html_code(row.get('scopeHint') or 'unknown')}",
                    f"phase={_html_code(row.get('livePhase') or 'unknown')}",
                    f"iter={_html_code(row.get('latestIteration') or '(none)')}",
                    f"remaining={_html_code(row.get('remainingTargetCount', 0))}",
                    f"helper_notes={_html_code(row.get('helperNoteCount', 0))}",
                ]
            )
        )

    attention_rows: list[str] = []
    for row in recoverable_runs[:12]:
        if not isinstance(row, Mapping):
            continue
        attention_rows.append(
            " ".join(
                [
                    _html_code(row.get("runId", "unknown")),
                    f"status={_html_code(row.get('status') or 'unknown')}",
                    f"action={_html_code(row.get('action') or 'none')}",
                    f"class={_html_code(row.get('recoveryClass') or 'unknown')}",
                ]
            )
        )

    finalized_rows: list[str] = []
    for row in recent_finalized[:12]:
        if not isinstance(row, Mapping):
            continue
        finalized_rows.append(
            " ".join(
                [
                    _html_code(row.get("kind", "unknown")),
                    _html_code(f"{row.get('runId', 'unknown')}:{row.get('relPath', 'unknown')}"),
                ]
            )
        )

    transition_rows: list[str] = []
    for row in recent_transitions[:12]:
        if not isinstance(row, Mapping):
            continue
        transition_rows.append(
            " ".join(
                [
                    _html_code(row.get("timestamp") or "unknown"),
                    _html_code(row.get("event") or "unknown"),
                    _html_text(row.get("summary") or "unknown"),
                ]
            )
        )

    command_rows: list[str] = []
    for row in recommended_commands[:12]:
        if not isinstance(row, Mapping):
            continue
        command_rows.append(
            f"<strong>{_html_text(row.get('label', 'command'))}</strong><br><code>{_html_text(row.get('command', ''))}</code>"
        )

    path_rows = [
        f"<strong>Canonical JSON</strong><br>{_html_code(paths.get('progressSummaryJsonPath', 'unknown'))}",
        f"<strong>Markdown Summary</strong><br>{_html_code(paths.get('progressSummaryPath', 'unknown'))}",
        f"<strong>Compare Report</strong><br>{_html_code(paths.get('compareReportPath', 'unknown'))}",
        f"<strong>Final Summary</strong><br>{_html_code(paths.get('finalSummaryPath', 'unknown'))}",
    ]

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="30">
  <title>AutoArchon Campaign Progress - {_html_text(campaign_id)}</title>
  <style>
    :root {{
      --bg: #f4f1e8;
      --panel: #fffdf8;
      --ink: #1d1b18;
      --muted: #6f665c;
      --line: #d7cec1;
      --accent: #14532d;
      --accent-2: #9a3412;
      --chip: #efe7da;
      --shadow: 0 10px 30px rgba(40, 31, 20, 0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Iosevka Aile", "IBM Plex Sans", sans-serif;
      background:
        radial-gradient(circle at top left, rgba(20,83,45,0.08), transparent 28rem),
        radial-gradient(circle at top right, rgba(154,52,18,0.08), transparent 22rem),
        var(--bg);
      color: var(--ink);
    }}
    .shell {{
      max-width: 1280px;
      margin: 0 auto;
      padding: 2rem 1.25rem 3rem;
    }}
    h1, h2 {{ margin: 0 0 0.75rem; }}
    p {{ margin: 0; }}
    .hero {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 24px;
      padding: 1.5rem;
      box-shadow: var(--shadow);
      margin-bottom: 1rem;
    }}
    .hero-grid, .metric-grid, .section-grid {{
      display: grid;
      gap: 1rem;
    }}
    .hero-grid {{
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      margin-top: 1rem;
    }}
    .metric-grid {{
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      margin: 1rem 0;
    }}
    .section-grid {{
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
    }}
    .card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 20px;
      padding: 1rem 1rem 1.1rem;
      box-shadow: var(--shadow);
    }}
    .eyebrow {{
      color: var(--muted);
      font-size: 0.82rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      margin-bottom: 0.35rem;
    }}
    .metric {{
      font-size: 1.5rem;
      font-weight: 700;
    }}
    .progress-bar {{
      display: inline-block;
      padding: 0.3rem 0.55rem;
      border-radius: 999px;
      background: var(--chip);
      color: var(--accent);
      font-weight: 700;
    }}
    .subtle {{
      color: var(--muted);
      font-size: 0.95rem;
      line-height: 1.45;
    }}
    ul {{
      margin: 0.6rem 0 0;
      padding-left: 1.1rem;
      line-height: 1.45;
    }}
    li + li {{ margin-top: 0.35rem; }}
    code {{
      font-family: "Iosevka", "IBM Plex Mono", monospace;
      font-size: 0.92em;
      background: #f3ece1;
      padding: 0.08rem 0.24rem;
      border-radius: 6px;
    }}
    .json-block {{
      margin-top: 0.5rem;
      padding: 0.75rem;
      border-radius: 14px;
      background: #faf5ed;
      overflow-x: auto;
      font-family: "Iosevka", "IBM Plex Mono", monospace;
      font-size: 0.88rem;
      white-space: pre-wrap;
    }}
  </style>
</head>
<body>
  <div class="shell">
    <section class="hero">
      <div class="eyebrow">Supplementary inspection only</div>
      <h1>AutoArchon Campaign Progress</h1>
      <p class="subtle">This browser view is generated from the canonical file-backed overview. The source of truth remains <code>{_html_text(paths.get('progressSummaryJsonPath', 'control/progress-summary.json'))}</code>.</p>
      <div class="hero-grid">
        <div>
          <div class="eyebrow">Campaign</div>
          <div class="metric">{_html_text(campaign_id)}</div>
          <p class="subtle">Updated {_html_code(overview.get('generatedAt', 'unknown'))}</p>
        </div>
        <div>
          <div class="eyebrow">Progress</div>
          <div class="metric"><span class="progress-bar">{_html_text(progress.get('bar', '[--------------------]'))} {_html_text(progress.get('percent', 0))}%</span></div>
          <p class="subtle">{_html_text(progress.get('completed', 0))}/{_html_text(progress.get('total', 0))} {_html_text(progress.get('label', 'items'))}</p>
        </div>
        <div>
          <div class="eyebrow">Watchdog</div>
          <div class="metric">{_html_text(overview.get('watchdogStatus') or 'unknown')}</div>
          <p class="subtle">cause={_html_text(watchdog_runtime.get('likelyCause') or 'unknown')} restart_count={_html_text(overview.get('restartCount') if overview.get('restartCount') is not None else 'unknown')}</p>
        </div>
      </div>
      <div class="metric-grid">
        <div class="card"><div class="eyebrow">Remaining Targets</div><div class="metric">{_html_text(target_counts.get('remainingTargets', 0))}</div></div>
        <div class="card"><div class="eyebrow">Accepted Proofs</div><div class="metric">{_html_text(target_counts.get('acceptedProofs', 0))}</div></div>
        <div class="card"><div class="eyebrow">Accepted Blockers</div><div class="metric">{_html_text(target_counts.get('acceptedBlockers', 0))}</div></div>
        <div class="card"><div class="eyebrow">ETA</div><div class="metric">{_html_text(eta.get('etaText', 'unknown'))}</div></div>
      </div>
    </section>

    <div class="section-grid">
      <section class="card">
        <h2>Running</h2>
        <ul>{_html_list_items(running_rows)}</ul>
      </section>
      <section class="card">
        <h2>Recent Finalized</h2>
        <ul>{_html_list_items(finalized_rows)}</ul>
      </section>
      <section class="card">
        <h2>Attention</h2>
        <ul>{_html_list_items(attention_rows)}</ul>
      </section>
      <section class="card">
        <h2>Recommended Commands</h2>
        <ul>{_html_list_items(command_rows)}</ul>
      </section>
      <section class="card">
        <h2>Recent Transitions</h2>
        <ul>{_html_list_items(transition_rows)}</ul>
      </section>
      <section class="card">
        <h2>Paths</h2>
        <ul>{_html_list_items(path_rows)}</ul>
      </section>
      <section class="card">
        <h2>Status Buckets</h2>
        <div class="json-block">{_html_text(json.dumps(status_buckets, indent=2, sort_keys=True))}</div>
      </section>
      <section class="card">
        <h2>Cooldown State</h2>
        <div class="json-block">{_html_text(json.dumps(cooldown_state, indent=2, sort_keys=True))}</div>
      </section>
    </div>
  </div>
</body>
</html>
"""


def write_campaign_progress_surface(campaign_root: Path, overview: Mapping[str, Any]) -> dict[str, str]:
    control_root = campaign_root / "control"
    control_root.mkdir(parents=True, exist_ok=True)
    markdown_path = control_root / "progress-summary.md"
    json_path = control_root / "progress-summary.json"
    html_path = control_root / "progress-summary.html"
    markdown_path.write_text(render_campaign_progress_markdown(overview), encoding="utf-8")
    json_path.write_text(json.dumps(dict(overview), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    html_path.write_text(render_campaign_progress_html(overview), encoding="utf-8")
    return {
        "markdown": str(markdown_path),
        "json": str(json_path),
        "html": str(html_path),
    }


def _maybe_prune_campaign_storage(
    campaign_root: Path,
    *,
    prune_workspace_lake: bool,
    prune_broken_prewarm: bool,
) -> dict[str, Any] | None:
    if not prune_workspace_lake and not prune_broken_prewarm:
        return None
    cleanup_payload = cleanup_stale_launch_processes(campaign_root, execute=True)
    prune_payload = prune_storage_candidates(
        campaign_root,
        prune_workspace_lake=prune_workspace_lake,
        prune_broken_prewarm=prune_broken_prewarm,
        execute=True,
    )
    if cleanup_payload.get("candidateCount"):
        prune_payload["staleLaunchCleanup"] = cleanup_payload
    return prune_payload


def archive_campaign_postmortem(
    campaign_root: Path,
    *,
    heartbeat_seconds: int = DEFAULT_HEARTBEAT_SECONDS,
    prune_workspace_lake: bool = False,
    prune_broken_prewarm: bool = False,
) -> dict[str, Any]:
    campaign_root = campaign_root.resolve()
    status_payload = collect_campaign_status(campaign_root, heartbeat_seconds=heartbeat_seconds)
    compare_report = build_campaign_compare_report(campaign_root, heartbeat_seconds=heartbeat_seconds)
    overview = build_campaign_overview(campaign_root, heartbeat_seconds=heartbeat_seconds, refresh_status=False)
    postmortem_root = campaign_root / "reports" / "postmortem"
    postmortem_root.mkdir(parents=True, exist_ok=True)

    watchdog_state = _read_json(campaign_root / "control" / "orchestrator-watchdog.json")
    owner_mode = _read_json(campaign_root / "control" / "owner-mode.json")
    owner_lease = read_owner_lease(campaign_root)
    watchdog_log_tail = _tail_text(campaign_root / "control" / "orchestrator-watchdog.log", max_bytes=65536)
    recent_events = _read_event_log(campaign_root / "events.jsonl")[-40:]

    incident_tags: list[str] = []
    watchdog_log_lower = watchdog_log_tail.lower()
    if isinstance(watchdog_state, Mapping) and watchdog_state.get("budgetExhausted") is True:
        incident_tags.append("restart_budget_exhausted")
    likely_cause = watchdog_state.get("likelyCause") if isinstance(watchdog_state, Mapping) else None
    if likely_cause == "likely_provider_transport":
        incident_tags.append("provider_transport_instability")
    elif likely_cause == "likely_local_resource_pressure":
        incident_tags.append("local_resource_pressure")
    elif likely_cause == "mixed_or_unknown":
        incident_tags.append("mixed_or_unknown_runtime_cause")
    if (
        (isinstance(watchdog_state, Mapping) and watchdog_state.get("stallReason") == "owner_conflict")
        or "owner conflict" in watchdog_log_lower
        or "competing campaign-owner" in watchdog_log_lower
    ):
        incident_tags.append("owner_conflict")
    if "stale" in watchdog_log_lower or "duplicate" in watchdog_log_lower:
        incident_tags.append("stale_or_conflicting_launch_state")
    if isinstance(overview.get("watchdogRuntime"), Mapping) and overview["watchdogRuntime"].get("stateLikelyStale") is True:
        incident_tags.append("stale_watchdog_state")
    incident_tags = sorted(set(incident_tags))

    _write_json(postmortem_root / "campaign-status.snapshot.json", status_payload)
    _write_json(postmortem_root / "compare-report.snapshot.json", compare_report)
    if isinstance(watchdog_state, dict):
        _write_json(postmortem_root / "orchestrator-watchdog.snapshot.json", watchdog_state)
    if isinstance(owner_mode, dict):
        _write_json(postmortem_root / "owner-mode.snapshot.json", owner_mode)
    if isinstance(owner_lease, dict):
        _write_json(postmortem_root / "owner-lease.snapshot.json", owner_lease)
    _write_text(postmortem_root / "watchdog-log.tail.txt", watchdog_log_tail)

    payload = {
        "schemaVersion": SCHEMA_VERSION,
        "campaignId": status_payload.get("campaignId", campaign_root.name),
        "archivedAt": _utc_now(),
        "overview": overview,
        "runCounts": status_payload.get("counts", {}),
        "targetCounts": _campaign_target_counts(status_payload),
        "prewarmCounts": _campaign_prewarm_counts(status_payload),
        "watchdogState": watchdog_state,
        "ownerMode": owner_mode,
        "ownerLease": owner_lease,
        "incidentTags": incident_tags,
        "recentEvents": recent_events,
        "paths": {
            "campaignRoot": str(campaign_root),
            "postmortemRoot": str(postmortem_root),
            "statusSnapshot": str(postmortem_root / "campaign-status.snapshot.json"),
            "compareSnapshot": str(postmortem_root / "compare-report.snapshot.json"),
            "watchdogLogTail": str(postmortem_root / "watchdog-log.tail.txt"),
        },
    }
    lesson_records: list[dict[str, Any]] = []
    status_by_run = {
        item["runId"]: item
        for item in status_payload.get("runs", [])
        if isinstance(item, dict) and isinstance(item.get("runId"), str)
    }
    for run_id, run_status in status_by_run.items():
        lesson_records.extend(
            _collect_run_lesson_records(
                campaign_id=str(payload["campaignId"]),
                run_id=run_id,
                artifacts_root=campaign_root / "runs" / run_id / "artifacts",
                run_status=run_status,
            )
        )
    lesson_records.extend(
        _watchdog_postmortem_records(
            campaign_id=str(payload["campaignId"]),
            watchdog_state=watchdog_state if isinstance(watchdog_state, Mapping) else None,
            incident_tags=incident_tags,
        )
    )
    postmortem_lessons_root = postmortem_root / "lessons"
    postmortem_records_path = postmortem_lessons_root / "lesson-records.jsonl"
    _write_lesson_records(postmortem_records_path, lesson_records)
    lesson_cluster_paths = write_lesson_cluster_artifacts(
        postmortem_lessons_root,
        records=lesson_records,
        source_paths=[str(postmortem_records_path)],
    )
    payload["paths"]["lessonRecords"] = str(postmortem_records_path)
    payload["paths"]["lessonClusters"] = lesson_cluster_paths
    cache_prune = _maybe_prune_campaign_storage(
        campaign_root,
        prune_workspace_lake=prune_workspace_lake,
        prune_broken_prewarm=prune_broken_prewarm,
    )
    if cache_prune is not None:
        payload["cachePrune"] = cache_prune
    _write_json(postmortem_root / "postmortem-summary.json", payload)

    lines = [
        f"# Postmortem Summary: {payload['campaignId']}",
        "",
        f"- Archived at: `{payload['archivedAt']}`",
        f"- Run counts: `{json.dumps(payload['runCounts'], sort_keys=True)}`",
        f"- Target counts: `{json.dumps(payload['targetCounts'], sort_keys=True)}`",
        f"- Watchdog status: `{watchdog_state.get('watchdogStatus') if isinstance(watchdog_state, Mapping) else 'unknown'}`",
        f"- Incident tags: `{', '.join(incident_tags) if incident_tags else 'none'}`",
            f"- Compare freshness: `{overview['reportFreshness']['compareIsFresh']}`",
            f"- ETA: `{overview['eta']['etaText']}`",
            f"- Likely cause: `{overview['watchdogRuntime'].get('likelyCause') if isinstance(overview.get('watchdogRuntime'), Mapping) else 'unknown'}`",
            f"- Effective launch cap: `{overview['watchdogRuntime'].get('effectiveMaxActiveLaunches') if isinstance(overview.get('watchdogRuntime'), Mapping) else 'unknown'}`",
            f"- Cache prune: `{cache_prune['selectedCount']} selected / {cache_prune['reclaimedBytes']} bytes`" if cache_prune is not None else "- Cache prune: `disabled`",
            "",
            "## Running Runs",
            "",
        ]
    if overview["runningRuns"]:
        for row in overview["runningRuns"]:
            lines.append(
                f"- `{row['runId']}` iter={row['latestIteration']} pending={row['pendingTargetCount']} "
                f"accepted_proofs={row['acceptedProofCount']} accepted_blockers={row['acceptedBlockerCount']}"
            )
    else:
        lines.append("- none")
    lines.extend(["", "## Recoverable Runs", ""])
    if overview["recoverableRuns"]:
        for row in overview["recoverableRuns"][:12]:
            lines.append(
                f"- `{row['runId']}` status={row['status']} action={row['action']} "
                f"class={row['recoveryClass']} pending={row['pendingTargetCount']}"
            )
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Paths",
            "",
            f"- Status snapshot: `{payload['paths']['statusSnapshot']}`",
            f"- Compare snapshot: `{payload['paths']['compareSnapshot']}`",
            f"- Watchdog log tail: `{payload['paths']['watchdogLogTail']}`",
            "",
        ]
    )
    _write_text(postmortem_root / "postmortem-summary.md", "\n".join(lines) + "\n")
    return payload


def _build_bootstrap_state(
    *,
    objective_regex: str,
    objective_limit: int,
    allowed_files: list[str],
    prewarm_required: bool,
    preload_historical_routes: bool,
) -> dict[str, Any]:
    expected_first_action = "verify source/workspace fidelity once, then run a single supervised cycle"
    initial_state_summary = "fresh isolated run; no supervisor lease, task results, validation, or iteration logs yet"
    if not prewarm_required:
        initial_state_summary += "; warmed local Lake build outputs were safely reused from the cache source"
    return {
        "schemaVersion": SCHEMA_VERSION,
        "freshRun": True,
        "allowedFiles": list(allowed_files),
        "objectiveRegex": objective_regex,
        "objectiveLimit": objective_limit,
        "expectedFirstAction": expected_first_action,
        "prewarmRequired": prewarm_required,
        "preloadHistoricalRoutes": preload_historical_routes,
        "initialStateSummary": initial_state_summary,
        "createdAt": _utc_now(),
    }


def create_campaign(
    *,
    archon_root: Path,
    source_root: Path,
    campaign_root: Path,
    run_specs: list[dict[str, Any]],
    reuse_lake_from: Path | None = None,
    teacher_model: str = "gpt-5.4",
    teacher_reasoning_effort: str = "xhigh",
    teacher_scope_policy: str = "single_file_micro_shard",
    plan_timeout_seconds: int = 180,
    prover_timeout_seconds: int = 240,
    prover_idle_seconds: int = 90,
    preload_historical_routes: bool = False,
) -> dict[str, Any]:
    archon_root = archon_root.resolve()
    source_root = source_root.resolve()
    campaign_root = campaign_root.resolve()

    normalized_specs = [_normalize_run_spec(spec) for spec in run_specs]
    run_ids = [spec["id"] for spec in normalized_specs]
    if len(set(run_ids)) != len(run_ids):
        raise ValueError("run spec ids must be unique")

    if campaign_root.exists():
        existing = list(campaign_root.iterdir())
        if existing:
            raise FileExistsError(f"campaign root already exists and is not empty: {campaign_root}")
    else:
        campaign_root.mkdir(parents=True, exist_ok=True)

    runs_root = campaign_root / "runs"
    campaign_control_root = ensure_campaign_control_root(campaign_root)
    ensure_operator_surfaces(
        campaign_root,
        source_root=source_root,
        spec_reference=None,
        resolved_spec={
            "campaignRoot": str(campaign_root),
            "sourceRoot": str(source_root),
            "teacherModel": teacher_model,
            "teacherReasoningEffort": teacher_reasoning_effort,
            "preloadHistoricalRoutes": preload_historical_routes,
        },
        mode="campaign_bootstrap",
        entrypoint="autoarchon-create-campaign",
        note="Campaign root bootstrapped; operator should review mission brief and resolved spec before launch.",
    )
    reports_root = campaign_root / "reports" / "final"
    runs_root.mkdir(parents=True, exist_ok=True)
    reports_root.mkdir(parents=True, exist_ok=True)

    manifest_runs: list[dict[str, Any]] = []
    events_path = campaign_root / "events.jsonl"
    for spec in normalized_specs:
        run_root = runs_root / spec["id"]
        run_manifest = create_isolated_run(
            source_root,
            run_root,
            reuse_lake_from=reuse_lake_from,
            scope_hint=spec["scopeHint"],
        )

        control_root = run_root / "control"
        prompt_path = control_root / "teacher-prompt.txt"
        launch_path = control_root / "launch-teacher.sh"
        bootstrap_path = control_root / "bootstrap-state.json"
        prompt_text = _build_teacher_prompt(
            archon_root=archon_root,
            run_root=run_root,
            source_root=run_root / "source",
            workspace_root=run_root / "workspace",
            teacher_model=teacher_model,
            teacher_reasoning_effort=teacher_reasoning_effort,
            plan_timeout_seconds=plan_timeout_seconds,
            prover_timeout_seconds=prover_timeout_seconds,
            prover_idle_seconds=prover_idle_seconds,
            preload_historical_routes=preload_historical_routes,
        )
        _write_text(prompt_path, prompt_text)
        _write_json(
            bootstrap_path,
            _build_bootstrap_state(
                objective_regex=spec["objectiveRegex"],
                objective_limit=spec["objectiveLimit"],
                allowed_files=spec["allowedFiles"],
                prewarm_required=not bool(run_manifest.get("projectBuildReused")),
                preload_historical_routes=preload_historical_routes,
            ),
        )
        _write_text(
            launch_path,
            _build_launch_script(
                archon_root=archon_root,
                workspace_root=run_root / "workspace",
                source_root=run_root / "source",
                run_root=run_root,
                teacher_model=teacher_model,
                teacher_reasoning_effort=teacher_reasoning_effort,
                objective_limit=spec["objectiveLimit"],
                objective_regex=spec["objectiveRegex"],
                scope_hint=spec["scopeHint"],
                allowed_files=spec["allowedFiles"],
                prompt_path=prompt_path,
                preload_historical_routes=preload_historical_routes,
            ),
        )
        launch_path.chmod(0o755)

        run_payload = {
            "id": spec["id"],
            "scopeHint": spec["scopeHint"],
            "allowedFiles": spec["allowedFiles"],
            "objectiveRegex": spec["objectiveRegex"],
            "objectiveLimit": spec["objectiveLimit"],
            "runRoot": _relative(run_root, start=campaign_root),
            "sourceRoot": _relative(run_root / "source", start=campaign_root),
            "workspaceRoot": _relative(run_root / "workspace", start=campaign_root),
            "artifactsRoot": _relative(run_root / "artifacts", start=campaign_root),
            "controlRoot": _relative(control_root, start=campaign_root),
            "teacherPrompt": _relative(prompt_path, start=campaign_root),
            "teacherLaunchScript": _relative(launch_path, start=campaign_root),
            "preloadHistoricalRoutes": preload_historical_routes,
        }
        _write_json(control_root / "run-config.json", {"schemaVersion": SCHEMA_VERSION, **run_payload})
        manifest_runs.append(run_payload)
        _append_jsonl(
            events_path,
            _event(
                {
                    "campaignId": campaign_root.name,
                    "runId": spec["id"],
                    "runRoot": run_payload["runRoot"],
                    "scopeHint": spec["scopeHint"],
                    "objectiveRegex": spec["objectiveRegex"],
                },
                kind="run_created",
            ),
        )

    manifest = {
        "schemaVersion": SCHEMA_VERSION,
        "campaignId": campaign_root.name,
        "createdAt": _utc_now(),
        "archonRoot": str(archon_root),
        "sourceRoot": str(source_root),
        "campaignRoot": str(campaign_root),
        "reuseLakeFrom": str(reuse_lake_from.resolve()) if reuse_lake_from else None,
        "teacherDefaults": {
            "model": teacher_model,
            "reasoningEffort": teacher_reasoning_effort,
            "scopePolicy": teacher_scope_policy,
            "planTimeoutSeconds": plan_timeout_seconds,
            "proverTimeoutSeconds": prover_timeout_seconds,
            "proverIdleSeconds": prover_idle_seconds,
            "preloadHistoricalRoutes": preload_historical_routes,
        },
        "runs": manifest_runs,
    }
    _write_json(campaign_root / "CAMPAIGN_MANIFEST.json", manifest)
    _append_jsonl(events_path, _event({"campaignId": campaign_root.name, "runCount": len(manifest_runs)}, kind="campaign_created"))

    status_payload = {
        "schemaVersion": SCHEMA_VERSION,
        "campaignId": campaign_root.name,
        "generatedAt": _utc_now(),
        "counts": {"queued": len(manifest_runs)},
        "runs": [
            {
                "runId": run["id"],
                "status": "queued",
                "scopeHint": run["scopeHint"],
                "teacherLaunchScript": run["teacherLaunchScript"],
                "teacherPrompt": run["teacherPrompt"],
            }
            for run in manifest_runs
        ],
    }
    _write_json(campaign_root / "campaign-status.json", status_payload)
    _write_json(
        campaign_control_root / "owner-mode.json",
        {
            "schemaVersion": SCHEMA_VERSION,
            "campaignId": campaign_root.name,
            "createdAt": _utc_now(),
            "updatedAt": _utc_now(),
            "ownerMode": "orchestrator",
            "watchdogEnabled": False,
            "managerEnabled": False,
            "ownerEntrypoint": None,
        },
    )
    return manifest


def refresh_campaign_launch_assets(
    campaign_root: Path,
    *,
    run_ids: list[str] | None = None,
    refresh_prompts: bool = False,
) -> dict[str, Any]:
    campaign_root = campaign_root.resolve()
    manifest = _load_campaign_manifest(campaign_root)
    archon_root = Path(str(manifest["archonRoot"])).resolve()
    teacher_defaults = manifest.get("teacherDefaults")
    teacher_model = "gpt-5.4"
    teacher_reasoning_effort = "xhigh"
    plan_timeout_seconds = 180
    prover_timeout_seconds = 240
    prover_idle_seconds = 90
    preload_historical_routes = False
    if isinstance(teacher_defaults, Mapping):
        if isinstance(teacher_defaults.get("model"), str) and teacher_defaults.get("model"):
            teacher_model = str(teacher_defaults["model"])
        if (
            isinstance(teacher_defaults.get("reasoningEffort"), str)
            and teacher_defaults.get("reasoningEffort")
        ):
            teacher_reasoning_effort = str(teacher_defaults["reasoningEffort"])
        if isinstance(teacher_defaults.get("planTimeoutSeconds"), int):
            plan_timeout_seconds = int(teacher_defaults["planTimeoutSeconds"])
        if isinstance(teacher_defaults.get("proverTimeoutSeconds"), int):
            prover_timeout_seconds = int(teacher_defaults["proverTimeoutSeconds"])
        if isinstance(teacher_defaults.get("proverIdleSeconds"), int):
            prover_idle_seconds = int(teacher_defaults["proverIdleSeconds"])
        if isinstance(teacher_defaults.get("preloadHistoricalRoutes"), bool):
            preload_historical_routes = bool(teacher_defaults["preloadHistoricalRoutes"])

    selected_run_ids = set(run_ids or [])
    runs = manifest.get("runs")
    assert isinstance(runs, list)
    refreshed_runs: list[dict[str, Any]] = []

    for run in runs:
        if not isinstance(run, Mapping):
            continue
        run_id = run.get("id")
        if not isinstance(run_id, str) or not run_id:
            continue
        if selected_run_ids and run_id not in selected_run_ids:
            continue

        run_root = campaign_root / str(run["runRoot"])
        source_root = campaign_root / str(run["sourceRoot"])
        workspace_root = campaign_root / str(run["workspaceRoot"])
        control_root = campaign_root / str(run["controlRoot"])
        prompt_path = campaign_root / str(run["teacherPrompt"])
        launch_path = campaign_root / str(run["teacherLaunchScript"])
        bootstrap_payload = _read_json(control_root / "bootstrap-state.json")
        allowed_files = _configured_allowed_files(run, bootstrap_payload)
        if not allowed_files:
            allowed_files = read_allowed_files(workspace_root)

        if refresh_prompts:
            _write_text(
                prompt_path,
                _build_teacher_prompt(
                    archon_root=archon_root,
                    run_root=run_root,
                    source_root=source_root,
                    workspace_root=workspace_root,
                    teacher_model=teacher_model,
                    teacher_reasoning_effort=teacher_reasoning_effort,
                    plan_timeout_seconds=plan_timeout_seconds,
                    prover_timeout_seconds=prover_timeout_seconds,
                    prover_idle_seconds=prover_idle_seconds,
                    preload_historical_routes=preload_historical_routes,
                ),
            )

        _write_text(
            launch_path,
            _build_launch_script(
                archon_root=archon_root,
                workspace_root=workspace_root,
                source_root=source_root,
                run_root=run_root,
                teacher_model=teacher_model,
                teacher_reasoning_effort=teacher_reasoning_effort,
                objective_limit=int(run["objectiveLimit"]),
                objective_regex=str(run["objectiveRegex"]),
                scope_hint=str(run["scopeHint"]) if isinstance(run.get("scopeHint"), str) else None,
                allowed_files=allowed_files,
                prompt_path=prompt_path,
                preload_historical_routes=preload_historical_routes,
            ),
        )
        launch_path.chmod(0o755)
        _write_json(control_root / "run-config.json", {"schemaVersion": SCHEMA_VERSION, **dict(run)})
        refreshed_runs.append(
            {
                "runId": run_id,
                "launchScript": _relative(launch_path, start=campaign_root),
                "promptRefreshed": refresh_prompts,
            }
        )

    payload = {
        "schemaVersion": SCHEMA_VERSION,
        "campaignId": campaign_root.name,
        "campaignRoot": str(campaign_root),
        "refreshedRuns": refreshed_runs,
        "refreshPrompts": refresh_prompts,
    }
    _append_jsonl(
        campaign_root / "events.jsonl",
        _event(
            {
                "campaignId": campaign_root.name,
                "runIds": [item["runId"] for item in refreshed_runs],
                "refreshPrompts": refresh_prompts,
            },
            kind="launch_assets_refreshed",
        ),
    )
    return payload


def cleanup_stale_launch_processes(
    campaign_root: Path,
    *,
    run_ids: list[str] | None = None,
    heartbeat_seconds: int = DEFAULT_HEARTBEAT_SECONDS,
    duplicate_grace_seconds: int = 60,
    execute: bool = False,
) -> dict[str, Any]:
    campaign_root = campaign_root.resolve()
    status_payload = collect_campaign_status(campaign_root, heartbeat_seconds=heartbeat_seconds)
    run_index = {
        str(run["runId"]): run
        for run in status_payload.get("runs", [])
        if isinstance(run, Mapping) and isinstance(run.get("runId"), str)
    }
    selected_run_ids = set(run_ids or [])
    launch_records = _live_launch_process_records(campaign_root)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in launch_records:
        run_id = str(record["runId"])
        if selected_run_ids and run_id not in selected_run_ids:
            continue
        grouped.setdefault(run_id, []).append(record)

    now_ts = datetime.now(timezone.utc).timestamp()
    candidates: list[dict[str, Any]] = []
    for run_id, launchers in sorted(grouped.items()):
        run_summary = run_index.get(run_id, {})
        run_root_rel = run_summary.get("runRoot")
        if not isinstance(run_root_rel, str):
            continue
        run_root = campaign_root / run_root_rel
        launch_state = _read_json(run_root / "control" / "teacher-launch-state.json")
        lease = _read_json(run_root / "workspace" / ".archon" / "supervisor" / "run-lease.json")
        candidates.extend(
            _select_stale_launch_processes_for_run(
                run_summary=run_summary,
                launch_state=launch_state,
                lease=lease,
                launchers=launchers,
                now_ts=now_ts,
                duplicate_grace_seconds=duplicate_grace_seconds,
            )
        )

    executed: list[dict[str, Any]] = []
    if execute:
        seen_pgids: set[int] = set()
        for candidate in candidates:
            pgid = int(candidate["pgid"])
            if pgid in seen_pgids:
                continue
            seen_pgids.add(pgid)
            killed = False
            error: str | None = None
            try:
                os.killpg(pgid, signal.SIGTERM)
                killed = True
            except ProcessLookupError:
                error = "process_group_missing"
            except PermissionError:
                error = "permission_denied"
            payload = dict(candidate)
            payload["killed"] = killed
            if error:
                payload["error"] = error
            executed.append(payload)
            if killed and str(candidate.get("reason")) in {"stale_after_terminal_lease", "launch_marked_inactive"}:
                run_id = str(candidate.get("runId") or "")
                run_summary = run_index.get(run_id, {})
                run_root_rel = run_summary.get("runRoot")
                if isinstance(run_root_rel, str) and run_root_rel:
                    launch_state_path = campaign_root / run_root_rel / "control" / "teacher-launch-state.json"
                    _write_teacher_launch_state(
                        launch_state_path,
                        active=False,
                        phase="cleanup_terminated",
                        launcher="cleanup_stale_launch_processes",
                    )
        if executed:
            _append_jsonl(
                campaign_root / "events.jsonl",
                _event(
                    {
                        "campaignId": campaign_root.name,
                        "runIds": sorted({str(item["runId"]) for item in executed}),
                        "killedPgids": sorted({int(item["pgid"]) for item in executed if item.get("killed") is True}),
                    },
                    kind="stale_launch_cleanup_executed",
                ),
            )

    return {
        "schemaVersion": SCHEMA_VERSION,
        "campaignId": campaign_root.name,
        "campaignRoot": str(campaign_root),
        "duplicateGraceSeconds": duplicate_grace_seconds,
        "candidateCount": len(candidates),
        "candidates": candidates,
        "executed": executed,
    }


def collect_campaign_status(campaign_root: Path, *, heartbeat_seconds: int = DEFAULT_HEARTBEAT_SECONDS) -> dict[str, Any]:
    campaign_root = campaign_root.resolve()
    manifest = _load_campaign_manifest(campaign_root)
    previous_status = _read_json(campaign_root / "campaign-status.json")
    runs = manifest.get("runs")
    assert isinstance(runs, list)
    archon_root = Path(str(manifest["archonRoot"]))

    run_summaries: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    live_launch_scripts = _live_launch_script_paths()
    accepted_proof_events = _accepted_proof_events_by_run(campaign_root)

    for run in runs:
        if not isinstance(run, dict):
            continue
        run_id = str(run["id"])
        run_root = campaign_root / str(run["runRoot"])
        workspace_root = run_root / "workspace"
        source_root = run_root / "source"
        artifacts_root = run_root / "artifacts"
        validation_root = workspace_root / ".archon" / "validation"
        artifact_validation_root = artifacts_root / "validation"
        task_results_root = workspace_root / ".archon" / "task_results"
        supervisor_root = workspace_root / ".archon" / "supervisor"
        supervisor_progress = _read_json(supervisor_root / "progress-summary.json")
        control_root = run_root / "control"
        launch_script = control_root / "launch-teacher.sh"
        lease_payload = _read_json(supervisor_root / "run-lease.json")
        launch_state = _read_json(control_root / "teacher-launch-state.json")
        launch_active, _ = _effective_launch_activity_with_lease(
            launch_state=launch_state,
            lease=lease_payload,
            heartbeat_seconds=heartbeat_seconds,
            launch_script=launch_script,
            live_launch_scripts=live_launch_scripts,
        )
        lease_active, _ = _effective_lease_activity(
            lease_payload,
            heartbeat_seconds=heartbeat_seconds,
        )
        bootstrap_payload = _read_json(control_root / "bootstrap-state.json")
        run_manifest = _read_json(run_root / "RUN_MANIFEST.json")

        allowed_files = read_allowed_files(workspace_root)
        configured_allowed_files = _configured_allowed_files(run, bootstrap_payload)
        project_build_reused = bool(run_manifest.get("projectBuildReused")) if isinstance(run_manifest, Mapping) else False
        prewarm_pending_value = (
            bootstrap_payload.get("prewarmRequired")
            if isinstance(bootstrap_payload, Mapping)
            else None
        )
        prewarm_pending = prewarm_pending_value if isinstance(prewarm_pending_value, bool) else None
        prewarm_plan = _planned_prewarm_mode(
            configured_allowed_files=configured_allowed_files,
            project_build_reused=project_build_reused,
        )
        changed_files = collect_changed_files(source_root, workspace_root, allowed_files=allowed_files or None)
        workspace_validation_payloads = _load_validation_payloads(validation_root)
        artifact_validation_payloads = _load_validation_payloads(artifact_validation_root)
        validation_summary = _combine_validation_summaries(
            _validation_summary(workspace_validation_payloads),
            _validation_summary(artifact_validation_payloads),
        )
        validation_summary = _merge_sticky_artifact_acceptance(
            validation_summary,
            artifacts_root=artifacts_root,
            accepted_from_events=accepted_proof_events.get(run_id),
        )
        validation_paths = set(validation_summary["validationByPath"])
        task_results = _list_file_names(task_results_root, "*.md")
        running_signal, heartbeat_age_seconds = _is_running_signal(
            run_root,
            heartbeat_seconds=heartbeat_seconds,
            live_launch_scripts=live_launch_scripts,
        )
        has_supervisor_state = supervisor_root.exists() and any(supervisor_root.iterdir())
        has_launch_state = any(
            (control_root / name).exists()
            for name in ("teacher-launch-state.json", "teacher-launch.log", "teacher-launch.stdout.log", "teacher-launch.stderr.log")
        )
        unverified_paths = _unverified_rel_paths(
            changed_files=changed_files,
            validation_paths=validation_paths,
            allowed_files=allowed_files,
            task_results=task_results,
            validation_payloads=workspace_validation_payloads + artifact_validation_payloads,
        )
        remaining_targets = _remaining_targets(
            allowed_files=allowed_files,
            configured_allowed_files=configured_allowed_files,
            changed_files=changed_files,
            validation_summary=validation_summary,
            unverified_rel_paths=unverified_paths,
        )
        status = _classify_run_status(
            allowed_files=allowed_files,
            changed_files=changed_files,
            validation_summary=validation_summary,
            task_results=task_results,
            running_signal=running_signal,
            unverified_rel_paths=unverified_paths,
            has_supervisor_state=has_supervisor_state,
            has_launch_state=has_launch_state,
        )
        latest_iter_name, _ = latest_iteration_meta(workspace_root)
        if (
            latest_iter_name is None
            and isinstance(supervisor_progress, Mapping)
            and isinstance(supervisor_progress.get("liveRuntime"), Mapping)
            and isinstance(supervisor_progress["liveRuntime"].get("iteration"), str)
        ):
            latest_iter_name = str(supervisor_progress["liveRuntime"]["iteration"])
        artifact_index = _read_json(artifacts_root / "artifact-index.json")
        launch_failure = _launch_failure_summary(control_root, launch_state)
        accepted_proofs = validation_summary["acceptedProofs"]
        accepted_blockers = validation_summary["acceptedBlockers"]
        recovery_class = _recovery_class(
            status=status,
            changed_files=changed_files,
            task_results=task_results,
            accepted_proofs=accepted_proofs,
            accepted_blockers=accepted_blockers,
            pending_targets=validation_summary["pendingTargets"],
            attention_targets=validation_summary["attentionTargets"],
            latest_iteration=latest_iter_name,
            launch_failure=launch_failure,
        )
        retry_after = _retry_after(recovery_class=recovery_class, launch_state=launch_state)

        run_summary = {
            "runId": run_id,
            "status": status,
            "scopeHint": run.get("scopeHint"),
            "objectiveRegex": run.get("objectiveRegex"),
            "objectiveLimit": run.get("objectiveLimit"),
            "runRoot": run.get("runRoot"),
            "sourceRoot": run.get("sourceRoot"),
            "workspaceRoot": run.get("workspaceRoot"),
            "artifactsRoot": run.get("artifactsRoot"),
            "teacherLaunchScript": run.get("teacherLaunchScript"),
            "teacherPrompt": run.get("teacherPrompt"),
            "allowedFiles": allowed_files,
            "configuredAllowedFiles": configured_allowed_files,
            "changedFiles": changed_files,
            "taskResults": task_results,
            "acceptedProofs": accepted_proofs,
            "acceptedBlockers": accepted_blockers,
            "pendingTargets": validation_summary["pendingTargets"],
            "attentionTargets": validation_summary["attentionTargets"],
            "remainingTargets": remaining_targets,
            "rejectedTargets": validation_summary["rejectedTargets"],
            "unverifiedArtifacts": unverified_paths,
            "artifactIndexPresent": artifact_index is not None,
            "heartbeatAgeSeconds": heartbeat_age_seconds,
            "runningSignal": running_signal,
            "launchStatePresent": has_launch_state,
            "latestIteration": latest_iter_name,
            "launchActive": launch_active,
            "launchPhase": launch_state.get("phase") if isinstance(launch_state, dict) else None,
            "launchUpdatedAt": launch_state.get("updatedAt") if isinstance(launch_state, dict) else None,
            "lastLaunchExitCode": launch_failure["lastLaunchExitCode"],
            "leaseActive": lease_active,
            "leaseRecordedActive": lease_payload.get("active") if isinstance(lease_payload, dict) else None,
            "leaseStatus": lease_payload.get("status") if isinstance(lease_payload, dict) else None,
            "latestActivityAt": _latest_run_activity_timestamp(run_root, latest_iter_name),
            "recoveryClass": recovery_class,
            "retryAfter": retry_after,
            "prewarmPlan": prewarm_plan,
            "prewarmPending": prewarm_pending,
            "prewarmSummary": _prewarm_summary(
                plan=prewarm_plan,
                configured_allowed_files=configured_allowed_files,
                prewarm_pending=prewarm_pending,
            ),
            "projectBuildReused": project_build_reused,
            "lakePackagesLinked": bool(run_manifest.get("lakePackagesLinked")) if isinstance(run_manifest, Mapping) else False,
            "lakeBuildReusePath": (
                run_manifest.get("lakeBuildReusePath")
                if isinstance(run_manifest, Mapping) and isinstance(run_manifest.get("lakeBuildReusePath"), str)
                else None
            ),
            "livePhase": (
                supervisor_progress.get("liveRuntime", {}).get("phase")
                if isinstance(supervisor_progress, Mapping)
                and isinstance(supervisor_progress.get("liveRuntime"), Mapping)
                and isinstance(supervisor_progress.get("liveRuntime", {}).get("phase"), str)
                else None
            ),
            "livePlanStatus": (
                supervisor_progress.get("liveRuntime", {}).get("planStatus")
                if isinstance(supervisor_progress, Mapping)
                and isinstance(supervisor_progress.get("liveRuntime"), Mapping)
                and isinstance(supervisor_progress.get("liveRuntime", {}).get("planStatus"), str)
                else None
            ),
            "liveProverStatus": (
                supervisor_progress.get("liveRuntime", {}).get("proverStatus")
                if isinstance(supervisor_progress, Mapping)
                and isinstance(supervisor_progress.get("liveRuntime"), Mapping)
                and isinstance(supervisor_progress.get("liveRuntime", {}).get("proverStatus"), str)
                else None
            ),
            "activeProverCount": (
                len(supervisor_progress.get("liveRuntime", {}).get("activeProvers", []))
                if isinstance(supervisor_progress, Mapping)
                and isinstance(supervisor_progress.get("liveRuntime"), Mapping)
                and isinstance(supervisor_progress.get("liveRuntime", {}).get("activeProvers"), list)
                else 0
            ),
            "helperNoteCount": (
                int(supervisor_progress.get("helper", {}).get("noteCount", 0))
                if isinstance(supervisor_progress, Mapping)
                and isinstance(supervisor_progress.get("helper"), Mapping)
                else 0
            ),
            "helperReasonCounts": (
                dict(supervisor_progress.get("helper", {}).get("countsByReason", {}))
                if isinstance(supervisor_progress, Mapping)
                and isinstance(supervisor_progress.get("helper"), Mapping)
                and isinstance(supervisor_progress.get("helper", {}).get("countsByReason"), Mapping)
                else {}
            ),
            "helperCooldownState": (
                dict(supervisor_progress.get("helper", {}).get("cooldownState", {}))
                if isinstance(supervisor_progress, Mapping)
                and isinstance(supervisor_progress.get("helper"), Mapping)
                and isinstance(supervisor_progress.get("helper", {}).get("cooldownState"), Mapping)
                else {}
            ),
            "taskResultBlockerCount": (
                int(supervisor_progress.get("taskResultsSummary", {}).get("counts", {}).get("blocker", 0))
                if isinstance(supervisor_progress, Mapping)
                and isinstance(supervisor_progress.get("taskResultsSummary"), Mapping)
                and isinstance(supervisor_progress.get("taskResultsSummary", {}).get("counts"), Mapping)
                else 0
            ),
        }
        run_summary["recommendedRecovery"] = _recommended_recovery(
            archon_root=archon_root,
            campaign_root=campaign_root,
            run_summary=run_summary,
        )
        run_summaries.append(run_summary)
        counts[status] = counts.get(status, 0) + 1

    payload = {
        "schemaVersion": SCHEMA_VERSION,
        "campaignId": manifest.get("campaignId"),
        "generatedAt": _utc_now(),
        "heartbeatSeconds": heartbeat_seconds,
        "counts": counts,
        "runs": run_summaries,
    }
    _write_json(campaign_root / "campaign-status.json", payload)
    _append_campaign_status_events(
        campaign_root,
        previous_status=previous_status,
        current_status=payload,
    )
    return payload


def execute_run_recovery(
    campaign_root: Path,
    run_id: str,
    *,
    action: str = "auto",
    execute: bool = False,
    detach_launch: bool = True,
    heartbeat_seconds: int = DEFAULT_HEARTBEAT_SECONDS,
    changed_file_verify_template: str | None = None,
) -> dict[str, Any]:
    campaign_root = campaign_root.resolve()
    manifest = _load_campaign_manifest(campaign_root)
    archon_root = Path(str(manifest["archonRoot"])).resolve()
    manifest_runs = _manifest_run_index(manifest)
    if run_id not in manifest_runs:
        raise KeyError(f"unknown run id: {run_id}")

    status_payload = collect_campaign_status(campaign_root, heartbeat_seconds=heartbeat_seconds)
    status_index = {
        item["runId"]: item for item in status_payload["runs"] if isinstance(item, dict) and isinstance(item.get("runId"), str)
    }
    if run_id not in status_index:
        raise KeyError(f"run id missing from campaign status: {run_id}")

    run_status = status_index[run_id]
    recommendation = run_status["recommendedRecovery"]
    assert isinstance(recommendation, dict)
    resolved_action = str(recommendation["action"]) if action == "auto" else action
    run_entry = manifest_runs[run_id]
    run_root = campaign_root / str(run_entry["runRoot"])
    workspace_root = run_root / "workspace"
    source_root = run_root / "source"
    control_root = campaign_root / str(run_entry["controlRoot"])
    launch_script = campaign_root / str(run_entry["teacherLaunchScript"])
    supervisor_cmd = _uv_run_command(
        archon_root,
        "autoarchon-supervised-cycle",
        "--workspace",
        str(workspace_root),
        "--source",
        str(source_root),
        "--recovery-only",
        "--skip-process-check",
    )
    if changed_file_verify_template:
        supervisor_cmd.extend(["--changed-file-verify-template", changed_file_verify_template])
    export_cmd = _uv_run_command(
        archon_root,
        "autoarchon-export-run-artifacts",
        "--run-root",
        str(run_root),
    )
    launch_cmd = ["bash", str(launch_script)]

    payload: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "campaignId": manifest.get("campaignId"),
        "runId": run_id,
        "status": run_status["status"],
        "requestedAction": action,
        "resolvedAction": resolved_action,
        "execute": execute,
        "detachLaunch": detach_launch,
        "recommendedRecovery": recommendation,
        "commands": [],
    }

    if resolved_action == "recovery_only":
        payload["commands"] = [_command_rendered(supervisor_cmd), _command_rendered(export_cmd)]
    elif resolved_action in {"launch_teacher", "relaunch_teacher"}:
        payload["commands"] = [_command_rendered(launch_cmd)]
    elif resolved_action in {"manual_rebuild", "none"}:
        payload["commands"] = [recommendation.get("command")] if recommendation.get("command") else []
    else:
        raise ValueError(f"unsupported recovery action: {resolved_action}")

    if not execute or resolved_action in {"none", "manual_rebuild"}:
        payload["executed"] = False
        payload["statusAfter"] = run_status["status"]
        return payload

    events_path = campaign_root / "events.jsonl"
    _append_jsonl(
        events_path,
        _event(
            {
                "campaignId": manifest.get("campaignId"),
                "runId": run_id,
                "requestedAction": action,
                "resolvedAction": resolved_action,
                "statusBefore": run_status["status"],
            },
            kind="recovery_planned",
        ),
    )
    if resolved_action == "recovery_only":
        supervisor_result = subprocess.run(
            supervisor_cmd,
            cwd=str(archon_root),
            capture_output=True,
            text=True,
            check=False,
        )
        payload["executed"] = True
        payload["supervisedCycle"] = {
            "returncode": supervisor_result.returncode,
            "stdoutTail": "\n".join(supervisor_result.stdout.splitlines()[-8:]),
            "stderrTail": "\n".join(supervisor_result.stderr.splitlines()[-8:]),
        }
        export_result = None
        if supervisor_result.returncode == 0:
            export_result = subprocess.run(
                export_cmd,
                cwd=str(archon_root),
                capture_output=True,
                text=True,
                check=False,
            )
            payload["artifactExport"] = {
                "returncode": export_result.returncode,
                "stdoutTail": "\n".join(export_result.stdout.splitlines()[-8:]),
                "stderrTail": "\n".join(export_result.stderr.splitlines()[-8:]),
            }
        _append_jsonl(
            events_path,
            _event(
                {
                    "campaignId": manifest.get("campaignId"),
                    "runId": run_id,
                    "action": resolved_action,
                    "returncode": supervisor_result.returncode,
                },
                kind="run_recovery_executed",
            ),
        )
    else:
        if detach_launch:
            stdout_log = control_root / "teacher-launch.stdout.log"
            stderr_log = control_root / "teacher-launch.stderr.log"
            launch_state_path = control_root / "teacher-launch-state.json"
            _write_teacher_launch_state(
                launch_state_path,
                active=True,
                phase="dispatch",
                launcher="campaign_recover.py",
            )
            stdout_handle = stdout_log.open("a", encoding="utf-8")
            stderr_handle = stderr_log.open("a", encoding="utf-8")
            try:
                proc = subprocess.Popen(
                    launch_cmd,
                    cwd=str(archon_root),
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    start_new_session=True,
                    text=True,
                )
            finally:
                stdout_handle.close()
                stderr_handle.close()
            _write_teacher_launch_state(
                launch_state_path,
                active=True,
                phase="dispatch",
                launcher="campaign_recover.py",
                pid=proc.pid,
            )
            payload["executed"] = True
            payload["teacherLaunch"] = {
                "pid": proc.pid,
                "detached": True,
                "stateFile": _relative(launch_state_path, start=campaign_root),
                "stdoutLog": _relative(stdout_log, start=campaign_root),
                "stderrLog": _relative(stderr_log, start=campaign_root),
            }
        else:
            launch_result = subprocess.run(
                launch_cmd,
                cwd=str(archon_root),
                capture_output=True,
                text=True,
                check=False,
            )
            payload["executed"] = True
            payload["teacherLaunch"] = {
                "detached": False,
                "returncode": launch_result.returncode,
                "stdoutTail": "\n".join(launch_result.stdout.splitlines()[-8:]),
                "stderrTail": "\n".join(launch_result.stderr.splitlines()[-8:]),
            }
        _append_jsonl(
            events_path,
            _event(
                {
                    "campaignId": manifest.get("campaignId"),
                    "runId": run_id,
                    "action": resolved_action,
                    "detached": detach_launch,
                },
                kind="run_recovery_executed",
            ),
        )

    refreshed = collect_campaign_status(campaign_root, heartbeat_seconds=heartbeat_seconds)
    refreshed_index = {
        item["runId"]: item for item in refreshed["runs"] if isinstance(item, dict) and isinstance(item.get("runId"), str)
    }
    payload["statusAfter"] = refreshed_index.get(run_id, {}).get("status")
    return payload


def build_campaign_compare_report(
    campaign_root: Path,
    *,
    heartbeat_seconds: int = DEFAULT_HEARTBEAT_SECONDS,
) -> dict[str, Any]:
    campaign_root = campaign_root.resolve()
    status_payload = collect_campaign_status(campaign_root, heartbeat_seconds=heartbeat_seconds)
    final_root = campaign_root / "reports" / "final"
    final_root.mkdir(parents=True, exist_ok=True)

    run_rows: list[dict[str, Any]] = []
    accepted_proof_count = 0
    accepted_blocker_count = 0
    unverified_artifact_count = 0
    pending_target_count = 0
    remaining_target_count = 0
    attention_target_count = 0
    rejected_target_count = 0
    changed_file_count = 0
    task_result_count = 0
    prewarm_plan_counts: dict[str, int] = {}
    prewarm_pending_runs = 0

    for run in status_payload["runs"]:
        if not isinstance(run, dict):
            continue
        accepted_proofs = run.get("acceptedProofs", [])
        accepted_blockers = run.get("acceptedBlockers", [])
        unverified_artifacts = run.get("unverifiedArtifacts", [])
        pending_targets = run.get("pendingTargets", [])
        remaining_targets = run.get("remainingTargets", [])
        attention_targets = run.get("attentionTargets", [])
        rejected_targets = run.get("rejectedTargets", [])
        changed_files = run.get("changedFiles", [])
        task_results = run.get("taskResults", [])
        accepted_proof_count += len(accepted_proofs) if isinstance(accepted_proofs, list) else 0
        accepted_blocker_count += len(accepted_blockers) if isinstance(accepted_blockers, list) else 0
        unverified_artifact_count += len(unverified_artifacts) if isinstance(unverified_artifacts, list) else 0
        pending_target_count += len(pending_targets) if isinstance(pending_targets, list) else 0
        remaining_target_count += len(remaining_targets) if isinstance(remaining_targets, list) else 0
        attention_target_count += len(attention_targets) if isinstance(attention_targets, list) else 0
        rejected_target_count += len(rejected_targets) if isinstance(rejected_targets, list) else 0
        changed_file_count += len(changed_files) if isinstance(changed_files, list) else 0
        task_result_count += len(task_results) if isinstance(task_results, list) else 0
        prewarm_plan = str(run.get("prewarmPlan") or "unknown")
        prewarm_plan_counts[prewarm_plan] = prewarm_plan_counts.get(prewarm_plan, 0) + 1
        if run.get("prewarmPending") is True:
            prewarm_pending_runs += 1
        configured_allowed_files = run.get("configuredAllowedFiles", [])

        row = {
            "runId": run.get("runId"),
            "status": run.get("status"),
            "recoveryClass": run.get("recoveryClass"),
            "retryAfter": run.get("retryAfter"),
            "lastLaunchExitCode": run.get("lastLaunchExitCode"),
            "acceptedProofCount": len(accepted_proofs) if isinstance(accepted_proofs, list) else 0,
            "acceptedBlockerCount": len(accepted_blockers) if isinstance(accepted_blockers, list) else 0,
            "unverifiedArtifactCount": len(unverified_artifacts) if isinstance(unverified_artifacts, list) else 0,
            "pendingTargetCount": len(pending_targets) if isinstance(pending_targets, list) else 0,
            "remainingTargetCount": len(remaining_targets) if isinstance(remaining_targets, list) else 0,
            "attentionTargetCount": len(attention_targets) if isinstance(attention_targets, list) else 0,
            "rejectedTargetCount": len(rejected_targets) if isinstance(rejected_targets, list) else 0,
            "changedFileCount": len(changed_files) if isinstance(changed_files, list) else 0,
            "taskResultCount": len(task_results) if isinstance(task_results, list) else 0,
            "configuredAllowedFileCount": len(configured_allowed_files) if isinstance(configured_allowed_files, list) else 0,
            "prewarmPlan": run.get("prewarmPlan"),
            "prewarmPending": run.get("prewarmPending"),
            "prewarmSummary": run.get("prewarmSummary"),
            "projectBuildReused": run.get("projectBuildReused"),
            "lakePackagesLinked": run.get("lakePackagesLinked"),
            "lakeBuildReusePath": run.get("lakeBuildReusePath"),
            "recommendedAction": (
                run.get("recommendedRecovery", {}).get("action")
                if isinstance(run.get("recommendedRecovery"), dict)
                else None
            ),
        }
        run_rows.append(row)

    run_timelines = _build_run_timelines(campaign_root, status_payload)
    timeline_index = {item["runId"]: item for item in run_timelines if isinstance(item.get("runId"), str)}
    for row in run_rows:
        run_id = row.get("runId")
        if not isinstance(run_id, str) or run_id not in timeline_index:
            row["timelinePath"] = None
            row["timelineEntryCount"] = 0
            continue
        timeline = timeline_index[run_id]
        row["timelinePath"] = f"runs/{run_id}/timeline.json"
        row["timelineEntryCount"] = int(timeline.get("eventCount", 0))
        _write_json(
            final_root / "runs" / run_id / "timeline.json",
            {
                "schemaVersion": SCHEMA_VERSION,
                "campaignId": status_payload.get("campaignId"),
                "runId": run_id,
                "generatedAt": _utc_now(),
                "status": row.get("status"),
                "recoveryClass": row.get("recoveryClass"),
                "retryAfter": row.get("retryAfter"),
                "lastLaunchExitCode": row.get("lastLaunchExitCode"),
                "recommendedAction": row.get("recommendedAction"),
                "entryCount": timeline.get("eventCount", 0),
                "entries": timeline.get("events", []),
            },
        )

    compare_report = {
        "schemaVersion": SCHEMA_VERSION,
        "campaignId": status_payload.get("campaignId"),
        "generatedAt": _utc_now(),
        "heartbeatSeconds": heartbeat_seconds,
        "runCounts": status_payload.get("counts", {}),
        "prewarmCounts": {
            "plans": prewarm_plan_counts,
            "pendingRuns": prewarm_pending_runs,
        },
        "targetCounts": {
            "acceptedProofs": accepted_proof_count,
            "acceptedBlockers": accepted_blocker_count,
            "unverifiedArtifacts": unverified_artifact_count,
            "pendingTargets": pending_target_count,
            "remainingTargets": remaining_target_count,
            "attentionTargets": attention_target_count,
            "rejectedTargets": rejected_target_count,
            "changedFiles": changed_file_count,
            "taskResults": task_result_count,
        },
        "runs": run_rows,
        "runTimelines": run_timelines,
    }
    _write_json(final_root / "compare-report.json", compare_report)

    lines = [
        "# Campaign Compare Report",
        "",
        f"- Campaign: `{status_payload.get('campaignId')}`",
        f"- Generated at: `{compare_report['generatedAt']}`",
        f"- Heartbeat window: `{heartbeat_seconds}s`",
        "",
        "## Summary",
        "",
        f"- Run counts: `{json.dumps(status_payload.get('counts', {}), sort_keys=True)}`",
        f"- Prewarm plans: `{json.dumps(prewarm_plan_counts, sort_keys=True)}`; pending runs: `{prewarm_pending_runs}`",
        (
            "- Target counts: "
            f"`accepted_proofs={accepted_proof_count}, accepted_blockers={accepted_blocker_count}, "
            f"unverified_artifacts={unverified_artifact_count}, pending_targets={pending_target_count}, "
            f"attention_targets={attention_target_count}, rejected_targets={rejected_target_count}`"
        ),
        "",
        "## Runs",
        "",
        "| run | status | class | retry_after | launch_exit | proofs | blockers | unverified | pending | attention | rejected | prewarm | recommended | timeline |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |",
    ]
    for row in run_rows:
        lines.append(
            "| {runId} | {status} | {recoveryClass} | {retryAfter} | {lastLaunchExitCode} | {acceptedProofCount} | "
            "{acceptedBlockerCount} | {unverifiedArtifactCount} | {pendingTargetCount} | {attentionTargetCount} | "
            "{rejectedTargetCount} | {prewarmSummary} | {recommendedAction} | {timelineEntryCount} |".format(
                **row
            )
        )
    lines.extend(_timeline_markdown_lines(run_timelines))
    _write_text(final_root / "compare-report.md", "\n".join(lines) + "\n")
    return compare_report


def _copy_if_exists(source: Path, destination: Path) -> None:
    if not source.exists():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _lesson_record(
    *,
    campaign_id: str,
    run_id: str | None,
    theorem_id: str | None,
    stage: str,
    category: str,
    summary: str,
    evidence_paths: list[str],
    action_taken: str,
    outcome: str,
    accepted_state: str,
    recommended_action: str | None = None,
    source_status: str | None = None,
    signal_tags: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "campaign_id": campaign_id,
        "run_id": run_id,
        "theorem_id": theorem_id,
        "stage": stage,
        "category": category,
        "summary": summary,
        "evidence_paths": evidence_paths,
        "action_taken": action_taken,
        "outcome": outcome,
        "accepted_state": accepted_state,
        "recommended_action": recommended_action or action_taken,
        "source_status": source_status or outcome,
        "signal_tags": signal_tags if isinstance(signal_tags, list) else [stage, category, accepted_state],
        "timestamp": _utc_now(),
    }


def _write_lesson_records(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True) + "\n")


def _lesson_theorem_id(
    artifacts_root: Path,
    lesson_payload: Mapping[str, Any],
    run_status: Mapping[str, Any],
) -> str | None:
    validation_files = lesson_payload.get("validationFiles")
    if isinstance(validation_files, list):
        for name in validation_files:
            if not isinstance(name, str):
                continue
            validation_payload = _read_json(artifacts_root / "validation" / name)
            rel_path = validation_payload.get("relPath") if isinstance(validation_payload, Mapping) else None
            if isinstance(rel_path, str) and rel_path:
                return rel_path
    allowed_files = lesson_payload.get("allowedFiles")
    if isinstance(allowed_files, list):
        for rel_path in allowed_files:
            if isinstance(rel_path, str) and rel_path:
                return rel_path
    for key in ("acceptedProofs", "acceptedBlockers"):
        values = run_status.get(key)
        if isinstance(values, list):
            for rel_path in values:
                if isinstance(rel_path, str) and rel_path:
                    return rel_path
    return None


def _collect_run_lesson_records(
    *,
    campaign_id: str,
    run_id: str,
    artifacts_root: Path,
    run_status: Mapping[str, Any],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    accepted_state = str(run_status.get("status") or "unknown")
    for lesson_name in _list_file_names(artifacts_root / "lessons", "*"):
        lesson_payload = _read_json(artifacts_root / "lessons" / lesson_name)
        if not isinstance(lesson_payload, Mapping):
            continue
        theorem_id = _lesson_theorem_id(artifacts_root, lesson_payload, run_status)
        evidence_base = [f"runs/{run_id}/artifacts/lessons/{lesson_name}"]
        action_taken = str(lesson_payload.get("recommendedAction") or "record_lesson")
        outcome = str(lesson_payload.get("status") or accepted_state)
        lesson_signals = lesson_payload.get("signals")
        entries = lesson_payload.get("lessons")
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            category = entry.get("category")
            summary = entry.get("summary")
            if not isinstance(category, str) or not isinstance(summary, str):
                continue
            evidence = []
            raw_evidence = entry.get("evidence")
            if isinstance(raw_evidence, list):
                evidence = [str(item) for item in raw_evidence if isinstance(item, str)]
            records.append(
                _lesson_record(
                    campaign_id=campaign_id,
                    run_id=run_id,
                    theorem_id=theorem_id,
                    stage="supervised_cycle",
                    category=category,
                    summary=summary,
                    evidence_paths=evidence_base + evidence,
                    action_taken=action_taken,
                    outcome=outcome,
                    accepted_state=accepted_state,
                    recommended_action=action_taken,
                    source_status=outcome,
                    signal_tags=[
                        tag
                        for tag in (
                            [str(item) for item in lesson_signals if isinstance(item, str)]
                            if isinstance(lesson_signals, list)
                            else []
                        )
                        + [category, "supervised_cycle", accepted_state]
                        if tag
                    ],
                )
            )
    return records


def _accepted_proof_records(
    *,
    campaign_id: str,
    run_id: str,
    run_status: Mapping[str, Any],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    accepted_proofs = run_status.get("acceptedProofs")
    if not isinstance(accepted_proofs, list):
        return records
    for rel_path in accepted_proofs:
        if not isinstance(rel_path, str):
            continue
        validation_name = rel_path.replace("/", "_") + ".json"
        records.append(
            _lesson_record(
                campaign_id=campaign_id,
                run_id=run_id,
                theorem_id=rel_path,
                stage="finalize",
                category="accepted_proof",
                summary="Validation-backed proof exported to reports/final/proofs.",
                evidence_paths=[
                    f"runs/{run_id}/artifacts/proofs/{rel_path}",
                    f"runs/{run_id}/artifacts/validation/{validation_name}",
                ],
                action_taken="export_final_proof",
                outcome="accepted",
                accepted_state="accepted",
            )
        )
    return records


def _accepted_blocker_records(
    *,
    campaign_id: str,
    run_id: str,
    artifacts_root: Path,
    run_status: Mapping[str, Any],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    accepted_blockers = run_status.get("acceptedBlockers")
    if not isinstance(accepted_blockers, list):
        return records
    for rel_path in accepted_blockers:
        if not isinstance(rel_path, str):
            continue
        validation_name = rel_path.replace("/", "_") + ".json"
        validation_payload = _read_json(artifacts_root / "validation" / validation_name)
        evidence_paths = [f"runs/{run_id}/artifacts/validation/{validation_name}"]
        if isinstance(validation_payload, Mapping):
            blocker_notes = validation_payload.get("blockerNotes")
            if isinstance(blocker_notes, list):
                for note_name in blocker_notes:
                    if isinstance(note_name, str):
                        evidence_paths.append(f"runs/{run_id}/artifacts/task-results/{note_name}")
        records.append(
            _lesson_record(
                campaign_id=campaign_id,
                run_id=run_id,
                theorem_id=rel_path,
                stage="finalize",
                category="accepted_blocker",
                summary="Validation-backed blocker note exported to reports/final/blockers.",
                evidence_paths=evidence_paths,
                action_taken="export_final_blocker",
                outcome="accepted",
                accepted_state="blocked",
            )
        )
    return records


def _watchdog_postmortem_records(
    *,
    campaign_id: str,
    watchdog_state: Mapping[str, Any] | None,
    incident_tags: list[str],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if "provider_transport_instability" in incident_tags:
        records.append(
            _lesson_record(
                campaign_id=campaign_id,
                run_id=None,
                theorem_id=None,
                stage="watchdog",
                category="provider_transport",
                summary="Provider or transport instability triggered cooldown or archive handling.",
                evidence_paths=[
                    "reports/postmortem/orchestrator-watchdog.snapshot.json",
                    "reports/postmortem/watchdog-log.tail.txt",
                ],
                action_taken="apply_provider_cooldown_and_archive",
                outcome="archived_postmortem",
                accepted_state="postmortem",
            )
        )
    restart_count = watchdog_state.get("restartCount") if isinstance(watchdog_state, Mapping) else None
    if isinstance(restart_count, int) and restart_count > 0:
        records.append(
            _lesson_record(
                campaign_id=campaign_id,
                run_id=None,
                theorem_id=None,
                stage="watchdog",
                category="watchdog_relaunch",
                summary="The watchdog restarted or relaunched the outer owner while preserving campaign state.",
                evidence_paths=[
                    "reports/postmortem/orchestrator-watchdog.snapshot.json",
                    "events.jsonl",
                ],
                action_taken="restart_orchestrator_with_budget",
                outcome="archived_postmortem",
                accepted_state="postmortem",
            )
        )
    return records


def finalize_campaign(
    campaign_root: Path,
    *,
    heartbeat_seconds: int = DEFAULT_HEARTBEAT_SECONDS,
    prune_workspace_lake: bool = False,
    prune_broken_prewarm: bool = False,
) -> dict[str, Any]:
    campaign_root = campaign_root.resolve()
    manifest = _read_json(campaign_root / "CAMPAIGN_MANIFEST.json")
    if manifest is None:
        raise FileNotFoundError(f"missing CAMPAIGN_MANIFEST.json under {campaign_root}")

    runs = manifest.get("runs")
    if not isinstance(runs, list):
        raise ValueError("CAMPAIGN_MANIFEST.json must contain a runs list")

    for run in runs:
        if not isinstance(run, dict):
            continue
        export_run_artifacts(campaign_root / str(run["runRoot"]))

    status_payload = collect_campaign_status(campaign_root, heartbeat_seconds=heartbeat_seconds)
    final_root = campaign_root / "reports" / "final"
    proofs_root = final_root / "proofs"
    diffs_root = final_root / "diffs"
    blockers_root = final_root / "blockers"
    validation_root = final_root / "validation"
    lessons_root = final_root / "lessons"
    supervisor_root = final_root / "supervisor"
    runs_root = final_root / "runs"
    final_root.mkdir(parents=True, exist_ok=True)

    copied_proofs: list[str] = []
    copied_blockers: list[str] = []
    lesson_records: list[dict[str, Any]] = []
    run_reports: list[dict[str, Any]] = []
    status_by_run = {item["runId"]: item for item in status_payload["runs"] if isinstance(item, dict)}
    for run in runs:
        if not isinstance(run, dict):
            continue
        run_id = str(run["id"])
        run_root = campaign_root / str(run["runRoot"])
        artifacts_root = run_root / "artifacts"
        run_status = status_by_run.get(run_id, {})

        for rel_path in run_status.get("acceptedProofs", []):
            source_proof = artifacts_root / "proofs" / rel_path
            source_diff = artifacts_root / "diffs" / f"{rel_path}.diff"
            _copy_if_exists(source_proof, proofs_root / run_id / rel_path)
            _copy_if_exists(source_diff, diffs_root / run_id / f"{rel_path}.diff")
            validation_name = rel_path.replace("/", "_") + ".json"
            _copy_if_exists(artifacts_root / "validation" / validation_name, validation_root / run_id / validation_name)
            copied_proofs.append(f"{run_id}:{rel_path}")

        for rel_path in run_status.get("acceptedBlockers", []):
            validation_name = rel_path.replace("/", "_") + ".json"
            validation_payload = _read_json(artifacts_root / "validation" / validation_name)
            if validation_payload is None:
                continue
            for note_name in validation_payload.get("blockerNotes", []):
                if not isinstance(note_name, str):
                    continue
                _copy_if_exists(artifacts_root / "task-results" / note_name, blockers_root / run_id / note_name)
                copied_blockers.append(f"{run_id}:{note_name}")
            _copy_if_exists(artifacts_root / "validation" / validation_name, validation_root / run_id / validation_name)

        for lesson_name in _list_file_names(artifacts_root / "lessons", "*"):
            _copy_if_exists(artifacts_root / "lessons" / lesson_name, lessons_root / run_id / lesson_name)
        for supervisor_name in _list_file_names(artifacts_root / "supervisor", "*"):
            _copy_if_exists(artifacts_root / "supervisor" / supervisor_name, supervisor_root / run_id / supervisor_name)

        _copy_if_exists(artifacts_root / "artifact-index.json", runs_root / run_id / "artifact-index.json")
        _copy_if_exists(run_root / "RUN_MANIFEST.json", runs_root / run_id / "RUN_MANIFEST.json")
        run_report = {
            "runId": run_id,
            "status": run_status.get("status"),
            "acceptedProofs": run_status.get("acceptedProofs", []),
            "acceptedBlockers": run_status.get("acceptedBlockers", []),
            "unverifiedArtifacts": run_status.get("unverifiedArtifacts", []),
            "rejectedTargets": run_status.get("rejectedTargets", []),
        }
        run_reports.append(run_report)
        _write_json(runs_root / run_id / "run-summary.json", run_report)
        lesson_records.extend(
            _collect_run_lesson_records(
                campaign_id=str(manifest.get("campaignId")),
                run_id=run_id,
                artifacts_root=artifacts_root,
                run_status=run_status,
            )
        )
        lesson_records.extend(
            _accepted_proof_records(
                campaign_id=str(manifest.get("campaignId")),
                run_id=run_id,
                run_status=run_status,
            )
        )
        lesson_records.extend(
            _accepted_blocker_records(
                campaign_id=str(manifest.get("campaignId")),
                run_id=run_id,
                artifacts_root=artifacts_root,
                run_status=run_status,
            )
        )

    summary = {
        "schemaVersion": SCHEMA_VERSION,
        "campaignId": manifest.get("campaignId"),
        "finalizedAt": _utc_now(),
        "counts": status_payload["counts"],
        "acceptedProofs": copied_proofs,
        "acceptedBlockers": copied_blockers,
        "runs": run_reports,
    }
    _write_json(final_root / "final-summary.json", summary)
    final_lessons_root = final_root / "lessons"
    final_records_path = final_lessons_root / "lesson-records.jsonl"
    _write_lesson_records(final_records_path, lesson_records)
    lesson_cluster_paths = write_lesson_cluster_artifacts(
        final_lessons_root,
        records=lesson_records,
        source_paths=[str(final_records_path)],
    )
    compare_report = build_campaign_compare_report(campaign_root, heartbeat_seconds=heartbeat_seconds)
    _append_jsonl(
        campaign_root / "events.jsonl",
        _event(
            {
                "campaignId": manifest.get("campaignId"),
                "acceptedProofCount": len(copied_proofs),
                "acceptedBlockerCount": len(copied_blockers),
            },
            kind="campaign_finalized",
        ),
    )
    summary["compareReport"] = {
        "runCounts": compare_report["runCounts"],
        "targetCounts": compare_report["targetCounts"],
    }
    summary["lessonRecordsPath"] = str(final_records_path)
    summary["lessonClusters"] = lesson_cluster_paths
    cache_prune = _maybe_prune_campaign_storage(
        campaign_root,
        prune_workspace_lake=prune_workspace_lake,
        prune_broken_prewarm=prune_broken_prewarm,
    )
    if cache_prune is not None:
        summary["cachePrune"] = cache_prune
    _write_json(final_root / "final-summary.json", summary)
    return summary
