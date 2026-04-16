from __future__ import annotations

import json
import textwrap
import threading
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from archonlib.campaign import create_campaign
from scripts.campaign_observer import CampaignObserverServer, make_campaign_observer_handler


ROOT = Path(__file__).resolve().parents[1]


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")


def make_source_project(tmp_path: Path, *, file_count: int = 1) -> Path:
    source = tmp_path / "source-project"
    write(source / "lakefile.lean", "import Lake\n")
    write(source / "lean-toolchain", "leanprover/lean4:v4.28.0\n")
    for index in range(1, file_count + 1):
        write(source / "FATEM" / f"{index}.lean", f"theorem file_{index} : True := by\n  sorry\n")
    return source


def test_campaign_observer_serves_progress_dashboard_and_refreshes_surface(tmp_path: Path):
    source = make_source_project(tmp_path, file_count=1)
    campaign_root = tmp_path / "campaign"
    create_campaign(
        archon_root=ROOT,
        source_root=source,
        campaign_root=campaign_root,
        run_specs=[
            {"id": "teacher-001", "objective_regex": "^FATEM/1\\.lean$", "objective_limit": 1, "scope_hint": "FATEM/1.lean"},
        ],
    )

    run_root = campaign_root / "runs" / "teacher-001"
    now = datetime.now(timezone.utc).isoformat()
    write(
        run_root / "workspace" / ".archon" / "supervisor" / "run-lease.json",
        json.dumps({"active": True, "lastHeartbeatAt": now, "status": "running"}, sort_keys=True),
    )
    write(
        run_root / "workspace" / ".archon" / "supervisor" / "progress-summary.json",
        json.dumps(
            {
                "status": "running",
                "liveRuntime": {
                    "phase": "proving",
                    "iteration": "iter-002",
                    "planStatus": "done",
                    "proverStatus": "running",
                    "reviewStatus": None,
                    "activeProvers": [{"file": "FATEM/1.lean", "id": "FATEM_1", "status": "running"}],
                },
                "helper": {
                    "noteCount": 1,
                    "failedCallCount": 1,
                    "countsByReason": {"lsp_timeout": 1},
                    "countsByPhase": {"prover": 1},
                    "failedCallsByReason": {"provider_transport": 1},
                    "cooldownState": {
                        "activeReasons": [{"phase": "prover", "reason": "provider_transport", "relPath": "FATEM/1.lean"}]
                    },
                },
                "taskResultsSummary": {
                    "counts": {"resolved": 0, "blocker": 0, "other": 0},
                },
            },
            sort_keys=True,
        ),
    )

    progress_html = campaign_root / "control" / "progress-summary.html"
    assert not progress_html.exists()

    server = CampaignObserverServer(
        ("127.0.0.1", 0),
        make_campaign_observer_handler(campaign_root),
        campaign_root=campaign_root,
        heartbeat_seconds=60,
        refresh_seconds=0,
        refresh_status=True,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_address[1]}/", timeout=5) as response:
            body = response.read().decode("utf-8")

        assert response.status == 200
        assert "AutoArchon Campaign Progress" in body
        assert "teacher-001" in body
        assert "Operator Queue" in body
        assert "Likely Bottleneck" in body
        assert "helper_failed_calls=<code>1</code>" in body
        assert progress_html.exists()
        progress_payload = json.loads((campaign_root / "control" / "progress-summary.json").read_text(encoding="utf-8"))
        assert progress_payload["paths"]["progressSummaryHtmlPath"].endswith("control/progress-summary.html")
        assert progress_payload["runningRuns"][0]["runId"] == "teacher-001"
        assert progress_payload["runningRuns"][0]["helperFailedReasonCounts"] == {"provider_transport": 1}
        assert progress_payload["operatorQueue"][0]["runId"] == "teacher-001"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
