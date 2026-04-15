from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from archonlib.helper_models import HelperProviderConfig, resolve_helper_provider_config

try:  # pragma: no cover - exercised on Python 3.11+ in normal test runs
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib


DEFAULT_RUNTIME_CONFIG_NAME = "runtime-config.toml"
LEGACY_HELPER_CONFIG_NAME = "helper-provider.json"


def runtime_config_path(workspace: Path) -> Path:
    return workspace / ".archon" / DEFAULT_RUNTIME_CONFIG_NAME


def legacy_helper_config_path(workspace: Path) -> Path:
    return workspace / ".archon" / LEGACY_HELPER_CONFIG_NAME


@dataclass(frozen=True)
class HelperPlanPolicy:
    enabled: bool
    max_calls_per_iteration: int
    trigger_on_missing_infrastructure: bool
    trigger_on_external_reference: bool
    trigger_on_repeated_failure: bool
    notes_dir: str


@dataclass(frozen=True)
class HelperProverPolicy:
    enabled: bool
    max_calls_per_session: int
    trigger_on_missing_infrastructure: bool
    trigger_on_lsp_timeout: bool
    trigger_on_first_stuck_attempt: bool
    notes_dir: str


@dataclass(frozen=True)
class ObservabilityConfig:
    write_progress_surface: bool


@dataclass(frozen=True)
class RuntimeConfig:
    helper: HelperProviderConfig | None
    helper_plan: HelperPlanPolicy
    helper_prover: HelperProverPolicy
    observability: ObservabilityConfig
    source_path: str | None
    legacy_helper_json_used: bool


