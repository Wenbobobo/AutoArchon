#!/usr/bin/env python3

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render a paste-ready prompt for an interactive AutoArchon campaign-operator session."
    )
    parser.add_argument("--repo-root", required=True, help="AutoArchon repository root")
    parser.add_argument("--source-root", required=True, help="Benchmark or source project root")
    parser.add_argument("--campaign-root", required=True, help="Campaign root to create or resume")
    parser.add_argument("--reuse-lake-from", help="Optional warmed project or .lake cache root; defaults to --source-root")
    parser.add_argument("--template", help="Optional tracked launch template path")
    parser.add_argument("--match-regex", help="Optional regex used to scope objectives")
    parser.add_argument("--shard-size", type=int, default=8, help="Preferred shard size for new campaigns")
    parser.add_argument("--run-id-mode", choices=("index", "file_stem"), default="index", help="Preferred run id mode")
    parser.add_argument("--run-id-prefix", default="teacher", help="Preferred run id prefix")
    parser.add_argument("--model", default="gpt-5.4", help="Preferred operator/watchdog model")
    parser.add_argument("--reasoning-effort", default="xhigh", help="Preferred operator/watchdog reasoning effort")
    parser.add_argument("--objective", help="Optional one-line objective summary")
    parser.add_argument("--output", help="Optional file path to also write the rendered prompt")
    return parser.parse_args()


def _line(label: str, value: str | None) -> str:
    return f"{label}: {value or '(fill me)'}"


def render_prompt(args: argparse.Namespace) -> str:
    repo_root = str(Path(args.repo_root).resolve())
    source_root = str(Path(args.source_root).resolve())
    campaign_root = str(Path(args.campaign_root).resolve())
    reuse_lake_from = str(Path(args.reuse_lake_from).resolve()) if args.reuse_lake_from else source_root
    helper_env_file = str((Path(args.repo_root).resolve() / "examples" / "helper.env"))

    mission_lines = [
        "Mission:",
        "- interpret the actual user objective and write or refresh `control/mission-brief.md` before launching anything",
        "- write or refresh `control/launch-spec.resolved.json` so it matches the intended campaign root, scope, model, and watchdog policy",
        "- append the launch decision and expected next checks to `control/operator-journal.md`",
        f"- keep helper enabled by default; confirm `{helper_env_file}` or equivalent exported helper env is loaded before bootstrap unless the run contract explicitly forbids it",
        "- if the campaign root does not exist yet, bootstrap it safely",
        "- if it already exists, treat it as exclusive scope and do not widen it without explicit instruction",
        "- launch or resume the watchdog only after the three control files are current",
        "- use `autoarchon-campaign-status` and `autoarchon-campaign-overview` as the primary truth surfaces",
        "- prefer deterministic single-run recovery commands over ad hoc interventions",
        "- record every recovery, archive, scope change, and finalization decision in `control/operator-journal.md`",
        "- finalize only validation-backed proofs and blocker notes",
    ]
    if args.template:
        mission_lines.insert(
            4,
            f"- prefer the tracked template `{Path(args.template).resolve()}` as the starting point for the resolved spec unless the real task requires a custom launch contract",
        )
    if args.objective:
        mission_lines.insert(0, f"Objective: {args.objective}")

    prompt = "\n".join(
        [
            "Use $archon-orchestrator to own this AutoArchon campaign.",
            "",
            _line("Repository root", repo_root),
            _line("Source root", source_root),
            _line("Campaign root", campaign_root),
            _line("Reuse lake from", reuse_lake_from),
            _line("Helper env file", helper_env_file),
            _line("Tracked template", str(Path(args.template).resolve()) if args.template else None),
            _line("Match regex", args.match_regex),
            _line("Shard size", str(args.shard_size)),
            _line("Run id mode", args.run_id_mode),
            _line("Run id prefix", args.run_id_prefix),
            _line("Preferred model", args.model),
            _line("Preferred reasoning effort", args.reasoning_effort),
            "",
            *mission_lines,
            "",
        ]
    )
    return prompt


def main() -> int:
    args = parse_args()
    prompt = render_prompt(args)
    if args.output:
        output_path = Path(args.output).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(prompt, encoding="utf-8")
    sys.stdout.write(prompt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
