#!/usr/bin/env bash
set -euo pipefail

ARCHON_ROOT="${ARCHON_ROOT:-/home/daism/Wenbo/math/Archon}"
WORK_ROOT="${WORK_ROOT:-/home/daism/Wenbo/math}"
BENCHMARK_ROOT="${BENCHMARK_ROOT:-${WORK_ROOT}/benchmarks}"
CAMPAIGNS_ROOT="${CAMPAIGNS_ROOT:-${WORK_ROOT}/runs/campaigns}"
RUN_SPECS_ROOT="${RUN_SPECS_ROOT:-${CAMPAIGNS_ROOT}/_run_specs}"
HELPER_ENV_FILE="${HELPER_ENV_FILE:-${ARCHON_ROOT}/examples/helper.env}"

DATE_TAG="${FATE_DATE_TAG:-$(date +%Y%m%d-nightly)}"
MODEL="${MODEL:-gpt-5.4}"
REASONING_EFFORT="${REASONING_EFFORT:-xhigh}"
POLL_SECONDS="${POLL_SECONDS:-30}"
STALL_SECONDS="${STALL_SECONDS:-300}"
BOOTSTRAP_LAUNCH_AFTER_SECONDS="${BOOTSTRAP_LAUNCH_AFTER_SECONDS:-45}"
MAX_RESTARTS="${MAX_RESTARTS:-3}"
OWNER_SILENCE_SECONDS="${OWNER_SILENCE_SECONDS:-1200}"
MAX_ACTIVE_LAUNCHES="${MAX_ACTIVE_LAUNCHES:-2}"
LAUNCH_BATCH_SIZE="${LAUNCH_BATCH_SIZE:-1}"
LAUNCH_COOLDOWN_SECONDS="${LAUNCH_COOLDOWN_SECONDS:-90}"
PRUNE_WORKSPACE_LAKE="${PRUNE_WORKSPACE_LAKE:-1}"
PRUNE_BROKEN_PREWARM="${PRUNE_BROKEN_PREWARM:-1}"
DRY_RUN="${DRY_RUN:-0}"
ARCHON_CODEX_READY_RETRIES="${ARCHON_CODEX_READY_RETRIES:-10}"
ARCHON_CODEX_READY_RETRY_DELAY_SECONDS="${ARCHON_CODEX_READY_RETRY_DELAY_SECONDS:-15}"

FATE_M_SHARD_SIZE="${FATE_M_SHARD_SIZE:-8}"
FATE_H_SHARD_SIZE="${FATE_H_SHARD_SIZE:-8}"
FATE_X_SHARD_SIZE="${FATE_X_SHARD_SIZE:-8}"

if [[ -n "${HELPER_ENV_FILE}" && -f "${HELPER_ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${HELPER_ENV_FILE}"
  set +a
fi

mkdir -p "${CAMPAIGNS_ROOT}" "${RUN_SPECS_ROOT}"

render_cmd() {
  printf '%q ' "$@"
  printf '\n'
}

run_cmd() {
  if [[ "${DRY_RUN}" == "1" ]]; then
    render_cmd "$@"
    return 0
  fi
  "$@"
}

launch_spec() {
  local slug="$1"
  local template_path="$2"
  local shard_size="$3"
  local resolved_spec_path="${RUN_SPECS_ROOT}/${DATE_TAG}-${slug}.launch.json"

  printf '[launch-spec] %s: %s -> %s\n' "${slug}" "${template_path}" "${resolved_spec_path}" >&2
  local init_cmd=(
    uv run --directory "${ARCHON_ROOT}" autoarchon-init-campaign-spec
    --template "${template_path}"
    --benchmark-root "${BENCHMARK_ROOT}"
    --campaigns-root "${CAMPAIGNS_ROOT}"
    --run-specs-root "${RUN_SPECS_ROOT}"
    --date-tag "${DATE_TAG}"
    --model "${MODEL}"
    --reasoning-effort "${REASONING_EFFORT}"
    --shard-size "${shard_size}"
    --output "${resolved_spec_path}"
  )
  if [[ "${DRY_RUN}" == "1" ]]; then
    init_cmd+=(--dry-run)
  fi
  run_cmd "${init_cmd[@]}"

  local launch_cmd=(
    uv run --directory "${ARCHON_ROOT}" autoarchon-launch-from-spec
    --spec-file "${resolved_spec_path}"
  )
  if [[ "${DRY_RUN}" == "1" ]]; then
    launch_cmd+=(--dry-run)
  fi
  run_cmd env \
    FATE_DATE_TAG="${DATE_TAG}" \
    MODEL="${MODEL}" \
    REASONING_EFFORT="${REASONING_EFFORT}" \
    BENCHMARK_ROOT="${BENCHMARK_ROOT}" \
    CAMPAIGNS_ROOT="${CAMPAIGNS_ROOT}" \
    RUN_SPECS_ROOT="${RUN_SPECS_ROOT}" \
    POLL_SECONDS="${POLL_SECONDS}" \
    STALL_SECONDS="${STALL_SECONDS}" \
    BOOTSTRAP_LAUNCH_AFTER_SECONDS="${BOOTSTRAP_LAUNCH_AFTER_SECONDS}" \
    MAX_RESTARTS="${MAX_RESTARTS}" \
    OWNER_SILENCE_SECONDS="${OWNER_SILENCE_SECONDS}" \
    MAX_ACTIVE_LAUNCHES="${MAX_ACTIVE_LAUNCHES}" \
    LAUNCH_BATCH_SIZE="${LAUNCH_BATCH_SIZE}" \
    LAUNCH_COOLDOWN_SECONDS="${LAUNCH_COOLDOWN_SECONDS}" \
    PRUNE_WORKSPACE_LAKE="${PRUNE_WORKSPACE_LAKE}" \
    PRUNE_BROKEN_PREWARM="${PRUNE_BROKEN_PREWARM}" \
    ARCHON_CODEX_READY_RETRIES="${ARCHON_CODEX_READY_RETRIES}" \
    ARCHON_CODEX_READY_RETRY_DELAY_SECONDS="${ARCHON_CODEX_READY_RETRY_DELAY_SECONDS}" \
    "${launch_cmd[@]}"
}

main() {
  local m_root h_root x_root
  m_root="${CAMPAIGNS_ROOT}/${DATE_TAG}-fate-m-full"
  h_root="${CAMPAIGNS_ROOT}/${DATE_TAG}-fate-h-full"
  x_root="${CAMPAIGNS_ROOT}/${DATE_TAG}-fate-x-full"

  launch_spec "fate-m-full" "${ARCHON_ROOT}/campaign_specs/fate-m-full.json" "${FATE_M_SHARD_SIZE}"
  launch_spec "fate-h-full" "${ARCHON_ROOT}/campaign_specs/fate-h-full.json" "${FATE_H_SHARD_SIZE}"
  launch_spec "fate-x-full" "${ARCHON_ROOT}/campaign_specs/fate-x-full.json" "${FATE_X_SHARD_SIZE}"

  cat <<EOF

Campaign roots:
- ${m_root}
- ${h_root}
- ${x_root}

Quick monitor:
bash "${ARCHON_ROOT}/scripts/watch_campaign.sh" "${m_root}" "${h_root}" "${x_root}"
EOF
}

main "$@"
