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
import os
import re
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from archonlib.helper_index import append_helper_index_event, helper_index_entries
from archonlib.helper_models import HelperProviderConfig
from archonlib.runtime_config import (
    RuntimeConfig,
    load_runtime_config,
    load_runtime_config_from_path,
    runtime_config_path,
)


HELPER_PHASES = ("plan", "prover")
ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
HELPER_PROMPT_PACKS = (
    "external_reference",
    "first_stuck_attempt",
    "generic",
    "lsp_timeout",
    "missing_infrastructure",
    "repeated_failure",
)
AUTO_PROMPT_PACKS: dict[tuple[str, str], str] = {
    ("plan", "external_reference"): "external_reference",
    ("plan", "missing_infrastructure"): "missing_infrastructure",
    ("plan", "repeated_failure"): "repeated_failure",
    ("prover", "first_stuck_attempt"): "first_stuck_attempt",
    ("prover", "lsp_timeout"): "lsp_timeout",
    ("prover", "missing_infrastructure"): "missing_infrastructure",
}


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
    phase: str | None,
    reason: str | None,
    requested_prompt_pack: str | None,
    selected_prompt_pack: str | None,
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
            "maxCallsPerReason": runtime_config.helper_plan.max_calls_per_reason,
            "cooldownIterationsPerReason": runtime_config.helper_plan.cooldown_iterations_per_reason,
            "triggerOnMissingInfrastructure": runtime_config.helper_plan.trigger_on_missing_infrastructure,
            "triggerOnExternalReference": runtime_config.helper_plan.trigger_on_external_reference,
            "triggerOnRepeatedFailure": runtime_config.helper_plan.trigger_on_repeated_failure,
            "reuseRecentNoteByReason": runtime_config.helper_plan.reuse_recent_note_by_reason,
            "notesDir": runtime_config.helper_plan.notes_dir,
        },
        "proverPolicy": {
            "enabled": runtime_config.helper_prover.enabled,
            "maxCallsPerSession": runtime_config.helper_prover.max_calls_per_session,
            "maxCallsPerReason": runtime_config.helper_prover.max_calls_per_reason,
            "cooldownAttemptsPerReason": runtime_config.helper_prover.cooldown_attempts_per_reason,
            "triggerOnMissingInfrastructure": runtime_config.helper_prover.trigger_on_missing_infrastructure,
            "triggerOnLspTimeout": runtime_config.helper_prover.trigger_on_lsp_timeout,
            "triggerOnFirstStuckAttempt": runtime_config.helper_prover.trigger_on_first_stuck_attempt,
            "reuseRecentNoteByReason": runtime_config.helper_prover.reuse_recent_note_by_reason,
            "notesDir": runtime_config.helper_prover.notes_dir,
        },
        "promptPack": {
            "available": list(HELPER_PROMPT_PACKS),
            "requested": requested_prompt_pack,
            "selected": selected_prompt_pack,
        },
    }


@contextmanager
def _patched_transport(informal_agent: Any, *, provider: str, api_key_env: str, base_url_env: str | None) -> Iterator[None]:
    original_api_key_env = informal_agent.API_KEY_ENVS[provider]
    original_base_url_env = informal_agent.BASE_URL_ENVS[provider]
    resolved_api_key_env, injected_api_key = _materialize_transport_binding(
        provider=provider,
        configured=api_key_env,
        kind="API_KEY",
    )
    resolved_base_url_env, injected_base_url = _materialize_transport_binding(
        provider=provider,
        configured=base_url_env,
        kind="BASE_URL",
    )
    injected_values = {**injected_api_key, **injected_base_url}
    original_env_values = {name: os.environ.get(name) for name in injected_values}
    for name, value in injected_values.items():
        os.environ[name] = value
    informal_agent.API_KEY_ENVS[provider] = resolved_api_key_env or api_key_env
    if resolved_base_url_env is not None:
        informal_agent.BASE_URL_ENVS[provider] = resolved_base_url_env
    try:
        yield
    finally:
        informal_agent.API_KEY_ENVS[provider] = original_api_key_env
        informal_agent.BASE_URL_ENVS[provider] = original_base_url_env
        for name, previous in original_env_values.items():
            if previous is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = previous


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
    parser.add_argument("--iteration", type=int, help="Optional planner iteration index for cooldown/budget bookkeeping")
    parser.add_argument("--attempt", type=int, help="Optional prover attempt index for cooldown/budget bookkeeping")
    parser.add_argument(
        "--prompt-pack",
        choices=("auto",) + HELPER_PROMPT_PACKS,
        help="Optional structured helper prompt template to apply before transport",
    )
    parser.add_argument(
        "--force-fresh-call",
        action="store_true",
        help="Bypass note reuse heuristics and force a new provider call",
    )
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


