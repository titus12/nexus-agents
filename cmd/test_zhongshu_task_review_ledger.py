from __future__ import annotations

from dataclasses import replace
import unittest

from orchestrator.domain.context import (
    ProgressState,
    RequestState,
    ReviewState,
    ReviewTaskGroup,
    ReviewTaskItem,
    ReviewTaskRecord,
    TaskIdentity,
    WorkflowContext,
    context_from_dto,
    context_to_dto,
)
from orchestrator.domain.events import DomainEvent
from orchestrator.domain.states import ZhongshuCriticState
from orchestrator.runtime.reducer import LinearContextReducer
from orchestrator.runtime.repository import WorkflowSnapshot


def _context(ledger: tuple[ReviewTaskRecord, ...] = ()) -> WorkflowContext:
    return WorkflowContext(
        identity=TaskIdentity("task-1", "issue-1", "", "request-1"),
        progression=ProgressState("ZHONGSHU_CRITIC", 5, "2026-09-11T00:00:00Z"),
        request=RequestState(raw_request="review"),
        review=ReviewState(
            revision_id="rev-1",
            plan_hash="plan-hash",
            task_items=(
                ReviewTaskItem("item-01", "group-01", title="t1", objective="o1",
                               source_requirement_ids=("req-1",), acceptance_signals=("a1",)),
                ReviewTaskItem("item-02", "group-01", title="t2", objective="o2",
                               source_requirement_ids=("req-1",), acceptance_signals=("a2",)),
                ReviewTaskItem("item-03", "group-02", title="t3", objective="o3",
                               source_requirement_ids=("req-2",), acceptance_signals=("a3",)),
            ),
            task_groups=(
                ReviewTaskGroup("group-01", ("item-01", "item-02")),
                ReviewTaskGroup("group-02", ("item-03",)),
            ),
            task_review_ledger=ledger,
        ),
    )


def _bindings(context: WorkflowContext) -> list[dict]:
    effect = ZhongshuCriticState._dispatch_effect(
        context, "ZHONGSHU_CRITIC", revision_id="rev-1", plan_hash="plan-hash"
    )
    return list(effect.payload["bindings"])


def _hashes(binding: dict) -> tuple[str, str]:
    ctx = binding["dispatch_context"]
    return ctx["task_hash"], ctx["dependency_hash"]


