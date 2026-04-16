from __future__ import annotations

import importlib.util
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load_helper_tool():
    module_path = ROOT / ".archon-src" / "tools" / "helper_prover_agent.py"
    spec = importlib.util.spec_from_file_location("archon_helper_tool", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_helper_tool_prints_effective_config_from_workspace_file(monkeypatch, tmp_path: Path, capsys):
    archon_dir = tmp_path / ".archon"
    archon_dir.mkdir()
    (archon_dir / "runtime-config.toml").write_text(
        """
[helper]
enabled = true
provider = "openai"
model = "deepseek-reasoner"
api_key_env = "DEEPSEEK_API_KEY"
base_url_env = "DEEPSEEK_BASE_URL"
max_retries = 7
initial_backoff_seconds = 11
timeout_seconds = 222

[[helper.fallbacks]]
provider = "gemini"
model = "gemini-3.1-pro-preview"

[helper.plan]
enabled = true
max_calls_per_iteration = 3
notes_dir = ".archon/informal/plan"

[helper.prover]
enabled = true
max_calls_per_session = 4
notes_dir = ".archon/informal/prover"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    module = load_helper_tool()
    monkeypatch.setattr(module.sys, "argv", ["archon-helper", "--print-effective-config"])

    assert module.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["configEnabled"] is True
    assert payload["provider"] == "openai"
    assert payload["model"] == "deepseek-reasoner"
    assert payload["apiKeyEnv"] == "DEEPSEEK_API_KEY"
    assert payload["baseUrlEnv"] == "DEEPSEEK_BASE_URL"
    assert payload["maxRetries"] == 7
    assert payload["initialBackoffSeconds"] == 11
    assert payload["timeoutSeconds"] == 222
    assert payload["fallbacks"] == [
        {
            "apiKeyEnv": "GEMINI_API_KEY",
            "baseUrlEnv": "GEMINI_BASE_URL",
            "initialBackoffSeconds": 5,
            "maxRetries": 5,
            "model": "gemini-3.1-pro-preview",
            "provider": "gemini",
            "timeoutSeconds": 300,
        }
    ]
    assert payload["legacyHelperJsonUsed"] is False
    assert payload["planPolicy"] == {
        "cooldownIterationsPerReason": 0,
        "enabled": True,
        "maxCallsPerIteration": 3,
        "maxCallsPerReason": 1,
        "notesDir": ".archon/informal/plan",
        "reuseRecentNoteByReason": True,
        "triggerOnExternalReference": True,
        "triggerOnMissingInfrastructure": True,
        "triggerOnRepeatedFailure": True,
    }
    assert payload["proverPolicy"] == {
        "cooldownAttemptsPerReason": 0,
        "enabled": True,
        "maxCallsPerSession": 4,
        "maxCallsPerReason": 2,
        "notesDir": ".archon/informal/prover",
        "reuseRecentNoteByReason": True,
        "triggerOnFirstStuckAttempt": True,
        "triggerOnLspTimeout": True,
        "triggerOnMissingInfrastructure": True,
    }


def test_helper_tool_requires_provider_without_enabled_config(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    module = load_helper_tool()
    monkeypatch.setattr(module.sys, "argv", ["archon-helper", "--print-effective-config"])

    with pytest.raises(SystemExit, match="helper provider is required"):
        module.main()


def test_helper_tool_writes_note_and_uses_configured_transport(monkeypatch, tmp_path: Path, capsys):
    config_path = tmp_path / "runtime-config.toml"
    config_path.write_text(
        """
[helper]
enabled = true
provider = "openai"
model = "gpt-5.4-mini"
api_key_env = "ALT_OPENAI_KEY"
base_url_env = "ALT_OPENAI_BASE_URL"
max_retries = 3
initial_backoff_seconds = 4
timeout_seconds = 99
""".strip()
        + "\n",
        encoding="utf-8",
    )

    captured: dict[str, object] = {}

    def fake_call(prompt: str, model: str, think: bool, *, max_retries: int, initial_backoff_seconds: int, timeout_seconds: int) -> str:
        captured["prompt"] = prompt
        captured["model"] = model
        captured["think"] = think
        captured["max_retries"] = max_retries
        captured["initial_backoff_seconds"] = initial_backoff_seconds
        captured["timeout_seconds"] = timeout_seconds
        return "helper result"

    fake_agent = SimpleNamespace(
        API_KEY_ENVS={"openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY", "openrouter": "OPENROUTER_API_KEY"},
        BASE_URL_ENVS={"openai": "OPENAI_BASE_URL", "gemini": "GEMINI_BASE_URL", "openrouter": "OPENROUTER_BASE_URL"},
        DEFAULTS={"openai": "gpt-5.4", "gemini": "gemini-3.1-pro-preview", "openrouter": "google/gemini-3.1-pro-preview"},
        MAX_RETRIES=5,
        INITIAL_BACKOFF_SECONDS=5,
        TIMEOUT=300,
        call_openai=fake_call,
        call_gemini=fake_call,
        call_openrouter=fake_call,
    )

    note_path = tmp_path / "note.md"
    module = load_helper_tool()
    monkeypatch.setattr(module, "_load_informal_agent", lambda: fake_agent)
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            "archon-helper",
            "--config",
            str(config_path),
            "--write-note",
            str(note_path),
            "route around missing infrastructure",
        ],
    )

    assert module.main() == 0
    assert capsys.readouterr().out == "helper result\n"
    assert note_path.read_text(encoding="utf-8") == "helper result\n"
    assert captured == {
        "prompt": "route around missing infrastructure",
        "model": "gpt-5.4-mini",
        "think": False,
        "max_retries": 3,
        "initial_backoff_seconds": 4,
        "timeout_seconds": 99,
    }
    assert fake_agent.API_KEY_ENVS["openai"] == "OPENAI_API_KEY"
    assert fake_agent.BASE_URL_ENVS["openai"] == "OPENAI_BASE_URL"


def test_helper_tool_accepts_inline_transport_values_in_runtime_config(monkeypatch, tmp_path: Path, capsys):
    config_path = tmp_path / "runtime-config.toml"
    config_path.write_text(
        """
[helper]
enabled = true
provider = "openai"
model = "gpt-5.4-mini"
api_key_env = "sk-inline-key"
base_url_env = "https://example.invalid/v1"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    captured: dict[str, object] = {}

    def fake_call(prompt: str, model: str, think: bool, *, max_retries: int, initial_backoff_seconds: int, timeout_seconds: int) -> str:
        api_env_name = fake_agent.API_KEY_ENVS["openai"]
        base_env_name = fake_agent.BASE_URL_ENVS["openai"]
        captured["api_env_name"] = api_env_name
        captured["base_env_name"] = base_env_name
        captured["api_env_value"] = os.environ.get(api_env_name)
        captured["base_env_value"] = os.environ.get(base_env_name)
        return "helper result"

    fake_agent = SimpleNamespace(
        API_KEY_ENVS={"openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY", "openrouter": "OPENROUTER_API_KEY"},
        BASE_URL_ENVS={"openai": "OPENAI_BASE_URL", "gemini": "GEMINI_BASE_URL", "openrouter": "OPENROUTER_BASE_URL"},
        DEFAULTS={"openai": "gpt-5.4", "gemini": "gemini-3.1-pro-preview", "openrouter": "google/gemini-3.1-pro-preview"},
        MAX_RETRIES=5,
        INITIAL_BACKOFF_SECONDS=5,
        TIMEOUT=300,
        call_openai=fake_call,
        call_gemini=fake_call,
        call_openrouter=fake_call,
    )

    module = load_helper_tool()
    monkeypatch.setattr(module, "_load_informal_agent", lambda: fake_agent)
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            "archon-helper",
            "--config",
            str(config_path),
            "route around inline helper transport config",
        ],
    )

    assert module.main() == 0
    assert capsys.readouterr().out == "helper result\n"
    assert captured == {
        "api_env_name": "ARCHON_HELPER_INLINE_OPENAI_API_KEY",
        "base_env_name": "ARCHON_HELPER_INLINE_OPENAI_BASE_URL",
        "api_env_value": "sk-inline-key",
        "base_env_value": "https://example.invalid/v1",
    }
    assert fake_agent.API_KEY_ENVS["openai"] == "OPENAI_API_KEY"
    assert fake_agent.BASE_URL_ENVS["openai"] == "OPENAI_BASE_URL"
    assert os.environ.get("ARCHON_HELPER_INLINE_OPENAI_API_KEY") is None
    assert os.environ.get("ARCHON_HELPER_INLINE_OPENAI_BASE_URL") is None


