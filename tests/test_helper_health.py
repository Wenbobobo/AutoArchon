from __future__ import annotations

import subprocess
from pathlib import Path

from archonlib.helper_health import (
    helper_model_provider_mismatch,
    load_helper_env_file,
    probe_helper_transport,
)


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_load_helper_env_file_parses_assignments_and_quotes(tmp_path: Path):
    env_file = tmp_path / "helper.env"
    write(
        env_file,
        "\n".join(
            [
                "# comment",
                "ARCHON_HELPER_ENABLE=1",
                "ARCHON_HELPER_PROVIDER='openai'",
                'ARCHON_HELPER_MODEL="gpt-5.4"',
                "",
            ]
        ),
    )

    payload = load_helper_env_file(env_file)

    assert payload == {
        "ARCHON_HELPER_ENABLE": "1",
        "ARCHON_HELPER_PROVIDER": "openai",
        "ARCHON_HELPER_MODEL": "gpt-5.4",
    }


def test_helper_model_provider_mismatch_flags_obvious_cross_provider_pairs():
    assert helper_model_provider_mismatch("openai", "gemini-2.5-pro") is True
    assert helper_model_provider_mismatch("gemini", "gpt-5.4") is True
    assert helper_model_provider_mismatch("openrouter", "gemini-2.5-pro") is False
    assert helper_model_provider_mismatch("openai", "gpt-5.4") is False


def test_probe_helper_transport_classifies_invalid_credentials_and_redacts_tokens(tmp_path: Path):
    env_file = tmp_path / "helper.env"
    write(
        env_file,
        "\n".join(
            [
                "ARCHON_HELPER_ENABLE=1",
                "ARCHON_HELPER_PROVIDER=openai",
                "ARCHON_HELPER_MODEL=gpt-5.4",
            ]
        ),
    )

    def fake_runner(*args, **kwargs) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr=(
                "API error 401: Incorrect API key provided: "
                "sk-abcdefghijklmnopqrstuvwxyz1234567890"
            ),
        )

    payload = probe_helper_transport(
        repo_root=tmp_path,
        env_file=env_file,
        runner=fake_runner,
    )

    assert payload["status"] == "failed"
    assert payload["classification"] == "invalid_credentials"
    assert "sk-abcdefghijklmnopqrstuvwxyz1234567890" not in payload["message"]
    assert "sk-[redacted]" in payload["message"]


def test_probe_helper_transport_reports_success(tmp_path: Path):
    env_file = tmp_path / "helper.env"
    write(
        env_file,
        "\n".join(
            [
                "ARCHON_HELPER_ENABLE=1",
                "ARCHON_HELPER_PROVIDER=openai",
                "ARCHON_HELPER_MODEL=gpt-5.4",
            ]
        ),
    )

    def fake_runner(*args, **kwargs) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="OK\n", stderr="")

    payload = probe_helper_transport(
        repo_root=tmp_path,
        env_file=env_file,
        runner=fake_runner,
    )

    assert payload["status"] == "ok"
    assert payload["classification"] is None
