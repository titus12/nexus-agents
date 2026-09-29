"""Per-worker reply re-ask + per-wave reply-debt clearing (2026-09-28).

Live run task-20260928-eaea40 died after 57 minutes on two mechanics: one
prose group reply failed the whole 4-group wave, and three earlier Solver doc
retries had already drained the reply budget so that single wave failure hit
the human gate without a retry.  A delivered-but-unusable reply is now re-asked
once with its rejection restated (targeted, per worker), and any clean dispatch
join pays the wave's reply debt.
"""

from __future__ import annotations

import unittest
from unittest import mock

from orchestrator.domain.context import (
    ProgressState,
    RecoveryState,
    TaskIdentity,
    WorkflowContext,
)
from orchestrator.domain.errors import (
    CONTRACT_REJECTED_EVENT,
    UNSTRUCTURED_REPLY_EVENT,
    FailureRecord,
)
from orchestrator.domain.events import DomainEvent
from orchestrator.domain.states import StateRegistry
from orchestrator.runtime.nodes import (
    NodeContext,
    WorkerBinding,
    WorkerResult,
    _rejected_reply,
    _run_worker,
)


def _binding() -> WorkerBinding:
    return WorkerBinding(
        worker_id="worker-01",
        agent_id="agent-1",
        task_id="task-1",
        request_id="req-1",
        role="review-critic",
        phase="ZHONGSHU",
        prompt_ref="Group review job: capsule body.",
    )


def _context() -> NodeContext:
    return NodeContext(
        task_id="task-1",
        node_run_id="node-1",
        revision_id="R1",
        state="ZHONGSHU_CRITIC",
        prompt_ref="node-level prompt",
    )


class _ScriptedRunner:
    """Returns scripted results in order; records the bindings it saw."""

    def __init__(self, *results: WorkerResult) -> None:
        self._results = list(results)
        self.bindings: list[WorkerBinding] = []

    def run(self, binding: WorkerBinding, context: NodeContext) -> WorkerResult:
        self.bindings.append(binding)
        return self._results.pop(0)


def _prose() -> WorkerResult:
    return WorkerResult(
        worker_id="worker-01",
        status="SUCCEEDED",
        payload_ref=None,
        result_payload={"action": UNSTRUCTURED_REPLY_EVENT},
    )


def _overflow() -> WorkerResult:
    """The platform placeholder for an output too large to post (task-20260928-21fe26)."""

    return WorkerResult(
        worker_id="worker-01",
        status="SUCCEEDED",
        payload_ref=None,
        result_payload={
            "action": UNSTRUCTURED_REPLY_EVENT,
            "raw_reply": (
                "This task completed, but its output was too large to post "
                "safely. The raw output was not posted."
            ),
        },
    )


def _contract_rejected(reason: str) -> WorkerResult:
    return WorkerResult(
        worker_id="worker-01",
        status="SUCCEEDED",
        payload_ref=None,
        result_payload={
            "action": CONTRACT_REJECTED_EVENT,
            "contract_rejection": reason,
        },
    )


def _good() -> WorkerResult:
    return WorkerResult(
        worker_id="worker-01",
        status="SUCCEEDED",
        payload_ref=None,
        result_payload={"action": "APPROVE_GROUP"},
    )


def _failed_reply() -> WorkerResult:
    return WorkerResult(
        worker_id="worker-01",
        status="FAILED",
        payload_ref=None,
        failure=FailureRecord(
            failure_id="f-1",
            stage="agent_reply",
            owner_component="agent_dispatch",
            task_id="task-1",
            state="ZHONGSHU_CRITIC",
            sequence=1,
            node_run_id="node-1",
            worker_id="worker-01",
            effect_id=None,
            error_code="AGENT_REPLY_UNSTRUCTURED",
            retryable=True,
            message="reply body was not usable",
            cause_type="AgentReply",
        ),
    )


class RejectedReplyTests(unittest.TestCase):
    def test_detects_both_shapes(self) -> None:
        self.assertIn("one complete JSON object", _rejected_reply(_prose()) or "")
        self.assertIn("role mode mismatch", _rejected_reply(_contract_rejected("role mode mismatch")) or "")
        self.assertIn("reply body was not usable", _rejected_reply(_failed_reply()) or "")
        self.assertIsNone(_rejected_reply(_good()))

    def test_oversized_output_placeholder_is_diagnosed_not_misread(self) -> None:
        rejection = _rejected_reply(_overflow())
        self.assertIn("too large", rejection or "")
        self.assertIn("result file", rejection or "")
        # The loss was the delivery channel, not JSON formatting.
        self.assertNotIn("one complete JSON object", rejection or "")


