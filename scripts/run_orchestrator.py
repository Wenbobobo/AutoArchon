#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from archonlib.campaign import (
    DEFAULT_HEARTBEAT_SECONDS,
    build_campaign_compare_report,
    build_orchestrator_prompt,
    campaign_is_terminal,
    collect_campaign_status,
    ensure_campaign_control_root,
    finalize_campaign,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a campaign-owning Codex orchestrator in repeated fresh sessions until the campaign reaches terminal states."
    )
    parser.add_argument("--campaign-root", required=True, help="Campaign directory created by scripts/create_campaign.py")
    parser.add_argument("--prompt-file", help="Optional orchestrator prompt file; defaults to campaign/control/orchestrator-prompt.txt")
    parser.add_argument("--model", default="gpt-5.4", help="Codex model for each orchestrator attempt")
    parser.add_argument("--reasoning-effort", default="xhigh", help="Codex reasoning effort")
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=0,
        help="Maximum fresh orchestrator attempts before giving up; 0 means unlimited",
    )
    parser.add_argument("--sleep-seconds", type=int, default=15, help="Delay between attempts when the campaign is not terminal")
    parser.add_argument(
        "--heartbeat-seconds",
        type=int,
        default=DEFAULT_HEARTBEAT_SECONDS,
        help="Recent activity threshold used while refreshing campaign status",
    )
    parser.add_argument(
        "--finalize-on-terminal",
        action="store_true",
        help="Build compare/final reports automatically once every run is terminal",
    )
    return parser.parse_args()


def _default_prompt_path(campaign_root: Path) -> Path:
    return campaign_root / "control" / "orchestrator-prompt.txt"


def _write_default_prompt(prompt_path: Path, *, campaign_root: Path) -> None:
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(
        build_orchestrator_prompt(archon_root=ROOT, campaign_root=campaign_root) + "\n",
        encoding="utf-8",
    )


def _attempt_paths(campaign_root: Path, attempt: int) -> tuple[Path, Path]:
    log_root = campaign_root / "control" / "orchestrator-attempts"
    log_root.mkdir(parents=True, exist_ok=True)
    return (
        log_root / f"attempt-{attempt:03d}.jsonl",
        log_root / f"attempt-{attempt:03d}.raw.jsonl",
    )


def _record_attempt(
    campaign_root: Path,
    *,
    attempt: int,
    returncode: int,
    before: dict,
    after: dict,
    log_path: Path,
    raw_log_path: Path,
) -> None:
    index_path = campaign_root / "control" / "orchestrator-attempts" / "attempt-index.jsonl"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "attempt": attempt,
        "returncode": returncode,
        "beforeCounts": before.get("counts", {}),
        "afterCounts": after.get("counts", {}),
        "logPath": log_path.relative_to(campaign_root).as_posix(),
        "rawLogPath": raw_log_path.relative_to(campaign_root).as_posix(),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    with index_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def _run_once(prompt_path: Path, *, attempt: int, campaign_root: Path, args: argparse.Namespace) -> int:
    log_path, raw_log_path = _attempt_paths(campaign_root, attempt)
    before = collect_campaign_status(campaign_root, heartbeat_seconds=args.heartbeat_seconds)
    command = [
        "uv",
        "run",
        "--directory",
        str(ROOT),
        "autoarchon-codex-exec",
        "--cwd",
        str(ROOT),
        "--model",
        args.model,
        "--prompt-file",
        str(prompt_path),
        "--log-path",
        str(log_path),
        "--raw-log-path",
        str(raw_log_path),
        "--extra-args",
        f"-c model_reasoning_effort={args.reasoning_effort}",
    ]
    result = subprocess.run(
        command,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    after = collect_campaign_status(campaign_root, heartbeat_seconds=args.heartbeat_seconds)
    build_campaign_compare_report(campaign_root, heartbeat_seconds=args.heartbeat_seconds)
    _record_attempt(
        campaign_root,
        attempt=attempt,
        returncode=result.returncode,
        before=before,
        after=after,
        log_path=log_path,
        raw_log_path=raw_log_path,
    )
    return result.returncode


def main() -> int:
    args = parse_args()
    campaign_root = Path(args.campaign_root).resolve()
    if not campaign_root.exists():
        raise SystemExit(f"campaign root not found: {campaign_root}")
    ensure_campaign_control_root(
        campaign_root,
        owner_mode="orchestrator",
        watchdog_enabled=False,
        manager_enabled=False,
        owner_entrypoint="autoarchon-run-orchestrator",
    )

    prompt_path = Path(args.prompt_file).resolve() if args.prompt_file else _default_prompt_path(campaign_root)
    if not prompt_path.exists():
        _write_default_prompt(prompt_path, campaign_root=campaign_root)

    attempt = 1
    while True:
        status = collect_campaign_status(campaign_root, heartbeat_seconds=args.heartbeat_seconds)
        if campaign_is_terminal(status):
            build_campaign_compare_report(campaign_root, heartbeat_seconds=args.heartbeat_seconds)
            if args.finalize_on_terminal:
                finalize_campaign(campaign_root, heartbeat_seconds=args.heartbeat_seconds)
            return 0
        if args.max_attempts and attempt > args.max_attempts:
            return 1

        returncode = _run_once(prompt_path, attempt=attempt, campaign_root=campaign_root, args=args)
        status = collect_campaign_status(campaign_root, heartbeat_seconds=args.heartbeat_seconds)
        if campaign_is_terminal(status):
            build_campaign_compare_report(campaign_root, heartbeat_seconds=args.heartbeat_seconds)
            if args.finalize_on_terminal:
                finalize_campaign(campaign_root, heartbeat_seconds=args.heartbeat_seconds)
            return 0
        if args.max_attempts and attempt >= args.max_attempts:
            return returncode if returncode != 0 else 1
        attempt += 1
        time.sleep(max(0, args.sleep_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
