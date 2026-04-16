#!/usr/bin/env bash
set -euo pipefail

trap 'echo ""; err "Interrupted by user."; exit 130' INT

# ============================================================
#  Archon Loop — dual-agent loop for Lean4
#
#  Usage:
#    ./archon-loop.sh [OPTIONS] [/path/to/lean-project]
#
#  If no path given, uses current directory.
#  Project state lives in <project>/.archon/.
#
#  Each iteration = one plan round + one prover round.
#  Plan always runs first to collect results and set objectives.
#
#  Logging: <project>/.archon/logs/archon-*.jsonl
# ============================================================

ARCHON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# -- Defaults --
MAX_ITERATIONS=10
MAX_PARALLEL=4
FORCE_STAGE=""
DRY_RUN=false
PARALLEL=true
VERBOSE_LOGS=false
ENABLE_REVIEW=true
LOG_BASE=""
CODEX_MODEL="${ARCHON_CODEX_MODEL:-gpt-5.4}"
if [[ -n "${ARCHON_CODEX_EXEC_ARGS:-}" ]]; then
    CODEX_EXTRA_ARGS="${ARCHON_CODEX_EXEC_ARGS}"
else
    CODEX_EXTRA_ARGS="--config model_reasoning_effort=xhigh"
fi
CODEX_ENABLE_SEARCH="${ARCHON_CODEX_ENABLE_SEARCH:-0}"
DEFAULT_CODEX_TIMEOUT_SECONDS="${ARCHON_CODEX_TIMEOUT_SECONDS:-}"
PLAN_TIMEOUT_SECONDS="${ARCHON_PLAN_TIMEOUT_SECONDS:-${DEFAULT_CODEX_TIMEOUT_SECONDS}}"
PROVER_TIMEOUT_SECONDS="${ARCHON_PROVER_TIMEOUT_SECONDS:-${DEFAULT_CODEX_TIMEOUT_SECONDS}}"
REVIEW_TIMEOUT_SECONDS="${ARCHON_REVIEW_TIMEOUT_SECONDS:-${DEFAULT_CODEX_TIMEOUT_SECONDS}}"
CODEX_READY_RETRIES="${ARCHON_CODEX_READY_RETRIES:-3}"
CODEX_READY_RETRY_DELAY_SECONDS="${ARCHON_CODEX_READY_RETRY_DELAY_SECONDS:-5}"
SKIP_INITIAL_PLAN="${ARCHON_SKIP_INITIAL_PLAN:-0}"
SKIP_INITIAL_PLAN_REASON="${ARCHON_SKIP_INITIAL_PLAN_REASON:-existing_objectives}"

# -- Color helpers with JSONL logging --
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
_log_jsonl() {
    if [[ -n "${LOG_BASE:-}" ]]; then
        local ts level msg escaped
        ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
        level="$1"
        msg="$2"
        escaped="${msg//\\/\\\\}"
        escaped="${escaped//\"/\\\"}"
        echo "{\"ts\":\"${ts}\",\"event\":\"shell\",\"level\":\"${level}\",\"message\":\"${escaped}\"}" >> "${LOG_BASE}.jsonl"
    fi
    return 0
}
info()  { echo -e "${CYAN}[ARCHON]${NC}  $*"; _log_jsonl "info" "$*"; }
ok()    { echo -e "${GREEN}[ARCHON]${NC}  $*"; _log_jsonl "ok" "$*"; }
warn()  { echo -e "${YELLOW}[ARCHON]${NC}  $*"; _log_jsonl "warn" "$*"; }
err()   { echo -e "${RED}[ARCHON]${NC}  $*"; _log_jsonl "error" "$*"; }

# -- Parse CLI args (options first, then positional project path) --
PROJECT_ARG=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --max-iterations) MAX_ITERATIONS="$2"; shift 2 ;;
        --max-parallel)   MAX_PARALLEL="$2";   shift 2 ;;
        --stage)          FORCE_STAGE="$2";    shift 2 ;;
        --dry-run)        DRY_RUN=true;        shift   ;;
        --serial)         PARALLEL=false;      shift   ;;
        --verbose-logs)   VERBOSE_LOGS=true;   shift   ;;
        --no-review)      ENABLE_REVIEW=false; shift   ;;
        -h|--help)
            echo "Usage: archon-loop.sh [OPTIONS] [/path/to/lean-project]"
            echo ""
            echo "If no path given, uses current directory."
            echo ""
            echo "Options:"
            echo "  --max-iterations N   Max loop iterations (default: 10)"
            echo "  --max-parallel N     Max concurrent provers in parallel mode (default: 4)"
            echo "  --stage STAGE        Override stage (autoformalize|prover|polish)"
            echo "  --serial             Use a single prover (default: parallel, one per sorry-file)"
            echo "  --verbose-logs       Also save raw Codex JSON events to .raw.jsonl"
            echo "  --no-review          Skip review phase after prover"
            echo "  --dry-run            Print prompts without launching Codex"
            echo "  -h, --help           Show this help"
            echo ""
            echo "User interaction (while the loop runs):"
            echo "  Edit .archon/USER_HINTS.md in your project"
            echo "  Add /- USER: ... -/ comments in .lean files"
            exit 0
            ;;
        -*) err "Unknown option: $1"; exit 1 ;;
        *)  PROJECT_ARG="$1"; shift ;;
    esac
done

# -- Resolve project path --
BOLD='\033[1m'
if [[ -n "$PROJECT_ARG" ]]; then
    PROJECT_PATH="$(cd "$PROJECT_ARG" 2>/dev/null && pwd)" || { err "Directory not found: $PROJECT_ARG"; exit 1; }
    info "Using specified project path: ${PROJECT_PATH}"
