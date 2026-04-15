import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_readme_centers_interactive_campaign_operator_and_result_paths():
    readme = read("README.md")

    assert "# AutoArchon" in readme
    assert "campaign-operator" in readme
    assert "Fastest Campaign Start" in readme
    assert "codex -C /path/to/AutoArchon" in readme
    assert "docs/templates/campaign-operator-prompt-template.md" in readme
    assert "scripts/start_campaign_operator.sh" in readme
    assert "Optional Wrapper" in readme
    assert "$archon-orchestrator" in readme
    assert "autoarchon-render-operator-prompt" in readme
    assert "Advanced: Rendered Prompt Path" in readme
    assert "autoarchon-validate-launch-contract" in readme
    assert "control/mission-brief.md" in readme
    assert "control/launch-spec.resolved.json" in readme
    assert "control/operator-journal.md" in readme
    assert "autoarchon-init-campaign-spec" in readme
    assert "autoarchon-init-operator-intake" in readme
    assert "Shortcut: Scripted Start" in readme
    assert "preloadHistoricalRoutes" in readme
    assert "formalization-default.json" in readme
    assert "open-problem-default.json" in readme
    assert "--source-roots-root" in readme
    assert "--source-subdir" in readme
    assert "--campaign-slug" in readme
    assert "bash scripts/watch_campaign.sh" in readme
    assert "bash scripts/watch_run.sh" in readme
    assert "Control-plane commands" in readme
    assert "not the web UI" in readme
    assert "control/progress-summary.md" in readme
    assert "control/progress-summary.json" in readme
    assert "workspace/.archon/supervisor/progress-summary.md" in readme
    assert "runtime-config.toml" in readme
    assert "examples/helper.env" in readme
    assert "examples/helper.env.example" in readme
    assert "canonical observability surface" in readme
    assert "supplementary inspection" in readme
    assert "Where Proofs and Lessons End Up" in readme
    assert "reports/final/lessons/lesson-records.jsonl" in readme
    assert "reports/postmortem/lessons/lesson-records.jsonl" in readme
    assert "docs/campaign-operator.md" in readme
    assert "autoarchon-storage-report" in readme
    assert "docs/roadmaps/control-plane-phase7.md" in readme


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
    assert "--preload-historical-routes" in operations
    assert "HISTORICAL_ROUTES.md" in operations
    assert "autoarchon-export-run-artifacts" in operations
    assert "autoarchon-storage-report" in operations
    assert "workspace/.archon/supervisor/progress-summary.md" in operations


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
    assert "progress-summary.md" in teacher_doc
    assert "task_results" in teacher_doc
    assert "artifacts/" in teacher_doc
    assert "timeline.json" in teacher_doc


def test_campaign_operator_doc_covers_default_and_interactive_owner_paths():
    operator_doc = read("docs/campaign-operator.md")

    assert "campaign-operator" in operator_doc
    assert "codex -C /path/to/AutoArchon" in operator_doc
    assert "docs/templates/campaign-operator-prompt-template.md" in operator_doc
    assert "control/mission-brief.md" in operator_doc
    assert "control/operator-journal.md" in operator_doc
    assert "Detailed TODO" in operator_doc
    assert "autoarchon-init-campaign-spec" in operator_doc
    assert "autoarchon-init-operator-intake" in operator_doc
    assert "autoarchon-launch-from-spec" in operator_doc
    assert "preloadHistoricalRoutes" in operator_doc
    assert "open-problem-default.json" in operator_doc
    assert "--source-subdir" in operator_doc
    assert "--campaign-slug" in operator_doc
    assert "bash scripts/start_fate_overnight_watchdogs.sh" in operator_doc
    assert "bash scripts/watch_campaign.sh" in operator_doc
    assert "scripts/start_campaign_operator.sh" in operator_doc
    assert "Optional Wrapper" in operator_doc
    assert "$archon-orchestrator" in operator_doc
    assert "autoarchon-render-operator-prompt" in operator_doc
    assert "examples/helper.env" in operator_doc
    assert "autoarchon-validate-launch-contract" in operator_doc
    assert "autoarchon-campaign-overview" in operator_doc
    assert "autoarchon-campaign-recover" in operator_doc
    assert "autoarchon-finalize-campaign" in operator_doc
    assert "autoarchon-campaign-archive" in operator_doc
    assert "owner-mode.json" in operator_doc
    assert "owner-lease.json" in operator_doc
    assert "launch-spec.resolved.json" in operator_doc


