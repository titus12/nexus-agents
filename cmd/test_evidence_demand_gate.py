from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from orchestrator.adapters import _evidence_demand_rejection
from orchestrator.transport.external import AgentRequest


def _request(context: dict) -> AgentRequest:
    return AgentRequest(
        task_id="task-1",
        request_id="req-1",
        agent_id="agent-1",
        role="review-analyst",
        phase="ZHONGSHU",
        prompt="",
        idempotency_key="req-1",
        context=context,
    )


def _demand(**finding_overrides) -> dict:
    finding = {
        "finding_id": "finding-1",
        "severity": "P0",
        "item_id": "item-0101",
        "claim": "signal unverifiable",
        "required_action": "cite the compute site",
        "evidence_targets": [],
    }
    finding.update(finding_overrides)
    targets = list(finding.get("evidence_targets") or [])
    return {"active_findings": [finding], "evidence_targets": targets}


class EvidenceDemandGateTests(unittest.TestCase):
    """The transport gate checks evidence coverage, never excuse wording."""

    def test_no_demand_passes(self) -> None:
        self.assertEqual(_evidence_demand_rejection(_request({}), {}), "")
        self.assertEqual(_evidence_demand_rejection(_request({"evidence_demand": {}}), {}), "")

    def test_missing_file_is_a_legitimate_unknown(self) -> None:
        demand = _demand(evidence_targets=[{"path": "no/such/file.py", "symbol": ""}])
        request = _request({"evidence_demand": demand})
        payload = {
            "finding_responses": [
                {
                    "finding_id": "finding-1",
                    "answer": "target file does not exist in the workspace",
                    "evidence_ids": [],
                    "suggested_disposition": "NEEDS_RUNTIME_DATA",
                }
            ]
        }
        self.assertEqual(_evidence_demand_rejection(request, payload), "")

    def test_existing_uncited_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "evidence_site.py"
            target.write_text("X = 1\n", encoding="utf-8")
            demand = _demand(
                evidence_targets=[{"path": str(target), "symbol": "compute_signal"}]
            )
            request = _request({"evidence_demand": demand})
            rejection = _evidence_demand_rejection(request, {"findings": []})
            self.assertIn("evidence targets unmet", rejection)
            self.assertIn("exists at", rejection)
            self.assertIn("file:line", rejection)
            self.assertIn("read-only", rejection)

    def test_citing_the_path_or_symbol_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "evidence_site.py"
            target.write_text("X = 1\n", encoding="utf-8")
            demand = _demand(
                evidence_targets=[{"path": str(target), "symbol": "compute_signal"}]
            )
            request = _request({"evidence_demand": demand})
            answer = {
                "finding_id": "finding-1",
                "answer": "computed here",
                "evidence_ids": ["ev-1"],
                "suggested_disposition": "CLOSE",
            }
            by_path = _evidence_demand_rejection(
                request,
                {
                    "note": f"see {target.as_posix()}",
                    "finding_responses": [answer],
                },
            )
            self.assertEqual(by_path, "")
            by_symbol = _evidence_demand_rejection(
                request,
                {
                    "note": "compute_signal is in module init",
                    "finding_responses": [answer],
                },
            )
            self.assertEqual(by_symbol, "")

    def test_backslash_citation_counts_as_covered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "evidence_site.py"
            target.write_text("X = 1\n", encoding="utf-8")
            demand = _demand(
                evidence_targets=[{"path": str(target), "symbol": ""}]
            )
            request = _request({"evidence_demand": demand})
            windows_citation = _evidence_demand_rejection(
                request,
                {
                    "note": f"verified at {str(target).replace('/', chr(92))}:1",
                    "finding_responses": [
                        {
                            "finding_id": "finding-1",
                            "answer": "verified",
                            "evidence_ids": [],
                            "suggested_disposition": "CLOSE",
                        }
                    ],
                },
            )
            self.assertEqual(windows_citation, "")

    def test_demanded_finding_without_answer_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "evidence_site.py"
            target.write_text("X = 1\n", encoding="utf-8")
            demand = _demand(
                evidence_targets=[{"path": str(target), "symbol": "compute_signal"}]
            )
            request = _request({"evidence_demand": demand})
            cited_but_unanswered = _evidence_demand_rejection(
                request, {"note": f"verified at {target.as_posix()}:1"}
            )
            self.assertIn("no finding_responses answer", cited_but_unanswered)
            self.assertIn("finding-1", cited_but_unanswered)
            answered = _evidence_demand_rejection(
                request,
                {
                    "note": f"verified at {target.as_posix()}:1",
                    "finding_responses": [
                        {
                            "finding_id": "finding-1",
                            "answer": "computed at states.py:914",
                            "evidence_ids": ["ev-1"],
                            "suggested_disposition": "CLOSE",
                        }
                    ],
                },
            )
            self.assertEqual(answered, "")


if __name__ == "__main__":
    unittest.main()
