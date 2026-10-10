"""Solver disposition ledger (implementation plan Task 3).

Revision replies must disposition every dictated batch finding (absorbed or
rejected); rejected findings enter a REJECTED_PENDING lifecycle the Critic
settles in later rounds (confirm, auto-close silent P2/P3, revive on re-raise).
"""

from __future__ import annotations

import unittest

from orchestrator.domain.findings import Finding
from orchestrator.domain.policies.solver_plan import (
    is_retryable_solver_reply_error,
)
from orchestrator.domain.policies.zhongshu import (
    apply_dispositions,
    settle_rejected_findings,
)
from orchestrator.domain.zhongshu.solver import ReviseSolverLogic, SolverBatch


def _finding(
    finding_id: str,
    severity: str = "P2",
    status: str = "OPEN",
    canonical_key: str = "",
) -> Finding:
    return Finding(
        finding_id=finding_id,
        severity=severity,
        status=status,
        group_id="group-000001",
        item_id="item-000001",
        canonical_key=canonical_key,
        claim=f"claim {finding_id}",
    )


def _review(findings: tuple[Finding, ...]) -> object:
    from types import SimpleNamespace

    return SimpleNamespace(
        plan={"items": [], "groups": []},
        findings=findings,
    )


def _payload(
    selected: tuple[str, ...],
    resolutions: list[dict[str, object]] | None,
) -> dict[str, object]:
    return {
        "finding_batch": {
            "selected_finding_ids": list(selected),
            "remaining_finding_ids": [],
        },
        "finding_resolutions": list(resolutions or []),
    }


class ResolutionCoverageTests(unittest.TestCase):
    def test_revision_without_dispositions_rejected(self):
        batch = SolverBatch(("F1", "F2"), ())
        error = ReviseSolverLogic(batch).validate(
            _payload(("F1", "F2"), []), _review((_finding("F1"), _finding("F2")))
        )
        self.assertEqual(
            error, "SOLVER_RESOLUTION_COVERAGE_INCOMPLETE:missing=['F1', 'F2']"
        )
        self.assertTrue(is_retryable_solver_reply_error(error))

    def test_partial_dispositions_rejected_with_missing_ids(self):
        batch = SolverBatch(("F1", "F2"), ())
        error = ReviseSolverLogic(batch).validate(
            _payload(("F1", "F2"), [{"finding_id": "F1", "response": "absorbed"}]),
            _review((_finding("F1"), _finding("F2"))),
        )
        self.assertEqual(
            error, "SOLVER_RESOLUTION_COVERAGE_INCOMPLETE:missing=['F2']"
        )
        self.assertTrue(is_retryable_solver_reply_error(error))

    def test_disposition_response_unknown_value_rejected(self):
        batch = SolverBatch(("F1", "F2"), ())
        error = ReviseSolverLogic(batch).validate(
            _payload(
                ("F1", "F2"),
                [
                    {"finding_id": "F1", "response": "ignored"},
                    {"finding_id": "F2", "response": "ignored"},
                ],
            ),
            _review((_finding("F1"), _finding("F2"))),
        )
        self.assertTrue(error.startswith("SOLVER_RESOLUTION_COVERAGE_INCOMPLETE:"))
        self.assertIn("F1", error)
        self.assertIn("F2", error)
        self.assertTrue(is_retryable_solver_reply_error(error))

    def test_full_coverage_passes(self):
        batch = SolverBatch(("F1", "F2"), ())
        error = ReviseSolverLogic(batch).validate(
            _payload(
                ("F1", "F2"),
                [
                    {"finding_id": "F1", "response": "absorbed"},
                    {"finding_id": "F2", "response": "rejected"},
                ],
            ),
            _review((_finding("F1"), _finding("F2"))),
        )
        self.assertEqual(error, "")


class DispositionFoldTests(unittest.TestCase):
    def test_absorbed_finding_closes_with_resolution(self):
        finding = _finding("F1")
        settled = apply_dispositions(
            (finding,),
            [{"finding_id": "F1", "response": "absorbed", "note": "merged into item"}],
            round=2,
        )
        self.assertEqual(settled[0].status, "CLOSED")
        self.assertEqual(settled[0].disposition, "absorbed")
        self.assertIn("absorbed", settled[0].resolution or "")
        self.assertIn("merged into item", settled[0].resolution or "")

    def test_rejected_p0_goes_rejected_pending_not_closed(self):
        finding = _finding("F1", severity="P0")
        settled = apply_dispositions(
            (finding,),
            [{"finding_id": "F1", "response": "rejected", "note": "not a defect"}],
            round=1,
        )
        self.assertEqual(settled[0].status, "REJECTED_PENDING")
        self.assertEqual(settled[0].disposition, "rejected")
        self.assertTrue(settled[0].active is False)

    def test_disposition_survives_dict_round_trip(self):
        finding = _finding("F1")
        settled = apply_dispositions(
            (finding,), [{"finding_id": "F1", "response": "absorbed"}], round=1
        )
        restored = Finding.from_dict(settled[0].to_dict())
        self.assertEqual(restored, settled[0])


class SettleRejectedTests(unittest.TestCase):
    def test_rejected_p2_auto_closes_after_silent_round(self):
        finding = _finding("F1", severity="P2", status="REJECTED_PENDING")
        settled = settle_rejected_findings((finding,), (), reraised_keys=())
        self.assertEqual(settled[0].status, "CLOSED")
        self.assertEqual(settled[0].disposition, "rejected-auto")
        self.assertIn("auto", (settled[0].resolution or "").lower())

    def test_rejected_p1_confirmed_by_critic_closes(self):
        finding = _finding("F1", severity="P1", status="REJECTED_PENDING")
        settled = settle_rejected_findings(
            (finding,),
            [{"finding_id": "F1", "response": "REJECTED", "note": "stays rejected"}],
            reraised_keys=(),
        )
        self.assertEqual(settled[0].status, "CLOSED")
        self.assertIn("critic", (settled[0].resolution or "").lower())

    def test_rejected_p1_without_confirmation_stays_pending(self):
        finding = _finding("F1", severity="P1", status="REJECTED_PENDING")
        settled = settle_rejected_findings((finding,), (), reraised_keys=())
        self.assertEqual(settled[0].status, "REJECTED_PENDING")

    def test_reraised_canonical_key_revives_open(self):
        finding = _finding(
            "F1",
            severity="P2",
            status="REJECTED_PENDING",
            canonical_key="hash-of-claim",
        )
        settled = settle_rejected_findings(
            (finding,), (), reraised_keys=("hash-of-claim",)
        )
        self.assertEqual(settled[0].status, "OPEN")
        self.assertEqual(settled[0].disposition, "")


if __name__ == "__main__":
    unittest.main()
