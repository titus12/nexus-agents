from __future__ import annotations

import unittest
from dataclasses import replace

from orchestrator.domain.context import (
    ProgressState,
    RecoveryState,
    ReviewState,
    ReviewTaskItem,
    ReviewTaskRecord,
    TaskIdentity,
    WorkflowContext,
)
from orchestrator.domain.events import DomainEvent
from orchestrator.domain.findings import Finding
from orchestrator.domain.states import (
    ZhongshuCriticState,
    _revision_editable_item_ids,
    _task_review_ledger_update,
)
from orchestrator.runtime.reducer import LinearContextReducer
from orchestrator.runtime.repository import WorkflowSnapshot


def _finding(
    finding_id: str,
    *,
    item_id: str = "item-000001",
    status: str = "OPEN",
    severity: str = "P1",
) -> Finding:
    return Finding(
        finding_id=finding_id,
        severity=severity,
        status=status,
        group_id="group-000001",
        item_id=item_id,
        claim="blocking claim",
        required_action="fix it",
    )


def _review_event(action: str, records: tuple[dict, ...]) -> DomainEvent:
    return DomainEvent(
        "NODE_COMPLETED",
        "task-1",
        6,
        {"action": action, "task_reviews": list(records)},
        "2026-09-15T00:00:00Z",
    )


def _record(item_id: str, action: str, *, task_hash: str = "th", dep: str = "dh") -> dict:
    return {
        "review_job_id": f"zhongshu:rev:group-000001:{item_id}",
        "group_id": "group-000001",
        "item_id": item_id,
        "action": action,
        "reviewed_task_hash": task_hash,
        "reviewed_dependency_hash": dep,
    }


class TaskReviewLedgerRatchetTests(unittest.TestCase):
    """An approved task stays approved while its review surface is unchanged."""

    def _context(self, *records: ReviewTaskRecord) -> WorkflowContext:
        return WorkflowContext(
            identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
            progression=ProgressState("ZHONGSHU_CRITIC", 6, "2026-09-15T00:00:00Z"),
            review=ReviewState(revision_id="rev-1", task_review_ledger=records),
        )

    def test_content_identical_demotion_is_ignored(self) -> None:
        context = self._context(
            ReviewTaskRecord(
                item_id="item-000001",
                task_hash="th",
                dependency_hash="dh",
                status="APPROVED",
            )
        )

        update = _task_review_ledger_update(
            context,
            {"task_reviews": [_record("item-000001", "TASK_CHANGES_REQUIRED")]},
        )

        self.assertIsNone(update)

    def test_changed_content_accepts_the_new_verdict(self) -> None:
        context = self._context(
            ReviewTaskRecord(
                item_id="item-000001",
                task_hash="th",
                dependency_hash="dh",
                status="APPROVED",
            )
        )

        update = _task_review_ledger_update(
            context,
            {
                "task_reviews": [
                    _record("item-000001", "TASK_CHANGES_REQUIRED", task_hash="th2")
                ]
            },
        )

        self.assertIsNotNone(update)
        self.assertEqual(update[0].status, "CHANGES_REQUIRED")
        self.assertEqual(update[0].task_hash, "th2")

    def test_changed_dependency_accepts_the_new_verdict(self) -> None:
        context = self._context(
            ReviewTaskRecord(
                item_id="item-000001",
                task_hash="th",
                dependency_hash="dh",
                status="APPROVED",
            )
        )

        update = _task_review_ledger_update(
            context,
            {"task_reviews": [_record("item-000001", "TASK_CHANGES_REQUIRED", dep="dh2")]},
        )

        self.assertIsNotNone(update)
        self.assertEqual(update[0].status, "CHANGES_REQUIRED")


