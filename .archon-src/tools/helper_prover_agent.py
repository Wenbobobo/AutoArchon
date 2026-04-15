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
import re
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
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


HELPER_PHASES = ("plan", "prover")


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
        "fallbacks": [
            {
                "provider": fallback.provider,
                "model": fallback.model,
                "apiKeyEnv": fallback.api_key_env,
                "baseUrlEnv": fallback.base_url_env,
                "maxRetries": fallback.max_retries,
                "initialBackoffSeconds": fallback.initial_backoff_seconds,
                "timeoutSeconds": fallback.timeout_seconds,
            }
            for fallback in (config.fallbacks if config is not None else ())
        ],
        "planPolicy": {
            "enabled": runtime_config.helper_plan.enabled,
            "maxCallsPerIteration": runtime_config.helper_plan.max_calls_per_iteration,
            "triggerOnMissingInfrastructure": runtime_config.helper_plan.trigger_on_missing_infrastructure,
            "triggerOnExternalReference": runtime_config.helper_plan.trigger_on_external_reference,
            "triggerOnRepeatedFailure": runtime_config.helper_plan.trigger_on_repeated_failure,
            "notesDir": runtime_config.helper_plan.notes_dir,
        },
        "proverPolicy": {
            "enabled": runtime_config.helper_prover.enabled,
            "maxCallsPerSession": runtime_config.helper_prover.max_calls_per_session,
            "triggerOnMissingInfrastructure": runtime_config.helper_prover.trigger_on_missing_infrastructure,
            "triggerOnLspTimeout": runtime_config.helper_prover.trigger_on_lsp_timeout,
            "triggerOnFirstStuckAttempt": runtime_config.helper_prover.trigger_on_first_stuck_attempt,
            "notesDir": runtime_config.helper_prover.notes_dir,
        },
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
    parser.add_argument(
        "--phase",
        choices=HELPER_PHASES,
        help="Optional helper phase used for auto note routing (`plan` or `prover`)",
    )
    parser.add_argument("--rel-path", help="Optional target Lean file relative path such as FATEM/42.lean")
    parser.add_argument("--reason", help="Optional short trigger/reason label such as lsp_timeout or repeated_failure")
    parser.add_argument(
        "--write-note",
        help="Optional path to also write the helper response, or `auto` to route into the configured notes_dir with metadata",
    )
    parser.add_argument("--print-effective-config", action="store_true", help="Print resolved helper config as JSON and exit")
    parser.add_argument("--max-retries", type=int, help="Override retry budget")
    parser.add_argument("--initial-backoff-seconds", type=int, help="Override retry backoff base")
    parser.add_argument("--timeout-seconds", type=int, help="Override request timeout")
    return parser.parse_args()


def _slugify(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip()).strip("._-") or "note"


def _note_dir_for_phase(runtime_config: RuntimeConfig, phase: str) -> str:
    if phase == "plan":
        return runtime_config.helper_plan.notes_dir
    return runtime_config.helper_prover.notes_dir


def _auto_note_path(*, workspace: Path, runtime_config: RuntimeConfig, phase: str, rel_path: str | None, reason: str | None) -> Path:
    notes_dir = _note_dir_for_phase(runtime_config, phase)
    stem_parts: list[str] = []
    if rel_path:
        stem_parts.append(_slugify(rel_path.replace("/", "_")))
    else:
        stem_parts.append("general")
    stem_parts.append(phase)
    if reason:
        stem_parts.append(_slugify(reason))
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = "__".join(stem_parts + [timestamp]) + ".md"
    return (workspace / notes_dir / filename).resolve()


def _render_auto_note(
    *,
    response: str,
    used_config: HelperProviderConfig,
    phase: str,
    rel_path: str | None,
    reason: str | None,
    config_path: str | None,
) -> str:
    lines = [
        "# Helper Note",
        "",
        f"- Generated at: `{datetime.now(timezone.utc).isoformat()}`",
        f"- Phase: `{phase}`",
        f"- Provider: `{used_config.provider}`",
        f"- Model: `{used_config.model}`",
        f"- Config path: `{config_path}`",
    ]
    if rel_path:
        lines.append(f"- Target: `{rel_path}`")
    if reason:
        lines.append(f"- Reason: `{reason}`")
    lines.extend(["", "## Helper Output", "", response.rstrip(), ""])
    return "\n".join(lines)


def _call_provider(
    informal_agent: Any,
    *,
    prompt: str,
    think: bool,
    config: HelperProviderConfig,
) -> str:
    fn = {
        "gemini": informal_agent.call_gemini,
        "openai": informal_agent.call_openai,
        "openrouter": informal_agent.call_openrouter,
    }[config.provider]
    api_key_env, base_url_env = _provider_env_names(
        informal_agent,
        provider=config.provider,
        config=config,
    )
    with _patched_transport(
        informal_agent,
        provider=config.provider,
        api_key_env=api_key_env,
        base_url_env=base_url_env,
    ):
        return fn(
            prompt,
            config.model,
            think,
            max_retries=config.max_retries,
            initial_backoff_seconds=config.initial_backoff_seconds,
            timeout_seconds=config.timeout_seconds,
        )


