#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CODEX_HOME_DIR="${CODEX_HOME:-${HOME}/.codex}"
SKILLS_DIR="${CODEX_HOME_DIR}/skills"

mkdir -p "${SKILLS_DIR}"

for SKILL_SOURCE in "${ROOT}"/skills/*; do
  [[ -d "${SKILL_SOURCE}" ]] || continue
  SKILL_NAME="$(basename "${SKILL_SOURCE}")"
  TARGET="${SKILLS_DIR}/${SKILL_NAME}"

  if [[ -L "${TARGET}" ]]; then
    CURRENT="$(readlink -f "${TARGET}")"
    if [[ "${CURRENT}" == "$(readlink -f "${SKILL_SOURCE}")" ]]; then
      echo "${SKILL_NAME} is already installed at ${TARGET}"
      continue
    fi
    rm -f "${TARGET}"
  elif [[ -e "${TARGET}" ]]; then
    echo "refusing to overwrite existing path: ${TARGET}" >&2
    exit 1
  fi

  ln -s "${SKILL_SOURCE}" "${TARGET}"
  echo "Installed ${SKILL_NAME} -> ${TARGET}"
done

echo "Restart Codex or launch a fresh codex exec session to pick up the repo-owned skills."
