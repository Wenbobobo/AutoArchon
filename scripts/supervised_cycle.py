#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from archonlib.supervisor import (
    collect_changed_files,
    collect_header_drifts,
    collect_meta_prover_errors,
    latest_iteration_meta,
    read_allowed_files,
)
from archonlib.lessons import write_lesson_artifact
from archonlib.project_state import build_task_pending_markdown, objective_for_file, stage_markdown
from archonlib.validation import write_validation_artifacts


INFORMAL_NOTE_PATTERN = re.compile(r"\.archon/informal/[A-Za-z0-9._/\-]+\.md")
OBJECTIVE_REL_PATH_PATTERN = re.compile(r"(?:\*\*|`)([^*`\n]+\.lean)(?:\*\*|`)")
LEASE_SCHEMA_VERSION = 1
TERMINAL_LEASE_FIELDS = (
    "completedAt",
    "finalStatus",
    "lessonFile",
    "loopExitCode",
    "recoveryEvent",
    "validationFiles",
)


def _env_float(name: str, default: float | None = None) -> float | None:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return float(raw)


def _env_int(name: str, default: int | None = None) -> int | None:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one supervised Archon cycle and record policy results.")
    parser.add_argument("--workspace", required=True, help="Workspace root passed to Archon")
    parser.add_argument("--source", required=True, help="Immutable source root for header fidelity checks")
    parser.add_argument(
        "--archon-loop",
        default=str(ROOT / "archon-loop.sh"),
        help="Path to archon-loop.sh or a compatible wrapper",
    )
    parser.add_argument("--max-iterations", type=int, default=1, help="Iterations to pass through to archon-loop.sh")
    parser.add_argument("--max-parallel", type=int, default=4, help="Parallel prover limit")
    parser.add_argument("--plan-timeout-seconds", type=int, help="Set ARCHON_PLAN_TIMEOUT_SECONDS for this cycle")
    parser.add_argument("--prover-timeout-seconds", type=int, help="Set ARCHON_PROVER_TIMEOUT_SECONDS for this cycle")
    parser.add_argument("--review-timeout-seconds", type=int, help="Set ARCHON_REVIEW_TIMEOUT_SECONDS for this cycle")
    parser.add_argument("--skip-process-check", action="store_true", help="Skip the pre-cycle ps scan")
    parser.add_argument(
        "--recovery-only",
        action="store_true",
        help="Skip archon-loop and close out the current workspace state into validation, lessons, and supervisor artifacts",
    )
    parser.add_argument("--dry-run", action="store_true", help="Pass --dry-run through to archon-loop.sh")
    parser.add_argument("--no-review", action="store_true", help="Pass --no-review through to archon-loop.sh")
    parser.add_argument(
        "--prover-idle-seconds",
        type=float,
        default=_env_float("ARCHON_SUPERVISOR_PROVER_IDLE_SECONDS"),
        help="Kill the loop if prover activity stays idle for this many seconds",
    )
    parser.add_argument(
        "--monitor-poll-seconds",
        type=float,
        default=_env_float("ARCHON_SUPERVISOR_MONITOR_POLL_SECONDS", 5.0) or 5.0,
        help="Polling interval for supervisor runtime monitoring",
    )
    parser.add_argument(
        "--changed-file-verify-template",
        default=os.environ.get("ARCHON_SUPERVISOR_VERIFY_TEMPLATE"),
        help="Optional shell template used to verify changed files after an idle timeout; use {file} as placeholder",
    )
    parser.add_argument(
        "--tail-scope-objective-threshold",
        type=int,
        default=_env_int("ARCHON_SUPERVISOR_TAIL_SCOPE_OBJECTIVE_THRESHOLD", 0) or 0,
        help="When the current objective list has at most this many files, apply tail-scope runtime overrides",
    )
    parser.add_argument(
        "--tail-scope-prover-timeout-seconds",
        type=int,
        default=_env_int("ARCHON_SUPERVISOR_TAIL_SCOPE_PROVER_TIMEOUT_SECONDS"),
        help="Optional prover timeout override used when the current objective count is within the tail-scope threshold",
    )
    return parser.parse_args()


def _append_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(content)


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _lease_path(workspace: Path) -> Path:
    return workspace / ".archon" / "supervisor" / "run-lease.json"


