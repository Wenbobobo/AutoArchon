#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from archonlib.campaign import DEFAULT_HEARTBEAT_SECONDS, cleanup_stale_launch_processes


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspect or clean stale detached launch-teacher process groups for an AutoArchon campaign."
    )
    parser.add_argument("--campaign-root", required=True, help="Existing campaign directory")
    parser.add_argument("--run-id", action="append", default=[], help="Optional run id(s) to inspect")
    parser.add_argument(
        "--heartbeat-seconds",
        type=int,
        default=DEFAULT_HEARTBEAT_SECONDS,
        help="Recent activity threshold used to classify live runs before cleanup",
    )
    parser.add_argument(
        "--duplicate-grace-seconds",
        type=int,
        default=60,
        help="Minimum age gap used before treating an older launcher as stale",
    )
    parser.add_argument("--execute", action="store_true", help="Actually send SIGTERM to stale launch process groups")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = cleanup_stale_launch_processes(
        Path(args.campaign_root),
        run_ids=args.run_id,
        heartbeat_seconds=args.heartbeat_seconds,
        duplicate_grace_seconds=args.duplicate_grace_seconds,
        execute=args.execute,
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
