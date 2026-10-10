from __future__ import annotations

import unittest

from orchestrator.domain.context import (
    context_from_dto,
    context_to_dto,
    ItemWorkflow,
    ProgressState,
    ReviewState,
    TaskIdentity,
    WorkflowContext,
)
from orchestrator.domain.policies.item_workflows import update_item_workflows
from orchestrator.domain.states import _ConcreteWorkflowState


def _entry(item_id: str, action: str, finding_id: str = "") -> dict[str, object]:
    entry: dict[str, object] = {"item_id": item_id, "action": action}
    if finding_id:
        entry["finding_ids"] = [finding_id]
    return entry


class UpdateItemWorkflowsTests(unittest.TestCase):
    """The dispatch table mirrors the ledger's attempt-scoped counting rule."""

    def test_first_rejection_creates_a_revising_row(self) -> None:
        rows = update_item_workflows(
            (), [_entry("item-000001", "TASK_CHANGES_REQUIRED", "finding-1")],
            attempted_item_ids=None, max_rounds=5,
        )

        self.assertEqual(rows[0].phase, "REVISING")
        self.assertEqual(rows[0].rounds, 1)
        self.assertEqual(rows[0].last_verdict, "TASK_CHANGES_REQUIRED")
        self.assertEqual(rows[0].finding_ids, ("finding-1",))

    def test_approval_creates_an_approved_row_and_resets_rounds(self) -> None:
        prior = (ItemWorkflow(item_id="item-000001", phase="REVISING", rounds=3),)

        rows = update_item_workflows(
            prior, [_entry("item-000001", "TASK_APPROVED")],
            attempted_item_ids=None, max_rounds=5,
        )

        self.assertEqual(rows[0].phase, "APPROVED")
        self.assertEqual(rows[0].rounds, 0)

    def test_unattempted_item_does_not_consume_its_budget(self) -> None:
        prior = (ItemWorkflow(item_id="item-000001", phase="REVISING", rounds=2),)

        rows = update_item_workflows(
            prior, [_entry("item-000001", "TASK_CHANGES_REQUIRED")],
            attempted_item_ids=("item-000009",), max_rounds=5,
        )

        self.assertEqual(rows[0].rounds, 2)
        self.assertEqual(rows[0].phase, "REVISING")

    def test_rounds_at_the_budget_escalate_the_row(self) -> None:
        prior = (ItemWorkflow(item_id="item-000001", phase="REVISING", rounds=4),)

        rows = update_item_workflows(
            prior, [_entry("item-000001", "TASK_CHANGES_REQUIRED")],
            attempted_item_ids=None, max_rounds=5,
        )

        self.assertEqual(rows[0].phase, "ESCALATED")
        self.assertEqual(rows[0].rounds, 5)

    def test_zero_budget_never_escalates(self) -> None:
        rows = update_item_workflows(
            (), [_entry("item-000001", "TASK_CHANGES_REQUIRED")],
            attempted_item_ids=None, max_rounds=0,
        )

        self.assertEqual(rows[0].phase, "REVISING")

    def test_rows_without_verdicts_are_preserved(self) -> None:
        prior = (
            ItemWorkflow(item_id="item-000001", phase="APPROVED"),
            ItemWorkflow(item_id="item-000002", phase="REVISING", rounds=1),
        )

        rows = update_item_workflows(
            prior, [_entry("item-000002", "TASK_CHANGES_REQUIRED")],
            attempted_item_ids=None, max_rounds=5,
        )

        self.assertEqual(len(rows), 2)
        untouched = next(row for row in rows if row.item_id == "item-000001")
        self.assertEqual(untouched.phase, "APPROVED")

    def test_failed_wave_records_verdict_without_spending_budget(self) -> None:
        prior = (ItemWorkflow(item_id="item-000001", phase="REVISING", rounds=2),)

        rows = update_item_workflows(
            prior,
            [
                _entry("item-000001", "TASK_CHANGES_REQUIRED"),
                _entry("item-000002", "TASK_CHANGES_REQUIRED"),
            ],
            attempted_item_ids=None, max_rounds=5, failed_round=True,
        )

        by_id = {row.item_id: row for row in rows}
        self.assertEqual(by_id["item-000001"].rounds, 2)
        self.assertEqual(by_id["item-000001"].phase, "REVISING")
        self.assertEqual(by_id["item-000001"].last_verdict, "TASK_CHANGES_REQUIRED")
        self.assertEqual(by_id["item-000002"].rounds, 0)
        self.assertEqual(by_id["item-000002"].phase, "REVIEWING")

    def test_failed_wave_approval_still_ratchets(self) -> None:
        prior = (ItemWorkflow(item_id="item-000001", phase="REVISING", rounds=3),)

        rows = update_item_workflows(
            prior, [_entry("item-000001", "TASK_APPROVED")],
            attempted_item_ids=None, max_rounds=5, failed_round=True,
        )

        self.assertEqual(rows[0].phase, "APPROVED")
        self.assertEqual(rows[0].rounds, 0)

    def test_no_task_reviews_keeps_the_current_table(self) -> None:
        prior = (ItemWorkflow(item_id="item-000001"),)

        self.assertIsNone(
            update_item_workflows(prior, None, attempted_item_ids=None, max_rounds=5)
        )
        self.assertIsNone(
            update_item_workflows(prior, [], attempted_item_ids=None, max_rounds=5)
        )


