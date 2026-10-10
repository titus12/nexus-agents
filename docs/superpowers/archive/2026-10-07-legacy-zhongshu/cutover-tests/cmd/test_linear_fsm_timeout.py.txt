from __future__ import annotations

import tempfile
import threading
import time
import unittest

from orchestrator.adapters import FakeMulticaAdapter
from orchestrator.app import OrchestratorApp
from orchestrator.domain.context import (
    HumanGateState,
    ProgressState,
    RecoveryState,
    TaskIdentity,
    WorkflowContext,
)
from orchestrator.domain.states import ZhongshuAnalystState


def _context(state: str, *, entered_at: str = "2026-09-15T00:00:00Z", **review):
    return WorkflowContext(
        identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
        progression=ProgressState(state, 3, entered_at),
        **review,
    )


class StateTimeoutBudgetTests(unittest.TestCase):
    """A stalled agent is retried on the timeout budget, then failed."""

    def test_timeout_retries_while_the_budget_lasts(self) -> None:
        context = _context(
            "ZHONGSHU_ANALYST",
            recovery=RecoveryState(timeout_retry_count=1, max_timeout_retries=3),
        )

        decision = ZhongshuAnalystState().on_timeout(context)

        self.assertEqual(decision.transition.action, "RETRY")
        self.assertEqual(decision.transition.reason_code, "AGENT_TIMEOUT")
        self.assertEqual(decision.update.recovery.timeout_retry_count, 2)
        self.assertEqual(
            decision.update.recovery.last_failure.error_code, "AGENT_TIMEOUT"
        )
        self.assertEqual(decision.update.progression.resume_state, "ZHONGSHU_ANALYST")

    def test_timeout_fails_once_the_budget_is_spent(self) -> None:
        context = _context(
            "ZHONGSHU_ANALYST",
            recovery=RecoveryState(timeout_retry_count=3, max_timeout_retries=3),
        )

        decision = ZhongshuAnalystState().on_timeout(context)

        self.assertEqual(decision.transition.action, "FAIL")
        self.assertFalse(decision.update.recovery.last_failure.retryable)


class HumanWaitIsNeverATimeoutTests(unittest.TestCase):
    """An idle clock must not turn waiting for a person into a failure."""

    def test_human_gate_is_exempt_from_the_idle_deadline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(
                "HUMAN_GATE",
                human_gate=HumanGateState(
                    decision_id="task-1:human-gate:4",
                    reason_code="ZHONGSHU_STUCK_FINDING",
                    resume_state="ZHONGSHU_ANALYST",
                ),
            )
            app = OrchestratorApp(
                context,
                root=directory,
                multica=FakeMulticaAdapter(),
                poll_interval=0.01,
                timeout_seconds=1,
            )
            published: list[str] = []
            real_publish = app.inbox.publish

            def record(event):
                published.append(event.name)
                return real_publish(event)

            app.inbox.publish = record
            worker = threading.Thread(target=app.run, daemon=True)
            worker.start()
            try:
                time.sleep(1.5)
                still_waiting = worker.is_alive()
            finally:
                app.cancel()
                worker.join(2)
            final_state = app.repository.load("task-1").context.progression.state

        self.assertTrue(still_waiting)
        self.assertNotIn("TIMEOUT", published)
        self.assertEqual(final_state, "CANCELLED")


if __name__ == "__main__":
    unittest.main()
