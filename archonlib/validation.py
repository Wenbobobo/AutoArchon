from __future__ import annotations

import json
from pathlib import Path

from archonlib.formalization import assess_formalization
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


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


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


def _should_preserve_previous_acceptance(
    previous_payload: dict | None,
    *,
    drift: HeaderDrift | None,
    workspace_changed: bool,
    durable_task_result: bool,
    task_result_kind: str | None,
    formalization_fidelity: str,
    prover_error: bool,
) -> bool:
    if not isinstance(previous_payload, dict):
        return False
    if previous_payload.get("acceptanceStatus") != "accepted":
        return False
    if previous_payload.get("validationStatus") != "passed":
        return False
    if drift is not None or prover_error:
        return False
    if task_result_kind == "blocker":
        return False
    if formalization_fidelity in {"partial", "violated"}:
        return False
    if workspace_changed or durable_task_result:
        return True
    blocker_notes = previous_payload.get("blockerNotes")
    return isinstance(blocker_notes, list) and bool(blocker_notes)


def _accepted_kind(*, acceptance_status: str, task_result_kind: str | None) -> str:
    if acceptance_status != "accepted":
        return "none"
    if task_result_kind == "blocker":
        return "blocker"
    return "proof"


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
        filename = _validation_filename(rel_path)
        previous_payload = _read_json(validation_root / filename)
        task_result_path = workspace / ".archon" / "task_results" / _task_result_name(rel_path)
        durable_task_result, task_result_kind = classify_task_result(task_result_path)
        drift = drifts_by_path.get(rel_path)
        workspace_changed = rel_path in changed_files
        task_result_name = task_result_path.name if task_result_path.exists() else None
        prover_error = rel_path in prover_failures
        formalization = assess_formalization(workspace, rel_path)
        formalization_fidelity = str(formalization.get("fidelity") or "not_applicable")
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
        task_result_payload = {
            "present": task_result_path.exists(),
            "durable": durable_task_result,
            "kind": task_result_kind,
            "path": (
                task_result_path.relative_to(workspace).as_posix() if task_result_path.exists() else None
            ),
        }

        if _should_preserve_previous_acceptance(
            previous_payload,
            drift=drift,
            workspace_changed=workspace_changed,
            durable_task_result=durable_task_result,
            task_result_kind=task_result_kind,
            formalization_fidelity=formalization_fidelity,
            prover_error=prover_error,
        ):
            acceptance_status = "accepted"
            validation_status = "passed"
            if not blocker_notes:
                previous_blocker_notes = previous_payload.get("blockerNotes")
                if isinstance(previous_blocker_notes, list):
                    blocker_notes = [str(item) for item in previous_blocker_notes if isinstance(item, str)]
            if not task_result_kinds:
                previous_task_result_kinds = previous_payload.get("taskResultKinds")
                if isinstance(previous_task_result_kinds, dict):
                    task_result_kinds = {
                        str(name): str(kind)
                        for name, kind in previous_task_result_kinds.items()
                        if isinstance(name, str) and isinstance(kind, str)
                    }
            previous_checks = previous_payload.get("checks")
            if isinstance(previous_checks, dict):
                previous_task_result = previous_checks.get("taskResult")
                if isinstance(previous_task_result, dict) and not task_result_payload["present"]:
                    task_result_payload = dict(previous_task_result)

        if formalization_fidelity in {"partial", "violated"} and acceptance_status == "accepted":
            acceptance_status = "pending"
            validation_status = "attention" if status != "no_progress" else "no_progress"

        accepted_kind = _accepted_kind(acceptance_status=acceptance_status, task_result_kind=task_result_kind)
        payload = {
            "schemaVersion": SCHEMA_VERSION,
            "relPath": rel_path,
            "status": status,
            "acceptanceStatus": acceptance_status,
            "acceptedKind": accepted_kind,
            "validationStatus": validation_status,
            "statementFidelity": "violated" if drift is not None else "preserved",
            "formalizationFidelity": formalization_fidelity,
            "formalizationContract": formalization.get("contract"),
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
                "taskResult": task_result_payload,
                "proverError": prover_error,
            },
            "sources": [
                ".archon/RUN_SCOPE.md",
                ".archon/task_results/",
                ".archon/logs/",
            ],
        }
        (validation_root / filename).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        written.append(filename)
    return written