class ItemWorkflowPersistenceTests(unittest.TestCase):
    def test_round_trip_preserves_the_table(self) -> None:
        context = WorkflowContext(
            identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
            progression=ProgressState("ZHONGSHU_CRITIC", 6, "2026-09-17T00:00:00Z"),
            review=ReviewState(
                revision_id="task-1:2",
                item_workflows=(
                    ItemWorkflow(
                        item_id="item-000001",
                        group_id="group-000001",
                        phase="REVISING",
                        rounds=2,
                        last_verdict="TASK_CHANGES_REQUIRED",
                        finding_ids=("finding-000001",),
                    ),
                ),
            ),
        )

        restored = context_from_dto(context_to_dto(context))

        self.assertEqual(restored.review.item_workflows, context.review.item_workflows)

    def test_legacy_snapshot_without_the_key_loads_empty(self) -> None:
        context = WorkflowContext(
            identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
            progression=ProgressState("ZHONGSHU_CRITIC", 6, "2026-09-17T00:00:00Z"),
            review=ReviewState(revision_id="task-1:2"),
        )
        dto = context_to_dto(context)
        del dto["review"]["item_workflows"]

        restored = context_from_dto(dto)

        self.assertEqual(restored.review.item_workflows, ())


class ReviewUpdateWiringTests(unittest.TestCase):
    """A critic round folds the table through the standard review update."""

    def test_critic_payload_updates_the_dispatch_table(self) -> None:
        context = WorkflowContext(
            identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
            progression=ProgressState("ZHONGSHU_CRITIC", 6, "2026-09-17T00:00:00Z"),
            review=ReviewState(revision_id="task-1:2"),
        )

        update = _ConcreteWorkflowState._review_update(
            context,
            {
                "task_reviews": [
                    _entry("item-000001", "TASK_CHANGES_REQUIRED", "finding-000001"),
                    _entry("item-000002", "TASK_APPROVED"),
                ],
                "findings": [],
            },
        )

        phases = {row.item_id: row.phase for row in update.item_workflows}
        self.assertEqual(phases["item-000001"], "REVISING")
        self.assertEqual(phases["item-000002"], "APPROVED")

    def test_failed_node_payload_folds_without_charging_rounds(self) -> None:
        context = WorkflowContext(
            identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
            progression=ProgressState("ZHONGSHU_CRITIC", 6, "2026-09-17T00:00:00Z"),
            review=ReviewState(
                revision_id="task-1:2",
                item_workflows=(
                    ItemWorkflow(item_id="item-000001", phase="REVISING", rounds=2),
                ),
            ),
        )

        update = _ConcreteWorkflowState._review_update(
            context,
            {
                "action": "FAIL",
                "task_reviews": [
                    _entry("item-000001", "TASK_CHANGES_REQUIRED", "finding-000001"),
                ],
                "findings": [],
            },
        )

        self.assertEqual(update.item_workflows[0].rounds, 2)
        self.assertEqual(update.item_workflows[0].last_verdict, "TASK_CHANGES_REQUIRED")

    def test_solver_payload_keeps_the_current_table(self) -> None:
        context = WorkflowContext(
            identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
            progression=ProgressState("ZHONGSHU_SOLVER", 8, "2026-09-17T00:00:00Z"),
            review=ReviewState(
                revision_id="task-1:2",
                item_workflows=(ItemWorkflow(item_id="item-000001"),),
            ),
        )

        update = _ConcreteWorkflowState._review_update(
            context, {"plan": {"items": [], "groups": []}},
        )

        self.assertIsNone(update.item_workflows)


if __name__ == "__main__":
    unittest.main()
