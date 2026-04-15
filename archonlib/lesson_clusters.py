from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


SCHEMA_VERSION = 1


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not path.exists():
        return records
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        payload = json.loads(raw_line)
        if isinstance(payload, dict):
            records.append(payload)
    return records


def load_lesson_records(paths: Iterable[Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in paths:
        records.extend(_load_jsonl(path))
    return records


def _sorted_counter(counter: Counter[str], *, top_n: int) -> list[dict[str, Any]]:
    rows = [{"value": key, "count": count} for key, count in counter.items()]
    rows.sort(key=lambda row: (-int(row["count"]), str(row["value"])))
    return rows[:top_n]


def _string(value: object, default: str) -> str:
    return value if isinstance(value, str) and value else default


def build_lesson_clusters(
    records: Iterable[Mapping[str, Any]],
    *,
    source_paths: list[str] | None = None,
    top_n: int = 10,
) -> dict[str, Any]:
    materialized = [dict(record) for record in records]
    category_buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    theorem_buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    action_buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for record in materialized:
        category = _string(record.get("category"), "unknown")
        theorem_id = _string(record.get("theorem_id"), "(campaign)")
        action = _string(record.get("action_taken"), "record_lesson")
        category_buckets[category].append(record)
        theorem_buckets[theorem_id].append(record)
        action_buckets[action].append(record)

    category_clusters: list[dict[str, Any]] = []
    for category, bucket in category_buckets.items():
        theorem_counts = Counter(_string(record.get("theorem_id"), "(campaign)") for record in bucket)
        action_counts = Counter(_string(record.get("action_taken"), "record_lesson") for record in bucket)
        accepted_state_counts = Counter(_string(record.get("accepted_state"), "unknown") for record in bucket)
        samples = sorted({_string(record.get("summary"), "") for record in bucket if isinstance(record.get("summary"), str)})
        category_clusters.append(
            {
                "category": category,
                "count": len(bucket),
                "theoremCount": len(theorem_counts),
                "acceptedStates": _sorted_counter(accepted_state_counts, top_n=top_n),
                "topTheorems": _sorted_counter(theorem_counts, top_n=top_n),
                "topActions": _sorted_counter(action_counts, top_n=top_n),
                "sampleSummaries": samples[: min(top_n, 3)],
            }
        )
    category_clusters.sort(key=lambda row: (-int(row["count"]), str(row["category"])))

    theorem_clusters: list[dict[str, Any]] = []
    for theorem_id, bucket in theorem_buckets.items():
        category_counts = Counter(_string(record.get("category"), "unknown") for record in bucket)
        action_counts = Counter(_string(record.get("action_taken"), "record_lesson") for record in bucket)
        run_ids = sorted({_string(record.get("run_id"), "(campaign)") for record in bucket})
        theorem_clusters.append(
            {
                "theoremId": theorem_id,
                "count": len(bucket),
                "runIds": run_ids[:top_n],
                "categories": _sorted_counter(category_counts, top_n=top_n),
                "topActions": _sorted_counter(action_counts, top_n=top_n),
                "sampleSummaries": sorted({_string(record.get("summary"), "") for record in bucket if isinstance(record.get("summary"), str)})[: min(top_n, 3)],
            }
        )
    theorem_clusters.sort(key=lambda row: (-int(row["count"]), str(row["theoremId"])))

    action_clusters: list[dict[str, Any]] = []
    for action, bucket in action_buckets.items():
        category_counts = Counter(_string(record.get("category"), "unknown") for record in bucket)
        theorem_counts = Counter(_string(record.get("theorem_id"), "(campaign)") for record in bucket)
        action_clusters.append(
            {
                "action": action,
                "count": len(bucket),
                "theoremCount": len(theorem_counts),
                "categories": _sorted_counter(category_counts, top_n=top_n),
                "topTheorems": _sorted_counter(theorem_counts, top_n=top_n),
            }
        )
    action_clusters.sort(key=lambda row: (-int(row["count"]), str(row["action"])))

    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": _utc_now(),
        "recordCount": len(materialized),
        "sourcePaths": list(source_paths or []),
        "categoryClusters": category_clusters,
        "theoremClusters": theorem_clusters,
        "actionClusters": action_clusters,
    }


def render_lesson_clusters_markdown(payload: Mapping[str, Any]) -> str:
    lines = [
        "# Lesson Clusters",
        "",
        f"- Generated at: `{payload.get('generatedAt')}`",
        f"- Record count: `{payload.get('recordCount')}`",
        "",
        "## Category Hotspots",
        "",
    ]
    for row in payload.get("categoryClusters", []):
        if not isinstance(row, Mapping):
            continue
        top_theorems = ", ".join(
            f"{item.get('value')} ({item.get('count')})"
            for item in row.get("topTheorems", [])[:3]
            if isinstance(item, Mapping)
        ) or "none"
        top_actions = ", ".join(
            f"{item.get('value')} ({item.get('count')})"
            for item in row.get("topActions", [])[:3]
            if isinstance(item, Mapping)
        ) or "none"
        lines.append(
            f"- `{row.get('category')}` count={row.get('count')} theorem_count={row.get('theoremCount')} "
            f"top_theorems={top_theorems} top_actions={top_actions}"
        )
        for sample in row.get("sampleSummaries", [])[:2]:
            lines.append(f"  sample: {sample}")

    lines.extend(["", "## Theorem Hotspots", ""])
    for row in payload.get("theoremClusters", [])[:10]:
        if not isinstance(row, Mapping):
            continue
        categories = ", ".join(
            f"{item.get('value')} ({item.get('count')})"
            for item in row.get("categories", [])[:3]
            if isinstance(item, Mapping)
        ) or "none"
        lines.append(f"- `{row.get('theoremId')}` count={row.get('count')} categories={categories}")

    lines.extend(["", "## Action Hotspots", ""])
    for row in payload.get("actionClusters", [])[:10]:
        if not isinstance(row, Mapping):
            continue
        categories = ", ".join(
            f"{item.get('value')} ({item.get('count')})"
            for item in row.get("categories", [])[:3]
            if isinstance(item, Mapping)
        ) or "none"
        lines.append(f"- `{row.get('action')}` count={row.get('count')} categories={categories}")

    return "\n".join(lines) + "\n"


def build_lesson_reminders(
    records: Iterable[Mapping[str, Any]],
    *,
    top_n: int = 10,
) -> dict[str, Any]:
    materialized = [dict(record) for record in records]
    buckets: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in materialized:
        category = _string(record.get("category"), "unknown")
        recommended_action = _string(record.get("recommended_action"), _string(record.get("action_taken"), "record_lesson"))
        source_status = _string(record.get("source_status"), _string(record.get("accepted_state"), "unknown"))
        buckets[(category, recommended_action, source_status)].append(record)

    reminders: list[dict[str, Any]] = []
    for (category, recommended_action, source_status), bucket in buckets.items():
        signal_counter = Counter()
        summaries: list[str] = []
        for record in bucket:
            signal_tags = record.get("signal_tags")
            if isinstance(signal_tags, list):
                for tag in signal_tags:
                    if isinstance(tag, str) and tag:
                        signal_counter[tag] += 1
            summary = record.get("summary")
            if isinstance(summary, str) and summary and summary not in summaries:
                summaries.append(summary)
        reminders.append(
            {
                "category": category,
                "recommendedAction": recommended_action,
                "sourceStatus": source_status,
                "count": len(bucket),
                "signalTags": _sorted_counter(signal_counter, top_n=top_n),
                "sampleSummaries": summaries[: min(top_n, 3)],
            }
        )
    reminders.sort(
        key=lambda row: (
            -int(row["count"]),
            str(row["category"]),
            str(row["recommendedAction"]),
            str(row["sourceStatus"]),
        )
    )
    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": _utc_now(),
        "recordCount": len(materialized),
        "reminders": reminders[:top_n],
    }


