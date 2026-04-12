from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

from archonlib.campaign import collect_campaign_status, execute_run_recovery, finalize_campaign


TERMINAL_RUN_STATUSES = {"accepted", "blocked", "contaminated"}
AUTOMATIC_RECOVERY_ACTIONS = {"launch_teacher", "relaunch_teacher", "recovery_only"}
SESSION_ID_RE = re.compile(r"session id:\s*([0-9a-f-]{36})", re.IGNORECASE)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    run_pairs: list[tuple[str, str]] = []
    if isinstance(runs, list):
        for run in runs:
            if not isinstance(run, dict):
                continue
            run_id = run.get("runId")
            status = run.get("status")
            if isinstance(run_id, str) and isinstance(status, str):
                run_pairs.append((run_id, status))

    event_lines = 0
    events_path = campaign_root / "events.jsonl"
    if events_path.exists():
        with events_path.open("r", encoding="utf-8") as handle:
            for event_lines, _ in enumerate(handle, start=1):
                pass

    activity_files = 0
    for pattern in (
        "runs/*/workspace/.archon/supervisor/run-lease.json",
        "runs/*/workspace/.archon/supervisor/HOT_NOTES.md",
        "runs/*/workspace/.archon/supervisor/LEDGER.md",
        "runs/*/control/teacher-launch.stdout.log",
        "runs/*/control/teacher-launch.stderr.log",
    ):
        activity_files += sum(1 for path in campaign_root.glob(pattern) if path.is_file())

    return {
        "runStatusPairs": sorted(run_pairs),
        "eventLines": event_lines,
        "activityFiles": activity_files,
    }


def automatic_recovery_run_ids(status_payload: dict[str, Any]) -> list[str]:
    runs = status_payload.get("runs")
    if not isinstance(runs, list):
        return []
    run_ids: list[str] = []
    for run in runs:
        if not isinstance(run, dict):
            continue
        recovery = run.get("recommendedRecovery")
        if not isinstance(recovery, dict) or recovery.get("action") not in AUTOMATIC_RECOVERY_ACTIONS:
            continue
        run_id = run.get("runId")
        if not isinstance(run_id, str) or not run_id:
            continue
        run_ids.append(run_id)
    return run_ids


