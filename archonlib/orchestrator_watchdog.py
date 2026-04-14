from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

from archonlib.campaign import (
    DEFAULT_HEARTBEAT_SECONDS,
    DEFAULT_OWNER_LEASE_SECONDS,
    build_campaign_compare_report,
    claim_owner_lease,
    cleanup_stale_launch_processes,
    compare_report_freshness,
    collect_campaign_status,
    execute_run_recovery,
    finalize_campaign,
    refresh_owner_lease,
    release_owner_lease,
)


TERMINAL_RUN_STATUSES = {"accepted", "blocked", "contaminated"}
AUTOMATIC_RECOVERY_ACTIONS = {"launch_teacher", "relaunch_teacher", "recovery_only"}
SESSION_ID_RE = re.compile(r"session id:\s*([0-9a-f-]{36})", re.IGNORECASE)
RECOVERY_CLASS_PRIORITY = {
    "recovery_finalize": 0,
    "partial_progress_relaunch": 1,
    "queued_launch": 2,
    "launch_failed_retry": 3,
    "rate_limited_backoff": 4,
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


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


def is_campaign_terminal(status_payload: dict[str, Any]) -> bool:
    runs = status_payload.get("runs")
    if not isinstance(runs, list) or not runs:
        return False
    for run in runs:
        if not isinstance(run, dict):
            return False
        if run.get("status") not in TERMINAL_RUN_STATUSES:
            return False
    return True


def campaign_progress_fingerprint(campaign_root: Path, status_payload: dict[str, Any]) -> dict[str, Any]:
    runs = status_payload.get("runs")
    run_fingerprints: list[dict[str, Any]] = []
    if isinstance(runs, list):
        for run in runs:
            if not isinstance(run, dict):
                continue
            run_id = run.get("runId")
            run_root_rel = run.get("runRoot")
            if not isinstance(run_id, str) or not isinstance(run_root_rel, str):
                continue
            run_root = campaign_root / run_root_rel
            latest_iteration = run.get("latestIteration")
            launch_state = _read_json(run_root / "control" / "teacher-launch-state.json") or {}
            lease = _read_json(run_root / "workspace" / ".archon" / "supervisor" / "run-lease.json") or {}
            tracked_paths = [
                run_root / "control" / "teacher-launch-state.json",
                run_root / "control" / "teacher-launch.stdout.log",
                run_root / "control" / "teacher-launch.stderr.log",
                run_root / "workspace" / ".archon" / "supervisor" / "run-lease.json",
                run_root / "workspace" / ".archon" / "supervisor" / "HOT_NOTES.md",
                run_root / "workspace" / ".archon" / "supervisor" / "LEDGER.md",
            ]
            validation_root = run_root / "workspace" / ".archon" / "validation"
            task_results_root = run_root / "workspace" / ".archon" / "task_results"
            if validation_root.exists():
                tracked_paths.extend(path for path in validation_root.glob("*.json") if path.is_file())
            if task_results_root.exists():
                tracked_paths.extend(path for path in task_results_root.glob("*.md") if path.is_file())
            if isinstance(latest_iteration, str) and latest_iteration:
                iter_root = run_root / "workspace" / ".archon" / "logs" / latest_iteration
                tracked_paths.append(iter_root / "meta.json")
                tracked_paths.extend(path for path in iter_root.glob("provers/*.jsonl") if path.is_file())
            mtimes = [path.stat().st_mtime_ns for path in tracked_paths if path.exists()]
            run_fingerprints.append(
                {
                    "runId": run_id,
                    "status": run.get("status"),
                    "latestIteration": latest_iteration,
                    "runningSignal": run.get("runningSignal"),
                    "launchPhase": run.get("launchPhase", launch_state.get("phase")),
                    "launchActive": run.get("launchActive", launch_state.get("active")),
                    "leaseActive": run.get("leaseActive", lease.get("active")),
                    "acceptedProofCount": len(run.get("acceptedProofs", [])) if isinstance(run.get("acceptedProofs"), list) else 0,
                    "acceptedBlockerCount": len(run.get("acceptedBlockers", [])) if isinstance(run.get("acceptedBlockers"), list) else 0,
                    "latestActivityNs": max(mtimes) if mtimes else None,
                }
            )

    event_lines = 0
    events_path = campaign_root / "events.jsonl"
    if events_path.exists():
        with events_path.open("r", encoding="utf-8") as handle:
            for event_lines, _ in enumerate(handle, start=1):
                pass

    return {
        "runFingerprints": sorted(run_fingerprints, key=lambda item: item["runId"]),
        "eventLines": event_lines,
    }


def automatic_recovery_run_ids(status_payload: dict[str, Any]) -> list[str]:
    runs = status_payload.get("runs")
    if not isinstance(runs, list):
        return []
    now = datetime.now(timezone.utc)
    run_ids: list[str] = []
    for run in runs:
        if not isinstance(run, dict):
            continue
        recovery = run.get("recommendedRecovery")
        if not isinstance(recovery, dict) or recovery.get("action") not in AUTOMATIC_RECOVERY_ACTIONS:
            continue
        retry_after = _parse_iso_datetime(run.get("retryAfter"))
        if retry_after is not None and retry_after > now:
            continue
        run_id = run.get("runId")
        if not isinstance(run_id, str) or not run_id:
            continue
        run_ids.append(run_id)
    return run_ids


def campaign_has_live_work(status_payload: dict[str, Any]) -> bool:
    runs = status_payload.get("runs")
    if not isinstance(runs, list):
        return False
    for run in runs:
        if not isinstance(run, dict):
            continue
        if run.get("status") == "running":
            return True
        if run.get("runningSignal") is True:
            return True
        if run.get("launchActive") is True:
            return True
        if run.get("leaseActive") is True:
            return True
    return False


def active_work_run_ids(status_payload: dict[str, Any]) -> list[str]:
    runs = status_payload.get("runs")
    if not isinstance(runs, list):
        return []
    active_ids: list[str] = []
    for run in runs:
        if not isinstance(run, dict):
            continue
        run_id = run.get("runId")
        if not isinstance(run_id, str) or not run_id:
            continue
        if run.get("runningSignal") is True or run.get("launchActive") is True:
            active_ids.append(run_id)
    return sorted(set(active_ids))


def select_automatic_recovery_run_ids(
    status_payload: dict[str, Any],
    *,
    max_active_launches: int,
    launch_batch_size: int,
    launch_cooldown_seconds: int,
    include_recovery_only: bool = True,
) -> list[str]:
    runs = status_payload.get("runs")
    if not isinstance(runs, list) or launch_batch_size <= 0:
        return []

    active_launches = 0
    active_work_slots = len(active_work_run_ids(status_payload))
    candidates: list[tuple[int, str, str]] = []
    now = datetime.now(timezone.utc)

    for run in runs:
        if not isinstance(run, dict):
            continue
        if run.get("launchActive") is True:
            active_launches += 1
        recovery = run.get("recommendedRecovery")
        if not isinstance(recovery, dict):
            continue
        action = recovery.get("action")
        if action not in AUTOMATIC_RECOVERY_ACTIONS:
            continue
        if not include_recovery_only and action == "recovery_only":
            continue
        retry_after = _parse_iso_datetime(run.get("retryAfter"))
        if retry_after is not None and retry_after > now:
            continue
        if action != "recovery_only" and launch_cooldown_seconds > 0:
            launch_updated_at = _parse_iso_datetime(run.get("launchUpdatedAt"))
            if launch_updated_at is not None and (now - launch_updated_at).total_seconds() < launch_cooldown_seconds:
                continue
        run_id = run.get("runId")
        if not isinstance(run_id, str) or not run_id:
            continue
        recovery_class = str(run.get("recoveryClass") or "manual_review")
        priority = RECOVERY_CLASS_PRIORITY.get(recovery_class, 99)
        candidates.append((priority, run_id, str(action)))

    selected: list[str] = []
    launch_slots = max(0, min(max_active_launches - active_launches, max_active_launches - active_work_slots))
    for _priority, run_id, action in sorted(candidates, key=lambda item: (item[0], item[1])):
        if len(selected) >= launch_batch_size:
            break
        if action == "recovery_only":
            selected.append(run_id)
            continue
        if launch_slots <= 0:
            continue
        selected.append(run_id)
        launch_slots -= 1
    return selected


def watchdog_campaign_snapshot(status_payload: dict[str, Any]) -> dict[str, Any]:
    runs = status_payload.get("runs")
    status_run_ids: dict[str, list[str]] = {}
    prewarm_plan_counts: dict[str, int] = {}
    prewarm_pending_run_ids: list[str] = []
    active_launches: list[dict[str, str]] = []
    accepted_proof_count = 0
    accepted_blocker_count = 0
    active_work_run_ids_list = active_work_run_ids(status_payload)

    if isinstance(runs, list):
        for run in runs:
            if not isinstance(run, dict):
                continue
            run_id = run.get("runId")
            status = run.get("status")
            if isinstance(run_id, str) and run_id and isinstance(status, str) and status:
                status_run_ids.setdefault(status, []).append(run_id)
            accepted_proofs = run.get("acceptedProofs", [])
            accepted_blockers = run.get("acceptedBlockers", [])
            if isinstance(accepted_proofs, list):
                accepted_proof_count += len(accepted_proofs)
            if isinstance(accepted_blockers, list):
                accepted_blocker_count += len(accepted_blockers)

            prewarm_plan = run.get("prewarmPlan")
            if isinstance(prewarm_plan, str) and prewarm_plan:
                prewarm_plan_counts[prewarm_plan] = prewarm_plan_counts.get(prewarm_plan, 0) + 1
            if run.get("prewarmPending") is True and isinstance(run_id, str) and run_id:
                prewarm_pending_run_ids.append(run_id)

            launch_phase = run.get("launchPhase")
            if run.get("launchActive") is True and isinstance(run_id, str) and run_id:
                active_launches.append(
                    {
                        "runId": run_id,
                        "phase": launch_phase if isinstance(launch_phase, str) and launch_phase else "unknown",
                    }
                )

    for run_ids in status_run_ids.values():
        run_ids.sort()
    prewarm_pending_run_ids.sort()
    active_launches.sort(key=lambda item: item["runId"])

    return {
        "runCounts": status_payload.get("counts", {}),
        "statusRunIds": status_run_ids,
        "recoverableRunIds": automatic_recovery_run_ids(status_payload),
        "prewarmPlanCounts": prewarm_plan_counts,
        "prewarmPendingRunIds": prewarm_pending_run_ids,
        "activeLaunches": active_launches,
        "activeWorkRunIds": active_work_run_ids_list,
        "acceptedProofCount": accepted_proof_count,
        "acceptedBlockerCount": accepted_blocker_count,
        "terminal": is_campaign_terminal(status_payload),
    }


def write_watchdog_state(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


@dataclass
class OutputMirrorState:
    session_id: str | None = None
    last_output_at: str | None = None
    last_output_monotonic: float | None = None
    line_count: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)


@dataclass
class LaunchResult:
    process: subprocess.Popen[str]
    log_handle: TextIO
    session_id: str | None
    mode: str
    command: list[str]
    output_state: OutputMirrorState
    output_thread: threading.Thread


def build_default_orchestrator_prompt(
    *,
    archon_root: Path,
    campaign_root: Path,
    max_active_launches: int = 1,
    launch_batch_size: int = 1,
) -> str:
    recover_cmd = (
        f"uv run --directory {archon_root} autoarchon-campaign-recover "
        f"--campaign-root {campaign_root} --run-id <run-id> --execute"
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
            "- the outer orchestrator_watchdog.py process is your expected wrapper, not a competing owner session",
            "- inspect CAMPAIGN_MANIFEST.json, campaign-status.json, and recommendedRecovery before acting",
            "- launch teachers only from runs/<id>/control/launch-teacher.sh",
            f"- prefer deterministic single-run recovery via {recover_cmd} over ad hoc shell logic",
            "- never use --all-recoverable --execute from the owner session",
            (
                f"- recovery discipline: launch or recover at most {launch_batch_size} run(s) per decision "
                f"and never exceed {max_active_launches} active detached teacher launch(es) at once; "
                "refresh campaign-status.json after each launch before deciding again"
            ),
            "- keep teachers on disjoint run roots",
            "- finalize only validated proofs and accepted blocker notes",
            "- do not stop to ask the user about ownership, monitor-vs-act, or whether to continue; choose the safest deterministic action yourself",
            "- if another real owner lease is active, do not ask the user; record monitor-only notes and let the watchdog recover later",
            "",
            "Stop only when:",
            "- all runs are in terminal states and reports/final/ is up to date, or",
            "- a hard external dependency prevents safe continuation",
            "",
        ]
    )


