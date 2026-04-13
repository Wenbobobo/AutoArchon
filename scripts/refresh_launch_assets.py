#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from archonlib.campaign import refresh_campaign_launch_assets


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Refresh generated launch-teacher assets for an existing AutoArchon campaign."
    )
    parser.add_argument("--campaign-root", required=True, help="Existing campaign directory")
    parser.add_argument("--run-id", action="append", default=[], help="Optional run id(s) to refresh")
    parser.add_argument(
        "--refresh-prompts",
        action="store_true",
        help="Also regenerate teacher-prompt.txt from the current manifest defaults",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = refresh_campaign_launch_assets(
        Path(args.campaign_root),
        run_ids=args.run_id,
        refresh_prompts=args.refresh_prompts,
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
