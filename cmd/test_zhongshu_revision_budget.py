from __future__ import annotations

import unittest

from orchestrator.domain.context import (
    ProgressState,
    RecoveryState,
    ReviewState,
    TaskIdentity,
    WorkflowContext,
)
from orchestrator.domain.events import DomainEvent
from orchestrator.domain.findings import Finding
from orchestrator.domain.states import (
    ZhongshuCriticState,
    ZhongshuFreezeCheckState,
    ZhongshuSolverState,
)
from orchestrator.runtime.reducer import LinearContextReducer
from orchestrator.runtime.repository import WorkflowSnapshot


def _context(round_: int, max_rounds: int = 8) -> WorkflowContext:
    return WorkflowContext(
        identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
        progression=ProgressState("ZHONGSHU_CRITIC", 4, "2026-09-13T00:00:00Z"),
        review=ReviewState(
            revision_id="task-1:ZHONGSHU_ANALYST:2",
            zhongshu_revision_round=round_,
            max_zhongshu_revision_rounds=max_rounds,
        ),
    )


def _event(action: str) -> DomainEvent:
    return DomainEvent(
        "NODE_COMPLETED",
        "task-1",
        4,
        {"action": action},
        "2026-09-13T00:00:00Z",
    )


class ZhongshuRevisionBudgetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = ZhongshuCriticState()
        self.reducer = LinearContextReducer()

    def _snapshot(self, context: WorkflowContext) -> WorkflowSnapshot:
        return WorkflowSnapshot("task-1", context, 0)

    def test_revision_request_increments_round_and_dispatches_solver(self) -> None:
        context = _context(round_=0)
        decision = self.state.handle(context, _event("REQUEST_SOLVER_REVISION"))

        self.assertEqual(decision.transition.action, "REQUEST_SOLVER_REVISION")
        self.assertEqual(decision.update.review.zhongshu_revision_round, 1)
        self.assertTrue(decision.effects)
        self.assertEqual(
            decision.effects[0].payload.get("target_state"), "ZHONGSHU_SOLVER"
        )

        after = self.reducer.apply(self._snapshot(context), decision)
        self.assertEqual(after.context.progression.state, "ZHONGSHU_SOLVER")
        self.assertEqual(after.context.review.zhongshu_revision_round, 1)
        self.assertEqual(after.context.review.max_zhongshu_revision_rounds, 8)

    def test_last_allowed_revision_reaches_the_cap(self) -> None:
        context = _context(round_=7)
        decision = self.state.handle(context, _event("REQUEST_SOLVER_REVISION"))

        self.assertEqual(decision.transition.action, "REQUEST_SOLVER_REVISION")
        self.assertEqual(decision.update.review.zhongshu_revision_round, 8)

    def test_task_changes_required_consumes_a_round(self) -> None:
        context = _context(round_=0)
        decision = self.state.handle(context, _event("TASK_CHANGES_REQUIRED"))

        self.assertEqual(decision.transition.action, "TASK_CHANGES_REQUIRED")
        self.assertEqual(decision.update.review.zhongshu_revision_round, 1)

    def test_exhausted_budget_blocks_instead_of_looping(self) -> None:
        context = _context(round_=8)
        decision = self.state.handle(context, _event("REQUEST_SOLVER_REVISION"))

        self.assertEqual(decision.transition.action, "BLOCKED")
        self.assertEqual(
            decision.transition.reason_code, "ZHONGSHU_REVISION_BUDGET_EXHAUSTED"
        )
        self.assertEqual(
            decision.update.recovery.blocked_reason,
            "ZHONGSHU_REVISION_BUDGET_EXHAUSTED",
        )
        self.assertFalse(decision.effects)

        after = self.reducer.apply(self._snapshot(context), decision)
        self.assertEqual(after.context.progression.state, "BLOCKED")
        self.assertEqual(after.context.progression.resume_state, "ZHONGSHU_CRITIC")

    def test_budget_does_not_block_approval(self) -> None:
        context = _context(round_=8)
        decision = self.state.handle(context, _event("APPROVE_CRITIC"))

        self.assertEqual(decision.transition.action, "APPROVE_CRITIC")
        after = self.reducer.apply(self._snapshot(context), decision)
        self.assertEqual(after.context.progression.state, "ZHONGSHU_FREEZE_CHECK")


class ZhongshuFreezeBudgetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = ZhongshuFreezeCheckState()
        self.reducer = LinearContextReducer()

    def _context(self, attempt: int, max_attempts: int = 2) -> WorkflowContext:
        return WorkflowContext(
            identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
            progression=ProgressState("ZHONGSHU_FREEZE_CHECK", 6, "2026-09-13T00:00:00Z"),
            review=ReviewState(
                revision_id="task-1:ZHONGSHU_ANALYST:2",
                freeze_check_attempt=attempt,
                max_freeze_check_attempts=max_attempts,
            ),
        )

    def _snapshot(self, context: WorkflowContext) -> WorkflowSnapshot:
        return WorkflowSnapshot("task-1", context, 0)

    def test_freeze_rejection_increments_attempt(self) -> None:
        context = self._context(attempt=0)
        decision = self.state.handle(context, _event("FREEZE_REJECTED"))

        self.assertEqual(decision.transition.action, "FREEZE_REJECTED")
        self.assertEqual(decision.update.review.freeze_check_attempt, 1)
        after = self.reducer.apply(self._snapshot(context), decision)
        self.assertEqual(after.context.progression.state, "ZHONGSHU_SOLVER")
        self.assertEqual(after.context.review.freeze_check_attempt, 1)

    def test_exhausted_freeze_budget_blocks(self) -> None:
        context = self._context(attempt=2)
        decision = self.state.handle(context, _event("FREEZE_REJECTED"))

        self.assertEqual(decision.transition.action, "BLOCKED")
        self.assertEqual(
            decision.transition.reason_code, "ZHONGSHU_FREEZE_BUDGET_EXHAUSTED"
        )
        self.assertFalse(decision.effects)
        after = self.reducer.apply(self._snapshot(context), decision)
        self.assertEqual(after.context.progression.state, "BLOCKED")
        self.assertEqual(
            after.context.progression.resume_state, "ZHONGSHU_FREEZE_CHECK"
        )


def _finding(finding_id: str, severity: str = "P1", status: str = "OPEN", item_id: str = "item-000001") -> Finding:
    return Finding(
        finding_id=finding_id,
        severity=severity,
        status=status,
        group_id="group-001",
        item_id=item_id,
        claim="blocking claim",
        required_action="fix it",
    )


class ZhongshuNoProgressTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = ZhongshuCriticState()
        self.reducer = LinearContextReducer()

    def _context(
        self,
        findings: tuple[Finding, ...],
        *,
        fingerprint: str = "",
        no_progress: int = 0,
        max_no_progress: int = 3,
    ) -> WorkflowContext:
        return WorkflowContext(
            identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
            progression=ProgressState("ZHONGSHU_CRITIC", 6, "2026-09-13T00:00:00Z"),
            recovery=RecoveryState(
                no_progress_count=no_progress, max_no_progress=max_no_progress
            ),
            review=ReviewState(
                revision_id="task-1:ZHONGSHU_ANALYST:2",
                findings=findings,
                zhongshu_revision_round=2,
                max_zhongshu_revision_rounds=8,
                last_reply_fingerprint=fingerprint,
                plan_hash="plan-hash",
            ),
        )

    def _snapshot(self, context: WorkflowContext) -> WorkflowSnapshot:
        return WorkflowSnapshot("task-1", context, 0)

    def test_first_observed_round_is_not_a_stall(self) -> None:
        context = self._context((_finding("f-1"),))
        decision = self.state.handle(context, _event("REQUEST_SOLVER_REVISION"))

        self.assertEqual(decision.transition.action, "REQUEST_SOLVER_REVISION")
        self.assertEqual(decision.update.recovery.no_progress_count, 0)
        self.assertEqual(decision.update.review.last_reply_fingerprint, "blockers=1")

    def test_repeated_blocker_count_blocks_after_budget(self) -> None:
        context = self._context(
            (_finding("f-1"),), fingerprint="blockers=1", no_progress=2
        )
        decision = self.state.handle(context, _event("REQUEST_SOLVER_REVISION"))

        self.assertEqual(decision.transition.action, "BLOCKED")
        self.assertEqual(decision.transition.reason_code, "ZHONGSHU_NO_PROGRESS")
        self.assertEqual(
            decision.update.recovery.blocked_reason, "ZHONGSHU_NO_PROGRESS"
        )
        after = self.reducer.apply(self._snapshot(context), decision)
        self.assertEqual(after.context.progression.state, "BLOCKED")

    def test_fewer_blockers_resets_no_progress_counter(self) -> None:
        context = self._context(
            (_finding("f-1"),), fingerprint="blockers=3", no_progress=2
        )
        decision = self.state.handle(context, _event("REQUEST_SOLVER_REVISION"))

        self.assertEqual(decision.transition.action, "REQUEST_SOLVER_REVISION")
        self.assertEqual(decision.update.recovery.no_progress_count, 0)
        self.assertEqual(decision.update.review.last_reply_fingerprint, "blockers=1")
        after = self.reducer.apply(self._snapshot(context), decision)
        self.assertEqual(after.context.recovery.no_progress_count, 0)
        self.assertEqual(after.context.review.last_reply_fingerprint, "blockers=1")


