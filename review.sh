#!/usr/bin/env bash
set -euo pipefail

ARCHON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CODEX_MODEL="${ARCHON_CODEX_MODEL:-gpt-5.4}"
CODEX_EXTRA_ARGS="${ARCHON_CODEX_EXEC_ARGS:--config model_reasoning_effort=xhigh}"
CODEX_ENABLE_SEARCH="${ARCHON_CODEX_ENABLE_SEARCH:-0}"
CODEX_TIMEOUT_SECONDS="${ARCHON_REVIEW_TIMEOUT_SECONDS:-${ARCHON_CODEX_TIMEOUT_SECONDS:-}}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
info()  { echo -e "${CYAN}[ARCHON]${NC}  $*"; }
ok()    { echo -e "${GREEN}[ARCHON]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[ARCHON]${NC}  $*"; }
err()   { echo -e "${RED}[ARCHON]${NC}  $*"; }

PROJECT_ARG=""
LOG_FILE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --log) LOG_FILE="$2"; shift 2 ;;
        -h|--help)
            echo "Usage: review.sh [/path/to/lean-project] [--log FILE.jsonl]"
            exit 0
            ;;
        -*) err "Unknown option: $1"; exit 1 ;;
        *) PROJECT_ARG="$1"; shift ;;
    esac
done

if [[ -n "$PROJECT_ARG" ]]; then
    PROJECT_PATH="$(cd "$PROJECT_ARG" && pwd)"
else
    PROJECT_PATH="$(pwd)"
fi

STATE_DIR="${PROJECT_PATH}/.archon"
PROJECT_NAME="$(basename "$PROJECT_PATH")"

if [[ ! -d "$STATE_DIR" ]]; then
    err "No .archon/ found in ${PROJECT_PATH}. Run init.sh first."
    exit 1
fi
if ! command -v codex >/dev/null 2>&1; then
    err "Codex CLI is not installed. Run setup.sh first."
    exit 1
fi

if [[ -z "$LOG_FILE" ]]; then
    LATEST_ITER=$(ls -d "${STATE_DIR}/logs/"iter-* 2>/dev/null | sort -V | tail -1)
    if [[ -n "$LATEST_ITER" && -f "${LATEST_ITER}/provers-combined.jsonl" ]]; then
        LOG_FILE="${LATEST_ITER}/provers-combined.jsonl"
    elif [[ -n "$LATEST_ITER" && -f "${LATEST_ITER}/prover.jsonl" ]]; then
        LOG_FILE="${LATEST_ITER}/prover.jsonl"
    else
        err "No prover log found."
        exit 1
    fi
fi

JOURNAL_DIR="${STATE_DIR}/proof-journal"
SESSIONS_DIR="${JOURNAL_DIR}/sessions"
mkdir -p "$SESSIONS_DIR" "${JOURNAL_DIR}/current_session"

MAX_N=0
for d in "$SESSIONS_DIR"/session_*; do
    [[ -d "$d" ]] || continue
    n="${d##*session_}"
    [[ "$n" =~ ^[0-9]+$ ]] && (( n > MAX_N )) && MAX_N=$n
done
SESSION_NUM=$(( MAX_N + 1 ))

SESSION_DIR="${SESSIONS_DIR}/session_${SESSION_NUM}"
ATTEMPTS_FILE="${JOURNAL_DIR}/current_session/attempts_raw.jsonl"
mkdir -p "$SESSION_DIR"

info "Extracting attempt data..."
uv run --directory "${ARCHON_DIR}" autoarchon-extract-attempts "$LOG_FILE" "$ATTEMPTS_FILE"

export LEAN4_PLUGIN_ROOT="${STATE_DIR}/lean4"
export LEAN4_SCRIPTS="${LEAN4_PLUGIN_ROOT}/lib/scripts"
export LEAN4_REFS="${LEAN4_PLUGIN_ROOT}/skills/lean4/references"
export LEAN4_PYTHON_BIN="${LEAN4_PYTHON_BIN:-$(command -v python3 || command -v python)}"
export ARCHON_INFORMAL_TOOL="${STATE_DIR}/tools/archon-informal-agent.py"

STAGE=$(awk '/^## Current Stage/{getline; gsub(/^[[:space:]]+|[[:space:]]+$/, ""); print; exit}' "${STATE_DIR}/PROGRESS.md" 2>/dev/null || echo "unknown")
REVIEW_PROMPT=$(cat <<EOF
You are the review agent for project '${PROJECT_NAME}'. Current stage: ${STAGE}.
Project directory: ${PROJECT_PATH}
Project state directory: ${STATE_DIR}
Read ${STATE_DIR}/AGENTS.md and ${STATE_DIR}/agents/review-agent.json for your role, then read ${STATE_DIR}/prompts/review.md.
Lean workflow references are vendored under ${STATE_DIR}/lean4/. Consult ${STATE_DIR}/lean4/skills/lean4/SKILL.md only if the review prompt and local session artifacts are insufficient.
Session number: ${SESSION_NUM}.
Pre-processed attempt data: ${ATTEMPTS_FILE} (READ THIS FIRST).
Prover log: ${LOG_FILE}

CRITICAL — Write your output files to EXACTLY these paths:
  ${SESSION_DIR}/milestones.jsonl
  ${SESSION_DIR}/summary.md
  ${SESSION_DIR}/recommendations.md
  ${STATE_DIR}/PROJECT_STATUS.md
EOF
)

SEARCH_FLAG=()
[[ "$CODEX_ENABLE_SEARCH" == "1" ]] && SEARCH_FLAG=(--search)
TIMEOUT_FLAG=()
[[ -n "$CODEX_TIMEOUT_SECONDS" ]] && TIMEOUT_FLAG=(--timeout-seconds "$CODEX_TIMEOUT_SECONDS")
uv run --directory "${ARCHON_DIR}" autoarchon-codex-exec \
    --cwd "${PROJECT_PATH}" \
    --model "${CODEX_MODEL}" \
    --extra-args "${CODEX_EXTRA_ARGS}" \
    "${TIMEOUT_FLAG[@]}" \
    "${SEARCH_FLAG[@]}" <<< "$REVIEW_PROMPT" || true

uv run --directory "${ARCHON_DIR}" autoarchon-validate-review "$SESSION_DIR" "$ATTEMPTS_FILE" || true

ok "Review complete: ${SESSION_DIR}"
