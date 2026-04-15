#!/usr/bin/env bash
set -euo pipefail

INTERVAL_SECONDS="${INTERVAL_SECONDS:-15}"
ONCE="${ONCE:-0}"

if [[ "$#" -lt 1 ]]; then
  echo "usage: bash scripts/watch_run.sh /path/to/run-root/workspace [more workspaces...]" >&2
  exit 1
fi

render_run() {
  local workspace="$1"
  local supervisor_dir="${workspace}/.archon/supervisor"
  local summary_md="${supervisor_dir}/progress-summary.md"
  local hot_notes="${supervisor_dir}/HOT_NOTES.md"
  local ledger="${supervisor_dir}/LEDGER.md"

  echo "== ${workspace} =="
  if [[ -f "${summary_md}" ]]; then
    cat "${summary_md}"
  else
    echo "(missing ${summary_md})"
  fi

  echo
  echo "## HOT_NOTES tail"
  if [[ -f "${hot_notes}" ]]; then
    tail -n 20 "${hot_notes}"
  else
    echo "(missing ${hot_notes})"
  fi

  echo
  echo "## LEDGER tail"
  if [[ -f "${ledger}" ]]; then
    tail -n 20 "${ledger}"
  else
    echo "(missing ${ledger})"
  fi

  echo
}

while true; do
  if [[ "${ONCE}" != "1" ]] && command -v clear >/dev/null 2>&1; then
    clear
  fi
  for workspace in "$@"; do
    render_run "${workspace}"
  done
  if [[ "${ONCE}" == "1" ]]; then
    break
  fi
  sleep "${INTERVAL_SECONDS}"
done
