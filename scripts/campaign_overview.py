#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from archonlib.campaign import DEFAULT_HEARTBEAT_SECONDS, build_campaign_overview


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a compact campaign overview for terminal or in-flight AutoArchon runs.")
    parser.add_argument("--campaign-root", required=True, help="Existing campaign directory")
    parser.add_argument(
        "--heartbeat-seconds",
        type=int,
        default=DEFAULT_HEARTBEAT_SECONDS,
        help="Recent activity threshold used while refreshing campaign status",
    )
    parser.add_argument(
        "--no-refresh-status",
        action="store_true",
        help="Read campaign-status.json when present instead of forcing a fresh status recompute",
    )
    parser.add_argument(
        "--markdown",
        action="store_true",
        help="Render a compact Markdown summary instead of JSON",
    )
    parser.add_argument("--output", help="Optional file path to write in addition to stdout")
    return parser.parse_args()


def _render_markdown(overview: dict[str, object]) -> str:
    run_counts = json.dumps(overview.get("runCounts", {}), sort_keys=True)
    target_counts = json.dumps(overview.get("targetCounts", {}), sort_keys=True)
    prewarm_counts = json.dumps(overview.get("prewarmCounts", {}), sort_keys=True)
    report_freshness = overview.get("reportFreshness", {}) if isinstance(overview.get("reportFreshness"), dict) else {}
    eta = overview.get("eta", {}) if isinstance(overview.get("eta"), dict) else {}
    running_runs = overview.get("runningRuns", []) if isinstance(overview.get("runningRuns"), list) else []
    recoverable_runs = overview.get("recoverableRuns", []) if isinstance(overview.get("recoverableRuns"), list) else []

    lines = [
        f"# Campaign Overview: {overview.get('campaignId')}",
        "",
        f"- Generated at: `{overview.get('generatedAt')}`",
        f"- Run counts: `{run_counts}`",
        f"- Target counts: `{target_counts}`",
        f"- Prewarm counts: `{prewarm_counts}`",
        f"- Watchdog status: `{overview.get('watchdogStatus')}`",
        f"- Restart count: `{overview.get('restartCount')}`",
        f"- Compare fresh: `{report_freshness.get('compareIsFresh')}`",
        f"- ETA: `{eta.get('etaText')}`",
        "",
        "## Running Runs",
        "",
    ]
    if running_runs:
        for row in running_runs:
            if not isinstance(row, dict):
                continue
            lines.append(
                f"- `{row.get('runId')}` pending={row.get('pendingTargetCount')} "
                f"accepted_proofs={row.get('acceptedProofCount')} accepted_blockers={row.get('acceptedBlockerCount')}"
            )
    else:
        lines.append("- none")

    lines.extend(["", "## Recoverable Runs", ""])
    if recoverable_runs:
        for row in recoverable_runs[:12]:
            if not isinstance(row, dict):
                continue
            lines.append(
                f"- `{row.get('runId')}` status={row.get('status')} action={row.get('action')} "
                f"class={row.get('recoveryClass')} pending={row.get('pendingTargetCount')}"
            )
    else:
        lines.append("- none")
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    payload = build_campaign_overview(
        Path(args.campaign_root),
        heartbeat_seconds=args.heartbeat_seconds,
        refresh_status=not args.no_refresh_status,
    )
    rendered = _render_markdown(payload) if args.markdown else json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
