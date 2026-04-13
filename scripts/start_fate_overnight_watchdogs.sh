#!/usr/bin/env bash
set -euo pipefail

ARCHON_ROOT="${ARCHON_ROOT:-/home/daism/Wenbo/math/Archon}"
WORK_ROOT="${WORK_ROOT:-/home/daism/Wenbo/math}"
BENCHMARK_ROOT="${BENCHMARK_ROOT:-${WORK_ROOT}/benchmarks}"
CAMPAIGNS_ROOT="${CAMPAIGNS_ROOT:-${WORK_ROOT}/runs/campaigns}"
RUN_SPECS_ROOT="${RUN_SPECS_ROOT:-${CAMPAIGNS_ROOT}/_run_specs}"

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
DRY_RUN="${DRY_RUN:-0}"
ARCHON_CODEX_READY_RETRIES="${ARCHON_CODEX_READY_RETRIES:-10}"
ARCHON_CODEX_READY_RETRY_DELAY_SECONDS="${ARCHON_CODEX_READY_RETRY_DELAY_SECONDS:-15}"

FATE_M_SHARD_SIZE="${FATE_M_SHARD_SIZE:-8}"
FATE_H_SHARD_SIZE="${FATE_H_SHARD_SIZE:-8}"
FATE_X_SHARD_SIZE="${FATE_X_SHARD_SIZE:-8}"

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
  local spec_path="$2"
  local shard_size="$3"

  printf '[launch-spec] %s: %s\n' "${slug}" "${spec_path}" >&2
  local cmd=(
    uv run --directory "${ARCHON_ROOT}" autoarchon-launch-from-spec
    --spec-file "${spec_path}"
    --shard-size "${shard_size}"
  )
  if [[ "${DRY_RUN}" == "1" ]]; then
    cmd+=(--dry-run)
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
    ARCHON_CODEX_READY_RETRIES="${ARCHON_CODEX_READY_RETRIES}" \
    ARCHON_CODEX_READY_RETRY_DELAY_SECONDS="${ARCHON_CODEX_READY_RETRY_DELAY_SECONDS}" \
    "${cmd[@]}"
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
for c in "${m_root}" "${h_root}" "${x_root}"; do
  echo "== \$c ==";
  uv run --directory "${ARCHON_ROOT}" autoarchon-campaign-overview --campaign-root "\$c" --markdown;
done
EOF
}

main "$@"
