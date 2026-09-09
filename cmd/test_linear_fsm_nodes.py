from __future__ import annotations

import threading
import unittest
from dataclasses import FrozenInstanceError

from orchestrator.runtime.nodes import (
    ConcurrentNodeExecutor,
    NodeContext,
    NodeResult,
    ReviewNode,
    SequentialNodeExecutor,
    WorkerBinding,
    WorkerResult,
)
from orchestrator.domain.errors import InvariantViolation


def _binding(worker_id: str, request_id: str | None = None) -> WorkerBinding:
    return WorkerBinding(
        worker_id=worker_id,
        agent_id=f"agent-{worker_id}",
        task_id="task-1",
        request_id=request_id or f"request-{worker_id}",
        role="review-analyst",
        phase="ZHONGSHU",
    )


class _Runner:
    def __init__(self, failing: set[str] | None = None) -> None:
        self.failing = failing or set()
        self.calls: list[str] = []
        self.lock = threading.Lock()

    def run(self, binding: WorkerBinding, context: NodeContext) -> WorkerResult:
        with self.lock:
            self.calls.append(binding.worker_id)
        if binding.worker_id in self.failing:
            raise TimeoutError(f"worker {binding.worker_id} timed out")
        return WorkerResult(binding.worker_id, "SUCCEEDED", f"artifact-{binding.worker_id}")


class _Joiner:
    def __init__(self, failing: bool = False) -> None:
        self.failing = failing
        self.received: tuple[WorkerResult, ...] = ()

    def join(self, results: tuple[WorkerResult, ...]) -> NodeResult:
        self.received = tuple(results)
        if self.failing:
            raise ValueError("fan-in failed")
        return NodeResult("node-1", "SUCCEEDED", tuple(results), aggregate=[r.worker_id for r in results])


class NodeExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = NodeContext("task-1", "node-1", "revision-1", "group-1", None)
        self.node = ReviewNode(
            "node-1",
            "ZHONGSHU",
            (_binding("worker-b"), _binding("worker-a")),
        )

    def test_sequential_and_concurrent_have_same_deterministic_join_input(self) -> None:
        sequential_joiner = _Joiner()
        concurrent_joiner = _Joiner()
        sequential = SequentialNodeExecutor(_Runner(), sequential_joiner).execute(
            self.node, self.context
        )
        concurrent = ConcurrentNodeExecutor(
            _Runner(), concurrent_joiner, max_workers=2
        ).execute(self.node, self.context)

        self.assertEqual(sequential, concurrent)
        self.assertEqual(sequential.aggregate, ["worker-a", "worker-b"])

    def test_worker_failure_is_scoped_to_worker_and_join_still_runs(self) -> None:
        joiner = _Joiner()
        result = SequentialNodeExecutor(_Runner({"worker-b"}), joiner).execute(
            self.node, self.context
        )

        self.assertEqual(result.status, "SUCCEEDED")
        failed = result.worker_results[1]
        self.assertIsNotNone(failed.failure)
        assert failed.failure is not None
        self.assertEqual(failed.failure.stage, "node_worker")
        self.assertEqual(failed.failure.worker_id, "worker-b")
        self.assertEqual(failed.failure.node_run_id, "node-1")
        self.assertEqual(len(joiner.received), 2)

    def test_join_failure_is_not_misattributed_to_worker(self) -> None:
        result = ConcurrentNodeExecutor(
            _Runner(), _Joiner(failing=True), max_workers=2
        ).execute(self.node, self.context)

        self.assertEqual(result.status, "FAILED")
        self.assertIsNotNone(result.failure)
        assert result.failure is not None
        self.assertEqual(result.failure.stage, "node_join")
        self.assertIsNone(result.failure.worker_id)

    def test_node_context_is_immutable_and_empty_node_is_rejected(self) -> None:
        with self.assertRaises(FrozenInstanceError):
            self.context.task_id = "other"  # type: ignore[misc]
        empty = ReviewNode("node-1", "ZHONGSHU", ())
        with self.assertRaises(ValueError):
            SequentialNodeExecutor(_Runner(), _Joiner()).execute(empty, self.context)

    def test_worker_binding_scope_is_checked_before_execution(self) -> None:
        wrong_binding = WorkerBinding(
            "worker-a", "agent-worker-a", "other-task", "request-worker-a", "review-analyst", "ZHONGSHU"
        )
        wrong = ReviewNode("node-1", "ZHONGSHU", (wrong_binding,))
        runner = _Runner()

        with self.assertRaises(InvariantViolation):
            SequentialNodeExecutor(runner, _Joiner()).execute(wrong, self.context)
        self.assertEqual(runner.calls, [])


if __name__ == "__main__":
    unittest.main()
