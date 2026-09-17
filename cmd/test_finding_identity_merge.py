from __future__ import annotations

import unittest

from orchestrator.domain.context import (
    ProgressState,
    ReviewState,
    TaskIdentity,
    WorkflowContext,
)
from orchestrator.domain.findings import Finding
from orchestrator.domain.policies.zhongshu import (
    age_unresolved_findings,
    merge_findings,
    select_solver_batch,
)
from orchestrator.domain.states import _ConcreteWorkflowState


_SHORT_ID = "finding-2bf907421ff768fa8d9e"
_FULL_ID = (
    "finding-2bf907421ff768fa8d9e4d066dec21c7af06b6b829a66471e922174dd36e03f4"
)


def _finding(
    finding_id: str,
    *,
    severity: str = "P1",
    status: str = "OPEN",
    stuck_rounds: int = 0,
    canonical_key: str = "",
    item_id: str = "item-000001",
    group_id: str = "group-000001",
) -> Finding:
    return Finding(
        finding_id=finding_id,
        severity=severity,
        status=status,
        group_id=group_id,
        item_id=item_id,
        claim="blocking claim",
        required_action="fix it",
        stuck_rounds=stuck_rounds,
        canonical_key=canonical_key,
    )


class MergeFindingsIdentityTests(unittest.TestCase):
    """One opinion must occupy one ledger entry regardless of its id form."""

    def test_same_canonical_key_under_different_ids_collapses(self) -> None:
        stored = _finding(_FULL_ID, canonical_key="abc123", stuck_rounds=2)
        re_raised = _finding(_SHORT_ID, canonical_key="abc123", status="DEFERRED")

        merged = merge_findings((stored,), (re_raised,))

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].finding_id, _SHORT_ID)
        self.assertEqual(merged[0].status, "DEFERRED")

    def test_re_raise_under_twin_id_does_not_duplicate_the_entry(self) -> None:
        stored = _finding(_SHORT_ID, canonical_key="abc123")
        twin = _finding(_FULL_ID, canonical_key="abc123")

        merged = merge_findings((stored, twin), ())

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].finding_id, _FULL_ID)

    def test_findings_without_canonical_key_keep_structural_identity(self) -> None:
        first = _finding("f-1", canonical_key="", item_id="item-000001")
        second = _finding("f-1", canonical_key="", item_id="item-000002")

        merged = merge_findings((first,), (second,))

        self.assertEqual(len(merged), 2)


class FairStuckCountingTests(unittest.TestCase):
    """stuck_rounds only grows for findings the Solver was actually asked to fix."""

    def test_unattempted_finding_is_frozen_not_incremented(self) -> None:
        prior = (_finding("f-1", stuck_rounds=2),)
        echo = (_finding("f-1"),)  # the re-raised echo carries stuck_rounds=0

        aged = age_unresolved_findings(
            prior, echo, echo, attempted_finding_ids=("f-other",)
        )

        self.assertEqual(aged[0].stuck_rounds, 2)

    def test_attempted_finding_ages(self) -> None:
        prior = (_finding("f-1", stuck_rounds=2),)
        echo = (_finding("f-1"),)

        aged = age_unresolved_findings(
            prior, echo, echo, attempted_finding_ids=("f-1",)
        )

        self.assertEqual(aged[0].stuck_rounds, 3)

    def test_abbreviated_batch_id_still_matches_the_stored_finding(self) -> None:
        prior = (_finding(_FULL_ID, stuck_rounds=1),)
        echo = (_finding(_FULL_ID),)

        aged = age_unresolved_findings(
            prior, echo, echo, attempted_finding_ids=(_SHORT_ID,)
        )

        self.assertEqual(aged[0].stuck_rounds, 2)

    def test_none_attempted_keeps_the_legacy_count_every_round(self) -> None:
        prior = (_finding("f-1", stuck_rounds=1), _finding("f-2", stuck_rounds=1))
        echo = (_finding("f-1"), _finding("f-2"))

        aged = age_unresolved_findings(prior, echo, echo, attempted_finding_ids=None)

        self.assertEqual([finding.stuck_rounds for finding in aged], [2, 2])

    def test_counting_survives_an_id_form_change_via_canonical_identity(self) -> None:
        prior = (_finding(_FULL_ID, canonical_key="abc123", stuck_rounds=2),)
        re_raised = (_finding(_SHORT_ID, canonical_key="abc123"),)

        aged = age_unresolved_findings(
            prior, re_raised, re_raised, attempted_finding_ids=(_FULL_ID,)
        )

        self.assertEqual(aged[0].stuck_rounds, 3)
        self.assertEqual(aged[0].finding_id, _SHORT_ID)


class BatchStarvationTests(unittest.TestCase):
    """A repeatedly-named finding must not starve behind newer opinions."""

    def test_stuck_findings_jump_the_queue_within_severity(self) -> None:
        findings = (
            _finding("finding-01", item_id="item-000001", stuck_rounds=0),
            _finding("finding-02", item_id="item-000002", stuck_rounds=0),
            _finding("finding-03", item_id="item-000003", stuck_rounds=2),
        )

        selected, _ = select_solver_batch(findings, 2)

        self.assertEqual(selected[0], "finding-03")
        self.assertIn("finding-01", selected)


class AttemptedFindingIdsWiringTests(unittest.TestCase):
    """The review update records what the round actually attempted."""

    def _context(self, findings: tuple[Finding, ...] = ()) -> WorkflowContext:
        return WorkflowContext(
            identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
            progression=ProgressState("ZHONGSHU_SOLVER", 8, "2026-09-17T00:00:00Z"),
            review=ReviewState(revision_id="task-1:2", findings=findings),
        )

    def test_batch_echo_names_the_attempted_findings(self) -> None:
        update = _ConcreteWorkflowState._review_update(
            self._context(),
            {
                "plan": {"items": [], "groups": []},
                "finding_batch": {
                    "selected_finding_ids": ["finding-000008", "finding-000006"],
                    "remaining_finding_ids": ["finding-000007"],
                },
            },
        )

        self.assertEqual(
            update.attempted_finding_ids, ("finding-000008", "finding-000006")
        )

    def test_full_re_plan_attempts_every_active_finding(self) -> None:
        findings = (_finding("f-1"), _finding("f-2", status="RESOLVED"))

        update = _ConcreteWorkflowState._review_update(
            self._context(findings),
            {"plan": {"items": [], "groups": []}},
        )

        self.assertEqual(update.attempted_finding_ids, ("f-1",))

    def test_critic_payload_keeps_the_previous_attempt(self) -> None:
        update = _ConcreteWorkflowState._review_update(
            self._context((_finding("f-1"),)),
            {
                "task_reviews": [
                    {"item_id": "item-000001", "action": "TASK_CHANGES_REQUIRED"}
                ],
                "findings": [_finding("f-1").to_dict()],
            },
        )

        self.assertIsNone(update.attempted_finding_ids)


if __name__ == "__main__":
    unittest.main()