def _normalize_reason(reason: str | None) -> str | None:
    if reason is None:
        return None
    lowered = reason.strip().lower().replace("-", "_").replace(" ", "_")
    lowered = re.sub(r"[^a-z0-9_]+", "_", lowered).strip("_")
    return lowered or None


def _selected_prompt_pack(*, phase: str | None, reason: str | None, requested: str | None) -> str | None:
    if requested is None:
        return None
    if requested != "auto":
        return requested
    normalized_reason = _normalize_reason(reason)
    if phase is not None and normalized_reason is not None:
        chosen = AUTO_PROMPT_PACKS.get((phase, normalized_reason))
        if chosen is not None:
            return chosen
    return "generic"


def _transport_binding_name(*, provider: str, kind: str) -> str:
    return f"ARCHON_HELPER_INLINE_{provider.upper()}_{kind}"


def _materialize_transport_binding(*, provider: str, configured: str | None, kind: str) -> tuple[str | None, dict[str, str]]:
    if configured is None:
        return None, {}
    if ENV_NAME_RE.fullmatch(configured):
        return configured, {}
    env_name = _transport_binding_name(provider=provider, kind=kind)
    return env_name, {env_name: configured}


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
    prompt_pack: str | None,
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
    if prompt_pack:
        lines.append(f"- Prompt pack: `{prompt_pack}`")
    lines.extend(["", "## Helper Output", "", response.rstrip(), ""])
    return "\n".join(lines)