def test_architecture_doc_contains_global_mermaid_and_future_extension_points():
    architecture = read("docs/architecture.md")

    assert "```mermaid" in architecture
    assert "campaign-operator" in architecture
    assert "orchestrator-agent" in architecture
    assert "watchdog" in architecture
    assert "statement-validator" in architecture
    assert "supervisor-agent" in architecture
    assert "helper-prover-agent" in architecture
    assert "mathlib-agent" in architecture
    assert "reports/final/" in architecture
    assert "reports/postmortem/" in architecture
    assert "reports/final/lessons/lesson-records.jsonl" in architecture
    assert "archon-lean-lsp" in architecture
    assert "mission-brief.md" in architecture
    assert "operator-journal.md" in architecture
    assert "owner-lease.json" in architecture
    assert "launch-spec.resolved.json" in architecture
    assert "progress-summary.md" in architecture
    assert "progress-summary.json" in architecture
    assert "workspace/.archon/supervisor/progress-summary.md" in architecture
    assert "reportFreshness" in architecture
    assert "input_tokens" in architecture
    assert "output_tokens" in architecture


def test_campaign_operator_prompt_template_exists_and_matches_direct_codex_flow():
    template = read("docs/templates/campaign-operator-prompt-template.md")

    assert "$archon-orchestrator" in template
    assert "Repository root:" in template
    assert "Source root:" in template
    assert "Campaign root:" in template
    assert "Real user objective" in template
    assert "ask intake questions" in template


def test_benchmarking_doc_still_explains_contamination_and_faithful_runs():
    benchmarking = read("docs/benchmarking.md")

    assert "benchmark-faithful" in benchmarking
    assert "contaminated" in benchmarking
    assert "do not reuse another run's `.archon/` state" in benchmarking
    assert "--preload-historical-routes" in benchmarking
    assert "FATEM/42.lean" in benchmarking


def test_agent_registry_doc_marks_campaign_operator_active_and_future_roles_explicit():
    registry_doc = read("docs/agent-registry.md")

    assert "`agents/*.json`" in registry_doc
    assert "campaign-operator" in registry_doc
    assert "helper-prover-agent" in registry_doc
    assert "mathlib-agent" in registry_doc
    assert "runtime-config.toml" in registry_doc
    assert "mission-brief.md" in registry_doc
    assert "operator-journal.md" in registry_doc
    assert "mechanical wrapper" in registry_doc
    assert "archive/manager-agent.md" in registry_doc
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
        "helper-prover-agent",
        "mathlib-agent",
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


def test_manager_archive_doc_keeps_manager_off_the_default_runtime_path():
    archive_doc = read("docs/archive/manager-agent.md")

    assert "manager-agent" in archive_doc
    assert "not part of the default runtime path" in archive_doc
    assert "campaign-operator" in archive_doc


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


def test_postmortem_doc_records_20260415_rerun12_as_fresh_finalized_evidence():
    postmortem_doc = read("docs/postmortem-20260415-rerun12-fatem-42-45-94.md")
    readme = read("README.md")

    assert "20260415-rerun12-fatem-42-45-94" in postmortem_doc
    assert "accepted = 2" in postmortem_doc
    assert "blocked = 1" in postmortem_doc
    assert "teacher-42:FATEM_42.lean.md" in postmortem_doc
    assert "teacher-45:FATEM/45.lean" in postmortem_doc
    assert "teacher-94:FATEM/94.lean" in postmortem_doc
    assert "reports/final/final-summary.json" in postmortem_doc
    assert "reuse_build_outputs" in postmortem_doc
    assert "durable artifact discipline" in postmortem_doc
    assert "docs/postmortem-20260415-rerun12-fatem-42-45-94.md" in readme


def test_phase6_roadmap_captures_operator_helper_observability_and_knowledge_work():
    roadmap = read("docs/roadmaps/control-plane-phase6.md")

    assert "Phase 6 Roadmap" in roadmap
    assert "campaign-operator" in roadmap
    assert "helper" in roadmap
    assert "observability" in roadmap
    assert "progress-summary.json" in roadmap
    assert "mathlib-agent" in roadmap
    assert "formalization-default.json" in roadmap
    assert "operator bootstrap" in roadmap


def test_phase7_roadmap_captures_operator_validator_helper_kanban_and_mathlib_research():
    roadmap = read("docs/roadmaps/control-plane-phase7.md")

    assert "Phase 7 Roadmap" in roadmap
    assert "campaign-operator" in roadmap
    assert "autoarchon-validate-launch-contract" in roadmap
    assert "progress-summary.json" in roadmap
    assert "lesson-reminders.json" in roadmap
    assert "analysis/mathlib-agent/" in roadmap
    assert "formalization" in roadmap
