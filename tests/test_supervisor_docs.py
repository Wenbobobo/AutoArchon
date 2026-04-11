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


def test_operations_doc_contains_full_supervisor_soak_test_and_monitoring_commands():
    operations = read("docs/operations.md")

    assert "create_run_workspace.py" in operations
    assert "codex exec" in operations
    assert "$archon-supervisor" in operations
    assert "tail -f" in operations
    assert "violations.jsonl" in operations
    assert "export_run_artifacts.py" in operations


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


def test_supervisor_skill_openai_yaml_exists():
    openai_yaml = read("skills/archon-supervisor/agents/openai.yaml")

    assert "display_name:" in openai_yaml
    assert "short_description:" in openai_yaml
    assert "default_prompt:" in openai_yaml
