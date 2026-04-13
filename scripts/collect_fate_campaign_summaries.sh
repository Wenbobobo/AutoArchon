#!/usr/bin/env bash
set -euo pipefail

ARCHON_ROOT="${ARCHON_ROOT:-/home/daism/Wenbo/math/Archon}"
WORK_ROOT="${WORK_ROOT:-/home/daism/Wenbo/math}"
CAMPAIGNS_ROOT="${CAMPAIGNS_ROOT:-${WORK_ROOT}/runs/campaigns}"
DATE_TAG="${FATE_DATE_TAG:-$(date +%Y%m%d-nightly)}"

campaigns=(
  "${CAMPAIGNS_ROOT}/${DATE_TAG}-fate-m-full"
  "${CAMPAIGNS_ROOT}/${DATE_TAG}-fate-h-full"
  "${CAMPAIGNS_ROOT}/${DATE_TAG}-fate-x-full"
)

for campaign_root in "${campaigns[@]}"; do
  if [[ ! -d "${campaign_root}" ]]; then
    echo "[skip] missing campaign root: ${campaign_root}" >&2
    continue
  fi

  echo "== ${campaign_root} =="
  uv run --directory "${ARCHON_ROOT}" autoarchon-campaign-overview --campaign-root "${campaign_root}" --markdown
  echo
done
