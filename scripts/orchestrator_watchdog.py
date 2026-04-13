#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from archonlib.campaign import ensure_campaign_control_root
from archonlib.orchestrator_watchdog import build_default_orchestrator_prompt, run_watchdog


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launch or resume an Archon orchestrator session with campaign-level stall recovery.")
    parser.add_argument("--campaign-root", required=True, help="Campaign directory created by scripts/create_campaign.py")
    parser.add_argument("--prompt-file", help="Optional orchestrator prompt file. A default prompt is written when omitted.")
    parser.add_argument("--model", default="gpt-5.4", help="Codex model for the orchestrator session")
    parser.add_argument("--reasoning-effort", default="xhigh", help="Codex reasoning effort")
    parser.add_argument("--poll-seconds", type=int, default=30, help="Campaign-status polling interval")
    parser.add_argument("--stall-seconds", type=int, default=300, help="Restart the orchestrator if campaign fingerprints stop changing for this long")
    parser.add_argument(
        "--owner-silence-seconds",
        type=int,
        default=1200,
        help="Restart the orchestrator only after the owner stops emitting logs for this long",
    )
    parser.add_argument(
        "--bootstrap-launch-after-seconds",
        type=int,
        default=45,
        help="If the campaign remains entirely queued for this long, let the watchdog launch queued teachers deterministically",
    )
    parser.add_argument("--max-restarts", type=int, default=3, help="Maximum orchestrator restarts or resumes before failing")
    parser.add_argument(
        "--max-active-launches",
        type=int,
        default=2,
        help="Maximum number of detached teacher launches allowed in flight at once",
    )
    parser.add_argument(
        "--launch-batch-size",
        type=int,
        default=1,
        help="Maximum number of automatic recoveries or launches to dispatch in one watchdog tick",
    )
    parser.add_argument(
        "--launch-cooldown-seconds",
        type=int,
        default=90,
        help="Minimum cooldown between detached teacher relaunches for the same run",
    )
    parser.add_argument("--no-finalize", action="store_true", help="Do not run finalize_campaign after terminal closure")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    campaign_root = Path(args.campaign_root).resolve()
    control_root = ensure_campaign_control_root(
        campaign_root,
        owner_mode="orchestrator",
        watchdog_enabled=True,
        manager_enabled=False,
        owner_entrypoint="autoarchon-orchestrator-watchdog",
    )
    prompt_path = Path(args.prompt_file).resolve() if args.prompt_file else control_root / "orchestrator-prompt.txt"
    if not args.prompt_file:
        prompt_path.write_text(
            build_default_orchestrator_prompt(
                archon_root=ROOT,
                campaign_root=campaign_root,
                max_active_launches=args.max_active_launches,
                launch_batch_size=args.launch_batch_size,
            ),
            encoding="utf-8",
        )

    result = run_watchdog(
        archon_root=ROOT,
        campaign_root=campaign_root,
        prompt_path=prompt_path,
        state_path=control_root / "orchestrator-watchdog.json",
        log_path=control_root / "orchestrator-watchdog.log",
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        poll_seconds=args.poll_seconds,
        stall_seconds=args.stall_seconds,
        owner_silence_seconds=args.owner_silence_seconds,
        bootstrap_launch_after_seconds=args.bootstrap_launch_after_seconds,
        max_restarts=args.max_restarts,
        max_active_launches=args.max_active_launches,
        launch_batch_size=args.launch_batch_size,
        launch_cooldown_seconds=args.launch_cooldown_seconds,
        finalize_on_terminal=not args.no_finalize,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    status = result.get("watchdogStatus")
    if status == "degraded":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
