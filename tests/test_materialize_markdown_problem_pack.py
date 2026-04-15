from __future__ import annotations

import json
import subprocess
from pathlib import Path

from archonlib.project_state import detect_stage


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "materialize_markdown_problem_pack.py"


def test_materialize_markdown_problem_pack_writes_autoformalize_ready_lean_root(tmp_path: Path):
    questions_markdown = tmp_path / "Questions.md"
    questions_markdown.write_text(
        """
| 形式化目标 | 方向详细描述 | 真实的 Mathlib 模块 | 备注与实现思路 |
| :--- | :--- | :--- | :--- |
| **1. 核心集合的定义与计数**<br>(Definition 1) | 定义有限域上的多项式三元组集合并计算其基数，并保留公式 $|A_d| = q^d$。 | `Data.Polynomial.Degree`<br>`Data.Polynomial.Monic` | 先做有限集计数，并保留 $|\\mathcal{Q}_d| = q^{3d}$。 |
| **2. 多项式环上的 Möbius 函数**<br>(Lemma 4) | 在首一多项式上定义 Möbius 函数并求和。 | `RingTheory.UniqueFactorizationDomain` | 需要唯一分解基建。 |
""".strip()
        + "\n",
        encoding="utf-8",
    )
    output_root = tmp_path / "generated-pack"

    result = subprocess.run(
        [
            "python3",
            str(SCRIPT),
            "--questions-markdown",
            str(questions_markdown),
            "--output-root",
            str(output_root),
            "--problem-id",
            "1",
            "--problem-id",
            "2",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["problemIds"] == ["1", "2"]
    library_name = payload["libraryName"]
    assert (output_root / "lakefile.lean").exists()
    assert (output_root / "lean-toolchain").exists()
    lean_file = output_root / library_name / "1.lean"
    assert lean_file.exists()
    lean_text = lean_file.read_text(encoding="utf-8")
    assert "import Mathlib" in lean_text
    assert "This file intentionally contains no Lean declaration yet." in lean_text
    assert "核心集合的定义与计数" in lean_text
    assert "Data.Polynomial.Degree" in lean_text
    assert "$|A_d| = q^d$" in lean_text
    assert "$|\\mathcal{Q}_d| = q^{3d}$" in lean_text
    assert "theorem " not in lean_text
    assert detect_stage(output_root) == "autoformalize"
    manifest = json.loads((output_root / "problem-pack.json").read_text(encoding="utf-8"))
    assert manifest[0]["title"] == "核心集合的定义与计数"
    assert manifest[1]["mathlib_modules"] == ["RingTheory.UniqueFactorizationDomain"]


def test_materialize_markdown_problem_pack_defaults_to_first_n_rows(tmp_path: Path):
    questions_markdown = tmp_path / "Questions.md"
    questions_markdown.write_text(
        """
| Goal | Details | Modules | Notes |
| :--- | :--- | :--- | :--- |
| 1. One | First informal goal. | `Mathlib` | Start here. |
| 2. Two | Second informal goal. | `Mathlib` | Continue. |
| 3. Three | Third informal goal. | `Mathlib` | Later. |
""".strip()
        + "\n",
        encoding="utf-8",
    )
    output_root = tmp_path / "generated-pack"

    result = subprocess.run(
        [
            "python3",
            str(SCRIPT),
            "--questions-markdown",
            str(questions_markdown),
            "--output-root",
            str(output_root),
            "--max-problems",
            "2",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["problemIds"] == ["1", "2"]
    assert (output_root / payload["libraryName"] / "3.lean").exists() is False
