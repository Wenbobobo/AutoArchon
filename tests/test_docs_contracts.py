import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_readme_points_to_generated_proofs_logs_and_dashboard():
    readme = read("README.md")

    assert "# AutoArchon" in readme
    assert "Where Proofs End Up" in readme
    assert "Use $archon-orchestrator to own this AutoArchon campaign." in readme
    assert "Use $archon-orchestrator to own this existing AutoArchon campaign." in readme
    assert "codex -C /path/to/AutoArchon" in readme
    assert "scripts/install_repo_skill.sh" in readme
    assert "Run id mode: file_stem" in readme
    assert "teacher-launch-state.json" in readme
    assert ".archon/logs/iter-" in readme
    assert ".archon/task_results/" in readme
    assert ".archon/proof-journal/" in readme
    assert "bash ui/start.sh --project" in readme
    assert "control-plane commands" in readme
    assert "not the web UI" in readme
    assert "docs/architecture.md" in readme
    assert "docs/benchmarking.md" in readme
    assert "docs/agent-registry.md" in readme
    assert "docs/orchestrator.md" in readme
    assert "docs/manager-watchdog.md" in readme
    assert "docs/teacher-agents.md" in readme
    assert "docs/operations.md" in readme
    assert "docs/roadmaps/control-plane-phase3.md" in readme
    assert "autoarchon-create-campaign" in readme
    assert "autoarchon-plan-shards" in readme
    assert "autoarchon-campaign-compare" in readme
    assert "autoarchon-campaign-recover" in readme
    assert "autoarchon-finalize-campaign" in readme
    assert "autoarchon-orchestrator-watchdog" in readme
    assert "run-lease.json" in readme
    assert "ORCHESTRATOR_GUIDE.md" not in readme


def test_teacher_agents_doc_covers_launch_monitoring_and_results():
    teacher_doc = read("docs/teacher-agents.md")

    assert "$archon-supervisor" in teacher_doc
    assert "codex exec" in teacher_doc
    assert "HOT_NOTES.md" in teacher_doc
    assert "LEDGER.md" in teacher_doc
    assert "launch-teacher.sh" in teacher_doc
    assert "task_results" in teacher_doc
    assert "artifacts/" in teacher_doc


def test_orchestrator_doc_covers_campaign_creation_monitoring_and_finalization():
    orchestrator_doc = read("docs/orchestrator.md")

    assert "bash scripts/install_repo_skill.sh" in orchestrator_doc
    assert "codex -C /path/to/AutoArchon" in orchestrator_doc
    assert "Use $archon-orchestrator to own this AutoArchon campaign." in orchestrator_doc
    assert "Use $archon-orchestrator to own this existing AutoArchon campaign." in orchestrator_doc
    assert "file_stem" in orchestrator_doc
    assert "autoarchon-create-campaign" in orchestrator_doc
    assert "autoarchon-plan-shards" in orchestrator_doc
    assert "autoarchon-campaign-status" in orchestrator_doc
    assert "autoarchon-campaign-compare" in orchestrator_doc
    assert "autoarchon-campaign-recover" in orchestrator_doc
    assert "autoarchon-finalize-campaign" in orchestrator_doc
    assert "launch-teacher.sh" in orchestrator_doc
    assert "run-lease.json" in orchestrator_doc
    assert "--recovery-only" in orchestrator_doc
    assert "compare-report.json" in orchestrator_doc
    assert "reports/final/" in orchestrator_doc
    assert "HOT_NOTES.md" in orchestrator_doc
    assert "LEDGER.md" in orchestrator_doc
    assert "not the web UI" in orchestrator_doc


def test_architecture_doc_contains_global_mermaid_and_extension_points():
    architecture = read("docs/architecture.md")

    assert "```mermaid" in architecture
    assert "manager-agent" in architecture
    assert "orchestrator-agent" in architecture
    assert "statement-validator" in architecture
    assert "supervisor-agent" in architecture
    assert "reports/final/" in architecture
    assert "archon-lean-lsp" in architecture
    assert ".archon/PROGRESS.md" in architecture
    assert "durationSecs" in architecture
    assert "input_tokens" in architecture
    assert "output_tokens" in architecture


def test_benchmarking_doc_separates_faithful_and_contaminated_runs():
    benchmarking = read("docs/benchmarking.md")

    assert "benchmark-faithful" in benchmarking
    assert "`iter-002`" in benchmarking
    assert "`iter-003`" in benchmarking
    assert "contaminated" in benchmarking
    assert "do not reuse another run's `.archon/` state" in benchmarking
    assert "FATEM/42.lean" in benchmarking


def test_agent_registry_doc_marks_vendored_lean4_agents_as_non_runtime():
    registry_doc = read("docs/agent-registry.md")

    assert "`agents/*.json`" in registry_doc
    assert ".archon-src/skills/lean4/agents/" in registry_doc
    assert "reference material" in registry_doc
    assert "not the canonical runtime registry" in registry_doc
    assert "orchestrator-agent" in registry_doc
    assert "manager-agent" in registry_doc


def test_agent_registry_json_files_are_well_formed_and_cover_current_plan():
    registry_dir = ROOT / "agents"
    files = sorted(registry_dir.glob("*.json"))

    assert files
    assert not (ROOT / "docs" / "agents").exists()

    payloads = [json.loads(path.read_text(encoding="utf-8")) for path in files]
    ids = {payload["id"] for payload in payloads}

    assert {
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


def test_manager_watchdog_doc_covers_owner_watchdog_and_ablation_contract():
    manager_doc = read("docs/manager-watchdog.md")

    assert "manager-agent" in manager_doc
    assert "autoarchon-orchestrator-watchdog" in manager_doc
    assert "orchestrator-watchdog.json" in manager_doc
    assert "sessionId" in manager_doc
    assert "restartCount" in manager_doc
    assert "supervisor-only" in manager_doc
    assert "orchestrator + watchdog" in manager_doc
