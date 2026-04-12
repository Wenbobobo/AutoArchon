#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from archonlib.campaign import plan_campaign_shards


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate stable run-spec JSON for Archon campaign micro-shards.")
    parser.add_argument("--source-root", required=True, help="Immutable benchmark or project source root")
    parser.add_argument("--run-id-prefix", default="teacher", help="Prefix for generated run ids")
    parser.add_argument(
        "--run-id-mode",
        choices=("index", "file_stem"),
        default="index",
        help="How to derive run ids. 'index' yields teacher-001 style ids; 'file_stem' yields teacher-39 for single-file shards.",
    )
    parser.add_argument("--match-regex", help="Optional regex applied to relative .lean paths")
    parser.add_argument("--limit", type=int, help="Optional limit after regex filtering")
    parser.add_argument("--shard-size", type=int, default=1, help="Number of files per generated run spec")
    parser.add_argument("--start-index", type=int, default=1, help="Starting index for generated run ids")
    parser.add_argument("--output", help="Optional JSON file path to write in addition to stdout")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = plan_campaign_shards(
        Path(args.source_root),
        run_id_prefix=args.run_id_prefix,
        run_id_mode=args.run_id_mode,
        include_regex=args.match_regex,
        limit=args.limit,
        shard_size=args.shard_size,
        start_index=args.start_index,
    )
    rendered = json.dumps(payload, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
