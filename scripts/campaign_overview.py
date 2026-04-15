#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from archonlib.campaign import (
    DEFAULT_HEARTBEAT_SECONDS,
    build_campaign_overview,
    render_campaign_overview_markdown,
    write_campaign_progress_surface,
)


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
    parser.add_argument(
        "--no-write-progress-surface",
        action="store_true",
        help="Do not refresh control/progress-summary.md, control/progress-summary.json, and control/progress-summary.html",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = build_campaign_overview(
        Path(args.campaign_root),
        heartbeat_seconds=args.heartbeat_seconds,
        refresh_status=not args.no_refresh_status,
    )
    if not args.no_write_progress_surface:
        write_campaign_progress_surface(Path(args.campaign_root), payload)
    rendered = render_campaign_overview_markdown(payload) if args.markdown else json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
