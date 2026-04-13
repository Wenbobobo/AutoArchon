from pathlib import Path

from archonlib.agent_registry import load_agent_contracts


ROOT = Path(__file__).resolve().parents[1]


def test_runtime_agent_registry_is_well_formed_and_covers_current_plan():
    payloads = load_agent_contracts()
    ids = {payload["id"] for payload in payloads}

    assert {
        "campaign-operator",
        "manager-agent",
        "plan-agent",
        "prover-agent",
        "review-agent",
        "informal-agent",
        "statement-validator",
        "supervisor-agent",
        "orchestrator-agent",
    } <= ids

    for payload in payloads:
        assert payload["status"] in {"active", "proposed"}
        assert payload["kind"]
        assert payload["summary"]
        assert payload["reads"]
        assert payload["writes"] is not None
        assert payload["outputs"]
        assert payload["handoff_to"] is not None
        assert payload["observability"]


def test_runtime_scripts_reference_canonical_agent_contracts():
    init_script = (ROOT / "init.sh").read_text(encoding="utf-8")
    loop_script = (ROOT / "archon-loop.sh").read_text(encoding="utf-8")
    agents_template = (ROOT / ".archon-src" / "archon-template" / "AGENTS.md").read_text(encoding="utf-8")

    assert 'ln -sfn "${ARCHON_DIR}/agents" "${STATE_DIR}/agents"' in init_script
    assert "${STATE_DIR}/agents/plan-agent.json" in loop_script
    assert "${STATE_DIR}/agents/prover-agent.json" in loop_script
    assert "${STATE_DIR}/agents/review-agent.json" in loop_script
    assert ".archon/agents/" in agents_template
