#!/usr/bin/env bash
set -euo pipefail

ARCHON_ROOT="${ARCHON_ROOT:-/home/daism/Wenbo/math/Archon}"
MODEL="${MODEL:-gpt-5.4}"
REASONING_EFFORT="${REASONING_EFFORT:-xhigh}"
SANDBOX="${SANDBOX:-danger-full-access}"
APPROVAL_MODE="${APPROVAL_MODE:-never}"
HELPER_ENV_FILE="${HELPER_ENV_FILE:-${ARCHON_ROOT}/examples/helper.env}"

if [[ -n "${HELPER_ENV_FILE}" && -f "${HELPER_ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${HELPER_ENV_FILE}"
  set +a
fi

exec codex -C "${ARCHON_ROOT}" \
  --model "${MODEL}" \
  --sandbox "${SANDBOX}" \
  --ask-for-approval "${APPROVAL_MODE}" \
  --config "model_reasoning_effort=${REASONING_EFFORT}" \
  "$@"