def _read_json(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _update_lease(
    lease_path: Path,
    *,
    workspace: Path,
    source: Path,
    fields: dict[str, object],
    clear_fields: tuple[str, ...] = (),
) -> dict[str, object]:
    payload = _read_json(lease_path) or {}
    for key in clear_fields:
        payload.pop(key, None)
    payload.update(
        {
            "schemaVersion": LEASE_SCHEMA_VERSION,
            "workspace": str(workspace),
            "source": str(source),
            "updatedAt": _now_iso(),
            **fields,
        }
    )
    _write_text(lease_path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return payload


def _lease_conflicts(skip: bool, lease_path: Path, *, current_pid: int) -> list[dict[str, object]]:
    if skip:
        return []
    lease = _read_json(lease_path)
    if lease is None or lease.get("active") is not True:
        return []

    events: list[dict[str, object]] = []
    supervisor_pid = _coerce_pid(lease.get("supervisorPid"))
    loop_pid = _coerce_pid(lease.get("loopPid"))
    if supervisor_pid is not None and supervisor_pid != current_pid and _pid_is_live(supervisor_pid):
        return [
            {
                "event": "active_supervisor_lease",
                "supervisorPid": supervisor_pid,
                "loopPid": loop_pid,
                "workspace": lease.get("workspace"),
                "updatedAt": lease.get("updatedAt"),
            }
        ]
    if loop_pid is not None and _pid_is_live(loop_pid):
        events.append(
            {
                "event": "orphaned_loop_lease",
                "supervisorPid": supervisor_pid,
                "loopPid": loop_pid,
                "workspace": lease.get("workspace"),
                "updatedAt": lease.get("updatedAt"),
            }
        )
    return events


def _current_objective_rel_paths(workspace: Path, *, allowed_files: list[str]) -> list[str]:
    progress_path = workspace / ".archon" / "PROGRESS.md"
    if not progress_path.exists():
        return []

    allowed = set(allowed_files)
    found_section = False
    rel_paths: list[str] = []
    for raw_line in progress_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.rstrip()
        if not found_section:
            if line.strip() == "## Current Objectives":
                found_section = True
            continue
        if line.startswith("## "):
            break
        if not re.match(r"^[ \t]*[0-9]+\.[ \t]+", line):
            continue
        for match in OBJECTIVE_REL_PATH_PATTERN.findall(line):
            rel_path = match.strip()
            if allowed and rel_path not in allowed:
                continue
            if rel_path not in rel_paths:
                rel_paths.append(rel_path)
    return rel_paths


def _resolve_runtime_overrides(
    args: argparse.Namespace,
    workspace: Path,
    *,
    allowed_files: list[str],
) -> dict[str, object]:
    overrides: dict[str, object] = {}
    objective_rel_paths = _current_objective_rel_paths(workspace, allowed_files=allowed_files)
    objective_count = len(objective_rel_paths)
    overrides["objectiveCount"] = objective_count
    overrides["objectiveFiles"] = objective_rel_paths

    if (
        args.tail_scope_objective_threshold > 0
        and args.tail_scope_prover_timeout_seconds is not None
        and 0 < objective_count <= args.tail_scope_objective_threshold
    ):
        current_timeout = args.prover_timeout_seconds
        tail_timeout = args.tail_scope_prover_timeout_seconds
        if current_timeout is None or tail_timeout > current_timeout:
            overrides["proverTimeoutSeconds"] = tail_timeout
            overrides["tailScopeApplied"] = True

    return overrides


def _build_loop_command(
    args: argparse.Namespace,
    runtime_overrides: dict[str, object] | None = None,
) -> tuple[list[str], dict[str, str] | None]:
    command = [
        "bash",
        str(Path(args.archon_loop).resolve()),
        "--max-iterations",
        str(args.max_iterations),
        "--max-parallel",
        str(args.max_parallel),
    ]
    if args.dry_run:
        command.append("--dry-run")
    if args.no_review:
        command.append("--no-review")
    command.append(str(Path(args.workspace).resolve()))
    env = None
    effective_plan_timeout = args.plan_timeout_seconds
    effective_prover_timeout = args.prover_timeout_seconds
    effective_review_timeout = args.review_timeout_seconds
    if isinstance(runtime_overrides, dict):
        if isinstance(runtime_overrides.get("proverTimeoutSeconds"), int):
            effective_prover_timeout = int(runtime_overrides["proverTimeoutSeconds"])
    if any(
        timeout is not None
        for timeout in (effective_plan_timeout, effective_prover_timeout, effective_review_timeout)
    ):
        env = dict(os.environ)
        if effective_plan_timeout is not None:
            env["ARCHON_PLAN_TIMEOUT_SECONDS"] = str(effective_plan_timeout)
        if effective_prover_timeout is not None:
            env["ARCHON_PROVER_TIMEOUT_SECONDS"] = str(effective_prover_timeout)
        if effective_review_timeout is not None:
            env["ARCHON_REVIEW_TIMEOUT_SECONDS"] = str(effective_review_timeout)
    return command, env


def _tracked_activity_paths(workspace: Path, iteration: str | None, allowed_files: list[str]) -> list[Path]:
    paths: list[Path] = []
    if iteration:
        iter_dir = workspace / ".archon" / "logs" / iteration
        provers_dir = iter_dir / "provers"
        if provers_dir.exists():
            paths.extend(sorted(provers_dir.glob("*.jsonl")))
        prover_log = iter_dir / "prover.jsonl"
        if prover_log.exists():
            paths.append(prover_log)

    results_dir = workspace / ".archon" / "task_results"
    if results_dir.exists():
        paths.extend(sorted(results_dir.glob("*.md")))

    for rel_path in allowed_files:
        target = workspace / rel_path
        if target.exists():
            paths.append(target)

    return paths


def _latest_mtime(paths: list[Path]) -> float | None:
    mtimes = [path.stat().st_mtime for path in paths if path.exists()]
    if not mtimes:
        return None
    return max(mtimes)


def _terminate_process_group(proc: subprocess.Popen[str]) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return

    deadline = time.monotonic() + 5.0
    while proc.poll() is None and time.monotonic() < deadline:
        time.sleep(0.1)

    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            return


def _monitor_for_idle_prover(
    proc: subprocess.Popen[str],
    workspace: Path,
    source: Path,
    lease_path: Path,
    allowed_files: list[str],
    *,
    idle_seconds: float,
    poll_seconds: float,
) -> dict[str, object] | None:
    tracked_iteration: str | None = None
    last_seen_mtime: float | None = None
    last_activity_at: float | None = None

    while proc.poll() is None:
        latest_iter_name, latest_meta = latest_iteration_meta(workspace)
        _update_lease(
            lease_path,
            workspace=workspace,
            source=source,
            fields={
                "active": True,
                "status": "running",
                "supervisorPid": os.getpid(),
                "loopPid": proc.pid,
                "lastHeartbeatAt": _now_iso(),
                "latestIteration": latest_iter_name,
            },
        )
        if latest_iter_name != tracked_iteration:
            tracked_iteration = latest_iter_name
            last_seen_mtime = None
            last_activity_at = None

        prover_status = None
        if isinstance(latest_meta, dict):
            prover_payload = latest_meta.get("prover")
            if isinstance(prover_payload, dict):
                prover_status = prover_payload.get("status")

        if prover_status == "running":
            if last_activity_at is None:
                last_activity_at = time.monotonic()

            tracked_paths = _tracked_activity_paths(workspace, tracked_iteration, allowed_files)
            newest_mtime = _latest_mtime(tracked_paths)
            if newest_mtime is not None and (last_seen_mtime is None or newest_mtime > last_seen_mtime):
                last_seen_mtime = newest_mtime
                last_activity_at = time.monotonic()

            if last_activity_at is not None and time.monotonic() - last_activity_at > idle_seconds:
                _terminate_process_group(proc)
                return {
                    "event": "prover_idle_timeout",
                    "iteration": tracked_iteration,
                    "idle_seconds": idle_seconds,
                    "tracked_path_count": len(tracked_paths),
                }

        time.sleep(max(poll_seconds, 0.05))

    return None


def _run_archon_loop(
    args: argparse.Namespace,
    workspace: Path,
    source: Path,
    lease_path: Path,
    allowed_files: list[str],
    runtime_overrides: dict[str, object] | None = None,
) -> tuple[subprocess.CompletedProcess[str], dict[str, object] | None]:
    command, env = _build_loop_command(args, runtime_overrides)

    if not args.prover_idle_seconds or args.prover_idle_seconds <= 0:
        result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                cwd=str(ROOT),
                env=env,
            )
        _update_lease(
            lease_path,
            workspace=workspace,
            source=source,
            fields={
                "active": True,
                "status": "loop_finished",
                "supervisorPid": os.getpid(),
                "loopPid": None,
                "lastHeartbeatAt": _now_iso(),
                "loopExitCode": result.returncode,
            },
        )
        return (result, None)

    supervisor_dir = workspace / ".archon" / "supervisor"
    stdout_tmp = supervisor_dir / ".supervised-cycle.stdout.tmp"
    stderr_tmp = supervisor_dir / ".supervised-cycle.stderr.tmp"
    supervisor_dir.mkdir(parents=True, exist_ok=True)

    try:
        with stdout_tmp.open("w", encoding="utf-8") as stdout_handle, stderr_tmp.open(
            "w", encoding="utf-8"
        ) as stderr_handle:
            proc = subprocess.Popen(
                command,
                stdout=stdout_handle,
                stderr=stderr_handle,
                text=True,
                cwd=str(ROOT),
                env=env,
                start_new_session=True,
            )
            _update_lease(
                lease_path,
                workspace=workspace,
                source=source,
                fields={
                    "active": True,
                    "status": "running",
                    "supervisorPid": os.getpid(),
                    "loopPid": proc.pid,
                    "lastHeartbeatAt": _now_iso(),
                },
            )
            idle_event = _monitor_for_idle_prover(
                proc,
                workspace,
                source,
                lease_path,
                allowed_files,
                idle_seconds=args.prover_idle_seconds,
                poll_seconds=args.monitor_poll_seconds,
            )
            returncode = proc.wait()

        return (
            subprocess.CompletedProcess(
                command,
                returncode,
                stdout_tmp.read_text(encoding="utf-8"),
                stderr_tmp.read_text(encoding="utf-8"),
            ),
            idle_event,
        )
    finally:
        stdout_tmp.unlink(missing_ok=True)
        stderr_tmp.unlink(missing_ok=True)


def _tail_text(text: str, max_lines: int = 8) -> str:
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    return "\n".join(lines[-max_lines:])


def _write_loop_output(supervisor_dir: Path, loop_result: subprocess.CompletedProcess[str]) -> tuple[Path, Path]:
    stdout_path = supervisor_dir / "last_loop.stdout.log"
    stderr_path = supervisor_dir / "last_loop.stderr.log"
    _write_text(stdout_path, loop_result.stdout)
    _write_text(stderr_path, loop_result.stderr)
    return stdout_path, stderr_path


def _path_mtimes(paths: list[Path]) -> dict[Path, float]:
    return {path: path.stat().st_mtime for path in paths if path.exists()}


def _verify_changed_file(workspace: Path, file_path: Path, template: str | None) -> tuple[bool, str]:
    command = template or "timeout 30s lake env lean {file}"
    rendered = command.format(file=shlex.quote(str(file_path)))
    result = subprocess.run(
        ["bash", "-lc", rendered],
        cwd=str(workspace),
        capture_output=True,
        text=True,
        check=False,
    )
    output = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part).strip()
    if result.returncode != 0:
        return False, output or f"verify command failed with exit code {result.returncode}"
    if "declaration uses `sorry`" in output:
        return False, output
    return True, output


