from __future__ import annotations

import unittest

from orchestrator.domain.context import (
    ProgressState,
    RecoveryState,
    ReviewState,
    TaskIdentity,
    WorkflowContext,
)
from orchestrator.domain.errors import FailureRecord, is_infrastructure_failure
from orchestrator.domain.events import DomainEvent
from orchestrator.domain.states import ZhongshuCriticState
from orchestrator.runtime import nodes
from orchestrator.runtime.agent_effects import AgentNodeWorkerRunner, AgentWorkerRunner
from orchestrator.runtime.nodes import (
    ConcurrentNodeExecutor,
    NodeContext,
    NodeResult,
    ReviewNode,
    WorkerBinding,
    WorkerResult,
)
from orchestrator.runtime.ports import (
    AgentDispatchRequest,
    DispatchReceipt,
    PollRequest,
    RemoteRunStatus,
)
from orchestrator.transport.replies import RawTransportReply


def _failure(code: str, *, retryable: bool = True) -> FailureRecord:
    return FailureRecord(
        failure_id="f-1",
        stage="remote_run",
        owner_component="agent_dispatch",
        task_id="task-1",
        state="ZHONGSHU_CRITIC",
        sequence=6,
        node_run_id="node-1",
        worker_id="worker-a",
        effect_id=None,
        error_code=code,
        retryable=retryable,
        message="boom",
        cause_type="RemoteRun",
    )


class InfrastructureFailureCodeTests(unittest.TestCase):
    def test_runtime_codes_are_infrastructure(self) -> None:
        for code in (
            "REMOTE_RUN_FAILED",
            "AGENT_RESULT_MISSING",
            "AGENT_TIMEOUT",
            "transport_error",
        ):
            with self.subTest(code=code):
                self.assertTrue(is_infrastructure_failure(code))

    def test_a_delivered_but_unusable_reply_is_not_infrastructure(self) -> None:
        # The agent answered; only its document was malformed.  That is a reply
        # problem with its own budget, not a backend outage.
        self.assertFalse(is_infrastructure_failure("AGENT_REPLY_UNSTRUCTURED"))

    def test_content_codes_are_not_infrastructure(self) -> None:
        for code in ("TASK_REVIEW_RESULT_INVALID", "SOLVER_CHANGE_OP_UNKNOWN", "", None):
            with self.subTest(code=code):
                self.assertFalse(is_infrastructure_failure(code))


class InfrastructureRetryBudgetTests(unittest.TestCase):
    """A backend failure must not consume the plan-convergence retry budget."""

    def setUp(self) -> None:
        self.state = ZhongshuCriticState()

    def _context(self, *, external: int = 0, retry: int = 0) -> WorkflowContext:
        return WorkflowContext(
            identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
            progression=ProgressState("ZHONGSHU_CRITIC", 6, "2026-09-13T00:00:00Z"),
            recovery=RecoveryState(
                retry_count=retry,
                max_retries=3,
                external_retry_count=external,
                max_external_retries=3,
            ),
            review=ReviewState(revision_id="task-1:ZHONGSHU_ANALYST:2"),
        )

    def _event(self, code: str) -> DomainEvent:
        return DomainEvent(
            "FAIL",
            "task-1",
            6,
            {
                "action": "FAIL",
                "retryable": True,
                "failure": {
                    "failure_id": "f-1",
                    "stage": "remote_run",
                    "owner_component": "agent_dispatch",
                    "task_id": "task-1",
                    "state": "ZHONGSHU_CRITIC",
                    "sequence": 6,
                    "node_run_id": "node-1",
                    "worker_id": "worker-a",
                    "effect_id": None,
                    "error_code": code,
                    "retryable": True,
                    "message": "boom",
                    "cause_type": "RemoteRun",
                },
            },
            "2026-09-13T00:00:00Z",
        )

    def test_backend_failure_uses_the_external_budget(self) -> None:
        decision = self.state.handle(
            self._context(external=0), self._event("REMOTE_RUN_FAILED")
        )

        self.assertEqual(decision.transition.action, "RETRY")
        self.assertEqual(decision.update.recovery.external_retry_count, 1)
        self.assertIsNone(decision.update.recovery.retry_count)

    def test_content_failure_still_uses_the_convergence_budget(self) -> None:
        decision = self.state.handle(
            self._context(retry=0), self._event("SOLVER_CHANGE_OP_UNKNOWN")
        )

        self.assertEqual(decision.transition.action, "RETRY")
        self.assertEqual(decision.update.recovery.retry_count, 1)
        self.assertIsNone(decision.update.recovery.external_retry_count)

    def test_exhausted_external_budget_fails_the_task(self) -> None:
        decision = self.state.handle(
            self._context(external=3, retry=0), self._event("REMOTE_RUN_FAILED")
        )

        self.assertEqual(decision.transition.action, "FAIL")
        self.assertIsNone(decision.update.recovery.external_retry_count)
        self.assertIsNone(decision.update.recovery.retry_count)

    def test_backend_failure_survives_an_exhausted_convergence_budget(self) -> None:
        decision = self.state.handle(
            self._context(retry=3), self._event("AGENT_RESULT_MISSING")
        )

        self.assertEqual(decision.transition.action, "RETRY")
        self.assertEqual(decision.update.recovery.external_retry_count, 1)


