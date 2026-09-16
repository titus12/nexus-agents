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
from orchestrator.runtime.nodes import (
    ConcurrentNodeExecutor,
    NodeContext,
    NodeResult,
    ReviewNode,
    WorkerBinding,
    WorkerResult,
)


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
            "AGENT_REPLY_UNSTRUCTURED",
            "transport_error",
        ):
            with self.subTest(code=code):
                self.assertTrue(is_infrastructure_failure(code))

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

    def run(self, binding: WorkerBinding, context: NodeContext) -> WorkerResult:
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


if __name__ == "__main__":
    unittest.main()
