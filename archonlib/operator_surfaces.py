from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


MISSION_BRIEF_NAME = "mission-brief.md"
OPERATOR_JOURNAL_NAME = "operator-journal.md"
RESOLVED_SPEC_NAME = "launch-spec.resolved.json"
CAMPAIGN_MODES = {"benchmark_faithful", "formalization", "open_problem"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _brief_scope_lines(resolved_spec: Mapping[str, Any] | None) -> list[str]:
    if not isinstance(resolved_spec, Mapping):
        return ["- Replace this section with the benchmark slice and success criteria before unattended runs."]
    lines: list[str] = []
    source_root = resolved_spec.get("sourceRoot")
    if isinstance(source_root, str) and source_root:
        lines.append(f"- Source root: `{source_root}`")
    campaign_root = resolved_spec.get("campaignRoot")
    if isinstance(campaign_root, str) and campaign_root:
        lines.append(f"- Campaign root: `{campaign_root}`")
    run_spec_output = resolved_spec.get("runSpecOutput") or resolved_spec.get("runSpecFile")
    if isinstance(run_spec_output, str) and run_spec_output:
        lines.append(f"- Run spec output: `{run_spec_output}`")
    teacher_model = resolved_spec.get("teacherModel")
    if isinstance(teacher_model, str) and teacher_model:
        lines.append(f"- Teacher model: `{teacher_model}`")
    return lines or ["- Replace this section with the benchmark slice and success criteria before unattended runs."]


def _mission_brief_template(
    *,
    campaign_root: Path,
    source_root: Path | None,
    spec_reference: str | None,
    resolved_spec: Mapping[str, Any] | None,
    mode: str,
) -> str:
    scope_lines = _brief_scope_lines(resolved_spec)
    source_line = f"- Source root: `{source_root}`" if source_root is not None else "- Source root: `(fill me)`"
    spec_line = f"- Spec reference: `{spec_reference}`" if spec_reference else "- Spec reference: `(fill me)`"
    return "\n".join(
        [
            "# Mission Brief",
            "",
            "> Replace the placeholders below before long unattended campaigns.",
            "",
            "## Operator Context",
            "",
            f"- Campaign root: `{campaign_root}`",
            source_line,
            spec_line,
            f"- Bootstrap mode: `{mode}`",
            "",
            "## Goal",
            "",
            "- Describe the real user objective in one paragraph.",
            "",
            "## Success Criteria",
            "",
            "- State what counts as success.",
            "- State what counts as acceptable blockers or postmortem-only output.",
            "",
            "## Constraints",
            "",
            "- Do not mutate `source/`.",
            "- Accept only exported artifacts backed by validation.",
            "- Keep teacher scopes disjoint.",
            "",
            "## Planned Scope",
            "",
            *scope_lines,
            "",
            "## Watch Items",
            "",
            "- Provider instability, theorem mutation, repeated no-progress loops, or launch conflicts.",
            "",
        ]
    ) + "\n"


def _journal_header(*, campaign_root: Path) -> str:
    return "\n".join(
        [
            "# Operator Journal",
            "",
            f"- Campaign root: `{campaign_root}`",
            "- Append a new timestamped block for every launch, recovery, archive, scope change, or final acceptance decision.",
            "",
        ]
    ) + "\n"


def _reviewed_journal_header(*, campaign_root: Path) -> str:
    return "\n".join(
        [
            "# Operator Journal",
            "",
            f"- Campaign root: `{campaign_root}`",
            "- This journal records reviewed operator launch, recovery, scope, archive, and acceptance decisions.",
            "",
        ]
    ) + "\n"


def _journal_block(
    *,
    entrypoint: str,
    mode: str,
    spec_reference: str | None,
    source_root: Path | None,
    note: str,
) -> str:
    lines = [
        f"## {_utc_now()}",
        "",
        f"- Entrypoint: `{entrypoint}`",
        f"- Mode: `{mode}`",
    ]
    if source_root is not None:
        lines.append(f"- Source root: `{source_root}`")
    if spec_reference:
        lines.append(f"- Spec reference: `{spec_reference}`")
    lines.append(f"- Note: {note}")
    lines.append("")
    return "\n".join(lines)


def _normalized_campaign_mode(mode: str) -> str:
    normalized = mode.strip().lower().replace("-", "_").replace(" ", "_")
    if normalized not in CAMPAIGN_MODES:
        raise ValueError(f"unsupported campaign mode: {mode}")
    return normalized


def _default_preload_historical_routes(mode: str) -> bool:
    return _normalized_campaign_mode(mode) != "benchmark_faithful"


def build_resolved_spec(
    *,
    campaign_root: Path,
    source_root: Path,
    campaign_mode: str,
    objective_regex: str | None,
    shard_size: int,
    run_id_prefix: str,
    run_id_mode: str,
    teacher_model: str,
    teacher_reasoning_effort: str,
    reuse_lake_from: Path | None = None,
    preload_historical_routes: bool | None = None,
    watchdog_enabled: bool = True,
) -> dict[str, Any]:
    normalized_mode = _normalized_campaign_mode(campaign_mode)
    preload_value = (
        _default_preload_historical_routes(normalized_mode)
        if preload_historical_routes is None
        else preload_historical_routes
    )
    if normalized_mode == "benchmark_faithful" and preload_value:
        raise ValueError("benchmark-faithful intake must not preload historical routes")
    return {
        "campaignMode": normalized_mode,
        "campaignRoot": str(campaign_root.resolve()),
        "sourceRoot": str(source_root.resolve()),
        "reuseLakeFrom": str((reuse_lake_from or source_root).resolve()),
        "runSpecOutput": str((campaign_root / "control" / "planned-run-specs.json").resolve()),
        "teacherModel": teacher_model,
        "teacherReasoningEffort": teacher_reasoning_effort,
        "preloadHistoricalRoutes": preload_value,
        "planShards": {
            "matchRegex": objective_regex or "^.*\\.lean$",
            "shardSize": shard_size,
            "runIdPrefix": run_id_prefix,
            "runIdMode": run_id_mode,
        },
        "watchdog": {
            "enabled": watchdog_enabled,
            "model": teacher_model,
            "reasoningEffort": teacher_reasoning_effort,
            "pollSeconds": 30,
            "stallSeconds": 300,
            "bootstrapLaunchAfterSeconds": 45,
            "maxRestarts": 3,
            "ownerSilenceSeconds": 1200,
            "maxActiveLaunches": 2,
            "launchBatchSize": 1,
            "launchCooldownSeconds": 90,
            "finalizeOnTerminal": True,
            "pruneWorkspaceLake": True,
            "pruneBrokenPrewarm": True,
        },
    }


def render_operator_intake_mission_brief(
    *,
    campaign_root: Path,
    source_root: Path,
    objective: str,
    campaign_mode: str,
    success_criteria: list[str],
    acceptable_blockers: list[str],
    constraints: list[str],
    watch_items: list[str],
    resolved_spec: Mapping[str, Any],
    spec_reference: str | None,
) -> str:
    normalized_mode = _normalized_campaign_mode(campaign_mode)
    planned_scope_lines = _brief_scope_lines(resolved_spec)
    return "\n".join(
        [
            "# Mission Brief",
            "",
            "## Operator Context",
            "",
            f"- Campaign root: `{campaign_root.resolve()}`",
            f"- Source root: `{source_root.resolve()}`",
            f"- Campaign mode: `{normalized_mode}`",
            f"- Spec reference: `{spec_reference or (campaign_root / 'control' / RESOLVED_SPEC_NAME).resolve()}`",
            "",
            "## Goal",
            "",
            objective,
            "",
            "## Success Criteria",
            "",
            *[f"- {item}" for item in success_criteria],
            "",
            "## Acceptable Blockers Or Postmortem Outputs",
            "",
            *[f"- {item}" for item in acceptable_blockers],
            "",
            "## Constraints",
            "",
            *[f"- {item}" for item in constraints],
            "",
            "## Planned Scope",
            "",
            *planned_scope_lines,
            "",
            "## Watch Items",
            "",
            *[f"- {item}" for item in watch_items],
            "",
        ]
    ) + "\n"


def write_operator_intake_bundle(
    campaign_root: Path,
    *,
    source_root: Path,
    objective: str,
    campaign_mode: str,
    success_criteria: list[str],
    acceptable_blockers: list[str],
    constraints: list[str],
    watch_items: list[str],
    resolved_spec: Mapping[str, Any],
    entrypoint: str,
    note: str,
    spec_reference: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    campaign_root = campaign_root.resolve()
    control_root = campaign_root / "control"
    control_root.mkdir(parents=True, exist_ok=True)
    mission_path = control_root / MISSION_BRIEF_NAME
    journal_path = control_root / OPERATOR_JOURNAL_NAME
    spec_path = control_root / RESOLVED_SPEC_NAME

    if not force:
        existing = [path for path in (mission_path, spec_path) if path.exists()]
        if existing:
            raise FileExistsError(
                "operator intake bundle already exists; rerun with force=True to refresh mission/spec surfaces"
            )

    mission_path.write_text(
        render_operator_intake_mission_brief(
            campaign_root=campaign_root,
            source_root=source_root,
            objective=objective,
            campaign_mode=campaign_mode,
            success_criteria=success_criteria,
            acceptable_blockers=acceptable_blockers,
            constraints=constraints,
            watch_items=watch_items,
            resolved_spec=resolved_spec,
            spec_reference=spec_reference,
        ),
        encoding="utf-8",
    )
    spec_path.write_text(json.dumps(dict(resolved_spec), indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if not journal_path.exists():
        journal_path.write_text(_reviewed_journal_header(campaign_root=campaign_root), encoding="utf-8")
    with journal_path.open("a", encoding="utf-8") as handle:
        handle.write(
            _journal_block(
                entrypoint=entrypoint,
                mode="operator_intake",
                spec_reference=spec_reference or str(spec_path),
                source_root=source_root.resolve(),
                note=note,
            )
        )
    return {
        "missionBriefPath": str(mission_path),
        "operatorJournalPath": str(journal_path),
        "resolvedSpecPath": str(spec_path),
    }


def ensure_operator_surfaces(
    campaign_root: Path,
    *,
    source_root: Path | None = None,
    spec_reference: str | None = None,
    resolved_spec: Mapping[str, Any] | None = None,
    mode: str,
    entrypoint: str,
    note: str,
) -> dict[str, Any]:
    campaign_root = campaign_root.resolve()
    control_root = campaign_root / "control"
    control_root.mkdir(parents=True, exist_ok=True)

    mission_path = control_root / MISSION_BRIEF_NAME
    journal_path = control_root / OPERATOR_JOURNAL_NAME
    created = {"missionBriefCreated": False, "operatorJournalCreated": False}

    if not mission_path.exists():
        mission_path.write_text(
            _mission_brief_template(
                campaign_root=campaign_root,
                source_root=source_root.resolve() if source_root is not None else None,
                spec_reference=spec_reference,
                resolved_spec=resolved_spec,
                mode=mode,
            ),
            encoding="utf-8",
        )
        created["missionBriefCreated"] = True

    if not journal_path.exists():
        journal_path.write_text(_journal_header(campaign_root=campaign_root), encoding="utf-8")
        created["operatorJournalCreated"] = True

    with journal_path.open("a", encoding="utf-8") as handle:
        handle.write(
            _journal_block(
                entrypoint=entrypoint,
                mode=mode,
                spec_reference=spec_reference,
                source_root=source_root.resolve() if source_root is not None else None,
                note=note,
            )
        )

    return {
        **created,
        "missionBriefPath": str(mission_path),
        "operatorJournalPath": str(journal_path),
    }


def render_resolved_spec_excerpt(resolved_spec: Mapping[str, Any] | None) -> str:
    if not isinstance(resolved_spec, Mapping):
        return "{}"
    excerpt: dict[str, Any] = {}
    for key in ("campaignRoot", "sourceRoot", "runSpecOutput", "teacherModel", "teacherReasoningEffort"):
        value = resolved_spec.get(key)
        if value is not None:
            excerpt[key] = value
    return json.dumps(excerpt, indent=2, sort_keys=True)
