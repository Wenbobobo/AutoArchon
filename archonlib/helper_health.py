from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Mapping


ENV_ASSIGNMENT_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)\s*$")
OPENAI_STYLE_MODEL_RE = re.compile(r"^(gpt-|o[134]|codex|text-|omni)", re.IGNORECASE)
GEMINI_STYLE_MODEL_RE = re.compile(r"^gemini", re.IGNORECASE)
REDACTION_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"sk-[A-Za-z0-9*._-]+"), "sk-[redacted]"),
    (re.compile(r"AIza[0-9A-Za-z_-]+"), "AIza[redacted]"),
    (re.compile(r"(?i)(api key provided:\s*)([^\\s,]+)"), r"\1[redacted]"),
    (re.compile(r"(?i)(authorization:\s*bearer\s+)(\S+)"), r"\1[redacted]"),
)


def _strip_quotes(value: str) -> str:
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {'"', "'"}:
        return stripped[1:-1]
    return stripped


def load_helper_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = ENV_ASSIGNMENT_RE.match(raw_line)
        if not match:
            continue
        key, raw_value = match.groups()
        values[key] = _strip_quotes(raw_value)
    return values


def helper_enabled(env: Mapping[str, str]) -> bool:
    raw = env.get("ARCHON_HELPER_ENABLE")
    if raw is None:
        return False
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def helper_provider(env: Mapping[str, str]) -> str:
    value = env.get("ARCHON_HELPER_PROVIDER", "").strip()
    return value or "openai"


def helper_model(env: Mapping[str, str]) -> str | None:
    value = env.get("ARCHON_HELPER_MODEL", "").strip()
    return value or None


def helper_model_provider_mismatch(provider: str, model: str | None) -> bool:
    if not model:
        return False
    normalized = provider.strip().lower()
    if normalized == "openrouter":
        return False
    if normalized == "openai" and GEMINI_STYLE_MODEL_RE.match(model):
        return True
    if normalized == "gemini" and OPENAI_STYLE_MODEL_RE.match(model):
        return True
    return False


def sanitize_helper_probe_text(text: str, *, limit: int = 1200) -> str:
    sanitized = text
    for pattern, replacement in REDACTION_RULES:
        sanitized = pattern.sub(replacement, sanitized)
    sanitized = sanitized.strip()
    if len(sanitized) > limit:
        return sanitized[-limit:]
    return sanitized


def classify_helper_probe_failure(message: str) -> str:
    lowered = message.lower()
    if "invalid_api_key" in lowered or "incorrect api key" in lowered or "unauthorized" in lowered:
        return "invalid_credentials"
    if "timed out" in lowered or "timeout" in lowered:
        return "provider_timeout"
    if any(marker in lowered for marker in ("403", "429", "connection", "refused", "transport failed")):
        return "provider_transport"
    return "unknown"


ProbeRunner = Callable[..., subprocess.CompletedProcess[str]]


def probe_helper_transport(
    *,
    repo_root: Path,
    env_file: Path | None = None,
    timeout_seconds: int = 20,
    runner: ProbeRunner | None = None,
) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    helper_env = load_helper_env_file(env_file) if env_file is not None else {}
    merged_env = dict(os.environ)
    merged_env.update(helper_env)

    enabled = helper_enabled(merged_env)
    provider = helper_provider(merged_env)
    model = helper_model(merged_env)

    payload: dict[str, Any] = {
        "enabled": enabled,
        "provider": provider,
        "model": model,
        "envFile": str(env_file.resolve()) if env_file is not None else None,
    }
    if not enabled:
        payload.update({"status": "disabled", "classification": None, "message": "Helper is disabled."})
        return payload

    command = [
        sys.executable,
        str(repo_root / ".archon-src" / "tools" / "helper_prover_agent.py"),
        "--provider",
        provider,
        "--max-retries",
        "0",
        "--initial-backoff-seconds",
        "1",
        "--timeout-seconds",
        str(max(5, min(timeout_seconds, 60))),
    ]
    if model:
        command.extend(["--model", model])
    command.append("Reply with exactly OK.")

    completed: subprocess.CompletedProcess[str]
    try:
        completed = (runner or subprocess.run)(
            command,
            cwd=str(repo_root),
            env=merged_env,
            capture_output=True,
            text=True,
            check=False,
            timeout=max(5, timeout_seconds + 5),
        )
    except subprocess.TimeoutExpired:
        payload.update(
            {
                "status": "failed",
                "classification": "provider_timeout",
                "message": "Helper probe timed out.",
            }
        )
        return payload

    stdout = sanitize_helper_probe_text(completed.stdout or "")
    stderr = sanitize_helper_probe_text(completed.stderr or "")
    success = completed.returncode == 0 and stdout.strip() == "OK"
    payload.update(
        {
            "status": "ok" if success else "failed",
            "exitCode": completed.returncode,
            "stdout": stdout,
            "stderr": stderr,
        }
    )
    if success:
        payload.update({"classification": None, "message": "Helper probe succeeded."})
        return payload

    failure_text = stderr or stdout or f"helper probe exited with code {completed.returncode}"
    payload.update(
        {
            "classification": classify_helper_probe_failure(failure_text),
            "message": failure_text,
        }
    )
    return payload