def _render_prompt_with_pack(
    *,
    prompt: str,
    phase: str | None,
    rel_path: str | None,
    reason: str | None,
    prompt_pack: str,
) -> str:
    phase_value = phase or "unknown"
    reason_value = _normalize_reason(reason) or prompt_pack
    target_value = rel_path or "(unspecified)"

    constraints: list[str]
    response_format: list[str]
    if prompt_pack == "lsp_timeout":
        constraints = [
            "- Assume Lean LSP is unavailable or timing out; prefer routes that can be executed from local file context and bounded shell verification.",
            "- Keep the original benchmark theorem statement unchanged.",
            "- Return a concise, actionable route rather than a long essay.",
        ]
        response_format = [
            "1. Immediate next proving route using Lean-available ingredients.",
            "2. One to three likely lemmas, theorem-search queries, or file-local pivots.",
            "3. A blocker test describing when to stop and write a durable blocker artifact instead of looping.",
        ]
    elif prompt_pack == "missing_infrastructure":
        constraints = [
            "- Route around the missing infrastructure; do not assume unavailable Mathlib APIs can be added on the fly.",
            "- Keep the original benchmark theorem statement unchanged.",
            "- Prefer proof decompositions that the prover can implement locally in Lean 4 Mathlib.",
        ]
        response_format = [
            "1. Alternative proof route that avoids the missing infrastructure.",
            "2. Helper lemmas or subgoals that make the detour implementable.",
            "3. A quick obstruction test for when the route should be escalated as a blocker instead.",
        ]
    elif prompt_pack == "external_reference":
        constraints = [
            "- Translate the external reference into a short local proof plan rather than a literature survey.",
            "- Keep the original benchmark theorem statement unchanged.",
            "- Prefer steps that a Lean prover can execute without additional browsing after this handoff.",
        ]
        response_format = [
            "1. The essential argument distilled into local steps.",
            "2. Terms, lemmas, or search queries the prover should try first.",
            "3. Any reference-sensitive caveat that would change acceptance or blocker handling.",
        ]
    elif prompt_pack == "repeated_failure":
        constraints = [
            "- Assume the previous route has already failed more than once.",
            "- Do not repeat the same dead end in different words.",
            "- Keep the route short enough for the next planner/prover cycle to execute immediately.",
        ]
        response_format = [
            "1. A materially different route from the prior failed attempts.",
            "2. The first concrete edit or search pivot to try next.",
            "3. A warning about what not to retry.",
        ]
    elif prompt_pack == "first_stuck_attempt":
        constraints = [
            "- Assume the prover already made one serious formal attempt and got stuck.",
            "- Reuse any local partial progress instead of restarting from scratch.",
            "- Keep the original benchmark theorem statement unchanged.",
        ]
        response_format = [
            "1. Best next formalization route from the current partial state.",
            "2. One to three specific local pivots or helper lemmas to try.",
            "3. A blocker threshold for abandoning this route if it still does not move.",
        ]
    else:
        constraints = [
            "- Keep the original benchmark theorem statement unchanged.",
            "- Return a concise, execution-ready route.",
            "- Prefer Lean 4 Mathlib-friendly ingredients over abstract advice.",
        ]
        response_format = [
            "1. Immediate next route.",
            "2. Likely lemmas or searches to try.",
            "3. A blocker test or escalation condition.",
        ]

    lines = [
        f"You are a bounded AutoArchon helper supporting the `{phase_value}` phase.",
        f"Task class: `{reason_value}`.",
        f"Target file: `{target_value}`.",
        "",
        "Constraints:",
        *constraints,
        "",
        "Response format:",
        *response_format,
        "",
        "User request:",
        prompt,
    ]
    return "\n".join(lines)


def _phase_policy(runtime_config: RuntimeConfig, phase: str) -> Any:
    return runtime_config.helper_plan if phase == "plan" else runtime_config.helper_prover


def _matching_provider_call_entries(
    workspace: Path,
    *,
    phase: str,
    rel_path: str | None,
    reason: str,
) -> list[dict[str, Any]]:
    normalized_reason = _normalize_reason(reason)
    matches: list[dict[str, Any]] = []
    for entry in helper_index_entries(workspace):
        if entry.get("event") != "provider_call":
            continue
        if entry.get("phase") != phase:
            continue
        if _normalize_reason(entry.get("reason")) != normalized_reason:
            continue
        if rel_path is not None and entry.get("relPath") != rel_path:
            continue
        matches.append(entry)
    return matches