class RevisionScopeExcludesApprovedTasksTests(unittest.TestCase):
    """A revision may not rewrite a task the Critic already accepted."""

    def _review(self, ledger: tuple[ReviewTaskRecord, ...]) -> ReviewState:
        return ReviewState(
            revision_id="rev-1",
            plan={"items": [{"item_id": "item-000001"}, {"item_id": "item-000002"}]},
            findings=(
                _finding("f-1", item_id="item-000001"),
                _finding("f-2", item_id="item-000002"),
            ),
            task_review_ledger=ledger,
        )

    def _payload(self) -> dict:
        return {"finding_batch": {"selected_finding_ids": ["f-1", "f-2"]}}

    def test_approved_task_leaves_the_scope(self) -> None:
        review = self._review(
            (
                ReviewTaskRecord(
                    item_id="item-000001",
                    task_hash="th",
                    dependency_hash="dh",
                    status="APPROVED",
                ),
                ReviewTaskRecord(
                    item_id="item-000002",
                    task_hash="th",
                    dependency_hash="dh",
                    status="CHANGES_REQUIRED",
                ),
            )
        )

        scope = _revision_editable_item_ids(review, self._payload())

        self.assertEqual(scope, {"item-000002"})

    def test_all_approved_scope_is_empty_not_unscoped(self) -> None:
        review = self._review(
            (
                ReviewTaskRecord(
                    item_id="item-000001", status="APPROVED", task_hash="th"
                ),
                ReviewTaskRecord(
                    item_id="item-000002", status="APPROVED", task_hash="th"
                ),
            )
        )

        scope = _revision_editable_item_ids(review, self._payload())

        # An empty set still means "scoped revision: nothing may change", which
        # is different from None ("not a scoped revision: the whole plan wins").
        self.assertEqual(scope, set())


class CriticApprovalGateTests(unittest.TestCase):
    """Freezing requires the whole folded task ledger to be approved."""

    def setUp(self) -> None:
        self.state = ZhongshuCriticState()
        self.reducer = LinearContextReducer()

    def _context(
        self,
        *records: ReviewTaskRecord,
        findings: tuple[Finding, ...] = (),
        round_: int = 0,
        recovery: RecoveryState | None = None,
    ) -> WorkflowContext:
        return WorkflowContext(
            identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
            progression=ProgressState("ZHONGSHU_CRITIC", 6, "2026-09-15T00:00:00Z"),
            recovery=recovery or RecoveryState(),
            review=ReviewState(
                revision_id="rev-1",
                findings=findings,
                task_review_ledger=records,
                zhongshu_revision_round=round_,
                max_zhongshu_revision_rounds=8,
                plan_hash="plan-hash",
            ),
        )

    def _snapshot(self, context: WorkflowContext) -> WorkflowSnapshot:
        return WorkflowSnapshot("task-1", context, 0)

    def test_empty_ledger_still_allows_the_freeze(self) -> None:
        context = self._context()
        decision = self.state.handle(
            context, _review_event("APPROVE_CRITIC", (_record("item-000001", "TASK_APPROVED"),))
        )

        self.assertEqual(decision.transition.action, "APPROVE_CRITIC")
        after = self.reducer.apply(self._snapshot(context), decision)
        self.assertEqual(after.context.progression.state, "ZHONGSHU_FREEZE_CHECK")

    def test_fully_approved_ledger_allows_the_freeze(self) -> None:
        context = self._context(
            ReviewTaskRecord(item_id="item-000001", status="APPROVED", task_hash="th"),
            ReviewTaskRecord(item_id="item-000002", status="APPROVED", task_hash="th"),
        )
        decision = self.state.handle(
            context, _review_event("APPROVE_CRITIC", (_record("item-000001", "TASK_APPROVED"),))
        )

        self.assertEqual(decision.transition.action, "APPROVE_CRITIC")

    def test_approval_is_held_while_a_task_is_unapproved(self) -> None:
        context = self._context(
            ReviewTaskRecord(item_id="item-000001", status="APPROVED", task_hash="th"),
            ReviewTaskRecord(item_id="item-000002", status="CHANGES_REQUIRED", task_hash="th"),
            findings=(_finding("f-2", item_id="item-000002"),),
        )
        decision = self.state.handle(
            context, _review_event("APPROVE_CRITIC", (_record("item-000001", "TASK_APPROVED"),))
        )

        self.assertEqual(decision.transition.action, "REQUEST_SOLVER_REVISION")

    def test_held_approval_without_a_blocker_escalates_to_a_human(self) -> None:
        context = self._context(
            ReviewTaskRecord(item_id="item-000001", status="APPROVED", task_hash="th"),
            ReviewTaskRecord(item_id="item-000002", status="CHANGES_REQUIRED", task_hash="th"),
        )
        decision = self.state.handle(
            context, _review_event("APPROVE_CRITIC", (_record("item-000001", "TASK_APPROVED"),))
        )

        self.assertEqual(decision.transition.action, "HUMAN_GATE")
        self.assertEqual(
            decision.transition.reason_code, "ZHONGSHU_FREEZE_INCOMPLETE"
        )
        after = self.reducer.apply(self._snapshot(context), decision)
        self.assertEqual(after.context.progression.state, "HUMAN_GATE")

    def test_held_approval_blocks_when_the_revision_budget_is_spent(self) -> None:
        context = self._context(
            ReviewTaskRecord(item_id="item-000001", status="APPROVED", task_hash="th"),
            ReviewTaskRecord(item_id="item-000002", status="CHANGES_REQUIRED", task_hash="th"),
            findings=(_finding("f-2", item_id="item-000002"),),
            round_=8,
        )
        decision = self.state.handle(
            context, _review_event("APPROVE_CRITIC", (_record("item-000001", "TASK_APPROVED"),))
        )

        self.assertEqual(decision.transition.action, "BLOCKED")
        self.assertEqual(
            decision.transition.reason_code, "ZHONGSHU_REVISION_BUDGET_EXHAUSTED"
        )

    def test_partial_ledger_cannot_freeze_unreviewed_tasks(self) -> None:
        context = self._context(
            ReviewTaskRecord(item_id="item-000001", status="APPROVED", task_hash="th"),
        )
        context = replace(
            context,
            review=replace(
                context.review,
                task_items=(
                    ReviewTaskItem("item-000001", "group-000001", order=0),
                    ReviewTaskItem("item-000002", "group-000001", order=1),
                ),
            ),
        )
        decision = self.state.handle(
            context, _review_event("APPROVE_CRITIC", (_record("item-000001", "TASK_APPROVED"),))
        )

        self.assertEqual(decision.transition.action, "HUMAN_GATE")
        self.assertEqual(
            decision.transition.reason_code, "ZHONGSHU_FREEZE_INCOMPLETE"
        )

    def test_revision_request_freezes_when_nothing_is_left_to_revise(self) -> None:
        context = self._context(
            ReviewTaskRecord(item_id="item-000001", status="APPROVED", task_hash="th"),
            ReviewTaskRecord(item_id="item-000002", status="APPROVED", task_hash="th"),
        )
        decision = self.state.handle(
            context, _review_event("REQUEST_SOLVER_REVISION", ())
        )

        self.assertEqual(decision.transition.action, "APPROVE_CRITIC")
        after = self.reducer.apply(self._snapshot(context), decision)
        self.assertEqual(after.context.progression.state, "ZHONGSHU_FREEZE_CHECK")


