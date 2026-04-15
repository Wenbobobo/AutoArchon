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
from archonlib.operator_surfaces import build_resolved_spec, write_operator_intake_bundle


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Write a reviewed operator-intake draft for mission-brief.md, launch-spec.resolved.json, and operator-journal.md."
    )
    parser.add_argument("--repo-root", default=str(ROOT), help="AutoArchon repository root used for validation")
    parser.add_argument("--campaign-root", required=True, help="Campaign root to create or refresh")
    parser.add_argument("--source-root", required=True, help="Lean source project root")
    parser.add_argument("--objective", required=True, help="Plain-language objective for the operator mission brief")
    parser.add_argument(
        "--campaign-mode",
        choices=("benchmark_faithful", "formalization", "open_problem"),
        default="benchmark_faithful",
        help="Campaign intent; controls default preload policy",
    )
    parser.add_argument("--template", help="Optional tracked template reference to record in the mission brief")
    parser.add_argument("--reuse-lake-from", help="Optional warmed project root; defaults to --source-root")
    parser.add_argument("--match-regex", help="Optional objective regex used for planShards.matchRegex")
    parser.add_argument("--shard-size", type=int, default=8, help="Planned shard size")
    parser.add_argument("--run-id-prefix", default="teacher", help="Planned run id prefix")
    parser.add_argument("--run-id-mode", choices=("index", "file_stem"), default="index", help="Planned run id mode")
    parser.add_argument("--model", default="gpt-5.4", help="Teacher/watchdog model")
    parser.add_argument("--reasoning-effort", default="xhigh", help="Teacher/watchdog reasoning effort")
    parser.add_argument("--success-criterion", action="append", default=[], help="Success criterion bullet (repeatable)")
    parser.add_argument("--acceptable-blocker", action="append", default=[], help="Acceptable blocker/output bullet (repeatable)")
    parser.add_argument("--constraint", action="append", default=[], help="Constraint bullet (repeatable)")
    parser.add_argument("--watch-item", action="append", default=[], help="Watch-item bullet (repeatable)")
    parser.add_argument(
        "--preload-historical-routes",
        dest="preload_historical_routes",
        action="store_true",
        help="Force historical-route preloading on",
    )
    parser.add_argument(
        "--no-preload-historical-routes",
        dest="preload_historical_routes",
        action="store_false",
        help="Force historical-route preloading off",
    )
    parser.set_defaults(preload_historical_routes=None)
    parser.add_argument("--force", action="store_true", help="Overwrite mission/spec surfaces if they already exist")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    campaign_root = Path(args.campaign_root).resolve()
    source_root = Path(args.source_root).resolve()
    reuse_lake_from = Path(args.reuse_lake_from).resolve() if args.reuse_lake_from else None
    repo_root = Path(args.repo_root).resolve()

    resolved_spec = build_resolved_spec(
        campaign_root=campaign_root,
        source_root=source_root,
        campaign_mode=args.campaign_mode,
        objective_regex=args.match_regex,
        shard_size=args.shard_size,
        run_id_prefix=args.run_id_prefix,
        run_id_mode=args.run_id_mode,
        teacher_model=args.model,
        teacher_reasoning_effort=args.reasoning_effort,
        reuse_lake_from=reuse_lake_from,
        preload_historical_routes=args.preload_historical_routes,
    )
    success_criteria = args.success_criterion or [
        "Produce validation-backed accepted proofs or accepted blockers for the scoped targets.",
    ]
    acceptable_blockers = args.acceptable_blocker or [
        "Validation-backed blocker notes are acceptable when the theorem is false, underspecified, or infrastructure-complete progress still proves impossible.",
    ]
    constraints = args.constraint or [
        "Do not mutate `source/`.",
        "Accept only exported artifacts backed by validation.",
        "Keep teacher scopes disjoint.",
    ]
    watch_items = args.watch_item or [
        "Provider instability, theorem mutation, repeated no-progress loops, or launch conflicts.",
    ]
    surfaces = write_operator_intake_bundle(
        campaign_root,
        source_root=source_root,
        objective=args.objective,
        campaign_mode=args.campaign_mode,
        success_criteria=success_criteria,
        acceptable_blockers=acceptable_blockers,
        constraints=constraints,
        watch_items=watch_items,
        resolved_spec=resolved_spec,
        entrypoint="autoarchon-init-operator-intake",
        note="reviewed intake scaffold generated before interactive operator launch",
        spec_reference=str(Path(args.template).resolve()) if args.template else None,
        force=args.force,
    )
    validation = validate_launch_contract(campaign_root, repo_root=repo_root, strict=False)
    payload = {
        "campaignRoot": str(campaign_root),
        "sourceRoot": str(source_root),
        "resolvedSpec": resolved_spec,
        "operatorSurfaces": surfaces,
        "launchContractValidation": validation,
    }
    sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
