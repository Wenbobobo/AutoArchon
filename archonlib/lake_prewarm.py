from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ManifestPackage:
    name: str
    config_file: str

    @classmethod
    def from_json(cls, payload: dict[str, object]) -> "ManifestPackage":
        config_file = str(payload.get("configFile") or "").strip()
        return cls(
            name=str(payload["name"]),
            config_file=config_file,
        )


@dataclass(frozen=True)
class Manifest:
    packages_dir: str
    packages: tuple[ManifestPackage, ...]

    @classmethod
    def from_json(cls, payload: dict[str, object]) -> "Manifest":
        packages = tuple(
            ManifestPackage.from_json(entry)
            for entry in payload.get("packages", [])
            if isinstance(entry, dict)
        )
        return cls(
            packages_dir=str(payload.get("packagesDir") or ".lake/packages"),
            packages=packages,
        )


def load_manifest(manifest_path: Path) -> Manifest:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    return Manifest.from_json(payload)


def package_path(project_path: Path, manifest: Manifest, package: ManifestPackage) -> Path:
    return project_path / manifest.packages_dir / package.name


def package_config_path(project_path: Path, manifest: Manifest, package: ManifestPackage) -> Path | None:
    if not package.config_file:
        return None
    return package_path(project_path, manifest, package) / package.config_file


def find_broken_packages(project_path: Path, manifest: Manifest) -> list[Path]:
    broken: list[Path] = []
    for package in manifest.packages:
        root = package_path(project_path, manifest, package)
        if not root.exists():
            continue
        config_path = package_config_path(project_path, manifest, package)
        if config_path is not None and not config_path.exists():
            broken.append(root)
    return broken


def remove_broken_packages(project_path: Path, manifest: Manifest) -> list[Path]:
    removed: list[Path] = []
    for root in find_broken_packages(project_path, manifest):
        shutil.rmtree(root)
        removed.append(root)
    return removed


def run_with_retries(
    cmd: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    retries: int,
    backoff_seconds: int,
) -> None:
    attempts = retries + 1
    for attempt in range(1, attempts + 1):
        result = subprocess.run(cmd, cwd=cwd, env=env, check=False)
        if result.returncode == 0:
            return
        if attempt == attempts:
            raise SystemExit(result.returncode)
        wait_seconds = backoff_seconds * attempt
        print(
            f"[prewarm] command failed ({result.returncode}); retrying in {wait_seconds}s: {' '.join(cmd)}",
            file=sys.stderr,
        )
        time.sleep(wait_seconds)


def build_env(use_cloudflare: bool) -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    env.setdefault("LEAN_BUILD_CONCURRENCY", "share")
    if use_cloudflare:
        env.setdefault("MATHLIB_CACHE_USE_CLOUDFLARE", "1")
    return env


def has_warmed_mathlib_cache(project_path: Path) -> bool:
    return (project_path / ".lake/packages/mathlib/.lake/build/lib/lean/Mathlib.olean").exists()