else
    PROJECT_PATH="$(pwd)"
    echo ""
    info "${BOLD}No project path specified — using current directory:${NC}"
    info "  ${PROJECT_PATH}"
    info ""
    info "To run on a project elsewhere, use:"
    info "  ${CYAN}./archon-loop.sh /path/to/your-lean-project${NC}"
    echo ""
fi

if [[ "$PROJECT_PATH" == "$ARCHON_DIR" ]]; then
    err "Cannot use the Archon directory as a project."
    err "Usage: ./archon-loop.sh /path/to/your-lean-project"
    exit 1
fi

if [[ "$DRY_RUN" == true && "$MAX_ITERATIONS" != "1" ]]; then
    warn "Dry run is single-shot; overriding max iterations to 1."
    MAX_ITERATIONS=1
fi

PROJECT_NAME="$(basename "$PROJECT_PATH")"
STATE_DIR="${PROJECT_PATH}/.archon"
PROGRESS_FILE="${STATE_DIR}/PROGRESS.md"
RUN_SCOPE_FILE="${STATE_DIR}/RUN_SCOPE.md"
LOG_DIR="${STATE_DIR}/logs"

# ============================================================
#  Helper functions
# ============================================================

read_stage() {
    if [[ -n "$FORCE_STAGE" ]]; then
        echo "$FORCE_STAGE"
        return
    fi
    if [[ ! -f "$PROGRESS_FILE" ]]; then
        echo -e "${RED}[ARCHON]${NC}  PROGRESS.md not found at $PROGRESS_FILE" >&2
        echo -e "${RED}[ARCHON]${NC}  Run ./init.sh ${PROJECT_PATH} first." >&2
        exit 1
    fi
    local stage
    stage=$(awk '/^## Current Stage/{getline; gsub(/^[[:space:]]+|[[:space:]]+$/, ""); print; exit}' "$PROGRESS_FILE")
    if [[ -z "$stage" ]]; then
        err "Could not read current stage from PROGRESS.md"
        exit 1
    fi
    echo "$stage"
}

is_complete() {
    [[ -f "$PROGRESS_FILE" ]] || return 1
    local stage
    stage=$(read_stage)
    [[ "$stage" == "COMPLETE" ]]
}

build_prompt() {
    local agent="$1"
    local stage="$2"
    if [[ "$agent" == "plan" ]]; then
        cat <<EOF
You are the plan agent for project '${PROJECT_NAME}'. Current stage: ${stage}.
Project directory: ${PROJECT_PATH}
Project state directory: ${STATE_DIR}
Start with ${STATE_DIR}/PROGRESS.md and ${STATE_DIR}/RUN_SCOPE.md to recover the active scoped objectives.
Read ${STATE_DIR}/prompts/plan.md next.
Only consult ${STATE_DIR}/AGENTS.md and ${STATE_DIR}/agents/plan-agent.json if you need to confirm permissions, outputs, or role details that are not already clear from the stage prompt and local state.
Treat ${STATE_DIR}/RUN_SCOPE.md as a hard constraint: do not schedule files outside its allowed scope.
Lean workflow references are vendored under ${STATE_DIR}/lean4/. Consult ${STATE_DIR}/lean4/skills/lean4/SKILL.md only if local project state and the stage prompt are insufficient; do not spend time reading the full Lean reference corpus up front.
All state files (PROGRESS.md, task_pending.md, task_done.md, USER_HINTS.md, task_results/) are in ${STATE_DIR}/.
The .lean files are in ${PROJECT_PATH}/.
Optional helper tool: ${STATE_DIR}/tools/archon-helper-prover-agent.py
Runtime config: ${STATE_DIR}/runtime-config.toml
If helper is enabled in the runtime config, use the helper tool according to its plan policy; otherwise fall back to ${STATE_DIR}/tools/archon-informal-agent.py.
EOF
    else
        cat <<EOF
You are the prover agent for project '${PROJECT_NAME}'. Current stage: ${stage}.
Project directory: ${PROJECT_PATH}
Project state directory: ${STATE_DIR}
Start with ${STATE_DIR}/PROGRESS.md, ${STATE_DIR}/RUN_SCOPE.md, and your assigned file to recover the exact scoped objective.
Read ${STATE_DIR}/prompts/prover-${stage}.md next.
Only consult ${STATE_DIR}/AGENTS.md and ${STATE_DIR}/agents/prover-agent.json if you need to confirm permissions, outputs, or role details that are not already clear from the stage prompt and local state.
Treat ${STATE_DIR}/RUN_SCOPE.md as a hard constraint: only edit files inside its allowed scope.
Lean workflow references are vendored under ${STATE_DIR}/lean4/. Consult ${STATE_DIR}/lean4/skills/lean4/SKILL.md only when the stage prompt, local file, and task state do not already give a concrete next step.
All state files are in ${STATE_DIR}/. The .lean files are in ${PROJECT_PATH}/.
Optional helper tool: ${STATE_DIR}/tools/archon-helper-prover-agent.py
Runtime config: ${STATE_DIR}/runtime-config.toml
If helper is enabled in the runtime config, use the helper tool according to its prover policy; otherwise fall back to ${STATE_DIR}/tools/archon-informal-agent.py.
EOF
    fi
}

relpath() {
    python3 -c "import os,sys; print(os.path.relpath(sys.argv[1], sys.argv[2]))" "$1" "$2" 2>/dev/null \
        || echo "$1"
}