class ConvergenceSimulationTests(unittest.TestCase):
    """Approvals must accumulate monotonically until the freeze is reachable."""

    def setUp(self) -> None:
        self.state = ZhongshuCriticState()
        self.reducer = LinearContextReducer()

    def _context(
        self, ledger: tuple[ReviewTaskRecord, ...], findings: tuple[Finding, ...] = ()
    ) -> WorkflowContext:
        return WorkflowContext(
            identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
            progression=ProgressState("ZHONGSHU_CRITIC", 6, "2026-09-15T00:00:00Z"),
            review=ReviewState(
                revision_id="rev-1",
                plan={"items": [{"item_id": "item-000001"}, {"item_id": "item-000002"},
                                {"item_id": "item-000003"}]},
                task_items=(
                    ReviewTaskItem("item-000001", "group-000001", order=0),
                    ReviewTaskItem("item-000002", "group-000001", order=1),
                    ReviewTaskItem("item-000003", "group-000002", order=2),
                ),
                findings=findings,
                task_review_ledger=ledger,
                zhongshu_revision_round=1,
                max_zhongshu_revision_rounds=8,
                plan_hash="plan-hash",
            ),
        )

    def _step(
        self,
        context: WorkflowContext,
        payload: dict,
        action: str,
    ) -> WorkflowContext:
        decision = self.state.handle(
            context,
            DomainEvent(
                "NODE_COMPLETED",
                "task-1",
                context.progression.sequence,
                {"action": action, **payload},
                "2026-09-15T00:00:00Z",
            ),
        )
        return self.reducer.apply(
            WorkflowSnapshot("task-1", context, 0), decision
        ).context

    def _next_round(self, context: WorkflowContext) -> WorkflowContext:
        """The Solver leg of the loop is not under test: return to the Critic."""

        return replace(
            context,
            progression=replace(context.progression, state="ZHONGSHU_CRITIC"),
        )

    def test_approvals_accumulate_and_the_freeze_is_reached(self) -> None:
        # Round 1: one task passes, two have blockers.
        context = self._step(
            self._context(()),
            {
                "task_reviews": [
                    _record("item-000001", "TASK_CHANGES_REQUIRED", task_hash="h1"),
                    _record("item-000002", "TASK_CHANGES_REQUIRED", task_hash="h1"),
                    _record("item-000003", "TASK_APPROVED", task_hash="h1"),
                ],
                "findings": [
                    _finding("f-1", item_id="item-000001").to_dict(),
                    _finding("f-2", item_id="item-000002").to_dict(),
                ],
            },
            "REQUEST_SOLVER_REVISION",
        )
        statuses = {r.item_id: r.status for r in context.review.task_review_ledger}
        self.assertEqual(
            statuses,
            {
                "item-000001": "CHANGES_REQUIRED",
                "item-000002": "CHANGES_REQUIRED",
                "item-000003": "APPROVED",
            },
        )
        context = self._next_round(context)

        # The next revision may only touch the two rejected tasks.
        scope = _revision_editable_item_ids(
            context.review,
            {
                "finding_batch": {
                    "selected_finding_ids": ["f-1", "f-2"],
                }
            },
        )
        self.assertEqual(scope, {"item-000001", "item-000002"})

        # A re-dispatched verdict on the untouched, already-approved task is a
        # no-op: the approval is a ratchet.
        context = self._step(
            context,
            {
                "task_reviews": [
                    _record("item-000003", "TASK_CHANGES_REQUIRED", task_hash="h1"),
                ],
            },
            "REQUEST_SOLVER_REVISION",
        )
        statuses = {r.item_id: r.status for r in context.review.task_review_ledger}
        self.assertEqual(statuses["item-000003"], "APPROVED")
        context = self._next_round(context)

        # Round 2: a second task passes.
        context = self._step(
            context,
            {
                "task_reviews": [
                    _record("item-000001", "TASK_APPROVED", task_hash="h1"),
                    _record("item-000002", "TASK_CHANGES_REQUIRED", task_hash="h1"),
                ],
                "findings": [_finding("f-2", item_id="item-000002").to_dict()],
            },
            "REQUEST_SOLVER_REVISION",
        )
        statuses = {r.item_id: r.status for r in context.review.task_review_ledger}
        self.assertEqual(
            statuses,
            {
                "item-000001": "APPROVED",
                "item-000002": "CHANGES_REQUIRED",
                "item-000003": "APPROVED",
            },
        )
        context = self._next_round(context)

        # Round 3: the last task passes, so the whole ledger is approved and the
        # aggregate's approval is honoured instead of held back.
        context = self._step(
            context,
            {
                "task_reviews": [_record("item-000002", "TASK_APPROVED", task_hash="h1")],
            },
            "APPROVE_CRITIC",
        )
        self.assertEqual(context.progression.state, "ZHONGSHU_FREEZE_CHECK")
        self.assertTrue(
            all(r.status == "APPROVED" for r in context.review.task_review_ledger)
        )


