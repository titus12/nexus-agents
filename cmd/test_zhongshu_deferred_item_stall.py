from __future__ import annotations

import unittest

from orchestrator.domain.context import (
    ProgressState,
    ReviewState,
    ReviewTaskRecord,
    TaskIdentity,
    WorkflowContext,
)
from orchestrator.domain.context import context_from_dto, context_to_dto
from orchestrator.domain.events import DomainEvent
from orchestrator.domain.findings import Finding
from orchestrator.domain.states import (
    ZhongshuCriticState,
    _attempted_item_ids,
    _task_review_ledger_update,
)
from orchestrator.runtime.repository import WorkflowSnapshot
from orchestrator.runtime.reducer import LinearContextReducer


def _finding(finding_id: str, item_id: str) -> Finding:
    return Finding(
        finding_id=finding_id,
        severity="P1",
        status="OPEN",
        group_id="group-000001",
        item_id=item_id,
        claim="blocking claim",
        required_action="fix it",
    )


class AttemptedItemIdsTests(unittest.TestCase):
    """Only the Solver's own bounded batch counts as an attempt."""

    def _findings(self) -> tuple[Finding, ...]:
        return (
            _finding("f-1", "item-000001"),
            _finding("f-2", "item-000002"),
            _finding("f-3", "item-000007"),
        )

    def test_selected_findings_map_to_their_items(self) -> None:
        payload = {"finding_batch": {"selected_finding_ids": ["f-2", "f-1"]}}

        attempted = _attempted_item_ids(self._findings(), payload)

        self.assertEqual(attempted, ("item-000001", "item-000002"))

    def test_deferred_findings_are_not_attempted(self) -> None:
        payload = {"finding_batch": {"selected_finding_ids": ["f-1"]}}

        attempted = _attempted_item_ids(self._findings(), payload)

        self.assertEqual(attempted, ("item-000001",))
        self.assertNotIn("item-000007", attempted)

    def test_no_batch_leaves_the_attempt_unknown(self) -> None:
        self.assertIsNone(_attempted_item_ids(self._findings(), {}))

    def test_empty_selection_attempts_nothing(self) -> None:
        payload = {"finding_batch": {"selected_finding_ids": []}}

        self.assertEqual(_attempted_item_ids(self._findings(), payload), ())


class DeferredItemDoesNotStallTests(unittest.TestCase):
    """A task the Solver never got to fix must not burn its stall budget.

    Regression: the counter advanced on every ``TASK_CHANGES_REQUIRED``, so
    items whose findings sat in ``finding_batch.remaining_finding_ids`` were
    escalated as stalled without a single repair attempt.
    """

    def _context(
        self,
        *records: ReviewTaskRecord,
        attempted: tuple[str, ...] = (),
    ) -> WorkflowContext:
        return WorkflowContext(
            identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
            progression=ProgressState("ZHONGSHU_CRITIC", 6, "2026-09-16T00:00:00Z"),
            review=ReviewState(
                revision_id="rev-1",
                task_review_ledger=records,
                attempted_item_ids=attempted,
            ),
        )

    def _review(self, item_id: str, action: str = "TASK_CHANGES_REQUIRED") -> dict:
        return {
            "review_job_id": f"zhongshu:rev-1:group-000001:{item_id}",
            "group_id": "group-000001",
            "item_id": item_id,
            "action": action,
            "reviewed_task_hash": "th",
            "reviewed_dependency_hash": "dh",
        }

    def _rounds(self, context: WorkflowContext, *reviews: dict) -> dict[str, int]:
        update = _task_review_ledger_update(
            context, {"task_reviews": list(reviews)}
        )
        records = update if update is not None else context.review.task_review_ledger
        return {record.item_id: record.changes_rounds for record in records}

    def test_deferred_task_keeps_its_counter_frozen(self) -> None:
        context = self._context(
            ReviewTaskRecord(
                item_id="item-000001",
                status="CHANGES_REQUIRED",
                task_hash="th",
                dependency_hash="dh",
                changes_rounds=2,
            ),
            ReviewTaskRecord(
                item_id="item-000007",
                status="CHANGES_REQUIRED",
                task_hash="th",
                dependency_hash="dh",
                changes_rounds=2,
            ),
            attempted=("item-000001",),
        )

        rounds = self._rounds(context, self._review("item-000007"))

        self.assertEqual(rounds["item-000007"], 2)

    def test_attempted_task_still_advances(self) -> None:
        context = self._context(
            ReviewTaskRecord(
                item_id="item-000001",
                status="CHANGES_REQUIRED",
                task_hash="th",
                dependency_hash="dh",
                changes_rounds=2,
            ),
            attempted=("item-000001",),
        )

        rounds = self._rounds(context, self._review("item-000001"))

        self.assertEqual(rounds["item-000001"], 3)

    def test_unknown_attempt_set_keeps_counting_every_rejection(self) -> None:
        context = self._context(
            ReviewTaskRecord(
                item_id="item-000001",
                status="CHANGES_REQUIRED",
                task_hash="th",
                dependency_hash="dh",
                changes_rounds=2,
            ),
        )

        rounds = self._rounds(context, self._review("item-000001"))

        self.assertEqual(rounds["item-000001"], 3)

    def test_first_rejection_of_a_deferred_task_stays_at_zero(self) -> None:
        context = self._context(attempted=("item-000001",))

        rounds = self._rounds(context, self._review("item-000007"))

        self.assertEqual(rounds["item-000007"], 0)

    def test_approval_still_resets_the_counter(self) -> None:
        context = self._context(
            ReviewTaskRecord(
                item_id="item-000001",
                status="CHANGES_REQUIRED",
                task_hash="th",
                dependency_hash="dh",
                changes_rounds=2,
            ),
            attempted=(),
        )

        rounds = self._rounds(
            context, self._review("item-000001", "TASK_APPROVED")
        )

        self.assertEqual(rounds["item-000001"], 0)


