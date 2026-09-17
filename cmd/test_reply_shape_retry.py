from __future__ import annotations

import unittest

from orchestrator.domain.context import (
    ProgressState,
    RecoveryState,
    ReviewState,
    TaskIdentity,
    WorkflowContext,
)
from orchestrator.domain.errors import is_reply_failure
from orchestrator.domain.events import DomainEvent
from orchestrator.domain.states import ZhongshuSolverState
from orchestrator.runtime.reducer import LinearContextReducer
from orchestrator.runtime.repository import WorkflowSnapshot


def _event(error_code: str) -> DomainEvent:
    return DomainEvent(
        "FAIL",
        "task-1",
        11,
        {
            "action": "FAIL",
            "retryable": True,
            "failure": {
                "failure_id": "solver-reply:11",
                "stage": "agent_reply",
                "owner_component": "ZHONGSHU_SOLVER",
                "task_id": "task-1",
                "state": "ZHONGSHU_SOLVER",
                "sequence": 11,
                "node_run_id": "node-1",
                "worker_id": None,
                "effect_id": None,
                "error_code": error_code,
                "retryable": True,
                "message": "reply body was not usable",
                "cause_type": "AgentReply",
            },
        },
        "2026-09-16T00:00:00Z",
    )


class ReplyFailureCodeTests(unittest.TestCase):
    def test_malformed_reply_is_a_reply_failure(self) -> None:
        self.assertTrue(is_reply_failure("AGENT_REPLY_UNSTRUCTURED"))
        self.assertTrue(is_reply_failure("agent_reply_unstructured"))

    def test_other_classes_are_not_reply_failures(self) -> None:
        for code in (
            "REMOTE_RUN_FAILED",
            "AGENT_RESULT_MISSING",
            "AGENT_TIMEOUT",
            "TASK_REVIEW_RESULT_INVALID",
            "",
            None,
        ):
            with self.subTest(code=code):
                self.assertFalse(is_reply_failure(code))


class UnusableSolverReplyRetryTests(unittest.TestCase):
    """A malformed Solver reply is re-asked on the reply budget.

    Regression: ``AGENT_REPLY_UNSTRUCTURED`` used to share the runtime budget,
    so once a provider outage had drained that budget a plain JSON syntax slip
    failed the whole run (observed as ``Expecting ':' delimiter`` at char 14521
    in a real Solver reply).
    """

    def setUp(self) -> None:
        self.state = ZhongshuSolverState()
        self.reducer = LinearContextReducer()

    def _context(
        self,
        *,
        reply: int = 0,
        external: int = 0,
        retry: int = 0,
    ) -> WorkflowContext:
        return WorkflowContext(
            identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
            progression=ProgressState("ZHONGSHU_SOLVER", 11, "2026-09-16T00:00:00Z"),
            recovery=RecoveryState(
                reply_retry_count=reply,
                max_reply_retries=3,
                external_retry_count=external,
                max_external_retries=3,
                retry_count=retry,
                max_retries=3,
            ),
            review=ReviewState(revision_id="rev-1", plan_hash="plan-hash"),
        )

    def _snapshot(self, context: WorkflowContext) -> WorkflowSnapshot:
        return WorkflowSnapshot("task-1", context, 0)

    def test_malformed_reply_uses_the_reply_budget(self) -> None:
        decision = self.state.handle(
            self._context(), _event("AGENT_REPLY_UNSTRUCTURED")
        )

        self.assertEqual(decision.transition.action, "RETRY")
        self.assertEqual(decision.update.recovery.reply_retry_count, 1)
        self.assertIsNone(decision.update.recovery.external_retry_count)
        self.assertIsNone(decision.update.recovery.retry_count)

    def test_malformed_reply_is_retried_even_when_the_runtime_budget_is_spent(
        self,
    ) -> None:
        decision = self.state.handle(
            self._context(reply=0, external=3, retry=0),
            _event("AGENT_REPLY_UNSTRUCTURED"),
        )

        self.assertEqual(decision.transition.action, "RETRY")
        self.assertEqual(decision.update.recovery.reply_retry_count, 1)
        self.assertIsNone(decision.update.recovery.external_retry_count)

    def test_malformed_reply_escalates_when_the_reply_budget_is_spent(self) -> None:
        context = self._context(reply=3, external=3, retry=0)
        decision = self.state.handle(
            context, _event("AGENT_REPLY_UNSTRUCTURED")
        )

        self.assertEqual(decision.transition.action, "HUMAN_GATE")
        after = self.reducer.apply(self._snapshot(context), decision)
        self.assertEqual(after.context.progression.state, "HUMAN_GATE")

    def test_a_runtime_failure_still_uses_the_runtime_budget(self) -> None:
        decision = self.state.handle(
            self._context(reply=3), _event("REMOTE_RUN_FAILED")
        )

        self.assertEqual(decision.transition.action, "RETRY")
        self.assertEqual(decision.update.recovery.external_retry_count, 1)
        self.assertIsNone(decision.update.recovery.reply_retry_count)


if __name__ == "__main__":
    unittest.main()
