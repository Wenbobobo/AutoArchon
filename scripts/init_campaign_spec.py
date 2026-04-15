#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from archonlib.operator_surfaces import ensure_operator_surfaces


ENV_PATTERN = re.compile(r"\$(?:\{(?P<braced>[A-Za-z_][A-Za-z0-9_]*)\}|(?P<plain>[A-Za-z_][A-Za-z0-9_]*))")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Resolve a tracked AutoArchon campaign template into a launchable spec file under the run-spec root."
    )
    parser.add_argument("--template", required=True, help="Tracked template under campaign_specs/ or an explicit JSON path")
    parser.add_argument("--benchmark-root", help="Compatibility name for the root that contains dataset or source clones")
    parser.add_argument("--source-roots-root", help="Generic root that contains source clones for formalization or benchmark campaigns")
    parser.add_argument("--campaigns-root", required=True, help="Root directory for campaign outputs")
    parser.add_argument("--run-specs-root", required=True, help="Root directory for generated run specs and resolved launch specs")
    parser.add_argument("--date-tag", required=True, help="Stable date or run tag used in campaign and spec naming")
    parser.add_argument("--model", default="gpt-5.4", help="Teacher and watchdog model name")
    parser.add_argument("--reasoning-effort", default="xhigh", help="Teacher and watchdog reasoning effort")
    parser.add_argument("--shard-size", type=int, help="Optional override for planShards.shardSize")
    parser.add_argument("--output", help="Optional explicit path for the resolved launch spec")
    parser.add_argument("--dry-run", action="store_true", help="Print the resolved spec without writing it")
    return parser.parse_args()


def _load_template(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("campaign template must be a JSON object")
    return payload


def _replace_placeholders(value: str, mapping: dict[str, str]) -> str:
    def repl(match: re.Match[str]) -> str:
        key = match.group("braced") or match.group("plain")
        assert key is not None
        return mapping.get(key, match.group(0))

    return ENV_PATTERN.sub(repl, value)


def _resolve_template(value: Any, *, mapping: dict[str, str]) -> Any:
    if isinstance(value, str):
        return _replace_placeholders(value, mapping)
    if isinstance(value, list):
        return [_resolve_template(item, mapping=mapping) for item in value]
    if isinstance(value, dict):
        return {key: _resolve_template(item, mapping=mapping) for key, item in value.items()}
    return value


def _drop_unresolved_optional_environment(spec: dict[str, Any]) -> dict[str, Any]:
    environment = spec.get("environment")
    if not isinstance(environment, dict):
        return spec
    cleaned = {
        key: value
        for key, value in environment.items()
        if not (isinstance(value, str) and ENV_PATTERN.search(value))
    }
    updated = dict(spec)
    if cleaned:
        updated["environment"] = cleaned
    else:
        updated.pop("environment", None)
    return updated


def _template_path(raw: str) -> Path:
    candidate = Path(raw)
    if candidate.is_absolute() or candidate.exists():
        return candidate.resolve()
    project_candidate = ROOT / "campaign_specs" / raw
    if project_candidate.exists():
        return project_candidate.resolve()
    raise FileNotFoundError(f"campaign template not found: {raw}")


def _default_output_path(template_path: Path, *, run_specs_root: Path, date_tag: str) -> Path:
    slug = template_path.stem
    return (run_specs_root / f"{date_tag}-{slug}.launch.json").resolve()


def _resolve_spec(args: argparse.Namespace) -> tuple[Path, dict[str, Any]]:
    template_path = _template_path(args.template)
    template = _load_template(template_path)
    source_roots_root = args.source_roots_root or args.benchmark_root
    if not source_roots_root:
        raise ValueError("either --source-roots-root or --benchmark-root is required")
    mapping = dict(os.environ)
    mapping.update(
        {
            "BENCHMARK_ROOT": str(Path(source_roots_root).resolve()),
            "SOURCE_ROOTS_ROOT": str(Path(source_roots_root).resolve()),
            "CAMPAIGNS_ROOT": str(Path(args.campaigns_root).resolve()),
            "RUN_SPECS_ROOT": str(Path(args.run_specs_root).resolve()),
            "FATE_DATE_TAG": args.date_tag,
            "MODEL": args.model,
            "REASONING_EFFORT": args.reasoning_effort,
        }
    )
    resolved = _resolve_template(template, mapping=mapping)
    if args.shard_size is not None:
        plan_shards = resolved.get("planShards")
        if not isinstance(plan_shards, dict):
            raise ValueError("template planShards must be a JSON object when --shard-size is used")
        plan_shards = dict(plan_shards)
        plan_shards["shardSize"] = args.shard_size
        resolved["planShards"] = plan_shards
    resolved = _drop_unresolved_optional_environment(resolved)
    output = Path(args.output).resolve() if args.output else _default_output_path(
        template_path,
        run_specs_root=Path(args.run_specs_root).resolve(),
        date_tag=args.date_tag,
    )
    return output, resolved


def main() -> int:
    args = parse_args()
    output_path, payload = _resolve_spec(args)
    rendered = json.dumps(payload, indent=2, sort_keys=True)
    if not args.dry_run:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered + "\n", encoding="utf-8")
        ensure_operator_surfaces(
            Path(str(payload["campaignRoot"])),
            source_root=Path(str(payload["sourceRoot"])),
            spec_reference=str(output_path),
            resolved_spec=payload,
            mode="resolved_spec_scaffold",
            entrypoint="autoarchon-init-campaign-spec",
            note="Scaffolded operator surfaces from a tracked template before launch.",
        )
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