def _classify_task_result_note(path: Path) -> tuple[str, str]:
    text = path.read_text(encoding="utf-8")
    lowered = text.lower()
    if any(marker in lowered for marker in ("concrete blocker:", "validated blocker", "genuine blocker")):
        return "blocker", "contains an explicit blocker marker"
    if "**result:** resolved" in lowered:
        return "resolved", "contains a RESOLVED result marker"
    return "other", "missing an explicit RESOLVED or blocker marker"


def _task_result_name(rel_path: str) -> str:
    return rel_path.replace("/", "_") + ".md"


def _validation_filename(rel_path: str) -> str:
    return rel_path.replace("/", "_") + ".json"


def _extract_informal_note_paths(text: str) -> list[str]:
    return INFORMAL_NOTE_PATTERN.findall(text)


def _prevalidated_blocker_evidence(workspace: Path, rel_path: str) -> tuple[list[str], str] | None:
    progress_path = workspace / ".archon" / "PROGRESS.md"
    pending_path = workspace / ".archon" / "task_pending.md"
    progress_text = progress_path.read_text(encoding="utf-8") if progress_path.exists() else ""
    pending_text = pending_path.read_text(encoding="utf-8") if pending_path.exists() else ""
    combined = "\n".join(part for part in (progress_text, pending_text) if part)
    lowered = combined.lower()

    if rel_path not in combined:
        return None
    if "lean-validated" not in lowered:
        return None
    if "blocker" not in lowered:
        return None
    if "false as written" not in lowered and "validated obstruction" not in lowered:
        return None

    provenance = [".archon/PROGRESS.md", ".archon/task_pending.md"]
    evidence = pending_text.strip() or progress_text.strip()
    for note_rel in _extract_informal_note_paths(combined):
        note_path = workspace / note_rel
        if not note_path.exists():
            continue
        provenance.append(note_rel)
        note_text = note_path.read_text(encoding="utf-8").strip()
        if note_text:
            evidence = note_text
            break

    if not evidence:
        return None
    return provenance, evidence