class DeferredItemDoesNotEscalateTests(unittest.TestCase):
    """End to end: review rounds alone cannot escalate an unattempted task."""

    def setUp(self) -> None:
        self.state = ZhongshuCriticState()
        self.reducer = LinearContextReducer()

    def _context(self, attempted: tuple[str, ...]) -> WorkflowContext:
        return WorkflowContext(
            identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
            progression=ProgressState("ZHONGSHU_CRITIC", 6, "2026-09-16T00:00:00Z"),
            review=ReviewState(
                revision_id="rev-1",
                plan={"items": [{"item_id": "item-000001"}]},
                task_items=(),
                findings=(_finding("f-1", "item-000001"),),
                task_review_ledger=(
                    ReviewTaskRecord(
                        item_id="item-000001",
                        status="CHANGES_REQUIRED",
                        task_hash="th",
                        dependency_hash="dh",
                        changes_rounds=2,
                    ),
                ),
                zhongshu_revision_round=1,
                max_zhongshu_revision_rounds=8,
                item_revision_round=0,
                max_item_revision_rounds=3,
                attempted_item_ids=attempted,
                plan_hash="plan-hash",
            ),
        )

    def _event(self) -> DomainEvent:
        return DomainEvent(
            "NODE_COMPLETED",
            "task-1",
            6,
            {
                "action": "REQUEST_SOLVER_REVISION",
                "task_reviews": [
                    {
                        "review_job_id": "zhongshu:rev-1:group-000001:item-000001",
                        "group_id": "group-000001",
                        "item_id": "item-000001",
                        "action": "TASK_CHANGES_REQUIRED",
                        "reviewed_task_hash": "th",
                        "reviewed_dependency_hash": "dh",
                    }
                ],
            },
            "2026-09-16T00:00:00Z",
        )

    def test_third_rejection_escalates_when_the_task_was_attempted(self) -> None:
        context = self._context(attempted=("item-000001",))
        decision = self.state.handle(context, self._event())

        self.assertEqual(decision.transition.action, "HUMAN_GATE")
        self.assertEqual(decision.transition.reason_code, "ZHONGSHU_ITEM_STALLED")

    def test_third_rejection_does_not_escalate_when_it_was_deferred(self) -> None:
        # The Solver's batch covered another task, so this one was never
        # repaired: its rejection must not count as a stall round.
        context = self._context(attempted=("item-000002",))
        decision = self.state.handle(context, self._event())

        self.assertEqual(decision.transition.action, "REQUEST_SOLVER_REVISION")
        after = self.reducer.apply(
            WorkflowSnapshot("task-1", context, 0), decision
        )
        record = after.context.review.task_review_ledger[0]
        self.assertEqual(record.changes_rounds, 2)


class AttemptedItemsPersistenceTests(unittest.TestCase):
    def test_attempted_items_survive_a_dto_round_trip(self) -> None:
        context = WorkflowContext(
            identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
            progression=ProgressState("ZHONGSHU_CRITIC", 6, "2026-09-16T00:00:00Z"),
            review=ReviewState(
                revision_id="rev-1",
                attempted_item_ids=("item-000001", "item-000002"),
            ),
        )

        restored = context_from_dto(context_to_dto(context))

        self.assertEqual(
            restored.review.attempted_item_ids, ("item-000001", "item-000002")
        )

    def test_a_legacy_snapshot_without_the_field_still_loads(self) -> None:
        context = WorkflowContext(
            identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
            progression=ProgressState("ZHONGSHU_CRITIC", 6, "2026-09-16T00:00:00Z"),
            review=ReviewState(revision_id="rev-1"),
        )
        payload = context_to_dto(context)
        payload["review"].pop("attempted_item_ids")

        self.assertEqual(
            context_from_dto(payload).review.attempted_item_ids, ()
        )


if __name__ == "__main__":
    unittest.main()
