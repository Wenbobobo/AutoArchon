#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from archonlib.storage import (
    build_retention_report,
    build_storage_report,
    prune_storage_candidates,
    render_retention_report_markdown,
    render_storage_report_markdown,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit AutoArchon storage usage and optionally prune cache-heavy run artifacts.")
    parser.add_argument("--root", required=True, help="Root directory to scan, for example /path/to/math or /path/to/math/runs")
    parser.add_argument("--retention", action="store_true", help="Render a top-level retention/archival audit instead of the cache-centric storage report")
    parser.add_argument("--markdown", action="store_true", help="Render a markdown summary instead of JSON")
    parser.add_argument("--limit", type=int, default=20, help="Max candidate rows to render in markdown output")
    parser.add_argument("--prune-workspace-lake", action="store_true", help="Select inactive run workspace/.lake caches for pruning")
    parser.add_argument("--prune-broken-prewarm", action="store_true", help="Select .lake.prewarm-* directories for pruning")
    parser.add_argument("--execute", action="store_true", help="Actually delete the selected candidates")
    return parser.parse_args()


def render_prune_markdown(payload: dict[str, object], *, root: Path, limit: int) -> str:
    lines = [
        "# Storage Prune",
        "",
        f"- Root: `{payload.get('root', root)}`",
        f"- Execute: `{payload.get('execute', False)}`",
        f"- Selected candidates: `{payload.get('selectedCount', 0)}`",
        f"- Reclaimed bytes: `{payload.get('reclaimedBytes', 0)}`",
        "",
        "## Selected Candidates",
        "",
    ]
    selected = payload.get("selected", [])
    if isinstance(selected, list) and selected:
        for row in selected[:limit]:
            if not isinstance(row, dict):
                continue
            lines.append(
                f"- `{row.get('kind')}` `{row.get('path')}` size=`{row.get('size_bytes', 0)}` "
                f"safe=`{row.get('safe_to_prune')}` reason=`{row.get('reason')}`"
            )
    else:
        lines.append("- none")
    lines.extend(["", "## Post-Prune Report", ""])
    lines.append(render_storage_report_markdown(build_storage_report(root), limit=limit).rstrip())
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    root = Path(args.root).resolve()
    if args.prune_workspace_lake or args.prune_broken_prewarm:
        payload = prune_storage_candidates(
            root,
            prune_workspace_lake=args.prune_workspace_lake,
            prune_broken_prewarm=args.prune_broken_prewarm,
            execute=args.execute,
        )
        rendered = (
            render_prune_markdown(payload, root=root, limit=args.limit)
            if args.markdown and args.execute
            else render_storage_report_markdown(
                {
                    "root": payload["root"],
                    "workspaceLakeCount": 0,
                    "legacyWorkspaceLakeCount": 0,
                    "brokenPrewarmCount": 0,
                    "reclaimableBytes": payload["reclaimedBytes"],
                    "reclaimableCount": payload["selectedCount"],
                    "staleActiveLeaseCount": 0,
                    "protectedActiveCount": 0,
                    "topLevel": [],
                    "candidates": payload["selected"],
                },
                limit=args.limit,
            )
            if args.markdown
            else json.dumps(payload, indent=2, sort_keys=True) + "\n"
        )
    elif args.retention:
        payload = build_retention_report(root)
        rendered = render_retention_report_markdown(payload, limit=args.limit) if args.markdown else json.dumps(payload, indent=2, sort_keys=True) + "\n"
    else:
        payload = build_storage_report(root)
        rendered = render_storage_report_markdown(payload, limit=args.limit) if args.markdown else json.dumps(payload, indent=2, sort_keys=True) + "\n"
    sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