def _enforce_fresh_call_policy(
    *,
    workspace: Path,
    runtime_config: RuntimeConfig,
    phase: str | None,
    rel_path: str | None,
    reason: str | None,
    prompt_pack: str | None,
    provider: str,
    model: str,
    iteration: int | None,
    attempt: int | None,
) -> None:
    normalized_reason = _normalize_reason(reason)
    if phase is None or normalized_reason is None:
        return
    policy = _phase_policy(runtime_config, phase)
    provider_calls = _matching_provider_call_entries(
        workspace,
        phase=phase,
        rel_path=rel_path,
        reason=normalized_reason,
    )
    if phase == "plan":
        cooldown = getattr(policy, "cooldown_iterations_per_reason", 0)
        if isinstance(iteration, int) and isinstance(cooldown, int) and cooldown > 0:
            prior_iterations = [
                int(entry["iteration"])
                for entry in provider_calls
                if isinstance(entry.get("iteration"), int)
            ]
            if prior_iterations:
                latest_iteration = max(prior_iterations)
                if iteration - latest_iteration <= cooldown:
                    append_helper_index_event(
                        workspace,
                        event="skipped_by_cooldown",
                        phase=phase,
                        rel_path=rel_path,
                        reason=normalized_reason,
                        prompt_pack=prompt_pack,
                        provider=provider,
                        model=model,
                        iteration=iteration,
                        attempt=attempt,
                        metadata={
                            "cooldownKind": "iterations",
                            "latestFreshIteration": latest_iteration,
                            "cooldownWindow": cooldown,
                        },
                    )
                    raise SystemExit(
                        f"Error: helper cooldown is active for {phase}:{normalized_reason}"
                        + (f" on {rel_path}" if rel_path else "")
                        + "."
                    )

    if phase == "prover":
        cooldown = getattr(policy, "cooldown_attempts_per_reason", 0)
        if isinstance(attempt, int) and isinstance(cooldown, int) and cooldown > 0:
            prior_attempts = [
                int(entry["attempt"])
                for entry in provider_calls
                if isinstance(entry.get("attempt"), int)
            ]
            if prior_attempts:
                latest_attempt = max(prior_attempts)
                if attempt - latest_attempt <= cooldown:
                    append_helper_index_event(
                        workspace,
                        event="skipped_by_cooldown",
                        phase=phase,
                        rel_path=rel_path,
                        reason=normalized_reason,
                        prompt_pack=prompt_pack,
                        provider=provider,
                        model=model,
                        iteration=iteration,
                        attempt=attempt,
                        metadata={
                            "cooldownKind": "attempts",
                            "latestFreshAttempt": latest_attempt,
                            "cooldownWindow": cooldown,
                        },
                    )
                    raise SystemExit(
                        f"Error: helper cooldown is active for {phase}:{normalized_reason}"
                        + (f" on {rel_path}" if rel_path else "")
                        + "."
                    )

    max_calls_per_reason = getattr(policy, "max_calls_per_reason", None)
    if isinstance(max_calls_per_reason, int) and max_calls_per_reason >= 0 and len(provider_calls) >= max_calls_per_reason:
        append_helper_index_event(
            workspace,
            event="skipped_by_budget",
            phase=phase,
            rel_path=rel_path,
            reason=normalized_reason,
            prompt_pack=prompt_pack,
            provider=provider,
            model=model,
            iteration=iteration,
            attempt=attempt,
            metadata={"budgetKind": "per_reason", "observedProviderCalls": len(provider_calls)},
        )
        raise SystemExit(
            f"Error: helper reason budget exhausted for {phase}:{normalized_reason}"
            + (f" on {rel_path}" if rel_path else "")
            + "."
        )


def _parse_note_metadata(path: Path) -> dict[str, str]:
    metadata: dict[str, str] = {}
    text = path.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines()[:16]:
        if not line.startswith("- "):
            continue
        body = line[2:]
        if ": `" not in body or not body.endswith("`"):
            continue
        raw_key, raw_value = body.split(": `", 1)
        key = raw_key.strip().lower().replace(" ", "")
        metadata[key] = raw_value[:-1]
    return metadata


def _extract_helper_output(text: str) -> str:
    marker = "\n## Helper Output\n"
    if marker not in text:
        return text.rstrip()
    return text.split(marker, 1)[1].strip()


