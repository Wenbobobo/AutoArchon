#!/usr/bin/env python3
"""Informal mathematical reasoning via external LLMs (OpenAI / Gemini / OpenRouter).

No dependencies beyond Python 3.10+ stdlib.

Environment variables:
    OPENAI_API_KEY      Required for --provider openai
    GEMINI_API_KEY      Required for --provider gemini
    OPENROUTER_API_KEY  Required for --provider openrouter

Usage:
    python3 archon-informal-agent.py --provider openai "Prove that ..."
    python3 archon-informal-agent.py --provider gemini --think "Prove that ..."
    python3 archon-informal-agent.py --provider openrouter "Prove that ..."
    python3 archon-informal-agent.py --provider openrouter --model deepseek/deepseek-r1 "..."

OpenRouter (https://openrouter.ai) provides access to 200+ models through a single
API key. Set OPENROUTER_API_KEY and use any model ID from their catalog, e.g.:
    --provider openrouter --model google/gemini-3.1-pro-preview   (default)
    --provider openrouter --model deepseek/deepseek-r1
    --provider openrouter --model openai/gpt-5
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULTS = {
    "openai": "gpt-5.4",
    "gemini": "gemini-3.1-pro-preview",
    "openrouter": "google/gemini-3.1-pro-preview",
}

API_KEY_ENVS = {
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}

BASE_URL_ENVS = {
    "openai": "OPENAI_BASE_URL",
    "gemini": "GEMINI_BASE_URL",
    "openrouter": "OPENROUTER_BASE_URL",
}

BASE_URL_DEFAULTS = {
    "openai": "https://api.openai.com/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta",
    "openrouter": "https://openrouter.ai/api/v1",
}

SYSTEM_PROMPT = (
    "You are an expert mathematician. Given a mathematical statement or problem, "
    "provide a clear, detailed informal proof or solution. "
    "Focus on mathematical reasoning and intuition. "
    "Structure your response with clear logical steps."
)

TIMEOUT = 300
MAX_RETRIES = 5
INITIAL_BACKOFF_SECONDS = 5


def _require_key(name: str) -> str:
    val = os.environ.get(name, "")
    if not val:
        auth_path = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "auth.json"
        try:
            auth = json.loads(auth_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            auth = {}
        auth_val = auth.get(name, "")
        if isinstance(auth_val, str):
            val = auth_val
    if not val:
        sys.exit(f"Error: {name} not set")
    return val


def _base_url(provider: str) -> str:
    env_name = BASE_URL_ENVS[provider]
    default = BASE_URL_DEFAULTS[provider]
    override = os.environ.get(env_name, "").strip()
    if not override:
        return default.rstrip("/")
    parsed = urllib.parse.urlsplit(override)
    default_path = urllib.parse.urlsplit(default).path.rstrip("/")
    if parsed.scheme and parsed.netloc and parsed.path in {"", "/"} and default_path:
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, default_path, parsed.query, parsed.fragment)).rstrip("/")
    return override.rstrip("/")


def _is_retryable_http_error(code: int) -> bool:
    return code == 429 or 500 <= code < 600


def _post(
    url: str,
    headers: dict,
    body: dict,
    *,
    timeout_seconds: int,
    max_retries: int,
    initial_backoff_seconds: int,
) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", **headers},
    )
    last_error = ""
    for attempt in range(max_retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            detail = e.read().decode() if e.fp else ""
            last_error = f"API error {e.code}: {detail}"
            if attempt >= max_retries or not _is_retryable_http_error(e.code):
                sys.exit(last_error)
        except (urllib.error.URLError, TimeoutError) as e:
            last_error = f"Transport error: {e}"
            if attempt >= max_retries:
                sys.exit(last_error)
        sleep_seconds = initial_backoff_seconds * (2**attempt)
        time.sleep(sleep_seconds)
    sys.exit(last_error or "Request failed")


def call_gemini(
    prompt: str,
    model: str,
    think: bool,
    *,
    max_retries: int,
    initial_backoff_seconds: int,
    timeout_seconds: int,
) -> str:
    key = _require_key(API_KEY_ENVS["gemini"])
    url = f"{_base_url('gemini')}/models/{model}:generateContent"
    gen_config: dict = {}
    if think:
        gen_config["thinkingConfig"] = {"thinkingLevel": "high", "includeThoughts": True}
    else:
        gen_config["temperature"] = 0.3

    data = _post(
        url,
        {"x-goog-api-key": key},
        {
            "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": gen_config,
        },
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        initial_backoff_seconds=initial_backoff_seconds,
    )

    parts = data["candidates"][0]["content"]["parts"]
    out = []
    for p in parts:
        if p.get("thought"):
            out.append(f"[Thinking]\n{p['text']}\n[/Thinking]")
        else:
            out.append(p["text"])
    return "\n\n".join(out)


def call_openai(
    prompt: str,
    model: str,
    think: bool,
    *,
    max_retries: int,
    initial_backoff_seconds: int,
    timeout_seconds: int,
) -> str:
    key = _require_key(API_KEY_ENVS["openai"])
    auth = {"Authorization": f"Bearer {key}"}
    base = _base_url("openai")

    if model.startswith("o") and "api.openai.com" in base:
        return _openai_responses(
            prompt,
            model,
            auth,
            base,
            think,
            max_retries=max_retries,
            initial_backoff_seconds=initial_backoff_seconds,
            timeout_seconds=timeout_seconds,
        )
    return _openai_chat(
        prompt,
        model,
        auth,
        base,
        max_retries=max_retries,
        initial_backoff_seconds=initial_backoff_seconds,
        timeout_seconds=timeout_seconds,
    )


def _openai_responses(
    prompt: str,
    model: str,
    auth: dict,
    base: str,
    think: bool,
    *,
    max_retries: int,
    initial_backoff_seconds: int,
    timeout_seconds: int,
) -> str:
    data = _post(
        f"{base}/responses",
        auth,
        {
            "model": model,
            "input": [
                {"role": "developer", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "reasoning": {"effort": "high" if think else "medium"},
        },
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        initial_backoff_seconds=initial_backoff_seconds,
    )
    out = []
    for item in data.get("output", []):
        if item.get("type") == "reasoning":
            for s in item.get("summary", []):
                out.append(f"[Thinking]\n{s.get('text', '')}\n[/Thinking]")
        elif item.get("type") == "message":
            for c in item.get("content", []):
                if c.get("type") == "output_text":
                    out.append(c["text"])
    return "\n\n".join(out) if out else json.dumps(data, indent=2)


def _openai_chat(
    prompt: str,
    model: str,
    auth: dict,
    base: str,
    *,
    max_retries: int,
    initial_backoff_seconds: int,
    timeout_seconds: int,
) -> str:
    data = _post(
        f"{base}/chat/completions",
        auth,
        {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        },
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        initial_backoff_seconds=initial_backoff_seconds,
    )
    return data["choices"][0]["message"]["content"]


def call_openrouter(
    prompt: str,
    model: str,
    think: bool,
    *,
    max_retries: int,
    initial_backoff_seconds: int,
    timeout_seconds: int,
) -> str:
    key = _require_key(API_KEY_ENVS["openrouter"])
    auth = {"Authorization": f"Bearer {key}"}
    data = _post(
        f"{_base_url('openrouter')}/chat/completions",
        auth,
        {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        },
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        initial_backoff_seconds=initial_backoff_seconds,
    )
    return data["choices"][0]["message"]["content"]


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("prompt")
    p.add_argument("--provider", choices=["openai", "gemini", "openrouter"], required=True)
    p.add_argument("--model", default=None)
    p.add_argument("--think", action="store_true")
    p.add_argument("--max-retries", type=int, default=int(os.environ.get("ARCHON_HELPER_MAX_RETRIES", MAX_RETRIES)))
    p.add_argument(
        "--initial-backoff-seconds",
        type=int,
        default=int(os.environ.get("ARCHON_HELPER_INITIAL_BACKOFF_SECONDS", INITIAL_BACKOFF_SECONDS)),
    )
    p.add_argument("--timeout-seconds", type=int, default=int(os.environ.get("ARCHON_HELPER_TIMEOUT_SECONDS", TIMEOUT)))
    args = p.parse_args()

    model = args.model or DEFAULTS[args.provider]
    fn = {"gemini": call_gemini, "openai": call_openai, "openrouter": call_openrouter}[args.provider]
    print(
        fn(
            args.prompt,
            model,
            args.think,
            max_retries=args.max_retries,
            initial_backoff_seconds=args.initial_backoff_seconds,
            timeout_seconds=args.timeout_seconds,
        )
    )


if __name__ == "__main__":
    main()
