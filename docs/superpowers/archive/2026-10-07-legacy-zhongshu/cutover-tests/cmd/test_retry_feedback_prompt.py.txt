from __future__ import annotations

import unittest
from dataclasses import replace

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

    def test_solver_shape_rejection_is_restated(self) -> None:
        # Live incident task-20260924-beb814: SOLVER_GROUP_DOC_MISSING was
        # re-asked four times over an empty feedback channel, so the worker
        # re-emitted the same unusable reply until the budget died.  A Solver
        # shape rejection must reach the re-ask like any other reply failure.
        failure = _failure(
            "SOLVER_GROUP_DOC_MISSING:all",
            message="SOLVER_GROUP_DOC_MISSING:all",
        )

        feedback = retry_feedback(self._context(failure), "ZHONGSHU_SOLVER")

        self.assertIn("Retry feedback", feedback)
        self.assertIn("SOLVER_GROUP_DOC_MISSING", feedback)

    def test_contract_rules_ride_the_prompt(self) -> None:
        # The contract's prompt_rules (including the nine-section group
        # document demand) were stored on the contract and never dispatched,
        # so the worker could not know group_docs was mandatory.
        prompt = build_prompt(self._context(None), target_state="ZHONGSHU_SOLVER")

        self.assertIn("[Contract rules]", prompt.content)
        self.assertIn("nine-section requirement document in group_docs", prompt.content)


class WorkerScopedFeedbackTests(unittest.TestCase):
    """A re-asked worker must see its own rejection, not a peer's.

    task-20260929-dad75d wave 4: the wave failure carried worker-02's error
    and all three re-asked analysts were told to fix worker-02's oversized
    quote while their own defects went unmentioned.
    """

    def _context(self, failure: FailureRecord | None) -> WorkflowContext:
        return WorkflowContext(
            identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
            progression=ProgressState("ZHONGSHU_ANALYST", 8, "2026-09-29T00:00:00Z"),
            recovery=RecoveryState(last_failure=failure),
        )

    def _wave_failure(self) -> FailureRecord:
        return FailureRecord(
            failure_id="node-1:NODE_UNSTRUCTURED_REPLY",
            stage="node_join",
            owner_component="agent_node_joiner",
            task_id="task-1",
            state="ZHONGSHU_ANALYST",
            sequence=8,
            node_run_id="node-1",
            worker_id="zhongshu_analyst-worker-02",
            effect_id=None,
            error_code="AGENT_REPLY_CONTRACT_REJECTED",
            retryable=True,
            message="ev-003 quote is 78 lines / 5210 chars but the cap is 30 / 4096",
            cause_type="WorkerResult",
            worker_rejections=(
                ("zhongshu_analyst-worker-02", "AGENT_REPLY_CONTRACT_REJECTED: ev-003 quote is 78 lines"),
                ("zhongshu_analyst-worker-01", "AGENT_REPLY_CONTRACT_REJECTED: ev-001 QUOTE_MISMATCH in app.py"),
            ),
        )

    def test_own_rejection_is_restated(self) -> None:
        feedback = retry_feedback(
            self._context(self._wave_failure()),
            "ZHONGSHU_ANALYST",
            worker_id="zhongshu_analyst-worker-01",
        )

        self.assertIn("ev-001 QUOTE_MISMATCH", feedback)
        self.assertNotIn("ev-003", feedback)

    def test_worker_without_rejection_gets_nothing(self) -> None:
        feedback = retry_feedback(
            self._context(self._wave_failure()),
            "ZHONGSHU_ANALYST",
            worker_id="zhongshu_analyst-worker-03",
        )

        self.assertEqual(feedback, "")

    def test_infrastructure_failure_of_a_worker_gives_no_feedback(self) -> None:
        failure = replace(
            self._wave_failure(),
            worker_rejections=(("zhongshu_analyst-worker-01", "AGENT_TIMEOUT: no result"),),
        )

        feedback = retry_feedback(
            self._context(failure),
            "ZHONGSHU_ANALYST",
            worker_id="zhongshu_analyst-worker-01",
        )

        self.assertEqual(feedback, "")

    def test_rejection_of_another_state_is_not_restated(self) -> None:
        failure = replace(self._wave_failure(), state="ZHONGSHU_CRITIC")

        feedback = retry_feedback(
            self._context(failure),
            "ZHONGSHU_ANALYST",
            worker_id="zhongshu_analyst-worker-01",
        )

        self.assertEqual(feedback, "")

    def test_ledger_absent_falls_back_to_the_wave_message(self) -> None:
        failure = _failure("AGENT_REPLY_UNSTRUCTURED", state="ZHONGSHU_ANALYST")

        feedback = retry_feedback(
            self._context(failure),
            "ZHONGSHU_ANALYST",
            worker_id="zhongshu_analyst-worker-01",
        )

        self.assertIn("Retry feedback", feedback)


if __name__ == "__main__":
    unittest.main()
