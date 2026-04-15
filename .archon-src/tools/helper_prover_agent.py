#!/usr/bin/env python3
"""Bounded helper-prover wrapper around the informal agent transport.

This wrapper adds one stable runtime surface for AutoArchon runs:

- canonical config file: `.archon/runtime-config.toml`
- legacy helper-only config: `.archon/helper-provider.json`
- optional explicit note output path
- effective-config inspection for debugging

The underlying transport still lives in `informal_agent.py`, so supported
providers and API behavior stay aligned.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from archonlib.helper_models import HelperProviderConfig
from archonlib.runtime_config import (
    RuntimeConfig,
    load_runtime_config,
    load_runtime_config_from_path,
    runtime_config_path,
)


def _load_informal_agent():
    module_path = Path(__file__).resolve().with_name("informal_agent.py")
    spec = importlib.util.spec_from_file_location("archon_informal_agent", module_path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError(f"cannot load informal agent module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _provider_env_names(
    informal_agent: Any,
    *,
    provider: str,
    config: HelperProviderConfig | None,
) -> tuple[str, str | None]:
    if config is not None and config.provider == provider:
        return config.api_key_env, config.base_url_env
    return informal_agent.API_KEY_ENVS[provider], informal_agent.BASE_URL_ENVS[provider]


def _effective_config(
    informal_agent: Any,
    *,
    provider: str,
    model: str | None,
    runtime_config: RuntimeConfig,
    max_retries: int | None,
    initial_backoff_seconds: int | None,
    timeout_seconds: int | None,
    config_path: str | None,
) -> dict[str, Any]:
    config = runtime_config.helper
    api_key_env, base_url_env = _provider_env_names(
        informal_agent,
        provider=provider,
        config=config,
    )
    return {
        "configPath": str(config_path),
        "configEnabled": config is not None,
        "legacyHelperJsonUsed": runtime_config.legacy_helper_json_used,
        "provider": provider,
        "model": model or (config.model if config is not None else informal_agent.DEFAULTS[provider]),
        "apiKeyEnv": api_key_env,
        "baseUrlEnv": base_url_env,
        "maxRetries": max_retries if max_retries is not None else (config.max_retries if config is not None else informal_agent.MAX_RETRIES),
        "initialBackoffSeconds": (
            initial_backoff_seconds
            if initial_backoff_seconds is not None
            else (config.initial_backoff_seconds if config is not None else informal_agent.INITIAL_BACKOFF_SECONDS)
        ),
        "timeoutSeconds": (
            timeout_seconds
            if timeout_seconds is not None
            else (config.timeout_seconds if config is not None else informal_agent.TIMEOUT)
        ),
    }


@contextmanager
def _patched_transport(informal_agent: Any, *, provider: str, api_key_env: str, base_url_env: str | None) -> Iterator[None]:
    original_api_key_env = informal_agent.API_KEY_ENVS[provider]
    original_base_url_env = informal_agent.BASE_URL_ENVS[provider]
    informal_agent.API_KEY_ENVS[provider] = api_key_env
    if base_url_env is not None:
        informal_agent.BASE_URL_ENVS[provider] = base_url_env
    try:
        yield
    finally:
        informal_agent.API_KEY_ENVS[provider] = original_api_key_env
        informal_agent.BASE_URL_ENVS[provider] = original_base_url_env


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", nargs="?", help="Helper prompt to send to the side model")
    parser.add_argument("--config", help="Optional runtime-config.toml or legacy helper-provider.json override path")
    parser.add_argument("--provider", choices=["openai", "gemini", "openrouter"], help="Override provider")
    parser.add_argument("--model", help="Override model")
    parser.add_argument("--think", action="store_true", help="Request higher reasoning where supported")
    parser.add_argument("--write-note", help="Optional path to also write the helper response")
    parser.add_argument("--print-effective-config", action="store_true", help="Print resolved helper config as JSON and exit")
    parser.add_argument("--max-retries", type=int, help="Override retry budget")
    parser.add_argument("--initial-backoff-seconds", type=int, help="Override retry backoff base")
    parser.add_argument("--timeout-seconds", type=int, help="Override request timeout")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    informal_agent = _load_informal_agent()
    workspace = Path.cwd()
    if args.config:
        runtime_config = load_runtime_config_from_path(Path(args.config).resolve())
    else:
        runtime_config = load_runtime_config(workspace)
    config = runtime_config.helper
    config_path = runtime_config.source_path or str(runtime_config_path(workspace))
    provider = args.provider or (config.provider if config is not None else None)
    if provider is None:
        raise SystemExit(
            "Error: helper provider is required. Set --provider or enable it in .archon/runtime-config.toml."
        )

    effective = _effective_config(
        informal_agent,
        provider=provider,
        model=args.model,
        runtime_config=runtime_config,
        max_retries=args.max_retries,
        initial_backoff_seconds=args.initial_backoff_seconds,
        timeout_seconds=args.timeout_seconds,
        config_path=config_path,
    )
    if args.print_effective_config:
        sys.stdout.write(json.dumps(effective, indent=2, sort_keys=True) + "\n")
        return 0

    if not args.prompt:
        raise SystemExit("Error: prompt is required unless --print-effective-config is used.")

    fn = {
        "gemini": informal_agent.call_gemini,
        "openai": informal_agent.call_openai,
        "openrouter": informal_agent.call_openrouter,
    }[provider]
    api_key_env, base_url_env = _provider_env_names(
        informal_agent,
        provider=provider,
        config=config,
    )
    with _patched_transport(
        informal_agent,
        provider=provider,
        api_key_env=api_key_env,
        base_url_env=base_url_env,
    ):
        response = fn(
            args.prompt,
            str(effective["model"]),
            args.think,
            max_retries=int(effective["maxRetries"]),
            initial_backoff_seconds=int(effective["initialBackoffSeconds"]),
            timeout_seconds=int(effective["timeoutSeconds"]),
        )
    if args.write_note:
        note_path = Path(args.write_note).resolve()
        note_path.parent.mkdir(parents=True, exist_ok=True)
        note_path.write_text(response + ("\n" if not response.endswith("\n") else ""), encoding="utf-8")
    sys.stdout.write(response)
    if response and not response.endswith("\n"):
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