def _synthesize_blocker_note_after_idle(
    workspace: Path,
    *,
    allowed_files: list[str],
    new_changed_files: list[str],
    new_task_result_paths: list[Path],
) -> dict[str, object] | None:
    if new_changed_files or new_task_result_paths:
        return None
    if len(allowed_files) != 1:
        return None

    rel_path = allowed_files[0]
    note_name = _task_result_name(rel_path)
    note_path = workspace / ".archon" / "task_results" / note_name
    if note_path.exists():
        return None

    evidence = _prevalidated_blocker_evidence(workspace, rel_path)
    if evidence is None:
        return None
    provenance, blocker_text = evidence

    provenance_rendered = ", ".join(f"`{path}`" for path in provenance)
    content = "\n".join(
        [
            f"# {rel_path}",
            "",
            "## Supervisor Recovery",
            "### Attempt 1",
            "- **Result:** FAILED",
            "- **Concrete blocker:** Preserved by the supervisor after a prover idle timeout. The benchmark theorem remains frozen and the statement is false as written.",
            f"- **Provenance:** Recovered from {provenance_rendered} after the prover stalled before writing a durable note.",
            "- **Next step:** Reuse this blocker note directly in the next planning pass. Only add a separately named helper/counterexample theorem after the blocker artifact already exists.",
            "",
            "## Evidence",
            blocker_text,
            "",
        ]
    )
    _write_text(note_path, content)
    return {
        "event": "synthesized_blocker_after_idle",
        "kind": "task_result",
        "task_results": [note_name],
        "provenance": provenance,
    }


def _recover_after_stall(
    workspace: Path,
    *,
    recovery_event: str,
    allow_synthesis: bool,
    allowed_files: list[str],
    new_changed_files: list[str],
    new_task_result_paths: list[Path],
    verify_template: str | None,
) -> dict[str, object] | None:
    verified_files: list[str] = []
    changed_file_failures: dict[str, str] = {}
    for rel_path in new_changed_files:
        ok, detail = _verify_changed_file(workspace, workspace / rel_path, verify_template)
        if ok:
            verified_files.append(rel_path)
        else:
            changed_file_failures[rel_path] = detail

    verified_task_results: list[str] = []
    task_result_kinds: dict[str, str] = {}
    task_result_failures: dict[str, str] = {}
    for path in new_task_result_paths:
        kind, detail = _classify_task_result_note(path)
        if kind in {"resolved", "blocker"}:
            verified_task_results.append(path.name)
            task_result_kinds[path.name] = kind
        else:
            task_result_failures[path.name] = detail

    if new_changed_files and not changed_file_failures:
        return {
            "event": recovery_event,
            "kind": "changed_file",
            "files": verified_files,
        }
    if new_task_result_paths and not task_result_failures:
        return {
            "event": recovery_event,
            "kind": "task_result",
            "task_results": verified_task_results,
            "task_result_kinds": task_result_kinds,
        }
    if allow_synthesis:
        synthesized = _synthesize_blocker_note_after_idle(
            workspace,
            allowed_files=allowed_files,
            new_changed_files=new_changed_files,
            new_task_result_paths=new_task_result_paths,
        )
        if synthesized is not None:
            return synthesized
    if changed_file_failures or task_result_failures:
        payload: dict[str, object] = {"event": "verification_failed_after_idle"}
        if changed_file_failures:
            payload["files"] = changed_file_failures
        if task_result_failures:
            payload["task_results"] = task_result_failures
        return payload
    return None


def _terminal_sync_records(workspace: Path, *, allowed_files: list[str]) -> list[dict[str, str]] | None:
    if not allowed_files:
        return None

    validation_root = workspace / ".archon" / "validation"
    records: list[dict[str, str]] = []
    for rel_path in allowed_files:
        payload = _read_json(validation_root / _validation_filename(rel_path))
        if payload is None or payload.get("acceptanceStatus") != "accepted":
            return None

        checks = payload.get("checks")
        workspace_changed = isinstance(checks, dict) and checks.get("workspaceChanged") is True
        blocker_notes = payload.get("blockerNotes")

        record = {
            "relPath": rel_path,
            "validationFile": _validation_filename(rel_path),
            "outcome": "accepted",
        }
        if isinstance(blocker_notes, list) and blocker_notes and not workspace_changed:
            record["outcome"] = "blocked"
            record["blockerNote"] = str(blocker_notes[0])
        records.append(record)
    return records


