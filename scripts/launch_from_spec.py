#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from archonlib.campaign import create_campaign, ensure_campaign_control_root, plan_campaign_shards, refresh_campaign_launch_assets


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create or reuse an AutoArchon campaign from a JSON spec, then optionally start the watchdog."
    )
    parser.add_argument("--spec-file", required=True, help="JSON launch spec")
    parser.add_argument(
        "--shard-size",
        type=int,
        help="Override planShards.shardSize without mutating the tracked spec template",
    )
    parser.add_argument(
        "--replan",
        action="store_true",
        help="Regenerate run specs even when the run-spec output file already exists",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve the spec and print the planned actions without writing files or starting the watchdog",
    )
    parser.add_argument(
        "--no-start-watchdog",
        action="store_true",
        help="Create or refresh the campaign but do not start autoarchon-orchestrator-watchdog",
    )
    parser.add_argument(
        "--refresh-launch-assets",
        action="store_true",
        help="Refresh launch-teacher.sh and related prompts for an existing campaign before starting the watchdog",
    )
    return parser.parse_args()


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        return os.path.expanduser(os.path.expandvars(value))
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    if isinstance(value, dict):
        return {key: _expand_env(item) for key, item in value.items()}
    return value


def _resolve_path(raw: str | None, *, base_dir: Path) -> Path | None:
    if raw is None:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = (base_dir / path).resolve()
    else:
        path = path.resolve()
    return path


def _as_int(value: object, *, field: str) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return int(value)
        except ValueError as exc:
            raise ValueError(f"{field} must be an integer") from exc
    raise ValueError(f"{field} must be an integer")


UNRESOLVED_ENV_RE = re.compile(r"^\$(?:\{[A-Za-z_][A-Za-z0-9_]*\}|[A-Za-z_][A-Za-z0-9_]*)$")


def _is_unresolved_env_placeholder(value: object) -> bool:
    return isinstance(value, str) and bool(UNRESOLVED_ENV_RE.fullmatch(value.strip()))


