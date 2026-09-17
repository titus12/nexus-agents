from __future__ import annotations

import unittest

from orchestrator.domain.context import (
    ProgressState,
    RecoveryState,
    TaskIdentity,
    WorkflowContext,
)
from orchestrator.domain.errors import FailureRecord
from orchestrator.domain.policies.prompts import build_prompt, retry_feedback


def _failure(code: str, state: str = "ZHONGSHU_SOLVER", message: str = "boom") -> FailureRecord:
    return FailureRecord(
        failure_id="f-1",
        stage="agent_result",
        owner_component="agent_dispatch",
        task_id="task-1",
        state=state,
        sequence=8,
        node_run_id=None,
        worker_id=None,
        effect_id=None,
        error_code=code,
        retryable=True,
        message=message,
        cause_type="AgentReply",
    )


class RetryFeedbackTests(unittest.TestCase):
    """A re-ask must tell the agent what the validator refused."""

    def _context(self, failure: FailureRecord | None) -> WorkflowContext:
        return WorkflowContext(
            identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
            progression=ProgressState("ZHONGSHU_SOLVER", 8, "2026-09-16T00:00:00Z"),
            recovery=RecoveryState(last_failure=failure),
        )

    def test_no_failure_adds_nothing(self) -> None:
        prompt = build_prompt(self._context(None), target_state="ZHONGSHU_SOLVER")

        self.assertNotIn("Retry feedback", prompt.content)

    def test_reply_rejection_is_restated_in_the_prompt(self) -> None:
        failure = _failure(
            "AGENT_REPLY_CONTRACT_REJECTED",
            message=(
                "agent reply violated the role result contract: "
                "inline result role mode mismatch: expected=A actual=B"
            ),
        )

        prompt = build_prompt(self._context(failure), target_state="ZHONGSHU_SOLVER")

        self.assertIn("Retry feedback", prompt.content)
        self.assertIn("role mode mismatch: expected=A actual=B", prompt.content)
        self.assertIn("one complete structured result", prompt.content)

    def test_unstructured_reply_is_also_restated(self) -> None:
        failure = _failure("AGENT_REPLY_UNSTRUCTURED")

        self.assertIn("Retry feedback", retry_feedback(self._context(failure), "ZHONGSHU_SOLVER"))

    def test_a_different_state_failure_is_not_restated(self) -> None:
        # A critic slip must never be presented to the solver as its own error.
        failure = _failure("AGENT_REPLY_UNSTRUCTURED", state="ZHONGSHU_CRITIC")

        self.assertEqual(retry_feedback(self._context(failure), "ZHONGSHU_SOLVER"), "")

    def test_infrastructure_failure_is_not_restated(self) -> None:
        failure = _failure("AGENT_RESULT_MISSING", message="agent run produced no result")

        self.assertEqual(retry_feedback(self._context(failure), "ZHONGSHU_SOLVER"), "")


if __name__ == "__main__":
    unittest.main()
