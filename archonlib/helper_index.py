from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


SCHEMA_VERSION = 1


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_reason(reason: object) -> str | None:
    if not isinstance(reason, str):
        return None
    lowered = reason.strip().lower().replace("-", "_").replace(" ", "_")
    lowered = re.sub(r"[^a-z0-9_]+", "_", lowered).strip("_")
    return lowered or None


def _relative_to_workspace(path: Path, workspace: Path) -> str:
    try:
        return path.resolve().relative_to(workspace.resolve()).as_posix()
    except ValueError:
        return str(path)


def helper_index_path(workspace: Path) -> Path:
    return workspace / ".archon" / "informal" / "helper" / "helper-index.json"


def load_helper_index(workspace: Path) -> dict[str, Any]:
    path = helper_index_path(workspace)
    if not path.exists():
        return {"schemaVersion": SCHEMA_VERSION, "entries": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"schemaVersion": SCHEMA_VERSION, "entries": []}
    if not isinstance(payload, dict):
        return {"schemaVersion": SCHEMA_VERSION, "entries": []}
    entries = payload.get("entries")
    if not isinstance(entries, list):
        entries = []
    return {
        "schemaVersion": SCHEMA_VERSION,
        "entries": [entry for entry in entries if isinstance(entry, dict)],
    }


def write_helper_index(workspace: Path, payload: Mapping[str, Any]) -> Path:
    path = helper_index_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def append_helper_index_event(
    workspace: Path,
    *,
    event: str,
    phase: str | None,
    rel_path: str | None,
    reason: str | None,
    prompt_pack: str | None,
    provider: str | None,
    model: str | None,
    note_path: Path | str | None = None,
    reused_from: Path | str | None = None,
    iteration: int | None = None,
    attempt: int | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload = load_helper_index(workspace)

    def _path_value(value: Path | str | None) -> str | None:
        if value is None:
            return None
        resolved = Path(value).resolve() if isinstance(value, Path) else Path(str(value)).resolve()
        return _relative_to_workspace(resolved, workspace)

    entry: dict[str, Any] = {
        "createdAt": _utc_now(),
        "event": event,
        "phase": phase,
        "relPath": rel_path,
        "reason": _normalize_reason(reason),
        "promptPack": prompt_pack,
        "provider": provider,
        "model": model,
        "notePath": _path_value(note_path),
        "reusedFrom": _path_value(reused_from),
        "iteration": iteration,
        "attempt": attempt,
    }
    if isinstance(metadata, Mapping):
        for key, value in metadata.items():
            entry[str(key)] = value
    payload["entries"].append(entry)
    write_helper_index(workspace, payload)
    return entry


def helper_index_entries(workspace: Path) -> list[dict[str, Any]]:
    return list(load_helper_index(workspace).get("entries", []))


def summarize_helper_index(entries: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    materialized = [dict(entry) for entry in entries if isinstance(entry, Mapping)]
    count_by_event = Counter()
    fresh_calls_by_reason = Counter()
    reuse_by_reason = Counter()
    blocked_by_budget_by_reason = Counter()
    blocked_by_cooldown_by_reason = Counter()
    latest_event_by_key: dict[tuple[str, str, str], str] = {}

    for entry in materialized:
        event = entry.get("event")
        if not isinstance(event, str) or not event:
            continue
        count_by_event[event] += 1
        phase = entry.get("phase") if isinstance(entry.get("phase"), str) else "unknown"
        reason = _normalize_reason(entry.get("reason")) or "(none)"
        rel_path = entry.get("relPath") if isinstance(entry.get("relPath"), str) and entry.get("relPath") else "(none)"
        latest_event_by_key[(phase, reason, rel_path)] = event
        if reason == "(none)":
            continue
        if event == "provider_call":
            fresh_calls_by_reason[reason] += 1
        elif event == "note_reuse":
            reuse_by_reason[reason] += 1
        elif event == "skipped_by_budget":
            blocked_by_budget_by_reason[reason] += 1
        elif event == "skipped_by_cooldown":
            blocked_by_cooldown_by_reason[reason] += 1

    cooldown_active_reasons = [
        {"phase": phase, "reason": reason, "relPath": rel_path}
        for (phase, reason, rel_path), event in sorted(latest_event_by_key.items())
        if event == "skipped_by_cooldown"
    ]

    return {
        "entryCount": len(materialized),
        "eventCounts": dict(sorted(count_by_event.items())),
        "freshCallCount": int(count_by_event.get("provider_call", 0)),
        "noteReuseCount": int(count_by_event.get("note_reuse", 0)),
        "blockedByBudgetCount": int(count_by_event.get("skipped_by_budget", 0)),
        "blockedByCooldownCount": int(count_by_event.get("skipped_by_cooldown", 0)),
        "freshCallsByReason": dict(sorted(fresh_calls_by_reason.items())),
        "reuseByReason": dict(sorted(reuse_by_reason.items())),
        "blockedByBudgetByReason": dict(sorted(blocked_by_budget_by_reason.items())),
        "blockedByCooldownByReason": dict(sorted(blocked_by_cooldown_by_reason.items())),
        "cooldownActiveReasons": cooldown_active_reasons,
    }