class SolverContextTests(unittest.TestCase):
    def test_solver_dispatch_carries_active_findings(self) -> None:
        context = WorkflowContext(
            identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
            progression=ProgressState("ZHONGSHU_CRITIC", 6, "2026-09-13T00:00:00Z"),
            review=ReviewState(
                revision_id="task-1:ZHONGSHU_ANALYST:2",
                findings=(
                    _finding("f-1"),
                    _finding("f-2", status="RESOLVED"),
                    _finding("f-3", severity="P2"),
                ),
                plan_hash="plan-hash",
            ),
        )

        effect = ZhongshuSolverState._dispatch_effect(
            context, "ZHONGSHU_SOLVER", plan_hash="plan-hash"
        )
        dispatch_context = effect.payload["dispatch_context"]

        self.assertEqual(dispatch_context["focus_finding_ids"], ["f-1", "f-3"])
        self.assertEqual(
            [item["finding_id"] for item in dispatch_context["active_findings"]],
            ["f-1", "f-3"],
        )

    def test_solver_dispatch_sees_findings_from_the_same_verdict(self) -> None:
        # The Critic's verdict arrives with the findings in the event payload;
        # the outgoing Solver dispatch must observe them even though the
        # pre-transition context had none.
        context = _context(round_=0)
        event = DomainEvent(
            "NODE_COMPLETED",
            "task-1",
            4,
            {
                "action": "REQUEST_SOLVER_REVISION",
                "findings": [
                    {
                        "finding_id": "f-1",
                        "severity": "P1",
                        "status": "OPEN",
                        "group_id": "group-001",
                        "item_id": "item-000001",
                        "claim": "blocking claim",
                        "required_action": "fix it",
                    }
                ],
            },
            "2026-09-13T00:00:00Z",
        )

        decision = ZhongshuCriticState().handle(context, event)

        self.assertEqual(decision.transition.action, "REQUEST_SOLVER_REVISION")
        solver = decision.effects[0]
        self.assertEqual(solver.payload.get("target_state"), "ZHONGSHU_SOLVER")
        dispatch_context = solver.payload["dispatch_context"]
        self.assertEqual(dispatch_context["focus_finding_ids"], ["f-1"])
        self.assertEqual(
            [item["finding_id"] for item in dispatch_context["active_findings"]],
            ["f-1"],
        )
        self.assertIn("Current review finding count: 1", solver.payload["prompt_ref"])


class ZhongshuTaskReviewInvalidRetryTests(unittest.TestCase):
    """A worker reply that is unusable is re-asked on the reply budget."""

    def _context(self, *, reply_retry: int = 0, retry: int = 0) -> WorkflowContext:
        return WorkflowContext(
            identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
            progression=ProgressState("ZHONGSHU_CRITIC", 6, "2026-09-13T00:00:00Z"),
            recovery=RecoveryState(
                retry_count=retry,
                max_retries=3,
                reply_retry_count=reply_retry,
                max_reply_retries=3,
            ),
            review=ReviewState(revision_id="task-1:ZHONGSHU_ANALYST:2"),
        )

    def _event(self) -> DomainEvent:
        return DomainEvent(
            "FAIL",
            "task-1",
            6,
            {
                "action": "FAIL",
                "retryable": True,
                "failure": {
                    "failure_id": "n:invalid",
                    "stage": "node_join",
                    "owner_component": "zhongshu_task_review_fan_in",
                    "task_id": "task-1",
                    "state": "ZHONGSHU_CRITIC",
                    "sequence": 6,
                    "node_run_id": "node:task-1:ZHONGSHU_CRITIC:6",
                    "worker_id": None,
                    "effect_id": None,
                    "error_code": "NODE_TASK_REVIEW_RESULT_INVALID",
                    "retryable": True,
                    "message": "one or more task-review replies were unusable",
                    "cause_type": "FanInValidation",
                },
            },
            "2026-09-13T00:00:00Z",
        )

    def test_unusable_reply_retries_on_the_reply_budget(self) -> None:
        decision = ZhongshuCriticState().handle(self._context(reply_retry=0), self._event())

        self.assertEqual(decision.transition.action, "RETRY")
        self.assertEqual(decision.update.recovery.reply_retry_count, 1)
        self.assertIsNone(decision.update.recovery.retry_count)

    def test_unusable_reply_escalates_to_human_gate_when_reply_budget_spent(self) -> None:
        decision = ZhongshuCriticState().handle(self._context(reply_retry=3), self._event())

        self.assertEqual(decision.transition.action, "HUMAN_GATE")

    def test_transient_failure_still_uses_the_shared_retry_budget(self) -> None:
        context = self._context(retry=0)
        event = DomainEvent(
            "FAIL",
            "task-1",
            6,
            {
                "action": "FAIL",
                "retryable": True,
                "failure": {
                    "failure_id": "n:worker",
                    "stage": "agent_result",
                    "owner_component": "agent_dispatch",
                    "task_id": "task-1",
                    "state": "ZHONGSHU_CRITIC",
                    "sequence": 6,
                    "node_run_id": "node:task-1:ZHONGSHU_CRITIC:6",
                    "worker_id": "worker-01",
                    "effect_id": None,
                    "error_code": "AGENT_REPLY_CONTRACT_REJECTED",
                    "retryable": True,
                    "message": "missing",
                    "cause_type": "ReplyValidationError",
                },
            },
            "2026-09-13T00:00:00Z",
        )
        decision = ZhongshuCriticState().handle(context, event)

        self.assertEqual(decision.transition.action, "RETRY")
        self.assertEqual(decision.update.recovery.retry_count, 1)
        self.assertIsNone(decision.update.recovery.reply_retry_count)
        self.assertIsNone(decision.update.recovery.external_retry_count)


if __name__ == "__main__":
    unittest.main()
