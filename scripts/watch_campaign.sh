#!/usr/bin/env bash
set -euo pipefail

ARCHON_ROOT="${ARCHON_ROOT:-/home/daism/Wenbo/math/Archon}"
INTERVAL_SECONDS="${INTERVAL_SECONDS:-30}"
ONCE="${ONCE:-0}"

if [[ "$#" -lt 1 ]]; then
  echo "usage: bash scripts/watch_campaign.sh /path/to/campaign-root [more roots...]" >&2
  exit 1
fi

render_watchdog_summary() {
  local campaign_root="$1"
  python3 - "$campaign_root" <<'PY'
import json
import sys
from pathlib import Path

campaign_root = Path(sys.argv[1])
state_path = campaign_root / "control" / "orchestrator-watchdog.json"
if not state_path.exists():
    print("- Watchdog state: missing")
    raise SystemExit(0)
try:
    payload = json.loads(state_path.read_text(encoding="utf-8"))
except json.JSONDecodeError:
    print("- Watchdog state: unreadable")
    raise SystemExit(0)
print(
    "- Watchdog:"
    f" status={payload.get('watchdogStatus', 'unknown')}"
    f" restarts={payload.get('restartCount', 'unknown')}"
    f" active={payload.get('activeWorkRunIds', [])}"
    f" likely_cause={payload.get('likelyCause', 'unknown')}"
)
PY
}

render_campaign() {
  local campaign_root="$1"
  echo "== ${campaign_root} =="
  uv run --directory "${ARCHON_ROOT}" autoarchon-campaign-overview --campaign-root "${campaign_root}" --markdown
  render_watchdog_summary "${campaign_root}"
  echo
}

while true; do
  if [[ "${ONCE}" != "1" ]] && command -v clear >/dev/null 2>&1; then
    clear
  fi
  for campaign_root in "$@"; do
    render_campaign "${campaign_root}"
  done
  if [[ "${ONCE}" == "1" ]]; then
    break
  fi
  sleep "${INTERVAL_SECONDS}"
done
