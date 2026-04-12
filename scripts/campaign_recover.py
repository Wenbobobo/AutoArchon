#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from archonlib.campaign import (
    DEFAULT_HEARTBEAT_SECONDS,
    collect_campaign_status,
    execute_run_recovery,
)


RECOVERABLE_ACTIONS = {"launch_teacher", "relaunch_teacher", "recovery_only"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plan or execute deterministic recovery actions for Archon campaign runs.")
    parser.add_argument("--campaign-root", required=True, help="Campaign directory created by scripts/create_campaign.py")
    parser.add_argument("--run-id", action="append", default=[], help="Specific run id to recover; may be repeated")
    parser.add_argument(
        "--all-recoverable",
        action="store_true",
        help="Select every run whose recommended action is launch_teacher, relaunch_teacher, or recovery_only",
    )
    parser.add_argument(
        "--action",
        default="auto",
        choices=["auto", "launch_teacher", "relaunch_teacher", "recovery_only"],
        help="Override the recommended action for selected runs",
    )
    parser.add_argument("--execute", action="store_true", help="Execute the selected recovery actions")
    parser.add_argument(
        "--foreground-launch",
        action="store_true",
        help="When launching teachers, run the generated launch script in the foreground instead of detached mode",
    )
    parser.add_argument(
        "--heartbeat-seconds",
        type=int,
        default=DEFAULT_HEARTBEAT_SECONDS,
        help="Recent activity threshold used while refreshing campaign status",
    )
    parser.add_argument(
        "--changed-file-verify-template",
        help="Optional verifier shell template passed through to supervised_cycle.py during recovery_only",
    )
    return parser.parse_args()


def _select_run_ids(args: argparse.Namespace) -> list[str]:
    if args.run_id:
        return list(dict.fromkeys(args.run_id))
    if not args.all_recoverable:
        raise ValueError("specify at least one --run-id or use --all-recoverable")

    status = collect_campaign_status(Path(args.campaign_root), heartbeat_seconds=args.heartbeat_seconds)
    run_ids: list[str] = []
    for run in status["runs"]:
        if not isinstance(run, dict):
            continue
        recovery = run.get("recommendedRecovery")
        if not isinstance(recovery, dict):
            continue
        action = recovery.get("action")
        if action in RECOVERABLE_ACTIONS and isinstance(run.get("runId"), str):
            run_ids.append(str(run["runId"]))
    return run_ids


def main() -> int:
    args = parse_args()
    run_ids = _select_run_ids(args)
    results = [
        execute_run_recovery(
            Path(args.campaign_root),
            run_id,
            action=args.action,
            execute=args.execute,
            detach_launch=not args.foreground_launch,
            heartbeat_seconds=args.heartbeat_seconds,
            changed_file_verify_template=args.changed_file_verify_template,
        )
        for run_id in run_ids
    ]
    payload: object = results[0] if len(results) == 1 and not args.all_recoverable else results
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
