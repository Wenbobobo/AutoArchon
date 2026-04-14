from archonlib.codex_logs import (
    codex_command,
    flatten_result_content,
    normalize_codex_json_event,
    strip_unsupported_search_args,
    wrap_command_with_timeout,
)


def test_codex_command_uses_current_noninteractive_flags():
    command = codex_command(
        "gpt-5.4",
        extra_args="--search",
        enable_search=False,
        search_supported=True,
    )

    assert "--dangerously-bypass-approvals-and-sandbox" not in command
    assert command[:8] == [
        "codex",
        "exec",
        "--json",
        "--skip-git-repo-check",
        "--sandbox",
        "danger-full-access",
        "-c",
        "approval_policy=never",
    ]
    assert "--model" in command
    assert command[command.index("--model") + 1] == "gpt-5.4"
    assert "--search" in command


def test_codex_command_reads_prompt_from_stdin_without_prompt_marker():
    command = codex_command(
        "gpt-5.4",
        extra_args="--config model_reasoning_effort=xhigh",
        enable_search=False,
        search_supported=True,
    )

    assert command[:8] == [
        "codex",
        "exec",
        "--json",
        "--skip-git-repo-check",
        "--sandbox",
        "danger-full-access",
        "-c",
        "approval_policy=never",
    ]
    assert command[command.index("--model") + 1] == "gpt-5.4"
    assert command[-2:] == ["--config", "model_reasoning_effort=xhigh"]
    assert "-" not in command


def test_codex_command_strips_search_when_cli_lacks_support():
    command = codex_command(
        "gpt-5.4",
        extra_args="--search --color never",
        enable_search=True,
        search_supported=False,
    )

    assert "--search" not in command
    assert "--color" in command
    assert "never" in command


def test_codex_command_wraps_with_timeout_when_requested():
    command = codex_command(
        "gpt-5.4",
        timeout_seconds=900,
        search_supported=True,
        timeout_available=True,
    )

    assert command[:4] == ["timeout", "--signal=TERM", "--kill-after=30", "900"]
    assert command[4:12] == [
        "codex",
        "exec",
        "--json",
        "--skip-git-repo-check",
        "--sandbox",
        "danger-full-access",
        "-c",
        "approval_policy=never",
    ]


def test_strip_unsupported_search_args_reports_removal():
    args, removed = strip_unsupported_search_args(
        ["--search", "--color", "never"],
        search_supported=False,
    )

    assert args == ["--color", "never"]
    assert removed is True


def test_wrap_command_with_timeout_skips_when_timeout_unavailable():
    command, wrapped = wrap_command_with_timeout(
        ["codex", "exec", "-"],
        120,
        timeout_available=False,
    )

    assert command == ["codex", "exec", "-"]
    assert wrapped is False


def test_normalize_agent_and_command_events():
    started_at = 0.0
    events, thread_id, last_text = normalize_codex_json_event(
        {"type": "thread.started", "thread_id": "thread-1"},
        model="gpt-5.4",
        thread_id="",
        started_at=started_at,
        last_text="",
    )
    assert events == []
    assert thread_id == "thread-1"

    events, thread_id, last_text = normalize_codex_json_event(
        {
            "type": "item.started",
            "item": {
                "id": "item_1",
                "type": "command_execution",
                "command": "/bin/bash -lc 'pwd'",
            },
        },
        model="gpt-5.4",
        thread_id=thread_id,
        started_at=started_at,
        last_text=last_text,
    )
    assert events[0]["event"] == "tool_call"
    assert events[0]["input"]["command"] == "/bin/bash -lc 'pwd'"

    events, thread_id, last_text = normalize_codex_json_event(
        {
            "type": "item.completed",
            "item": {
                "id": "item_1",
                "type": "command_execution",
                "command": "/bin/bash -lc 'pwd'",
                "aggregated_output": "/tmp\n",
                "exit_code": 0,
                "status": "completed",
            },
        },
        model="gpt-5.4",
        thread_id=thread_id,
        started_at=started_at,
        last_text=last_text,
    )
    assert events[0]["event"] == "tool_result"
    assert events[0]["content"] == "/tmp\n"

    events, thread_id, last_text = normalize_codex_json_event(
        {"type": "item.completed", "item": {"id": "item_2", "type": "agent_message", "text": "DONE"}},
        model="gpt-5.4",
        thread_id=thread_id,
        started_at=started_at,
        last_text=last_text,
    )
    assert events[0]["event"] == "text"
    assert last_text == "DONE"


def test_normalize_turn_completed():
    events, _, _ = normalize_codex_json_event(
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 12,
                "cached_input_tokens": 5,
                "output_tokens": 7,
            },
        },
        model="gpt-5.4",
        thread_id="thread-1",
        started_at=0.0,
        last_text="DONE",
    )
    assert events[0]["event"] == "session_end"
    assert events[0]["session_id"] == "thread-1"
    assert events[0]["input_tokens"] == 12
    assert events[0]["cache_read_input_tokens"] == 5
    assert events[0]["summary"] == "DONE"


def test_normalize_mcp_tool_call_events():
    started_at = 0.0
    events, thread_id, last_text = normalize_codex_json_event(
        {
            "type": "item.started",
            "item": {
                "id": "item_1",
                "type": "mcp_tool_call",
                "server": "archon-lean-lsp",
                "tool": "lean_local_search",
                "arguments": {"query": "Nat.add_comm", "limit": 5},
            },
        },
        model="gpt-5.4",
        thread_id="thread-1",
        started_at=started_at,
        last_text="",
    )
    assert thread_id == "thread-1"
    assert last_text == ""
    assert events[0]["event"] == "tool_call"
    assert events[0]["tool"] == "mcp__archon-lean-lsp__lean_local_search"
    assert events[0]["input"] == {"query": "Nat.add_comm", "limit": 5}

    events, _, _ = normalize_codex_json_event(
        {
            "type": "item.completed",
            "item": {
                "id": "item_1",
                "type": "mcp_tool_call",
                "server": "archon-lean-lsp",
                "tool": "lean_local_search",
                "arguments": {"query": "Nat.add_comm", "limit": 5},
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": "Error executing tool lean_local_search: Lean project path not set.",
                        }
                    ]
                },
                "error": None,
                "status": "failed",
            },
        },
        model="gpt-5.4",
        thread_id="thread-1",
        started_at=started_at,
        last_text="",
    )
    assert events[0]["event"] == "tool_result"
    assert events[0]["content"] == "Error executing tool lean_local_search: Lean project path not set."


def test_flatten_result_content_handles_text_content_lists():
    payload = {
        "content": [
            {"type": "text", "text": "first"},
            {"type": "text", "text": "second"},
        ]
    }

    assert flatten_result_content(payload) == "first\nsecond"
