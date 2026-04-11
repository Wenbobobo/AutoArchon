#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from archonlib.run_workspace import create_isolated_run


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create an isolated Archon run root with source/workspace/artifacts.")
    parser.add_argument("--source-root", required=True, help="Path to the immutable source project")
    parser.add_argument("--run-root", required=True, help="Directory to create for the isolated run")
    parser.add_argument(
        "--reuse-lake-from",
        help="Optional project root or .lake directory to reuse as workspace/.lake",
    )
    parser.add_argument(
        "--scope-hint",
        help="Optional human-readable scope hint stored in RUN_MANIFEST.json",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = create_isolated_run(
        Path(args.source_root),
        Path(args.run_root),
        reuse_lake_from=Path(args.reuse_lake_from) if args.reuse_lake_from else None,
        scope_hint=args.scope_hint,
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