def _render_terminal_progress(records: list[dict[str, str]]) -> str:
    lines = [
        "# Project Progress",
        "",
        "## Current Stage",
        "COMPLETE",
        "",
        stage_markdown("COMPLETE", autoformalize_skipped=True),
        "",
        "## Current Objectives",
        "",
    ]
    for index, record in enumerate(records, start=1):
        rel_path = record["relPath"]
        if record["outcome"] == "blocked":
            blocker_note = record["blockerNote"]
            lines.append(
                f"{index}. **{rel_path}** — Accepted blocker note `{blocker_note}` validated; no further prover work remains in this run scope."
            )
        else:
            lines.append(
                f"{index}. **{rel_path}** — Accepted proof validated; no further prover work remains in this run scope."
            )
    lines.append("")
    return "\n".join(lines)


def _render_terminal_task_done(records: list[dict[str, str]]) -> str:
    lines = ["# Completed Tasks", ""]
    for record in records:
        rel_path = record["relPath"]
        validation_path = f".archon/validation/{record['validationFile']}"
        if record["outcome"] == "blocked":
            blocker_note = record["blockerNote"]
            lines.append(
                f"- `{rel_path}` — Accepted blocker note `{blocker_note}` validated by `{validation_path}`."
            )
        else:
            lines.append(f"- `{rel_path}` — Accepted proof validated by `{validation_path}`.")
    lines.append("")
    return "\n".join(lines)


def _partial_sync_records(workspace: Path, *, allowed_files: list[str]) -> tuple[list[dict[str, str]], list[object]] | None:
    if not allowed_files:
        return None

    validation_root = workspace / ".archon" / "validation"
    completed: list[dict[str, str]] = []
    remaining = []
    for rel_path in allowed_files:
        payload = _read_json(validation_root / _validation_filename(rel_path))
        if payload is None:
            return None

        checks = payload.get("checks")
        workspace_changed = isinstance(checks, dict) and checks.get("workspaceChanged") is True
        blocker_notes = payload.get("blockerNotes")
        if payload.get("acceptanceStatus") == "accepted":
            record = {
                "relPath": rel_path,
                "validationFile": _validation_filename(rel_path),
                "outcome": "accepted",
            }
            if isinstance(blocker_notes, list) and blocker_notes and not workspace_changed:
                record["outcome"] = "blocked"
                record["blockerNote"] = str(blocker_notes[0])
            completed.append(record)
            continue

        target = workspace / rel_path
        if target.exists():
            remaining.append(objective_for_file(workspace, target))

    if not completed or not remaining:
        return None
    return completed, remaining


def _render_focused_progress(remaining: list[object]) -> str:
    lines = [
        "# Project Progress",
        "",
        "## Current Stage",
        "prover",
        "",
        stage_markdown("prover", autoformalize_skipped=True),
        "",
        "## Current Objectives",
        "",
    ]
    for index, objective in enumerate(remaining, start=1):
        lines.append(objective.to_markdown(index))
    lines.append("")
    return "\n".join(lines)


def _render_focused_task_done(records: list[dict[str, str]]) -> str:
    lines = ["# Completed Tasks", ""]
    for record in records:
        rel_path = record["relPath"]
        validation_path = f".archon/validation/{record['validationFile']}"
        if record["outcome"] == "blocked":
            blocker_note = record["blockerNote"]
            lines.append(f"- `{rel_path}` — Accepted blocker note `{blocker_note}` validated by `{validation_path}`.")
        else:
            lines.append(f"- `{rel_path}` — Accepted proof validated by `{validation_path}`.")
    lines.append("")
    return "\n".join(lines)


def _sync_focused_planner_state(workspace: Path, *, allowed_files: list[str]) -> dict[str, object] | None:
    records = _partial_sync_records(workspace, allowed_files=allowed_files)
    if records is None:
        return None

    completed, remaining = records
    state_root = workspace / ".archon"
    _write_text(state_root / "PROGRESS.md", _render_focused_progress(remaining))
    _write_text(state_root / "task_pending.md", build_task_pending_markdown(remaining))
    _write_text(state_root / "task_done.md", _render_focused_task_done(completed))
    return {
        "event": "planner_state_synced",
        "status": "focused_remaining_scope",
        "completedTargets": [record["relPath"] for record in completed],
        "remainingTargets": [objective.rel_path for objective in remaining],
    }


def _sync_terminal_planner_state(workspace: Path, *, allowed_files: list[str]) -> dict[str, object] | None:
    records = _terminal_sync_records(workspace, allowed_files=allowed_files)
    if records is None:
        return None

    state_root = workspace / ".archon"
    _write_text(state_root / "PROGRESS.md", _render_terminal_progress(records))
    _write_text(state_root / "task_pending.md", build_task_pending_markdown([]))
    _write_text(state_root / "task_done.md", _render_terminal_task_done(records))

    return {
        "event": "planner_state_synced",
        "status": "terminal_complete",
        "targets": [record["relPath"] for record in records],
        "outcomes": {record["relPath"]: record["outcome"] for record in records},
    }


