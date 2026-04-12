from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from archonlib.run_workspace import export_run_artifacts, create_isolated_run
from archonlib.supervisor import collect_changed_files, latest_iteration_meta, read_allowed_files


SCHEMA_VERSION = 1
DEFAULT_HEARTBEAT_SECONDS = 900
LAUNCH_GRACE_SECONDS = 120
IGNORED_SHARD_DIRS = {".archon", ".git", ".lake", "build", "lake-packages", "__pycache__"}
TERMINAL_RUN_STATUSES = {"accepted", "blocked", "contaminated"}


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


def _relative(path: Path, *, start: Path) -> str:
    return path.resolve().relative_to(start.resolve()).as_posix()


def _event(payload: dict[str, Any], *, kind: str) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "timestamp": _utc_now(),
        "event": kind,
        **payload,
    }


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
    return {
        "id": run_id.strip(),
        "objectiveRegex": objective_regex,
        "objectiveLimit": objective_limit,
        "scopeHint": scope_hint.strip() if isinstance(scope_hint, str) else None,
    }


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
) -> str:
    supervised_cycle_cmd = _uv_run_rendered(
        archon_root,
        "autoarchon-supervised-cycle",
        "--workspace",
        str(workspace_root),
        "--source",
        str(source_root),
        "--plan-timeout-seconds",
        str(plan_timeout_seconds),
        "--prover-timeout-seconds",
        str(prover_timeout_seconds),
        "--prover-idle-seconds",
        str(prover_idle_seconds),
        "--no-review",
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
            "",
            "Mission:",
            "- keep theorem headers faithful to source",
            "- supervise repeated plan/prover cycles until the scoped objectives are solved, or a blocker is validated, or an external stop condition is hit",
            f"- prefer {supervised_cycle_cmd}",
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
    prompt_path: Path,
) -> str:
    archon_rendered = shlex.quote(str(archon_root))
    workspace_rendered = shlex.quote(str(workspace_root))
    prompt_rendered = shlex.quote(str(prompt_path))
    control_rendered = shlex.quote(str(run_root / "control"))
    init_cmd = (
        f"{shlex.quote(str(archon_root / 'init.sh'))} --skip-mcp "
        f"--objective-limit {objective_limit} --objective-regex {shlex.quote(objective_regex)} {workspace_rendered}"
    )
    prewarm_cmd = _command_rendered(
        _uv_run_command(
            archon_root,
            "autoarchon-prewarm-project",
            str(workspace_root),
        )
    )
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
            'LAUNCH_STATE_FILE="${CONTROL_ROOT}/teacher-launch-state.json"',
            "",
            "write_launch_state() {",
            '  python3 - "$LAUNCH_STATE_FILE" "$1" "$2" <<'"'"'PY'"'"'',
            "import json",
            "import sys",
            "from datetime import datetime, timezone",
            "from pathlib import Path",
            "",
            "path = Path(sys.argv[1])",
            "phase = sys.argv[2]",
            'active = sys.argv[3].lower() == "true"',
            "payload = {",
            f'    "schemaVersion": {SCHEMA_VERSION},',
            '    "active": active,',
            '    "phase": phase,',
            '    "updatedAt": datetime.now(timezone.utc).isoformat(),',
            "}",
            'path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\\n", encoding="utf-8")',
            "PY",
            "}",
            "",
            'write_launch_state "bootstrap" "true"',
            "",
            'if [[ ! -f "${WORKSPACE_ROOT}/.archon/RUN_SCOPE.md" ]]; then',
            f"  {prewarm_cmd}",
            f"  {init_cmd}",
            "fi",
            "",
            'write_launch_state "codex_exec" "true"',
            "",
            'export ARCHON_CODEX_READY_RETRIES="${ARCHON_CODEX_READY_RETRIES:-6}"',
            'export ARCHON_CODEX_READY_RETRY_DELAY_SECONDS="${ARCHON_CODEX_READY_RETRY_DELAY_SECONDS:-10}"',
            'cd "${ARCHON_ROOT}"',
            "exec codex exec \\",
            "  --skip-git-repo-check \\",
            "  --sandbox danger-full-access \\",
            "  -c approval_policy=never \\",
            f"  -c model_reasoning_effort={shlex.quote(teacher_reasoning_effort)} \\",
            f"  --model {shlex.quote(teacher_model)} \\",
            '  - < "${PROMPT_FILE}"',
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


