from __future__ import annotations

import json
from pathlib import Path

from archonlib.supervisor import HeaderDrift


SCHEMA_VERSION = 1


def _task_result_name(rel_path: str) -> str:
    return rel_path.replace("/", "_") + ".md"


def classify_task_result(task_result_path: Path | None) -> tuple[bool, str | None]:
    if task_result_path is None or not task_result_path.exists():
        return False, None
    text = task_result_path.read_text(encoding="utf-8").lower()
    if any(marker in text for marker in ("concrete blocker:", "validated blocker", "genuine blocker")):
        return True, "blocker"
    if "**result:** resolved" in text:
        return True, "resolved"
    return False, "other"


def _validation_filename(rel_path: str) -> str:
    return rel_path.replace("/", "_") + ".json"


def _collect_targets(
    *,
    allowed_files: list[str],
    changed_files: list[str],
    drifts: list[HeaderDrift],
    prover_failures: list[str],
) -> list[str]:
    targets = set(allowed_files) | set(changed_files) | {drift.rel_path for drift in drifts} | set(prover_failures)
    return sorted(targets)


def _acceptance_status(
    *,
    overall_status: str,
    drift: HeaderDrift | None,
    workspace_changed: bool,
    durable_task_result: bool,
) -> str:
    if drift is not None:
        return "rejected"
    if overall_status == "clean" and (workspace_changed or durable_task_result):
        return "accepted"
    if overall_status == "no_progress":
        return "none"
    return "pending"


def _validation_status(
    *,
    overall_status: str,
    drift: HeaderDrift | None,
    acceptance_status: str,
) -> str:
    if drift is not None:
        return "failed"
    if overall_status == "no_progress":
        return "no_progress"
    if acceptance_status == "accepted":
        return "passed"
    return "attention"


def write_validation_artifacts(
    workspace: Path,
    *,
    status: str,
    allowed_files: list[str],
    changed_files: list[str],
    drifts: list[HeaderDrift],
    prover_failures: list[str],
    iteration: str | None,
    loop_exit_code: int,
    recovered_after_stall: dict[str, object] | None = None,
) -> list[str]:
    validation_root = workspace / ".archon" / "validation"
    validation_root.mkdir(parents=True, exist_ok=True)

    drifts_by_path = {drift.rel_path: drift for drift in drifts}
    written: list[str] = []
    for rel_path in _collect_targets(
        allowed_files=allowed_files,
        changed_files=changed_files,
        drifts=drifts,
        prover_failures=prover_failures,
    ):
        task_result_path = workspace / ".archon" / "task_results" / _task_result_name(rel_path)
        durable_task_result, task_result_kind = classify_task_result(task_result_path)
        drift = drifts_by_path.get(rel_path)
        workspace_changed = rel_path in changed_files
        task_result_name = task_result_path.name if task_result_path.exists() else None
        acceptance_status = _acceptance_status(
            overall_status=status,
            drift=drift,
            workspace_changed=workspace_changed,
            durable_task_result=durable_task_result,
        )
        validation_status = _validation_status(
            overall_status=status,
            drift=drift,
            acceptance_status=acceptance_status,
        )
        blocker_notes = [task_result_name] if task_result_kind == "blocker" and task_result_name else []
        task_result_kinds = {task_result_name: task_result_kind} if task_result_name and task_result_kind else {}
        header_drifts = [drift.to_event()] if drift is not None else []
        recovery_event = recovered_after_stall.get("event") if isinstance(recovered_after_stall, dict) else None

        payload = {
            "schemaVersion": SCHEMA_VERSION,
            "relPath": rel_path,
            "status": status,
            "acceptanceStatus": acceptance_status,
            "validationStatus": validation_status,
            "statementFidelity": "violated" if drift is not None else "preserved",
            "iteration": iteration,
            "overallStatus": status,
            "loopExitCode": loop_exit_code,
            "recoveryEvent": recovery_event if isinstance(recovery_event, str) else None,
            "headerDrifts": header_drifts,
            "blockerNotes": blocker_notes,
            "taskResultKinds": task_result_kinds,
            "checks": {
                "headerDrift": drift.mutation_class if drift is not None else "none",
                "workspaceChanged": workspace_changed,
                "taskResult": {
                    "present": task_result_path.exists(),
                    "durable": durable_task_result,
                    "kind": task_result_kind,
                    "path": (
                        task_result_path.relative_to(workspace).as_posix() if task_result_path.exists() else None
                    ),
                },
                "proverError": rel_path in prover_failures,
            },
            "sources": [
                ".archon/RUN_SCOPE.md",
                ".archon/task_results/",
                ".archon/logs/",
            ],
        }
        filename = _validation_filename(rel_path)
        (validation_root / filename).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        written.append(filename)
    return written