def build_watchdog_resume_prompt(*, stalled: bool) -> str:
    if stalled:
        return (
            "Continue owning this AutoArchon campaign after a stalled outer session. "
            "The outer orchestrator_watchdog.py process is your wrapper, not a competing owner. "
            "Refresh campaign truth from campaign-status.json and recommendedRecovery, "
            "launch or recover only what is still needed, finalize when terminal, "
            "and do not stop to ask the user about ownership or whether to continue."
        )
    return (
        "Continue owning this AutoArchon campaign. "
        "The outer orchestrator_watchdog.py process is your wrapper, not a competing owner. "
        "Refresh campaign truth from campaign-status.json and recommendedRecovery before acting. "
        "If all runs are already terminal, finalize the campaign. "
        "Do not stop to ask the user about ownership or whether to continue."
    )


def _write_watchdog_log(log_handle: TextIO, output_state: OutputMirrorState, message: str) -> None:
    with output_state.lock:
        log_handle.write(message)
        log_handle.flush()


def _mirror_process_output(stdout: TextIO, log_handle: TextIO, output_state: OutputMirrorState) -> None:
    try:
        for line in stdout:
            now_iso = utc_now_iso()
            now_monotonic = time.monotonic()
            session_id_match = SESSION_ID_RE.search(line)
            with output_state.lock:
                log_handle.write(line)
                log_handle.flush()
                output_state.last_output_at = now_iso
                output_state.last_output_monotonic = now_monotonic
                output_state.line_count += 1
                if session_id_match:
                    output_state.session_id = session_id_match.group(1)
    finally:
        stdout.close()
        with output_state.lock:
            log_handle.flush()