def test_helper_tool_auto_routes_note_with_metadata(monkeypatch, tmp_path: Path, capsys):
    config_path = tmp_path / "runtime-config.toml"
    config_path.write_text(
        """
[helper]
enabled = true
provider = "openai"
model = "gpt-5.4-mini"
api_key_env = "ALT_OPENAI_KEY"
base_url_env = "ALT_OPENAI_BASE_URL"

[helper.plan]
notes_dir = ".archon/helper/plan"

[helper.prover]
notes_dir = ".archon/helper/prover"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            stamp = cls(2026, 4, 15, 3, 4, 5, tzinfo=timezone.utc)
            if tz is None:
                return stamp.replace(tzinfo=None)
            return stamp.astimezone(tz)

    def fake_call(prompt: str, model: str, think: bool, *, max_retries: int, initial_backoff_seconds: int, timeout_seconds: int) -> str:
        return "helper result"

    fake_agent = SimpleNamespace(
        API_KEY_ENVS={"openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY", "openrouter": "OPENROUTER_API_KEY"},
        BASE_URL_ENVS={"openai": "OPENAI_BASE_URL", "gemini": "GEMINI_BASE_URL", "openrouter": "OPENROUTER_BASE_URL"},
        DEFAULTS={"openai": "gpt-5.4", "gemini": "gemini-3.1-pro-preview", "openrouter": "google/gemini-3.1-pro-preview"},
        MAX_RETRIES=5,
        INITIAL_BACKOFF_SECONDS=5,
        TIMEOUT=300,
        call_openai=fake_call,
        call_gemini=fake_call,
        call_openrouter=fake_call,
    )

    monkeypatch.chdir(tmp_path)
    module = load_helper_tool()
    monkeypatch.setattr(module, "datetime", FrozenDateTime)
    monkeypatch.setattr(module, "_load_informal_agent", lambda: fake_agent)
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            "archon-helper",
            "--config",
            str(config_path),
            "--phase",
            "prover",
            "--rel-path",
            "FATEM/42.lean",
            "--reason",
            "lsp_timeout",
            "--write-note",
            "auto",
            "route around timeout",
        ],
    )

    assert module.main() == 0
    captured = capsys.readouterr()
    note_path = tmp_path / ".archon" / "helper" / "prover" / "FATEM_42.lean__prover__lsp_timeout__20260415T030405Z.md"
    assert captured.out == "helper result\n"
    assert str(note_path) in captured.err
    assert note_path.read_text(encoding="utf-8") == "\n".join(
        [
            "# Helper Note",
            "",
            "- Generated at: `2026-04-15T03:04:05+00:00`",
            "- Phase: `prover`",
            "- Provider: `openai`",
            "- Model: `gpt-5.4-mini`",
            f"- Config path: `{config_path}`",
            "- Target: `FATEM/42.lean`",
            "- Reason: `lsp_timeout`",
            "",
            "## Helper Output",
            "",
            "helper result",
            "",
        ]
    )
    index_path = tmp_path / ".archon" / "informal" / "helper" / "helper-index.json"
    assert index_path.exists()
    index_payload = json.loads(index_path.read_text(encoding="utf-8"))
    assert index_payload["entries"][0]["event"] == "provider_call"
    assert index_payload["entries"][0]["phase"] == "prover"
    assert index_payload["entries"][0]["reason"] == "lsp_timeout"
    assert index_payload["entries"][0]["notePath"] == ".archon/helper/prover/FATEM_42.lean__prover__lsp_timeout__20260415T030405Z.md"


def test_helper_tool_auto_prompt_pack_wraps_request_and_exposes_selection(monkeypatch, tmp_path: Path, capsys):
    config_path = tmp_path / "runtime-config.toml"
    config_path.write_text(
        """
[helper]
enabled = true
provider = "openai"
model = "gpt-5.4-mini"
api_key_env = "ALT_OPENAI_KEY"
base_url_env = "ALT_OPENAI_BASE_URL"

[helper.plan]
notes_dir = ".archon/helper/plan"

[helper.prover]
notes_dir = ".archon/helper/prover"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    captured: dict[str, object] = {}

    def fake_call(prompt: str, model: str, think: bool, *, max_retries: int, initial_backoff_seconds: int, timeout_seconds: int) -> str:
        captured["prompt"] = prompt
        return "helper result"

    fake_agent = SimpleNamespace(
        API_KEY_ENVS={"openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY", "openrouter": "OPENROUTER_API_KEY"},
        BASE_URL_ENVS={"openai": "OPENAI_BASE_URL", "gemini": "GEMINI_BASE_URL", "openrouter": "OPENROUTER_BASE_URL"},
        DEFAULTS={"openai": "gpt-5.4", "gemini": "gemini-3.1-pro-preview", "openrouter": "google/gemini-3.1-pro-preview"},
        MAX_RETRIES=5,
        INITIAL_BACKOFF_SECONDS=5,
        TIMEOUT=300,
        call_openai=fake_call,
        call_gemini=fake_call,
        call_openrouter=fake_call,
    )

    monkeypatch.chdir(tmp_path)
    module = load_helper_tool()
    monkeypatch.setattr(module, "_load_informal_agent", lambda: fake_agent)
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            "archon-helper",
            "--config",
            str(config_path),
            "--print-effective-config",
            "--phase",
            "prover",
            "--reason",
            "lsp_timeout",
            "--prompt-pack",
            "auto",
        ],
    )

    assert module.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["promptPack"] == {
        "available": [
            "external_reference",
            "first_stuck_attempt",
            "generic",
            "lsp_timeout",
            "missing_infrastructure",
            "repeated_failure",
        ],
        "requested": "auto",
        "selected": "lsp_timeout",
    }

    module = load_helper_tool()
    monkeypatch.setattr(module, "_load_informal_agent", lambda: fake_agent)
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            "archon-helper",
            "--config",
            str(config_path),
            "--phase",
            "prover",
            "--rel-path",
            "FATEM/42.lean",
            "--reason",
            "lsp_timeout",
            "--prompt-pack",
            "auto",
            "Need a route that survives missing Lean LSP.",
        ],
    )

    assert module.main() == 0
    assert captured["prompt"] == "\n".join(
        [
            "You are a bounded AutoArchon helper supporting the `prover` phase.",
            "Task class: `lsp_timeout`.",
            "Target file: `FATEM/42.lean`.",
            "",
            "Constraints:",
            "- Assume Lean LSP is unavailable or timing out; prefer routes that can be executed from local file context and bounded shell verification.",
            "- Keep the original benchmark theorem statement unchanged.",
            "- Return a concise, actionable route rather than a long essay.",
            "",
            "Response format:",
            "1. Immediate next proving route using Lean-available ingredients.",
            "2. One to three likely lemmas, theorem-search queries, or file-local pivots.",
            "3. A blocker test describing when to stop and write a durable blocker artifact instead of looping.",
            "",
            "User request:",
            "Need a route that survives missing Lean LSP.",
        ]
    )


