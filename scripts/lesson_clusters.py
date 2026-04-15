#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from archonlib.lesson_clusters import (
    build_lesson_clusters,
    load_lesson_records,
    render_lesson_clusters_markdown,
    write_lesson_cluster_artifacts,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cluster AutoArchon lesson-records into category/theorem/action hotspots.")
    parser.add_argument("--campaign-root", help="Optional campaign root; loads final/postmortem lesson-records.jsonl when present")
    parser.add_argument("--input", action="append", default=[], help="Explicit lesson-records.jsonl input path (repeatable)")
    parser.add_argument("--top", type=int, default=10, help="Max rows per hotspot table")
    parser.add_argument("--markdown", action="store_true", help="Render Markdown to stdout instead of JSON")
    parser.add_argument("--output", help="Optional path to write the rendered stdout payload")
    parser.add_argument(
        "--write-default-files",
        action="store_true",
        help="When using --campaign-root, also write lesson-clusters.json/.md next to each discovered lesson-records.jsonl",
    )
    return parser.parse_args()


def _discover_inputs(args: argparse.Namespace) -> list[Path]:
    inputs = [Path(item).resolve() for item in args.input]
    if args.campaign_root:
        campaign_root = Path(args.campaign_root).resolve()
        for rel_path in (
            "reports/final/lessons/lesson-records.jsonl",
            "reports/postmortem/lessons/lesson-records.jsonl",
        ):
            candidate = campaign_root / rel_path
            if candidate.exists():
                inputs.append(candidate)
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in inputs:
        if path not in seen:
            unique.append(path)
            seen.add(path)
    return unique


def main() -> int:
    args = parse_args()
    inputs = _discover_inputs(args)
    if not inputs:
        raise SystemExit("No lesson-records.jsonl inputs found.")

    records = load_lesson_records(inputs)
    payload = build_lesson_clusters(records, source_paths=[str(path) for path in inputs], top_n=args.top)
    rendered = render_lesson_clusters_markdown(payload) if args.markdown else json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output:
        output_path = Path(args.output).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered, encoding="utf-8")
    if args.write_default_files:
        for input_path in inputs:
            write_lesson_cluster_artifacts(
                input_path.parent,
                records=load_lesson_records([input_path]),
                source_paths=[str(input_path)],
                top_n=args.top,
            )
    sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