def _stop_output_mirror(launch_result: LaunchResult, *, timeout_seconds: float = 5.0) -> None:
    if launch_result.output_thread.is_alive():
        launch_result.output_thread.join(timeout_seconds)
    with launch_result.output_state.lock:
        launch_result.log_handle.flush()


def launch_codex_session(
    *,
    archon_root: Path,
    prompt_path: Path,
    log_path: Path,
    model: str,
    reasoning_effort: str,
    session_id: str | None = None,
    resume_prompt: str | None = None,
) -> LaunchResult:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    mode = "resume" if session_id else "start"
    command = [
        "codex",
        "exec",
        "--skip-git-repo-check",
        "--sandbox",
        "danger-full-access",
        "-c",
        "approval_policy=never",
        "-c",
        f"model_reasoning_effort={reasoning_effort}",
        "--model",
        model,
    ]
    if session_id:
        command.extend(["resume", session_id])
        if resume_prompt:
            command.append(resume_prompt)
    else:
        command.extend(["-",])

    log_handle = log_path.open("a", encoding="utf-8")
    prompt_handle = None if session_id else prompt_path.open("r", encoding="utf-8")
    proc = subprocess.Popen(
        command,
        cwd=str(archon_root),
        stdin=prompt_handle,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        preexec_fn=os.setsid,
    )
    if prompt_handle is not None:
        prompt_handle.close()

    assert proc.stdout is not None
    output_state = OutputMirrorState(session_id=session_id)
    output_thread = threading.Thread(
        target=_mirror_process_output,
        args=(proc.stdout, log_handle, output_state),
        name=f"watchdog-output-{mode}",
        daemon=True,
    )
    output_thread.start()

    parsed_session_id = session_id
    start_deadline = time.monotonic() + 30.0
    while time.monotonic() < start_deadline:
        if output_state.session_id:
            parsed_session_id = output_state.session_id
            break
        if proc.poll() is not None and not output_thread.is_alive():
            break
        time.sleep(0.1)
    if output_state.session_id:
        parsed_session_id = output_state.session_id

    return LaunchResult(
        process=proc,
        log_handle=log_handle,
        session_id=parsed_session_id,
        mode=mode,
        command=command,
        output_state=output_state,
        output_thread=output_thread,
    )