def render_lesson_reminders_markdown(payload: Mapping[str, Any]) -> str:
    lines = [
        "# Lesson Reminders",
        "",
        f"- Generated at: `{payload.get('generatedAt')}`",
        f"- Record count: `{payload.get('recordCount')}`",
        "",
    ]
    reminders = payload.get("reminders")
    if not isinstance(reminders, list) or not reminders:
        lines.append("- none")
        return "\n".join(lines) + "\n"

    for row in reminders:
        if not isinstance(row, Mapping):
            continue
        signal_tags = ", ".join(
            f"{item.get('value')} ({item.get('count')})"
            for item in row.get("signalTags", [])[:4]
            if isinstance(item, Mapping)
        ) or "none"
        lines.append(
            f"- `{row.get('category')}` action=`{row.get('recommendedAction')}` "
            f"source=`{row.get('sourceStatus')}` count={row.get('count')} signals={signal_tags}"
        )
        for summary in row.get("sampleSummaries", [])[:2]:
            lines.append(f"  sample: {summary}")
    return "\n".join(lines) + "\n"


def write_lesson_cluster_artifacts(
    lessons_root: Path,
    *,
    records: Iterable[Mapping[str, Any]],
    source_paths: list[str] | None = None,
    top_n: int = 10,
) -> dict[str, str]:
    lessons_root.mkdir(parents=True, exist_ok=True)
    payload = build_lesson_clusters(records, source_paths=source_paths, top_n=top_n)
    json_path = lessons_root / "lesson-clusters.json"
    markdown_path = lessons_root / "lesson-clusters.md"
    reminders_payload = build_lesson_reminders(records, top_n=top_n)
    reminders_json_path = lessons_root / "lesson-reminders.json"
    reminders_markdown_path = lessons_root / "lesson-reminders.md"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(render_lesson_clusters_markdown(payload), encoding="utf-8")
    reminders_json_path.write_text(json.dumps(reminders_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    reminders_markdown_path.write_text(render_lesson_reminders_markdown(reminders_payload), encoding="utf-8")
    return {
        "json": str(json_path),
        "markdown": str(markdown_path),
        "remindersJson": str(reminders_json_path),
        "remindersMarkdown": str(reminders_markdown_path),
    }