def test_helper_tool_reuses_recent_auto_note_for_same_reason(monkeypatch, tmp_path: Path, capsys):
    config_path = tmp_path / "runtime-config.toml"
    config_path.write_text(
        """
[helper]
enabled = true
provider = "openai"
model = "gpt-5.4-mini"
api_key_env = "ALT_OPENAI_KEY"
base_url_env = "ALT_OPENAI_BASE_URL"

[helper.plan]
notes_dir = ".archon/helper/plan"

[helper.prover]
notes_dir = ".archon/helper/prover"
reuse_recent_note_by_reason = true
""".strip()
        + "\n",
        encoding="utf-8",
    )
    note_path = tmp_path / ".archon" / "helper" / "prover" / "FATEM_42.lean__prover__lsp_timeout__20260415T030405Z.md"
    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text(
        "\n".join(
            [
                "# Helper Note",
                "",
                "- Generated at: `2026-04-15T03:04:05+00:00`",
                "- Phase: `prover`",
                "- Provider: `openai`",
                "- Model: `gpt-5.4-mini`",
                f"- Config path: `{config_path}`",
                "- Target: `FATEM/42.lean`",
                "- Reason: `lsp_timeout`",
                "- Prompt pack: `lsp_timeout`",
                "",
                "## Helper Output",
                "",
                "reuse this note",
                "",
            ]
        ),
        encoding="utf-8",
    )

    calls = 0

    def fake_call(prompt: str, model: str, think: bool, *, max_retries: int, initial_backoff_seconds: int, timeout_seconds: int) -> str:
        nonlocal calls
        calls += 1
        return "fresh helper result"

    fake_agent = SimpleNamespace(
        API_KEY_ENVS={"openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY", "openrouter": "OPENROUTER_API_KEY"},
        BASE_URL_ENVS={"openai": "OPENAI_BASE_URL", "gemini": "GEMINI_BASE_URL", "openrouter": "OPENROUTER_BASE_URL"},
        DEFAULTS={"openai": "gpt-5.4", "gemini": "gemini-3.1-pro-preview", "openrouter": "google/gemini-3.1-pro-preview"},
        MAX_RETRIES=5,
        INITIAL_BACKOFF_SECONDS=5,
        TIMEOUT=300,
        call_openai=fake_call,
        call_gemini=fake_call,
        call_openrouter=fake_call,
    )

    monkeypatch.chdir(tmp_path)
    module = load_helper_tool()
    monkeypatch.setattr(module, "_load_informal_agent", lambda: fake_agent)
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            "archon-helper",
            "--config",
            str(config_path),
            "--phase",
            "prover",
            "--rel-path",
            "FATEM/42.lean",
            "--reason",
            "lsp_timeout",
            "--prompt-pack",
            "auto",
            "--write-note",
            "auto",
            "Need a route that survives missing Lean LSP.",
        ],
    )

    assert module.main() == 0
    captured = capsys.readouterr()
    assert captured.out == "reuse this note\n"
    assert str(note_path) in captured.err
    assert calls == 0
    index_payload = json.loads((tmp_path / ".archon" / "informal" / "helper" / "helper-index.json").read_text(encoding="utf-8"))
    assert index_payload["entries"][0]["event"] == "note_reuse"
    assert index_payload["entries"][0]["reusedFrom"] == ".archon/helper/prover/FATEM_42.lean__prover__lsp_timeout__20260415T030405Z.md"