def _primary_attempt_config(
    informal_agent: Any,
    *,
    provider: str,
    model: str | None,
    runtime_config: RuntimeConfig,
    max_retries: int | None,
    initial_backoff_seconds: int | None,
    timeout_seconds: int | None,
) -> HelperProviderConfig:
    config = runtime_config.helper
    api_key_env, base_url_env = _provider_env_names(
        informal_agent,
        provider=provider,
        config=config,
    )
    return HelperProviderConfig(
        provider=provider,
        model=model or (config.model if config is not None and config.provider == provider else informal_agent.DEFAULTS[provider]),
        api_key_env=api_key_env,
        base_url_env=base_url_env,
        max_retries=max_retries if max_retries is not None else (config.max_retries if config is not None and config.provider == provider else informal_agent.MAX_RETRIES),
        initial_backoff_seconds=(
            initial_backoff_seconds
            if initial_backoff_seconds is not None
            else (config.initial_backoff_seconds if config is not None and config.provider == provider else informal_agent.INITIAL_BACKOFF_SECONDS)
        ),
        timeout_seconds=(
            timeout_seconds
            if timeout_seconds is not None
            else (config.timeout_seconds if config is not None and config.provider == provider else informal_agent.TIMEOUT)
        ),
        fallbacks=config.fallbacks if config is not None and config.provider == provider else (),
    )


def _attempt_chain(
    informal_agent: Any,
    *,
    provider: str,
    model: str | None,
    runtime_config: RuntimeConfig,
    max_retries: int | None,
    initial_backoff_seconds: int | None,
    timeout_seconds: int | None,
    explicit_provider: bool,
) -> tuple[HelperProviderConfig, ...]:
    primary = _primary_attempt_config(
        informal_agent,
        provider=provider,
        model=model,
        runtime_config=runtime_config,
        max_retries=max_retries,
        initial_backoff_seconds=initial_backoff_seconds,
        timeout_seconds=timeout_seconds,
    )
    if explicit_provider:
        return (primary,)

    ordered: list[HelperProviderConfig] = [primary]
    seen: set[tuple[str, str, str, str | None]] = {
        (primary.provider, primary.model, primary.api_key_env, primary.base_url_env)
    }
    for fallback in primary.fallbacks:
        key = (fallback.provider, fallback.model, fallback.api_key_env, fallback.base_url_env)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(fallback)
    return tuple(ordered)


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
    if args.write_note == "auto" and args.phase is None:
        raise SystemExit("Error: --phase is required when --write-note auto is used.")
    attempts = _attempt_chain(
        informal_agent,
        provider=provider,
        model=args.model,
        runtime_config=runtime_config,
        max_retries=args.max_retries,
        initial_backoff_seconds=args.initial_backoff_seconds,
        timeout_seconds=args.timeout_seconds,
        explicit_provider=args.provider is not None,
    )
    failures: list[str] = []
    response: str | None = None
    used_config: HelperProviderConfig | None = None
    for index, attempt in enumerate(attempts):
        try:
            response = _call_provider(
                informal_agent,
                prompt=args.prompt,
                think=args.think,
                config=attempt,
            )
            used_config = attempt
            if index > 0:
                sys.stderr.write(
                    f"[archon-helper] primary provider failed; used fallback {attempt.provider}:{attempt.model}\n"
                )
            break
        except SystemExit as exc:
            failures.append(f"{attempt.provider}:{attempt.model}: {exc}")
            if index + 1 < len(attempts):
                sys.stderr.write(
                    f"[archon-helper] helper attempt failed on {attempt.provider}:{attempt.model}; trying next fallback\n"
                )
                continue
            joined = "\n".join(f"- {message}" for message in failures)
            raise SystemExit(f"Error: helper transport failed across configured providers.\n{joined}") from exc
    assert response is not None
    assert used_config is not None
    if args.write_note:
        if args.write_note == "auto":
            note_path = _auto_note_path(
                workspace=workspace,
                runtime_config=runtime_config,
                phase=args.phase,
                rel_path=args.rel_path,
                reason=args.reason,
            )
            note_text = _render_auto_note(
                response=response,
                used_config=used_config,
                phase=args.phase,
                rel_path=args.rel_path,
                reason=args.reason,
                config_path=config_path,
            )
        else:
            note_path = Path(args.write_note).resolve()
            note_text = response + ("\n" if not response.endswith("\n") else "")
        note_path.parent.mkdir(parents=True, exist_ok=True)
        note_path.write_text(note_text, encoding="utf-8")
        if args.write_note == "auto":
            sys.stderr.write(f"[archon-helper] wrote note {note_path}\n")
    sys.stdout.write(response)
    if response and not response.endswith("\n"):
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
