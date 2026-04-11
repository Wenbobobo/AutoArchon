import json
from pathlib import Path

from archonlib.lake_prewarm import (
    build_env,
    find_broken_packages,
    has_warmed_mathlib_cache,
    load_manifest,
    remove_broken_packages,
)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_find_broken_packages_flags_missing_config_files(tmp_path: Path):
    write(
        tmp_path / "lake-manifest.json",
        json.dumps(
            {
                "packagesDir": ".lake/packages",
                "packages": [
                    {"name": "mathlib", "configFile": "lakefile.lean"},
                    {"name": "Cli", "configFile": "lakefile.toml"},
                ],
            }
        ),
    )
    write(tmp_path / ".lake/packages/mathlib/.git/HEAD", "ref: refs/heads/main\n")
    write(tmp_path / ".lake/packages/Cli/lakefile.toml", "name = \"Cli\"\n")

    manifest = load_manifest(tmp_path / "lake-manifest.json")
    broken = find_broken_packages(tmp_path, manifest)

    assert broken == [tmp_path / ".lake/packages/mathlib"]


def test_remove_broken_packages_preserves_healthy_checkouts(tmp_path: Path):
    write(
        tmp_path / "lake-manifest.json",
        json.dumps(
            {
                "packagesDir": ".lake/packages",
                "packages": [
                    {"name": "broken", "configFile": "lakefile.lean"},
                    {"name": "healthy", "configFile": "lakefile.toml"},
                ],
            }
        ),
    )
    write(tmp_path / ".lake/packages/broken/.git/HEAD", "ref: refs/heads/main\n")
    write(tmp_path / ".lake/packages/healthy/lakefile.toml", "name = \"healthy\"\n")

    manifest = load_manifest(tmp_path / "lake-manifest.json")
    removed = remove_broken_packages(tmp_path, manifest)

    assert removed == [tmp_path / ".lake/packages/broken"]
    assert not (tmp_path / ".lake/packages/broken").exists()
    assert (tmp_path / ".lake/packages/healthy").exists()


def test_build_env_sets_expected_defaults(monkeypatch):
    monkeypatch.delenv("GIT_TERMINAL_PROMPT", raising=False)
    monkeypatch.delenv("LEAN_BUILD_CONCURRENCY", raising=False)
    monkeypatch.delenv("MATHLIB_CACHE_USE_CLOUDFLARE", raising=False)

    env = build_env(use_cloudflare=True)

    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["LEAN_BUILD_CONCURRENCY"] == "share"
    assert env["MATHLIB_CACHE_USE_CLOUDFLARE"] == "1"


def test_has_warmed_mathlib_cache_detects_copied_build_outputs(tmp_path: Path):
    write(tmp_path / ".lake/packages/mathlib/.lake/build/lib/lean/Mathlib.olean", "")

    assert has_warmed_mathlib_cache(tmp_path) is True


def test_has_warmed_mathlib_cache_returns_false_without_mathlib_olean(tmp_path: Path):
    write(tmp_path / ".lake/packages/mathlib/lakefile.lean", "import Lake\n")

    assert has_warmed_mathlib_cache(tmp_path) is False