class _FlakyRunner:
    """Fails the first N calls per worker, then succeeds."""

    def __init__(self, failures: list[bool], code: str, *, retryable: bool = True) -> None:
        self._failures = list(failures)
        self._code = code
        self._retryable = retryable
        self.calls = 0
        self.bindings: list[WorkerBinding] = []

    def run(self, binding: WorkerBinding, context: NodeContext) -> WorkerResult:
        self.bindings.append(binding)
        index = self.calls
        self.calls += 1
        if index < len(self._failures) and self._failures[index]:
            return WorkerResult(
                binding.worker_id,
                "FAILED",
                None,
                _failure(self._code, retryable=self._retryable),
            )
        return WorkerResult(binding.worker_id, "SUCCEEDED", "artifact-1")


class _Joiner:
    def join(self, results: tuple[WorkerResult, ...]) -> NodeResult:
        return NodeResult("node-1", "SUCCEEDED", tuple(results))


class NodeWorkerRetryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._real_sleep = nodes._worker_retry_sleep
        nodes._worker_retry_sleep = lambda _seconds: None
        self.addCleanup(lambda: setattr(nodes, "_worker_retry_sleep", self._real_sleep))
        self.context = NodeContext(
            "task-1", "node-1", "revision-1", "group-1", None, "ZHONGSHU_CRITIC", 6
        )
        self.node = ReviewNode(
            "node-1",
            "ZHONGSHU",
            (
                WorkerBinding(
                    worker_id="worker-a",
                    agent_id="agent-a",
                    task_id="task-1",
                    request_id="request-a",
                    role="review-critic",
                    phase="ZHONGSHU",
                ),
            ),
        )

    def _execute(self, runner: _FlakyRunner) -> WorkerResult:
        executor = ConcurrentNodeExecutor(runner, _Joiner(), max_workers=1)
        result = executor.execute(self.node, self.context)
        return result.worker_results[0]

    def test_transient_backend_failure_is_retried_on_the_same_binding(self) -> None:
        runner = _FlakyRunner([True, False], "REMOTE_RUN_FAILED")

        worker_result = self._execute(runner)

        self.assertEqual(runner.calls, 2)
        self.assertEqual(worker_result.status, "SUCCEEDED")

    def test_missing_result_is_retried_on_the_same_binding(self) -> None:
        runner = _FlakyRunner([True, False], "AGENT_RESULT_MISSING")

        worker_result = self._execute(runner)

        self.assertEqual(runner.calls, 2)
        self.assertEqual(worker_result.status, "SUCCEEDED")

    def test_content_failure_is_not_retried(self) -> None:
        runner = _FlakyRunner([True, False], "REPLY_VALIDATION_ERROR")

        worker_result = self._execute(runner)

        self.assertEqual(runner.calls, 1)
        self.assertEqual(worker_result.status, "FAILED")

    def test_non_retryable_backend_failure_is_not_retried(self) -> None:
        runner = _FlakyRunner([True, False], "REMOTE_RUN_FAILED", retryable=False)

        worker_result = self._execute(runner)

        self.assertEqual(runner.calls, 1)
        self.assertEqual(worker_result.status, "FAILED")

    def test_repeated_backend_failure_stops_at_the_attempt_cap(self) -> None:
        runner = _FlakyRunner([True, True, True, True], "REMOTE_RUN_FAILED")

        worker_result = self._execute(runner)

        self.assertEqual(runner.calls, nodes.INFRASTRUCTURE_WORKER_ATTEMPTS)
        self.assertEqual(worker_result.status, "FAILED")

    def test_retry_carries_a_distinct_attempt_identity(self) -> None:
        runner = _FlakyRunner([True, False], "AGENT_RESULT_MISSING")

        self._execute(runner)

        first, second = runner.bindings
        self.assertEqual(first.attempt, 1)
        self.assertEqual(second.attempt, 2)
        # The worker identity must stay stable so fan-in still matches, while
        # the attempt tells the runner to scope its dispatch identity.
        self.assertEqual(second.worker_id, first.worker_id)
        self.assertEqual(second.request_id, first.request_id)