def write_watchdog_state(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


@dataclass
class LaunchResult:
    process: subprocess.Popen[str]
    log_handle: TextIO
    session_id: str | None
    mode: str
    command: list[str]


def build_default_orchestrator_prompt(*, archon_root: Path, campaign_root: Path) -> str:
    recover_cmd = (
        f"uv run --directory {archon_root} autoarchon-campaign-recover "
        f"--campaign-root {campaign_root} --all-recoverable --execute"
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
            "- inspect CAMPAIGN_MANIFEST.json, campaign-status.json, and recommendedRecovery before acting",
            "- launch teachers only from runs/<id>/control/launch-teacher.sh",
            f"- prefer deterministic recovery via {recover_cmd} over ad hoc shell logic",
            "- keep teachers on disjoint run roots",
            "- finalize only validated proofs and accepted blocker notes",
            "",
            "Stop only when:",
            "- all runs are in terminal states and reports/final/ is up to date, or",
            "- a hard external dependency prevents safe continuation",
            "",
        ]
    )


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

    parsed_session_id = session_id
    assert proc.stdout is not None
    start_deadline = time.monotonic() + 30.0
    while time.monotonic() < start_deadline:
        line = proc.stdout.readline()
        if not line:
            if proc.poll() is not None:
                break
            time.sleep(0.1)
            continue
        log_handle.write(line)
        log_handle.flush()
        match = SESSION_ID_RE.search(line)
        if match:
            parsed_session_id = match.group(1)
            break

    return LaunchResult(
        process=proc,
        log_handle=log_handle,
        session_id=parsed_session_id,
        mode=mode,
        command=command,
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


def drain_process_output(proc: subprocess.Popen[str], log_handle: TextIO) -> None:
    stdout = proc.stdout
    if stdout is None:
        return
    for line in stdout:
        log_handle.write(line)
    log_handle.flush()


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
    finalize_on_terminal: bool = True,
) -> dict[str, Any]:
    status = collect_campaign_status(campaign_root)
    fingerprint = campaign_progress_fingerprint(campaign_root, status)
    session_id: str | None = None
    restart_count = 0
    bootstrap_done = False
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
        write_watchdog_state(
            state_path,
            {
                "schemaVersion": 1,
                "campaignRoot": str(campaign_root),
                "updatedAt": utc_now_iso(),
                "sessionId": session_id,
                "restartCount": restart_count,
                "childPid": launch_result.process.pid if launch_result.process.poll() is None else None,
                "mode": launch_result.mode,
                "lastFingerprint": fingerprint,
            },
        )

        if launch_result.process.poll() is not None:
            drain_process_output(launch_result.process, launch_result.log_handle)
            if is_campaign_terminal(status):
                break
            if restart_count >= max_restarts:
                raise RuntimeError("orchestrator watchdog exhausted restart budget before terminal campaign closure")
            restart_count += 1
            resume_prompt = (
                "Continue owning this AutoArchon campaign. "
                "Refresh campaign truth from campaign-status.json and recommendedRecovery before acting. "
                "If all runs are already terminal, finalize the campaign."
            )
            launch_result.log_handle.write(
                f"\n[watchdog] restarting orchestrator at {utc_now_iso()} after child exit; restart={restart_count}\n"
            )
            launch_result.log_handle.flush()
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
        new_fingerprint = campaign_progress_fingerprint(campaign_root, status)
        if new_fingerprint != fingerprint:
            fingerprint = new_fingerprint
            progress_at = time.monotonic()
            bootstrap_done = False
        elif (
            not bootstrap_done
            and bootstrap_launch_after_seconds > 0
            and time.monotonic() - progress_at >= bootstrap_launch_after_seconds
            and new_fingerprint.get("activityFiles") == 0
        ):
            recoverable_run_ids = automatic_recovery_run_ids(status)
            if recoverable_run_ids:
                for run_id in recoverable_run_ids:
                    execute_run_recovery(campaign_root, run_id, execute=True)
                launch_result.log_handle.write(
                    f"\n[watchdog] executed automatic recoveries at {utc_now_iso()} for runs: {', '.join(recoverable_run_ids)}\n"
                )
                launch_result.log_handle.flush()
                status = collect_campaign_status(campaign_root)
                fingerprint = campaign_progress_fingerprint(campaign_root, status)
                progress_at = time.monotonic()
                bootstrap_done = True
        elif time.monotonic() - progress_at >= stall_seconds:
            if restart_count >= max_restarts:
                raise RuntimeError("orchestrator watchdog detected a stalled campaign and exhausted restart budget")
            restart_count += 1
            launch_result.log_handle.write(
                f"\n[watchdog] terminating stalled orchestrator at {utc_now_iso()}; restart={restart_count}\n"
            )
            launch_result.log_handle.flush()
            terminate_process_group(launch_result.process)
            drain_process_output(launch_result.process, launch_result.log_handle)
            launch_result.log_handle.close()
            resume_prompt = (
                "Continue owning this AutoArchon campaign after a stalled outer session. "
                "Refresh campaign truth from campaign-status.json and recommendedRecovery, "
                "launch or recover only what is still needed, and finalize when terminal."
            )
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

        if is_campaign_terminal(status):
            break

    terminate_process_group(launch_result.process)
    drain_process_output(launch_result.process, launch_result.log_handle)
    launch_result.log_handle.close()

    if finalize_on_terminal:
        finalize_campaign(campaign_root)
        status = collect_campaign_status(campaign_root)

    result = {
        "schemaVersion": 1,
        "campaignRoot": str(campaign_root),
        "sessionId": session_id,
        "restartCount": restart_count,
        "terminal": is_campaign_terminal(status),
        "finalized": finalize_on_terminal and (campaign_root / "reports" / "final" / "final-summary.json").exists(),
        "runCounts": status.get("counts", {}),
    }
    write_watchdog_state(state_path, {**result, "updatedAt": utc_now_iso()})
    return result