def terminate_process_group(proc: subprocess.Popen[str], *, grace_seconds: float = 10.0) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return
        time.sleep(0.2)
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except ProcessLookupError:
        return


def _refresh_compare_report(campaign_root: Path, *, heartbeat_seconds: int) -> str:
    report = build_campaign_compare_report(campaign_root, heartbeat_seconds=heartbeat_seconds)
    generated_at = report.get("generatedAt")
    return generated_at if isinstance(generated_at, str) and generated_at else utc_now_iso()


def _current_compare_freshness(campaign_root: Path, status_payload: dict[str, Any]) -> dict[str, Any]:
    report = _read_json(campaign_root / "reports" / "final" / "compare-report.json")
    return compare_report_freshness(status_payload, report)


def _cleanup_stale_launchers_best_effort(
    *,
    campaign_root: Path,
    log_handle: TextIO,
    output_state: OutputMirrorState,
    heartbeat_seconds: int,
) -> dict[str, Any] | None:
    try:
        return cleanup_stale_launch_processes(
            campaign_root,
            heartbeat_seconds=heartbeat_seconds,
            execute=True,
        )
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
        _write_watchdog_log(
            log_handle,
            output_state,
            f"\n[watchdog] stale-launch cleanup skipped at {utc_now_iso()}: {exc}\n",
        )
        return None


