from __future__ import annotations

from dataclasses import replace

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
from orchestrator.domain.policies.zhongshu import (
    blocker_fingerprint,
    revision_made_progress,
)
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
        findings = (_finding("f-1"),)
        context = self._context(findings)
        decision = self.state.handle(context, _event("REQUEST_SOLVER_REVISION"))

        self.assertEqual(decision.transition.action, "REQUEST_SOLVER_REVISION")
        self.assertEqual(decision.update.recovery.no_progress_count, 0)
        self.assertEqual(
            decision.update.review.last_reply_fingerprint,
            blocker_fingerprint(findings),
        )

    def test_repeated_p0_blocker_blocks_after_budget(self) -> None:
        findings = (_finding("f-1", severity="P0"),)
        context = self._context(
            findings,
            fingerprint=blocker_fingerprint(findings),
            no_progress=2,
        )
        decision = self.state.handle(context, _event("REQUEST_SOLVER_REVISION"))

        self.assertEqual(decision.transition.action, "BLOCKED")
        self.assertEqual(decision.transition.reason_code, "ZHONGSHU_NO_PROGRESS")
        self.assertEqual(
            decision.update.recovery.blocked_reason, "ZHONGSHU_NO_PROGRESS"
        )
        after = self.reducer.apply(self._snapshot(context), decision)
        self.assertEqual(after.context.progression.state, "BLOCKED")

    def test_repeated_p1_blocker_freezes_with_followups(self) -> None:
        findings = (_finding("f-1"),)
        context = self._context(
            findings,
            fingerprint=blocker_fingerprint(findings),
            no_progress=2,
        )
        decision = self.state.handle(context, _event("REQUEST_SOLVER_REVISION"))

        self.assertEqual(decision.transition.action, "APPROVE_CRITIC")
        self.assertEqual(
            decision.transition.reason_code, "ZHONGSHU_FREEZE_WITH_FOLLOWUPS"
        )
        after = self.reducer.apply(self._snapshot(context), decision)
        self.assertEqual(after.context.progression.state, "ZHONGSHU_FREEZE_CHECK")
        (finding,) = after.context.review.findings
        self.assertEqual(finding.status, "DEFERRED")

    def test_fewer_blockers_resets_no_progress_counter(self) -> None:
        previous = (
            _finding("f-1"),
            _finding("f-2", item_id="item-000002"),
            _finding("f-3", item_id="item-000003"),
        )
        current = (_finding("f-1"),)
        context = self._context(
            current,
            fingerprint=blocker_fingerprint(previous),
            no_progress=2,
        )
        decision = self.state.handle(context, _event("REQUEST_SOLVER_REVISION"))

        self.assertEqual(decision.transition.action, "REQUEST_SOLVER_REVISION")
        self.assertEqual(decision.update.recovery.no_progress_count, 0)
        self.assertEqual(
            decision.update.review.last_reply_fingerprint,
            blocker_fingerprint(current),
        )
        after = self.reducer.apply(self._snapshot(context), decision)
        self.assertEqual(after.context.recovery.no_progress_count, 0)
        self.assertEqual(
            after.context.review.last_reply_fingerprint,
            blocker_fingerprint(current),
        )

    def test_blocker_swap_does_not_reset_no_progress(self) -> None:
        # The count of blockers dipped 5->3->4->5 in live run
        # task-20260918-30ec74 and every dip reset the no-progress detector.
        # Set semantics: a swap (one resolved, one minted) is not progress.
        first = _finding("f-1")
        second = _finding("f-2", item_id="item-000002")
        third = _finding("f-3", item_id="item-000003")
        replacement = _finding("f-4", item_id="item-000004")

        self.assertFalse(
            revision_made_progress(
                blocker_fingerprint((first, second)), (second, third)
            )
        )
        self.assertFalse(
            revision_made_progress(blocker_fingerprint((first,)), (first,))
        )
        self.assertFalse(
            revision_made_progress(blocker_fingerprint((first,)), (first, second))
        )
        self.assertTrue(
            revision_made_progress(
                blocker_fingerprint((first, second)), (first,)
            )
        )
        # Legacy count-only fingerprints cannot be compared as sets and start
        # fair instead of misreading an old state as a stall.
        self.assertTrue(revision_made_progress("blockers=3", (first,)))

    def test_chronic_survivor_with_churn_exhausts_no_progress(self) -> None:
        # 30ec74 replay: one chronic blocker survives while churn swaps
        # satellites around it.  Under count semantics the dip reset the
        # detector; under set semantics the run stops burning rounds.
        chronic = _finding("f-1")
        previous = (
            chronic,
            _finding("f-2", item_id="item-000002"),
            _finding("f-3", item_id="item-000003"),
        )
        current = (chronic, _finding("f-4", item_id="item-000004"))
        context = self._context(
            current,
            fingerprint=blocker_fingerprint(previous),
            no_progress=2,
        )
        decision = self.state.handle(context, _event("REQUEST_SOLVER_REVISION"))

        self.assertNotEqual(decision.transition.action, "REQUEST_SOLVER_REVISION")
        self.assertEqual(
            decision.transition.reason_code, "ZHONGSHU_FREEZE_WITH_FOLLOWUPS"
        )


