#!/usr/bin/env bash
set -euo pipefail

# ============================================================
#  AutoArchon Setup Script
#  Installs system prerequisites: Python, uv, Lean, Codex CLI
# ============================================================

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()   { echo -e "${RED}[ERROR]${NC} $*"; }

ARCHON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LEAN_TOOLCHAIN="${ARCHON_LEAN_TOOLCHAIN:-leanprover/lean4:v4.28.0}"
LEAN_INSTALL_METHOD="${ARCHON_LEAN_INSTALL_METHOD:-auto}"
LEAN_INSTALL_ROOT="${ARCHON_LEAN_INSTALL_ROOT:-$HOME/.local/opt}"
LEAN_ARCHIVE_URL="${ARCHON_LEAN_ARCHIVE_URL:-}"
LEAN_ARCHIVE_CACHE_DIRS="${ARCHON_LEAN_ARCHIVE_CACHE_DIRS:-$HOME/.cache/archon/lean}"
LEAN_ARCHIVE_LOCK_FILE="${ARCHON_LEAN_ARCHIVE_LOCK_FILE:-$HOME/.cache/archon/lean/install.lock}"
LOCAL_BIN_DIR="${HOME}/.local/bin"

detect_shell_rc() {
    if [[ "${SHELL:-}" == *"zsh"* ]]; then
        echo "$HOME/.zshrc"
    else
        echo "$HOME/.bashrc"
    fi
}

ensure_path_line() {
    local line="$1"
    local rc_file
    rc_file="$(detect_shell_rc)"
    touch "$rc_file"
    if ! grep -qF "$line" "$rc_file"; then
        printf '\n# Added by AutoArchon setup\n%s\n' "$line" >> "$rc_file"
        ok "Updated PATH in ${rc_file}"
    fi
}

ensure_command() {
    local name="$1"
    local install_hint="$2"
    if command -v "$name" >/dev/null 2>&1; then
        ok "${name}: $(command -v "$name")"
    else
        err "${name} is required. ${install_hint}"
        exit 1
    fi
}

install_lean_toolchain() {
    local attempts=0
    while (( attempts < 3 )); do
        attempts=$((attempts + 1))
        if elan toolchain install "${LEAN_TOOLCHAIN}"; then
            return 0
        fi
        warn "elan toolchain install failed (attempt ${attempts}/3). Retrying..."
        sleep 2
    done
    return 1
}

lean_archive_platform() {
    local os arch
    os="$(uname -s | tr '[:upper:]' '[:lower:]')"
    arch="$(uname -m)"
    case "${os}:${arch}" in
        linux:x86_64) echo "linux" ;;
        linux:aarch64|linux:arm64) echo "linux_aarch64" ;;
        darwin:x86_64) echo "darwin" ;;
        darwin:arm64) echo "darwin_aarch64" ;;
        *)
            return 1
            ;;
    esac
}

lean_archive_version() {
    local version="${LEAN_TOOLCHAIN##*:}"
    echo "${version#v}"
}

lean_version_satisfied() {
    local expected_version actual_version
    expected_version="$(lean_archive_version)"
    if ! command -v lean >/dev/null 2>&1 || ! command -v lake >/dev/null 2>&1; then
        return 1
    fi
    actual_version="$(lean --version 2>/dev/null | head -1)"
    [[ "${actual_version}" == *"version ${expected_version}"* ]]
}

lean_archive_url() {
    local version platform
    version="$(lean_archive_version)"
    platform="$(lean_archive_platform)" || return 1
    echo "https://releases.lean-lang.org/lean4/v${version}/lean-${version}-${platform}.tar.zst"
}

download_with_resume() {
    local url="$1"
    local output="$2"
    mkdir -p "$(dirname "$output")"
    if command -v aria2c >/dev/null 2>&1; then
        info "Downloading Lean archive with aria2c (16 connections)..."
        aria2c \
            --continue=true \
            --max-connection-per-server=16 \
            --split=16 \
            --min-split-size=1M \
            --file-allocation=none \
            --summary-interval=0 \
            --dir "$(dirname "$output")" \
            --out "$(basename "$output")" \
            "$url"
    else
        info "Downloading Lean archive with curl resume support..."
        curl -L --continue-at - --output "$output" "$url"
    fi
}

validate_lean_archive() {
    local archive_path="$1"
    [[ -f "$archive_path" ]] || return 1
    zstd -t "$archive_path" >/dev/null 2>&1
}

