#!/usr/bin/env python3

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from archonlib.lake_prewarm import (
    build_env,
    has_warmed_mathlib_cache,
    load_manifest,
    remove_broken_packages,
    run_cache_get_with_fallback,
    run_with_retries,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prewarm a Lean/Lake project with cache and build or scoped verify retries.")
    parser.add_argument("project", help="Path to the Lean project")
    parser.add_argument(
        "--cache-repo",
        default="leanprover-community/mathlib4",
        help="Repository argument passed to `lake exe cache get --repo=...`",
    )
    parser.add_argument(
        "--cache-retries",
        type=int,
        default=1,
        help="Number of retries for `lake exe cache get` after the initial attempt",
    )
    parser.add_argument(
        "--build-retries",
        type=int,
        default=1,
        help="Number of retries for `lake build` after the initial attempt",
    )
    parser.add_argument(
        "--retry-backoff-seconds",
        type=int,
        default=15,
        help="Base backoff duration between retries",
    )
    parser.add_argument(
        "--skip-cache",
        action="store_true",
        help="Skip `lake exe cache get` and only run `lake build`",
    )
    parser.add_argument(
        "--verify-file",
        action="append",
        dest="verify_files",
        default=[],
        help="Relative `.lean` file to verify with `lake env lean` instead of running full `lake build`",
    )
    parser.add_argument(
        "--no-cloudflare",
        action="store_true",
        help="Do not set `MATHLIB_CACHE_USE_CLOUDFLARE=1`",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_path = Path(args.project).resolve()
    if not project_path.exists():
        raise SystemExit(f"project not found: {project_path}")
    if not (project_path / "lean-toolchain").exists():
        raise SystemExit(f"lean-toolchain not found: {project_path}")

    manifest_path = project_path / "lake-manifest.json"
    if manifest_path.exists():
        removed = remove_broken_packages(project_path, load_manifest(manifest_path))
        for path in removed:
            print(f"[prewarm] removed broken package checkout: {path}")

    env = build_env(use_cloudflare=not args.no_cloudflare)

    skip_cache = args.skip_cache
    if not skip_cache and has_warmed_mathlib_cache(project_path):
        print("[prewarm] detected warmed mathlib cache in project .lake; skipping `lake exe cache get`")
        skip_cache = True

    if not skip_cache:
        run_cache_get_with_fallback(
            cwd=project_path,
            env=env,
            cache_repo=args.cache_repo,
            retries=args.cache_retries,
            backoff_seconds=args.retry_backoff_seconds,
        )

    if args.verify_files:
        for rel_path in args.verify_files:
            verify_path = project_path / rel_path
            if not verify_path.exists():
                raise SystemExit(f"verify file not found: {verify_path}")
            print(f"[prewarm] using scoped verify instead of `lake build`: {rel_path}")
            run_with_retries(
                ["lake", "env", "lean", rel_path],
                cwd=project_path,
                env=env,
                retries=args.build_retries,
                backoff_seconds=args.retry_backoff_seconds,
            )
        return 0

    run_with_retries(
        ["lake", "build"],
        cwd=project_path,
        env=env,
        retries=args.build_retries,
        backoff_seconds=args.retry_backoff_seconds,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