class EmptyRevisionScopeTests(unittest.TestCase):
    """An empty scope is a scoped revision, not an unscoped one."""

    _CURRENT = {
        "items": [{"item_id": "item-000001", "objective": "reviewed"}],
        "groups": [],
    }
    _REWRITTEN = {
        "items": [{"item_id": "item-000001", "objective": "rewritten"}],
        "groups": [],
    }

    def _materialize(self, editable):
        from orchestrator.domain.policies.solver_plan import materialize_solver_reply

        plan, error = materialize_solver_reply(
            {"plan": self._REWRITTEN}, self._CURRENT, editable_item_ids=editable
        )
        return error, plan

    def test_empty_scope_carries_the_reviewed_plan_forward(self) -> None:
        error, plan = self._materialize(set())

        self.assertEqual(error, "")
        self.assertEqual(plan["items"][0]["objective"], "reviewed")

    def test_no_scope_lets_the_new_plan_win(self) -> None:
        error, plan = self._materialize(None)

        self.assertEqual(error, "")
        self.assertEqual(plan["items"][0]["objective"], "rewritten")


class ReviewJobSelectionTests(unittest.TestCase):
    """The ratchet's other half: which tasks are re-dispatched at all."""

    def _review(self, *records: ReviewTaskRecord) -> ReviewState:
        return ReviewState(revision_id="rev-1", task_review_ledger=records)

    def _jobs(self):
        from orchestrator.zhongshu_review_queue import queue_from_dispatch_contexts

        contexts = [
            {
                "zhongshu_dispatch_mode": "task_review",
                "review_job_id": f"zhongshu:rev-1:group-000001:{item_id}",
                "revision_id": "rev-1",
                "group_id": "group-000001",
                "item_id": item_id,
                "task_hash": "th",
                "dependency_hash": "dh",
            }
            for item_id in ("item-000001", "item-000002")
        ]
        return queue_from_dispatch_contexts(contexts, "rev-1", "plan-hash").jobs

    def _selected(self, review) -> list[str]:
        from orchestrator.domain.states import _select_review_jobs

        return [job.item_id for job in _select_review_jobs(self._jobs(), review)]

    def test_unchanged_approved_task_is_not_re_reviewed(self) -> None:
        review = self._review(
            ReviewTaskRecord(
                item_id="item-000001",
                task_hash="th",
                dependency_hash="dh",
                status="APPROVED",
            ),
            ReviewTaskRecord(
                item_id="item-000002",
                task_hash="th",
                dependency_hash="dh",
                status="CHANGES_REQUIRED",
            ),
        )

        self.assertEqual(self._selected(review), ["item-000002"])

    def test_approved_task_with_changed_content_is_re_reviewed(self) -> None:
        review = self._review(
            ReviewTaskRecord(
                item_id="item-000001",
                task_hash="other",
                dependency_hash="dh",
                status="APPROVED",
            ),
            ReviewTaskRecord(
                item_id="item-000002",
                task_hash="th",
                dependency_hash="dh",
                status="APPROVED",
            ),
        )

        self.assertEqual(self._selected(review), ["item-000001"])

    def test_approved_task_with_changed_dependency_is_re_reviewed(self) -> None:
        review = self._review(
            ReviewTaskRecord(
                item_id="item-000001",
                task_hash="th",
                dependency_hash="other",
                status="APPROVED",
            ),
            ReviewTaskRecord(
                item_id="item-000002",
                task_hash="th",
                dependency_hash="dh",
                status="APPROVED",
            ),
        )

        self.assertEqual(self._selected(review), ["item-000001"])

    def test_everything_approved_falls_back_to_a_full_pass(self) -> None:
        review = self._review(
            ReviewTaskRecord(
                item_id="item-000001",
                task_hash="th",
                dependency_hash="dh",
                status="APPROVED",
            ),
            ReviewTaskRecord(
                item_id="item-000002",
                task_hash="th",
                dependency_hash="dh",
                status="APPROVED",
            ),
        )

        # Nothing needs re-review, so the caller re-dispatches everything rather
        # than dispatching zero workers; the approval ratchet keeps the verdicts.
        self.assertEqual(
            self._selected(review), ["item-000001", "item-000002"]
        )


class LedgerPersistenceTests(unittest.TestCase):
    """The ratchet and the stall counter must survive a persisted restart."""

    def test_changes_rounds_survives_a_dto_round_trip(self) -> None:
        from orchestrator.domain.context import context_from_dto, context_to_dto

        context = WorkflowContext(
            identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
            progression=ProgressState("ZHONGSHU_CRITIC", 6, "2026-09-15T00:00:00Z"),
            review=ReviewState(
                revision_id="rev-1",
                task_review_ledger=(
                    ReviewTaskRecord(
                        item_id="item-000001",
                        task_hash="th",
                        dependency_hash="dh",
                        status="CHANGES_REQUIRED",
                        changes_rounds=2,
                    ),
                ),
            ),
        )

        restored = context_from_dto(context_to_dto(context))

        record = restored.review.task_review_ledger[0]
        self.assertEqual(record.status, "CHANGES_REQUIRED")
        self.assertEqual(record.changes_rounds, 2)
        self.assertEqual(record.task_hash, "th")
        self.assertEqual(record.dependency_hash, "dh")


if __name__ == "__main__":
    unittest.main()
