from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from archonlib.helper_health import (
    helper_model,
    helper_model_provider_mismatch,
    helper_provider,
    load_helper_env_file,
    probe_helper_transport,
)


SCHEMA_VERSION = 1
PLACEHOLDER_MARKERS = (
    "replace the placeholders below",
    "describe the real user objective in one paragraph",
    "state what counts as success",
    "replace this section with the benchmark slice and success criteria",
)
JOURNAL_SCAFFOLD_MARKERS = (
    "append a new timestamped block for every launch",
    "scaffolded operator surfaces",
)
MODE_VALUES = {"benchmark_faithful", "formalization", "open_problem"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _load_text(path: Path) -> str | None:
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8", errors="replace")


def _issue(*, level: str, code: str, message: str, path: str | None = None, hint: str | None = None) -> dict[str, str]:
    payload = {"level": level, "code": code, "message": message}
    if path:
        payload["path"] = path
    if hint:
        payload["hint"] = hint
    return payload


def _normalize_mode(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    return normalized if normalized in MODE_VALUES else None


def _detect_mode(spec: Mapping[str, Any], mission_brief: str | None, *, source_root: Path | None) -> str:
    explicit = _normalize_mode(spec.get("campaignMode"))
    if explicit is not None:
        return explicit

    mission_lower = mission_brief.lower() if isinstance(mission_brief, str) else ""
    if "benchmark-faithful" in mission_lower or "benchmark faithful" in mission_lower:
        return "benchmark_faithful"
    if "open problem" in mission_lower or "open-problem" in mission_lower:
        return "open_problem"
    if "formalization" in mission_lower:
        return "formalization"

    source_text = str(source_root).lower() if source_root is not None else ""
    if "fate" in source_text or "fatem" in source_text or "fateh" in source_text or "fatex" in source_text:
        return "benchmark_faithful"
    if bool(spec.get("preloadHistoricalRoutes")):
        return "formalization"
    return "formalization"


def _matches_scope_count(source_root: Path, regex_text: str | None) -> int | None:
    if not regex_text:
        return None
    pattern = re.compile(regex_text)
    count = 0
    for path in source_root.rglob("*.lean"):
        rel_path = path.relative_to(source_root).as_posix()
        if pattern.search(rel_path):
            count += 1
    return count


def _helper_env_disabled(spec: Mapping[str, Any]) -> bool:
    environment = spec.get("environment")
    if not isinstance(environment, Mapping):
        return False
    value = environment.get("ARCHON_HELPER_ENABLE")
    if isinstance(value, bool):
        return value is False
    if isinstance(value, str):
        return value.strip().lower() in {"0", "false", "no", "off"}
    return False


def _is_placeholder_text(text: str | None, *, markers: tuple[str, ...]) -> bool:
    if not isinstance(text, str) or not text.strip():
        return True
    lowered = text.lower()
    return any(marker in lowered for marker in markers)


def validate_launch_contract(
    campaign_root: Path,
    *,
    repo_root: Path,
    strict: bool = False,
    probe_helper: bool = False,
    helper_probe_timeout_seconds: int = 20,
) -> dict[str, Any]:
    campaign_root = campaign_root.resolve()
    repo_root = repo_root.resolve()
    control_root = campaign_root / "control"
    mission_brief_path = control_root / "mission-brief.md"
    journal_path = control_root / "operator-journal.md"
    spec_path = control_root / "launch-spec.resolved.json"
    helper_env_path = repo_root / "examples" / "helper.env"

    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    suggested_fixes: list[str] = []
    helper_probe_payload: dict[str, Any] | None = None

    spec = _load_json(spec_path)
    mission_brief = _load_text(mission_brief_path)
    operator_journal = _load_text(journal_path)

    if spec is None:
        errors.append(
            _issue(
                level="error",
                code="resolved_spec_missing_or_invalid",
                message="control/launch-spec.resolved.json is missing or invalid JSON.",
                path=str(spec_path),
                hint="Write a valid resolved spec before launching the watchdog.",
            )
        )
        suggested_fixes.append("Write or refresh control/launch-spec.resolved.json.")
        detected_mode = "unknown"
    else:
        detected_mode = _detect_mode(spec, mission_brief, source_root=Path(str(spec["sourceRoot"])) if isinstance(spec.get("sourceRoot"), str) else None)

    if mission_brief is None:
        errors.append(
            _issue(
                level="error",
                code="mission_brief_missing",
                message="control/mission-brief.md is missing.",
                path=str(mission_brief_path),
                hint="Create the mission brief before launch.",
            )
        )
        suggested_fixes.append("Create control/mission-brief.md.")
    elif _is_placeholder_text(mission_brief, markers=PLACEHOLDER_MARKERS):
        warnings.append(
            _issue(
                level="warning",
                code="mission_brief_scaffolded",
                message="Mission brief still contains scaffold placeholder text.",
                path=str(mission_brief_path),
                hint="Replace the placeholder sections with the real objective, success criteria, and watch items.",
            )
        )
        suggested_fixes.append("Replace scaffold text in control/mission-brief.md.")

    if operator_journal is None:
        errors.append(
            _issue(
                level="error",
                code="operator_journal_missing",
                message="control/operator-journal.md is missing.",
                path=str(journal_path),
                hint="Create the operator journal before launch.",
            )
        )
        suggested_fixes.append("Create control/operator-journal.md.")
    elif _is_placeholder_text(operator_journal, markers=JOURNAL_SCAFFOLD_MARKERS):
        warnings.append(
            _issue(
                level="warning",
                code="operator_journal_scaffolded",
                message="Operator journal still looks scaffolded and may not contain real operator review notes.",
                path=str(journal_path),
                hint="Add a timestamped operator decision block with the current mission and next expected check.",
            )
        )
        suggested_fixes.append("Append a reviewed decision block to control/operator-journal.md.")

    if spec is not None:
        if str(campaign_root) != str(Path(str(spec.get("campaignRoot"))).resolve()):
            errors.append(
                _issue(
                    level="error",
                    code="campaign_root_mismatch",
                    message="Resolved spec campaignRoot does not match the campaign root being launched.",
                    path=str(spec_path),
                    hint="Update control/launch-spec.resolved.json to point at this campaign root.",
                )
            )
            suggested_fixes.append("Sync campaignRoot in control/launch-spec.resolved.json with the actual campaign root.")

        source_root_raw = spec.get("sourceRoot")
        source_root = Path(str(source_root_raw)).resolve() if isinstance(source_root_raw, str) else None
        if source_root is None or not source_root.exists():
            errors.append(
                _issue(
                    level="error",
                    code="source_root_missing",
                    message="Resolved spec sourceRoot is missing or does not exist.",
                    path=str(spec_path),
                    hint="Point sourceRoot at an existing Lean project.",
                )
            )
            suggested_fixes.append("Point sourceRoot at an existing Lean project.")
        elif not (source_root / "lean-toolchain").exists():
            errors.append(
                _issue(
                    level="error",
                    code="source_root_not_lean_project",
                    message="sourceRoot does not look like a Lean project because lean-toolchain is missing.",
                    path=str(source_root),
                    hint="Use a warmed Lean project clone as the source root.",
                )
            )
            suggested_fixes.append("Use a valid Lean project as sourceRoot.")
        else:
            match_regex = None
            plan_shards = spec.get("planShards")
            if isinstance(plan_shards, Mapping) and isinstance(plan_shards.get("matchRegex"), str):
                match_regex = str(plan_shards.get("matchRegex"))
            try:
                matched_scope_count = _matches_scope_count(source_root, match_regex)
            except re.error:
                matched_scope_count = None
                errors.append(
                    _issue(
                        level="error",
                        code="scope_regex_invalid",
                        message="planShards.matchRegex is not a valid regular expression.",
                        path=str(spec_path),
                        hint="Fix planShards.matchRegex before launch.",
                    )
                )
                suggested_fixes.append("Fix planShards.matchRegex in the resolved spec.")
            else:
                if matched_scope_count == 0:
                    errors.append(
                        _issue(
                            level="error",
                            code="scope_regex_matches_no_files",
                            message="planShards.matchRegex matches no Lean files under sourceRoot.",
                            path=str(spec_path),
                            hint="Adjust the source root or the scope regex so the campaign actually has targets.",
                        )
                    )
                    suggested_fixes.append("Adjust planShards.matchRegex or sourceRoot so at least one Lean file matches.")

        if detected_mode == "benchmark_faithful" and bool(spec.get("preloadHistoricalRoutes")):
            errors.append(
                _issue(
                    level="error",
                    code="historical_routes_forbidden",
                    message="Benchmark-faithful campaigns must not preload historical routes.",
                    path=str(spec_path),
                    hint="Set preloadHistoricalRoutes = false or switch the campaign mode to a non benchmark path.",
                )
            )
            suggested_fixes.append("Disable preloadHistoricalRoutes for benchmark-faithful runs.")

        helper_disabled = _helper_env_disabled(spec)
        if not helper_disabled and not helper_env_path.exists():
            warnings.append(
                _issue(
                    level="warning",
                    code="helper_env_missing",
                    message="Default helper env file is missing, so helper-backed runs may start without the intended provider config.",
                    path=str(helper_env_path),
                    hint="Create examples/helper.env or explicitly disable helper for this campaign.",
                )
            )
            suggested_fixes.append("Create examples/helper.env or disable helper explicitly for this campaign.")
        elif not helper_disabled and helper_env_path.exists():
            helper_env_values = load_helper_env_file(helper_env_path)
            configured_provider = helper_provider(helper_env_values)
            configured_model = helper_model(helper_env_values)
            if helper_model_provider_mismatch(configured_provider, configured_model):
                warnings.append(
                    _issue(
                        level="warning",
                        code="helper_model_provider_mismatch",
                        message=(
                            "examples/helper.env uses a helper model name that looks mismatched with the selected provider."
                        ),
                        path=str(helper_env_path),
                        hint=(
                            "If this is not an intentional OpenAI-compatible relay setup, align "
                            "ARCHON_HELPER_PROVIDER and ARCHON_HELPER_MODEL before launch."
                        ),
                    )
                )
                suggested_fixes.append("Align ARCHON_HELPER_PROVIDER and ARCHON_HELPER_MODEL in examples/helper.env.")
            if probe_helper:
                helper_probe_payload = probe_helper_transport(
                    repo_root=repo_root,
                    env_file=helper_env_path,
                    timeout_seconds=helper_probe_timeout_seconds,
                )
                if helper_probe_payload.get("status") != "ok":
                    warnings.append(
                        _issue(
                            level="warning",
                            code="helper_probe_failed",
                            message="Helper transport probe failed before launch.",
                            path=str(helper_env_path),
                            hint="Fix helper credentials/provider config or disable helper explicitly for this campaign.",
                        )
                    )
                    suggested_fixes.append("Repair helper credentials/provider config or disable helper for this campaign.")

    all_warnings_are_errors = strict and warnings
    valid = not errors and not all_warnings_are_errors
    if all_warnings_are_errors:
        errors.extend(
            [
                _issue(
                    level="error",
                    code=item["code"],
                    message=item["message"],
                    path=item.get("path"),
                    hint=item.get("hint"),
                )
                for item in warnings
            ]
        )

    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": _utc_now(),
        "campaignRoot": str(campaign_root),
        "repoRoot": str(repo_root),
        "detectedMode": detected_mode,
        "valid": valid,
        "strict": strict,
        "errors": errors,
        "warnings": warnings if not strict else [],
        "suggestedFixes": sorted(dict.fromkeys(suggested_fixes)),
        "checkedPaths": {
            "missionBrief": str(mission_brief_path),
            "operatorJournal": str(journal_path),
            "resolvedSpec": str(spec_path),
            "helperEnv": str(helper_env_path),
        },
        "helperProbe": helper_probe_payload,
    }