parse_objective_files() {
    local allowed_rel
    allowed_rel="$(awk '
        /^## Allowed Files/ { found=1; next }
        found && /^## /      { exit }
        found                { print }
    ' "$RUN_SCOPE_FILE" 2>/dev/null \
        | grep -oE '`[^`]+\.lean`' \
        | tr -d '`' \
        | sort -u)"

    awk '
        /^## Current Objectives/            { found=1; next }
        found && /^## /                     { exit }
        found && /^[[:space:]]*[0-9]+\.[[:space:]]+/ { print }
    ' "$PROGRESS_FILE" \
        | grep -oE '(\*\*|`)[^*`]+\.lean(\*\*|`)' \
        | sed 's/\*\*//g; s/`//g' \
        | while IFS= read -r f; do
            local found
            if [[ -n "$allowed_rel" ]] && ! grep -qxF "$f" <<< "$allowed_rel"; then
                continue
            fi
            found=$(find "${PROJECT_PATH}" -path "*/$f" -not -path '*/.archon/*' -not -path '*/.lake/*' -not -path '*/lake-packages/*' 2>/dev/null | head -1)
            [[ -n "$found" ]] && echo "$found"
        done \
        | sort -u
}

has_live_task_results() {
    local results_dir="${STATE_DIR}/task_results"
    [[ -d "$results_dir" ]] || return 1
    find "$results_dir" -maxdepth 1 -type f -name '*.md' -print -quit 2>/dev/null | grep -q .
}

can_fallback_to_existing_objectives() {
    [[ "$STAGE" == "prover" || "$STAGE" == "polish" ]] || return 1
    has_live_task_results && return 1

    local objective_files
    objective_files="$(parse_objective_files)"
    [[ -n "$objective_files" ]]
}

planning_surface_changed_since_backup() {
    local backup_path="$1"
    local live_path="$2"
    [[ -n "$backup_path" && -f "$backup_path" && -f "$live_path" ]] || return 1
    ! cmp -s "$backup_path" "$live_path"
}

can_fallback_to_recovered_autoformalize_objective() {
    [[ "$STAGE" == "autoformalize" ]] || return 1
    has_live_task_results && return 1
    [[ "${PLAN_SURFACE_RECOVERED:-0}" == "1" ]] || return 1

    local objective_files
    objective_files="$(parse_objective_files)"
    [[ -n "$objective_files" ]]
}

check_codex_ready() {
    local max_attempts=$(( CODEX_READY_RETRIES + 1 ))
    local attempt=1
    local output=""
    local output_tail=""

    while (( attempt <= max_attempts )); do
        if output=$(codex exec --json --skip-git-repo-check --sandbox danger-full-access -c approval_policy=never --model "${CODEX_MODEL}" "Reply with exactly OK." 2>&1); then
            if (( attempt > 1 )); then
                ok "Codex readiness recovered on attempt ${attempt}/${max_attempts}"
            fi
            return 0
        fi

        output_tail=$(printf '%s\n' "$output" | tail -n 5 | tr '\n' ' ' | sed 's/  */ /g; s/^ //; s/ $//')
        if (( attempt < max_attempts )); then
            warn "Codex readiness check failed (attempt ${attempt}/${max_attempts}). Retrying in ${CODEX_READY_RETRY_DELAY_SECONDS}s."
            [[ -n "$output_tail" ]] && warn "  Last output: ${output_tail}"
            sleep "${CODEX_READY_RETRY_DELAY_SECONDS}"
        else
            err "Codex cannot run after ${max_attempts} attempt(s). Check: codex login/config, model access, network."
            [[ -n "$output_tail" ]] && err "Last output: ${output_tail}"
            return 1
        fi
        (( attempt++ ))
    done
}

# ============================================================
#  Run codex exec with normalized JSONL logging
# ============================================================

prepare_agent_env() {
    export LEAN4_PLUGIN_ROOT="${STATE_DIR}/lean4"
    export LEAN4_SCRIPTS="${LEAN4_PLUGIN_ROOT}/lib/scripts"
    export LEAN4_REFS="${LEAN4_PLUGIN_ROOT}/skills/lean4/references"
    export LEAN4_PYTHON_BIN="${LEAN4_PYTHON_BIN:-$(command -v python3 || command -v python)}"
    export ARCHON_RUNTIME_CONFIG="${STATE_DIR}/runtime-config.toml"
    export ARCHON_HELPER_CONFIG="${STATE_DIR}/runtime-config.toml"
    export ARCHON_HELPER_TOOL="${STATE_DIR}/tools/archon-helper-prover-agent.py"
    export ARCHON_INFORMAL_TOOL="${STATE_DIR}/tools/archon-informal-agent.py"
}

run_codex() {
    local prompt="$1"
    shift
    local log_base="${LOG_BASE:-}"
    local jsonl=""
    local raw_log=""
    local search_flag=()
    local timeout_flag=()
    prepare_agent_env
    [[ "$CODEX_ENABLE_SEARCH" == "1" ]] && search_flag=(--search)
    [[ -n "${CODEX_TIMEOUT_SECONDS:-}" ]] && timeout_flag=(--timeout-seconds "${CODEX_TIMEOUT_SECONDS}")

    if [[ -n "$log_base" ]]; then
        jsonl="${log_base}.jsonl"
        raw_log="${log_base}.raw.jsonl"
        if [[ "${VERBOSE_LOGS:-false}" == "true" ]]; then
            uv run --directory "${ARCHON_DIR}" autoarchon-codex-exec \
                --cwd "${PROJECT_PATH}" \
                --model "${CODEX_MODEL}" \
                --log-path "${jsonl}" \
                --raw-log-path "${raw_log}" \
                --extra-args "${CODEX_EXTRA_ARGS}" \
                "${timeout_flag[@]}" \
                "${search_flag[@]}" <<< "$prompt"
        else
            uv run --directory "${ARCHON_DIR}" autoarchon-codex-exec \
                --cwd "${PROJECT_PATH}" \
                --model "${CODEX_MODEL}" \
                --log-path "${jsonl}" \
                --extra-args "${CODEX_EXTRA_ARGS}" \
                "${timeout_flag[@]}" \
                "${search_flag[@]}" <<< "$prompt"
        fi
        return $?
    else
        uv run --directory "${ARCHON_DIR}" autoarchon-codex-exec \
            --cwd "${PROJECT_PATH}" \
            --model "${CODEX_MODEL}" \
            --extra-args "${CODEX_EXTRA_ARGS}" \
            "${timeout_flag[@]}" \
            "${search_flag[@]}" <<< "$prompt"
    fi
}