def test_helper_tool_requires_phase_before_auto_note_transport(monkeypatch, tmp_path: Path):
    config_path = tmp_path / "runtime-config.toml"
    config_path.write_text(
        """
[helper]
enabled = true
provider = "openai"
model = "gpt-5.4-mini"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    calls = 0

    def fake_call(prompt: str, model: str, think: bool, *, max_retries: int, initial_backoff_seconds: int, timeout_seconds: int) -> str:
        nonlocal calls
        calls += 1
        return "helper result"

    fake_agent = SimpleNamespace(
        API_KEY_ENVS={"openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY", "openrouter": "OPENROUTER_API_KEY"},
        BASE_URL_ENVS={"openai": "OPENAI_BASE_URL", "gemini": "GEMINI_BASE_URL", "openrouter": "OPENROUTER_BASE_URL"},
        DEFAULTS={"openai": "gpt-5.4", "gemini": "gemini-3.1-pro-preview", "openrouter": "google/gemini-3.1-pro-preview"},
        MAX_RETRIES=5,
        INITIAL_BACKOFF_SECONDS=5,
        TIMEOUT=300,
        call_openai=fake_call,
        call_gemini=fake_call,
        call_openrouter=fake_call,
    )

    module = load_helper_tool()
    monkeypatch.setattr(module, "_load_informal_agent", lambda: fake_agent)
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            "archon-helper",
            "--config",
            str(config_path),
            "--write-note",
            "auto",
            "route around timeout",
        ],
    )

    with pytest.raises(SystemExit, match="--phase is required"):
        module.main()
    assert calls == 0


def test_helper_tool_accepts_legacy_helper_json_override(monkeypatch, tmp_path: Path, capsys):
    config_path = tmp_path / "helper-provider.json"
    config_path.write_text(
        json.dumps(
            {
                "enabled": True,
                "provider": "openai",
                "model": "deepseek-reasoner",
                "apiKeyEnv": "DEEPSEEK_API_KEY",
                "baseUrlEnv": "DEEPSEEK_BASE_URL",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    module = load_helper_tool()
    monkeypatch.setattr(module.sys, "argv", ["archon-helper", "--config", str(config_path), "--print-effective-config"])

    assert module.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["legacyHelperJsonUsed"] is True
    assert payload["provider"] == "openai"


def test_helper_tool_uses_fallback_provider_when_primary_fails(monkeypatch, tmp_path: Path, capsys):
    config_path = tmp_path / "runtime-config.toml"
    config_path.write_text(
        """
[helper]
enabled = true
provider = "openai"
model = "deepseek-reasoner"
api_key_env = "DEEPSEEK_API_KEY"
base_url_env = "DEEPSEEK_BASE_URL"

[[helper.fallbacks]]
provider = "gemini"
model = "gemini-3.1-pro-preview"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    attempts: list[tuple[str, str]] = []

    def fail_openai(prompt: str, model: str, think: bool, *, max_retries: int, initial_backoff_seconds: int, timeout_seconds: int) -> str:
        attempts.append(("openai", model))
        raise SystemExit("Transport error: primary unavailable")

    def pass_gemini(prompt: str, model: str, think: bool, *, max_retries: int, initial_backoff_seconds: int, timeout_seconds: int) -> str:
        attempts.append(("gemini", model))
        return "fallback result"

    fake_agent = SimpleNamespace(
        API_KEY_ENVS={"openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY", "openrouter": "OPENROUTER_API_KEY"},
        BASE_URL_ENVS={"openai": "OPENAI_BASE_URL", "gemini": "GEMINI_BASE_URL", "openrouter": "OPENROUTER_BASE_URL"},
        DEFAULTS={"openai": "gpt-5.4", "gemini": "gemini-3.1-pro-preview", "openrouter": "google/gemini-3.1-pro-preview"},
        MAX_RETRIES=5,
        INITIAL_BACKOFF_SECONDS=5,
        TIMEOUT=300,
        call_openai=fail_openai,
        call_gemini=pass_gemini,
        call_openrouter=pass_gemini,
    )

    module = load_helper_tool()
    monkeypatch.setattr(module, "_load_informal_agent", lambda: fake_agent)
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            "archon-helper",
            "--config",
            str(config_path),
            "suggest a route",
        ],
    )

    assert module.main() == 0
    captured = capsys.readouterr()
    assert captured.out == "fallback result\n"
    assert "trying next fallback" in captured.err
    assert "used fallback gemini:gemini-3.1-pro-preview" in captured.err
    assert attempts == [("openai", "deepseek-reasoner"), ("gemini", "gemini-3.1-pro-preview")]


