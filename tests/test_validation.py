from __future__ import annotations

import json
import textwrap
from pathlib import Path

from archonlib.validation import write_validation_artifacts


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")


def test_write_validation_artifacts_preserves_prior_accepted_proof_on_no_progress(tmp_path: Path):
    workspace = tmp_path / "workspace"
    write(workspace / "FATEM" / "1.lean", "theorem foo : True := by\n  trivial\n")
    write(workspace / "FATEM" / "2.lean", "theorem bar : True := by\n  sorry\n")
    validation_root = workspace / ".archon" / "validation"
    validation_root.mkdir(parents=True, exist_ok=True)
    (validation_root / "FATEM_1.lean.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "relPath": "FATEM/1.lean",
                "status": "clean",
                "acceptanceStatus": "accepted",
                "validationStatus": "passed",
                "statementFidelity": "preserved",
                "iteration": "iter-001",
                "overallStatus": "clean",
                "loopExitCode": 0,
                "recoveryEvent": None,
                "headerDrifts": [],
                "blockerNotes": [],
                "taskResultKinds": {},
                "checks": {
                    "headerDrift": "none",
                    "workspaceChanged": True,
                    "taskResult": {
                        "present": False,
                        "durable": False,
                        "kind": None,
                        "path": None,
                    },
                    "proverError": False,
                },
                "sources": [
                    ".archon/RUN_SCOPE.md",
                    ".archon/task_results/",
                    ".archon/logs/",
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    written = write_validation_artifacts(
        workspace,
        status="no_progress",
        allowed_files=["FATEM/1.lean", "FATEM/2.lean"],
        changed_files=["FATEM/1.lean"],
        drifts=[],
        prover_failures=[],
        iteration="iter-002",
        loop_exit_code=0,
    )

    assert written == ["FATEM_1.lean.json", "FATEM_2.lean.json"]

    preserved = json.loads((validation_root / "FATEM_1.lean.json").read_text(encoding="utf-8"))
    assert preserved["acceptanceStatus"] == "accepted"
    assert preserved["validationStatus"] == "passed"
    assert preserved["checks"]["workspaceChanged"] is True
    assert preserved["status"] == "no_progress"
    assert preserved["overallStatus"] == "no_progress"

    untouched = json.loads((validation_root / "FATEM_2.lean.json").read_text(encoding="utf-8"))
    assert untouched["acceptanceStatus"] == "none"
    assert untouched["validationStatus"] == "no_progress"
