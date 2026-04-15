from __future__ import annotations

import importlib.util
from pathlib import Path


def load_informal_agent():
    module_path = Path(__file__).resolve().parents[1] / ".archon-src" / "tools" / "informal_agent.py"
    spec = importlib.util.spec_from_file_location("archon_informal_agent", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_require_key_falls_back_to_codex_auth(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    (codex_dir / "auth.json").write_text('{"OPENAI_API_KEY":"sk-test-from-auth"}', encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))

    module = load_informal_agent()

    assert module._require_key("OPENAI_API_KEY") == "sk-test-from-auth"


def test_require_key_prefers_environment(monkeypatch, tmp_path: Path):
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    (codex_dir / "auth.json").write_text('{"OPENAI_API_KEY":"sk-test-from-auth"}', encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-from-env")

    module = load_informal_agent()

    assert module._require_key("OPENAI_API_KEY") == "sk-test-from-env"


def test_base_url_prefers_environment_override(monkeypatch):
    module = load_informal_agent()

    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.invalid/v1")

    assert module._base_url("openai") == "https://example.invalid/v1"


def test_base_url_appends_provider_default_path_when_override_has_host_only(monkeypatch):
    module = load_informal_agent()

    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.invalid")

    assert module._base_url("openai") == "https://example.invalid/v1"