cleanup_lean_archive() {
    local archive_path="$1"
    rm -f "${archive_path}" "${archive_path}.aria2"
}

find_valid_cached_archive() {
    local archive_name="$1"
    local dir candidate
    local old_ifs="$IFS"
    IFS=':'
    for dir in ${LEAN_ARCHIVE_CACHE_DIRS}; do
        [[ -n "$dir" ]] || continue
        candidate="${dir}/${archive_name}"
        if validate_lean_archive "$candidate"; then
            echo "$candidate"
            IFS="$old_ifs"
            return 0
        fi
    done
    IFS="$old_ifs"
    return 1
}

ensure_valid_lean_archive() {
    local archive_path="$1"
    local archive_name="$2"
    local archive_url="$3"
    local cached_archive=""
    if validate_lean_archive "$archive_path"; then
        ok "Reusing existing Lean archive: ${archive_path}"
        return 0
    fi
    if [[ -f "$archive_path" ]]; then
        warn "Existing Lean archive is incomplete or corrupted: ${archive_path}"
        cleanup_lean_archive "$archive_path"
    fi
    cached_archive="$(find_valid_cached_archive "$archive_name" || true)"
    if [[ -n "$cached_archive" ]]; then
        info "Copying validated Lean archive from cache: ${cached_archive}"
        cp -f "$cached_archive" "$archive_path"
        return 0
    fi
    download_with_resume "${archive_url}" "${archive_path}"
    if ! validate_lean_archive "$archive_path"; then
        err "Downloaded Lean archive is incomplete or corrupted: ${archive_path}"
        cleanup_lean_archive "$archive_path"
        return 1
    fi
}

install_lean_archive() {
    local version platform archive_url archive_name archive_path extracted_root install_dir tmp_root
    version="$(lean_archive_version)"
    platform="$(lean_archive_platform)" || {
        warn "No direct Lean archive mapping for $(uname -s)/$(uname -m); archive fallback unavailable."
        return 1
    }
    archive_url="${LEAN_ARCHIVE_URL:-$(lean_archive_url)}"
    archive_name="lean-${version}-${platform}.tar.zst"
    archive_path="${LEAN_INSTALL_ROOT}/${archive_name}"
    install_dir="${LEAN_INSTALL_ROOT}/lean-${version}-${platform}"

    info "Installing Lean ${version} from archive: ${archive_url}"
    ensure_command tar "Install tar to extract Lean archives."
    ensure_command zstd "Install zstd to extract Lean archives."
    mkdir -p "${LEAN_INSTALL_ROOT}" "${LOCAL_BIN_DIR}" "$(dirname "${LEAN_ARCHIVE_LOCK_FILE}")"
    if command -v flock >/dev/null 2>&1; then
        exec 9>"${LEAN_ARCHIVE_LOCK_FILE}"
        info "Waiting for Lean archive lock: ${LEAN_ARCHIVE_LOCK_FILE}"
        flock 9
    else
        warn "flock not found. Archive install will proceed without an inter-process lock."
    fi
    ensure_valid_lean_archive "${archive_path}" "${archive_name}" "${archive_url}"

    tmp_root="$(mktemp -d "${LEAN_INSTALL_ROOT}/extract.XXXXXX")"
    tar --zstd -xf "${archive_path}" -C "${tmp_root}"
    extracted_root="$(find "${tmp_root}" -mindepth 1 -maxdepth 1 -type d | head -1)"
    if [[ -z "${extracted_root}" ]]; then
        err "Lean archive extraction produced no root directory."
        rm -rf "${tmp_root}"
        return 1
    fi
    rm -rf "${install_dir}"
    mv "${extracted_root}" "${install_dir}"
    rm -rf "${tmp_root}"

    ln -sfn "${install_dir}/bin/lean" "${LOCAL_BIN_DIR}/lean"
    ln -sfn "${install_dir}/bin/lake" "${LOCAL_BIN_DIR}/lake"
    ln -sfn "${install_dir}/bin/leanc" "${LOCAL_BIN_DIR}/leanc"
    ln -sfn "${install_dir}/bin/leanchecker" "${LOCAL_BIN_DIR}/leanchecker"
    ensure_path_line "export PATH=\"${install_dir}/bin:\$HOME/.local/bin:\$HOME/.elan/bin:\$PATH\""
    export PATH="${install_dir}/bin:${LOCAL_BIN_DIR}:$HOME/.elan/bin:$PATH"
    ok "Installed Lean archive to ${install_dir}"
    return 0
}

