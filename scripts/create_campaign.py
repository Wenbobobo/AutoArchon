#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from archonlib.campaign import create_campaign


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a campaign root with isolated runs and teacher launch assets.")
    parser.add_argument("--source-root", required=True, help="Path to the immutable benchmark or project source")
    parser.add_argument("--campaign-root", required=True, help="Directory to create for the campaign")
    parser.add_argument(
        "--run-spec",
        action="append",
        default=[],
        help='Inline JSON run spec, for example: {"id":"teacher-a","objective_regex":"^FATEM/1\\\\.lean$","objective_limit":1}',
    )
    parser.add_argument("--run-spec-file", help="JSON file containing an array of run spec objects")
    parser.add_argument("--reuse-lake-from", help="Optional project root or .lake directory to reuse as workspace/.lake")
    parser.add_argument("--teacher-model", default="gpt-5.4", help="Teacher model passed to codex exec")
    parser.add_argument("--teacher-reasoning-effort", default="xhigh", help="Teacher reasoning effort")
    parser.add_argument(
        "--teacher-scope-policy",
        default="single_file_micro_shard",
        help="Human-readable default policy recorded in the campaign manifest",
    )
    parser.add_argument("--plan-timeout-seconds", type=int, default=180, help="Default supervised plan timeout")
    parser.add_argument("--prover-timeout-seconds", type=int, default=240, help="Default supervised prover timeout")
    parser.add_argument("--prover-idle-seconds", type=int, default=90, help="Default supervisor idle cutoff")
    return parser.parse_args()


def _load_run_specs(args: argparse.Namespace) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    if args.run_spec_file:
        raw = json.loads(Path(args.run_spec_file).read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise ValueError("--run-spec-file must contain a JSON array")
        payloads.extend(item for item in raw if isinstance(item, dict))
    for raw in args.run_spec:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("--run-spec must be a JSON object")
        payloads.append(payload)
    if not payloads:
        raise ValueError("at least one run spec is required")
    return payloads


def main() -> int:
    args = parse_args()
    manifest = create_campaign(
        archon_root=ROOT,
        source_root=Path(args.source_root),
        campaign_root=Path(args.campaign_root),
        run_specs=_load_run_specs(args),
        reuse_lake_from=Path(args.reuse_lake_from) if args.reuse_lake_from else None,
        teacher_model=args.teacher_model,
        teacher_reasoning_effort=args.teacher_reasoning_effort,
        teacher_scope_policy=args.teacher_scope_policy,
        plan_timeout_seconds=args.plan_timeout_seconds,
        prover_timeout_seconds=args.prover_timeout_seconds,
        prover_idle_seconds=args.prover_idle_seconds,
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
