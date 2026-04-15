from __future__ import annotations

import importlib.util
import json
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

[helper.prover]
enabled = true
max_calls_per_session = 4
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