def _load_spec(spec_file: Path) -> dict[str, Any]:
    raw = json.loads(spec_file.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("launch spec must be a JSON object")
    payload = _expand_env(raw)
    base_dir = spec_file.parent.resolve()

    source_root_raw = payload.get("sourceRoot")
    campaign_root_raw = payload.get("campaignRoot")
    if not isinstance(source_root_raw, str) or not source_root_raw.strip():
        raise ValueError("launch spec requires sourceRoot")
    if not isinstance(campaign_root_raw, str) or not campaign_root_raw.strip():
        raise ValueError("launch spec requires campaignRoot")

    resolved: dict[str, Any] = dict(payload)
    resolved["sourceRoot"] = str(_resolve_path(source_root_raw, base_dir=base_dir))
    resolved["campaignRoot"] = str(_resolve_path(campaign_root_raw, base_dir=base_dir))

    for key in ("reuseLakeFrom", "runSpecFile", "runSpecOutput"):
        raw_value = resolved.get(key)
        if isinstance(raw_value, str) and raw_value.strip():
            resolved[key] = str(_resolve_path(raw_value, base_dir=base_dir))

    return resolved


def _plan_shards_config(spec: dict[str, Any], *, shard_size_override: int | None) -> dict[str, Any]:
    payload = spec.get("planShards", {})
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ValueError("planShards must be a JSON object when provided")
    config = dict(payload)
    for key in ("matchRegex", "shardSize", "runIdPrefix", "runIdMode", "limit", "startIndex"):
        if key not in config and key in spec:
            config[key] = spec[key]
    if shard_size_override is not None:
        config["shardSize"] = shard_size_override
    return config


def _run_spec_output_path(spec: dict[str, Any]) -> Path | None:
    raw_path = spec.get("runSpecOutput") or spec.get("runSpecFile")
    return Path(raw_path) if isinstance(raw_path, str) and raw_path else None


def _load_existing_run_specs(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"run-spec file must contain a JSON array: {path}")
    run_specs = [item for item in payload if isinstance(item, dict)]
    if not run_specs:
        raise ValueError(f"run-spec file did not contain any run-spec objects: {path}")
    return run_specs


def _resolve_run_specs(
    spec: dict[str, Any],
    *,
    shard_size_override: int | None,
    replan: bool,
    dry_run: bool,
) -> tuple[list[dict[str, Any]], Path | None, bool]:
    explicit = spec.get("runSpecs")
    if isinstance(explicit, list) and explicit:
        run_specs = [item for item in explicit if isinstance(item, dict)]
        if not run_specs:
            raise ValueError("runSpecs must contain JSON objects")
        return run_specs, _run_spec_output_path(spec), False

    run_spec_output = _run_spec_output_path(spec)
    if run_spec_output is not None and run_spec_output.exists() and not replan:
        return _load_existing_run_specs(run_spec_output), run_spec_output, False

    plan_config = _plan_shards_config(spec, shard_size_override=shard_size_override)
    run_specs = plan_campaign_shards(
        Path(spec["sourceRoot"]),
        run_id_prefix=str(plan_config.get("runIdPrefix") or "teacher"),
        run_id_mode=str(plan_config.get("runIdMode") or "index"),
        include_regex=str(plan_config.get("matchRegex")) if isinstance(plan_config.get("matchRegex"), str) else None,
        limit=_as_int(plan_config.get("limit"), field="planShards.limit") if plan_config.get("limit") is not None else None,
        shard_size=_as_int(plan_config.get("shardSize") or 1, field="planShards.shardSize"),
        start_index=_as_int(plan_config.get("startIndex") or 1, field="planShards.startIndex"),
    )
    if run_spec_output is not None and not dry_run:
        run_spec_output.parent.mkdir(parents=True, exist_ok=True)
        run_spec_output.write_text(json.dumps(run_specs, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return run_specs, run_spec_output, True


def _pid_is_live(pid: int | None) -> bool:
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _build_watchdog_command(campaign_root: Path, spec: dict[str, Any]) -> tuple[list[str], dict[str, str]]:
    watchdog = spec.get("watchdog", {})
    if watchdog is None:
        watchdog = {}
    if not isinstance(watchdog, dict):
        raise ValueError("watchdog must be a JSON object when provided")

    model = str(watchdog.get("model") or spec.get("teacherModel") or "gpt-5.4")
    reasoning_effort = str(watchdog.get("reasoningEffort") or spec.get("teacherReasoningEffort") or "xhigh")
    watchdog_exec_override = os.environ.get("ARCHON_WATCHDOG_EXECUTABLE")
    if watchdog_exec_override:
        command = shlex.split(watchdog_exec_override)
    else:
        command = [
            sys.executable,
            str(ROOT / "scripts" / "orchestrator_watchdog.py"),
        ]
    command.extend(
        [
        "--campaign-root",
        str(campaign_root),
        "--model",
        model,
        "--reasoning-effort",
        reasoning_effort,
        "--poll-seconds",
        str(_as_int(watchdog.get("pollSeconds") or 30, field="watchdog.pollSeconds")),
        "--stall-seconds",
        str(_as_int(watchdog.get("stallSeconds") or 300, field="watchdog.stallSeconds")),
        "--owner-silence-seconds",
        str(_as_int(watchdog.get("ownerSilenceSeconds") or 1200, field="watchdog.ownerSilenceSeconds")),
        "--bootstrap-launch-after-seconds",
        str(_as_int(watchdog.get("bootstrapLaunchAfterSeconds") or 45, field="watchdog.bootstrapLaunchAfterSeconds")),
        "--max-restarts",
        str(_as_int(watchdog.get("maxRestarts") or 3, field="watchdog.maxRestarts")),
        "--max-active-launches",
        str(_as_int(watchdog.get("maxActiveLaunches") or 2, field="watchdog.maxActiveLaunches")),
        "--launch-batch-size",
        str(_as_int(watchdog.get("launchBatchSize") or 1, field="watchdog.launchBatchSize")),
        "--launch-cooldown-seconds",
        str(_as_int(watchdog.get("launchCooldownSeconds") or 90, field="watchdog.launchCooldownSeconds")),
        ]
    )
    if watchdog.get("finalizeOnTerminal") is False:
        command.append("--no-finalize")

    child_env = os.environ.copy()
    extra_env = spec.get("environment", {})
    if extra_env is not None and not isinstance(extra_env, dict):
        raise ValueError("environment must be a JSON object when provided")
    if isinstance(extra_env, dict):
        for key, value in extra_env.items():
            if isinstance(key, str) and isinstance(value, (str, int, float)):
                if _is_unresolved_env_placeholder(value):
                    continue
                child_env[key] = str(value)
    return command, child_env


def _start_watchdog(campaign_root: Path, spec: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    control_root = campaign_root / "control"
    pid_file = control_root / "watchdog-launch.pid"
    stdout_log = control_root / "watchdog-launch.stdout.log"
    stderr_log = control_root / "watchdog-launch.stderr.log"
    command, child_env = _build_watchdog_command(campaign_root, spec)

    if dry_run:
        return {
            "status": "dry_run",
            "pid": None,
            "pidFile": str(pid_file),
            "stdoutLog": str(stdout_log),
            "stderrLog": str(stderr_log),
            "command": command,
        }

    existing_pid: int | None = None
    if pid_file.exists():
        try:
            existing_pid = int(pid_file.read_text(encoding="utf-8").strip())
        except ValueError:
            existing_pid = None
    if _pid_is_live(existing_pid):
        return {
            "status": "already_running",
            "pid": existing_pid,
            "pidFile": str(pid_file),
            "stdoutLog": str(stdout_log),
            "stderrLog": str(stderr_log),
            "command": command,
        }

    stdout_handle = stdout_log.open("a", encoding="utf-8")
    stderr_handle = stderr_log.open("a", encoding="utf-8")
    try:
        proc = subprocess.Popen(
            command,
            cwd=str(ROOT),
            env=child_env,
            stdin=subprocess.DEVNULL,
            stdout=stdout_handle,
            stderr=stderr_handle,
            text=True,
            start_new_session=True,
        )
    finally:
        stdout_handle.close()
        stderr_handle.close()
    time.sleep(1.0)
    if proc.poll() is not None:
        if pid_file.exists():
            pid_file.unlink()
        return {
            "status": "failed",
            "pid": proc.pid,
            "pidFile": str(pid_file),
            "stdoutLog": str(stdout_log),
            "stderrLog": str(stderr_log),
            "exitCode": proc.returncode,
            "command": command,
        }
    pid_file.write_text(f"{proc.pid}\n", encoding="utf-8")
    return {
        "status": "started",
        "pid": proc.pid,
        "pidFile": str(pid_file),
        "stdoutLog": str(stdout_log),
        "stderrLog": str(stderr_log),
        "command": command,
    }


def main() -> int:
    args = parse_args()
    spec_file = Path(args.spec_file).resolve()
    spec = _load_spec(spec_file)
    campaign_root = Path(spec["campaignRoot"])
    source_root = Path(spec["sourceRoot"])
    reuse_lake_from = Path(spec["reuseLakeFrom"]) if isinstance(spec.get("reuseLakeFrom"), str) else None

    if not (source_root / "lean-toolchain").exists():
        raise FileNotFoundError(f"benchmark root is not a Lean project: {source_root}")

    run_specs, run_spec_path, planned_run_specs = _resolve_run_specs(
        spec,
        shard_size_override=args.shard_size,
        replan=args.replan,
        dry_run=args.dry_run,
    )

    created = False
    refreshed_runs: list[dict[str, Any]] = []
    refresh_requested = args.refresh_launch_assets or spec.get("refreshLaunchAssets") is True
    if not args.dry_run:
        if not campaign_root.exists():
            create_campaign(
                archon_root=ROOT,
                source_root=source_root,
                campaign_root=campaign_root,
                run_specs=run_specs,
                reuse_lake_from=reuse_lake_from,
                teacher_model=str(spec.get("teacherModel") or "gpt-5.4"),
                teacher_reasoning_effort=str(spec.get("teacherReasoningEffort") or "xhigh"),
                teacher_scope_policy=str(spec.get("teacherScopePolicy") or "single_file_micro_shard"),
                plan_timeout_seconds=_as_int(spec.get("planTimeoutSeconds") or 180, field="planTimeoutSeconds"),
                prover_timeout_seconds=_as_int(spec.get("proverTimeoutSeconds") or 240, field="proverTimeoutSeconds"),
                prover_idle_seconds=_as_int(spec.get("proverIdleSeconds") or 90, field="proverIdleSeconds"),
            )
            created = True
        elif refresh_requested:
            refreshed_payload = refresh_campaign_launch_assets(
                campaign_root,
                refresh_prompts=bool(spec.get("refreshPrompts")),
            )
            refreshed_runs = refreshed_payload["refreshedRuns"]

    watchdog_config = spec.get("watchdog", {})
    watchdog_enabled = not args.no_start_watchdog and bool(
        watchdog_config.get("enabled", True) if isinstance(watchdog_config, dict) else True
    )

    control_root = campaign_root / "control"
    resolved_spec_path = control_root / "launch-spec.resolved.json"
    if not args.dry_run:
        control_root = ensure_campaign_control_root(
            campaign_root,
            owner_mode="campaign_operator",
            watchdog_enabled=watchdog_enabled,
            manager_enabled=False,
            owner_entrypoint="autoarchon-launch-from-spec",
        )
        resolved_spec_path = control_root / "launch-spec.resolved.json"
        resolved_spec_path.write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    watchdog_payload = {"status": "disabled", "command": None}
    if watchdog_enabled:
        watchdog_payload = _start_watchdog(campaign_root, spec, dry_run=args.dry_run)

    payload = {
        "specFile": str(spec_file),
        "campaignRoot": str(campaign_root),
        "campaignCreated": created,
        "sourceRoot": str(source_root),
        "runSpecFile": str(run_spec_path) if run_spec_path is not None else None,
        "runSpecCount": len(run_specs),
        "plannedRunSpecs": planned_run_specs,
        "refreshLaunchAssets": refresh_requested,
        "refreshedRuns": refreshed_runs,
        "dryRun": args.dry_run,
        "resolvedSpecPath": str(resolved_spec_path),
        "watchdog": watchdog_payload,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    watchdog_status = watchdog_payload.get("status") if isinstance(watchdog_payload, dict) else None
    if watchdog_enabled and watchdog_status not in {"started", "already_running", "dry_run"}:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