class ReplyBudgetWaveScopeTests(unittest.TestCase):
    """The reply-retry budget is per-wave: a clean join pays the debt.

    Regression: the counter only ever incremented, so a late-phase role
    inherited the retries an earlier role already spent (an Analyst that
    burned 2 of 3 left the Critic's first protocol slip nowhere to go).
    """

    def _context(self, reply_retry_count: int) -> WorkflowContext:
        return replace(
            _context(round_=0),
            recovery=RecoveryState(reply_retry_count=reply_retry_count),
        )

    def test_clean_join_resets_the_reply_retry_budget(self) -> None:
        context = self._context(reply_retry_count=2)

        decision = ZhongshuCriticState().handle(
            context, _event("REQUEST_SOLVER_REVISION")
        )

        self.assertEqual(decision.transition.action, "REQUEST_SOLVER_REVISION")
        self.assertEqual(decision.update.recovery.reply_retry_count, 0)

        after = LinearContextReducer().apply(
            WorkflowSnapshot("task-1", context, 0), decision
        )
        self.assertEqual(after.context.recovery.reply_retry_count, 0)

    def test_clean_join_resets_even_when_the_wave_asks_for_evidence(self) -> None:
        context = self._context(reply_retry_count=3)

        decision = ZhongshuCriticState().handle(
            context, _event("REQUEST_ANALYST_EVIDENCE")
        )

        self.assertEqual(decision.transition.action, "REQUEST_ANALYST_EVIDENCE")
        self.assertEqual(decision.update.recovery.reply_retry_count, 0)

    def test_a_reply_retry_within_a_wave_keeps_charging(self) -> None:
        # The FAIL path must not reset: the budget bounds re-asks of the
        # same wave, so the charge survives until the wave joins cleanly.
        context = self._context(reply_retry_count=1)
        event = DomainEvent(
            "FAIL",
            "task-1",
            4,
            {
                "retryable": True,
                "error_code": "NODE_TASK_REVIEW_RESULT_INVALID",
                "failure_id": "f-1",
            },
            "2026-09-13T00:00:00Z",
        )

        decision = ZhongshuCriticState().handle(context, event)

        self.assertEqual(decision.transition.action, "RETRY")
        self.assertEqual(decision.update.recovery.reply_retry_count, 2)


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
        # A *content* failure (the reply parsed, but its meaning was wrong) shares
        # the convergence budget.  Reply-shape failures have their own code (see
        # the reply-budget tests above), so this case must not use one of those.
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
                    "error_code": "SOLVER_CHANGE_OP_UNKNOWN",
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
