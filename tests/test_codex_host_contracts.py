from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_lean4_hooks_support_codex_plugin_root():
    bootstrap = read(".archon-src/skills/lean4/hooks/bootstrap.sh")
    hooks_json = read(".archon-src/skills/lean4/hooks/hooks.json")

    assert "CODEX_PLUGIN_ROOT" in bootstrap
    assert 'missing CODEX_PLUGIN_ROOT' in bootstrap
    assert 'command": "${CODEX_PLUGIN_ROOT}/hooks/bootstrap.sh"' in hooks_json
    assert 'command": "${CODEX_PLUGIN_ROOT}/hooks/guardrails.sh"' in hooks_json
    assert 'python3 ${CODEX_PLUGIN_ROOT}/hooks/snapshot.py' in hooks_json


def test_runtime_docs_point_to_codex_not_claude():
    readme = read(".archon-src/skills/lean4/README.md")
    doctor = read(".archon-src/skills/lean4/commands/doctor.md")
    snapshot = read(".archon-src/skills/lean4/hooks/snapshot.py")
    informal_agent = read(".archon-src/tools/informal_agent.py")

    assert "Claude Code adapter" not in readme
    assert "Codex adapter" in readme
    assert "claude mcp list" not in doctor
    assert "codex mcp list" in doctor
    assert "Claude Code" not in snapshot
    assert "anthropic/claude" not in informal_agent


def test_lint_docs_tracks_codex_plugin_paths():
    lint_docs = read(".archon-src/skills/lean4/tools/lint_docs.sh")

    assert ".codex-plugin/plugin.json" in lint_docs
    assert ".codex-plugin/marketplace.json" in lint_docs
