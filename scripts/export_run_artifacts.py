#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from archonlib.run_workspace import export_run_artifacts


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export proofs, diffs, blockers, and supervisor notes from a run root.")
    parser.add_argument("--run-root", required=True, help="Path to the isolated run root")
    return parser.parse_args()


def main() -> int:
    summary = export_run_artifacts(Path(parse_args().run_root))
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
