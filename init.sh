#!/usr/bin/env bash
set -euo pipefail

# ============================================================
#  Archon Init — Per-project Codex setup
# ============================================================

ARCHON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
info()  { echo -e "${CYAN}[ARCHON]${NC}  $*"; }
ok()    { echo -e "${GREEN}[ARCHON]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[ARCHON]${NC}  $*"; }
err()   { echo -e "${RED}[ARCHON]${NC}  $*"; }

OBJECTIVE_LIMIT=""
OBJECTIVE_REGEX=""
SKIP_MCP=false
PROJECT_ARG=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --objective-limit) OBJECTIVE_LIMIT="$2"; shift 2 ;;
        --objective-regex) OBJECTIVE_REGEX="$2"; shift 2 ;;
        --skip-mcp) SKIP_MCP=true; shift ;;
        -h|--help)
            cat <<EOF
Usage: ./init.sh [OPTIONS] [/path/to/lean-project]

Options:
  --objective-limit N     Limit initial objectives to the first N matching files
  --objective-regex REGEX Filter initial objectives by relative path regex
  --skip-mcp              Skip Codex MCP registration
EOF
            exit 0
            ;;
        -*) err "Unknown option: $1"; exit 1 ;;
        *) PROJECT_ARG="$1"; shift ;;
    esac
done

if [[ -n "$PROJECT_ARG" ]]; then
    if [[ ! -d "$PROJECT_ARG" ]]; then
        mkdir -p "$PROJECT_ARG"
        info "Created directory: $PROJECT_ARG"
    fi
    PROJECT_PATH="$(cd "$PROJECT_ARG" && pwd)"
else
    PROJECT_PATH="$(pwd)"
    info "No project path specified — using current directory: ${PROJECT_PATH}"
fi

if [[ "$PROJECT_PATH" == "$ARCHON_DIR" ]]; then
    err "Cannot use the Archon directory as a project."
    exit 1
fi

PROJECT_NAME="$(basename "$PROJECT_PATH")"
STATE_DIR="${PROJECT_PATH}/.archon"
LEAN_LINK="${STATE_DIR}/lean4"
TOOLS_DIR="${STATE_DIR}/tools"
RUNTIME_CONFIG="${STATE_DIR}/runtime-config.toml"
MCP_DIR="${ARCHON_DIR}/.archon-src/tools/lean-lsp-mcp"
LEAN_BUILD_CONCURRENCY="${ARCHON_LEAN_BUILD_CONCURRENCY:-share}"
LEAN_REPL_TIMEOUT="${ARCHON_LEAN_REPL_TIMEOUT:-90}"
LEAN_LOOGLE_LOCAL="${ARCHON_LEAN_LOOGLE_LOCAL:-0}"

info "Archon directory: ${ARCHON_DIR}"
info "Project: ${PROJECT_PATH}"
info "State directory: ${STATE_DIR}"

if ! command -v codex >/dev/null 2>&1; then
    err "Codex CLI is not installed. Run setup.sh first."
    exit 1
fi

mkdir -p \
    "${STATE_DIR}/task_results" \
    "${STATE_DIR}/logs" \
    "${STATE_DIR}/informal" \
    "${STATE_DIR}/informal/helper" \
    "${STATE_DIR}/prompts" \
    "${STATE_DIR}/proof-journal/sessions" \
    "${STATE_DIR}/proof-journal/current_session" \
    "${TOOLS_DIR}"

for f in PROGRESS.md AGENTS.md USER_HINTS.md task_pending.md task_done.md; do
    if [[ ! -f "${STATE_DIR}/${f}" ]]; then
        cp "${ARCHON_DIR}/.archon-src/archon-template/${f}" "${STATE_DIR}/${f}"
    fi
done
ok "State templates copied"

if [[ -d "${PROJECT_PATH}/.git" ]]; then
    GITIGNORE="${PROJECT_PATH}/.gitignore"
    touch "$GITIGNORE"
    if ! grep -qxF '.archon/' "$GITIGNORE"; then
        echo '.archon/' >> "$GITIGNORE"
    fi
fi