def test_helper_tool_records_transport_failure_and_skips_same_config_retry(monkeypatch, tmp_path: Path):
    config_path = tmp_path / "runtime-config.toml"
    config_path.write_text(
        """
[helper]
enabled = true
provider = "openai"
model = "gpt-5.4-mini"

[helper.prover]
reuse_recent_note_by_reason = false
""".strip()
        + "\n",
        encoding="utf-8",
    )

    calls = 0

    def fail_openai(prompt: str, model: str, think: bool, *, max_retries: int, initial_backoff_seconds: int, timeout_seconds: int) -> str:
        nonlocal calls
        calls += 1
        raise SystemExit("Transport error: provider unavailable")

    fake_agent = SimpleNamespace(
        API_KEY_ENVS={"openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY", "openrouter": "OPENROUTER_API_KEY"},
        BASE_URL_ENVS={"openai": "OPENAI_BASE_URL", "gemini": "GEMINI_BASE_URL", "openrouter": "OPENROUTER_BASE_URL"},
        DEFAULTS={"openai": "gpt-5.4", "gemini": "gemini-3.1-pro-preview", "openrouter": "google/gemini-3.1-pro-preview"},
        MAX_RETRIES=5,
        INITIAL_BACKOFF_SECONDS=5,
        TIMEOUT=300,
        call_openai=fail_openai,
        call_gemini=fail_openai,
        call_openrouter=fail_openai,
    )

    monkeypatch.chdir(tmp_path)
    module = load_helper_tool()
    monkeypatch.setattr(module, "_load_informal_agent", lambda: fake_agent)
    argv = [
        "archon-helper",
        "--config",
        str(config_path),
        "--phase",
        "prover",
        "--rel-path",
        "FATEM/42.lean",
        "--reason",
        "missing_infrastructure",
        "Need a route around missing infrastructure.",
    ]
    monkeypatch.setattr(module.sys, "argv", argv)

    with pytest.raises(SystemExit, match="helper transport failed across configured providers"):
        module.main()
    assert calls == 1

    index_path = tmp_path / ".archon" / "informal" / "helper" / "helper-index.json"
    index_payload = json.loads(index_path.read_text(encoding="utf-8"))
    assert index_payload["entries"][-1]["event"] == "provider_call_failed"
    assert index_payload["entries"][-1]["reason"] == "missing_infrastructure"
    assert index_payload["entries"][-1]["failureMessage"] == "Transport error: provider unavailable"
    assert "configSignature" in index_payload["entries"][-1]

    module = load_helper_tool()
    monkeypatch.setattr(module, "_load_informal_agent", lambda: fake_agent)
    monkeypatch.setattr(module.sys, "argv", argv)
    with pytest.raises(SystemExit, match="already failed for the same config"):
        module.main()
    assert calls == 1

    index_payload = json.loads(index_path.read_text(encoding="utf-8"))
    assert index_payload["entries"][-1]["event"] == "skipped_by_cooldown"
    assert index_payload["entries"][-1]["cooldownKind"] == "config_signature_failure"

    module = load_helper_tool()
    monkeypatch.setattr(module, "_load_informal_agent", lambda: fake_agent)
    monkeypatch.setattr(module.sys, "argv", argv[:-1] + ["--force-fresh-call", argv[-1]])
    with pytest.raises(SystemExit, match="helper transport failed across configured providers"):
        module.main()
    assert calls == 2