# ============================================================
#  Iteration directory helpers
# ============================================================

next_iter_num() {
    local max_n=0
    if [[ -d "$LOG_DIR" ]]; then
        for d in "$LOG_DIR"/iter-*; do
            [[ -d "$d" ]] || continue
            local n="${d##*iter-}"
            n="${n#"${n%%[!0]*}"}"  # strip leading zeros
            [[ "$n" =~ ^[0-9]+$ ]] && (( n > max_n )) && max_n=$n
        done
    fi
    echo $(( max_n + 1 ))
}

write_meta() {
    local meta_file="$1"
    shift
    # Accepts key=value pairs, writes/updates JSON via python
    python3 -c "
import json, sys, os
path = '$meta_file'
data = {}
if os.path.exists(path):
    with open(path) as f:
        try: data = json.load(f)
        except: pass
for arg in sys.argv[1:]:
    k, v = arg.split('=', 1)
    # Parse nested keys like provers.File.status
    keys = k.split('.')
    d = data
    for part in keys[:-1]:
        if part not in d or not isinstance(d[part], dict):
            d[part] = {}
        d = d[part]
    # Try to parse as JSON value (number, bool, null, list, dict)
    try:
        d[keys[-1]] = json.loads(v)
    except (json.JSONDecodeError, ValueError):
        d[keys[-1]] = v
with open(path, 'w') as f:
    json.dump(data, f, indent=2)
" "$@" 2>/dev/null || true
}

# ============================================================
#  Cost summary helpers
# ============================================================

show_cost_summary() {
    local label="$1"
    local iter_dir="${2:-}"
    [[ -n "$iter_dir" && -d "$iter_dir" ]] || return 0
    python3 -c "
import sys, json, os, glob
rows = []
for jsonl in glob.glob(os.path.join('$iter_dir', '**', '*.jsonl'), recursive=True):
    for l in open(jsonl):
        l = l.strip()
        if not l: continue
        try:
            r = json.loads(l)
            if r.get('event') == 'session_end': rows.append(r)
        except: pass
if not rows: sys.exit(0)
cost  = sum(r.get('total_cost_usd', 0) or 0 for r in rows)
dur   = sum(r.get('duration_ms', 0) or 0 for r in rows)
tin   = sum(r.get('input_tokens', 0) or 0 for r in rows)
tout  = sum(r.get('output_tokens', 0) or 0 for r in rows)
turns = sum(r.get('num_turns', 0) or 0 for r in rows)
models = {}
for r in rows:
    for m, u in (r.get('model_usage') or {}).items():
        if m not in models:
            models[m] = {'in': 0, 'out': 0, 'cost': 0.0}
        models[m]['in']   += u.get('inputTokens', 0) or 0
        models[m]['out']  += u.get('outputTokens', 0) or 0
        models[m]['cost'] += u.get('costUSD', 0) or 0
parts = []
if dur:   parts.append(f'{dur/60000:.1f}min')
if cost:  parts.append(f'\${cost:.4f}')
if tin or tout: parts.append(f'in={tin} out={tout}')
if turns: parts.append(f'turns={turns}')
print('$label ' + ' | '.join(parts))
for m, u in models.items():
    print(f'  {m}: in={u[\"in\"]} out={u[\"out\"]} \${u[\"cost\"]:.4f}')
" 2>/dev/null || true
}

# ============================================================
#  Parallel prover iteration
# ============================================================

