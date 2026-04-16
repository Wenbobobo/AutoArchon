from __future__ import annotations

import json
import textwrap
from pathlib import Path

from archonlib.helper_analysis import (
    build_campaign_helper_analysis,
    build_helper_analysis,
    render_helper_analysis_markdown,
    write_helper_analysis_artifacts,
)


ROOT = Path(__file__).resolve().parents[1]


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")


def test_build_campaign_helper_analysis_summarizes_reason_families_and_clusters(tmp_path: Path):
    campaign_root = tmp_path / "campaign"
    write(
        campaign_root / "campaign-status.json",
        json.dumps(
            {
                "campaignId": "sample-campaign",
                "runs": [
                    {
                        "runId": "teacher-1",
                        "status": "accepted",
                        "scopeHint": "FATEM/1.lean",
                        "acceptedProofs": ["FATEM/1.lean"],
                    }
                ],
            },
            sort_keys=True,
        ),
    )
    write(
        campaign_root / "reports" / "final" / "final-summary.json",
        json.dumps(
            {
                "campaignId": "sample-campaign",
                "runs": [
                    {
                        "runId": "teacher-1",
                        "status": "accepted",
                        "acceptedProofs": ["FATEM/1.lean"],
                        "acceptedBlockers": [],
                    }
                ],
            },
            sort_keys=True,
        ),
    )
    write(
        campaign_root / "reports" / "final" / "supervisor" / "teacher-1" / "progress-summary.json",
        json.dumps(
            {
                "status": "clean",
                "helper": {
                    "enabled": True,
                    "noteCount": 2,
                    "countsByReason": {"lsp_timeout": 2},
                    "countsByPhase": {"prover": 2},
                    "countsByPromptPack": {"lsp_timeout": 2},
                },
            },
            sort_keys=True,
        ),
    )
    write(
        campaign_root / "runs" / "teacher-1" / "workspace" / ".archon" / "informal" / "helper" / "helper-index.json",
        json.dumps(
            {
                "schemaVersion": 1,
                "entries": [
                    {
                        "createdAt": "2026-04-16T00:00:00+00:00",
                        "event": "provider_call_failed",
                        "phase": "prover",
                        "relPath": "FATEM/1.lean",
                        "reason": "lsp_timeout",
                        "provider": "openai",
                        "model": "gpt-5.4",
                        "configSignature": "sig-a",
                    },
                    {
                        "createdAt": "2026-04-16T00:01:00+00:00",
                        "event": "provider_call",
                        "phase": "prover",
                        "relPath": "FATEM/1.lean",
                        "reason": "lsp_timeout",
                        "provider": "openai",
                        "model": "gpt-5.4",
                        "configSignature": "sig-a",
                    },
                    {
                        "createdAt": "2026-04-16T00:02:00+00:00",
                        "event": "note_reuse",
                        "phase": "prover",
                        "relPath": "FATEM/1.lean",
                        "reason": "lsp_timeout",
                        "provider": "openai",
                        "model": "gpt-5.4",
                        "configSignature": "sig-a",
                    },
                    {
                        "createdAt": "2026-04-16T00:03:00+00:00",
                        "event": "skipped_by_budget",
                        "phase": "prover",
                        "relPath": "FATEM/1.lean",
                        "reason": "lsp_timeout",
                        "provider": "openai",
                        "model": "gpt-5.4",
                        "configSignature": "sig-a",
                    },
                    {
                        "createdAt": "2026-04-16T00:04:00+00:00",
                        "event": "skipped_by_cooldown",
                        "phase": "prover",
                        "relPath": "FATEM/1.lean",
                        "reason": "lsp_timeout",
                        "provider": "openai",
                        "model": "gpt-5.4",
                        "configSignature": "sig-a",
                    },
                ],
            },
            sort_keys=True,
        ),
    )
    write(
        campaign_root / "reports" / "final" / "lessons" / "lesson-records.jsonl",
        """
        {"accepted_state":"accepted","category":"provider_transport","run_id":"teacher-1","theorem_id":"FATEM/1.lean","summary":"Provider transport failed once.","action_taken":"retry_with_backoff"}
        {"accepted_state":"accepted","category":"scope_control","run_id":"teacher-1","theorem_id":"FATEM/1.lean","summary":"Keep scope narrow.","action_taken":"tighten_scope"}
        """,
    )

    payload = build_campaign_helper_analysis(campaign_root, top_n=10)

    assert payload["campaignId"] == "sample-campaign"
    assert payload["helperEnabledRunCount"] == 1
    assert payload["helperActiveRunCount"] == 1
    assert payload["helperTotals"]["noteCount"] == 2
    assert payload["helperTotals"]["freshCallCount"] == 1
    assert payload["helperTotals"]["failedCallCount"] == 1
    assert payload["helperTotals"]["noteReuseCount"] == 1
    assert payload["helperTotals"]["blockedByBudgetCount"] == 1
    assert payload["helperTotals"]["blockedByCooldownCount"] == 1
    assert payload["lessonCategoryCounts"] == {"provider_transport": 1, "scope_control": 1}
    assert payload["helperProviderModelCounts"] == {"openai:gpt-5.4": 5}

    reason_row = payload["reasonStats"][0]
    assert reason_row["reason"] == "lsp_timeout"
    assert reason_row["noteMentions"] == 2
    assert reason_row["freshCalls"] == 1
    assert reason_row["failedCalls"] == 1
    assert reason_row["noteReuses"] == 1
    assert reason_row["blockedByBudget"] == 1
    assert reason_row["blockedByCooldown"] == 1
    assert reason_row["terminalCategoryCounts"] == {"accepted_proof": 1}

    cluster_row = payload["repeatedAttemptClusters"][0]
    assert cluster_row["runId"] == "teacher-1"
    assert cluster_row["reason"] == "lsp_timeout"
    assert cluster_row["eventCounts"] == {
        "note_reuse": 1,
        "provider_call": 1,
        "provider_call_failed": 1,
        "skipped_by_budget": 1,
        "skipped_by_cooldown": 1,
    }


def test_helper_analysis_writes_default_artifacts_and_markdown(tmp_path: Path):
    campaign_root = tmp_path / "campaign"
    write(
        campaign_root / "campaign-status.json",
        json.dumps({"campaignId": "sample-campaign", "runs": [{"runId": "teacher-1", "status": "blocked", "acceptedBlockers": ["FATEM/2.lean"]}]}, sort_keys=True),
    )
    write(
        campaign_root / "reports" / "final" / "final-summary.json",
        json.dumps({"campaignId": "sample-campaign", "runs": [{"runId": "teacher-1", "status": "blocked", "acceptedBlockers": ["FATEM/2.lean"]}]}, sort_keys=True),
    )
    payload = build_helper_analysis([campaign_root], top_n=5)
    rendered = render_helper_analysis_markdown(payload)

    assert "# Helper Analysis" in rendered
    assert "`sample-campaign`" in rendered

    output_root = campaign_root / "reports" / "final" / "helper-analysis"
    artifacts = write_helper_analysis_artifacts(output_root, payload["campaigns"][0])

    assert Path(artifacts["json"]).exists()
    assert Path(artifacts["markdown"]).exists()
    written_payload = json.loads(Path(artifacts["json"]).read_text(encoding="utf-8"))
    assert written_payload["campaigns"][0]["campaignId"] == "sample-campaign"
    assert "`sample-campaign`" in Path(artifacts["markdown"]).read_text(encoding="utf-8")