def test_helper_tool_blocks_fresh_call_after_reason_budget_and_requires_reuse(monkeypatch, tmp_path: Path):
    config_path = tmp_path / "runtime-config.toml"
    config_path.write_text(
        """
[helper]
enabled = true
provider = "openai"
model = "gpt-5.4-mini"

[helper.prover]
max_calls_per_reason = 1
reuse_recent_note_by_reason = false
""".strip()
        + "\n",
        encoding="utf-8",
    )
    index_path = tmp_path / ".archon" / "informal" / "helper" / "helper-index.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "entries": [
                    {
                        "createdAt": "2026-04-15T00:00:00+00:00",
                        "event": "provider_call",
                        "notePath": None,
                        "phase": "prover",
                        "promptPack": "lsp_timeout",
                        "provider": "openai",
                        "reason": "lsp_timeout",
                        "relPath": "FATEM/42.lean",
                        "reusedFrom": None,
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    calls = 0

    def fake_call(prompt: str, model: str, think: bool, *, max_retries: int, initial_backoff_seconds: int, timeout_seconds: int) -> str:
        nonlocal calls
        calls += 1
        return "fresh helper result"

    fake_agent = SimpleNamespace(
        API_KEY_ENVS={"openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY", "openrouter": "OPENROUTER_API_KEY"},
        BASE_URL_ENVS={"openai": "OPENAI_BASE_URL", "gemini": "GEMINI_BASE_URL", "openrouter": "OPENROUTER_BASE_URL"},
        DEFAULTS={"openai": "gpt-5.4", "gemini": "gemini-3.1-pro-preview", "openrouter": "google/gemini-3.1-pro-preview"},
        MAX_RETRIES=5,
        INITIAL_BACKOFF_SECONDS=5,
        TIMEOUT=300,
        call_openai=fake_call,
        call_gemini=fake_call,
        call_openrouter=fake_call,
    )

    monkeypatch.chdir(tmp_path)
    module = load_helper_tool()
    monkeypatch.setattr(module, "_load_informal_agent", lambda: fake_agent)
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            "archon-helper",
            "--config",
            str(config_path),
            "--phase",
            "prover",
            "--rel-path",
            "FATEM/42.lean",
            "--reason",
            "lsp_timeout",
            "Need a route that survives missing Lean LSP.",
        ],
    )

    with pytest.raises(SystemExit, match="reason budget exhausted"):
        module.main()
    assert calls == 0


