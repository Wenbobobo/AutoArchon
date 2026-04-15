#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from archonlib.launch_contract import validate_launch_contract


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate campaign-operator launch contract files before starting the watchdog.")
    parser.add_argument("--campaign-root", required=True, help="Existing or scaffolded campaign root")
    parser.add_argument("--repo-root", default=str(ROOT), help="AutoArchon repository root used for helper env checks")
    parser.add_argument("--strict", action="store_true", help="Treat warnings as launch-blocking errors")
    parser.add_argument("--output", help="Optional JSON output path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = validate_launch_contract(
        Path(args.campaign_root),
        repo_root=Path(args.repo_root),
        strict=args.strict,
    )
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output:
        Path(args.output).resolve().write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)
    return 0 if payload["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
