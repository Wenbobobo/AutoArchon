from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_plan_prompt_keeps_informal_content_out_of_lean_files():
    plan_prompt = read(".archon-src/prompts/plan.md")

    assert "Write as comments in the corresponding `.lean` file" not in plan_prompt
    assert "block comment above the declaration" not in plan_prompt
    assert ".archon/informal/" in plan_prompt


def test_plan_prompt_keeps_heavy_proof_search_out_of_default_path():
    plan_prompt = read(".archon-src/prompts/plan.md")

    assert "Default to lightweight verification" in plan_prompt
    assert "skip both `lean_diagnostic_messages` and `lake env lean`" in plan_prompt
    assert "bare theorem with one top-level `sorry`" in plan_prompt
    assert "Do not sit and wait on toolchain installs or lock contention during the plan phase." in plan_prompt
    assert "Do not independently re-prove every theorem during planning." in plan_prompt


def test_plan_prompt_fast_paths_fresh_small_bare_subset_batches():
    plan_prompt = read(".archon-src/prompts/plan.md")

    assert "In a small smoke/subset batch run" in plan_prompt
    assert "every target file is still a bare theorem with one top-level `sorry`" in plan_prompt
    assert "skip `lean_diagnostic_messages` and `lake env lean` for that first planning pass" in plan_prompt


def test_plan_prompt_forbids_long_theorem_search_on_first_small_batch_pass():
    plan_prompt = read(".archon-src/prompts/plan.md")

    assert "do not call `lean_local_search`" in plan_prompt
    assert "`lean_leansearch`" in plan_prompt
    assert "`lean_loogle`" in plan_prompt
    assert "`lean_multi_attempt`" in plan_prompt


def test_plan_prompt_ignores_archived_state_outside_live_scope():
    plan_prompt = read(".archon-src/prompts/plan.md")

    assert "Do not treat `.archon/logs/` or archived `task_results-*` directories as live state." in plan_prompt
    assert "Only reuse `.archon/informal/` notes that still match the current `RUN_SCOPE.md`." in plan_prompt


def test_plan_prompt_keeps_false_theorems_frozen_and_routes_to_blockers():
    plan_prompt = read(".archon-src/prompts/plan.md")

    assert "do not ask the prover to change the original theorem statement" in plan_prompt
    assert "Keep the original declaration unchanged" in plan_prompt
    assert "helper theorem" in plan_prompt


def test_agents_contract_exposes_shared_informal_directory():
    agents = read(".archon-src/archon-template/AGENTS.md")

    assert ".archon/informal/" in agents
    assert "| `.archon/informal/` | read + write | read only | read only | read |" in agents


def test_prover_prompt_bounds_shell_verification_and_prefers_lsp():
    prover_prompt = read(".archon-src/prompts/prover-prover.md")

    assert "Lean LSP diagnostics (`lean_diagnostic_messages`) as the primary compile check" in prover_prompt
    assert "timeout 30s lake env lean <file>" in prover_prompt
    assert "do not sit and wait indefinitely" in prover_prompt


def test_prover_prompt_falls_back_after_lsp_timeout_or_start_failure():
    prover_prompt = read(".archon-src/prompts/prover-prover.md")

    assert "If the first Lean MCP call times out or the language server fails to start" in prover_prompt
    assert "treat that as an infrastructure failure, not a proof failure" in prover_prompt
    assert "Do not spend the rest of the session retrying LSP-dependent searches" in prover_prompt


def test_prover_prompt_forbids_fixing_false_theorems_by_mutating_statements():
    prover_prompt = read(".archon-src/prompts/prover-prover.md")

    assert "Do not add assumptions to an existing theorem" in prover_prompt
    assert "leave the original theorem statement unchanged" in prover_prompt
    assert "add separately named helper theorem" in prover_prompt


def test_prover_prompt_forces_immediate_blocker_artifact_after_validated_obstruction():
    prover_prompt = read(".archon-src/prompts/prover-prover.md")

    assert "Lean-validated counterexample" in prover_prompt
    assert "your very next substantive action must be creating a durable blocker artifact" in prover_prompt
    assert "Do not spend the rest of the session on extra theorem search" in prover_prompt


def test_prover_prompt_forces_fast_blocker_artifacts_when_route_is_prevalidated():
    prover_prompt = read(".archon-src/prompts/prover-prover.md")

    assert "blocker candidate" in prover_prompt
    assert "your next substantive action must be producing a durable artifact" in prover_prompt
    assert "Do not spend more than 3 additional theorem-search or `lean_run_code` attempts" in prover_prompt
