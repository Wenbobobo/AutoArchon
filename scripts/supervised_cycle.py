#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from archonlib.supervisor import (
    collect_changed_files,
    collect_header_drifts,
    list_runtime_process_lines,
    read_allowed_files,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one supervised Archon cycle and record policy results.")
    parser.add_argument("--workspace", required=True, help="Workspace root passed to Archon")
    parser.add_argument("--source", required=True, help="Immutable source root for header fidelity checks")
    parser.add_argument(
        "--archon-loop",
        default=str(ROOT / "archon-loop.sh"),
        help="Path to archon-loop.sh or a compatible wrapper",
    )
    parser.add_argument("--max-iterations", type=int, default=1, help="Iterations to pass through to archon-loop.sh")
    parser.add_argument("--max-parallel", type=int, default=4, help="Parallel prover limit")
    parser.add_argument("--skip-process-check", action="store_true", help="Skip the pre-cycle ps scan")
    parser.add_argument("--dry-run", action="store_true", help="Pass --dry-run through to archon-loop.sh")
    parser.add_argument("--no-review", action="store_true", help="Pass --no-review through to archon-loop.sh")
    return parser.parse_args()


def _append_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(content)


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _run_process_check(skip: bool) -> list[str]:
    if skip:
        return []
    result = subprocess.run(
        ["ps", "-ef"],
        capture_output=True,
        text=True,
        check=False,
    )
    return list_runtime_process_lines(result.stdout)


def _run_archon_loop(args: argparse.Namespace) -> subprocess.CompletedProcess[str]:
    command = [
        "bash",
        str(Path(args.archon_loop).resolve()),
        "--max-iterations",
        str(args.max_iterations),
        "--max-parallel",
        str(args.max_parallel),
    ]
    if args.dry_run:
        command.append("--dry-run")
    if args.no_review:
        command.append("--no-review")
    command.append(str(Path(args.workspace).resolve()))
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        cwd=str(ROOT),
    )


def main() -> int:
    args = parse_args()
    workspace = Path(args.workspace).resolve()
    source = Path(args.source).resolve()
    supervisor_dir = workspace / ".archon" / "supervisor"
    hot_notes_path = supervisor_dir / "HOT_NOTES.md"
    ledger_path = supervisor_dir / "LEDGER.md"
    violations_path = supervisor_dir / "violations.jsonl"

    started_at = datetime.now(timezone.utc).isoformat()
    allowed_files = read_allowed_files(workspace)
    process_lines = _run_process_check(args.skip_process_check)
    loop_result = _run_archon_loop(args)
    drifts = collect_header_drifts(source, workspace, allowed_files=allowed_files or None)
    changed_files = collect_changed_files(source, workspace, allowed_files=allowed_files or None)
    blocker_notes = sorted(path.name for path in (workspace / ".archon" / "task_results").glob("*.md"))

    events: list[dict[str, object]] = []
    for line in process_lines:
        events.append(
            {
                "event": "runtime_process_present",
                "line": line,
            }
        )
    for drift in drifts:
        events.append(drift.to_event())

    if not changed_files and not blocker_notes:
        events.append({"event": "no_progress"})

    status = "clean"
    if loop_result.returncode != 0:
        status = "loop_failed"
    if any(event["event"] == "header_mutation" for event in events):
        status = "policy_violation"

    if events:
        _append_text(
            violations_path,
            "".join(json.dumps(event, sort_keys=True) + "\n" for event in events),
        )

    hot_notes = [
        "# Supervisor Hot Notes",
        "",
        "Read this before touching the run.",
        "",
        f"- Status: {status}",
        f"- Started at: {started_at}",
        f"- Workspace: {workspace}",
        f"- Source: {source}",
        f"- Allowed files: {', '.join(allowed_files) if allowed_files else '(all .lean files)'}",
        f"- Loop exit code: {loop_result.returncode}",
        f"- Changed files: {', '.join(changed_files) if changed_files else '(none)'}",
        f"- Blocker notes: {', '.join(blocker_notes) if blocker_notes else '(none)'}",
        f"- Policy violations: {len([event for event in events if event['event'] == 'header_mutation'])}",
    ]
    for drift in drifts:
        hot_notes.append(f"- Violation: {drift.rel_path}::{drift.declaration_name} -> {drift.mutation_class}")
    if process_lines:
        hot_notes.append(f"- Runtime processes already present: {len(process_lines)}")
    _write_text(hot_notes_path, "\n".join(hot_notes) + "\n")

    ledger_lines = [
        f"## Cycle {started_at}",
        "",
        f"- Status: `{status}`",
        f"- Loop exit code: `{loop_result.returncode}`",
        f"- Changed files: `{', '.join(changed_files) if changed_files else '(none)'}`",
        f"- Blocker notes: `{', '.join(blocker_notes) if blocker_notes else '(none)'}`",
        f"- Policy events: `{len(events)}`",
        "",
    ]
    _append_text(ledger_path, "\n".join(ledger_lines))

    print(f"status={status}")
    print(f"changed_files={len(changed_files)}")
    print(f"blocker_notes={len(blocker_notes)}")
    print(f"policy_events={len(events)}")

    if status == "policy_violation":
        return 2
    if status == "loop_failed":
        return loop_result.returncode or 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
