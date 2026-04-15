from __future__ import annotations

import json
from pathlib import Path

from archonlib.runtime_config import load_runtime_config, load_runtime_config_from_path


def test_load_runtime_config_prefers_toml_and_exposes_helper_policies(tmp_path: Path):
    workspace = tmp_path / "workspace"
    archon_dir = workspace / ".archon"
    archon_dir.mkdir(parents=True)
    (archon_dir / "runtime-config.toml").write_text(
        """
[helper]
enabled = true
provider = "openai"
model = "gpt-5.4"
api_key_env = "OPENAI_API_KEY"
base_url_env = "OPENAI_BASE_URL"
max_retries = 8
initial_backoff_seconds = 9
timeout_seconds = 321

[[helper.fallbacks]]
provider = "gemini"
model = "gemini-3.1-pro-preview"

[helper.plan]
enabled = true
max_calls_per_iteration = 2
trigger_on_missing_infrastructure = true
trigger_on_external_reference = false
trigger_on_repeated_failure = true
reuse_recent_note_by_reason = false
notes_dir = ".archon/informal/helper"

[helper.prover]
enabled = true
max_calls_per_session = 3
trigger_on_missing_infrastructure = true
trigger_on_lsp_timeout = false
trigger_on_first_stuck_attempt = true
reuse_recent_note_by_reason = false
notes_dir = ".archon/informal/helper"

[observability]
write_progress_surface = false
""".strip()
        + "\n",
        encoding="utf-8",
    )

    config = load_runtime_config(workspace)

    assert config.helper is not None
    assert config.helper.provider == "openai"
    assert config.helper.max_retries == 8
    assert len(config.helper.fallbacks) == 1
    assert config.helper.fallbacks[0].provider == "gemini"
    assert config.helper_plan.max_calls_per_iteration == 2
    assert config.helper_plan.trigger_on_external_reference is False
    assert config.helper_plan.reuse_recent_note_by_reason is False
    assert config.helper_prover.max_calls_per_session == 3
    assert config.helper_prover.trigger_on_lsp_timeout is False
    assert config.helper_prover.reuse_recent_note_by_reason is False
    assert config.observability.write_progress_surface is False
    assert config.legacy_helper_json_used is False


def test_load_runtime_config_falls_back_to_legacy_helper_json(tmp_path: Path):
    workspace = tmp_path / "workspace"
    archon_dir = workspace / ".archon"
    archon_dir.mkdir(parents=True)
    (archon_dir / "helper-provider.json").write_text(
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

    config = load_runtime_config(workspace)

    assert config.helper is not None
    assert config.helper.model == "deepseek-reasoner"
    assert config.legacy_helper_json_used is True
    assert config.helper_plan.max_calls_per_iteration == 1
    assert config.helper_plan.reuse_recent_note_by_reason is True
    assert config.observability.write_progress_surface is True


def test_load_runtime_config_from_path_accepts_legacy_json_explicitly(tmp_path: Path):
    config_path = tmp_path / "helper-provider.json"
    config_path.write_text(
        json.dumps(
            {
                "enabled": True,
                "provider": "gemini",
                "model": "gemini-3.1-pro-preview",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    config = load_runtime_config_from_path(config_path)

    assert config.helper is not None
    assert config.helper.provider == "gemini"
    assert config.legacy_helper_json_used is True