def main() -> int:
    args = parse_args()
    workspace = Path(args.workspace).resolve()
    source = Path(args.source).resolve()
    supervisor_dir = workspace / ".archon" / "supervisor"
    lease_path = _lease_path(workspace)
    hot_notes_path = supervisor_dir / "HOT_NOTES.md"
    ledger_path = supervisor_dir / "LEDGER.md"
    violations_path = supervisor_dir / "violations.jsonl"

    started_at = _now_iso()
    allowed_files = read_allowed_files(workspace)
    baseline_changed_files = collect_changed_files(source, workspace, allowed_files=allowed_files or None)
    baseline_changed_mtimes = _path_mtimes([workspace / rel_path for rel_path in baseline_changed_files])
    baseline_task_result_paths = sorted((workspace / ".archon" / "task_results").glob("*.md"))
    baseline_task_result_mtimes = _path_mtimes(baseline_task_result_paths)
    previous_iter_name, _ = latest_iteration_meta(workspace)
    lease_conflicts = _lease_conflicts(args.skip_process_check, lease_path, current_pid=os.getpid())
    runtime_overrides = _resolve_runtime_overrides(args, workspace, allowed_files=allowed_files)
    if lease_conflicts:
        _append_text(
            violations_path,
            "".join(json.dumps(event, sort_keys=True) + "\n" for event in lease_conflicts),
        )
        hot_notes = [
            "# Supervisor Hot Notes",
            "",
            "Read this before touching the run.",
            "",
            "- Status: run_busy",
            f"- Started at: {started_at}",
            f"- Workspace: {workspace}",
            f"- Source: {source}",
            f"- Allowed files: {', '.join(allowed_files) if allowed_files else '(all .lean files)'}",
            "- Reason: an active run-local lease already owns this workspace",
        ]
        for event in lease_conflicts:
            hot_notes.append(f"- Lease event: {event['event']}")
            if event.get("supervisorPid") is not None:
                hot_notes.append(f"- Lease supervisor pid: {event['supervisorPid']}")
            if event.get("loopPid") is not None:
                hot_notes.append(f"- Lease loop pid: {event['loopPid']}")
            if event.get("updatedAt") is not None:
                hot_notes.append(f"- Lease updated at: {event['updatedAt']}")
        _write_text(hot_notes_path, "\n".join(hot_notes) + "\n")
        _append_text(
            ledger_path,
            "\n".join(
                [
                    f"## Cycle {started_at}",
                    "",
                    "- Status: `run_busy`",
                    "- Reason: `active run-local lease detected`",
                    f"- Lease events: `{len(lease_conflicts)}`",
                    "",
                ]
            ),
        )
        print("status=run_busy")
        print(f"policy_events={len(lease_conflicts)}")
        return 6

    _update_lease(
        lease_path,
        workspace=workspace,
        source=source,
        fields={
            "active": True,
            "status": "starting",
            "supervisorPid": os.getpid(),
            "loopPid": None,
            "startedAt": started_at,
            "lastHeartbeatAt": started_at,
            "latestIteration": previous_iter_name,
            "recoveryOnly": args.recovery_only,
        },
        clear_fields=TERMINAL_LEASE_FIELDS,
    )

    if args.recovery_only:
        loop_result = subprocess.CompletedProcess(["recovery-only"], 0, "", "")
        idle_event = None
    else:
        loop_result, idle_event = _run_archon_loop(
            args,
            workspace,
            source,
            lease_path,
            allowed_files,
            runtime_overrides,
        )
    stdout_path, stderr_path = _write_loop_output(supervisor_dir, loop_result)
    _update_lease(
        lease_path,
        workspace=workspace,
        source=source,
        fields={
            "active": True,
            "status": "analyzing",
            "supervisorPid": os.getpid(),
            "loopPid": None,
            "lastHeartbeatAt": _now_iso(),
            "loopExitCode": loop_result.returncode,
        },
    )
    latest_iter_name, latest_meta = latest_iteration_meta(workspace)
    drifts = collect_header_drifts(source, workspace, allowed_files=allowed_files or None)
    changed_files = collect_changed_files(source, workspace, allowed_files=allowed_files or None)
    task_result_paths = sorted((workspace / ".archon" / "task_results").glob("*.md"))
    task_results = sorted(path.name for path in task_result_paths)
    if args.recovery_only:
        new_changed_files = sorted(changed_files)
        new_task_result_paths = sorted(task_result_paths)
    else:
        new_changed_files = sorted(
            rel_path
            for rel_path in changed_files
            if (workspace / rel_path).stat().st_mtime > baseline_changed_mtimes.get(workspace / rel_path, float("-inf"))
        )
        new_task_result_paths = sorted(
            path
            for path in task_result_paths
            if path.stat().st_mtime > baseline_task_result_mtimes.get(path, float("-inf"))
        )
    new_task_results = [path.name for path in new_task_result_paths]
    recovered_after_stall = None
    prover_failures = collect_meta_prover_errors(latest_meta)
    if args.recovery_only:
        recovered_after_stall = _recover_after_stall(
            workspace,
            recovery_event="verified_in_recovery",
            allow_synthesis=False,
            allowed_files=allowed_files,
            new_changed_files=new_changed_files,
            new_task_result_paths=new_task_result_paths,
            verify_template=args.changed_file_verify_template,
        )
    elif idle_event is not None or prover_failures:
        recovered_after_stall = _recover_after_stall(
            workspace,
            recovery_event="verified_after_idle" if idle_event is not None else "verified_after_stall",
            allow_synthesis=idle_event is not None,
            allowed_files=allowed_files,
            new_changed_files=new_changed_files,
            new_task_result_paths=new_task_result_paths,
            verify_template=args.changed_file_verify_template,
        )
        if recovered_after_stall is not None and recovered_after_stall.get("event") == "synthesized_blocker_after_idle":
            task_result_paths = sorted((workspace / ".archon" / "task_results").glob("*.md"))
            task_results = sorted(path.name for path in task_result_paths)
            new_task_result_paths = sorted(
                path
                for path in task_result_paths
                if path.stat().st_mtime > baseline_task_result_mtimes.get(path, float("-inf"))
            )
            new_task_results = [path.name for path in new_task_result_paths]
    created_new_iteration = latest_iter_name is not None and latest_iter_name != previous_iter_name

    events: list[dict[str, object]] = []
    if args.recovery_only:
        events.append({"event": "recovery_only"})
    for drift in drifts:
        events.append(drift.to_event())
    if prover_failures:
        events.append(
            {
                "event": "prover_error",
                "files": prover_failures,
                "iteration": latest_iter_name,
            }
        )
    if idle_event is not None:
        events.append(idle_event)
    if recovered_after_stall is not None:
        events.append(recovered_after_stall)
    if loop_result.returncode != 0 and not created_new_iteration:
        events.append(
            {
                "event": "no_new_iteration_meta",
                "previous_iteration": previous_iter_name,
                "latest_iteration": latest_iter_name,
            }
        )

    if not new_changed_files and not new_task_results:
        events.append({"event": "no_progress"})

    status = "clean"
    if any(event["event"] == "header_mutation" for event in events):
        status = "policy_violation"
    elif recovered_after_stall is not None and recovered_after_stall.get("event") in {
        "verified_after_idle",
        "verified_after_stall",
        "verified_in_recovery",
        "synthesized_blocker_after_idle",
    }:
        status = "clean"
    elif prover_failures:
        status = "prover_failed"
    elif idle_event is not None:
        status = "prover_idle"
    elif loop_result.returncode != 0:
        status = "loop_failed"
    elif not new_changed_files and not new_task_results:
        status = "no_progress"

    validation_files = write_validation_artifacts(
        workspace,
        status=status,
        allowed_files=allowed_files,
        changed_files=changed_files,
        drifts=drifts,
        prover_failures=prover_failures,
        iteration=latest_iter_name,
        loop_exit_code=loop_result.returncode,
        recovered_after_stall=recovered_after_stall,
    )
    lesson_file = write_lesson_artifact(
        workspace,
        status=status,
        iteration=latest_iter_name,
        allowed_files=allowed_files,
        validation_files=validation_files,
        drifts=drifts,
        prover_failures=prover_failures,
        recovered_after_stall=recovered_after_stall,
    )
    planner_state_sync = _sync_terminal_planner_state(workspace, allowed_files=allowed_files)
    if planner_state_sync is None:
        planner_state_sync = _sync_focused_planner_state(workspace, allowed_files=allowed_files)
    if planner_state_sync is not None:
        events.append(planner_state_sync)

    if events:
        _append_text(
            violations_path,
            "".join(json.dumps(event, sort_keys=True) + "\n" for event in events),
        )

    hot_notes = [
        "# Supervisor Hot Notes",
        "",
        "Read this before touching the run.",
        "",
        f"- Status: {status}",
        f"- Started at: {started_at}",
        f"- Workspace: {workspace}",
        f"- Source: {source}",
        f"- Lease file: {lease_path}",
        f"- Allowed files: {', '.join(allowed_files) if allowed_files else '(all .lean files)'}",
        f"- Loop exit code: {loop_result.returncode}",
        f"- Changed files: {', '.join(changed_files) if changed_files else '(none)'}",
        f"- Task results: {', '.join(task_results) if task_results else '(none)'}",
        f"- New changed files: {', '.join(new_changed_files) if new_changed_files else '(none)'}",
        f"- New task results: {', '.join(new_task_results) if new_task_results else '(none)'}",
        f"- Policy violations: {len([event for event in events if event['event'] == 'header_mutation'])}",
        f"- Validation artifacts: {', '.join(validation_files) if validation_files else '(none)'}",
        f"- Lesson artifact: {lesson_file or '(none)'}",
    ]
    if latest_iter_name is not None:
        hot_notes.append(f"- Latest iteration: {latest_iter_name}")
    if isinstance(latest_meta, dict):
        plan_status = latest_meta.get("plan", {}).get("status") if isinstance(latest_meta.get("plan"), dict) else None
        prover_status = latest_meta.get("prover", {}).get("status") if isinstance(latest_meta.get("prover"), dict) else None
        if isinstance(plan_status, str):
            hot_notes.append(f"- Latest plan status: {plan_status}")
        if isinstance(prover_status, str):
            hot_notes.append(f"- Latest prover status: {prover_status}")
    if prover_failures:
        hot_notes.append(f"- Prover errors: {', '.join(prover_failures)}")
    if idle_event is not None:
        hot_notes.append(f"- Idle timeout triggered: {idle_event['idle_seconds']}s without prover activity")
        if idle_event.get("iteration"):
            hot_notes.append(f"- Idle iteration: {idle_event['iteration']}")
    if recovered_after_stall is not None and recovered_after_stall.get("event") in {
        "verified_after_idle",
        "verified_after_stall",
        "verified_in_recovery",
    }:
        recovery_event = recovered_after_stall.get("event")
        if recovery_event == "verified_after_idle":
            recovery_label = "idle"
        elif recovery_event == "verified_after_stall":
            recovery_label = "stall"
        else:
            recovery_label = "recovery-only pass"
        if recovered_after_stall.get("kind") == "changed_file":
            files = ", ".join(recovered_after_stall.get("files", [])) or "(none)"
            hot_notes.append(f"- Recovered after prover {recovery_label}: verified changed files {files}")
        elif recovered_after_stall.get("kind") == "task_result":
            results = ", ".join(recovered_after_stall.get("task_results", [])) or "(none)"
            hot_notes.append(f"- Recovered after prover {recovery_label}: durable task results already existed ({results})")
    if recovered_after_stall is not None and recovered_after_stall.get("event") == "synthesized_blocker_after_idle":
        results = ", ".join(recovered_after_stall.get("task_results", [])) or "(none)"
        hot_notes.append(f"- Recovered after prover idle: synthesized durable blocker note ({results})")
        provenance = recovered_after_stall.get("provenance", [])
        if isinstance(provenance, list) and provenance:
            hot_notes.append(f"- Blocker note provenance: {', '.join(str(item) for item in provenance)}")
    if recovered_after_stall is not None and recovered_after_stall.get("event") == "verification_failed_after_idle":
        files = recovered_after_stall.get("files", {})
        if isinstance(files, dict):
            for rel_path, detail in files.items():
                hot_notes.append(f"- Verification after idle failed for {rel_path}: {detail}")
        task_results_failures = recovered_after_stall.get("task_results", {})
        if isinstance(task_results_failures, dict):
            for note_name, detail in task_results_failures.items():
                hot_notes.append(f"- Verification after idle failed for task result {note_name}: {detail}")
    if runtime_overrides.get("tailScopeApplied") is True:
        objective_count = runtime_overrides.get("objectiveCount")
        prover_timeout = runtime_overrides.get("proverTimeoutSeconds")
        hot_notes.append(
            f"- Tail-scope runtime override: raised prover timeout to {prover_timeout}s for {objective_count} current objectives"
        )
    if planner_state_sync is not None:
        if planner_state_sync.get("status") == "terminal_complete":
            hot_notes.append("- Planner state synced: wrote terminal closure to .archon/PROGRESS.md, task_pending.md, and task_done.md")
        elif planner_state_sync.get("status") == "focused_remaining_scope":
            hot_notes.append("- Planner state synced: removed accepted targets from the next-cycle objective list and refreshed task_pending.md/task_done.md")
    for drift in drifts:
        hot_notes.append(f"- Violation: {drift.rel_path}::{drift.declaration_name} -> {drift.mutation_class}")
    if loop_result.returncode != 0 and not created_new_iteration:
        hot_notes.append("- No new iteration metadata was created during this cycle; the failure happened before Archon initialized a fresh iter-* directory.")
    stdout_tail = _tail_text(loop_result.stdout)
    stderr_tail = _tail_text(loop_result.stderr)
    if stdout_tail:
        hot_notes.append(f"- Last archon-loop stdout log: {stdout_path}")
        hot_notes.append("```")
        hot_notes.extend(stdout_tail.splitlines())
        hot_notes.append("```")
    if stderr_tail:
        hot_notes.append(f"- Last archon-loop stderr log: {stderr_path}")
        hot_notes.append("```")
        hot_notes.extend(stderr_tail.splitlines())
        hot_notes.append("```")
    _write_text(hot_notes_path, "\n".join(hot_notes) + "\n")

    ledger_lines = [
        f"## Cycle {started_at}",
        "",
        f"- Status: `{status}`",
        f"- Lease file: `{lease_path}`",
        f"- Loop exit code: `{loop_result.returncode}`",
        f"- Changed files: `{', '.join(changed_files) if changed_files else '(none)'}`",
        f"- Task results: `{', '.join(task_results) if task_results else '(none)'}`",
        f"- New changed files: `{', '.join(new_changed_files) if new_changed_files else '(none)'}`",
        f"- New task results: `{', '.join(new_task_results) if new_task_results else '(none)'}`",
        f"- Policy events: `{len(events)}`",
        f"- Latest iteration: `{latest_iter_name or '(none)'}`",
        f"- Prover errors: `{', '.join(prover_failures) if prover_failures else '(none)'}`",
        f"- Validation artifacts: `{', '.join(validation_files) if validation_files else '(none)'}`",
        f"- Lesson artifact: `{lesson_file or '(none)'}`",
        f"- Idle timeout: `{idle_event['idle_seconds']}s`" if idle_event is not None else "- Idle timeout: `(none)`",
        f"- Idle recovery: `{recovered_after_stall['event']}`" if recovered_after_stall is not None else "- Idle recovery: `(none)`",
        f"- Planner state sync: `{planner_state_sync['status']}`" if planner_state_sync is not None else "- Planner state sync: `(none)`",
        f"- Recovery only: `{args.recovery_only}`",
        f"- New iteration created: `{created_new_iteration}`",
        "",
    ]
    _append_text(ledger_path, "\n".join(ledger_lines))

    _update_lease(
        lease_path,
        workspace=workspace,
        source=source,
        fields={
            "active": False,
            "status": "completed",
            "supervisorPid": os.getpid(),
            "loopPid": None,
            "lastHeartbeatAt": _now_iso(),
            "latestIteration": latest_iter_name,
            "loopExitCode": loop_result.returncode,
            "finalStatus": status,
            "completedAt": _now_iso(),
            "recoveryEvent": recovered_after_stall.get("event") if isinstance(recovered_after_stall, dict) else None,
            "validationFiles": validation_files,
            "lessonFile": lesson_file,
        },
    )

    print(f"status={status}")
    print(f"changed_files={len(changed_files)}")
    print(f"new_changed_files={len(new_changed_files)}")
    print(f"task_results={len(task_results)}")
    print(f"new_task_results={len(new_task_results)}")
    print(f"policy_events={len(events)}")

    if status == "policy_violation":
        return 2
    if status == "prover_failed":
        return 3
    if status == "no_progress":
        return 4
    if status == "prover_idle":
        return 5
    if status == "loop_failed":
        return loop_result.returncode or 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
