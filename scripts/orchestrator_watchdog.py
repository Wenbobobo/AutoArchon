#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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
        "--bootstrap-launch-after-seconds",
        type=int,
        default=45,
        help="If the campaign remains entirely queued for this long, let the watchdog launch queued teachers deterministically",
    )
    parser.add_argument("--max-restarts", type=int, default=3, help="Maximum orchestrator restarts or resumes before failing")
    parser.add_argument("--no-finalize", action="store_true", help="Do not run finalize_campaign after terminal closure")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    campaign_root = Path(args.campaign_root).resolve()
    control_root = campaign_root / "control"
    control_root.mkdir(parents=True, exist_ok=True)
    prompt_path = Path(args.prompt_file).resolve() if args.prompt_file else control_root / "orchestrator-prompt.txt"
    if not args.prompt_file:
        prompt_path.write_text(
            build_default_orchestrator_prompt(archon_root=ROOT, campaign_root=campaign_root),
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
        bootstrap_launch_after_seconds=args.bootstrap_launch_after_seconds,
        max_restarts=args.max_restarts,
        finalize_on_terminal=not args.no_finalize,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
