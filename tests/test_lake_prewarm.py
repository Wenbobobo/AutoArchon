import os
import json
import subprocess
from pathlib import Path

from archonlib.lake_prewarm import (
    build_env,
    run_cache_get_with_fallback,
    cache_get_unavailable,
    find_broken_packages,
    has_warmed_mathlib_cache,
    load_manifest,
    remove_broken_packages,
)


ROOT = Path(__file__).resolve().parents[1]
PREWARM_PROJECT = ROOT / "scripts" / "prewarm_project.py"


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


def test_run_cache_get_with_fallback_retries_without_repo(monkeypatch, tmp_path: Path, capsys):
    calls: list[list[str]] = []

    def fake_run(cmd, cwd, env, check, capture_output, text):
        calls.append(cmd)
        if cmd[-2:] == ["--repo", "leanprover-community/mathlib4"]:
            return subprocess.CompletedProcess(
                cmd,
                1,
                "",
                "Invalid argument: non-existing path leanprover-community/mathlib4\n",
            )
        return subprocess.CompletedProcess(cmd, 0, "cache ready\n", "")

    monkeypatch.setattr("archonlib.lake_prewarm.subprocess.run", fake_run)

    run_cache_get_with_fallback(
        cwd=tmp_path,
        env={"PATH": ""},
        cache_repo="leanprover-community/mathlib4",
        retries=1,
        backoff_seconds=1,
    )

    captured = capsys.readouterr()
    assert calls == [
        ["lake", "exe", "cache", "get", "--repo", "leanprover-community/mathlib4"],
        ["lake", "exe", "cache", "get"],
    ]
    assert "rejected `--repo`" in captured.err
    assert "cache ready" in captured.out


def test_cache_get_unavailable_detects_projects_without_cache_executable():
    assert cache_get_unavailable("error: unknown executable cache\n") is True
    assert cache_get_unavailable("error: unknown executable `cache`\n") is True
    assert cache_get_unavailable("other failure\n") is False


def test_run_cache_get_with_fallback_skips_when_cache_executable_is_unavailable(monkeypatch, tmp_path: Path, capsys):
    calls: list[list[str]] = []

    def fake_run(cmd, cwd, env, check, capture_output, text):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 1, "", "error: unknown executable cache\n")

    monkeypatch.setattr("archonlib.lake_prewarm.subprocess.run", fake_run)

    result = run_cache_get_with_fallback(
        cwd=tmp_path,
        env={"PATH": ""},
        cache_repo="leanprover-community/mathlib4",
        retries=1,
        backoff_seconds=1,
    )

    captured = capsys.readouterr()
    assert result is False
    assert calls == [["lake", "exe", "cache", "get", "--repo", "leanprover-community/mathlib4"]]
    assert "skipping cache download" in captured.err


def test_prewarm_project_verify_file_uses_scoped_lake_env_lean(tmp_path: Path):
    project = tmp_path / "project"
    write(project / "lean-toolchain", "leanprover/lean4:v4.28.0\n")
    write(project / "Foo.lean", "theorem foo : True := by\n  trivial\n")

    fake_bin = tmp_path / "bin"
    fake_lake = fake_bin / "lake"
    lake_log = tmp_path / "lake.log"
    write(
        fake_lake,
        f"""#!/usr/bin/env python3
import os
import sys
from pathlib import Path

path = Path({str(lake_log)!r})
path.parent.mkdir(parents=True, exist_ok=True)
with path.open("a", encoding="utf-8") as handle:
    handle.write(" ".join(sys.argv[1:]) + "\\n")
raise SystemExit(0)
""",
    )
    fake_lake.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"

    result = subprocess.run(
        [
            "python3",
            str(PREWARM_PROJECT),
            str(project),
            "--skip-cache",
            "--verify-file",
            "Foo.lean",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert "scoped verify" in result.stdout
    assert lake_log.read_text(encoding="utf-8").splitlines() == ["env lean Foo.lean"]


def test_prewarm_project_multiple_verify_files_run_in_order(tmp_path: Path):
    project = tmp_path / "project"
    write(project / "lean-toolchain", "leanprover/lean4:v4.28.0\n")
    write(project / "Foo.lean", "theorem foo : True := by\n  trivial\n")
    write(project / "Bar.lean", "theorem bar : True := by\n  trivial\n")

    fake_bin = tmp_path / "bin"
    fake_lake = fake_bin / "lake"
    lake_log = tmp_path / "lake.log"
    write(
        fake_lake,
        f"""#!/usr/bin/env python3
import sys
from pathlib import Path

path = Path({str(lake_log)!r})
path.parent.mkdir(parents=True, exist_ok=True)
with path.open("a", encoding="utf-8") as handle:
    handle.write(" ".join(sys.argv[1:]) + "\\n")
raise SystemExit(0)
""",
    )
    fake_lake.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"

    result = subprocess.run(
        [
            "python3",
            str(PREWARM_PROJECT),
            str(project),
            "--skip-cache",
            "--verify-file",
            "Foo.lean",
            "--verify-file",
            "Bar.lean",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert lake_log.read_text(encoding="utf-8").splitlines() == [
        "env lean Foo.lean",
        "env lean Bar.lean",
    ]
