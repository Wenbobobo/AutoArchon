from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "materialize_problem_pack.py"


def test_materialize_problem_pack_writes_lean_source_root_from_json(tmp_path: Path):
    input_json = tmp_path / "problems.json"
    input_json.write_text(
        json.dumps(
            [
                {
                    "id": 2,
                    "informal_statement": "Prove theorem two.",
                    "formal_statement": "import Mathlib\n\ntheorem two : True := by\n  sorry\n",
                    "source": "FATE-X",
                    "tag": ["Sample", "Algebra"],
                    "version": "v4.28.0",
                },
                {
                    "id": 1,
                    "informal_statement": "Prove theorem one.",
                    "formal_statement": "import Mathlib\n\ntheorem one : True := by\n  sorry\n",
                    "source": "FATE-X",
                    "tag": ["Sample"],
                    "version": "v4.28.0",
                },
            ],
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    output_root = tmp_path / "generated-pack"

    result = subprocess.run(
        [
            "python3",
            str(SCRIPT),
            "--input-json",
            str(input_json),
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
    assert payload["packageName"] == "FATE-X"
    assert payload["libraryName"] == "FATEX"
    assert payload["problemIds"] == ["1", "2"]
    assert (output_root / "lakefile.lean").exists()
    assert (output_root / "lean-toolchain").read_text(encoding="utf-8").strip() == "leanprover/lean4:v4.28.0"
    assert (output_root / "FATEX" / "1.lean").exists()
    assert (output_root / "FATEX" / "2.lean").exists()
    assert "require mathlib from git" in (output_root / "lakefile.lean").read_text(encoding="utf-8")
    questions = (output_root / "QUESTIONS.md").read_text(encoding="utf-8")
    assert "| `1` | `FATEX/1.lean` | Prove theorem one. | Sample |" in questions
    manifest = json.loads((output_root / "problem-pack.json").read_text(encoding="utf-8"))
    assert len(manifest) == 2
    assert manifest[0]["id"] == 1


def test_materialize_problem_pack_defaults_to_first_n_records(tmp_path: Path):
    input_json = tmp_path / "problems.json"
    input_json.write_text(
        json.dumps(
            [
                {
                    "id": index,
                    "informal_statement": f"Problem {index}.",
                    "formal_statement": f"import Mathlib\n\ntheorem t{index} : True := by\n  sorry\n",
                    "source": "FATE-X",
                    "version": "v4.28.0",
                }
                for index in range(1, 5)
            ],
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    output_root = tmp_path / "generated-pack"

    result = subprocess.run(
        [
            "python3",
            str(SCRIPT),
            "--input-json",
            str(input_json),
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
    assert (output_root / "FATEX" / "3.lean").exists() is False