run_parallel_provers() {
    local stage="$1"

    # Archive old results
    local results_dir="${STATE_DIR}/task_results"
    if ls "${results_dir}/"*.md &>/dev/null; then
        local archive="${LOG_DIR}/task_results-$(date +%Y%m%d-%H%M%S)"
        mkdir -p "$archive"
        mv "${results_dir}/"*.md "$archive/"
        info "Archived previous task_results/"
    fi

    local sorry_files
    sorry_files=$(parse_objective_files)

    if [[ -z "$sorry_files" ]]; then
        warn "No files parsed from PROGRESS.md ## Current Objectives."
        warn "The plan agent must list live target files on numbered objective lines in **bold** or \`backticks\` (e.g. \`1. **Foo/Bar.lean** — ...\`)."
        warn "Skipping this prover iteration."
        return 0
    fi

    local file_count
    file_count=$(echo "$sorry_files" | wc -l | tr -d ' ')

    if [[ "$file_count" -eq 1 ]]; then
        local rel
        rel=$(relpath "$(echo "$sorry_files" | head -1)" "$PROJECT_PATH")
        info "Only 1 file (${rel}) — running serial prover"

        if [[ "$DRY_RUN" == true ]]; then
            echo "=== Prover: ${rel} ==="
            return 0
        fi

        # -- Snapshot: baseline + env vars for single-file serial prover --
        local file_slug
        file_slug=$(echo "$rel" | sed 's|/|_|g; s|\.lean$||')
        local result_file
        result_file="$(echo "$rel" | sed 's|/|_|g').md"
        local prover_log="${ITER_DIR}/provers/${file_slug}"
        LOG_BASE="$prover_log"

        write_meta "$ITER_META" "provers.${file_slug}.file=${rel}" "provers.${file_slug}.status=running"

        local snap_dir="${ITER_DIR}/snapshots/${file_slug}"
        mkdir -p "$snap_dir"
        cp "$(echo "$sorry_files" | head -1)" "${snap_dir}/baseline.lean" 2>/dev/null || true

        export ARCHON_SNAPSHOT_DIR="$snap_dir"
        export ARCHON_PROVER_JSONL="${prover_log}.jsonl"
        export ARCHON_PROJECT_PATH="$PROJECT_PATH"

        local prover_prompt
        prover_prompt="$(build_prompt "prover" "$stage")"$'\n'"Your assigned file: ${rel}"$'\n'"Write your task result to exactly: ${STATE_DIR}/task_results/${result_file}"

	        if CODEX_TIMEOUT_SECONDS="${PROVER_TIMEOUT_SECONDS}" run_codex "$prover_prompt"; then
            write_meta "$ITER_META" "provers.${file_slug}.status=done"
        else
            write_meta "$ITER_META" "provers.${file_slug}.status=error"
        fi

        unset ARCHON_SNAPSHOT_DIR ARCHON_PROVER_JSONL ARCHON_PROJECT_PATH
        return 0
    fi

    info "Found ${file_count} file(s) — launching parallel provers (background processes)"

    local prover_prompt_base
    prover_prompt_base=$(cat <<EOF
You are a prover agent for project '${PROJECT_NAME}'. Current stage: ${stage}.
Project directory: ${PROJECT_PATH}
Project state directory: ${STATE_DIR}
Read ${STATE_DIR}/AGENTS.md for your role, then read ${STATE_DIR}/prompts/prover-${stage}.md and ${STATE_DIR}/PROGRESS.md.
Lean workflow references are vendored under ${STATE_DIR}/lean4/. Consult ${STATE_DIR}/lean4/skills/lean4/SKILL.md only when the current prompt and file context do not already give a concrete next step.
Check your .lean file for /- USER: ... -/ comments for file-specific hints.

IMPORTANT:
- You own ONLY the file assigned below. Do NOT edit any other .lean file.
- Write your results to ${STATE_DIR}/task_results/<your_file>.md when done.
- Do NOT edit PROGRESS.md, task_pending.md, or task_done.md.
- Missing Mathlib infrastructure is NEVER a valid reason to leave a sorry.
- NEVER revert to a bare sorry. Always leave your partial proof attempt in the code.
EOF
    )

    if [[ "$DRY_RUN" == true ]]; then
        while IFS= read -r f; do
            local rel
            rel=$(relpath "$f" "$PROJECT_PATH")
            echo "=== Prover: ${rel} ==="
        done <<< "$sorry_files"
        return 0
    fi

    info ""
    info "Watch progress:"
    info "  tail -f ${ITER_DIR}/provers/*.jsonl"
    info "  watch -n10 'ls -lt ${STATE_DIR}/task_results/'"
    info ""

    # Launch provers as background processes, respecting MAX_PARALLEL
    local pids=()
    local prover_files=()
    local running=0
    while IFS= read -r f; do
        local rel
        rel=$(relpath "$f" "$PROJECT_PATH")
        local result_file
        result_file="$(echo "$rel" | sed 's|/|_|g').md"
        local prover_prompt="${prover_prompt_base}"$'\n'"Your assigned file: ${rel}"$'\n'"Write your task result to exactly: ${STATE_DIR}/task_results/${result_file}"
        local file_slug
        file_slug=$(echo "$rel" | sed 's|/|_|g; s|\.lean$||')
        local prover_log="${ITER_DIR}/provers/${file_slug}"

        # Wait for a slot if at capacity
        while (( running >= MAX_PARALLEL )); do
            # Wait for any child to finish, then recount
            wait -n 2>/dev/null || true
            running=0
            for pid in "${pids[@]}"; do
                kill -0 "$pid" 2>/dev/null && (( running++ )) || true
            done
        done

        info "  Starting prover for ${rel} (log: provers/${file_slug}.jsonl)"

        write_meta "$ITER_META" "provers.${file_slug}.file=${rel}" "provers.${file_slug}.status=running"

        # -- Snapshot: baseline + env vars for this prover --
        local snap_dir="${ITER_DIR}/snapshots/${file_slug}"
        mkdir -p "$snap_dir"
        cp "$f" "${snap_dir}/baseline.lean" 2>/dev/null || true

        # Run each prover in a subshell with its own LOG_BASE + snapshot env
        (
            LOG_BASE="$prover_log"
            export ARCHON_SNAPSHOT_DIR="$snap_dir"
            export ARCHON_PROVER_JSONL="${prover_log}.jsonl"
            export ARCHON_PROJECT_PATH="$PROJECT_PATH"
	            CODEX_TIMEOUT_SECONDS="${PROVER_TIMEOUT_SECONDS}" run_codex "$prover_prompt" || true
        ) &
        pids+=($!)
        prover_files+=("$rel")
        (( running++ )) || true
    done <<< "$sorry_files"

    info "Launched ${#pids[@]} prover process(es) (max ${MAX_PARALLEL} concurrent). Waiting for all to finish..."

    # Wait for all provers and report results
    local failed=0
    for idx in "${!pids[@]}"; do
        local pid="${pids[$idx]}"
        local pfile="${prover_files[$idx]}"
        local file_slug
        file_slug=$(echo "$pfile" | sed 's|/|_|g; s|\.lean$||')
        if wait "$pid"; then
            info "  Prover for ${pfile} finished (pid ${pid})"
            write_meta "$ITER_META" "provers.${file_slug}.status=done"
        else
            warn "  Prover for ${pfile} exited with error (pid ${pid})"
            write_meta "$ITER_META" "provers.${file_slug}.status=error"
            (( failed++ )) || true
        fi
    done

    if [[ "$failed" -gt 0 ]]; then
        warn "${failed}/${#pids[@]} prover(s) had errors"
    else
        ok "All ${#pids[@]} prover(s) finished successfully"
    fi

    # Collect results: update task tracking files
    local results_dir="${STATE_DIR}/task_results"
    local result_count
    result_count=$(ls "${results_dir}/"*.md 2>/dev/null | wc -l | tr -d ' ')
    info "Found ${result_count}/${file_count} task result file(s) in task_results/"

    # Emit parallel round note
    if [[ -n "${LOG_BASE:-}" ]]; then
        python3 -c "
import json, datetime
row = {'ts': datetime.datetime.now().isoformat(), 'event': 'parallel_round_end', 'prover_count': ${file_count}, 'failed': ${failed}}
with open('${LOG_BASE}.jsonl', 'a') as f:
    f.write(json.dumps(row) + '\n')
" 2>/dev/null || true
    fi
}

