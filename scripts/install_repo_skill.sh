#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILL_SOURCE="${ROOT}/skills/archon-supervisor"
CODEX_HOME_DIR="${CODEX_HOME:-${HOME}/.codex}"
SKILLS_DIR="${CODEX_HOME_DIR}/skills"
TARGET="${SKILLS_DIR}/archon-supervisor"

if [[ ! -d "${SKILL_SOURCE}" ]]; then
  echo "skill source not found: ${SKILL_SOURCE}" >&2
  exit 1
fi

mkdir -p "${SKILLS_DIR}"

if [[ -L "${TARGET}" ]]; then
  CURRENT="$(readlink -f "${TARGET}")"
  if [[ "${CURRENT}" == "$(readlink -f "${SKILL_SOURCE}")" ]]; then
    echo "archon-supervisor is already installed at ${TARGET}"
    echo "Restart Codex or launch a fresh codex exec session to pick up the skill."
    exit 0
  fi
  rm -f "${TARGET}"
elif [[ -e "${TARGET}" ]]; then
  echo "refusing to overwrite existing path: ${TARGET}" >&2
  exit 1
fi

ln -s "${SKILL_SOURCE}" "${TARGET}"
echo "Installed archon-supervisor -> ${TARGET}"
echo "Restart Codex or launch a fresh codex exec session to pick up the skill."