def _as_bool(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise ValueError(f"expected boolean value, got {value!r}")


def _as_int(value: object, *, default: int, minimum: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and value.strip():
        parsed = int(value)
    else:
        raise ValueError(f"expected integer value, got {value!r}")
    if parsed < minimum:
        raise ValueError(f"expected integer >= {minimum}, got {parsed}")
    return parsed


def _as_str(value: object, *, default: str) -> str:
    if value is None:
        return default
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise ValueError(f"expected non-empty string value, got {value!r}")


def _mapping(payload: object, *, field: str) -> Mapping[str, Any]:
    if isinstance(payload, Mapping):
        return payload
    raise ValueError(f"{field} must be a table/mapping")


def _read_toml(path: Path) -> Mapping[str, Any]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, Mapping):
        raise ValueError("runtime config root must be a table")
    return data


def _resolve_helper_plan_policy(payload: Mapping[str, Any] | None) -> HelperPlanPolicy:
    mapping = payload or {}
    return HelperPlanPolicy(
        enabled=_as_bool(mapping.get("enabled"), default=True),
        max_calls_per_iteration=_as_int(mapping.get("max_calls_per_iteration"), default=1, minimum=0),
        trigger_on_missing_infrastructure=_as_bool(mapping.get("trigger_on_missing_infrastructure"), default=True),
        trigger_on_external_reference=_as_bool(mapping.get("trigger_on_external_reference"), default=True),
        trigger_on_repeated_failure=_as_bool(mapping.get("trigger_on_repeated_failure"), default=True),
        notes_dir=_as_str(mapping.get("notes_dir"), default=".archon/informal/helper"),
    )


def _resolve_helper_prover_policy(payload: Mapping[str, Any] | None) -> HelperProverPolicy:
    mapping = payload or {}
    return HelperProverPolicy(
        enabled=_as_bool(mapping.get("enabled"), default=True),
        max_calls_per_session=_as_int(mapping.get("max_calls_per_session"), default=2, minimum=0),
        trigger_on_missing_infrastructure=_as_bool(mapping.get("trigger_on_missing_infrastructure"), default=True),
        trigger_on_lsp_timeout=_as_bool(mapping.get("trigger_on_lsp_timeout"), default=True),
        trigger_on_first_stuck_attempt=_as_bool(mapping.get("trigger_on_first_stuck_attempt"), default=True),
        notes_dir=_as_str(mapping.get("notes_dir"), default=".archon/informal/helper"),
    )


def _resolve_observability_config(payload: Mapping[str, Any] | None) -> ObservabilityConfig:
    mapping = payload or {}
    return ObservabilityConfig(
        write_progress_surface=_as_bool(mapping.get("write_progress_surface"), default=True),
    )


def _runtime_from_mapping(payload: Mapping[str, Any], *, source_path: Path) -> RuntimeConfig:
    helper_payload = payload.get("helper")
    if helper_payload is not None:
        helper_mapping = _mapping(helper_payload, field="helper")
        helper_config = resolve_helper_provider_config(helper_mapping)
        helper_plan = _resolve_helper_plan_policy(
            _mapping(helper_mapping["plan"], field="helper.plan") if "plan" in helper_mapping else None
        )
        helper_prover = _resolve_helper_prover_policy(
            _mapping(helper_mapping["prover"], field="helper.prover") if "prover" in helper_mapping else None
        )
    else:
        helper_config = None
        helper_plan = _resolve_helper_plan_policy(None)
        helper_prover = _resolve_helper_prover_policy(None)

    observability_payload = payload.get("observability")
    observability = _resolve_observability_config(
        _mapping(observability_payload, field="observability") if observability_payload is not None else None
    )

    return RuntimeConfig(
        helper=helper_config,
        helper_plan=helper_plan,
        helper_prover=helper_prover,
        observability=observability,
        source_path=str(source_path),
        legacy_helper_json_used=False,
    )


def _runtime_from_legacy_helper_json(path: Path) -> RuntimeConfig:
    payload = json.loads(path.read_text(encoding="utf-8"))
    helper_payload = _mapping(payload, field="legacy helper config")
    return RuntimeConfig(
        helper=resolve_helper_provider_config(helper_payload),
        helper_plan=_resolve_helper_plan_policy(None),
        helper_prover=_resolve_helper_prover_policy(None),
        observability=_resolve_observability_config(None),
        source_path=str(path),
        legacy_helper_json_used=True,
    )


def load_runtime_config_from_path(path: Path) -> RuntimeConfig:
    resolved = path.resolve()
    if not resolved.exists():
        raise FileNotFoundError(resolved)
    if resolved.suffix.lower() == ".json":
        return _runtime_from_legacy_helper_json(resolved)
    return _runtime_from_mapping(_read_toml(resolved), source_path=resolved)


def load_runtime_config(workspace: Path) -> RuntimeConfig:
    runtime_path = runtime_config_path(workspace)
    if runtime_path.exists():
        return load_runtime_config_from_path(runtime_path)
    legacy_path = legacy_helper_config_path(workspace)
    if legacy_path.exists():
        return load_runtime_config_from_path(legacy_path)
    return RuntimeConfig(
        helper=None,
        helper_plan=_resolve_helper_plan_policy(None),
        helper_prover=_resolve_helper_prover_policy(None),
        observability=_resolve_observability_config(None),
        source_path=None,
        legacy_helper_json_used=False,
    )


def render_default_runtime_config(
    *,
    helper_enabled: bool,
    helper_provider: str,
    helper_model: str,
    helper_api_key_env: str,
    helper_base_url_env: str,
    helper_max_retries: int,
    helper_initial_backoff_seconds: int,
    helper_timeout_seconds: int,
    write_progress_surface: bool = True,
) -> str:
    helper_enabled_literal = "true" if helper_enabled else "false"
    observability_literal = "true" if write_progress_surface else "false"
    return (
        "# AutoArchon runtime config\n"
        "# This is the canonical per-workspace config surface.\n\n"
        "[helper]\n"
        f"enabled = {helper_enabled_literal}\n"
        f'provider = "{helper_provider}"\n'
        f'model = "{helper_model}"\n'
        f'api_key_env = "{helper_api_key_env}"\n'
        f'base_url_env = "{helper_base_url_env}"\n'
        f"max_retries = {helper_max_retries}\n"
        f"initial_backoff_seconds = {helper_initial_backoff_seconds}\n"
        f"timeout_seconds = {helper_timeout_seconds}\n\n"
        "[helper.plan]\n"
        "enabled = true\n"
        "max_calls_per_iteration = 1\n"
        "trigger_on_missing_infrastructure = true\n"
        "trigger_on_external_reference = true\n"
        "trigger_on_repeated_failure = true\n"
        'notes_dir = ".archon/informal/helper"\n\n'
        "[helper.prover]\n"
        "enabled = true\n"
        "max_calls_per_session = 2\n"
        "trigger_on_missing_infrastructure = true\n"
        "trigger_on_lsp_timeout = true\n"
        "trigger_on_first_stuck_attempt = true\n"
        'notes_dir = ".archon/informal/helper"\n\n'
        "[observability]\n"
        f"write_progress_surface = {observability_literal}\n"
    )
