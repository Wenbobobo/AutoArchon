from __future__ import annotations

import json
from pathlib import Path

from archonlib.supervisor import HeaderDrift


SCHEMA_VERSION = 1


def _load_validation_payloads(workspace: Path, validation_files: list[str]) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    for name in validation_files:
        path = workspace / ".archon" / "validation" / name
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            payloads.append(payload)
    return payloads


def _append_lesson(lessons: list[dict[str, object]], *, category: str, summary: str, evidence: list[str]) -> None:
    lessons.append(
        {
            "category": category,
            "summary": summary,
            "evidence": evidence,
        }
    )


def write_lesson_artifact(
    workspace: Path,
    *,
    status: str,
    iteration: str | None,
    allowed_files: list[str],
    validation_files: list[str],
    drifts: list[HeaderDrift],
    prover_failures: list[str],
    recovered_after_stall: dict[str, object] | None,
) -> str | None:
    signals: list[str] = []
    if drifts:
        signals.append("header_mutation")
    if prover_failures:
        signals.append("prover_error")
    if recovered_after_stall is not None:
        event = recovered_after_stall.get("event")
        if isinstance(event, str):
            signals.append(event)
    if status == "no_progress":
        signals.append("no_progress")
    if not signals and not validation_files:
        return None

    validation_payloads = _load_validation_payloads(workspace, validation_files)
    blocker_validation_files = [
        payload.get("relPath")
        for payload in validation_payloads
        if isinstance(payload.get("blockerNotes"), list) and payload.get("blockerNotes")
    ]
    lessons: list[dict[str, object]] = []
    if drifts:
        _append_lesson(
            lessons,
            category="theorem_fidelity",
            summary="Freeze the original theorem header and discard mutated attempts instead of repairing contaminated statements in place.",
            evidence=sorted({drift.rel_path for drift in drifts}),
        )
    if any(
        signal in {"verified_after_idle", "verified_after_stall", "verified_in_recovery", "synthesized_blocker_after_idle"}
        for signal in signals
    ):
        _append_lesson(
            lessons,
            category="idle_recovery",
            summary="If durable evidence already exists when the prover stalls, preserve and reuse it in the next planning pass instead of rerunning the same search.",
            evidence=validation_files,
        )
    if blocker_validation_files:
        _append_lesson(
            lessons,
            category="blocker_discipline",
            summary="When a theorem is false or underspecified, keep the original statement frozen and accept a durable blocker note before any optional helper theorem work.",
            evidence=[str(item) for item in blocker_validation_files if isinstance(item, str)],
        )
    if status == "no_progress":
        _append_lesson(
            lessons,
            category="scope_control",
            summary="When a cycle produces no new changed files or task results, tighten the scope or reduce time budgets before the next attempt.",
            evidence=allowed_files,
        )
    if prover_failures and not any(signal in {"verified_after_idle", "verified_after_stall"} for signal in signals):
        _append_lesson(
            lessons,
            category="prover_failure",
            summary="Treat prover errors as untrusted until a changed file or durable task result has been independently re-verified.",
            evidence=prover_failures,
        )

    lessons_root = workspace / ".archon" / "lessons"
    lessons_root.mkdir(parents=True, exist_ok=True)
    slug = (iteration or "iter-none").replace("/", "_")
    filename = f"{slug}-{status}.json"
    payload = {
        "schemaVersion": SCHEMA_VERSION,
        "status": status,
        "iteration": iteration,
        "allowedFiles": allowed_files,
        "validationFiles": validation_files,
        "signals": signals,
        "lessons": lessons,
        "recommendedAction": _recommended_action(status, signals),
    }
    (lessons_root / filename).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return filename


def _recommended_action(status: str, signals: list[str]) -> str:
    if "header_mutation" in signals:
        return "Freeze the theorem header, discard the mutated attempt, and restart from the original statement."
    if "verified_after_idle" in signals or "verified_after_stall" in signals or "verified_in_recovery" in signals:
        return "Preserve the durable artifact and feed it back into the next planning pass instead of rerunning the same search."
    if status == "no_progress":
        return "Tighten scope or lower timeouts before the next cycle."
    if "prover_error" in signals:
        return "Inspect the failing prover log and keep only verified artifacts."
    return "Record the outcome and continue with the next scoped iteration."