# ============================================================
#  Review phase
# ============================================================

next_session_num() {
    local journal_dir="${STATE_DIR}/proof-journal/sessions"
    local max_n=0
    if [[ -d "$journal_dir" ]]; then
        for d in "$journal_dir"/session_*; do
            [[ -d "$d" ]] || continue
            local n="${d##*session_}"
            [[ "$n" =~ ^[0-9]+$ ]] && (( n > max_n )) && max_n=$n
        done
    fi
    echo $(( max_n + 1 ))
}

run_review_phase() {
    local stage="$1"

    local session_num
    session_num=$(next_session_num)
    local journal_dir="${STATE_DIR}/proof-journal"
    local session_dir="${journal_dir}/sessions/session_${session_num}"
    local current_session_dir="${journal_dir}/current_session"
    local attempts_file="${current_session_dir}/attempts_raw.jsonl"

    mkdir -p "$session_dir" "$current_session_dir"

    # Phase 3a: Extract attempt data from prover log (deterministic, no LLM)
    info "Extracting attempt data from prover logs..."

    # Concatenate all prover logs from this iteration for the extract script
    local combined_prover_log="${ITER_DIR}/prover.jsonl"
    if [[ -d "${ITER_DIR}/provers" ]] && ls "${ITER_DIR}/provers/"*.jsonl &>/dev/null; then
        combined_prover_log="${ITER_DIR}/provers-combined.jsonl"
        cat "${ITER_DIR}/provers/"*.jsonl > "$combined_prover_log" 2>/dev/null || true
    fi

    uv run --directory "${ARCHON_DIR}" autoarchon-extract-attempts \
        "$combined_prover_log" "$attempts_file" 2>&1 || true

    # Phase 3b: Run review agent
    local review_prompt
    review_prompt=$(cat <<EOF
You are the review agent for project '${PROJECT_NAME}'. Current stage: ${stage}.
Project directory: ${PROJECT_PATH}
Project state directory: ${STATE_DIR}
Read ${STATE_DIR}/AGENTS.md and ${STATE_DIR}/agents/review-agent.json for your role, then read ${STATE_DIR}/prompts/review.md.
Lean workflow references are vendored under ${STATE_DIR}/lean4/. Consult ${STATE_DIR}/lean4/skills/lean4/SKILL.md only if the review prompt and local session artifacts are insufficient.
Session number: ${session_num}.
Pre-processed attempt data: ${attempts_file} (READ THIS FIRST).
Prover log: ${combined_prover_log}

CRITICAL — Write your output files to EXACTLY these paths:
  ${session_dir}/milestones.jsonl
  ${session_dir}/summary.md
  ${session_dir}/recommendations.md
  ${STATE_DIR}/PROJECT_STATUS.md
EOF
    )

    LOG_BASE="${ITER_DIR}/review"
    CODEX_TIMEOUT_SECONDS="${REVIEW_TIMEOUT_SECONDS}" run_codex "$review_prompt" || true

    # Phase 3c: Validate review output
    info "Validating review output..."
    uv run --directory "${ARCHON_DIR}" autoarchon-validate-review \
        "$session_dir" "$attempts_file" 2>&1 || true
}

# ============================================================
#  Main
# ============================================================

# -- Pre-flight --
if [[ "$DRY_RUN" != true ]]; then
    if ! command -v codex &>/dev/null; then
        err "Codex CLI is not installed. Run setup.sh first."
        exit 1
    fi
    if ! check_codex_ready; then
        exit 1
    fi
    ok "Codex is authenticated and ready"
fi

# -- Check project state exists --
if [[ ! -f "$PROGRESS_FILE" ]]; then
    err "No project state found for '${PROJECT_NAME}'."
    err "Run: ./init.sh ${PROJECT_PATH}"
    exit 1
fi

STAGE=$(read_stage)
if [[ "$STAGE" == "init" ]]; then
    err "Project '${PROJECT_NAME}' is still in init stage."
    err "Run: ./init.sh ${PROJECT_PATH}"
    exit 1
fi

# -- Logging setup --
if [[ "$DRY_RUN" != true ]]; then
    mkdir -p "$LOG_DIR" "${STATE_DIR}/task_results" \
             "${STATE_DIR}/proof-journal/sessions" "${STATE_DIR}/proof-journal/current_session"
fi

info "Archon Loop starting"
info "Project: ${PROJECT_PATH}"
info "State: ${STATE_DIR}"
	info "Max iterations: ${MAX_ITERATIONS}"
	[[ -n "$FORCE_STAGE" ]] && info "Forced stage: ${FORCE_STAGE}"
	[[ "$PARALLEL" == true ]] && info "Prover mode: parallel (max ${MAX_PARALLEL} concurrent)"
	[[ "$PARALLEL" != true ]] && info "Prover mode: serial"
	[[ "$ENABLE_REVIEW" == true ]] && info "Review: enabled"
	[[ "$ENABLE_REVIEW" != true ]] && info "Review: disabled (--no-review)"
	[[ -n "$PLAN_TIMEOUT_SECONDS" ]] && info "Plan timeout: ${PLAN_TIMEOUT_SECONDS}s"
	[[ -n "$PROVER_TIMEOUT_SECONDS" ]] && info "Prover timeout: ${PROVER_TIMEOUT_SECONDS}s"
	[[ -n "$REVIEW_TIMEOUT_SECONDS" ]] && info "Review timeout: ${REVIEW_TIMEOUT_SECONDS}s"
	[[ "$DRY_RUN" == true ]] && warn "DRY RUN mode"
	info "Logs: ${LOG_DIR}/"