class TaskReviewLedgerTests(unittest.TestCase):
    def test_first_round_reviews_every_task(self) -> None:
        bindings = _bindings(_context())
        self.assertEqual([b["item_id"] for b in bindings], ["item-01", "item-02", "item-03"])

    def test_approved_unchanged_task_is_carried_forward(self) -> None:
        first = _bindings(_context())
        task_hash, dependency_hash = _hashes(first[0])
        ledger = (
            ReviewTaskRecord("item-01", task_hash, dependency_hash, "APPROVED"),
        )
        bindings = _bindings(_context(ledger))
        self.assertEqual([b["item_id"] for b in bindings], ["item-02", "item-03"])

    def test_changed_task_hash_forces_re_review(self) -> None:
        ledger = (
            ReviewTaskRecord("item-01", "stale-hash", "stale-dep", "APPROVED"),
        )
        bindings = _bindings(_context(ledger))
        self.assertEqual(
            [b["item_id"] for b in bindings], ["item-01", "item-02", "item-03"]
        )

    def test_rejected_task_is_re_reviewed(self) -> None:
        first = _bindings(_context())
        task_hash, dependency_hash = _hashes(first[1])
        ledger = (
            ReviewTaskRecord("item-02", task_hash, dependency_hash, "CHANGES_REQUIRED"),
        )
        bindings = _bindings(_context(ledger))
        self.assertIn("item-02", [b["item_id"] for b in bindings])

    def test_review_update_records_verdicts(self) -> None:
        payload = {
            "findings": [],
            "revision_id": "rev-1",
            "task_reviews": [
                {"item_id": "item-01", "action": "TASK_APPROVED",
                 "reviewed_task_hash": "h1", "reviewed_dependency_hash": "d1"},
                {"item_id": "item-02", "action": "TASK_CHANGES_REQUIRED",
                 "reviewed_task_hash": "h2", "reviewed_dependency_hash": "d2"},
            ],
        }
        update = ZhongshuCriticState._review_update(_context(), payload)
        ledger = {record.item_id: record for record in update.task_review_ledger}
        self.assertEqual(ledger["item-01"].status, "APPROVED")
        self.assertEqual(ledger["item-02"].status, "CHANGES_REQUIRED")
        self.assertEqual(ledger["item-02"].task_hash, "h2")

    def test_approved_task_closes_its_active_findings(self) -> None:
        context = _context()
        from dataclasses import replace

        from orchestrator.domain.findings import Finding

        finding = Finding(
            finding_id="f-1", severity="P0", status="OPEN", item_id="item-01",
            group_id="group-01",
        )
        context = replace(
            context,
            review=replace(context.review, findings=(finding,)),
        )
        payload = {
            "findings": [],
            "revision_id": "rev-1",
            "task_reviews": [
                {"item_id": "item-01", "action": "TASK_APPROVED",
                 "reviewed_task_hash": "h1", "reviewed_dependency_hash": "d1"},
            ],
        }
        update = ZhongshuCriticState._review_update(context, payload)
        merged = {item.item_id: item for item in update.findings}
        self.assertEqual(merged["item-01"].status, "RESOLVED")

    def test_review_update_without_task_reviews_is_noop(self) -> None:
        update = ZhongshuCriticState._review_update(
            _context(), {"findings": [], "revision_id": "rev-1"}
        )
        self.assertIsNone(update.task_review_ledger)

    def test_failed_retryable_event_folds_partial_verdicts_and_shrinks_retry(self) -> None:
        context = _context()
        first = _bindings(context)
        h1 = _hashes(first[0])
        h2 = _hashes(first[1])
        event = DomainEvent(
            "FAIL",
            "task-1",
            5,
            {
                "action": "FAIL",
                "retryable": True,
                "node_run_id": "node:1",
                "task_reviews": [
                    {
                        "item_id": "item-01",
                        "action": "TASK_APPROVED",
                        "reviewed_task_hash": h1[0],
                        "reviewed_dependency_hash": h1[1],
                    },
                    {
                        "item_id": "item-02",
                        "action": "TASK_CHANGES_REQUIRED",
                        "reviewed_task_hash": h2[0],
                        "reviewed_dependency_hash": h2[1],
                    },
                ],
            },
            "2026-09-11T00:00:00Z",
        )

        decision = ZhongshuCriticState().handle(context, event)

        self.assertEqual(decision.transition.action, "RETRY")
        ledger = {
            record.item_id: record
            for record in decision.update.review.task_review_ledger
        }
        self.assertEqual(ledger["item-01"].status, "APPROVED")
        self.assertEqual(ledger["item-02"].status, "CHANGES_REQUIRED")

        after = LinearContextReducer().apply(
            WorkflowSnapshot("task-1", context, 0), decision
        )
        # The retry must only re-dispatch the missing/changed tasks.
        bindings = _bindings(after.context)
        self.assertEqual(
            [binding["item_id"] for binding in bindings], ["item-02", "item-03"]
        )

    def test_ledger_round_trips_through_dto(self) -> None:
        context = _context((
            ReviewTaskRecord("item-01", "h1", "d1", "APPROVED"),
        ))
        restored = context_from_dto(context_to_dto(context))
        self.assertEqual(restored.review.task_review_ledger, context.review.task_review_ledger)
        self.assertEqual(
            restored.review.task_items[0].source_requirement_ids, ("req-1",)
        )

    def test_first_critic_round_uses_projected_task_items(self) -> None:
        populated = _context()
        stale = replace(
            populated,
            review=replace(
                populated.review, task_items=(), task_groups=()
            ),
        )
        effect = ZhongshuCriticState._dispatch_effect(
            stale,
            "ZHONGSHU_CRITIC",
            revision_id="rev-1",
            plan_hash="plan-hash",
            pending_review=populated.review,
        )
        self.assertEqual(effect.payload["dispatch_mode"], "task_review")
        self.assertEqual(len(effect.payload["bindings"]), 3)

    def test_critic_without_task_items_falls_back_to_whole_plan(self) -> None:
        context = _context()
        stale = replace(
            context,
            review=replace(context.review, task_items=(), task_groups=()),
        )
        effect = ZhongshuCriticState._dispatch_effect(
            stale, "ZHONGSHU_CRITIC", revision_id="rev-1", plan_hash="plan-hash"
        )
        self.assertNotEqual(effect.payload.get("dispatch_mode"), "task_review")


if __name__ == "__main__":
    unittest.main()
