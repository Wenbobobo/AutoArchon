from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import IO, Iterable, Iterator

TIMEOUT_EXIT_CODE = 124


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_jsonl(lines: Iterable[str]) -> Iterator[dict]:
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def build_model_usage(model: str, usage: dict) -> dict:
    return {
        model: {
            "inputTokens": usage.get("input_tokens", 0),
            "outputTokens": usage.get("output_tokens", 0),
            "cachedInputTokens": usage.get("cached_input_tokens", 0),
            "costUSD": 0,
        }
    }


def flatten_result_content(payload: object) -> str:
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        content = payload.get("content")
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text = item.get("text", "")
                    if text:
                        parts.append(text)
            if parts:
                return "\n".join(parts)
        text = payload.get("text")
        if isinstance(text, str):
            return text
    return json.dumps(payload, ensure_ascii=False)


@lru_cache(maxsize=1)
def codex_supports_search() -> bool:
    try:
        result = subprocess.run(
            ["codex", "exec", "--help"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return False
    help_text = f"{result.stdout}\n{result.stderr}"
    return bool(re.search(r"(^|\s)--search(\s|$)", help_text))


def strip_unsupported_search_args(args: list[str], *, search_supported: bool) -> tuple[list[str], bool]:
    if search_supported:
        return args, False
    filtered = [arg for arg in args if arg != "--search"]
    return filtered, filtered != args


def wrap_command_with_timeout(
    command: list[str],
    timeout_seconds: int | None,
    *,
    timeout_available: bool | None = None,
) -> tuple[list[str], bool]:
    if timeout_seconds is None or timeout_seconds <= 0:
        return command, False
    if timeout_available is None:
        timeout_available = shutil.which("timeout") is not None
    if not timeout_available:
        return command, False
    return [
        "timeout",
        "--signal=TERM",
        "--kill-after=30",
        str(timeout_seconds),
        *command,
    ], True


def normalize_codex_json_event(
    obj: dict,
    *,
    model: str,
    thread_id: str,
    started_at: float,
    last_text: str,
) -> tuple[list[dict], str, str]:
    ts = utc_now()
    events: list[dict] = []
    event_type = obj.get("type", "")

    if event_type == "thread.started":
        thread_id = obj.get("thread_id", thread_id)
        return events, thread_id, last_text

    if event_type == "item.started":
        item = obj.get("item", {})
        if item.get("type") == "command_execution":
            events.append(
                {
                    "ts": ts,
                    "event": "tool_call",
                    "tool": "Bash",
                    "input": {"command": item.get("command", "")},
                }
            )
        elif item.get("type") == "mcp_tool_call":
            server = item.get("server", "")
            tool = item.get("tool", "")
            events.append(
                {
                    "ts": ts,
                    "event": "tool_call",
                    "tool": f"mcp__{server}__{tool}",
                    "input": item.get("arguments", {}) or {},
                }
            )
        return events, thread_id, last_text

    if event_type == "item.completed":
        item = obj.get("item", {})
        item_type = item.get("type")
        if item_type == "agent_message":
            text = item.get("text", "").strip()
            if text:
                last_text = text
                events.append({"ts": ts, "event": "text", "content": text})
        elif item_type == "command_execution":
            output = item.get("aggregated_output", "")
            exit_code = item.get("exit_code")
            if exit_code is not None and not output.strip():
                output = f"exit_code={exit_code}"
            elif exit_code not in (None, 0):
                output = f"{output.rstrip()}\nexit_code={exit_code}".strip()
            events.append({"ts": ts, "event": "tool_result", "content": output})
        elif item_type == "mcp_tool_call":
            content = flatten_result_content(item.get("result"))
            error = item.get("error")
            if error:
                error_text = flatten_result_content(error)
                content = f"{content}\n{error_text}".strip() if content else error_text
            events.append({"ts": ts, "event": "tool_result", "content": content})
        return events, thread_id, last_text

    if event_type == "turn.completed":
        usage = obj.get("usage", {}) or {}
        duration_ms = int((time.monotonic() - started_at) * 1000)
        events.append(
            {
                "ts": ts,
                "event": "session_end",
                "session_id": thread_id,
                "total_cost_usd": 0,
                "duration_ms": duration_ms,
                "duration_api_ms": 0,
                "num_turns": 1,
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                "cache_read_input_tokens": usage.get("cached_input_tokens", 0),
                "cache_creation_input_tokens": 0,
                "model_usage": build_model_usage(model, usage),
                "summary": last_text,
            }
        )
        return events, thread_id, last_text

    return events, thread_id, last_text


def codex_command(
    model: str,
    extra_args: str | None = None,
    enable_search: bool = False,
    *,
    search_supported: bool | None = None,
    timeout_seconds: int | None = None,
    timeout_available: bool | None = None,
) -> list[str]:
    if search_supported is None:
        search_supported = codex_supports_search()
    cmd = [
        "codex",
        "exec",
        "--json",
        "--skip-git-repo-check",
        "--sandbox",
        "danger-full-access",
        "-c",
        "approval_policy=never",
        "--model",
        model,
    ]
    if enable_search and search_supported:
        cmd.append("--search")
    if extra_args:
        parsed_extra_args = shlex.split(extra_args)
        parsed_extra_args, _ = strip_unsupported_search_args(
            parsed_extra_args,
            search_supported=search_supported,
        )
        cmd.extend(parsed_extra_args)
    cmd.append("-")
    wrapped_cmd, _ = wrap_command_with_timeout(
        cmd,
        timeout_seconds,
        timeout_available=timeout_available,
    )
    return wrapped_cmd


@dataclass
class CodexRunConfig:
    prompt: str
    cwd: Path
    model: str
    log_path: Path | None = None
    raw_log_path: Path | None = None
    extra_args: str | None = None
    enable_search: bool = False
    timeout_seconds: int | None = None


def run_codex(config: CodexRunConfig) -> int:
    search_supported = codex_supports_search()
    requested_search = config.enable_search or (
        config.extra_args is not None and "--search" in shlex.split(config.extra_args)
    )
    if requested_search and not search_supported:
        print(
            "[WARN] codex-cli does not support --search; continuing without web search.",
            file=sys.stderr,
            flush=True,
        )
    timeout_available = shutil.which("timeout") is not None
    if config.timeout_seconds and not timeout_available:
        print(
            "[WARN] coreutils timeout is not installed; continuing without a Codex timeout guard.",
            file=sys.stderr,
            flush=True,
        )
    command = codex_command(
        config.model,
        extra_args=config.extra_args,
        enable_search=config.enable_search,
        search_supported=search_supported,
        timeout_seconds=config.timeout_seconds,
        timeout_available=timeout_available,
    )
    env = os.environ.copy()
    process = subprocess.Popen(
        command,
        cwd=str(config.cwd),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    process.stdin.write(config.prompt)
    process.stdin.close()

    log_handle: IO[str] | None = None
    raw_handle: IO[str] | None = None
    if config.log_path is not None:
        config.log_path.parent.mkdir(parents=True, exist_ok=True)
        log_handle = config.log_path.open("a", encoding="utf-8")
    if config.raw_log_path is not None:
        config.raw_log_path.parent.mkdir(parents=True, exist_ok=True)
        raw_handle = config.raw_log_path.open("a", encoding="utf-8")

    thread_id = ""
    last_text = ""
    started_at = time.monotonic()

    exit_code = 0
    try:
        for raw_line in process.stdout:
            if raw_handle is not None:
                raw_handle.write(raw_line)
                raw_handle.flush()
            line = raw_line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                if log_handle is not None:
                    log_handle.write(
                        json.dumps(
                            {
                                "ts": utc_now(),
                                "event": "runner_log",
                                "content": line,
                            }
                        )
                        + "\n"
                    )
                    log_handle.flush()
                print(line, file=sys.stderr, flush=True)
                continue
            events, thread_id, last_text = normalize_codex_json_event(
                obj,
                model=config.model,
                thread_id=thread_id,
                started_at=started_at,
                last_text=last_text,
            )
            for event in events:
                if log_handle is not None:
                    log_handle.write(json.dumps(event) + "\n")
                    log_handle.flush()
                if event["event"] == "text":
                    print(event["content"], flush=True)
                elif event["event"] == "session_end":
                    usage_bits = []
                    if event["duration_ms"]:
                        usage_bits.append(f"{event['duration_ms'] / 60000:.1f}min")
                    if event["input_tokens"] or event["output_tokens"]:
                        usage_bits.append(
                            f"in={event['input_tokens']} out={event['output_tokens']}"
                        )
                    if usage_bits:
                        print(f"[USAGE] {' | '.join(usage_bits)}", flush=True)
        exit_code = process.wait()
        if config.timeout_seconds and timeout_available and exit_code == TIMEOUT_EXIT_CODE:
            timeout_event = {
                "ts": utc_now(),
                "event": "runner_timeout",
                "timeout_seconds": config.timeout_seconds,
                "content": f"codex exec timed out after {config.timeout_seconds}s",
            }
            if log_handle is not None:
                log_handle.write(json.dumps(timeout_event) + "\n")
                log_handle.flush()
            print(timeout_event["content"], file=sys.stderr, flush=True)
    finally:
        if log_handle is not None:
            log_handle.close()
        if raw_handle is not None:
            raw_handle.close()

    return exit_code


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run Codex and normalize its JSON log stream.")
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--prompt-file")
    parser.add_argument("--log-path")
    parser.add_argument("--raw-log-path")
    parser.add_argument("--extra-args")
    parser.add_argument("--search", action="store_true")
    parser.add_argument("--timeout-seconds", type=int)
    args = parser.parse_args(argv)

    if args.prompt_file:
        prompt = Path(args.prompt_file).read_text(encoding="utf-8")
    else:
        prompt = sys.stdin.read()
    return run_codex(
        CodexRunConfig(
            prompt=prompt,
            cwd=Path(args.cwd),
            model=args.model,
            log_path=Path(args.log_path) if args.log_path else None,
            raw_log_path=Path(args.raw_log_path) if args.raw_log_path else None,
            extra_args=args.extra_args,
            enable_search=args.search,
            timeout_seconds=args.timeout_seconds,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
