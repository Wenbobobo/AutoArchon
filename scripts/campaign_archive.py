#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from archonlib.campaign import DEFAULT_HEARTBEAT_SECONDS, archive_campaign_postmortem


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Archive a stopped or degraded AutoArchon campaign into reports/postmortem/.")
    parser.add_argument("--campaign-root", required=True, help="Existing campaign directory")
    parser.add_argument(
        "--heartbeat-seconds",
        type=int,
        default=DEFAULT_HEARTBEAT_SECONDS,
        help="Recent activity threshold used while refreshing campaign status",
    )
    parser.add_argument(
        "--prune-workspace-lake",
        action="store_true",
        help="After archiving the postmortem, remove inactive run workspace/.lake caches under the campaign",
    )
    parser.add_argument(
        "--prune-broken-prewarm",
        action="store_true",
        help="After archiving the postmortem, remove failed or abandoned .lake.prewarm-* directories under the campaign",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = archive_campaign_postmortem(
        Path(args.campaign_root),
        heartbeat_seconds=args.heartbeat_seconds,
        prune_workspace_lake=args.prune_workspace_lake,
        prune_broken_prewarm=args.prune_broken_prewarm,
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
