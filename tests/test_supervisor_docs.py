from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_phase2_roadmap_is_saved_in_repo():
    roadmap = read("docs/roadmaps/supervisor-phase2.md")

    assert "Supervisor Skill" in roadmap
    assert "source / workspace / artifacts" in roadmap
    assert "soak test" in roadmap


def test_readme_includes_repo_layout_skill_install_and_supervisor_entrypoint():
    readme = read("README.md")

    assert "Repository Layout" in readme
    assert "Quick Supervisor Soak Test" in readme
    assert "bash scripts/install_repo_skill.sh" in readme
    assert "$archon-supervisor" in readme
    assert "docs/operations.md" in readme
    assert "autoarchon-supervised-cycle" in readme
    assert "workspace/.archon/supervisor/progress-summary.md" in readme
    assert "scripts/watch_run.sh" in readme
    assert "autoarchon-storage-report" in readme


def test_operations_doc_contains_full_supervisor_soak_test_and_monitoring_commands():
    operations = read("docs/operations.md")

    assert "autoarchon-create-run-workspace" in operations
    assert "codex exec" in operations
    assert "$archon-supervisor" in operations
    assert "tail -f" in operations
    assert "watch_run.sh" in operations
    assert "violations.jsonl" in operations
    assert "autoarchon-export-run-artifacts" in operations
    assert "progress-summary.md" in operations
    assert "autoarchon-storage-report" in operations


def test_supervisor_skill_has_startup_brief_and_failure_taxonomy():
    skill = read("skills/archon-supervisor/SKILL.md")
    startup = read("skills/archon-supervisor/references/startup-brief.md")
    taxonomy = read("skills/archon-supervisor/references/failure-taxonomy.md")
    artifact_map = read("skills/archon-supervisor/references/artifact-map.md")

    assert "Do not stop to give an interim report" in skill
    assert "references/startup-brief.md" in skill
    assert "references/failure-taxonomy.md" in skill
    assert "references/artifact-map.md" in skill
    assert "Read this before touching the run" in startup
    assert "theorem mutation" in taxonomy
    assert "workspace/.archon/supervisor/HOT_NOTES.md" in artifact_map


def test_supervisor_skill_excludes_inner_plan_prover_review_sessions():
    skill = read("skills/archon-supervisor/SKILL.md")
    openai_yaml = read("skills/archon-supervisor/agents/openai.yaml")

    assert "Do not use this skill for inner Archon plan/prover/review sessions" in skill
    assert "If the prompt already assigns you to the Archon `plan agent`" in skill
    assert "explicitly asks for the AutoArchon supervisor or teacher role" in skill
    assert "external AutoArchon run" in openai_yaml


def test_supervisor_skill_openai_yaml_exists():
    openai_yaml = read("skills/archon-supervisor/agents/openai.yaml")

    assert "display_name:" in openai_yaml
    assert "short_description:" in openai_yaml
    assert "default_prompt:" in openai_yaml


def test_orchestrator_skill_requires_operator_surfaces_and_journal_updates():
    skill = read("skills/archon-orchestrator/SKILL.md")
    startup = read("skills/archon-orchestrator/references/startup-brief.md")
    surfaces = read("skills/archon-orchestrator/references/operator-surfaces.md")

    assert "references/operator-surfaces.md" in skill
    assert "mission-brief.md" in skill
    assert "operator-journal.md" in skill
    assert "operator-journal.md" in startup
    assert "control/mission-brief.md" in surfaces
    assert "control/operator-journal.md" in surfaces


def test_start_campaign_operator_script_uses_repo_defaults():
    script = read("scripts/start_campaign_operator.sh")

    assert 'MODEL="${MODEL:-gpt-5.4}"' in script
    assert 'REASONING_EFFORT="${REASONING_EFFORT:-xhigh}"' in script
    assert '--config "model_reasoning_effort=${REASONING_EFFORT}"' in script