info "AutoArchon directory: ${ARCHON_DIR}"
info "Lean toolchain target: ${LEAN_TOOLCHAIN}"

info "=== Phase 1: Core prerequisites ==="
ensure_command git "Install git first."
ensure_command python3 "Install Python 3.10+ first."
ensure_command curl "Install curl first."

if command -v uv >/dev/null 2>&1; then
    ok "uv: $(uv --version)"
else
    info "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
    ensure_path_line 'export PATH="$HOME/.local/bin:$PATH"'
    ensure_command uv "uv installation failed."
fi

if command -v tmux >/dev/null 2>&1; then
    ok "tmux: $(tmux -V)"
else
    warn "tmux not found. Parallel prover mode benefits from tmux, but setup will continue."
fi

if command -v rg >/dev/null 2>&1; then
    ok "ripgrep: $(rg --version | head -1)"
else
    warn "ripgrep not found. Install it for faster search."
fi

info "=== Phase 2: Lean toolchain ==="
if ! command -v elan >/dev/null 2>&1; then
    info "Installing elan..."
    curl https://elan.lean-lang.org/elan-init.sh -sSf | sh -s -- -y
fi
export PATH="$HOME/.elan/bin:$PATH"
ensure_path_line 'export PATH="$HOME/.elan/bin:$PATH"'
ensure_command elan "elan installation failed."

info "Installing Lean toolchain ${LEAN_TOOLCHAIN}..."
LEAN_READY=false
if lean_version_satisfied; then
    ok "Lean ${LEAN_TOOLCHAIN} already available on PATH"
    LEAN_READY=true
else
    case "${LEAN_INSTALL_METHOD}" in
        elan)
            install_lean_toolchain
            elan default "${LEAN_TOOLCHAIN}"
            LEAN_READY=true
            ;;
        archive)
            install_lean_archive
            LEAN_READY=true
            ;;
        auto)
            if install_lean_toolchain; then
                elan default "${LEAN_TOOLCHAIN}"
                LEAN_READY=true
            else
                warn "elan installation path failed; falling back to direct Lean archive install."
                install_lean_archive
                LEAN_READY=true
            fi
            ;;
        *)
            err "Unknown ARCHON_LEAN_INSTALL_METHOD='${LEAN_INSTALL_METHOD}'. Use auto, elan, or archive."
            exit 1
            ;;
    esac
fi

if [[ "${LEAN_READY}" != true ]]; then
    err "Lean installation did not complete."
    exit 1
fi

ensure_command lean "Lean installation failed."
ensure_command lake "Lake installation failed."
ok "lean: $(lean --version | head -1)"
ok "lake: $(lake --version | head -1)"

info "=== Phase 3: Codex CLI ==="
if command -v codex >/dev/null 2>&1; then
    ok "codex: $(codex --version)"
else
    ensure_command npm "Install Node.js and npm before installing Codex CLI."
    info "Installing Codex CLI..."
    npm install -g @openai/codex
    ensure_command codex "Codex CLI installation failed."
fi

info "=== Phase 4: Optional API keys ==="
[[ -n "${OPENAI_API_KEY:-}" ]] && ok "OPENAI_API_KEY is set" || info "  OPENAI_API_KEY not set"
[[ -n "${GEMINI_API_KEY:-}" ]] && ok "GEMINI_API_KEY is set" || info "  GEMINI_API_KEY not set"
[[ -n "${OPENROUTER_API_KEY:-}" ]] && ok "OPENROUTER_API_KEY is set" || info "  OPENROUTER_API_KEY not set"
[[ -n "${DEEPSEEK_API_KEY:-}" ]] && ok "DEEPSEEK_API_KEY is set" || info "  DEEPSEEK_API_KEY not set"

info "=== Phase 5: AutoArchon Python environment ==="
uv sync --all-groups
ok "uv environment synchronized"

echo ""
echo -e "${GREEN}============================================================${NC}"
echo -e "${GREEN}  Setup complete${NC}"
echo -e "${GREEN}============================================================${NC}"
echo ""
warn "Reload your shell if PATH was updated:"
warn "  source $(detect_shell_rc)"
