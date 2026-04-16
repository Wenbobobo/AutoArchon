#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from archonlib.helper_analysis import (
    build_helper_analysis,
    render_helper_analysis_markdown,
    write_helper_analysis_artifacts,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze AutoArchon helper usage, failure families, and repeated-attempt clusters.")
    parser.add_argument("--campaign-root", action="append", default=[], help="Campaign root to inspect (repeatable)")
    parser.add_argument("--top", type=int, default=10, help="Max rows per hotspot table")
    parser.add_argument("--markdown", action="store_true", help="Render Markdown to stdout instead of JSON")
    parser.add_argument("--output", help="Optional path to write the rendered stdout payload")
    parser.add_argument(
        "--write-default-files",
        action="store_true",
        help="Also write helper-analysis.json/.md under each campaign's default reports/.../helper-analysis/ root",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.campaign_root:
        raise SystemExit("At least one --campaign-root is required.")

    campaign_roots = [Path(item).resolve() for item in args.campaign_root]
    payload = build_helper_analysis(campaign_roots, top_n=args.top)
    rendered = render_helper_analysis_markdown(payload) if args.markdown else json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output:
        output_path = Path(args.output).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered, encoding="utf-8")
    if args.write_default_files:
        for campaign in payload.get("campaigns", []):
            if not isinstance(campaign, dict):
                continue
            output_root = campaign.get("paths", {}).get("defaultOutputRoot") if isinstance(campaign.get("paths"), dict) else None
            if not isinstance(output_root, str) or not output_root:
                continue
            write_helper_analysis_artifacts(Path(output_root), campaign)
    sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
