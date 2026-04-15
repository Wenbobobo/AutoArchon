from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


SUPPORTED_HELPER_PROVIDERS = {"openai", "gemini", "openrouter"}


@dataclass(frozen=True)
class HelperProviderConfig:
    provider: str
    model: str
    api_key_env: str
    base_url_env: str | None
    max_retries: int
    initial_backoff_seconds: int
    timeout_seconds: int


_PROVIDER_DEFAULTS: dict[str, dict[str, str | None]] = {
    "openai": {
        "api_key_env": "OPENAI_API_KEY",
        "base_url_env": "OPENAI_BASE_URL",
    },
    "gemini": {
        "api_key_env": "GEMINI_API_KEY",
        "base_url_env": "GEMINI_BASE_URL",
    },
    "openrouter": {
        "api_key_env": "OPENROUTER_API_KEY",
        "base_url_env": "OPENROUTER_BASE_URL",
    },
}


def _as_int(value: object, *, field: str, minimum: int = 1) -> int:
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and value.strip():
        parsed = int(value)
    else:
        raise ValueError(f"{field} must be an integer")
    if parsed < minimum:
        raise ValueError(f"{field} must be >= {minimum}")
    return parsed


def _mapping_get(payload: Mapping[str, Any], snake_key: str, camel_key: str) -> object:
    if snake_key in payload:
        return payload[snake_key]
    return payload.get(camel_key)


def resolve_helper_provider_config(payload: Mapping[str, Any] | bool | None) -> HelperProviderConfig | None:
    if payload is None or payload is False:
        return None
    if not isinstance(payload, Mapping):
        raise ValueError("helper provider config must be a mapping")
    if payload.get("enabled") is False:
        return None

    provider = payload.get("provider")
    if not isinstance(provider, str) or not provider.strip():
        raise ValueError("helper provider is required when helper config is enabled")
    provider = provider.strip()
    if provider not in SUPPORTED_HELPER_PROVIDERS:
        raise ValueError(f"unsupported helper provider: {provider}")

    model = payload.get("model")
    if not isinstance(model, str) or not model.strip():
        raise ValueError("helper model is required when helper config is enabled")

    defaults = _PROVIDER_DEFAULTS[provider]
    api_key_env = _mapping_get(payload, "api_key_env", "apiKeyEnv") or defaults["api_key_env"]
    base_url_env = _mapping_get(payload, "base_url_env", "baseUrlEnv") or defaults["base_url_env"]

    if not isinstance(api_key_env, str) or not api_key_env.strip():
        raise ValueError("helper apiKeyEnv must be a non-empty string")
    if base_url_env is not None and (not isinstance(base_url_env, str) or not base_url_env.strip()):
        raise ValueError("helper baseUrlEnv must be a non-empty string when provided")

    max_retries = _as_int(_mapping_get(payload, "max_retries", "maxRetries") or 5, field="helper maxRetries")
    initial_backoff_seconds = _as_int(
        _mapping_get(payload, "initial_backoff_seconds", "initialBackoffSeconds") or 5,
        field="helper initialBackoffSeconds",
    )
    timeout_seconds = _as_int(_mapping_get(payload, "timeout_seconds", "timeoutSeconds") or 300, field="helper timeoutSeconds")

    return HelperProviderConfig(
        provider=provider,
        model=model.strip(),
        api_key_env=api_key_env.strip(),
        base_url_env=base_url_env.strip() if isinstance(base_url_env, str) else None,
        max_retries=max_retries,
        initial_backoff_seconds=initial_backoff_seconds,
        timeout_seconds=timeout_seconds,
    )