def run_watchdog(
    *,
    archon_root: Path,
    campaign_root: Path,
    prompt_path: Path,
    state_path: Path,
    log_path: Path,
    model: str,
    reasoning_effort: str,
    poll_seconds: int = 30,
    stall_seconds: int = 300,
    bootstrap_launch_after_seconds: int = 45,
    max_restarts: int = 3,
    max_active_launches: int = 2,
    launch_batch_size: int = 1,
    launch_cooldown_seconds: int = 90,
    owner_silence_seconds: int = 1200,
    finalize_on_terminal: bool = True,
    owner_entrypoint: str = "autoarchon-orchestrator-watchdog",
    owner_lease_seconds: int = DEFAULT_OWNER_LEASE_SECONDS,
) -> dict[str, Any]:
    status = collect_campaign_status(campaign_root)
    fingerprint = campaign_progress_fingerprint(campaign_root, status)
    snapshot = watchdog_campaign_snapshot(status)
    compare_freshness = _current_compare_freshness(campaign_root, status)
    session_id: str | None = None
    restart_count = 0
    bootstrap_done = False
    stall_reason: str | None = None
    watchdog_error: dict[str, str] | None = None
    manual_interventions = 0
    last_status_refresh_at = utc_now_iso()
    last_progress_at = utc_now_iso()
    last_recovery_at: str | None = None
    last_compare_report_at = _refresh_compare_report(campaign_root, heartbeat_seconds=DEFAULT_HEARTBEAT_SECONDS)
    watchdog_status = "running"
    budget_exhausted = False
    owner_pid = os.getpid()
    claimed, owner_lease = claim_owner_lease(
        campaign_root,
        owner_entrypoint=owner_entrypoint,
        owner_pid=owner_pid,
        lease_seconds=owner_lease_seconds,
        metadata={"mode": "watchdog"},
    )
    if not claimed:
        result = {
            "schemaVersion": 1,
            "campaignId": status.get("campaignId", campaign_root.name),
            "campaignRoot": str(campaign_root),
            "sessionId": None,
            "restartCount": restart_count,
            "watchdogStatus": "degraded",
            "budgetExhausted": False,
            "terminal": is_campaign_terminal(status),
            "finalized": False,
            "runCounts": snapshot["runCounts"],
            "statusRunIds": snapshot["statusRunIds"],
            "recoverableRunIds": snapshot["recoverableRunIds"],
            "prewarmPlanCounts": snapshot["prewarmPlanCounts"],
            "prewarmPendingRunIds": snapshot["prewarmPendingRunIds"],
            "activeLaunches": snapshot["activeLaunches"],
            "activeWorkRunIds": snapshot["activeWorkRunIds"],
            "acceptedProofCount": snapshot["acceptedProofCount"],
            "acceptedBlockerCount": snapshot["acceptedBlockerCount"],
            "lastStatusRefreshAt": last_status_refresh_at,
            "lastProgressAt": last_progress_at,
            "lastRecoveryAt": last_recovery_at,
            "lastCompareReportAt": last_compare_report_at,
            "ownerLastLogAt": None,
            "stallReason": "owner_conflict",
            "launchBudget": {
                "maxActiveLaunches": max_active_launches,
                "launchBatchSize": launch_batch_size,
                "launchCooldownSeconds": launch_cooldown_seconds,
            },
            "manualInterventions": manual_interventions,
            "reportFreshness": compare_freshness,
            "ownerLease": owner_lease,
        }
        write_watchdog_state(state_path, {**result, "campaignSnapshot": snapshot, "updatedAt": utc_now_iso()})
        return result
    launch_result = None
    try:
        launch_result = launch_codex_session(
            archon_root=archon_root,
            prompt_path=prompt_path,
            log_path=log_path,
            model=model,
            reasoning_effort=reasoning_effort,
        )
        session_id = launch_result.session_id
        progress_at = time.monotonic()

        while True:
            owner_lease = refresh_owner_lease(
                campaign_root,
                owner_entrypoint=owner_entrypoint,
                owner_pid=owner_pid,
                session_id=session_id,
                child_pid=launch_result.process.pid if launch_result.process.poll() is None else None,
                lease_seconds=owner_lease_seconds,
                metadata={"mode": "watchdog", "watchdogStatus": watchdog_status},
            )
            compare_freshness = _current_compare_freshness(campaign_root, status)
            write_watchdog_state(
                state_path,
                {
                    "schemaVersion": 1,
                    "campaignId": status.get("campaignId", campaign_root.name),
                    "campaignRoot": str(campaign_root),
                    "updatedAt": utc_now_iso(),
                    "sessionId": session_id,
                    "restartCount": restart_count,
                    "childPid": launch_result.process.pid if launch_result.process.poll() is None else None,
                    "mode": launch_result.mode,
                    "watchdogStatus": watchdog_status,
                    "budgetExhausted": budget_exhausted,
                    "lastFingerprint": fingerprint,
                    "campaignSnapshot": snapshot,
                    "runCounts": snapshot["runCounts"],
                    "statusRunIds": snapshot["statusRunIds"],
                    "recoverableRunIds": snapshot["recoverableRunIds"],
                    "prewarmPlanCounts": snapshot["prewarmPlanCounts"],
                    "prewarmPendingRunIds": snapshot["prewarmPendingRunIds"],
                    "activeLaunches": snapshot["activeLaunches"],
                    "activeWorkRunIds": snapshot["activeWorkRunIds"],
                    "acceptedProofCount": snapshot["acceptedProofCount"],
                    "acceptedBlockerCount": snapshot["acceptedBlockerCount"],
                    "lastStatusRefreshAt": last_status_refresh_at,
                    "lastProgressAt": last_progress_at,
                    "lastRecoveryAt": last_recovery_at,
                    "lastCompareReportAt": last_compare_report_at,
                    "ownerLastLogAt": launch_result.output_state.last_output_at,
                    "stallReason": stall_reason,
                    "launchBudget": {
                        "maxActiveLaunches": max_active_launches,
                        "launchBatchSize": launch_batch_size,
                        "launchCooldownSeconds": launch_cooldown_seconds,
                    },
                    "manualInterventions": manual_interventions,
                    "reportFreshness": compare_freshness,
                    "ownerLease": owner_lease,
                },
            )

            if launch_result.process.poll() is not None:
                _stop_output_mirror(launch_result)
                status = collect_campaign_status(campaign_root)
                last_status_refresh_at = utc_now_iso()
                fingerprint = campaign_progress_fingerprint(campaign_root, status)
                snapshot = watchdog_campaign_snapshot(status)
                last_compare_report_at = _refresh_compare_report(campaign_root, heartbeat_seconds=DEFAULT_HEARTBEAT_SECONDS)
                if is_campaign_terminal(status):
                    break
                if restart_count >= max_restarts:
                    stall_reason = "owner_exit"
                    watchdog_status = "degraded"
                    budget_exhausted = True
                    last_compare_report_at = _refresh_compare_report(campaign_root, heartbeat_seconds=DEFAULT_HEARTBEAT_SECONDS)
                    break
                restart_count += 1
                stall_reason = "owner_exit"
                resume_prompt = build_watchdog_resume_prompt(stalled=False)
                _write_watchdog_log(
                    launch_result.log_handle,
                    launch_result.output_state,
                    f"\n[watchdog] restarting orchestrator at {utc_now_iso()} after child exit; restart={restart_count}\n"
                )
                launch_result.log_handle.close()
                launch_result = launch_codex_session(
                    archon_root=archon_root,
                    prompt_path=prompt_path,
                    log_path=log_path,
                    model=model,
                    reasoning_effort=reasoning_effort,
                    session_id=session_id,
                    resume_prompt=resume_prompt,
                )
                session_id = launch_result.session_id or session_id
                progress_at = time.monotonic()

            time.sleep(max(1, poll_seconds))
            status = collect_campaign_status(campaign_root)
            last_status_refresh_at = utc_now_iso()
            new_fingerprint = campaign_progress_fingerprint(campaign_root, status)
            snapshot = watchdog_campaign_snapshot(status)
            if new_fingerprint != fingerprint:
                fingerprint = new_fingerprint
                progress_at = time.monotonic()
                last_progress_at = utc_now_iso()
                stall_reason = None
                bootstrap_done = False
                last_compare_report_at = _refresh_compare_report(campaign_root, heartbeat_seconds=DEFAULT_HEARTBEAT_SECONDS)
            elif (
                not bootstrap_done
                and bootstrap_launch_after_seconds > 0
                and time.monotonic() - progress_at >= bootstrap_launch_after_seconds
                and not campaign_has_live_work(status)
            ):
                recoverable_run_ids = select_automatic_recovery_run_ids(
                    status,
                    max_active_launches=max_active_launches,
                    launch_batch_size=launch_batch_size,
                    launch_cooldown_seconds=launch_cooldown_seconds,
                )
                if recoverable_run_ids:
                    _cleanup_stale_launchers_best_effort(
                        campaign_root=campaign_root,
                        log_handle=launch_result.log_handle,
                        output_state=launch_result.output_state,
                        heartbeat_seconds=DEFAULT_HEARTBEAT_SECONDS,
                    )
                    for run_id in recoverable_run_ids:
                        execute_run_recovery(campaign_root, run_id, execute=True)
                    _write_watchdog_log(
                        launch_result.log_handle,
                        launch_result.output_state,
                        f"\n[watchdog] executed automatic recoveries at {utc_now_iso()} for runs: {', '.join(recoverable_run_ids)}\n"
                    )
                    status = collect_campaign_status(campaign_root)
                    fingerprint = campaign_progress_fingerprint(campaign_root, status)
                    snapshot = watchdog_campaign_snapshot(status)
                    progress_at = time.monotonic()
                    last_progress_at = utc_now_iso()
                    last_recovery_at = utc_now_iso()
                    last_compare_report_at = _refresh_compare_report(campaign_root, heartbeat_seconds=DEFAULT_HEARTBEAT_SECONDS)
                    stall_reason = None
                    bootstrap_done = True
            elif campaign_has_live_work(status):
                recoverable_run_ids = select_automatic_recovery_run_ids(
                    status,
                    max_active_launches=max_active_launches,
                    launch_batch_size=launch_batch_size,
                    launch_cooldown_seconds=launch_cooldown_seconds,
                    include_recovery_only=False,
                )
                if recoverable_run_ids:
                    _cleanup_stale_launchers_best_effort(
                        campaign_root=campaign_root,
                        log_handle=launch_result.log_handle,
                        output_state=launch_result.output_state,
                        heartbeat_seconds=DEFAULT_HEARTBEAT_SECONDS,
                    )
                    for run_id in recoverable_run_ids:
                        execute_run_recovery(campaign_root, run_id, execute=True)
                    _write_watchdog_log(
                        launch_result.log_handle,
                        launch_result.output_state,
                        f"\n[watchdog] topped up automatic launches at {utc_now_iso()} for runs: {', '.join(recoverable_run_ids)}\n",
                    )
                    status = collect_campaign_status(campaign_root)
                    fingerprint = campaign_progress_fingerprint(campaign_root, status)
                    snapshot = watchdog_campaign_snapshot(status)
                    progress_at = time.monotonic()
                    last_progress_at = utc_now_iso()
                    last_recovery_at = utc_now_iso()
                    last_compare_report_at = _refresh_compare_report(campaign_root, heartbeat_seconds=DEFAULT_HEARTBEAT_SECONDS)
                    stall_reason = None
                    continue
            elif time.monotonic() - progress_at >= stall_seconds:
                recoverable_run_ids = select_automatic_recovery_run_ids(
                    status,
                    max_active_launches=max_active_launches,
                    launch_batch_size=launch_batch_size,
                    launch_cooldown_seconds=launch_cooldown_seconds,
                )
                if recoverable_run_ids:
                    _cleanup_stale_launchers_best_effort(
                        campaign_root=campaign_root,
                        log_handle=launch_result.log_handle,
                        output_state=launch_result.output_state,
                        heartbeat_seconds=DEFAULT_HEARTBEAT_SECONDS,
                    )
                    for run_id in recoverable_run_ids:
                        execute_run_recovery(campaign_root, run_id, execute=True)
                    _write_watchdog_log(
                        launch_result.log_handle,
                        launch_result.output_state,
                        f"\n[watchdog] executed bounded recoveries at {utc_now_iso()} for runs: {', '.join(recoverable_run_ids)}\n",
                    )
                    status = collect_campaign_status(campaign_root)
                    fingerprint = campaign_progress_fingerprint(campaign_root, status)
                    snapshot = watchdog_campaign_snapshot(status)
                    progress_at = time.monotonic()
                    last_progress_at = utc_now_iso()
                    last_recovery_at = utc_now_iso()
                    last_compare_report_at = _refresh_compare_report(campaign_root, heartbeat_seconds=DEFAULT_HEARTBEAT_SECONDS)
                    stall_reason = None
                    continue

                owner_last_output = launch_result.output_state.last_output_monotonic
                owner_silent = (
                    owner_silence_seconds > 0
                    and (
                        owner_last_output is None
                        or time.monotonic() - owner_last_output >= owner_silence_seconds
                    )
                )
                if owner_silent:
                    if restart_count >= max_restarts:
                        stall_reason = "campaign_stall"
                        watchdog_status = "degraded"
                        budget_exhausted = True
                        last_compare_report_at = _refresh_compare_report(campaign_root, heartbeat_seconds=DEFAULT_HEARTBEAT_SECONDS)
                        break
                    restart_count += 1
                    stall_reason = "campaign_stall"
                    _write_watchdog_log(
                        launch_result.log_handle,
                        launch_result.output_state,
                        f"\n[watchdog] terminating stalled orchestrator at {utc_now_iso()}; restart={restart_count}\n",
                    )
                    terminate_process_group(launch_result.process)
                    _stop_output_mirror(launch_result)
                    launch_result.log_handle.close()
                    resume_prompt = build_watchdog_resume_prompt(stalled=True)
                    launch_result = launch_codex_session(
                        archon_root=archon_root,
                        prompt_path=prompt_path,
                        log_path=log_path,
                        model=model,
                        reasoning_effort=reasoning_effort,
                        session_id=session_id,
                        resume_prompt=resume_prompt,
                    )
                    session_id = launch_result.session_id or session_id
                    progress_at = time.monotonic()
                else:
                    stall_reason = "campaign_idle"
                    last_compare_report_at = _refresh_compare_report(campaign_root, heartbeat_seconds=DEFAULT_HEARTBEAT_SECONDS)

            if is_campaign_terminal(status):
                watchdog_status = "terminal"
                break

        if finalize_on_terminal and is_campaign_terminal(status):
            finalize_campaign(campaign_root)
            status = collect_campaign_status(campaign_root)
            snapshot = watchdog_campaign_snapshot(status)
            last_compare_report_at = utc_now_iso()
        elif not is_campaign_terminal(status):
            last_compare_report_at = _refresh_compare_report(campaign_root, heartbeat_seconds=DEFAULT_HEARTBEAT_SECONDS)
    except BaseException as exc:
        watchdog_status = "degraded"
        stall_reason = f"watchdog_exception:{type(exc).__name__}"
        watchdog_error = {"type": type(exc).__name__, "message": str(exc)}
        try:
            status = collect_campaign_status(campaign_root)
            snapshot = watchdog_campaign_snapshot(status)
            fingerprint = campaign_progress_fingerprint(campaign_root, status)
            last_status_refresh_at = utc_now_iso()
        except (FileNotFoundError, OSError, RuntimeError, ValueError):
            pass
        try:
            last_compare_report_at = _refresh_compare_report(campaign_root, heartbeat_seconds=DEFAULT_HEARTBEAT_SECONDS)
        except (FileNotFoundError, OSError, RuntimeError, ValueError):
            pass
        if launch_result is not None:
            _write_watchdog_log(
                launch_result.log_handle,
                launch_result.output_state,
                f"\n[watchdog] exception at {utc_now_iso()}: {type(exc).__name__}: {exc}\n",
            )
    finally:
        if launch_result is not None:
            terminate_process_group(launch_result.process)
            _stop_output_mirror(launch_result)
            launch_result.log_handle.close()

    compare_freshness = _current_compare_freshness(campaign_root, status)
    owner_lease = release_owner_lease(
        campaign_root,
        owner_entrypoint=owner_entrypoint,
        owner_pid=owner_pid,
        session_id=session_id,
        child_pid=None,
        release_reason=watchdog_status,
        metadata={"mode": "watchdog", "stallReason": stall_reason},
    )
    result = {
        "schemaVersion": 1,
        "campaignId": status.get("campaignId", campaign_root.name),
        "campaignRoot": str(campaign_root),
        "sessionId": session_id,
        "restartCount": restart_count,
        "watchdogStatus": watchdog_status,
        "budgetExhausted": budget_exhausted,
        "terminal": is_campaign_terminal(status),
        "finalized": finalize_on_terminal and (campaign_root / "reports" / "final" / "final-summary.json").exists(),
        "runCounts": snapshot["runCounts"],
        "statusRunIds": snapshot["statusRunIds"],
        "recoverableRunIds": snapshot["recoverableRunIds"],
        "prewarmPlanCounts": snapshot["prewarmPlanCounts"],
        "prewarmPendingRunIds": snapshot["prewarmPendingRunIds"],
        "activeLaunches": snapshot["activeLaunches"],
        "activeWorkRunIds": snapshot["activeWorkRunIds"],
        "acceptedProofCount": snapshot["acceptedProofCount"],
        "acceptedBlockerCount": snapshot["acceptedBlockerCount"],
        "lastStatusRefreshAt": last_status_refresh_at,
        "lastProgressAt": last_progress_at,
        "lastRecoveryAt": last_recovery_at,
        "lastCompareReportAt": last_compare_report_at,
        "ownerLastLogAt": launch_result.output_state.last_output_at if launch_result is not None else None,
        "stallReason": stall_reason,
        "launchBudget": {
            "maxActiveLaunches": max_active_launches,
            "launchBatchSize": launch_batch_size,
            "launchCooldownSeconds": launch_cooldown_seconds,
        },
        "manualInterventions": manual_interventions,
        "reportFreshness": compare_freshness,
        "ownerLease": owner_lease,
    }
    if watchdog_error is not None:
        result["watchdogError"] = watchdog_error
    write_watchdog_state(state_path, {**result, "campaignSnapshot": snapshot, "updatedAt": utc_now_iso()})
    return result
