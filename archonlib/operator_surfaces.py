from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


MISSION_BRIEF_NAME = "mission-brief.md"
OPERATOR_JOURNAL_NAME = "operator-journal.md"


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
