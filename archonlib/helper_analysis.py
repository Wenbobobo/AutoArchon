from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from archonlib.helper_index import summarize_helper_index
from archonlib.lesson_clusters import load_lesson_records


SCHEMA_VERSION = 1


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _string(value: object, default: str) -> str:
    return value if isinstance(value, str) and value else default


def _int(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    return 0


def _sorted_counter(counter: Counter[str], *, top_n: int) -> list[dict[str, Any]]:
    rows = [{"value": key, "count": count} for key, count in counter.items()]
    rows.sort(key=lambda row: (-int(row["count"]), str(row["value"])))
    return rows[:top_n]


def _relative_to_root(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def _dedupe_paths(paths: Iterable[Path]) -> list[Path]:
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        unique.append(resolved)
        seen.add(resolved)
    return unique


def _load_helper_index_entries(paths: Iterable[Path]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in paths:
        payload = _read_json(path)
        if not isinstance(payload, dict):
            continue
        raw_entries = payload.get("entries")
        if not isinstance(raw_entries, list):
            continue
        for entry in raw_entries:
            if isinstance(entry, dict):
                entries.append(dict(entry))
    return entries


def _discover_summary_paths(campaign_root: Path) -> dict[str, Path | None]:
    final_summary = campaign_root / "reports" / "final" / "final-summary.json"
    postmortem_summary = campaign_root / "reports" / "postmortem" / "postmortem-summary.json"
    status_candidates = [
        campaign_root / "campaign-status.json",
        campaign_root / "reports" / "postmortem" / "campaign-status.snapshot.json",
    ]
    status_path = next((path for path in status_candidates if path.exists()), None)
    return {
        "campaignStatus": status_path,
        "finalSummary": final_summary if final_summary.exists() else None,
        "postmortemSummary": postmortem_summary if postmortem_summary.exists() else None,
    }


def _merge_run_rows(
    primary_rows: Iterable[Mapping[str, Any]],
    supplemental_rows: Iterable[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for row in list(primary_rows) + list(supplemental_rows):
        if not isinstance(row, Mapping):
            continue
        run_id = row.get("runId")
        if not isinstance(run_id, str) or not run_id:
            continue
        slot = merged.setdefault(run_id, {})
        for key, value in row.items():
            if key not in slot or slot.get(key) in (None, [], {}, ""):
                slot[key] = value
    return merged


def _run_terminal_category(run_row: Mapping[str, Any]) -> str:
    accepted_proofs = run_row.get("acceptedProofs")
    accepted_formalizations = run_row.get("acceptedFormalizations")
    accepted_blockers = run_row.get("acceptedBlockers")
    proof_count = len(accepted_proofs) if isinstance(accepted_proofs, list) else 0
    formalization_count = len(accepted_formalizations) if isinstance(accepted_formalizations, list) else 0
    blocker_count = len(accepted_blockers) if isinstance(accepted_blockers, list) else 0
    populated = [
        label
        for label, count in (
            ("accepted_proof", proof_count),
            ("accepted_formalization", formalization_count),
            ("accepted_blocker", blocker_count),
        )
        if count > 0
    ]
    if len(populated) == 1:
        return populated[0]
    if len(populated) > 1:
        return "mixed_finalized"
    return _string(run_row.get("status"), "unknown")


def _discover_run_progress_path(campaign_root: Path, run_id: str) -> Path | None:
    candidates = [
        campaign_root / "reports" / "final" / "supervisor" / run_id / "progress-summary.json",
        campaign_root / "runs" / run_id / "artifacts" / "supervisor" / "progress-summary.json",
        campaign_root / "runs" / run_id / "workspace" / ".archon" / "supervisor" / "progress-summary.json",
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def _discover_run_helper_index_paths(campaign_root: Path, run_id: str) -> list[Path]:
    run_root = campaign_root / "runs" / run_id
    candidates = [
        run_root / "artifacts" / "informal" / "helper" / "helper-index.json",
        run_root / "workspace" / ".archon" / "informal" / "helper" / "helper-index.json",
    ]
    candidates.extend(sorted(run_root.glob("artifacts/relaunch_archived/*/informal/helper/helper-index.json")))
    candidates.extend(sorted(run_root.glob("workspace/.archon/relaunch_archived/*/informal/helper/helper-index.json")))
    return _dedupe_paths(path for path in candidates if path.exists())


def _helper_summary_from_progress(progress_payload: Mapping[str, Any]) -> dict[str, Any]:
    helper = progress_payload.get("helper")
    if not isinstance(helper, Mapping):
        return {}
    return {
        "enabled": helper.get("enabled") is True,
        "noteCount": _int(helper.get("noteCount")),
        "countsByReason": dict(helper.get("countsByReason", {})) if isinstance(helper.get("countsByReason"), Mapping) else {},
        "countsByPhase": dict(helper.get("countsByPhase", {})) if isinstance(helper.get("countsByPhase"), Mapping) else {},
        "countsByPromptPack": (
            dict(helper.get("countsByPromptPack", {}))
            if isinstance(helper.get("countsByPromptPack"), Mapping)
            else {}
        ),
        "freshCallCount": _int(helper.get("freshCallCount")),
        "failedCallCount": _int(helper.get("failedCallCount")),
        "noteReuseCount": _int(helper.get("noteReuseCount")),
        "blockedByBudgetCount": _int(helper.get("blockedByBudgetCount")),
        "blockedByCooldownCount": _int(helper.get("blockedByCooldownCount")),
        "freshCallsByReason": (
            dict(helper.get("freshCallsByReason", {}))
            if isinstance(helper.get("freshCallsByReason"), Mapping)
            else {}
        ),
        "failedCallsByReason": (
            dict(helper.get("failedCallsByReason", {}))
            if isinstance(helper.get("failedCallsByReason"), Mapping)
            else {}
        ),
        "reuseByReason": dict(helper.get("reuseByReason", {})) if isinstance(helper.get("reuseByReason"), Mapping) else {},
        "blockedByBudgetByReason": (
            dict(helper.get("blockedByBudgetByReason", {}))
            if isinstance(helper.get("blockedByBudgetByReason"), Mapping)
            else {}
        ),
        "blockedByCooldownByReason": (
            dict(helper.get("blockedByCooldownByReason", {}))
            if isinstance(helper.get("blockedByCooldownByReason"), Mapping)
            else {}
        ),
        "cooldownState": dict(helper.get("cooldownState", {})) if isinstance(helper.get("cooldownState"), Mapping) else {},
    }


def _merge_helper_summaries(progress_summary: Mapping[str, Any], index_entries: list[dict[str, Any]]) -> dict[str, Any]:
    merged = dict(progress_summary)
    if not index_entries:
        return merged
    index_summary = summarize_helper_index(index_entries)
    merged["entryCount"] = _int(index_summary.get("entryCount"))
    for field in (
        "freshCallCount",
        "failedCallCount",
        "noteReuseCount",
        "blockedByBudgetCount",
        "blockedByCooldownCount",
    ):
        merged[field] = _int(index_summary.get(field))
    for field in (
        "freshCallsByReason",
        "failedCallsByReason",
        "reuseByReason",
        "blockedByBudgetByReason",
        "blockedByCooldownByReason",
    ):
        merged[field] = dict(index_summary.get(field, {})) if isinstance(index_summary.get(field), Mapping) else {}
    merged["eventCounts"] = dict(index_summary.get("eventCounts", {})) if isinstance(index_summary.get("eventCounts"), Mapping) else {}
    return merged


def _provider_model_counts(entries: Iterable[Mapping[str, Any]]) -> Counter[str]:
    counter: Counter[str] = Counter()
    for entry in entries:
        provider = _string(entry.get("provider"), "")
        model = _string(entry.get("model"), "")
        if provider and model:
            counter[f"{provider}:{model}"] += 1
    return counter


def _repeated_attempt_clusters(
    entries: Iterable[Mapping[str, Any]],
    *,
    campaign_root: Path,
    run_id: str,
    top_n: int,
) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for entry in entries:
        event = _string(entry.get("event"), "")
        if event not in {
            "provider_call",
            "provider_call_failed",
            "note_reuse",
            "skipped_by_budget",
            "skipped_by_cooldown",
        }:
            continue
        phase = _string(entry.get("phase"), "unknown")
        rel_path = _string(entry.get("relPath"), "(none)")
        reason = _string(entry.get("reason"), "(none)")
        config_signature = _string(entry.get("configSignature"), "(none)")
        buckets[(phase, rel_path, reason, config_signature)].append(dict(entry))

    rows: list[dict[str, Any]] = []
    for (phase, rel_path, reason, config_signature), bucket in buckets.items():
        event_counts = Counter(_string(entry.get("event"), "unknown") for entry in bucket)
        provider_models = _provider_model_counts(bucket)
        if sum(event_counts.values()) <= 1:
            continue
        rows.append(
            {
                "runId": run_id,
                "phase": phase,
                "relPath": rel_path,
                "reason": reason,
                "configSignature": config_signature,
                "eventCounts": dict(sorted(event_counts.items())),
                "providerModels": _sorted_counter(provider_models, top_n=top_n),
                "count": len(bucket),
                "latestCreatedAt": max(_string(entry.get("createdAt"), "") for entry in bucket),
                "sampleNotePaths": sorted(
                    {
                        _relative_to_root(Path(str(entry.get("notePath"))), campaign_root)
                        for entry in bucket
                        if isinstance(entry.get("notePath"), str) and entry.get("notePath")
                    }
                )[: min(top_n, 3)],
            }
        )
    rows.sort(
        key=lambda row: (
            -int(row["count"]),
            -int(row["eventCounts"].get("provider_call_failed", 0)),
            str(row["runId"]),
            str(row["relPath"]),
        )
    )
    return rows[:top_n]


def _default_output_root(campaign_root: Path) -> Path:
    if (campaign_root / "reports" / "postmortem" / "postmortem-summary.json").exists():
        return campaign_root / "reports" / "postmortem" / "helper-analysis"
    if (campaign_root / "reports" / "final" / "final-summary.json").exists():
        return campaign_root / "reports" / "final" / "helper-analysis"
    return campaign_root / "control" / "helper-analysis"


def build_campaign_helper_analysis(campaign_root: Path, *, top_n: int = 10) -> dict[str, Any]:
    campaign_root = campaign_root.resolve()
    summary_paths = _discover_summary_paths(campaign_root)
    campaign_status = _read_json(summary_paths["campaignStatus"]) or {}
    final_summary = _read_json(summary_paths["finalSummary"]) or {}
    postmortem_summary = _read_json(summary_paths["postmortemSummary"]) or {}
    lesson_paths = [
        path
        for path in (
            campaign_root / "reports" / "final" / "lessons" / "lesson-records.jsonl",
            campaign_root / "reports" / "postmortem" / "lessons" / "lesson-records.jsonl",
        )
        if path.exists()
    ]
    lesson_records = load_lesson_records(lesson_paths)
    lesson_records_by_run: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in lesson_records:
        run_id = record.get("run_id")
        if isinstance(run_id, str) and run_id:
            lesson_records_by_run[run_id].append(record)

    merged_runs = _merge_run_rows(
        campaign_status.get("runs", []) if isinstance(campaign_status.get("runs"), list) else [],
        final_summary.get("runs", []) if isinstance(final_summary.get("runs"), list) else [],
    )
    if not merged_runs:
        for run_root in sorted((campaign_root / "runs").glob("*")) if (campaign_root / "runs").exists() else []:
            if run_root.is_dir():
                merged_runs.setdefault(run_root.name, {"runId": run_root.name})

    helper_totals = Counter()
    helper_phase_counts: Counter[str] = Counter()
    helper_note_reason_counts: Counter[str] = Counter()
    helper_prompt_pack_counts: Counter[str] = Counter()
    helper_provider_model_counts: Counter[str] = Counter()
    lesson_category_counts: Counter[str] = Counter()
    lesson_accepted_state_counts: Counter[str] = Counter()
    run_outcome_counts: Counter[str] = Counter()
    terminal_category_counts: Counter[str] = Counter()
    reason_rollups: dict[str, dict[str, Any]] = {}
    repeated_attempt_rows: list[dict[str, Any]] = []
    run_rows: list[dict[str, Any]] = []

    for record in lesson_records:
        lesson_category_counts[_string(record.get("category"), "unknown")] += 1
        lesson_accepted_state_counts[_string(record.get("accepted_state"), "unknown")] += 1

    for run_id, run_row in sorted(merged_runs.items()):
        progress_path = _discover_run_progress_path(campaign_root, run_id)
        progress_payload = _read_json(progress_path) or {}
        progress_helper = _helper_summary_from_progress(progress_payload)
        helper_index_paths = _discover_run_helper_index_paths(campaign_root, run_id)
        helper_index_entries = _load_helper_index_entries(helper_index_paths)
        merged_helper = _merge_helper_summaries(progress_helper, helper_index_entries)
        run_status = _string(run_row.get("status"), "unknown")
        terminal_category = _run_terminal_category(run_row)
        run_outcome_counts[run_status] += 1
        terminal_category_counts[terminal_category] += 1
        for key in (
            "noteCount",
            "freshCallCount",
            "failedCallCount",
            "noteReuseCount",
            "blockedByBudgetCount",
            "blockedByCooldownCount",
        ):
            helper_totals[key] += _int(merged_helper.get(key))
        if merged_helper.get("enabled") is True:
            helper_totals["enabledRuns"] += 1
        if any(_int(merged_helper.get(key)) > 0 for key in ("noteCount", "freshCallCount", "failedCallCount", "noteReuseCount")):
            helper_totals["activeRuns"] += 1

        if isinstance(merged_helper.get("countsByPhase"), Mapping):
            for reason, count in merged_helper["countsByPhase"].items():
                if isinstance(reason, str):
                    helper_phase_counts[reason] += _int(count)
        if isinstance(merged_helper.get("countsByReason"), Mapping):
            for reason, count in merged_helper["countsByReason"].items():
                if isinstance(reason, str):
                    helper_note_reason_counts[reason] += _int(count)
        if isinstance(merged_helper.get("countsByPromptPack"), Mapping):
            for prompt_pack, count in merged_helper["countsByPromptPack"].items():
                if isinstance(prompt_pack, str):
                    helper_prompt_pack_counts[prompt_pack] += _int(count)

        helper_provider_model_counts.update(_provider_model_counts(helper_index_entries))
        repeated_attempt_rows.extend(
            _repeated_attempt_clusters(
                helper_index_entries,
                campaign_root=campaign_root,
                run_id=run_id,
                top_n=top_n,
            )
        )

        reason_keys = set()
        for field in (
            "countsByReason",
            "freshCallsByReason",
            "failedCallsByReason",
            "reuseByReason",
            "blockedByBudgetByReason",
            "blockedByCooldownByReason",
        ):
            mapping = merged_helper.get(field)
            if isinstance(mapping, Mapping):
                reason_keys.update(key for key in mapping if isinstance(key, str))

        for reason in sorted(reason_keys):
            row = reason_rollups.setdefault(
                reason,
                {
                    "reason": reason,
                    "noteMentions": 0,
                    "freshCalls": 0,
                    "failedCalls": 0,
                    "noteReuses": 0,
                    "blockedByBudget": 0,
                    "blockedByCooldown": 0,
                    "runIds": set(),
                    "runOutcomeCounts": Counter(),
                    "terminalCategoryCounts": Counter(),
                    "sampleScopes": set(),
                },
            )
            row["noteMentions"] += _int(merged_helper.get("countsByReason", {}).get(reason) if isinstance(merged_helper.get("countsByReason"), Mapping) else 0)
            row["freshCalls"] += _int(merged_helper.get("freshCallsByReason", {}).get(reason) if isinstance(merged_helper.get("freshCallsByReason"), Mapping) else 0)
            row["failedCalls"] += _int(merged_helper.get("failedCallsByReason", {}).get(reason) if isinstance(merged_helper.get("failedCallsByReason"), Mapping) else 0)
            row["noteReuses"] += _int(merged_helper.get("reuseByReason", {}).get(reason) if isinstance(merged_helper.get("reuseByReason"), Mapping) else 0)
            row["blockedByBudget"] += _int(merged_helper.get("blockedByBudgetByReason", {}).get(reason) if isinstance(merged_helper.get("blockedByBudgetByReason"), Mapping) else 0)
            row["blockedByCooldown"] += _int(merged_helper.get("blockedByCooldownByReason", {}).get(reason) if isinstance(merged_helper.get("blockedByCooldownByReason"), Mapping) else 0)
            row["runIds"].add(run_id)
            row["runOutcomeCounts"][run_status] += 1
            row["terminalCategoryCounts"][terminal_category] += 1
            if isinstance(run_row.get("scopeHint"), str) and run_row["scopeHint"]:
                row["sampleScopes"].add(run_row["scopeHint"])

        lesson_categories = Counter(_string(record.get("category"), "unknown") for record in lesson_records_by_run.get(run_id, []))
        run_rows.append(
            {
                "runId": run_id,
                "status": run_status,
                "terminalCategory": terminal_category,
                "scopeHint": run_row.get("scopeHint"),
                "progressSummaryPath": _relative_to_root(progress_path, campaign_root) if progress_path is not None else None,
                "helperIndexPaths": [_relative_to_root(path, campaign_root) for path in helper_index_paths],
                "helperEnabled": merged_helper.get("enabled") is True,
                "helperActivityScore": sum(
                    _int(merged_helper.get(key))
                    for key in (
                        "noteCount",
                        "freshCallCount",
                        "failedCallCount",
                        "noteReuseCount",
                        "blockedByBudgetCount",
                        "blockedByCooldownCount",
                    )
                ),
                "helper": {
                    key: merged_helper.get(key, 0)
                    for key in (
                        "noteCount",
                        "freshCallCount",
                        "failedCallCount",
                        "noteReuseCount",
                        "blockedByBudgetCount",
                        "blockedByCooldownCount",
                    )
                },
                "lessonCategories": _sorted_counter(lesson_categories, top_n=top_n),
                "acceptedProofCount": len(run_row.get("acceptedProofs", [])) if isinstance(run_row.get("acceptedProofs"), list) else 0,
                "acceptedFormalizationCount": (
                    len(run_row.get("acceptedFormalizations", []))
                    if isinstance(run_row.get("acceptedFormalizations"), list)
                    else 0
                ),
                "acceptedBlockerCount": (
                    len(run_row.get("acceptedBlockers", []))
                    if isinstance(run_row.get("acceptedBlockers"), list)
                    else 0
                ),
            }
        )

    reason_stats: list[dict[str, Any]] = []
    for row in reason_rollups.values():
        reason_stats.append(
            {
                "reason": row["reason"],
                "noteMentions": row["noteMentions"],
                "freshCalls": row["freshCalls"],
                "failedCalls": row["failedCalls"],
                "noteReuses": row["noteReuses"],
                "blockedByBudget": row["blockedByBudget"],
                "blockedByCooldown": row["blockedByCooldown"],
                "runCount": len(row["runIds"]),
                "runIds": sorted(row["runIds"])[:top_n],
                "runOutcomeCounts": dict(sorted(row["runOutcomeCounts"].items())),
                "terminalCategoryCounts": dict(sorted(row["terminalCategoryCounts"].items())),
                "sampleScopes": sorted(row["sampleScopes"])[: min(top_n, 3)],
            }
        )
    reason_stats.sort(
        key=lambda row: (
            -(int(row["failedCalls"]) + int(row["freshCalls"]) + int(row["noteMentions"])),
            str(row["reason"]),
        )
    )
    repeated_attempt_rows.sort(
        key=lambda row: (
            -int(row["count"]),
            -int(row["eventCounts"].get("provider_call_failed", 0)),
            str(row["runId"]),
        )
    )
    run_rows.sort(
        key=lambda row: (
            -int(row["helperActivityScore"]),
            str(row["runId"]),
        )
    )

    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": _utc_now(),
        "campaignId": _string(campaign_status.get("campaignId"), campaign_root.name),
        "campaignRoot": str(campaign_root),
        "paths": {
            "campaignStatus": str(summary_paths["campaignStatus"]) if summary_paths["campaignStatus"] is not None else None,
            "finalSummary": str(summary_paths["finalSummary"]) if summary_paths["finalSummary"] is not None else None,
            "postmortemSummary": str(summary_paths["postmortemSummary"]) if summary_paths["postmortemSummary"] is not None else None,
            "defaultOutputRoot": str(_default_output_root(campaign_root)),
            "lessonRecordPaths": [str(path) for path in lesson_paths],
        },
        "runCount": len(run_rows),
        "runOutcomeCounts": dict(sorted(run_outcome_counts.items())),
        "terminalCategoryCounts": dict(sorted(terminal_category_counts.items())),
        "helperEnabledRunCount": helper_totals.get("enabledRuns", 0),
        "helperActiveRunCount": helper_totals.get("activeRuns", 0),
        "helperTotals": {
            "noteCount": helper_totals.get("noteCount", 0),
            "freshCallCount": helper_totals.get("freshCallCount", 0),
            "failedCallCount": helper_totals.get("failedCallCount", 0),
            "noteReuseCount": helper_totals.get("noteReuseCount", 0),
            "blockedByBudgetCount": helper_totals.get("blockedByBudgetCount", 0),
            "blockedByCooldownCount": helper_totals.get("blockedByCooldownCount", 0),
        },
        "helperPhaseCounts": dict(sorted(helper_phase_counts.items())),
        "helperNoteReasonCounts": dict(sorted(helper_note_reason_counts.items())),
        "helperPromptPackCounts": dict(sorted(helper_prompt_pack_counts.items())),
        "helperProviderModelCounts": dict(sorted(helper_provider_model_counts.items())),
        "lessonCategoryCounts": dict(sorted(lesson_category_counts.items())),
        "lessonAcceptedStateCounts": dict(sorted(lesson_accepted_state_counts.items())),
        "reasonStats": reason_stats[:top_n],
        "repeatedAttemptClusters": repeated_attempt_rows[:top_n],
        "runs": run_rows[: max(top_n, 20)],
    }


def build_helper_analysis(campaign_roots: Iterable[Path], *, top_n: int = 10) -> dict[str, Any]:
    campaign_payloads = [build_campaign_helper_analysis(Path(root), top_n=top_n) for root in campaign_roots]
    aggregate_helper_totals = Counter()
    aggregate_run_outcomes = Counter()
    aggregate_terminal_categories = Counter()
    aggregate_phase_counts = Counter()
    aggregate_note_reasons = Counter()
    aggregate_prompt_packs = Counter()
    aggregate_provider_models = Counter()
    aggregate_lesson_categories = Counter()
    aggregate_lesson_states = Counter()
    reason_buckets: dict[str, dict[str, Any]] = {}
    repeated_clusters: list[dict[str, Any]] = []

    for campaign in campaign_payloads:
        aggregate_helper_totals["enabledRuns"] += _int(campaign.get("helperEnabledRunCount"))
        aggregate_helper_totals["activeRuns"] += _int(campaign.get("helperActiveRunCount"))
        for key, value in (campaign.get("helperTotals") or {}).items():
            aggregate_helper_totals[str(key)] += _int(value)
        aggregate_run_outcomes.update(
            {str(key): _int(value) for key, value in (campaign.get("runOutcomeCounts") or {}).items()}
        )
        aggregate_terminal_categories.update(
            {str(key): _int(value) for key, value in (campaign.get("terminalCategoryCounts") or {}).items()}
        )
        aggregate_phase_counts.update(
            {str(key): _int(value) for key, value in (campaign.get("helperPhaseCounts") or {}).items()}
        )
        aggregate_note_reasons.update(
            {str(key): _int(value) for key, value in (campaign.get("helperNoteReasonCounts") or {}).items()}
        )
        aggregate_prompt_packs.update(
            {str(key): _int(value) for key, value in (campaign.get("helperPromptPackCounts") or {}).items()}
        )
        aggregate_provider_models.update(
            {str(key): _int(value) for key, value in (campaign.get("helperProviderModelCounts") or {}).items()}
        )
        aggregate_lesson_categories.update(
            {str(key): _int(value) for key, value in (campaign.get("lessonCategoryCounts") or {}).items()}
        )
        aggregate_lesson_states.update(
            {str(key): _int(value) for key, value in (campaign.get("lessonAcceptedStateCounts") or {}).items()}
        )
        for row in campaign.get("reasonStats", []):
            if not isinstance(row, Mapping):
                continue
            reason = _string(row.get("reason"), "unknown")
            bucket = reason_buckets.setdefault(
                reason,
                {
                    "reason": reason,
                    "noteMentions": 0,
                    "freshCalls": 0,
                    "failedCalls": 0,
                    "noteReuses": 0,
                    "blockedByBudget": 0,
                    "blockedByCooldown": 0,
                    "runIds": set(),
                    "campaignIds": set(),
                    "runOutcomeCounts": Counter(),
                    "terminalCategoryCounts": Counter(),
                    "sampleScopes": set(),
                },
            )
            for key in ("noteMentions", "freshCalls", "failedCalls", "noteReuses", "blockedByBudget", "blockedByCooldown"):
                bucket[key] += _int(row.get(key))
            bucket["runIds"].update(item for item in row.get("runIds", []) if isinstance(item, str))
            bucket["campaignIds"].add(_string(campaign.get("campaignId"), campaign.get("campaignRoot", "unknown")))
            bucket["runOutcomeCounts"].update(
                {str(key): _int(value) for key, value in (row.get("runOutcomeCounts") or {}).items()}
            )
            bucket["terminalCategoryCounts"].update(
                {str(key): _int(value) for key, value in (row.get("terminalCategoryCounts") or {}).items()}
            )
            bucket["sampleScopes"].update(item for item in row.get("sampleScopes", []) if isinstance(item, str))
        for row in campaign.get("repeatedAttemptClusters", []):
            if isinstance(row, Mapping):
                repeated_clusters.append(dict(row))

    aggregate_reason_stats: list[dict[str, Any]] = []
    for bucket in reason_buckets.values():
        aggregate_reason_stats.append(
            {
                "reason": bucket["reason"],
                "noteMentions": bucket["noteMentions"],
                "freshCalls": bucket["freshCalls"],
                "failedCalls": bucket["failedCalls"],
                "noteReuses": bucket["noteReuses"],
                "blockedByBudget": bucket["blockedByBudget"],
                "blockedByCooldown": bucket["blockedByCooldown"],
                "runCount": len(bucket["runIds"]),
                "campaignCount": len(bucket["campaignIds"]),
                "runIds": sorted(bucket["runIds"])[:top_n],
                "campaignIds": sorted(bucket["campaignIds"])[:top_n],
                "runOutcomeCounts": dict(sorted(bucket["runOutcomeCounts"].items())),
                "terminalCategoryCounts": dict(sorted(bucket["terminalCategoryCounts"].items())),
                "sampleScopes": sorted(bucket["sampleScopes"])[: min(top_n, 3)],
            }
        )
    aggregate_reason_stats.sort(
        key=lambda row: (
            -(int(row["failedCalls"]) + int(row["freshCalls"]) + int(row["noteMentions"])),
            str(row["reason"]),
        )
    )
    repeated_clusters.sort(
        key=lambda row: (
            -int(row.get("count", 0)),
            -int((row.get("eventCounts") or {}).get("provider_call_failed", 0)),
            str(row.get("runId", "")),
        )
    )

    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": _utc_now(),
        "campaignCount": len(campaign_payloads),
        "campaignRoots": [str(Path(campaign["campaignRoot"])) for campaign in campaign_payloads],
        "aggregate": {
            "runOutcomeCounts": dict(sorted(aggregate_run_outcomes.items())),
            "terminalCategoryCounts": dict(sorted(aggregate_terminal_categories.items())),
            "helperEnabledRunCount": aggregate_helper_totals.get("enabledRuns", 0),
            "helperActiveRunCount": aggregate_helper_totals.get("activeRuns", 0),
            "helperTotals": {
                "noteCount": aggregate_helper_totals.get("noteCount", 0),
                "freshCallCount": aggregate_helper_totals.get("freshCallCount", 0),
                "failedCallCount": aggregate_helper_totals.get("failedCallCount", 0),
                "noteReuseCount": aggregate_helper_totals.get("noteReuseCount", 0),
                "blockedByBudgetCount": aggregate_helper_totals.get("blockedByBudgetCount", 0),
                "blockedByCooldownCount": aggregate_helper_totals.get("blockedByCooldownCount", 0),
            },
            "helperPhaseCounts": dict(sorted(aggregate_phase_counts.items())),
            "helperNoteReasonCounts": dict(sorted(aggregate_note_reasons.items())),
            "helperPromptPackCounts": dict(sorted(aggregate_prompt_packs.items())),
            "helperProviderModelCounts": dict(sorted(aggregate_provider_models.items())),
            "lessonCategoryCounts": dict(sorted(aggregate_lesson_categories.items())),
            "lessonAcceptedStateCounts": dict(sorted(aggregate_lesson_states.items())),
            "reasonStats": aggregate_reason_stats[:top_n],
            "repeatedAttemptClusters": repeated_clusters[:top_n],
        },
        "campaigns": campaign_payloads,
    }


def _coerce_report_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(payload.get("aggregate"), Mapping) and isinstance(payload.get("campaigns"), list):
        return dict(payload)
    helper_totals = payload.get("helperTotals", {}) if isinstance(payload.get("helperTotals"), Mapping) else {}
    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": payload.get("generatedAt"),
        "campaignCount": 1,
        "campaignRoots": [payload.get("campaignRoot")] if isinstance(payload.get("campaignRoot"), str) else [],
        "aggregate": {
            "runOutcomeCounts": dict(payload.get("runOutcomeCounts", {})) if isinstance(payload.get("runOutcomeCounts"), Mapping) else {},
            "terminalCategoryCounts": (
                dict(payload.get("terminalCategoryCounts", {}))
                if isinstance(payload.get("terminalCategoryCounts"), Mapping)
                else {}
            ),
            "helperEnabledRunCount": _int(payload.get("helperEnabledRunCount")),
            "helperActiveRunCount": _int(payload.get("helperActiveRunCount")),
            "helperTotals": dict(helper_totals),
            "helperPhaseCounts": dict(payload.get("helperPhaseCounts", {})) if isinstance(payload.get("helperPhaseCounts"), Mapping) else {},
            "helperNoteReasonCounts": (
                dict(payload.get("helperNoteReasonCounts", {}))
                if isinstance(payload.get("helperNoteReasonCounts"), Mapping)
                else {}
            ),
            "helperPromptPackCounts": (
                dict(payload.get("helperPromptPackCounts", {}))
                if isinstance(payload.get("helperPromptPackCounts"), Mapping)
                else {}
            ),
            "helperProviderModelCounts": (
                dict(payload.get("helperProviderModelCounts", {}))
                if isinstance(payload.get("helperProviderModelCounts"), Mapping)
                else {}
            ),
            "lessonCategoryCounts": (
                dict(payload.get("lessonCategoryCounts", {}))
                if isinstance(payload.get("lessonCategoryCounts"), Mapping)
                else {}
            ),
            "lessonAcceptedStateCounts": (
                dict(payload.get("lessonAcceptedStateCounts", {}))
                if isinstance(payload.get("lessonAcceptedStateCounts"), Mapping)
                else {}
            ),
            "reasonStats": list(payload.get("reasonStats", [])) if isinstance(payload.get("reasonStats"), list) else [],
            "repeatedAttemptClusters": (
                list(payload.get("repeatedAttemptClusters", []))
                if isinstance(payload.get("repeatedAttemptClusters"), list)
                else []
            ),
        },
        "campaigns": [dict(payload)],
    }


def render_helper_analysis_markdown(payload: Mapping[str, Any]) -> str:
    report_payload = _coerce_report_payload(payload)
    aggregate = report_payload.get("aggregate") if isinstance(report_payload.get("aggregate"), Mapping) else {}
    lines = [
        "# Helper Analysis",
        "",
        f"- Generated at: `{report_payload.get('generatedAt')}`",
        f"- Campaign count: `{report_payload.get('campaignCount', 0)}`",
        f"- Helper enabled runs: `{aggregate.get('helperEnabledRunCount', 0)}`",
        f"- Helper active runs: `{aggregate.get('helperActiveRunCount', 0)}`",
        f"- Helper totals: `{json.dumps(aggregate.get('helperTotals', {}), sort_keys=True)}`",
        f"- Run outcomes: `{json.dumps(aggregate.get('runOutcomeCounts', {}), sort_keys=True)}`",
        f"- Terminal categories: `{json.dumps(aggregate.get('terminalCategoryCounts', {}), sort_keys=True)}`",
        f"- Lesson categories: `{json.dumps(aggregate.get('lessonCategoryCounts', {}), sort_keys=True)}`",
        "",
        "## Reason Families",
        "",
    ]
    reason_rows = aggregate.get("reasonStats")
    if isinstance(reason_rows, list) and reason_rows:
        for row in reason_rows:
            if not isinstance(row, Mapping):
                continue
            lines.append(
                f"- `{row.get('reason')}` fresh={row.get('freshCalls', 0)} failed={row.get('failedCalls', 0)} "
                f"notes={row.get('noteMentions', 0)} reuse={row.get('noteReuses', 0)} "
                f"budget={row.get('blockedByBudget', 0)} cooldown={row.get('blockedByCooldown', 0)} "
                f"runs={row.get('runCount', 0)} terminal={json.dumps(row.get('terminalCategoryCounts', {}), sort_keys=True)}"
            )
    else:
        lines.append("- none")

    lines.extend(["", "## Repeated Attempt Clusters", ""])
    repeated_rows = aggregate.get("repeatedAttemptClusters")
    if isinstance(repeated_rows, list) and repeated_rows:
        for row in repeated_rows:
            if not isinstance(row, Mapping):
                continue
            lines.append(
                f"- `{row.get('runId')}` `{row.get('relPath')}` reason=`{row.get('reason')}` "
                f"count={row.get('count', 0)} events={json.dumps(row.get('eventCounts', {}), sort_keys=True)}"
            )
    else:
        lines.append("- none")

    lines.extend(["", "## Campaigns", ""])
    campaigns = report_payload.get("campaigns")
    if isinstance(campaigns, list) and campaigns:
        for campaign in campaigns:
            if not isinstance(campaign, Mapping):
                continue
            helper_totals = campaign.get("helperTotals", {}) if isinstance(campaign.get("helperTotals"), Mapping) else {}
            lines.append(
                f"- `{campaign.get('campaignId')}` runs={campaign.get('runCount', 0)} "
                f"helper_active={campaign.get('helperActiveRunCount', 0)} helper_totals={json.dumps(helper_totals, sort_keys=True)} "
                f"outcomes={json.dumps(campaign.get('runOutcomeCounts', {}), sort_keys=True)} "
                f"lessons={json.dumps(campaign.get('lessonCategoryCounts', {}), sort_keys=True)}"
            )
    else:
        lines.append("- none")

    return "\n".join(lines) + "\n"


def write_helper_analysis_artifacts(output_root: Path, payload: Mapping[str, Any]) -> dict[str, str]:
    output_root.mkdir(parents=True, exist_ok=True)
    json_path = output_root / "helper-analysis.json"
    markdown_path = output_root / "helper-analysis.md"
    report_payload = _coerce_report_payload(payload)
    json_path.write_text(json.dumps(report_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(render_helper_analysis_markdown(report_payload), encoding="utf-8")
    return {
        "json": str(json_path),
        "markdown": str(markdown_path),
    }
