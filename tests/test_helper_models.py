from __future__ import annotations

import pytest

from archonlib.helper_models import HelperProviderConfig, resolve_helper_provider_config


def test_resolve_helper_provider_config_returns_none_when_disabled():
    assert resolve_helper_provider_config(None) is None
    assert resolve_helper_provider_config(False) is None
    assert resolve_helper_provider_config({"enabled": False}) is None


def test_resolve_helper_provider_config_applies_provider_defaults():
    config = resolve_helper_provider_config(
        {
            "enabled": True,
            "provider": "gemini",
            "model": "gemini-3.1-pro-preview",
        }
    )

    assert isinstance(config, HelperProviderConfig)
    assert config.provider == "gemini"
    assert config.model == "gemini-3.1-pro-preview"
    assert config.api_key_env == "GEMINI_API_KEY"
    assert config.base_url_env == "GEMINI_BASE_URL"
    assert config.max_retries == 5
    assert config.initial_backoff_seconds == 5
    assert config.timeout_seconds == 300


def test_resolve_helper_provider_config_allows_openai_compatible_override():
    config = resolve_helper_provider_config(
        {
            "enabled": True,
            "provider": "openai",
            "model": "deepseek-reasoner",
            "apiKeyEnv": "DEEPSEEK_API_KEY",
            "baseUrlEnv": "DEEPSEEK_BASE_URL",
            "maxRetries": 7,
            "initialBackoffSeconds": 11,
            "timeoutSeconds": 420,
        }
    )

    assert config.api_key_env == "DEEPSEEK_API_KEY"
    assert config.base_url_env == "DEEPSEEK_BASE_URL"
    assert config.max_retries == 7
    assert config.initial_backoff_seconds == 11
    assert config.timeout_seconds == 420


def test_resolve_helper_provider_config_accepts_snake_case_fields_for_toml():
    config = resolve_helper_provider_config(
        {
            "enabled": True,
            "provider": "openai",
            "model": "deepseek-reasoner",
            "api_key_env": "DEEPSEEK_API_KEY",
            "base_url_env": "DEEPSEEK_BASE_URL",
            "max_retries": 6,
            "initial_backoff_seconds": 8,
            "timeout_seconds": 333,
        }
    )

    assert config.api_key_env == "DEEPSEEK_API_KEY"
    assert config.base_url_env == "DEEPSEEK_BASE_URL"
    assert config.max_retries == 6
    assert config.initial_backoff_seconds == 8
    assert config.timeout_seconds == 333


def test_resolve_helper_provider_config_rejects_unknown_provider():
    with pytest.raises(ValueError, match="unsupported helper provider"):
        resolve_helper_provider_config(
            {
                "enabled": True,
                "provider": "anthropic",
                "model": "claude-sonnet",
            }
        )


def test_resolve_helper_provider_config_requires_model_when_enabled():
    with pytest.raises(ValueError, match="helper model is required"):
        resolve_helper_provider_config({"enabled": True, "provider": "openrouter"})