for f in "${ARCHON_DIR}"/.archon-src/prompts/*.md; do
    target="${STATE_DIR}/prompts/$(basename "$f")"
    rm -f "$target"
    ln -s "$f" "$target"
done
ok "Prompts linked"

ln -sfn "${ARCHON_DIR}/agents" "${STATE_DIR}/agents"
ln -sfn "${ARCHON_DIR}/.archon-src/tools/informal_agent.py" "${TOOLS_DIR}/archon-informal-agent.py"
ln -sfn "${ARCHON_DIR}/.archon-src/tools/helper_prover_agent.py" "${TOOLS_DIR}/archon-helper-prover-agent.py"
ln -sfn "${ARCHON_DIR}/.archon-src/skills/lean4" "${LEAN_LINK}"
ok "Lean references, canonical agent contracts, and helper tools linked"

if [[ ! -f "${RUNTIME_CONFIG}" ]]; then
    PYTHONPATH="${ARCHON_DIR}" python3 - "${RUNTIME_CONFIG}" <<'PY'
import os
import sys
from pathlib import Path

from archonlib.runtime_config import render_default_runtime_config


provider_defaults = {
    "openai": {
        "api_key_env": "OPENAI_API_KEY",
        "base_url_env": "OPENAI_BASE_URL",
        "model": "gpt-5.4",
    },
    "gemini": {
        "api_key_env": "GEMINI_API_KEY",
        "base_url_env": "GEMINI_BASE_URL",
        "model": "gemini-3.1-pro-preview",
    },
    "openrouter": {
        "api_key_env": "OPENROUTER_API_KEY",
        "base_url_env": "OPENROUTER_BASE_URL",
        "model": "google/gemini-3.1-pro-preview",
    },
}


path = Path(sys.argv[1])
provider = os.environ.get("ARCHON_HELPER_PROVIDER", "").strip() or "openai"
defaults = provider_defaults.get(provider, provider_defaults["openai"])
path.write_text(
    render_default_runtime_config(
        helper_enabled=bool(os.environ.get("ARCHON_HELPER_PROVIDER", "").strip()),
        helper_provider=provider,
        helper_model=os.environ.get("ARCHON_HELPER_MODEL", "").strip() or defaults["model"],
        helper_api_key_env=os.environ.get("ARCHON_HELPER_API_KEY_ENV", "").strip() or defaults["api_key_env"],
        helper_base_url_env=os.environ.get("ARCHON_HELPER_BASE_URL_ENV", "").strip() or defaults["base_url_env"],
        helper_max_retries=int(os.environ.get("ARCHON_HELPER_MAX_RETRIES", "5")),
        helper_initial_backoff_seconds=int(os.environ.get("ARCHON_HELPER_INITIAL_BACKOFF_SECONDS", "5")),
        helper_timeout_seconds=int(os.environ.get("ARCHON_HELPER_TIMEOUT_SECONDS", "300")),
        write_progress_surface=os.environ.get("ARCHON_WRITE_PROGRESS_SURFACE", "1").strip() not in {"0", "false", "False"},
    ),
    encoding="utf-8",
)
PY
    ok "runtime-config.toml initialized"
fi

if [[ "$SKIP_MCP" != true ]]; then
    info "Refreshing archon-lean-lsp MCP via Codex..."
    codex mcp remove archon-lean-lsp >/dev/null 2>&1 || true

    MCP_COMMAND=(uv run --directory "${MCP_DIR}" lean-lsp-mcp --repl --repl-timeout "${LEAN_REPL_TIMEOUT}")
    if [[ "${LEAN_LOOGLE_LOCAL}" == "1" ]]; then
        MCP_COMMAND+=(--loogle-local)
    fi

    codex mcp add \
        archon-lean-lsp \
        --env "LEAN_BUILD_CONCURRENCY=${LEAN_BUILD_CONCURRENCY}" \
        --env "LEAN_LOG_LEVEL=${ARCHON_LEAN_LOG_LEVEL:-WARNING}" \
        -- \
        "${MCP_COMMAND[@]}" >/dev/null
    ok "archon-lean-lsp MCP configured (repl enabled, build concurrency=${LEAN_BUILD_CONCURRENCY})"
fi

PROGRESS_CONTENT="$(
    PYTHONPATH="${ARCHON_DIR}" python3 - <<PY
from pathlib import Path
from archonlib.project_state import build_objectives, detect_stage, has_lean_project, stage_markdown

project = Path(${PROJECT_PATH@Q})
state_dir = Path(${STATE_DIR@Q})
limit_raw = ${OBJECTIVE_LIMIT@Q}
regex_raw = ${OBJECTIVE_REGEX@Q}
limit = int(limit_raw) if limit_raw else None
include_regex = regex_raw or None

if not has_lean_project(project):
    print("NO_LEAN_PROJECT")
    raise SystemExit(0)

stage = detect_stage(project)
autoformalize_skipped = stage != "autoformalize"
objectives = build_objectives(project, stage=stage, limit=limit, include_regex=include_regex)

lines = [
    "# Project Progress",
    "",
    "## Current Stage",
    stage,
    "",
    stage_markdown(stage, autoformalize_skipped=autoformalize_skipped),
    "",
    "## Current Objectives",
    "",
]
if objectives:
    for index, obj in enumerate(objectives, start=1):
        lines.append(obj.to_markdown(index))
else:
    lines.append("1. **No target files selected** — adjust the objective filters or add Lean declarations.")
print("\\n".join(lines) + "\\n")
PY
)"

if [[ "$PROGRESS_CONTENT" == "NO_LEAN_PROJECT" ]]; then
    err "No Lean project detected. This Codex migration currently expects an existing Lean project."
    exit 1
fi

printf '%s' "$PROGRESS_CONTENT" > "${STATE_DIR}/PROGRESS.md"
ok "PROGRESS.md initialized"

TASK_PENDING_CONTENT="$(
    PYTHONPATH="${ARCHON_DIR}" python3 - <<PY
from pathlib import Path
from archonlib.project_state import build_objectives, build_task_pending_markdown, detect_stage

project = Path(${PROJECT_PATH@Q})
limit_raw = ${OBJECTIVE_LIMIT@Q}
regex_raw = ${OBJECTIVE_REGEX@Q}
limit = int(limit_raw) if limit_raw else None
include_regex = regex_raw or None
stage = detect_stage(project)
objectives = build_objectives(project, stage=stage, limit=limit, include_regex=include_regex)
print(build_task_pending_markdown(objectives), end="")
PY
)"

printf '%s' "$TASK_PENDING_CONTENT" > "${STATE_DIR}/task_pending.md"
ok "task_pending.md initialized"

TASK_DONE_CONTENT="$(
    PYTHONPATH="${ARCHON_DIR}" python3 - <<PY
from archonlib.project_state import build_task_done_markdown

print(build_task_done_markdown(), end="")
PY
)"

printf '%s' "$TASK_DONE_CONTENT" > "${STATE_DIR}/task_done.md"
ok "task_done.md initialized"

RUN_SCOPE_CONTENT="$(
    PYTHONPATH="${ARCHON_DIR}" python3 - <<PY
from pathlib import Path
from archonlib.project_state import build_run_scope_markdown, detect_stage

project = Path(${PROJECT_PATH@Q})
limit_raw = ${OBJECTIVE_LIMIT@Q}
regex_raw = ${OBJECTIVE_REGEX@Q}
limit = int(limit_raw) if limit_raw else None
include_regex = regex_raw or None
stage = detect_stage(project)
print(build_run_scope_markdown(project, stage=stage, limit=limit, include_regex=include_regex))
PY
)"

printf '%s\n' "$RUN_SCOPE_CONTENT" > "${STATE_DIR}/RUN_SCOPE.md"
ok "RUN_SCOPE.md initialized"

STAGE="$(awk '/^## Current Stage/{getline; gsub(/^[[:space:]]+|[[:space:]]+$/, ""); print; exit}' "${STATE_DIR}/PROGRESS.md")"
ok "Init complete. Current stage: ${STAGE}"
ok "Next step: ./archon-loop.sh ${PROJECT_PATH}"
