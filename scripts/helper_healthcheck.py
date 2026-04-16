#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from archonlib.helper_health import probe_helper_transport


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a bounded helper transport probe against a local helper env file.")
    parser.add_argument("--repo-root", default=str(ROOT), help="AutoArchon repository root")
    parser.add_argument("--env-file", help="Helper env file to load before probing")
    parser.add_argument("--timeout-seconds", type=int, default=20, help="Probe timeout budget")
    parser.add_argument("--output", help="Optional JSON output path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    env_file = Path(args.env_file).resolve() if args.env_file else None
    payload = probe_helper_transport(
        repo_root=repo_root,
        env_file=env_file,
        timeout_seconds=args.timeout_seconds,
    )
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output:
        Path(args.output).resolve().write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)
    return 0 if payload.get("status") in {"ok", "disabled"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