class WorkerReplyReAskTests(unittest.TestCase):
    def _run(self, runner, binding=None, context=None):
        with mock.patch("orchestrator.runtime.nodes._worker_retry_sleep"):
            return _run_worker(
                runner, binding or _binding(), context or _context()
            )

    def test_prose_reply_is_re_asked_with_feedback(self) -> None:
        runner = _ScriptedRunner(_prose(), _good())

        result = self._run(runner)

        self.assertEqual(result.result_payload, {"action": "APPROVE_GROUP"})
        self.assertEqual(len(runner.bindings), 2)
        retried = runner.bindings[1]
        self.assertIn("[Retry feedback]", retried.prompt_ref)
        self.assertIn("one complete JSON object", retried.prompt_ref)
        self.assertIn("Group review job: capsule body.", retried.prompt_ref)

    def test_contract_rejection_details_ride_the_feedback(self) -> None:
        runner = _ScriptedRunner(_contract_rejected("item_id expected string"), _good())

        self._run(runner)

        self.assertIn("item_id expected string", runner.bindings[1].prompt_ref)

    def test_oversized_output_re_ask_points_at_the_result_file(self) -> None:
        runner = _ScriptedRunner(_overflow(), _good())

        self._run(runner)

        retried = runner.bindings[1]
        self.assertIn("[Retry feedback]", retried.prompt_ref)
        self.assertIn("too large", retried.prompt_ref)
        self.assertIn("result file", retried.prompt_ref)

    def test_valid_reply_is_not_re_asked(self) -> None:
        runner = _ScriptedRunner(_good())

        self._run(runner)

        self.assertEqual(len(runner.bindings), 1)

    def test_re_ask_gives_up_after_one_attempt(self) -> None:
        runner = _ScriptedRunner(_prose(), _prose())

        result = self._run(runner)

        self.assertEqual(len(runner.bindings), 2)
        self.assertEqual(
            result.result_payload.get("action"), UNSTRUCTURED_REPLY_EVENT
        )


class ReplyDebtClearingTests(unittest.TestCase):
    """The reply budget is scoped to one dispatch wave."""

    def _context(self, reply_retry_count: int = 3) -> WorkflowContext:
        return WorkflowContext(
            identity=TaskIdentity("task-1", "issue-1", "proj", "req-1"),
            progression=ProgressState(
                "ZHONGSHU_ANALYST", 1, "2026-09-28T00:00:00Z"
            ),
            recovery=RecoveryState(reply_retry_count=reply_retry_count),
        )

    def test_single_dispatch_success_pays_the_debt(self) -> None:
        # Regression: Solver doc retries drained the budget and the Critic's
        # first wave failed straight to the human gate (task-20260928-eaea40).
        state = StateRegistry.default().get("ZHONGSHU_ANALYST")
        event = DomainEvent(
            name="EVIDENCE_PACKET_READY",
            task_id="task-1",
            sequence=1,
            payload={},
            occurred_at="2026-09-28T00:00:01Z",
            event_id="effect:dispatch:task-1:ZHONGSHU_ANALYST:1:SUCCEEDED",
        )

        decision = state._transition_decision(self._context(), event)

        self.assertEqual(decision.update.recovery.reply_retry_count, 0)

    def test_resume_does_not_clear_the_debt(self) -> None:
        state = StateRegistry.default().get("HUMAN_GATE")
        event = DomainEvent(
            name="RESUME",
            task_id="task-1",
            sequence=2,
            payload={},
            occurred_at="2026-09-28T00:00:01Z",
            event_id="resume:task-1:2",
        )

        decision = state._transition_decision(
            WorkflowContext(
                identity=TaskIdentity("task-1", "issue-1", "proj", "req-1"),
                progression=ProgressState(
                    "HUMAN_GATE", 2, "2026-09-28T00:00:00Z", "ZHONGSHU_SOLVER"
                ),
                recovery=RecoveryState(reply_retry_count=3),
            ),
            event,
        )

        self.assertIsNone(decision.update.recovery)


if __name__ == "__main__":
    unittest.main()