def test_helper_tool_blocks_fresh_call_during_plan_cooldown_and_records_skip(monkeypatch, tmp_path: Path):
    config_path = tmp_path / "runtime-config.toml"
    config_path.write_text(
        """
[helper]
enabled = true
provider = "openai"
model = "gpt-5.4-mini"

[helper.plan]
cooldown_iterations_per_reason = 1
""".strip()
        + "\n",
        encoding="utf-8",
    )
    index_path = tmp_path / ".archon" / "informal" / "helper" / "helper-index.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "entries": [
                    {
                        "createdAt": "2026-04-15T00:00:00+00:00",
                        "event": "provider_call",
                        "iteration": 7,
                        "notePath": None,
                        "phase": "plan",
                        "promptPack": "repeated_failure",
                        "provider": "openai",
                        "reason": "repeated_failure",
                        "relPath": "FATEM/42.lean",
                        "reusedFrom": None,
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    calls = 0

    def fake_call(prompt: str, model: str, think: bool, *, max_retries: int, initial_backoff_seconds: int, timeout_seconds: int) -> str:
        nonlocal calls
        calls += 1
        return "fresh helper result"

    fake_agent = SimpleNamespace(
        API_KEY_ENVS={"openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY", "openrouter": "OPENROUTER_API_KEY"},
        BASE_URL_ENVS={"openai": "OPENAI_BASE_URL", "gemini": "GEMINI_BASE_URL", "openrouter": "OPENROUTER_BASE_URL"},
        DEFAULTS={"openai": "gpt-5.4", "gemini": "gemini-3.1-pro-preview", "openrouter": "google/gemini-3.1-pro-preview"},
        MAX_RETRIES=5,
        INITIAL_BACKOFF_SECONDS=5,
        TIMEOUT=300,
        call_openai=fake_call,
        call_gemini=fake_call,
        call_openrouter=fake_call,
    )

    monkeypatch.chdir(tmp_path)
    module = load_helper_tool()
    monkeypatch.setattr(module, "_load_informal_agent", lambda: fake_agent)
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            "archon-helper",
            "--config",
            str(config_path),
            "--phase",
            "plan",
            "--rel-path",
            "FATEM/42.lean",
            "--reason",
            "repeated_failure",
            "--iteration",
            "8",
            "Need a materially different plan.",
        ],
    )

    with pytest.raises(SystemExit, match="cooldown is active"):
        module.main()
    assert calls == 0
    index_payload = json.loads(index_path.read_text(encoding="utf-8"))
    assert index_payload["entries"][-1]["event"] == "skipped_by_cooldown"
    assert index_payload["entries"][-1]["reason"] == "repeated_failure"
