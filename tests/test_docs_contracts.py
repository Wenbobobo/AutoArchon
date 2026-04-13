import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_readme_centers_campaign_operator_spec_launch_and_proof_locations():
    readme = read("README.md")

    assert "# AutoArchon" in readme
    assert "campaign-operator" in readme
    assert "Fastest Campaign Start" in readme
    assert "bash scripts/start_fate_overnight_watchdogs.sh" in readme
    assert "autoarchon-launch-from-spec" in readme
    assert "autoarchon-campaign-overview" in readme
    assert "autoarchon-campaign-archive" in readme
    assert "control-plane commands" in readme
    assert "not the web UI" in readme
    assert "Where Proofs End Up" in readme
    assert "run-root/workspace/" in readme
    assert "reports/final/" in readme
    assert "reports/postmortem/" in readme
    assert "campaign_specs/" in readme


def test_operations_doc_covers_single_run_prewarm_supervisor_and_export_flow():
    operations = read("docs/operations.md")

    assert "autoarchon-create-run-workspace" in operations
    assert "autoarchon-prewarm-project" in operations
    assert "--verify-file" in operations
    assert "RUN_MANIFEST.json" in operations
    assert "projectBuildReused" in operations
    assert "prewarmRequired" in operations
    assert "allowedFiles" in operations
    assert "lake env lean" in operations
    assert "codex exec" in operations
    assert "$archon-supervisor" in operations
    assert "autoarchon-supervised-cycle" in operations
    assert "autoarchon-export-run-artifacts" in operations


def test_teacher_agents_doc_covers_launch_monitoring_and_results():
    teacher_doc = read("docs/teacher-agents.md")

    assert "$archon-supervisor" in teacher_doc
    assert "codex exec" in teacher_doc
    assert "launch-teacher.sh" in teacher_doc
    assert "RUN_MANIFEST.json" in teacher_doc
    assert "prewarmRequired" in teacher_doc
    assert "allowedFiles" in teacher_doc
    assert "HOT_NOTES.md" in teacher_doc
    assert "LEDGER.md" in teacher_doc
    assert "task_results" in teacher_doc
    assert "artifacts/" in teacher_doc
    assert "timeline.json" in teacher_doc


def test_orchestrator_doc_covers_interactive_and_spec_driven_owner_paths():
    orchestrator_doc = read("docs/orchestrator.md")

    assert "campaign-operator" in orchestrator_doc
    assert "bash scripts/start_fate_overnight_watchdogs.sh" in orchestrator_doc or "autoarchon-launch-from-spec" in orchestrator_doc
    assert "codex -C /path/to/AutoArchon" in orchestrator_doc
    assert "$archon-orchestrator" in orchestrator_doc
    assert "autoarchon-plan-shards" in orchestrator_doc
    assert "autoarchon-create-campaign" in orchestrator_doc
    assert "autoarchon-launch-from-spec" in orchestrator_doc
    assert "autoarchon-campaign-overview" in orchestrator_doc
    assert "autoarchon-campaign-recover" in orchestrator_doc
    assert "autoarchon-finalize-campaign" in orchestrator_doc
    assert "autoarchon-campaign-archive" in orchestrator_doc
    assert "launch-teacher.sh" in orchestrator_doc
    assert "owner-mode.json" in orchestrator_doc
    assert "owner-lease.json" in orchestrator_doc
    assert "launch-spec.resolved.json" in orchestrator_doc
    assert "file_stem" in orchestrator_doc


def test_architecture_doc_contains_global_mermaid_and_outer_role_story():
    architecture = read("docs/architecture.md")

    assert "```mermaid" in architecture
    assert "campaign-operator" in architecture
    assert "orchestrator-agent" in architecture
    assert "watchdog" in architecture
    assert "manager-agent" in architecture
    assert "statement-validator" in architecture
    assert "supervisor-agent" in architecture
    assert "reports/final/" in architecture
    assert "reports/postmortem/" in architecture
    assert "archon-lean-lsp" in architecture
    assert "owner-lease.json" in architecture
    assert "launch-spec.resolved.json" in architecture
    assert "reportFreshness" in architecture
    assert "input_tokens" in architecture
    assert "output_tokens" in architecture


def test_benchmarking_doc_still_explains_contamination_and_faithful_runs():
    benchmarking = read("docs/benchmarking.md")

    assert "benchmark-faithful" in benchmarking
    assert "contaminated" in benchmarking
    assert "do not reuse another run's `.archon/` state" in benchmarking
    assert "FATEM/42.lean" in benchmarking


def test_agent_registry_doc_marks_campaign_operator_active_and_manager_future():
    registry_doc = read("docs/agent-registry.md")

    assert "`agents/*.json`" in registry_doc
    assert "campaign-operator" in registry_doc
    assert "orchestrator-agent" in registry_doc
    assert "manager-agent" in registry_doc
    assert "mechanical wrapper" in registry_doc
    assert "not the canonical runtime registry" in registry_doc
    assert ".archon-src/skills/lean4/agents/" in registry_doc


def test_agent_registry_json_files_are_well_formed_and_cover_current_roles():
    registry_dir = ROOT / "agents"
    files = sorted(registry_dir.glob("*.json"))

    assert files
    payloads = [json.loads(path.read_text(encoding="utf-8")) for path in files]
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


def test_manager_watchdog_doc_covers_watchdog_state_owner_lease_and_launch_from_spec():
    manager_doc = read("docs/manager-watchdog.md")

    assert "campaign-operator" in manager_doc
    assert "manager-agent" in manager_doc
    assert "autoarchon-launch-from-spec" in manager_doc
    assert "autoarchon-orchestrator-watchdog" in manager_doc
    assert "owner-mode.json" in manager_doc
    assert "owner-lease.json" in manager_doc
    assert "orchestrator-watchdog.json" in manager_doc
    assert "watchdogStatus" in manager_doc
    assert "restartCount" in manager_doc
    assert "runCounts" in manager_doc
    assert "statusRunIds" in manager_doc
    assert "recoverableRunIds" in manager_doc
    assert "prewarmPlanCounts" in manager_doc
    assert "prewarmPendingRunIds" in manager_doc
    assert "activeLaunches" in manager_doc
    assert "launchBudget" in manager_doc
    assert "lastStatusRefreshAt" in manager_doc
    assert "lastProgressAt" in manager_doc
    assert "lastRecoveryAt" in manager_doc
    assert "lastCompareReportAt" in manager_doc
    assert "ownerLastLogAt" in manager_doc
    assert "budgetExhausted" in manager_doc
    assert "reportFreshness" in manager_doc
    assert "ownerLease" in manager_doc
    assert "concrete reliability wrapper" in manager_doc


def test_postmortem_doc_records_20260413_nightly_samples_as_archived_only():
    postmortem_doc = read("docs/postmortem-20260413-nightly.md")
    roadmap = read("docs/roadmaps/control-plane-phase4.md")

    assert "20260413-nightly-fate-m-full" in postmortem_doc
    assert "20260413-nightly-fate-h-full" in postmortem_doc
    assert "20260413-nightly-fate-x-full" in postmortem_doc
    assert "archived samples, not final benchmark results" in postmortem_doc
    assert "restart budget exhaustion" in postmortem_doc.lower()
    assert "stale watchdog state" in postmortem_doc.lower()
    assert "fresh campaign root" in postmortem_doc

    assert "campaign-operator" in roadmap
    assert "autoarchon-launch-from-spec" in roadmap
    assert "nightly FATE samples" in roadmap