class _DrainingTransport:
    """A transport whose first run ends without a correlated reply.

    This mirrors the production idempotency behaviour: ``find_existing`` returns
    the receipt of an already-dispatched request, so a caller that reuses the
    same request id can never start a second run -- it can only re-poll the
    finished one, which is how a per-binding retry used to burn a whole result
    window and then fail with the same error.
    """

    def __init__(self, *, fail_first: bool = True) -> None:
        self.fail_first = fail_first
        self.dispatched_request_ids: list[str] = []
        self._receipts: dict[str, DispatchReceipt] = {}
        self._replies: dict[str, list[RawTransportReply]] = {}

    def dispatch(self, request: AgentDispatchRequest) -> DispatchReceipt:
        self.dispatched_request_ids.append(request.request_id)
        run_id = f"run-{len(self.dispatched_request_ids)}"
        receipt = DispatchReceipt(
            operation_id=run_id,
            external_message_id=f"msg-{run_id}",
            confirmed=True,
            request_id=request.request_id,
            issue_id=f"issue-{run_id}",
        )
        self._receipts[request.idempotency_key] = receipt
        drained = self.fail_first and len(self.dispatched_request_ids) == 1
        self._replies[request.request_id] = [] if drained else [self._reply(request)]
        return receipt

    def find_existing(self, request: AgentDispatchRequest) -> DispatchReceipt | None:
        return self._receipts.get(request.idempotency_key)

    def poll(self, request: PollRequest) -> tuple[RawTransportReply, ...]:
        return tuple(self._replies.get(request.request_id, ()))

    def status(self, request: PollRequest) -> RemoteRunStatus:
        return RemoteRunStatus(
            request_id=request.request_id,
            operation_id=request.operation_id,
            status="COMPLETED",
        )

    def lookup(self, operation_id: str) -> DispatchReceipt | None:
        return None

    @staticmethod
    def _reply(request: AgentDispatchRequest) -> RawTransportReply:
        return RawTransportReply(
            author_id=request.agent_id,
            external_message_id="msg-reply",
            request_id=request.request_id,
            payload={"action": "TASK_APPROVED", "item_id": "item-1"},
            received_at="",
            source="fake",
        )


class WorkerRetryReDispatchTests(unittest.TestCase):
    """A worker retry must re-ask the agent, not re-read the drained run."""

    def setUp(self) -> None:
        self._real_sleep = nodes._worker_retry_sleep
        nodes._worker_retry_sleep = lambda _seconds: None
        self.addCleanup(lambda: setattr(nodes, "_worker_retry_sleep", self._real_sleep))
        self.context = NodeContext(
            "task-1",
            "node:task-1:ZHONGSHU_CRITIC:6",
            "revision-1",
            "group-1",
            None,
            "ZHONGSHU_CRITIC",
            6,
        )
        self.node = ReviewNode(
            "node:task-1:ZHONGSHU_CRITIC:6",
            "ZHONGSHU",
            (
                WorkerBinding(
                    worker_id="zhongshu_critic-worker-01",
                    agent_id="agent-a",
                    task_id="task-1",
                    request_id="task-1:ZHONGSHU_CRITIC:6:worker-01",
                    role="review-critic",
                    phase="ZHONGSHU",
                ),
            ),
        )

    def _execute(self, transport: _DrainingTransport) -> WorkerResult:
        runner = AgentNodeWorkerRunner(
            AgentWorkerRunner(
                transport,
                poll_interval=0.0,
                max_polls=1,
                timeout_seconds=5.0,
            )
        )
        executor = ConcurrentNodeExecutor(runner, _Joiner(), max_workers=1)
        return executor.execute(self.node, self.context).worker_results[0]

    def test_retry_dispatches_a_new_run_instead_of_repolling(self) -> None:
        transport = _DrainingTransport()

        worker_result = self._execute(transport)

        self.assertEqual(
            transport.dispatched_request_ids,
            [
                "task-1:ZHONGSHU_CRITIC:6:worker-01",
                "task-1:ZHONGSHU_CRITIC:6:worker-01:attempt-2",
            ],
        )
        self.assertEqual(worker_result.status, "SUCCEEDED")
        self.assertEqual(worker_result.worker_id, "zhongshu_critic-worker-01")

    def test_first_attempt_keeps_the_original_request_id(self) -> None:
        transport = _DrainingTransport(fail_first=False)

        worker_result = self._execute(transport)

        self.assertEqual(
            transport.dispatched_request_ids,
            ["task-1:ZHONGSHU_CRITIC:6:worker-01"],
        )
        self.assertEqual(worker_result.status, "SUCCEEDED")


if __name__ == "__main__":
    unittest.main()
