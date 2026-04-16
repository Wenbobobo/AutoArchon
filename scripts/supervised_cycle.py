#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import signal
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from archonlib.supervisor import (
    collect_changed_files,
    collect_header_drifts,
    collect_meta_prover_errors,
    latest_iteration_meta,
    read_allowed_files,
)
from archonlib.helper_index import helper_index_entries, summarize_helper_index
from archonlib.lessons import write_lesson_artifact
from archonlib.project_state import build_task_pending_markdown, objective_for_file, stage_markdown
from archonlib.runtime_config import RuntimeConfig, load_runtime_config
from archonlib.validation import write_validation_artifacts


INFORMAL_NOTE_PATTERN = re.compile(r"\.archon/informal/[A-Za-z0-9._/\-]+\.md")
OBJECTIVE_REL_PATH_PATTERN = re.compile(r"(?:\*\*|`)([^*`\n]+\.lean)(?:\*\*|`)")
LEASE_SCHEMA_VERSION = 1
TERMINAL_LEASE_FIELDS = (
    "completedAt",
    "finalStatus",
    "lessonFile",
    "loopExitCode",
    "recoveryEvent",
    "validationFiles",
)
EXACT_ROUTE_MARKERS = (
    "exact compile-checked route",
    "exact compile-checked proof",
    "exact rewrite route",
    "expected proof shape",
    "shortest route",
)
BLOCKER_ROUTE_MARKERS = (
    "lean-validated blocker",
    "lean-validated obstruction",
    "false as written",
    "validated obstruction",
)
LEAN_CODE_BLOCK_PATTERN = re.compile(r"```(?:lean)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)
LEAN_PROOF_KEYWORDS = ("simpa", "rw", "exact", "refine", "apply", "convert", "aesop", "trivial")
HISTORICAL_ROUTES_FILE = ".archon/HISTORICAL_ROUTES.md"
HISTORICAL_ROUTES_MANIFEST_FILE = ".archon/supervisor/historical-routes.json"


def _env_float(name: str, default: float | None = None) -> float | None:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return float(raw)


def _env_int(name: str, default: int | None = None) -> int | None:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


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
    parser.add_argument("--plan-timeout-seconds", type=int, help="Set ARCHON_PLAN_TIMEOUT_SECONDS for this cycle")
    parser.add_argument("--prover-timeout-seconds", type=int, help="Set ARCHON_PROVER_TIMEOUT_SECONDS for this cycle")
    parser.add_argument("--review-timeout-seconds", type=int, help="Set ARCHON_REVIEW_TIMEOUT_SECONDS for this cycle")
    parser.add_argument("--skip-process-check", action="store_true", help="Skip the pre-cycle ps scan")
    parser.add_argument(
        "--recovery-only",
        action="store_true",
        help="Skip archon-loop and close out the current workspace state into validation, lessons, and supervisor artifacts",
    )
    parser.add_argument("--dry-run", action="store_true", help="Pass --dry-run through to archon-loop.sh")
    parser.add_argument("--no-review", action="store_true", help="Pass --no-review through to archon-loop.sh")
    parser.add_argument(
        "--prover-idle-seconds",
        type=float,
        default=_env_float("ARCHON_SUPERVISOR_PROVER_IDLE_SECONDS"),
        help="Kill the loop if prover activity stays idle for this many seconds",
    )
    parser.add_argument(
        "--monitor-poll-seconds",
        type=float,
        default=_env_float("ARCHON_SUPERVISOR_MONITOR_POLL_SECONDS", 5.0) or 5.0,
        help="Polling interval for supervisor runtime monitoring",
    )
    parser.add_argument(
        "--changed-file-verify-template",
        default=os.environ.get("ARCHON_SUPERVISOR_VERIFY_TEMPLATE"),
        help="Optional shell template used to verify changed files after an idle timeout; use {file} as placeholder",
    )
    parser.add_argument(
        "--tail-scope-objective-threshold",
        type=int,
        default=_env_int("ARCHON_SUPERVISOR_TAIL_SCOPE_OBJECTIVE_THRESHOLD", 0) or 0,
        help="When the current objective list has at most this many files, apply tail-scope runtime overrides",
    )
    parser.add_argument(
        "--tail-scope-prover-timeout-seconds",
        type=int,
        default=_env_int("ARCHON_SUPERVISOR_TAIL_SCOPE_PROVER_TIMEOUT_SECONDS"),
        help="Optional prover timeout override used when the current objective count is within the tail-scope threshold",
    )
    parser.add_argument(
        "--tail-scope-plan-timeout-seconds",
        type=int,
        default=_env_int("ARCHON_SUPERVISOR_TAIL_SCOPE_PLAN_TIMEOUT_SECONDS"),
        help="Optional plan timeout override used when the current objective count is within the tail-scope threshold",
    )
    parser.add_argument(
        "--preload-historical-routes",
        action="store_true",
        default=_env_flag("ARCHON_SUPERVISOR_PRELOAD_HISTORICAL_ROUTES", False),
        help=(
            "Preload accepted proof/blocker routes from finalized sibling campaigns into "
            ".archon/HISTORICAL_ROUTES.md before the cycle starts. This is useful for experience-reuse "
            "campaigns, but it is not benchmark-faithful."
        ),
    )
    return parser.parse_args()


def _append_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(content)


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _lease_path(workspace: Path) -> Path:
    return workspace / ".archon" / "supervisor" / "run-lease.json"


def _read_json(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _coerce_pid(value: object) -> int | None:
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.isdigit():
        parsed = int(value)
        return parsed if parsed > 0 else None
    return None


def _pid_is_live(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _update_lease(
    lease_path: Path,
    *,
    workspace: Path,
    source: Path,
    fields: dict[str, object],
    clear_fields: tuple[str, ...] = (),
) -> dict[str, object]:
    payload = _read_json(lease_path) or {}
    for key in clear_fields:
        payload.pop(key, None)
    payload.update(
        {
            "schemaVersion": LEASE_SCHEMA_VERSION,
            "workspace": str(workspace),
            "source": str(source),
            "updatedAt": _now_iso(),
            **fields,
        }
    )
    _write_text(lease_path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return payload


def _run_progress_paths(workspace: Path) -> tuple[Path, Path]:
    supervisor_dir = workspace / ".archon" / "supervisor"
    return supervisor_dir / "progress-summary.md", supervisor_dir / "progress-summary.json"


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _relative_to_workspace(path: Path, workspace: Path) -> str:
    try:
        return path.resolve().relative_to(workspace.resolve()).as_posix()
    except ValueError:
        return str(path)


def _current_campaign_root(workspace: Path) -> Path | None:
    for candidate in workspace.parents:
        if (candidate / "CAMPAIGN_MANIFEST.json").exists() and (candidate / "runs").is_dir():
            return candidate
    return None


def _historical_route_note_path(workspace: Path, rel_path: str, kind: str) -> Path:
    slug = rel_path.replace("/", "_").removesuffix(".lean").lower()
    suffix = "accepted_proof" if kind == "proof" else "accepted_blocker"
    return workspace / ".archon" / "informal" / "historical_routes" / f"{slug}_{suffix}.md"


def _historical_route_candidates(workspace: Path, rel_path: str) -> list[dict[str, object]]:
    campaign_root = _current_campaign_root(workspace)
    if campaign_root is None:
        return []
    campaigns_root = campaign_root.parent
    note_name = _task_result_name(rel_path)
    candidates: list[dict[str, object]] = []

    for other_campaign in sorted(campaigns_root.iterdir()):
        if not other_campaign.is_dir() or other_campaign == campaign_root:
            continue
        final_root = other_campaign / "reports" / "final"
        if not final_root.exists():
            continue

        proofs_root = final_root / "proofs"
        if proofs_root.exists():
            for artifact_path in proofs_root.rglob(rel_path):
                if not artifact_path.is_file():
                    continue
                rel_parts = artifact_path.relative_to(proofs_root).parts
                run_id = rel_parts[0] if rel_parts else "(unknown)"
                candidates.append(
                    {
                        "kind": "proof",
                        "artifactPath": artifact_path,
                        "campaignId": other_campaign.name,
                        "runId": run_id,
                        "mtime": artifact_path.stat().st_mtime,
                    }
                )

        blockers_root = final_root / "blockers"
        if blockers_root.exists():
            for artifact_path in blockers_root.rglob(note_name):
                if not artifact_path.is_file():
                    continue
                rel_parts = artifact_path.relative_to(blockers_root).parts
                run_id = rel_parts[0] if rel_parts else "(unknown)"
                candidates.append(
                    {
                        "kind": "blocker",
                        "artifactPath": artifact_path,
                        "campaignId": other_campaign.name,
                        "runId": run_id,
                        "mtime": artifact_path.stat().st_mtime,
                    }
                )

    candidates.sort(key=lambda item: (float(item["mtime"]), str(item["kind"])))
    return candidates


def _render_historical_route_note(
    *,
    rel_path: str,
    kind: str,
    campaign_id: str,
    run_id: str,
    artifact_path: Path,
    artifact_text: str,
) -> str:
    lines = [
        f"# Historical Accepted {'Proof' if kind == 'proof' else 'Blocker'} Route: {rel_path}",
        "",
        f"- Source campaign: `{campaign_id}`",
        f"- Source run: `{run_id}`",
        f"- Source artifact: `{artifact_path}`",
        "",
    ]
    if kind == "proof":
        lines.extend(
            [
                "Exact compile-checked route:",
                "",
                "```lean",
                artifact_text.rstrip(),
                "```",
            ]
        )
    else:
        lines.extend(
            [
                "Lean-validated blocker route: false as written.",
                "",
                artifact_text.rstrip(),
            ]
        )
    lines.append("")
    return "\n".join(lines)


def _write_historical_routes_summary(workspace: Path, records: list[dict[str, str]]) -> None:
    summary_path = workspace / HISTORICAL_ROUTES_FILE
    manifest_path = workspace / HISTORICAL_ROUTES_MANIFEST_FILE
    if not records:
        if summary_path.exists():
            summary_path.unlink()
        if manifest_path.exists():
            manifest_path.unlink()
        return

    lines = [
        "# Historical Routes",
        "",
        "This file is machine-generated by `autoarchon-supervised-cycle --preload-historical-routes`.",
        "It is intended for experience-reuse campaigns and is not benchmark-faithful evidence on its own.",
        "",
    ]
    for record in records:
        lines.append(f"## {record['relPath']}")
        if record["kind"] == "proof":
            lines.append(
                f"- Historical accepted proof route preloaded from `{record['sourceArtifact']}`."
            )
            lines.append(f"- Exact compile-checked route in `{record['noteRelPath']}`.")
        else:
            lines.append(
                f"- Historical accepted blocker route preloaded from `{record['sourceArtifact']}`."
            )
            lines.append(
                f"- Lean-validated blocker route: false as written. Notes in `{record['noteRelPath']}`."
            )
        lines.append("")
    _write_text(summary_path, "\n".join(lines))
    _write_text(
        manifest_path,
        json.dumps({"schemaVersion": 1, "records": records}, indent=2, sort_keys=True) + "\n",
    )


def _clear_historical_routes(workspace: Path) -> None:
    _write_historical_routes_summary(workspace, [])
    notes_root = workspace / ".archon" / "informal" / "historical_routes"
    if notes_root.exists():
        shutil.rmtree(notes_root)


def _seed_historical_routes(workspace: Path, *, allowed_files: list[str]) -> list[dict[str, str]]:
    _clear_historical_routes(workspace)
    if not allowed_files:
        return []

    records: list[dict[str, str]] = []
    for rel_path in allowed_files:
        candidates = _historical_route_candidates(workspace, rel_path)
        if not candidates:
            continue
        chosen = candidates[-1]
        artifact_path = Path(str(chosen["artifactPath"])).resolve()
        kind = str(chosen["kind"])
        campaign_id = str(chosen["campaignId"])
        run_id = str(chosen["runId"])
        artifact_text = artifact_path.read_text(encoding="utf-8", errors="replace")
        note_path = _historical_route_note_path(workspace, rel_path, kind)
        _write_text(
            note_path,
            _render_historical_route_note(
                rel_path=rel_path,
                kind=kind,
                campaign_id=campaign_id,
                run_id=run_id,
                artifact_path=artifact_path,
                artifact_text=artifact_text,
            ),
        )
        records.append(
            {
                "campaignId": campaign_id,
                "kind": kind,
                "noteRelPath": _relative_to_workspace(note_path, workspace),
                "relPath": rel_path,
                "runId": run_id,
                "sourceArtifact": str(artifact_path),
            }
        )

    _write_historical_routes_summary(workspace, records)
    return records


def _helper_note_dirs(workspace: Path, runtime_config: RuntimeConfig) -> list[Path]:
    candidates: list[Path] = []
    for notes_dir in (
        runtime_config.helper_plan.notes_dir,
        runtime_config.helper_prover.notes_dir,
    ):
        candidate = Path(notes_dir)
        if not candidate.is_absolute():
            candidate = workspace / candidate
        candidates.append(candidate)
    return [Path(item) for item in _dedupe_strings([str(path) for path in candidates])]


def _helper_note_files(workspace: Path, runtime_config: RuntimeConfig) -> list[Path]:
    note_files: list[Path] = []
    for note_dir in _helper_note_dirs(workspace, runtime_config):
        if not note_dir.exists():
            continue
        note_files.extend(path for path in note_dir.rglob("*") if path.is_file())
    return sorted(
        [Path(item) for item in _dedupe_strings([str(path) for path in note_files])],
        key=lambda path: path.stat().st_mtime if path.exists() else 0.0,
        reverse=True,
    )


def _parse_helper_note_metadata(path: Path, workspace: Path) -> dict[str, str]:
    metadata = {"path": _relative_to_workspace(path, workspace)}
    text = path.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines()[:16]:
        if not line.startswith("- "):
            continue
        body = line[2:]
        if ": `" not in body or not body.endswith("`"):
            continue
        raw_key, raw_value = body.split(": `", 1)
        key = raw_key.strip().lower().replace(" ", "")
        metadata[key] = raw_value[:-1]
    return metadata


def _helper_note_summary(workspace: Path, runtime_config: RuntimeConfig) -> dict[str, object]:
    note_files = _helper_note_files(workspace, runtime_config)
    recent_metadata = [_parse_helper_note_metadata(path, workspace) for path in note_files[:8]]
    counts_by_phase: dict[str, int] = {}
    counts_by_reason: dict[str, int] = {}
    counts_by_prompt_pack: dict[str, int] = {}

    for path in note_files:
        metadata = _parse_helper_note_metadata(path, workspace)
        phase = metadata.get("phase")
        reason = metadata.get("reason")
        prompt_pack = metadata.get("promptpack")
        if phase:
            counts_by_phase[phase] = counts_by_phase.get(phase, 0) + 1
        if reason:
            counts_by_reason[reason] = counts_by_reason.get(reason, 0) + 1
        if prompt_pack:
            counts_by_prompt_pack[prompt_pack] = counts_by_prompt_pack.get(prompt_pack, 0) + 1

    return {
        "noteCount": len(note_files),
        "recentNotes": [_relative_to_workspace(path, workspace) for path in note_files[:8]],
        "recentMetadata": recent_metadata,
        "countsByPhase": dict(sorted(counts_by_phase.items())),
        "countsByReason": dict(sorted(counts_by_reason.items())),
        "countsByPromptPack": dict(sorted(counts_by_prompt_pack.items())),
    }


def _helper_runtime_summary(workspace: Path, runtime_config: RuntimeConfig) -> dict[str, object]:
    note_summary = _helper_note_summary(workspace, runtime_config)
    index_summary = summarize_helper_index(helper_index_entries(workspace))
    return {
        **note_summary,
        **index_summary,
        "cooldownState": {
            "activeReasons": index_summary.get("cooldownActiveReasons", []),
        },
    }


def _task_result_summary(workspace: Path) -> dict[str, object]:
    task_results_root = workspace / ".archon" / "task_results"
    note_files = sorted(
        task_results_root.glob("*.md"),
        key=lambda path: path.stat().st_mtime if path.exists() else 0.0,
        reverse=True,
    )
    counts = {"resolved": 0, "blocker": 0, "other": 0}
    recent_results: list[dict[str, str]] = []
    for path in note_files:
        kind, _ = _classify_task_result_note(path)
        counts[kind] = counts.get(kind, 0) + 1
        if len(recent_results) < 8:
            recent_results.append({"path": path.name, "kind": kind})
    return {
        "counts": counts,
        "recentResults": recent_results,
    }


def _progress_bar(completed: int, total: int, *, width: int = 20) -> tuple[str, int]:
    if total <= 0:
        return "[" + "-" * width + "]", 0
    percent = int(round((completed / total) * 100))
    filled = max(0, min(width, int(round((percent / 100) * width))))
    return "[" + "#" * filled + "-" * (width - filled) + "]", percent


def _scope_targets(
    workspace: Path,
    *,
    allowed_files: list[str],
    runtime_overrides: dict[str, object],
    changed_files: list[str],
) -> tuple[list[str], str]:
    if allowed_files:
        return allowed_files, "run_scope"

    objective_files = runtime_overrides.get("objectiveFiles")
    if isinstance(objective_files, list):
        resolved = [str(item) for item in objective_files if isinstance(item, str) and item]
        if resolved:
            return _dedupe_strings(resolved), "planner_objectives"

    if changed_files:
        return _dedupe_strings(changed_files), "changed_files"

    validation_root = workspace / ".archon" / "validation"
    if validation_root.exists():
        rel_paths: list[str] = []
        for path in sorted(validation_root.glob("*.json")):
            payload = _read_json(path)
            rel_path = payload.get("relPath") if isinstance(payload, dict) else None
            if isinstance(rel_path, str) and rel_path:
                rel_paths.append(rel_path)
        if rel_paths:
            return _dedupe_strings(rel_paths), "validation_files"

    return [], "none"


def _validation_target_counts(workspace: Path, scope_targets: list[str]) -> dict[str, int]:
    counts = {
        "acceptedProofs": 0,
        "acceptedBlockers": 0,
        "rejectedTargets": 0,
        "pendingTargets": 0,
        "attentionTargets": 0,
    }
    validation_root = workspace / ".archon" / "validation"
    for rel_path in scope_targets:
        payload = _read_json(validation_root / _validation_filename(rel_path))
        if payload is None:
            counts["pendingTargets"] += 1
            continue

        acceptance_status = payload.get("acceptanceStatus")
        checks = payload.get("checks")
        workspace_changed = isinstance(checks, dict) and checks.get("workspaceChanged") is True
        blocker_notes = payload.get("blockerNotes")
        if acceptance_status == "accepted":
            if isinstance(blocker_notes, list) and blocker_notes and not workspace_changed:
                counts["acceptedBlockers"] += 1
            else:
                counts["acceptedProofs"] += 1
            continue
        if acceptance_status == "rejected":
            counts["rejectedTargets"] += 1
            continue
        if payload.get("validationStatus") in {"attention", "failed"}:
            counts["attentionTargets"] += 1
            continue
        counts["pendingTargets"] += 1
    return counts


def _active_prover_rows(latest_meta: dict[str, object] | None) -> list[dict[str, str]]:
    if not isinstance(latest_meta, dict):
        return []
    provers = latest_meta.get("provers")
    if not isinstance(provers, dict):
        return []
    rows: list[dict[str, str]] = []
    for prover_id, payload in sorted(provers.items()):
        if not isinstance(payload, dict):
            continue
        status = payload.get("status")
        if not isinstance(status, str) or status != "running":
            continue
        file_path = payload.get("file")
        rows.append(
            {
                "id": str(prover_id),
                "file": str(file_path) if isinstance(file_path, str) else str(prover_id),
                "status": status,
            }
        )
    return rows


def _infer_live_phase(latest_meta: dict[str, object] | None) -> str:
    if not isinstance(latest_meta, dict):
        return "loop_running"
    plan = latest_meta.get("plan")
    if isinstance(plan, dict) and plan.get("status") == "running":
        return "planning"
    prover = latest_meta.get("prover")
    if isinstance(prover, dict) and prover.get("status") == "running":
        return "proving"
    review = latest_meta.get("review")
    if isinstance(review, dict) and review.get("status") == "running":
        return "review"
    return "loop_running"


def _build_live_runtime_payload(
    *,
    latest_iteration: str | None,
    latest_meta: dict[str, object] | None,
    loop_pid: int | None,
    last_activity_at: float | None,
    tracked_path_count: int,
) -> dict[str, object]:
    plan = latest_meta.get("plan") if isinstance(latest_meta, dict) and isinstance(latest_meta.get("plan"), dict) else {}
    prover = latest_meta.get("prover") if isinstance(latest_meta, dict) and isinstance(latest_meta.get("prover"), dict) else {}
    review = latest_meta.get("review") if isinstance(latest_meta, dict) and isinstance(latest_meta.get("review"), dict) else {}
    return {
        "phase": _infer_live_phase(latest_meta),
        "iteration": latest_iteration,
        "loopPid": loop_pid,
        "planStatus": plan.get("status") if isinstance(plan.get("status"), str) else None,
        "proverStatus": prover.get("status") if isinstance(prover.get("status"), str) else None,
        "reviewStatus": review.get("status") if isinstance(review.get("status"), str) else None,
        "activeProvers": _active_prover_rows(latest_meta),
        "trackedPathCount": tracked_path_count,
        "lastActivityAgeSeconds": None if last_activity_at is None else round(max(0.0, time.monotonic() - last_activity_at), 3),
    }


def _text_has_marker(text: str, markers: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in markers)


def _text_has_exact_route(text: str) -> bool:
    if _text_has_marker(text, EXACT_ROUTE_MARKERS):
        return True
    for block in LEAN_CODE_BLOCK_PATTERN.findall(text):
        lowered_block = block.lower()
        if "sorry" in lowered_block:
            continue
        if any(keyword in lowered_block for keyword in LEAN_PROOF_KEYWORDS):
            return True
    return False


def _matching_informal_note_paths(workspace: Path, rel_path: str) -> list[Path]:
    informal_root = workspace / ".archon" / "informal"
    if not informal_root.exists():
        return []
    slug = rel_path.replace("/", "_").removesuffix(".lean").lower()
    rel_path_lower = rel_path.lower()
    matches: list[Path] = []
    for path in sorted(informal_root.rglob("*.md")):
        stem = path.stem.lower()
        if stem == slug or stem.startswith(f"{slug}_"):
            matches.append(path)
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if rel_path_lower in text.lower():
            matches.append(path)
    deduped: list[Path] = []
    seen: set[str] = set()
    for path in matches:
        rendered = str(path)
        if rendered in seen:
            continue
        seen.add(rendered)
        deduped.append(path)
    return deduped


def _exact_route_sources(workspace: Path, rel_path: str) -> list[str]:
    sources: list[str] = []
    rel_path_lower = rel_path.lower()
    for rel_name in (".archon/PROGRESS.md", ".archon/task_pending.md", ".archon/USER_HINTS.md", HISTORICAL_ROUTES_FILE):
        path = workspace / rel_name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if rel_path_lower in text.lower() and _text_has_exact_route(text):
            sources.append(rel_name)
    for raw_path in _matching_informal_note_paths(workspace, rel_path):
        path = Path(raw_path)
        text = path.read_text(encoding="utf-8", errors="replace")
        if _text_has_exact_route(text):
            sources.append(_relative_to_workspace(path, workspace))
    return _dedupe_strings(sources)


def _file_has_single_sorry(path: Path) -> bool:
    if not path.exists():
        return False
    return path.read_text(encoding="utf-8", errors="replace").count("sorry") == 1


def _detect_known_route_fast_path(
    workspace: Path,
    *,
    objective_files: list[str],
    tail_scope_threshold: int,
) -> dict[str, object] | None:
    if tail_scope_threshold <= 0 or not objective_files or len(objective_files) > tail_scope_threshold:
        return None
    task_results_root = workspace / ".archon" / "task_results"
    if task_results_root.exists() and any(task_results_root.glob("*.md")):
        return None

    routes: list[dict[str, object]] = []
    for rel_path in objective_files:
        if not _file_has_single_sorry(workspace / rel_path):
            return None
        exact_sources = _exact_route_sources(workspace, rel_path)
        if exact_sources:
            routes.append({"relPath": rel_path, "kind": "exact", "sources": exact_sources})
            continue
        blocker_evidence = _prevalidated_blocker_evidence(workspace, rel_path)
        if blocker_evidence is not None:
            provenance, _ = blocker_evidence
            routes.append({"relPath": rel_path, "kind": "blocker", "sources": provenance})
            continue
        return None

    return {"reason": "known_routes", "routes": routes}


def _build_run_progress_payload(
    *,
    workspace: Path,
    source: Path,
    runtime_config: RuntimeConfig,
    status: str,
    started_at: str,
    allowed_files: list[str],
    runtime_overrides: dict[str, object],
    latest_iteration: str | None,
    loop_exit_code: int | None,
    changed_files: list[str],
    new_changed_files: list[str],
    task_results: list[str],
    new_task_results: list[str],
    validation_files: list[str],
    lesson_file: str | None,
    planner_state_sync: dict[str, object] | None,
    recovery_event: str | None,
    live_runtime: dict[str, object] | None = None,
) -> dict[str, Any]:
    scope_targets, scope_source = _scope_targets(
        workspace,
        allowed_files=allowed_files,
        runtime_overrides=runtime_overrides,
        changed_files=changed_files,
    )
    target_counts = _validation_target_counts(workspace, scope_targets)
    closed_targets = (
        target_counts["acceptedProofs"]
        + target_counts["acceptedBlockers"]
        + target_counts["rejectedTargets"]
    )
    progress_bar, progress_percent = _progress_bar(closed_targets, len(scope_targets))
    helper_summary = _helper_runtime_summary(workspace, runtime_config)
    task_results_summary = _task_result_summary(workspace)
    helper_model = runtime_config.helper.model if runtime_config.helper is not None else None
    helper_provider = runtime_config.helper.provider if runtime_config.helper is not None else None
    historical_routes_seeded = runtime_overrides.get("historicalRoutesSeeded")
    if not isinstance(historical_routes_seeded, list):
        historical_routes_seeded = []
    historical_note_files = [
        str(item.get("noteRelPath"))
        for item in historical_routes_seeded
        if isinstance(item, dict) and isinstance(item.get("noteRelPath"), str)
    ]

    return {
        "updatedAt": _now_iso(),
        "startedAt": started_at,
        "status": status,
        "workspace": str(workspace),
        "source": str(source),
        "latestIteration": latest_iteration,
        "loopExitCode": loop_exit_code,
        "scope": {
            "kind": scope_source,
            "targets": scope_targets,
            "targetCount": len(scope_targets),
        },
        "progress": {
            "bar": progress_bar,
            "percent": progress_percent,
            "completed": closed_targets,
            "total": len(scope_targets),
            "label": "closed targets",
        },
        "targetCounts": target_counts,
        "changedFiles": changed_files,
        "newChangedFiles": new_changed_files,
        "taskResults": task_results,
        "newTaskResults": new_task_results,
        "validationFiles": validation_files,
        "lessonFile": lesson_file,
        "plannerStateSync": planner_state_sync.get("status") if isinstance(planner_state_sync, dict) else None,
        "recoveryEvent": recovery_event,
        "tailScopeApplied": runtime_overrides.get("tailScopeApplied") is True,
        "planFastPathApplied": runtime_overrides.get("skipInitialPlan") is True,
        "planFastPathReason": runtime_overrides.get("skipInitialPlanReason"),
        "tailScopeTimeouts": {
            "plan": runtime_overrides.get("planTimeoutSeconds"),
            "prover": runtime_overrides.get("proverTimeoutSeconds"),
        },
        "liveRuntime": live_runtime,
        "helper": {
            "enabled": runtime_config.helper is not None
            and (runtime_config.helper_plan.enabled or runtime_config.helper_prover.enabled),
            "provider": helper_provider,
            "model": helper_model,
            "noteDirs": [_relative_to_workspace(path, workspace) for path in _helper_note_dirs(workspace, runtime_config)],
            **helper_summary,
        },
        "taskResultsSummary": task_results_summary,
        "historicalRoutes": {
            "enabled": runtime_overrides.get("preloadHistoricalRoutes") is True,
            "count": len(historical_routes_seeded),
            "manifest": runtime_overrides.get("historicalRoutesManifest"),
            "recentNotes": historical_note_files[:8],
        },
    }


def _render_run_progress_markdown(payload: dict[str, Any]) -> str:
    progress = payload.get("progress") if isinstance(payload.get("progress"), dict) else {}
    scope = payload.get("scope") if isinstance(payload.get("scope"), dict) else {}
    helper = payload.get("helper") if isinstance(payload.get("helper"), dict) else {}
    task_results_summary = payload.get("taskResultsSummary") if isinstance(payload.get("taskResultsSummary"), dict) else {}
    historical_routes = payload.get("historicalRoutes") if isinstance(payload.get("historicalRoutes"), dict) else {}
    target_counts = payload.get("targetCounts") if isinstance(payload.get("targetCounts"), dict) else {}
    tail_scope_timeouts = payload.get("tailScopeTimeouts") if isinstance(payload.get("tailScopeTimeouts"), dict) else {}
    live_runtime = payload.get("liveRuntime") if isinstance(payload.get("liveRuntime"), dict) else {}
    task_result_counts = task_results_summary.get("counts") if isinstance(task_results_summary.get("counts"), dict) else {}
    tail_scope_parts: list[str] = []
    if isinstance(tail_scope_timeouts.get("plan"), int):
        tail_scope_parts.append(f"plan={tail_scope_timeouts['plan']}s")
    if isinstance(tail_scope_timeouts.get("prover"), int):
        tail_scope_parts.append(f"prover={tail_scope_timeouts['prover']}s")
    lines = [
        "# Run Progress",
        "",
        f"- Updated at: `{payload.get('updatedAt', 'unknown')}`",
        f"- Started at: `{payload.get('startedAt', 'unknown')}`",
        f"- Status: `{payload.get('status', 'unknown')}`",
        f"- Progress: `{progress.get('bar', '[--------------------]')} {progress.get('percent', 0)}% ({progress.get('completed', 0)}/{progress.get('total', 0)} {progress.get('label', 'targets')})`",
        f"- Scope source: `{scope.get('kind', 'none')}`",
        f"- Latest iteration: `{payload.get('latestIteration') or '(none)'}`",
        f"- Loop exit code: `{payload.get('loopExitCode') if payload.get('loopExitCode') is not None else '(none)'}`",
        f"- Live phase: `{live_runtime.get('phase') or '(none)'}`",
        f"- Live plan status: `{live_runtime.get('planStatus') or '(none)'}`",
        f"- Live prover status: `{live_runtime.get('proverStatus') or '(none)'}`",
        f"- Live review status: `{live_runtime.get('reviewStatus') or '(none)'}`",
        f"- Active prover count: `{len(live_runtime.get('activeProvers', [])) if isinstance(live_runtime.get('activeProvers'), list) else 0}`",
        f"- Live activity age: `{live_runtime.get('lastActivityAgeSeconds') if live_runtime.get('lastActivityAgeSeconds') is not None else '(none)'}`",
        f"- Accepted proofs: `{target_counts.get('acceptedProofs', 0)}`",
        f"- Accepted blockers: `{target_counts.get('acceptedBlockers', 0)}`",
        f"- Rejected targets: `{target_counts.get('rejectedTargets', 0)}`",
        f"- Pending targets: `{target_counts.get('pendingTargets', 0)}`",
        f"- Attention targets: `{target_counts.get('attentionTargets', 0)}`",
        f"- Changed files: `{len(payload.get('changedFiles', [])) if isinstance(payload.get('changedFiles'), list) else 0}`",
        f"- Task results: `{len(payload.get('taskResults', [])) if isinstance(payload.get('taskResults'), list) else 0}`",
        f"- Task result kinds: `resolved={task_result_counts.get('resolved', 0)}, blocker={task_result_counts.get('blocker', 0)}, other={task_result_counts.get('other', 0)}`",
        f"- Helper enabled: `{helper.get('enabled')}`",
        f"- Helper notes observed: `{helper.get('noteCount', 0)}`",
        f"- Helper note phases: `{json.dumps(helper.get('countsByPhase', {}), sort_keys=True)}`",
        f"- Helper note reasons: `{json.dumps(helper.get('countsByReason', {}), sort_keys=True)}`",
        f"- Helper prompt packs: `{json.dumps(helper.get('countsByPromptPack', {}), sort_keys=True)}`",
        f"- Helper fresh calls: `{helper.get('freshCallCount', 0)}`",
        f"- Helper failed calls: `{helper.get('failedCallCount', 0)}`",
        f"- Helper failed reasons: `{json.dumps(helper.get('failedCallsByReason', {}), sort_keys=True)}`",
        f"- Helper note reuses: `{helper.get('noteReuseCount', 0)}`",
        f"- Helper blocked by budget: `{helper.get('blockedByBudgetCount', 0)}`",
        f"- Helper blocked by cooldown: `{helper.get('blockedByCooldownCount', 0)}`",
        f"- Historical routes enabled: `{historical_routes.get('enabled')}`",
        f"- Historical routes seeded: `{historical_routes.get('count', 0)}`",
        f"- Historical route manifest: `{historical_routes.get('manifest') or '(none)'}`",
        f"- Planner state sync: `{payload.get('plannerStateSync') or '(none)'}`",
        f"- Recovery event: `{payload.get('recoveryEvent') or '(none)'}`",
        f"- Tail-scope applied: `{payload.get('tailScopeApplied')}`",
        f"- Tail-scope overrides: `{', '.join(tail_scope_parts) if tail_scope_parts else '(none)'}`",
        f"- Plan fast-path: `{payload.get('planFastPathReason') if payload.get('planFastPathApplied') else '(none)'}`",
        "",
        "## Scope",
        "",
    ]
    scope_targets = scope.get("targets")
    if isinstance(scope_targets, list) and scope_targets:
        for rel_path in scope_targets[:12]:
            lines.append(f"- `{rel_path}`")
    else:
        lines.append("- none")

    lines.extend(["", "## Live Loop", ""])
    active_provers = live_runtime.get("activeProvers")
    if isinstance(active_provers, list) and active_provers:
        for row in active_provers[:12]:
            if not isinstance(row, dict):
                continue
            lines.append(f"- `{row.get('file', row.get('id', 'unknown'))}` — `{row.get('status', 'unknown')}`")
    else:
        lines.append("- none")

    lines.extend(["", "## Historical Routes", ""])
    historical_recent = historical_routes.get("recentNotes")
    if isinstance(historical_recent, list) and historical_recent:
        for note in historical_recent:
            lines.append(f"- `{note}`")
    else:
        lines.append("- none")

    lines.extend(["", "## New This Cycle", ""])
    new_changed = payload.get("newChangedFiles")
    if isinstance(new_changed, list) and new_changed:
        lines.append(f"- New changed files: `{', '.join(new_changed)}`")
    else:
        lines.append("- New changed files: `(none)`")
    new_task_results = payload.get("newTaskResults")
    if isinstance(new_task_results, list) and new_task_results:
        lines.append(f"- New task results: `{', '.join(new_task_results)}`")
    else:
        lines.append("- New task results: `(none)`")

    lines.extend(["", "## Helper Notes", ""])
    recent_note_metadata = helper.get("recentMetadata")
    if isinstance(recent_note_metadata, list) and recent_note_metadata:
        for note in recent_note_metadata:
            if not isinstance(note, dict):
                continue
            descriptor = [
                f"`{note.get('path', 'unknown')}`",
            ]
            if isinstance(note.get("phase"), str):
                descriptor.append(f"phase=`{note['phase']}`")
            if isinstance(note.get("reason"), str):
                descriptor.append(f"reason=`{note['reason']}`")
            if isinstance(note.get("promptpack"), str):
                descriptor.append(f"pack=`{note['promptpack']}`")
            lines.append("- " + " ".join(descriptor))
    else:
        lines.append("- none")

    lines.extend(["", "## Helper Runtime", ""])
    active_cooldowns = helper.get("cooldownState", {}).get("activeReasons") if isinstance(helper.get("cooldownState"), dict) else None
    if isinstance(active_cooldowns, list) and active_cooldowns:
        for row in active_cooldowns[:12]:
            if not isinstance(row, dict):
                continue
            lines.append(
                f"- cooldown `{row.get('phase', 'unknown')}:{row.get('reason', 'unknown')}`"
                + (f" on `{row.get('relPath')}`" if isinstance(row.get("relPath"), str) and row.get("relPath") else "")
            )
    else:
        lines.append("- none")

    lines.extend(["", "## Task Results", ""])
    recent_task_results = task_results_summary.get("recentResults")
    if isinstance(recent_task_results, list) and recent_task_results:
        for row in recent_task_results:
            if not isinstance(row, dict):
                continue
            lines.append(f"- `{row.get('path', 'unknown')}` — `{row.get('kind', 'unknown')}`")
    else:
        lines.append("- none")

    return "\n".join(lines) + "\n"


def _write_run_progress_surface(
    workspace: Path,
    *,
    runtime_config: RuntimeConfig,
    payload: dict[str, Any],
) -> None:
    if runtime_config.observability.write_progress_surface is not True:
        return
    markdown_path, json_path = _run_progress_paths(workspace)
    _write_text(markdown_path, _render_run_progress_markdown(payload))
    _write_text(json_path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _emit_live_progress_surface(
    *,
    workspace: Path,
    source: Path,
    runtime_config: RuntimeConfig,
    started_at: str,
    allowed_files: list[str],
    runtime_overrides: dict[str, object],
    baseline_changed_mtimes: dict[Path, float],
    baseline_task_result_mtimes: dict[Path, float],
    live_runtime: dict[str, object],
) -> None:
    changed_files = collect_changed_files(source, workspace, allowed_files=allowed_files or None)
    task_result_paths = sorted((workspace / ".archon" / "task_results").glob("*.md"))
    validation_files = sorted(path.name for path in (workspace / ".archon" / "validation").glob("*.json"))
    new_changed_files = sorted(
        rel_path
        for rel_path in changed_files
        if (workspace / rel_path).stat().st_mtime > baseline_changed_mtimes.get(workspace / rel_path, float("-inf"))
    )
    new_task_result_paths = sorted(
        path
        for path in task_result_paths
        if path.stat().st_mtime > baseline_task_result_mtimes.get(path, float("-inf"))
    )
    _write_run_progress_surface(
        workspace,
        runtime_config=runtime_config,
        payload=_build_run_progress_payload(
            workspace=workspace,
            source=source,
            runtime_config=runtime_config,
            status="running",
            started_at=started_at,
            allowed_files=allowed_files,
            runtime_overrides=runtime_overrides,
            latest_iteration=live_runtime.get("iteration") if isinstance(live_runtime.get("iteration"), str) else None,
            loop_exit_code=None,
            changed_files=changed_files,
            new_changed_files=new_changed_files,
            task_results=[path.name for path in task_result_paths],
            new_task_results=[path.name for path in new_task_result_paths],
            validation_files=validation_files,
            lesson_file=None,
            planner_state_sync=None,
            recovery_event=None,
            live_runtime=live_runtime,
        ),
    )


def _lease_conflicts(skip: bool, lease_path: Path, *, current_pid: int) -> list[dict[str, object]]:
    if skip:
        return []
    lease = _read_json(lease_path)
    if lease is None or lease.get("active") is not True:
        return []

    events: list[dict[str, object]] = []
    supervisor_pid = _coerce_pid(lease.get("supervisorPid"))
    loop_pid = _coerce_pid(lease.get("loopPid"))
    if supervisor_pid is not None and supervisor_pid != current_pid and _pid_is_live(supervisor_pid):
        return [
            {
                "event": "active_supervisor_lease",
                "supervisorPid": supervisor_pid,
                "loopPid": loop_pid,
                "workspace": lease.get("workspace"),
                "updatedAt": lease.get("updatedAt"),
            }
        ]
    if loop_pid is not None and _pid_is_live(loop_pid):
        events.append(
            {
                "event": "orphaned_loop_lease",
                "supervisorPid": supervisor_pid,
                "loopPid": loop_pid,
                "workspace": lease.get("workspace"),
                "updatedAt": lease.get("updatedAt"),
            }
        )
    return events


def _current_objective_rel_paths(workspace: Path, *, allowed_files: list[str]) -> list[str]:
    progress_path = workspace / ".archon" / "PROGRESS.md"
    if not progress_path.exists():
        return []

    allowed = set(allowed_files)
    found_section = False
    rel_paths: list[str] = []
    for raw_line in progress_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.rstrip()
        if not found_section:
            if line.strip() == "## Current Objectives":
                found_section = True
            continue
        if line.startswith("## "):
            break
        if not re.match(r"^[ \t]*[0-9]+\.[ \t]+", line):
            continue
        for match in OBJECTIVE_REL_PATH_PATTERN.findall(line):
            rel_path = match.strip()
            if allowed and rel_path not in allowed:
                continue
            if rel_path not in rel_paths:
                rel_paths.append(rel_path)
    return rel_paths


def _resolve_runtime_overrides(
    args: argparse.Namespace,
    workspace: Path,
    *,
    allowed_files: list[str],
) -> dict[str, object]:
    overrides: dict[str, object] = {}
    objective_rel_paths = _current_objective_rel_paths(workspace, allowed_files=allowed_files)
    if not objective_rel_paths and allowed_files:
        objective_rel_paths = list(allowed_files)
    objective_count = len(objective_rel_paths)
    overrides["objectiveCount"] = objective_count
    overrides["objectiveFiles"] = objective_rel_paths

    in_tail_scope = args.tail_scope_objective_threshold > 0 and 0 < objective_count <= args.tail_scope_objective_threshold
    if in_tail_scope:
        tail_scope_applied = False
        if args.tail_scope_plan_timeout_seconds is not None:
            current_timeout = args.plan_timeout_seconds
            tail_timeout = args.tail_scope_plan_timeout_seconds
            if current_timeout is None or tail_timeout > current_timeout:
                overrides["planTimeoutSeconds"] = tail_timeout
                tail_scope_applied = True

        if args.tail_scope_prover_timeout_seconds is not None:
            current_timeout = args.prover_timeout_seconds
            tail_timeout = args.tail_scope_prover_timeout_seconds
            if current_timeout is None or tail_timeout > current_timeout:
                overrides["proverTimeoutSeconds"] = tail_timeout
                tail_scope_applied = True

        if tail_scope_applied:
            overrides["tailScopeApplied"] = True

    fast_path = _detect_known_route_fast_path(
        workspace,
        objective_files=objective_rel_paths,
        tail_scope_threshold=args.tail_scope_objective_threshold,
    )
    if fast_path is not None:
        overrides["skipInitialPlan"] = True
        overrides["skipInitialPlanReason"] = fast_path["reason"]
        overrides["knownRoutes"] = fast_path["routes"]

    return overrides


def _build_loop_command(
    args: argparse.Namespace,
    runtime_overrides: dict[str, object] | None = None,
) -> tuple[list[str], dict[str, str] | None]:
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
    env = None
    effective_plan_timeout = args.plan_timeout_seconds
    effective_prover_timeout = args.prover_timeout_seconds
    effective_review_timeout = args.review_timeout_seconds
    if isinstance(runtime_overrides, dict):
        if isinstance(runtime_overrides.get("planTimeoutSeconds"), int):
            effective_plan_timeout = int(runtime_overrides["planTimeoutSeconds"])
        if isinstance(runtime_overrides.get("proverTimeoutSeconds"), int):
            effective_prover_timeout = int(runtime_overrides["proverTimeoutSeconds"])
    if any(
        timeout is not None
        for timeout in (effective_plan_timeout, effective_prover_timeout, effective_review_timeout)
    ):
        env = dict(os.environ)
        if effective_plan_timeout is not None:
            env["ARCHON_PLAN_TIMEOUT_SECONDS"] = str(effective_plan_timeout)
        if effective_prover_timeout is not None:
            env["ARCHON_PROVER_TIMEOUT_SECONDS"] = str(effective_prover_timeout)
        if effective_review_timeout is not None:
            env["ARCHON_REVIEW_TIMEOUT_SECONDS"] = str(effective_review_timeout)
    if isinstance(runtime_overrides, dict) and runtime_overrides.get("skipInitialPlan") is True:
        if env is None:
            env = dict(os.environ)
        env["ARCHON_SKIP_INITIAL_PLAN"] = "1"
        reason = runtime_overrides.get("skipInitialPlanReason")
        if isinstance(reason, str) and reason:
            env["ARCHON_SKIP_INITIAL_PLAN_REASON"] = reason
    return command, env


def _tracked_activity_paths(workspace: Path, iteration: str | None, allowed_files: list[str]) -> list[Path]:
    paths: list[Path] = []
    if iteration:
        iter_dir = workspace / ".archon" / "logs" / iteration
        provers_dir = iter_dir / "provers"
        if provers_dir.exists():
            paths.extend(sorted(provers_dir.glob("*.jsonl")))
        prover_log = iter_dir / "prover.jsonl"
        if prover_log.exists():
            paths.append(prover_log)

    results_dir = workspace / ".archon" / "task_results"
    if results_dir.exists():
        paths.extend(sorted(results_dir.glob("*.md")))

    for rel_path in allowed_files:
        target = workspace / rel_path
        if target.exists():
            paths.append(target)

    return paths


def _latest_mtime(paths: list[Path]) -> float | None:
    mtimes = [path.stat().st_mtime for path in paths if path.exists()]
    if not mtimes:
        return None
    return max(mtimes)


def _terminate_process_group(proc: subprocess.Popen[str]) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return

    deadline = time.monotonic() + 5.0
    while proc.poll() is None and time.monotonic() < deadline:
        time.sleep(0.1)

    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            return


def _monitor_for_idle_prover(
    proc: subprocess.Popen[str],
    workspace: Path,
    source: Path,
    lease_path: Path,
    allowed_files: list[str],
    *,
    idle_seconds: float,
    poll_seconds: float,
    progress_callback: Callable[[dict[str, object]], None] | None = None,
) -> dict[str, object] | None:
    tracked_iteration: str | None = None
    last_seen_mtime: float | None = None
    last_activity_at: float | None = None

    while proc.poll() is None:
        latest_iter_name, latest_meta = latest_iteration_meta(workspace)
        _update_lease(
            lease_path,
            workspace=workspace,
            source=source,
            fields={
                "active": True,
                "status": "running",
                "supervisorPid": os.getpid(),
                "loopPid": proc.pid,
                "lastHeartbeatAt": _now_iso(),
                "latestIteration": latest_iter_name,
            },
        )
        if latest_iter_name != tracked_iteration:
            tracked_iteration = latest_iter_name
            last_seen_mtime = None
            last_activity_at = None

        prover_status = None
        tracked_paths: list[Path] = []
        if isinstance(latest_meta, dict):
            prover_payload = latest_meta.get("prover")
            if isinstance(prover_payload, dict):
                prover_status = prover_payload.get("status")

        if prover_status == "running":
            if last_activity_at is None:
                last_activity_at = time.monotonic()

            tracked_paths = _tracked_activity_paths(workspace, tracked_iteration, allowed_files)
            newest_mtime = _latest_mtime(tracked_paths)
            if newest_mtime is not None and (last_seen_mtime is None or newest_mtime > last_seen_mtime):
                last_seen_mtime = newest_mtime
                last_activity_at = time.monotonic()

        if progress_callback is not None:
            progress_callback(
                _build_live_runtime_payload(
                    latest_iteration=latest_iter_name,
                    latest_meta=latest_meta,
                    loop_pid=proc.pid,
                    last_activity_at=last_activity_at,
                    tracked_path_count=len(tracked_paths),
                )
            )

        if prover_status == "running":
            if last_activity_at is not None and time.monotonic() - last_activity_at > idle_seconds:
                _terminate_process_group(proc)
                return {
                    "event": "prover_idle_timeout",
                    "iteration": tracked_iteration,
                    "idle_seconds": idle_seconds,
                    "tracked_path_count": len(tracked_paths),
                }

        time.sleep(max(poll_seconds, 0.05))

    return None


def _run_archon_loop(
    args: argparse.Namespace,
    workspace: Path,
    source: Path,
    lease_path: Path,
    allowed_files: list[str],
    runtime_overrides: dict[str, object] | None = None,
    progress_callback: Callable[[dict[str, object]], None] | None = None,
) -> tuple[subprocess.CompletedProcess[str], dict[str, object] | None]:
    command, env = _build_loop_command(args, runtime_overrides)

    if not args.prover_idle_seconds or args.prover_idle_seconds <= 0:
        if progress_callback is not None:
            progress_callback(
                _build_live_runtime_payload(
                    latest_iteration=None,
                    latest_meta=None,
                    loop_pid=None,
                    last_activity_at=None,
                    tracked_path_count=0,
                )
            )
        result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                cwd=str(ROOT),
                env=env,
            )
        _update_lease(
            lease_path,
            workspace=workspace,
            source=source,
            fields={
                "active": True,
                "status": "loop_finished",
                "supervisorPid": os.getpid(),
                "loopPid": None,
                "lastHeartbeatAt": _now_iso(),
                "loopExitCode": result.returncode,
            },
        )
        return (result, None)

    supervisor_dir = workspace / ".archon" / "supervisor"
    stdout_tmp = supervisor_dir / ".supervised-cycle.stdout.tmp"
    stderr_tmp = supervisor_dir / ".supervised-cycle.stderr.tmp"
    supervisor_dir.mkdir(parents=True, exist_ok=True)

    try:
        with stdout_tmp.open("w", encoding="utf-8") as stdout_handle, stderr_tmp.open(
            "w", encoding="utf-8"
        ) as stderr_handle:
            proc = subprocess.Popen(
                command,
                stdout=stdout_handle,
                stderr=stderr_handle,
                text=True,
                cwd=str(ROOT),
                env=env,
                start_new_session=True,
            )
            _update_lease(
                lease_path,
                workspace=workspace,
                source=source,
                fields={
                    "active": True,
                    "status": "running",
                    "supervisorPid": os.getpid(),
                    "loopPid": proc.pid,
                    "lastHeartbeatAt": _now_iso(),
                },
            )
            if progress_callback is not None:
                progress_callback(
                    _build_live_runtime_payload(
                        latest_iteration=None,
                        latest_meta=None,
                        loop_pid=proc.pid,
                        last_activity_at=None,
                        tracked_path_count=0,
                    )
                )
            idle_event = _monitor_for_idle_prover(
                proc,
                workspace,
                source,
                lease_path,
                allowed_files,
                idle_seconds=args.prover_idle_seconds,
                poll_seconds=args.monitor_poll_seconds,
                progress_callback=progress_callback,
            )
            returncode = proc.wait()

        return (
            subprocess.CompletedProcess(
                command,
                returncode,
                stdout_tmp.read_text(encoding="utf-8"),
                stderr_tmp.read_text(encoding="utf-8"),
            ),
            idle_event,
        )
    finally:
        stdout_tmp.unlink(missing_ok=True)
        stderr_tmp.unlink(missing_ok=True)


def _tail_text(text: str, max_lines: int = 8) -> str:
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    return "\n".join(lines[-max_lines:])


def _write_loop_output(supervisor_dir: Path, loop_result: subprocess.CompletedProcess[str]) -> tuple[Path, Path]:
    stdout_path = supervisor_dir / "last_loop.stdout.log"
    stderr_path = supervisor_dir / "last_loop.stderr.log"
    _write_text(stdout_path, loop_result.stdout)
    _write_text(stderr_path, loop_result.stderr)
    return stdout_path, stderr_path


def _path_mtimes(paths: list[Path]) -> dict[Path, float]:
    return {path: path.stat().st_mtime for path in paths if path.exists()}


def _verify_changed_file(workspace: Path, file_path: Path, template: str | None) -> tuple[bool, str]:
    command = template or "timeout 30s lake env lean {file}"
    rendered = command.format(file=shlex.quote(str(file_path)))
    result = subprocess.run(
        ["bash", "-lc", rendered],
        cwd=str(workspace),
        capture_output=True,
        text=True,
        check=False,
    )
    output = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part).strip()
    if result.returncode != 0:
        return False, output or f"verify command failed with exit code {result.returncode}"
    if "declaration uses `sorry`" in output:
        return False, output
    return True, output


def _classify_task_result_note(path: Path) -> tuple[str, str]:
    text = path.read_text(encoding="utf-8")
    lowered = text.lower()
    if any(marker in lowered for marker in ("concrete blocker:", "validated blocker", "genuine blocker")):
        return "blocker", "contains an explicit blocker marker"
    if "**result:** resolved" in lowered:
        return "resolved", "contains a RESOLVED result marker"
    return "other", "missing an explicit RESOLVED or blocker marker"


def _task_result_name(rel_path: str) -> str:
    return rel_path.replace("/", "_") + ".md"


def _validation_filename(rel_path: str) -> str:
    return rel_path.replace("/", "_") + ".json"


def _archive_stale_accepted_task_results(
    workspace: Path,
    *,
    objective_files: list[str],
) -> list[dict[str, str]]:
    if not objective_files:
        return []

    task_results_root = workspace / ".archon" / "task_results"
    validation_root = workspace / ".archon" / "validation"
    if not task_results_root.exists() or not validation_root.exists():
        return []

    objective_set = set(objective_files)
    archive_root = workspace / ".archon" / "task_results_archived" / "accepted_stale"
    timestamp_prefix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archived: list[dict[str, str]] = []

    for validation_path in sorted(validation_root.glob("*.json")):
        payload = _read_json(validation_path)
        if not isinstance(payload, dict):
            continue
        rel_path = payload.get("relPath")
        if not isinstance(rel_path, str) or rel_path in objective_set:
            continue
        if payload.get("acceptanceStatus") != "accepted" or payload.get("validationStatus") != "passed":
            continue

        note_name = _task_result_name(rel_path)
        note_path = task_results_root / note_name
        if not note_path.exists():
            continue

        archive_root.mkdir(parents=True, exist_ok=True)
        archive_path = archive_root / f"{timestamp_prefix}-{note_name}"
        note_path.rename(archive_path)
        archived.append(
            {
                "relPath": rel_path,
                "noteName": note_name,
                "archivePath": _relative_to_workspace(archive_path, workspace),
            }
        )

    return archived


def _extract_informal_note_paths(text: str) -> list[str]:
    return INFORMAL_NOTE_PATTERN.findall(text)


def _prevalidated_blocker_evidence(workspace: Path, rel_path: str) -> tuple[list[str], str] | None:
    progress_path = workspace / ".archon" / "PROGRESS.md"
    pending_path = workspace / ".archon" / "task_pending.md"
    historical_routes_path = workspace / HISTORICAL_ROUTES_FILE
    progress_text = progress_path.read_text(encoding="utf-8") if progress_path.exists() else ""
    pending_text = pending_path.read_text(encoding="utf-8") if pending_path.exists() else ""
    historical_routes_text = historical_routes_path.read_text(encoding="utf-8") if historical_routes_path.exists() else ""
    combined = "\n".join(part for part in (progress_text, pending_text, historical_routes_text) if part)
    lowered = combined.lower()

    if rel_path not in combined:
        return None
    if "lean-validated" not in lowered:
        return None
    if "blocker" not in lowered:
        return None
    if "false as written" not in lowered and "validated obstruction" not in lowered:
        return None

    provenance = [
        rel_name
        for rel_name, text in (
            (".archon/PROGRESS.md", progress_text),
            (".archon/task_pending.md", pending_text),
            (HISTORICAL_ROUTES_FILE, historical_routes_text),
        )
        if text.strip()
    ]
    evidence = historical_routes_text.strip() or pending_text.strip() or progress_text.strip()
    for note_rel in _extract_informal_note_paths(combined):
        note_path = workspace / note_rel
        if not note_path.exists():
            continue
        provenance.append(note_rel)
        note_text = note_path.read_text(encoding="utf-8").strip()
        if note_text:
            evidence = note_text
            break

    if not evidence:
        return None
    return provenance, evidence


def _synthesize_blocker_note_after_idle(
    workspace: Path,
    *,
    allowed_files: list[str],
    new_changed_files: list[str],
    new_task_result_paths: list[Path],
) -> dict[str, object] | None:
    if new_changed_files or new_task_result_paths:
        return None
    if len(allowed_files) != 1:
        return None

    rel_path = allowed_files[0]
    note_name = _task_result_name(rel_path)
    note_path = workspace / ".archon" / "task_results" / note_name
    if note_path.exists():
        return None

    evidence = _prevalidated_blocker_evidence(workspace, rel_path)
    if evidence is None:
        return None
    provenance, blocker_text = evidence

    provenance_rendered = ", ".join(f"`{path}`" for path in provenance)
    content = "\n".join(
        [
            f"# {rel_path}",
            "",
            "## Supervisor Recovery",
            "### Attempt 1",
            "- **Result:** FAILED",
            "- **Concrete blocker:** Preserved by the supervisor after a prover idle timeout. The benchmark theorem remains frozen and the statement is false as written.",
            f"- **Provenance:** Recovered from {provenance_rendered} after the prover stalled before writing a durable note.",
            "- **Next step:** Reuse this blocker note directly in the next planning pass. Only add a separately named helper/counterexample theorem after the blocker artifact already exists.",
            "",
            "## Evidence",
            blocker_text,
            "",
        ]
    )
    _write_text(note_path, content)
    return {
        "event": "synthesized_blocker_after_idle",
        "kind": "task_result",
        "task_results": [note_name],
        "provenance": provenance,
    }


def _recover_after_stall(
    workspace: Path,
    *,
    recovery_event: str,
    allow_synthesis: bool,
    allowed_files: list[str],
    new_changed_files: list[str],
    new_task_result_paths: list[Path],
    verify_template: str | None,
) -> dict[str, object] | None:
    verified_files: list[str] = []
    changed_file_failures: dict[str, str] = {}
    for rel_path in new_changed_files:
        ok, detail = _verify_changed_file(workspace, workspace / rel_path, verify_template)
        if ok:
            verified_files.append(rel_path)
        else:
            changed_file_failures[rel_path] = detail

    verified_task_results: list[str] = []
    task_result_kinds: dict[str, str] = {}
    task_result_failures: dict[str, str] = {}
    for path in new_task_result_paths:
        kind, detail = _classify_task_result_note(path)
        if kind in {"resolved", "blocker"}:
            verified_task_results.append(path.name)
            task_result_kinds[path.name] = kind
        else:
            task_result_failures[path.name] = detail

    if new_changed_files and not changed_file_failures:
        return {
            "event": recovery_event,
            "kind": "changed_file",
            "files": verified_files,
        }
    if new_task_result_paths and not task_result_failures:
        return {
            "event": recovery_event,
            "kind": "task_result",
            "task_results": verified_task_results,
            "task_result_kinds": task_result_kinds,
        }
    if allow_synthesis:
        synthesized = _synthesize_blocker_note_after_idle(
            workspace,
            allowed_files=allowed_files,
            new_changed_files=new_changed_files,
            new_task_result_paths=new_task_result_paths,
        )
        if synthesized is not None:
            return synthesized
    if changed_file_failures or task_result_failures:
        payload: dict[str, object] = {"event": "verification_failed_after_idle"}
        if changed_file_failures:
            payload["files"] = changed_file_failures
        if task_result_failures:
            payload["task_results"] = task_result_failures
        return payload
    return None


def _terminal_sync_records(workspace: Path, *, allowed_files: list[str]) -> list[dict[str, str]] | None:
    if not allowed_files:
        return None

    validation_root = workspace / ".archon" / "validation"
    records: list[dict[str, str]] = []
    for rel_path in allowed_files:
        payload = _read_json(validation_root / _validation_filename(rel_path))
        if payload is None or payload.get("acceptanceStatus") != "accepted":
            return None

        checks = payload.get("checks")
        workspace_changed = isinstance(checks, dict) and checks.get("workspaceChanged") is True
        blocker_notes = payload.get("blockerNotes")

        record = {
            "relPath": rel_path,
            "validationFile": _validation_filename(rel_path),
            "outcome": "accepted",
        }
        if isinstance(blocker_notes, list) and blocker_notes and not workspace_changed:
            record["outcome"] = "blocked"
            record["blockerNote"] = str(blocker_notes[0])
        records.append(record)
    return records


def _render_terminal_progress(records: list[dict[str, str]]) -> str:
    lines = [
        "# Project Progress",
        "",
        "## Current Stage",
        "COMPLETE",
        "",
        stage_markdown("COMPLETE", autoformalize_skipped=True),
        "",
        "## Current Objectives",
        "",
    ]
    for index, record in enumerate(records, start=1):
        rel_path = record["relPath"]
        if record["outcome"] == "blocked":
            blocker_note = record["blockerNote"]
            lines.append(
                f"{index}. **{rel_path}** — Accepted blocker note `{blocker_note}` validated; no further prover work remains in this run scope."
            )
        else:
            lines.append(
                f"{index}. **{rel_path}** — Accepted proof validated; no further prover work remains in this run scope."
            )
    lines.append("")
    return "\n".join(lines)


def _render_terminal_task_done(records: list[dict[str, str]]) -> str:
    lines = ["# Completed Tasks", ""]
    for record in records:
        rel_path = record["relPath"]
        validation_path = f".archon/validation/{record['validationFile']}"
        if record["outcome"] == "blocked":
            blocker_note = record["blockerNote"]
            lines.append(
                f"- `{rel_path}` — Accepted blocker note `{blocker_note}` validated by `{validation_path}`."
            )
        else:
            lines.append(f"- `{rel_path}` — Accepted proof validated by `{validation_path}`.")
    lines.append("")
    return "\n".join(lines)


def _partial_sync_records(workspace: Path, *, allowed_files: list[str]) -> tuple[list[dict[str, str]], list[object]] | None:
    if not allowed_files:
        return None

    validation_root = workspace / ".archon" / "validation"
    completed: list[dict[str, str]] = []
    remaining = []
    for rel_path in allowed_files:
        payload = _read_json(validation_root / _validation_filename(rel_path))
        if payload is None:
            return None

        checks = payload.get("checks")
        workspace_changed = isinstance(checks, dict) and checks.get("workspaceChanged") is True
        blocker_notes = payload.get("blockerNotes")
        if payload.get("acceptanceStatus") == "accepted":
            record = {
                "relPath": rel_path,
                "validationFile": _validation_filename(rel_path),
                "outcome": "accepted",
            }
            if isinstance(blocker_notes, list) and blocker_notes and not workspace_changed:
                record["outcome"] = "blocked"
                record["blockerNote"] = str(blocker_notes[0])
            completed.append(record)
            continue

        target = workspace / rel_path
        if target.exists():
            remaining.append(objective_for_file(workspace, target))

    if not completed or not remaining:
        return None
    return completed, remaining


def _render_focused_progress(remaining: list[object]) -> str:
    lines = [
        "# Project Progress",
        "",
        "## Current Stage",
        "prover",
        "",
        stage_markdown("prover", autoformalize_skipped=True),
        "",
        "## Current Objectives",
        "",
    ]
    for index, objective in enumerate(remaining, start=1):
        lines.append(objective.to_markdown(index))
    lines.append("")
    return "\n".join(lines)


def _render_focused_task_done(records: list[dict[str, str]]) -> str:
    lines = ["# Completed Tasks", ""]
    for record in records:
        rel_path = record["relPath"]
        validation_path = f".archon/validation/{record['validationFile']}"
        if record["outcome"] == "blocked":
            blocker_note = record["blockerNote"]
            lines.append(f"- `{rel_path}` — Accepted blocker note `{blocker_note}` validated by `{validation_path}`.")
        else:
            lines.append(f"- `{rel_path}` — Accepted proof validated by `{validation_path}`.")
    lines.append("")
    return "\n".join(lines)


def _sync_focused_planner_state(workspace: Path, *, allowed_files: list[str]) -> dict[str, object] | None:
    records = _partial_sync_records(workspace, allowed_files=allowed_files)
    if records is None:
        return None

    completed, remaining = records
    state_root = workspace / ".archon"
    _write_text(state_root / "PROGRESS.md", _render_focused_progress(remaining))
    _write_text(state_root / "task_pending.md", build_task_pending_markdown(remaining))
    _write_text(state_root / "task_done.md", _render_focused_task_done(completed))
    return {
        "event": "planner_state_synced",
        "status": "focused_remaining_scope",
        "completedTargets": [record["relPath"] for record in completed],
        "remainingTargets": [objective.rel_path for objective in remaining],
    }


def _sync_terminal_planner_state(workspace: Path, *, allowed_files: list[str]) -> dict[str, object] | None:
    records = _terminal_sync_records(workspace, allowed_files=allowed_files)
    if records is None:
        return None

    state_root = workspace / ".archon"
    _write_text(state_root / "PROGRESS.md", _render_terminal_progress(records))
    _write_text(state_root / "task_pending.md", build_task_pending_markdown([]))
    _write_text(state_root / "task_done.md", _render_terminal_task_done(records))

    return {
        "event": "planner_state_synced",
        "status": "terminal_complete",
        "targets": [record["relPath"] for record in records],
        "outcomes": {record["relPath"]: record["outcome"] for record in records},
    }


def main() -> int:
    args = parse_args()
    workspace = Path(args.workspace).resolve()
    source = Path(args.source).resolve()
    supervisor_dir = workspace / ".archon" / "supervisor"
    lease_path = _lease_path(workspace)
    hot_notes_path = supervisor_dir / "HOT_NOTES.md"
    ledger_path = supervisor_dir / "LEDGER.md"
    violations_path = supervisor_dir / "violations.jsonl"

    started_at = _now_iso()
    allowed_files = read_allowed_files(workspace)
    historical_routes_seeded = (
        _seed_historical_routes(workspace, allowed_files=allowed_files)
        if args.preload_historical_routes and not args.recovery_only
        else []
    )
    if not args.preload_historical_routes or args.recovery_only:
        _clear_historical_routes(workspace)
    baseline_changed_files = collect_changed_files(source, workspace, allowed_files=allowed_files or None)
    baseline_changed_mtimes = _path_mtimes([workspace / rel_path for rel_path in baseline_changed_files])
    baseline_task_result_paths = sorted((workspace / ".archon" / "task_results").glob("*.md"))
    baseline_task_result_mtimes = _path_mtimes(baseline_task_result_paths)
    previous_iter_name, _ = latest_iteration_meta(workspace)
    lease_conflicts = _lease_conflicts(args.skip_process_check, lease_path, current_pid=os.getpid())
    runtime_config = load_runtime_config(workspace)
    runtime_overrides = _resolve_runtime_overrides(args, workspace, allowed_files=allowed_files)
    if args.preload_historical_routes:
        runtime_overrides["preloadHistoricalRoutes"] = True
        runtime_overrides["historicalRoutesSeeded"] = historical_routes_seeded
        if historical_routes_seeded:
            runtime_overrides["historicalRoutesManifest"] = HISTORICAL_ROUTES_MANIFEST_FILE
    if lease_conflicts:
        _append_text(
            violations_path,
            "".join(json.dumps(event, sort_keys=True) + "\n" for event in lease_conflicts),
        )
        hot_notes = [
            "# Supervisor Hot Notes",
            "",
            "Read this before touching the run.",
            "",
            "- Status: run_busy",
            f"- Started at: {started_at}",
            f"- Workspace: {workspace}",
            f"- Source: {source}",
            f"- Allowed files: {', '.join(allowed_files) if allowed_files else '(all .lean files)'}",
            "- Reason: an active run-local lease already owns this workspace",
        ]
        for event in lease_conflicts:
            hot_notes.append(f"- Lease event: {event['event']}")
            if event.get("supervisorPid") is not None:
                hot_notes.append(f"- Lease supervisor pid: {event['supervisorPid']}")
            if event.get("loopPid") is not None:
                hot_notes.append(f"- Lease loop pid: {event['loopPid']}")
            if event.get("updatedAt") is not None:
                hot_notes.append(f"- Lease updated at: {event['updatedAt']}")
        _write_text(hot_notes_path, "\n".join(hot_notes) + "\n")
        _append_text(
            ledger_path,
            "\n".join(
                [
                    f"## Cycle {started_at}",
                    "",
                    "- Status: `run_busy`",
                    "- Reason: `active run-local lease detected`",
                    f"- Lease events: `{len(lease_conflicts)}`",
                    "",
                ]
            ),
        )
        _write_run_progress_surface(
            workspace,
            runtime_config=runtime_config,
            payload=_build_run_progress_payload(
                workspace=workspace,
                source=source,
                runtime_config=runtime_config,
                status="run_busy",
                started_at=started_at,
                allowed_files=allowed_files,
                runtime_overrides=runtime_overrides,
                latest_iteration=previous_iter_name,
                loop_exit_code=None,
                changed_files=baseline_changed_files,
                new_changed_files=[],
                task_results=sorted(path.name for path in baseline_task_result_paths),
                new_task_results=[],
                validation_files=sorted(path.name for path in (workspace / ".archon" / "validation").glob("*.json")),
                lesson_file=None,
                planner_state_sync=None,
                recovery_event="active_run_local_lease",
            ),
        )
        print("status=run_busy")
        print(f"policy_events={len(lease_conflicts)}")
        return 6

    objective_files = runtime_overrides.get("objectiveFiles")
    stale_task_result_archives = _archive_stale_accepted_task_results(
        workspace,
        objective_files=objective_files if isinstance(objective_files, list) else [],
    )
    baseline_task_result_paths = sorted((workspace / ".archon" / "task_results").glob("*.md"))
    baseline_task_result_mtimes = _path_mtimes(baseline_task_result_paths)

    _update_lease(
        lease_path,
        workspace=workspace,
        source=source,
        fields={
            "active": True,
            "status": "starting",
            "supervisorPid": os.getpid(),
            "loopPid": None,
            "startedAt": started_at,
            "lastHeartbeatAt": started_at,
            "latestIteration": previous_iter_name,
            "recoveryOnly": args.recovery_only,
        },
        clear_fields=TERMINAL_LEASE_FIELDS,
    )
    _write_run_progress_surface(
        workspace,
        runtime_config=runtime_config,
        payload=_build_run_progress_payload(
            workspace=workspace,
            source=source,
            runtime_config=runtime_config,
            status="starting",
            started_at=started_at,
            allowed_files=allowed_files,
            runtime_overrides=runtime_overrides,
            latest_iteration=previous_iter_name,
            loop_exit_code=None,
            changed_files=baseline_changed_files,
            new_changed_files=[],
            task_results=sorted(path.name for path in baseline_task_result_paths),
            new_task_results=[],
            validation_files=sorted(path.name for path in (workspace / ".archon" / "validation").glob("*.json")),
            lesson_file=None,
            planner_state_sync=None,
            recovery_event=None,
        ),
    )

    def emit_live_progress(live_runtime: dict[str, object]) -> None:
        _emit_live_progress_surface(
            workspace=workspace,
            source=source,
            runtime_config=runtime_config,
            started_at=started_at,
            allowed_files=allowed_files,
            runtime_overrides=runtime_overrides,
            baseline_changed_mtimes=baseline_changed_mtimes,
            baseline_task_result_mtimes=baseline_task_result_mtimes,
            live_runtime=live_runtime,
        )

    if args.recovery_only:
        loop_result = subprocess.CompletedProcess(["recovery-only"], 0, "", "")
        idle_event = None
    else:
        loop_result, idle_event = _run_archon_loop(
            args,
            workspace,
            source,
            lease_path,
            allowed_files,
            runtime_overrides,
            progress_callback=emit_live_progress,
        )
    stdout_path, stderr_path = _write_loop_output(supervisor_dir, loop_result)
    _update_lease(
        lease_path,
        workspace=workspace,
        source=source,
        fields={
            "active": True,
            "status": "analyzing",
            "supervisorPid": os.getpid(),
            "loopPid": None,
            "lastHeartbeatAt": _now_iso(),
            "loopExitCode": loop_result.returncode,
        },
    )
    latest_iter_name, latest_meta = latest_iteration_meta(workspace)
    drifts = collect_header_drifts(source, workspace, allowed_files=allowed_files or None)
    changed_files = collect_changed_files(source, workspace, allowed_files=allowed_files or None)
    task_result_paths = sorted((workspace / ".archon" / "task_results").glob("*.md"))
    task_results = sorted(path.name for path in task_result_paths)
    if args.recovery_only:
        new_changed_files = sorted(changed_files)
        new_task_result_paths = sorted(task_result_paths)
    else:
        new_changed_files = sorted(
            rel_path
            for rel_path in changed_files
            if (workspace / rel_path).stat().st_mtime > baseline_changed_mtimes.get(workspace / rel_path, float("-inf"))
        )
        new_task_result_paths = sorted(
            path
            for path in task_result_paths
            if path.stat().st_mtime > baseline_task_result_mtimes.get(path, float("-inf"))
        )
    new_task_results = [path.name for path in new_task_result_paths]
    recovered_after_stall = None
    prover_failures = collect_meta_prover_errors(latest_meta)
    if args.recovery_only:
        recovered_after_stall = _recover_after_stall(
            workspace,
            recovery_event="verified_in_recovery",
            allow_synthesis=False,
            allowed_files=allowed_files,
            new_changed_files=new_changed_files,
            new_task_result_paths=new_task_result_paths,
            verify_template=args.changed_file_verify_template,
        )
    elif idle_event is not None or prover_failures:
        recovered_after_stall = _recover_after_stall(
            workspace,
            recovery_event="verified_after_idle" if idle_event is not None else "verified_after_stall",
            allow_synthesis=idle_event is not None,
            allowed_files=allowed_files,
            new_changed_files=new_changed_files,
            new_task_result_paths=new_task_result_paths,
            verify_template=args.changed_file_verify_template,
        )
        if recovered_after_stall is not None and recovered_after_stall.get("event") == "synthesized_blocker_after_idle":
            task_result_paths = sorted((workspace / ".archon" / "task_results").glob("*.md"))
            task_results = sorted(path.name for path in task_result_paths)
            new_task_result_paths = sorted(
                path
                for path in task_result_paths
                if path.stat().st_mtime > baseline_task_result_mtimes.get(path, float("-inf"))
            )
            new_task_results = [path.name for path in new_task_result_paths]
    created_new_iteration = latest_iter_name is not None and latest_iter_name != previous_iter_name

    events: list[dict[str, object]] = []
    if args.recovery_only:
        events.append({"event": "recovery_only"})
    for drift in drifts:
        events.append(drift.to_event())
    if prover_failures:
        events.append(
            {
                "event": "prover_error",
                "files": prover_failures,
                "iteration": latest_iter_name,
            }
        )
    if idle_event is not None:
        events.append(idle_event)
    if recovered_after_stall is not None:
        events.append(recovered_after_stall)
    if stale_task_result_archives:
        events.append(
            {
                "event": "archived_stale_accepted_task_results",
                "task_results": [item["noteName"] for item in stale_task_result_archives],
                "targets": [item["relPath"] for item in stale_task_result_archives],
                "archivePaths": [item["archivePath"] for item in stale_task_result_archives],
            }
        )
    if loop_result.returncode != 0 and not created_new_iteration:
        events.append(
            {
                "event": "no_new_iteration_meta",
                "previous_iteration": previous_iter_name,
                "latest_iteration": latest_iter_name,
            }
        )

    if not new_changed_files and not new_task_results:
        events.append({"event": "no_progress"})

    status = "clean"
    if any(event["event"] == "header_mutation" for event in events):
        status = "policy_violation"
    elif recovered_after_stall is not None and recovered_after_stall.get("event") in {
        "verified_after_idle",
        "verified_after_stall",
        "verified_in_recovery",
        "synthesized_blocker_after_idle",
    }:
        status = "clean"
    elif prover_failures:
        status = "prover_failed"
    elif idle_event is not None:
        status = "prover_idle"
    elif loop_result.returncode != 0:
        status = "loop_failed"
    elif not new_changed_files and not new_task_results:
        status = "no_progress"

    validation_files = write_validation_artifacts(
        workspace,
        status=status,
        allowed_files=allowed_files,
        changed_files=changed_files,
        drifts=drifts,
        prover_failures=prover_failures,
        iteration=latest_iter_name,
        loop_exit_code=loop_result.returncode,
        recovered_after_stall=recovered_after_stall,
    )
    lesson_file = write_lesson_artifact(
        workspace,
        status=status,
        iteration=latest_iter_name,
        allowed_files=allowed_files,
        validation_files=validation_files,
        drifts=drifts,
        prover_failures=prover_failures,
        recovered_after_stall=recovered_after_stall,
    )
    planner_state_sync = _sync_terminal_planner_state(workspace, allowed_files=allowed_files)
    if planner_state_sync is None:
        planner_state_sync = _sync_focused_planner_state(workspace, allowed_files=allowed_files)
    if planner_state_sync is not None:
        events.append(planner_state_sync)

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
        f"- Lease file: {lease_path}",
        f"- Allowed files: {', '.join(allowed_files) if allowed_files else '(all .lean files)'}",
        f"- Loop exit code: {loop_result.returncode}",
        f"- Changed files: {', '.join(changed_files) if changed_files else '(none)'}",
        f"- Task results: {', '.join(task_results) if task_results else '(none)'}",
        f"- New changed files: {', '.join(new_changed_files) if new_changed_files else '(none)'}",
        f"- New task results: {', '.join(new_task_results) if new_task_results else '(none)'}",
        f"- Policy violations: {len([event for event in events if event['event'] == 'header_mutation'])}",
        f"- Validation artifacts: {', '.join(validation_files) if validation_files else '(none)'}",
        f"- Lesson artifact: {lesson_file or '(none)'}",
    ]
    if latest_iter_name is not None:
        hot_notes.append(f"- Latest iteration: {latest_iter_name}")
    if isinstance(latest_meta, dict):
        plan_status = latest_meta.get("plan", {}).get("status") if isinstance(latest_meta.get("plan"), dict) else None
        prover_status = latest_meta.get("prover", {}).get("status") if isinstance(latest_meta.get("prover"), dict) else None
        if isinstance(plan_status, str):
            hot_notes.append(f"- Latest plan status: {plan_status}")
        if isinstance(prover_status, str):
            hot_notes.append(f"- Latest prover status: {prover_status}")
    if prover_failures:
        hot_notes.append(f"- Prover errors: {', '.join(prover_failures)}")
    if idle_event is not None:
        hot_notes.append(f"- Idle timeout triggered: {idle_event['idle_seconds']}s without prover activity")
        if idle_event.get("iteration"):
            hot_notes.append(f"- Idle iteration: {idle_event['iteration']}")
    if recovered_after_stall is not None and recovered_after_stall.get("event") in {
        "verified_after_idle",
        "verified_after_stall",
        "verified_in_recovery",
    }:
        recovery_event = recovered_after_stall.get("event")
        if recovery_event == "verified_after_idle":
            recovery_label = "idle"
        elif recovery_event == "verified_after_stall":
            recovery_label = "stall"
        else:
            recovery_label = "recovery-only pass"
        if recovered_after_stall.get("kind") == "changed_file":
            files = ", ".join(recovered_after_stall.get("files", [])) or "(none)"
            hot_notes.append(f"- Recovered after prover {recovery_label}: verified changed files {files}")
        elif recovered_after_stall.get("kind") == "task_result":
            results = ", ".join(recovered_after_stall.get("task_results", [])) or "(none)"
            hot_notes.append(f"- Recovered after prover {recovery_label}: durable task results already existed ({results})")
    if recovered_after_stall is not None and recovered_after_stall.get("event") == "synthesized_blocker_after_idle":
        results = ", ".join(recovered_after_stall.get("task_results", [])) or "(none)"
        hot_notes.append(f"- Recovered after prover idle: synthesized durable blocker note ({results})")
        provenance = recovered_after_stall.get("provenance", [])
        if isinstance(provenance, list) and provenance:
            hot_notes.append(f"- Blocker note provenance: {', '.join(str(item) for item in provenance)}")
    if recovered_after_stall is not None and recovered_after_stall.get("event") == "verification_failed_after_idle":
        files = recovered_after_stall.get("files", {})
        if isinstance(files, dict):
            for rel_path, detail in files.items():
                hot_notes.append(f"- Verification after idle failed for {rel_path}: {detail}")
        task_results_failures = recovered_after_stall.get("task_results", {})
        if isinstance(task_results_failures, dict):
            for note_name, detail in task_results_failures.items():
                hot_notes.append(f"- Verification after idle failed for task result {note_name}: {detail}")
    if runtime_overrides.get("tailScopeApplied") is True:
        objective_count = runtime_overrides.get("objectiveCount")
        plan_timeout = runtime_overrides.get("planTimeoutSeconds")
        prover_timeout = runtime_overrides.get("proverTimeoutSeconds")
        timeout_parts: list[str] = []
        if isinstance(plan_timeout, int):
            timeout_parts.append(f"plan timeout to {plan_timeout}s")
        if isinstance(prover_timeout, int):
            timeout_parts.append(f"prover timeout to {prover_timeout}s")
        if timeout_parts:
            hot_notes.append(
                f"- Tail-scope runtime override: raised {' and '.join(timeout_parts)} for {objective_count} current objectives"
            )
    if runtime_overrides.get("skipInitialPlan") is True:
        hot_notes.append(
            "- Plan fast-path: skipped the initial plan phase because every tail-scope objective already had a known route"
        )
    historical_routes_seeded = runtime_overrides.get("historicalRoutesSeeded")
    if isinstance(historical_routes_seeded, list) and historical_routes_seeded:
        hot_notes.append(f"- Historical routes preloaded: {len(historical_routes_seeded)}")
        for record in historical_routes_seeded[:8]:
            if not isinstance(record, dict):
                continue
            hot_notes.append(
                "- Historical route: "
                f"{record.get('relPath', '(unknown)')} [{record.get('kind', 'unknown')}] "
                f"from {record.get('campaignId', '(unknown)')}/{record.get('runId', '(unknown)')} "
                f"-> {record.get('noteRelPath', '(unknown)')}"
            )
    if stale_task_result_archives:
        hot_notes.append(
            "- Pre-cycle cleanup: archived stale accepted task results "
            + ", ".join(item["noteName"] for item in stale_task_result_archives)
        )
    if planner_state_sync is not None:
        if planner_state_sync.get("status") == "terminal_complete":
            hot_notes.append("- Planner state synced: wrote terminal closure to .archon/PROGRESS.md, task_pending.md, and task_done.md")
        elif planner_state_sync.get("status") == "focused_remaining_scope":
            hot_notes.append("- Planner state synced: removed accepted targets from the next-cycle objective list and refreshed task_pending.md/task_done.md")
    for drift in drifts:
        hot_notes.append(f"- Violation: {drift.rel_path}::{drift.declaration_name} -> {drift.mutation_class}")
    if loop_result.returncode != 0 and not created_new_iteration:
        hot_notes.append("- No new iteration metadata was created during this cycle; the failure happened before Archon initialized a fresh iter-* directory.")
    stdout_tail = _tail_text(loop_result.stdout)
    stderr_tail = _tail_text(loop_result.stderr)
    if stdout_tail:
        hot_notes.append(f"- Last archon-loop stdout log: {stdout_path}")
        hot_notes.append("```")
        hot_notes.extend(stdout_tail.splitlines())
        hot_notes.append("```")
    if stderr_tail:
        hot_notes.append(f"- Last archon-loop stderr log: {stderr_path}")
        hot_notes.append("```")
        hot_notes.extend(stderr_tail.splitlines())
        hot_notes.append("```")
    _write_text(hot_notes_path, "\n".join(hot_notes) + "\n")

    ledger_lines = [
        f"## Cycle {started_at}",
        "",
        f"- Status: `{status}`",
        f"- Lease file: `{lease_path}`",
        f"- Loop exit code: `{loop_result.returncode}`",
        f"- Changed files: `{', '.join(changed_files) if changed_files else '(none)'}`",
        f"- Task results: `{', '.join(task_results) if task_results else '(none)'}`",
        f"- New changed files: `{', '.join(new_changed_files) if new_changed_files else '(none)'}`",
        f"- New task results: `{', '.join(new_task_results) if new_task_results else '(none)'}`",
        f"- Policy events: `{len(events)}`",
        f"- Latest iteration: `{latest_iter_name or '(none)'}`",
        f"- Prover errors: `{', '.join(prover_failures) if prover_failures else '(none)'}`",
        f"- Validation artifacts: `{', '.join(validation_files) if validation_files else '(none)'}`",
        f"- Lesson artifact: `{lesson_file or '(none)'}`",
        f"- Idle timeout: `{idle_event['idle_seconds']}s`" if idle_event is not None else "- Idle timeout: `(none)`",
        f"- Idle recovery: `{recovered_after_stall['event']}`" if recovered_after_stall is not None else "- Idle recovery: `(none)`",
        (
            f"- Pre-cycle cleanup: `{', '.join(item['noteName'] for item in stale_task_result_archives)}`"
            if stale_task_result_archives
            else "- Pre-cycle cleanup: `(none)`"
        ),
        f"- Planner state sync: `{planner_state_sync['status']}`" if planner_state_sync is not None else "- Planner state sync: `(none)`",
        f"- Recovery only: `{args.recovery_only}`",
        f"- New iteration created: `{created_new_iteration}`",
        "",
    ]
    _append_text(ledger_path, "\n".join(ledger_lines))

    _update_lease(
        lease_path,
        workspace=workspace,
        source=source,
        fields={
            "active": False,
            "status": "completed",
            "supervisorPid": os.getpid(),
            "loopPid": None,
            "lastHeartbeatAt": _now_iso(),
            "latestIteration": latest_iter_name,
            "loopExitCode": loop_result.returncode,
            "finalStatus": status,
            "completedAt": _now_iso(),
            "recoveryEvent": recovered_after_stall.get("event") if isinstance(recovered_after_stall, dict) else None,
            "validationFiles": validation_files,
            "lessonFile": lesson_file,
        },
    )
    _write_run_progress_surface(
        workspace,
        runtime_config=runtime_config,
        payload=_build_run_progress_payload(
            workspace=workspace,
            source=source,
            runtime_config=runtime_config,
            status=status,
            started_at=started_at,
            allowed_files=allowed_files,
            runtime_overrides=runtime_overrides,
            latest_iteration=latest_iter_name,
            loop_exit_code=loop_result.returncode,
            changed_files=changed_files,
            new_changed_files=new_changed_files,
            task_results=task_results,
            new_task_results=new_task_results,
            validation_files=validation_files,
            lesson_file=lesson_file,
            planner_state_sync=planner_state_sync,
            recovery_event=recovered_after_stall.get("event") if isinstance(recovered_after_stall, dict) else None,
        ),
    )

    print(f"status={status}")
    print(f"changed_files={len(changed_files)}")
    print(f"new_changed_files={len(new_changed_files)}")
    print(f"task_results={len(task_results)}")
    print(f"new_task_results={len(new_task_results)}")
    print(f"policy_events={len(events)}")

    if status == "policy_violation":
        return 2
    if status == "prover_failed":
        return 3
    if status == "no_progress":
        return 4
    if status == "prover_idle":
        return 5
    if status == "loop_failed":
        return loop_result.returncode or 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