info ""
info "User hints: ${STATE_DIR}/USER_HINTS.md"
info "Or add /- USER: ... -/ comments in .lean files"
info ""
info "Dashboard: bash ${ARCHON_DIR}/ui/start.sh --project ${PROJECT_PATH}"
echo ""

# -- COMPLETE check --
if is_complete; then
    ok "Project '${PROJECT_NAME}' is COMPLETE. Nothing to do."
    exit 0
fi

# ============================================================
#  Automated loop: plan → prover → plan → prover → ...
# ============================================================

STAGE=$(read_stage)
info "Stage: ${STAGE} — Starting automated loop"
echo ""

LOOP_START=$SECONDS

for (( i=0; i<MAX_ITERATIONS; i++ )); do
    STAGE=$(read_stage)

    if is_complete; then
        ok "PROGRESS.md says COMPLETE. Exiting loop."
        break
    fi

    info "════════════════════════════════════════"
    info "Iteration $((i+1))/${MAX_ITERATIONS}  |  Stage: ${STAGE}  |  Project: ${PROJECT_NAME}"
    info "════════════════════════════════════════"

    ITER_START=$SECONDS

    # -- Set up iteration directory --
    if [[ "$DRY_RUN" != true ]]; then
        ITER_NUM=$(next_iter_num)
        ITER_DIR="${LOG_DIR}/iter-$(printf '%03d' "$ITER_NUM")"
        ITER_META="${ITER_DIR}/meta.json"
        mkdir -p "${ITER_DIR}"
        [[ "$PARALLEL" == true ]] && mkdir -p "${ITER_DIR}/provers"
        LOG_BASE="${ITER_DIR}/plan"
        write_meta "$ITER_META" \
            "iteration=${ITER_NUM}" \
            "stage=${STAGE}" \
            "mode=$( [[ "$PARALLEL" == true ]] && echo parallel || echo serial )" \
            "startedAt=$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
            "plan.status=running"
        info "Log dir: ${ITER_DIR}"
    fi

	    # --- Plan phase ---
	    info "Phase 1: Plan agent"
	    info "────────────────────────────────────────"

	    PLAN_START=$SECONDS
	    PLAN_PROMPT=$(build_prompt "plan" "$STAGE")
	    RUN_SCOPE_BACKUP=""
        PROGRESS_BACKUP=""
        TASK_PENDING_BACKUP=""
        PLAN_SURFACE_RECOVERED=0
	    if [[ "$DRY_RUN" != true && -f "$RUN_SCOPE_FILE" ]]; then
	        RUN_SCOPE_BACKUP="${ITER_DIR}/run_scope.before-plan.md"
	        cp "$RUN_SCOPE_FILE" "$RUN_SCOPE_BACKUP"
	    fi
        if [[ "$DRY_RUN" != true && -f "$PROGRESS_FILE" ]]; then
            PROGRESS_BACKUP="${ITER_DIR}/progress.before-plan.md"
            cp "$PROGRESS_FILE" "$PROGRESS_BACKUP"
        fi
        if [[ "$DRY_RUN" != true && -f "${STATE_DIR}/task_pending.md" ]]; then
            TASK_PENDING_BACKUP="${ITER_DIR}/task_pending.before-plan.md"
            cp "${STATE_DIR}/task_pending.md" "$TASK_PENDING_BACKUP"
        fi
	    if [[ "$DRY_RUN" == true ]]; then
	        echo "$PLAN_PROMPT"
	        PLAN_STATUS="done"
	    elif [[ "$SKIP_INITIAL_PLAN" == "1" && "$i" -eq 0 ]] && can_fallback_to_existing_objectives; then
	        warn "Skipping initial plan phase (${SKIP_INITIAL_PLAN_REASON}) and reusing existing objectives."
	        PLAN_STATUS="skipped_fast_path"
	    else
	        if CODEX_TIMEOUT_SECONDS="${PLAN_TIMEOUT_SECONDS}" run_codex "$PLAN_PROMPT"; then
            PLAN_STATUS="done"
        else
            PLAN_STATUS="error"
            warn "Plan agent exited with an error. Check ${ITER_DIR}/plan.jsonl for details."
	        fi
	    fi
	    if [[ "$DRY_RUN" != true && -n "$RUN_SCOPE_BACKUP" ]]; then
	        if [[ ! -f "$RUN_SCOPE_FILE" ]] || ! cmp -s "$RUN_SCOPE_BACKUP" "$RUN_SCOPE_FILE"; then
	            warn "RUN_SCOPE.md changed during plan phase. Preserving the newer file and skipping prover to avoid clobbering scope changes."
	            if [[ "$PLAN_STATUS" == "done" ]]; then
	                PLAN_STATUS="scope_changed"
	            fi
	        fi
	    fi
        if [[ "$DRY_RUN" != true ]]; then
            if planning_surface_changed_since_backup "$PROGRESS_BACKUP" "$PROGRESS_FILE"; then
                PLAN_SURFACE_RECOVERED=1
            fi
            if planning_surface_changed_since_backup "$TASK_PENDING_BACKUP" "${STATE_DIR}/task_pending.md"; then
                PLAN_SURFACE_RECOVERED=1
            fi
        fi

	    PLAN_SECS=$(( SECONDS - PLAN_START ))
	    info "Plan phase finished. (${PLAN_SECS}s)"
	    [[ "$DRY_RUN" != true ]] && write_meta "$ITER_META" "plan.status=${PLAN_STATUS}" "plan.durationSecs=${PLAN_SECS}"
	    echo ""

	    if [[ "$PLAN_STATUS" != "done" && "$PLAN_STATUS" != "skipped_fast_path" ]]; then
	        if [[ "$PLAN_STATUS" == "error" ]] && can_fallback_to_existing_objectives; then
	            warn "Plan agent failed, but existing scoped objectives remain valid and there are no live task_results to merge."
	            warn "Continuing to prover with the current PROGRESS.md."
	            PLAN_STATUS="fallback"
	            [[ "$DRY_RUN" != true ]] && write_meta "$ITER_META" "plan.status=${PLAN_STATUS}"
            elif [[ "$PLAN_STATUS" == "error" ]] && can_fallback_to_recovered_autoformalize_objective; then
                warn "Plan agent failed after updating the scoped autoformalize planning files."
                warn "Continuing to prover with the recovered autoformalize objective."
                PLAN_STATUS="fallback_recovered_autoformalize"
                [[ "$DRY_RUN" != true ]] && write_meta "$ITER_META" "plan.status=${PLAN_STATUS}"
	        else
	            warn "Skipping prover phase because the plan phase did not complete successfully."
	            break
	        fi
	    fi

	    if is_complete; then
	        ok "PROGRESS.md says COMPLETE. Exiting loop."
	        break
	    fi

    STAGE=$(read_stage)

    # --- Prover phase ---
    info "Phase 2: Prover agent(s)"
    [[ "$PARALLEL" == true ]] && info "Mode: parallel"
    info "────────────────────────────────────────"

    PROVER_START=$SECONDS
    [[ "$DRY_RUN" != true ]] && write_meta "$ITER_META" "prover.status=running"
    if [[ "$PARALLEL" == true ]]; then
        run_parallel_provers "$STAGE" || true
    else
        LOG_BASE="${ITER_DIR}/prover"
        PROVER_PROMPT=$(build_prompt "prover" "$STAGE")
        if [[ "$DRY_RUN" == true ]]; then
            echo "$PROVER_PROMPT"
        else
            # -- Snapshot: baseline for all target files in serial mode --
            sorry_files_serial=""
            sorry_files_serial=$(parse_objective_files)
            if [[ -n "$sorry_files_serial" ]]; then
                while IFS= read -r sf; do
                    srel=""
                    srel=$(relpath "$sf" "$PROJECT_PATH")
                    sslug=""
                    sslug=$(echo "$srel" | sed 's|/|_|g; s|\.lean$||')
                    ssnap="${ITER_DIR}/snapshots/${sslug}"
                    mkdir -p "$ssnap"
                    cp "$sf" "${ssnap}/baseline.lean" 2>/dev/null || true
                done <<< "$sorry_files_serial"
            fi
            # Serial prover edits multiple files — snapshot.py uses file_path to route
            # We set ARCHON_SNAPSHOT_DIR to the snapshots root; snapshot.py derives the subdir
            export ARCHON_SNAPSHOT_DIR="${ITER_DIR}/snapshots"
            export ARCHON_PROVER_JSONL="${ITER_DIR}/prover.jsonl"
            export ARCHON_PROJECT_PATH="$PROJECT_PATH"
            export ARCHON_SERIAL_MODE="true"
            CODEX_TIMEOUT_SECONDS="${PROVER_TIMEOUT_SECONDS}" run_codex "$PROVER_PROMPT" || true
            unset ARCHON_SNAPSHOT_DIR ARCHON_PROVER_JSONL ARCHON_PROJECT_PATH ARCHON_SERIAL_MODE
        fi
    fi

    PROVER_SECS=$(( SECONDS - PROVER_START ))
    info "Prover phase finished. (${PROVER_SECS}s)"
    [[ "$DRY_RUN" != true ]] && write_meta "$ITER_META" "prover.status=done" "prover.durationSecs=${PROVER_SECS}"
    echo ""

    # --- Review phase ---
    if [[ "$ENABLE_REVIEW" == true && "$DRY_RUN" != true ]]; then
        info "Phase 3: Review agent"
        info "────────────────────────────────────────"

        REVIEW_START=$SECONDS
        write_meta "$ITER_META" "review.status=running"
        if run_review_phase "$STAGE"; then
            REVIEW_STATUS="done"
        else
            REVIEW_STATUS="error"
            warn "Review agent exited with an error. Check ${ITER_DIR}/review.jsonl for details."
        fi

        REVIEW_SECS=$(( SECONDS - REVIEW_START ))
        info "Review phase finished. (${REVIEW_SECS}s)"
        write_meta "$ITER_META" "review.status=${REVIEW_STATUS}" "review.durationSecs=${REVIEW_SECS}"
        echo ""
    fi

    ITER_SECS=$(( SECONDS - ITER_START ))
    info "Iteration $((i+1)) complete. Wall time: ${ITER_SECS}s"
    [[ "$DRY_RUN" != true ]] && write_meta "$ITER_META" "completedAt=$(date -u +%Y-%m-%dT%H:%M:%SZ)" "wallTimeSecs=${ITER_SECS}"
    show_cost_summary "  Iteration $((i+1)) totals:" "${ITER_DIR:-}"
    echo ""
done

LOOP_SECS=$(( SECONDS - LOOP_START ))
if ! is_complete; then
    warn "Reached max iterations (${MAX_ITERATIONS}). Stopping."
fi
info "Total wall time: ${LOOP_SECS}s"
show_cost_summary "  Loop totals:" "${LOG_DIR}"
echo ""
info "View results: bash ${ARCHON_DIR}/ui/start.sh --project ${PROJECT_PATH}"