def _is_running_signal(run_root: Path, *, heartbeat_seconds: int) -> tuple[bool, float | None]:
    lease = _read_json(run_root / "workspace" / ".archon" / "supervisor" / "run-lease.json")
    if lease is not None:
        heartbeat_age = _heartbeat_age_from_iso(lease.get("lastHeartbeatAt"))
        if lease.get("active") is True:
            if heartbeat_age is None:
                return True, None
            return heartbeat_age <= heartbeat_seconds, heartbeat_age
        if lease.get("active") is False:
            # A completed or explicitly inactive supervisor lease is authoritative.
            # Do not resurrect the run as "running" from fresh log mtimes or a stale
            # pre-lease launch marker.
            return False, heartbeat_age

    launch_state = _read_json(run_root / "control" / "teacher-launch-state.json")
    if launch_state is not None and launch_state.get("active") is True:
        heartbeat_age = _heartbeat_age_from_iso(launch_state.get("updatedAt"))
        if heartbeat_age is None:
            return True, None
        if heartbeat_age <= min(heartbeat_seconds, LAUNCH_GRACE_SECONDS):
            return True, heartbeat_age

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
    reports_root = campaign_root / "reports" / "final"
    runs_root.mkdir(parents=True, exist_ok=True)
    reports_root.mkdir(parents=True, exist_ok=True)

    manifest_runs: list[dict[str, Any]] = []
    events_path = campaign_root / "events.jsonl"
    for spec in normalized_specs:
        run_root = runs_root / spec["id"]
        create_isolated_run(
            source_root,
            run_root,
            reuse_lake_from=reuse_lake_from,
            scope_hint=spec["scopeHint"],
        )

        control_root = run_root / "control"
        prompt_path = control_root / "teacher-prompt.txt"
        launch_path = control_root / "launch-teacher.sh"
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
        )
        _write_text(prompt_path, prompt_text)
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
                prompt_path=prompt_path,
            ),
        )
        launch_path.chmod(0o755)

        run_payload = {
            "id": spec["id"],
            "scopeHint": spec["scopeHint"],
            "objectiveRegex": spec["objectiveRegex"],
            "objectiveLimit": spec["objectiveLimit"],
            "runRoot": _relative(run_root, start=campaign_root),
            "sourceRoot": _relative(run_root / "source", start=campaign_root),
            "workspaceRoot": _relative(run_root / "workspace", start=campaign_root),
            "artifactsRoot": _relative(run_root / "artifacts", start=campaign_root),
            "controlRoot": _relative(control_root, start=campaign_root),
            "teacherPrompt": _relative(prompt_path, start=campaign_root),
            "teacherLaunchScript": _relative(launch_path, start=campaign_root),
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
    return manifest


def collect_campaign_status(campaign_root: Path, *, heartbeat_seconds: int = DEFAULT_HEARTBEAT_SECONDS) -> dict[str, Any]:
    campaign_root = campaign_root.resolve()
    manifest = _load_campaign_manifest(campaign_root)
    runs = manifest.get("runs")
    assert isinstance(runs, list)
    archon_root = Path(str(manifest["archonRoot"]))

    run_summaries: list[dict[str, Any]] = []
    counts: dict[str, int] = {}

    for run in runs:
        if not isinstance(run, dict):
            continue
        run_id = str(run["id"])
        run_root = campaign_root / str(run["runRoot"])
        workspace_root = run_root / "workspace"
        source_root = run_root / "source"
        artifacts_root = run_root / "artifacts"
        validation_root = workspace_root / ".archon" / "validation"
        task_results_root = workspace_root / ".archon" / "task_results"
        supervisor_root = workspace_root / ".archon" / "supervisor"
        control_root = run_root / "control"

        allowed_files = read_allowed_files(workspace_root)
        changed_files = collect_changed_files(source_root, workspace_root, allowed_files=allowed_files or None)
        validation_payloads = _load_validation_payloads(validation_root)
        validation_summary = _validation_summary(validation_payloads)
        validation_paths = set(validation_summary["validationByPath"])
        task_results = _list_file_names(task_results_root, "*.md")
        running_signal, heartbeat_age_seconds = _is_running_signal(run_root, heartbeat_seconds=heartbeat_seconds)
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
            validation_payloads=validation_payloads,
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
        artifact_index = _read_json(artifacts_root / "artifact-index.json")

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
            "changedFiles": changed_files,
            "taskResults": task_results,
            "acceptedProofs": validation_summary["acceptedProofs"],
            "acceptedBlockers": validation_summary["acceptedBlockers"],
            "pendingTargets": validation_summary["pendingTargets"],
            "attentionTargets": validation_summary["attentionTargets"],
            "rejectedTargets": validation_summary["rejectedTargets"],
            "unverifiedArtifacts": unverified_paths,
            "artifactIndexPresent": artifact_index is not None,
            "heartbeatAgeSeconds": heartbeat_age_seconds,
            "runningSignal": running_signal,
            "launchStatePresent": has_launch_state,
            "latestIteration": latest_iter_name,
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
    attention_target_count = 0
    rejected_target_count = 0
    changed_file_count = 0
    task_result_count = 0

    for run in status_payload["runs"]:
        if not isinstance(run, dict):
            continue
        accepted_proofs = run.get("acceptedProofs", [])
        accepted_blockers = run.get("acceptedBlockers", [])
        unverified_artifacts = run.get("unverifiedArtifacts", [])
        pending_targets = run.get("pendingTargets", [])
        attention_targets = run.get("attentionTargets", [])
        rejected_targets = run.get("rejectedTargets", [])
        changed_files = run.get("changedFiles", [])
        task_results = run.get("taskResults", [])
        accepted_proof_count += len(accepted_proofs) if isinstance(accepted_proofs, list) else 0
        accepted_blocker_count += len(accepted_blockers) if isinstance(accepted_blockers, list) else 0
        unverified_artifact_count += len(unverified_artifacts) if isinstance(unverified_artifacts, list) else 0
        pending_target_count += len(pending_targets) if isinstance(pending_targets, list) else 0
        attention_target_count += len(attention_targets) if isinstance(attention_targets, list) else 0
        rejected_target_count += len(rejected_targets) if isinstance(rejected_targets, list) else 0
        changed_file_count += len(changed_files) if isinstance(changed_files, list) else 0
        task_result_count += len(task_results) if isinstance(task_results, list) else 0

        row = {
            "runId": run.get("runId"),
            "status": run.get("status"),
            "acceptedProofCount": len(accepted_proofs) if isinstance(accepted_proofs, list) else 0,
            "acceptedBlockerCount": len(accepted_blockers) if isinstance(accepted_blockers, list) else 0,
            "unverifiedArtifactCount": len(unverified_artifacts) if isinstance(unverified_artifacts, list) else 0,
            "pendingTargetCount": len(pending_targets) if isinstance(pending_targets, list) else 0,
            "attentionTargetCount": len(attention_targets) if isinstance(attention_targets, list) else 0,
            "rejectedTargetCount": len(rejected_targets) if isinstance(rejected_targets, list) else 0,
            "changedFileCount": len(changed_files) if isinstance(changed_files, list) else 0,
            "taskResultCount": len(task_results) if isinstance(task_results, list) else 0,
            "recommendedAction": (
                run.get("recommendedRecovery", {}).get("action")
                if isinstance(run.get("recommendedRecovery"), dict)
                else None
            ),
        }
        run_rows.append(row)

    compare_report = {
        "schemaVersion": SCHEMA_VERSION,
        "campaignId": status_payload.get("campaignId"),
        "generatedAt": _utc_now(),
        "heartbeatSeconds": heartbeat_seconds,
        "runCounts": status_payload.get("counts", {}),
        "targetCounts": {
            "acceptedProofs": accepted_proof_count,
            "acceptedBlockers": accepted_blocker_count,
            "unverifiedArtifacts": unverified_artifact_count,
            "pendingTargets": pending_target_count,
            "attentionTargets": attention_target_count,
            "rejectedTargets": rejected_target_count,
            "changedFiles": changed_file_count,
            "taskResults": task_result_count,
        },
        "runs": run_rows,
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
        (
            "- Target counts: "
            f"`accepted_proofs={accepted_proof_count}, accepted_blockers={accepted_blocker_count}, "
            f"unverified_artifacts={unverified_artifact_count}, pending_targets={pending_target_count}, "
            f"attention_targets={attention_target_count}, rejected_targets={rejected_target_count}`"
        ),
        "",
        "## Runs",
        "",
        "| run | status | proofs | blockers | unverified | pending | attention | rejected | recommended |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in run_rows:
        lines.append(
            "| {runId} | {status} | {acceptedProofCount} | {acceptedBlockerCount} | {unverifiedArtifactCount} | "
            "{pendingTargetCount} | {attentionTargetCount} | {rejectedTargetCount} | {recommendedAction} |".format(
                **row
            )
        )
    _write_text(final_root / "compare-report.md", "\n".join(lines) + "\n")
    return compare_report


def _copy_if_exists(source: Path, destination: Path) -> None:
    if not source.exists():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def finalize_campaign(campaign_root: Path, *, heartbeat_seconds: int = DEFAULT_HEARTBEAT_SECONDS) -> dict[str, Any]:
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
    _write_json(final_root / "final-summary.json", summary)
    return summary