def _find_reusable_auto_note(
    *,
    workspace: Path,
    runtime_config: RuntimeConfig,
    phase: str | None,
    rel_path: str | None,
    reason: str | None,
    prompt_pack: str | None,
    force_fresh_call: bool,
) -> tuple[Path, str] | None:
    normalized_reason = _normalize_reason(reason)
    if force_fresh_call or phase is None or rel_path is None or normalized_reason is None:
        return None
    policy = _phase_policy(runtime_config, phase)
    if getattr(policy, "reuse_recent_note_by_reason", True) is not True:
        return None
    note_dir = workspace / _note_dir_for_phase(runtime_config, phase)
    if not note_dir.exists():
        return None

    note_files = sorted(
        [path for path in note_dir.rglob("*.md") if path.is_file()],
        key=lambda path: path.stat().st_mtime if path.exists() else 0.0,
        reverse=True,
    )
    for path in note_files:
        metadata = _parse_note_metadata(path)
        if metadata.get("phase") != phase:
            continue
        if metadata.get("target") != rel_path:
            continue
        if _normalize_reason(metadata.get("reason")) != normalized_reason:
            continue
        if prompt_pack is not None:
            existing_pack = metadata.get("promptpack")
            if existing_pack not in {None, prompt_pack}:
                continue
        text = path.read_text(encoding="utf-8", errors="replace")
        return path, _extract_helper_output(text)
    return None


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
        phase=args.phase,
        reason=_normalize_reason(args.reason),
        requested_prompt_pack=args.prompt_pack,
        selected_prompt_pack=_selected_prompt_pack(
            phase=args.phase,
            reason=args.reason,
            requested=args.prompt_pack,
        ),
    )
    if args.print_effective_config:
        sys.stdout.write(json.dumps(effective, indent=2, sort_keys=True) + "\n")
        return 0

    if not args.prompt:
        raise SystemExit("Error: prompt is required unless --print-effective-config is used.")
    if args.write_note == "auto" and args.phase is None:
        raise SystemExit("Error: --phase is required when --write-note auto is used.")
    selected_prompt_pack = _selected_prompt_pack(
        phase=args.phase,
        reason=args.reason,
        requested=args.prompt_pack,
    )
    provider_prompt = (
        _render_prompt_with_pack(
            prompt=args.prompt,
            phase=args.phase,
            rel_path=args.rel_path,
            reason=args.reason,
            prompt_pack=selected_prompt_pack,
        )
        if selected_prompt_pack is not None
        else args.prompt
    )
    reusable_note = (
        _find_reusable_auto_note(
            workspace=workspace,
            runtime_config=runtime_config,
            phase=args.phase,
            rel_path=args.rel_path,
            reason=args.reason,
            prompt_pack=selected_prompt_pack,
            force_fresh_call=args.force_fresh_call,
        )
        if args.write_note == "auto"
        else None
    )
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
    event_note_path: Path | None = None
    if reusable_note is not None:
        note_path, response = reusable_note
        used_config = attempts[0]
        sys.stderr.write(f"[archon-helper] reused note {note_path}\n")
        append_helper_index_event(
            workspace,
            event="note_reuse",
            phase=args.phase,
            rel_path=args.rel_path,
            reason=args.reason,
            prompt_pack=selected_prompt_pack,
            provider=used_config.provider,
            model=used_config.model,
            note_path=note_path,
            reused_from=note_path,
            iteration=args.iteration,
            attempt=args.attempt,
        )
    else:
        _enforce_fresh_call_policy(
            workspace=workspace,
            runtime_config=runtime_config,
            phase=args.phase,
            rel_path=args.rel_path,
            reason=args.reason,
            prompt_pack=selected_prompt_pack,
            provider=attempts[0].provider,
            model=attempts[0].model,
            iteration=args.iteration,
            attempt=args.attempt,
        )
        for index, attempt in enumerate(attempts):
            try:
                response = _call_provider(
                    informal_agent,
                    prompt=provider_prompt,
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
        if args.write_note == "auto" and reusable_note is None:
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
                prompt_pack=selected_prompt_pack,
            )
        else:
            note_path = Path(args.write_note).resolve()
            note_text = response + ("\n" if not response.endswith("\n") else "")
        event_note_path = note_path
        if args.write_note != "auto" or reusable_note is None:
            note_path.parent.mkdir(parents=True, exist_ok=True)
            note_path.write_text(note_text, encoding="utf-8")
            if args.write_note == "auto":
                sys.stderr.write(f"[archon-helper] wrote note {note_path}\n")
    if reusable_note is None:
        append_helper_index_event(
            workspace,
            event="provider_call",
            phase=args.phase,
            rel_path=args.rel_path,
            reason=args.reason,
            prompt_pack=selected_prompt_pack,
            provider=used_config.provider,
            model=used_config.model,
            note_path=event_note_path,
            iteration=args.iteration,
            attempt=args.attempt,
        )
    sys.stdout.write(response)
    if response and not response.endswith("\n"):
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
