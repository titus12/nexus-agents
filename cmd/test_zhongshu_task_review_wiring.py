from __future__ import annotations

from dataclasses import replace
from unittest import mock
import unittest

from orchestrator.domain.decisions import EffectRequest
from orchestrator.domain.errors import FailureRecord
from orchestrator.runtime.agent_effects import (
    AgentNodeJoiner,
    AgentNodeWorkerRunner,
    AgentWorkerRunner,
)
from orchestrator.runtime.effects import EffectOutcome
from orchestrator.runtime.node_effects import NodeEffectRunner
from orchestrator.runtime.nodes import (
    NodeContext,
    NodeResult,
    ReviewNode,
    WorkerBinding,
    WorkerResult,
)
from orchestrator.zhongshu_review_queue import queue_from_dispatch_contexts


class _CapturingRunner:
    def __init__(self) -> None:
        self.requests: list = []

    def run_once(self, request):
        self.requests.append(request)
        return EffectOutcome(
            status="SUCCEEDED",
            event_payload={"action": "TASK_APPROVED", "result_artifact_id": "art-1"},
        )


def _binding(index: int) -> WorkerBinding:
    return WorkerBinding(
        worker_id=f"zhongshu_critic-worker-{index:02d}",
        agent_id=f"zhongshu-critic-{index:02d}",
        task_id="task-1",
        request_id=f"task-1:ZHONGSHU_CRITIC:3:worker-{index:02d}",
        role="review-critic",
        phase="ZHONGSHU",
        group_id=f"group-{index:02d}",
        item_id=f"item-{index:02d}",
        prompt_ref=f"review item-{index:02d}",
        dispatch_context={
            "zhongshu_dispatch_mode": "task_review",
            "review_job_id": f"zhongshu:rev-1:group-{index:02d}:item-{index:02d}",
            "group_id": f"group-{index:02d}",
            "item_id": f"item-{index:02d}",
            "task_hash": f"task-hash-{index:02d}",
            "dependency_hash": f"dep-hash-{index:02d}",
        },
    )


class TaskReviewWorkerBindingTests(unittest.TestCase):
    def test_worker_uses_per_binding_scope_prompt_and_context(self) -> None:
        runner = _CapturingRunner()
        context = NodeContext(
            task_id="task-1",
            node_run_id="node:1",
            revision_id="rev-1",
            state="ZHONGSHU_CRITIC",
            sequence=3,
            plan_hash="plan-hash",
            dispatch_mode="task_review",
        )
        AgentNodeWorkerRunner(runner).run(_binding(2), context)

        self.assertEqual(len(runner.requests), 1)
        payload = runner.requests[0].payload
        self.assertEqual(payload["group_id"], "group-02")
        self.assertEqual(payload["item_id"], "item-02")
        self.assertEqual(payload["prompt_ref"], "review item-02")
        self.assertEqual(payload["dispatch_context"]["review_job_id"], "zhongshu:rev-1:group-02:item-02")


class TaskReviewJoinerTests(unittest.TestCase):
    def _contexts(self) -> list[dict]:
        return [
            {
                "zhongshu_dispatch_mode": "task_review",
                "review_job_id": f"zhongshu:rev-1:group-{index:02d}:item-{index:02d}",
                "revision_id": "rev-1",
                "group_id": f"group-{index:02d}",
                "item_id": f"item-{index:02d}",
                "task_hash": f"task-hash-{index:02d}",
                "dependency_hash": f"dep-hash-{index:02d}",
            }
            for index in (1, 2, 3)
        ]

    def _result(self, index: int, action: str, findings: list) -> WorkerResult:
        return WorkerResult(
            f"zhongshu_critic-worker-{index:02d}",
            "SUCCEEDED",
            None,
            result_payload={
                "action": action,
                "revision_id": "rev-1",
                "plan_hash": "plan-hash",
                "reviewed_plan_hash": "plan-hash",
                "group_id": f"group-{index:02d}",
                "item_id": f"item-{index:02d}",
                "reviewed_task_hash": f"task-hash-{index:02d}",
                "reviewed_dependency_hash": f"dep-hash-{index:02d}",
                "worker_id": f"zhongshu_critic-worker-{index:02d}",
                "findings": findings,
                "review_checks": {},
            },
        )

    def test_all_approved_maps_to_approve_critic(self) -> None:
        queue = queue_from_dispatch_contexts(self._contexts(), "rev-1", "plan-hash")
        joiner = AgentNodeJoiner(
            "node:1",
            task_id="task-1",
            state="ZHONGSHU_CRITIC",
            revision_id="rev-1",
            plan_hash="plan-hash",
            task_review_queue=queue,
        )
        results = tuple(self._result(i, "TASK_APPROVED", []) for i in (1, 2, 3))
        joined = joiner.join(results)
        self.assertEqual(joined.status, "SUCCEEDED")
        self.assertEqual(joined.aggregate["action"], "APPROVE_CRITIC")
        self.assertTrue(joined.aggregate["task_review_complete"])

    def test_task_change_requires_solver_revision_with_item(self) -> None:
        queue = queue_from_dispatch_contexts(self._contexts(), "rev-1", "plan-hash")
        joiner = AgentNodeJoiner(
            "node:1",
            task_id="task-1",
            state="ZHONGSHU_CRITIC",
            revision_id="rev-1",
            plan_hash="plan-hash",
            task_review_queue=queue,
        )
        results = (
            self._result(1, "TASK_APPROVED", []),
            self._result(2, "TASK_CHANGES_REQUIRED", [
                {"finding_id": "f-1", "severity": "P0", "claim": "boundary unclear"},
            ]),
            self._result(3, "TASK_APPROVED", []),
        )
        joined = joiner.join(results)
        self.assertEqual(joined.aggregate["action"], "REQUEST_SOLVER_REVISION")
        self.assertEqual(joined.aggregate["affected_item_ids"], ["item-02"])
        self.assertEqual(joined.aggregate["findings"][0]["item_id"], "item-02")


class PartialTaskReviewTests(unittest.TestCase):
    def _queue(self):
        contexts = [
            {
                "zhongshu_dispatch_mode": "task_review",
                "review_job_id": f"zhongshu:rev-1:group-{i:02d}:item-{i:02d}",
                "revision_id": "rev-1",
                "group_id": f"group-{i:02d}",
                "item_id": f"item-{i:02d}",
                "task_hash": f"task-hash-{i:02d}",
                "dependency_hash": f"dep-hash-{i:02d}",
            }
            for i in (1, 2, 3)
        ]
        return queue_from_dispatch_contexts(contexts, "rev-1", "plan-hash")

    def _review(self, index: int, action: str) -> WorkerResult:
        return WorkerResult(
            f"zhongshu_critic-worker-{index:02d}",
            "SUCCEEDED",
            None,
            result_payload={
                "action": action,
                "item_id": f"item-{index:02d}",
                "reviewed_task_hash": f"task-hash-{index:02d}",
                "reviewed_dependency_hash": f"dep-hash-{index:02d}",
            },
        )

    def _joiner(self) -> AgentNodeJoiner:
        return AgentNodeJoiner(
            "node:1",
            task_id="task-1",
            state="ZHONGSHU_CRITIC",
            revision_id="rev-1",
            plan_hash="plan-hash",
            task_review_queue=self._queue(),
        )

    def test_partial_failure_carries_successful_verdicts(self) -> None:
        results = (
            self._review(1, "TASK_APPROVED"),
            self._review(2, "TASK_CHANGES_REQUIRED"),
            WorkerResult("zhongshu_critic-worker-03", "FAILED", None),
        )
        joined = self._joiner().join(results)
        self.assertEqual(joined.status, "FAILED")
        reviews = joined.aggregate["task_reviews"]
        self.assertEqual(
            {entry["item_id"] for entry in reviews}, {"item-01", "item-02"}
        )
        self.assertEqual(
            {entry["action"] for entry in reviews},
            {"TASK_APPROVED", "TASK_CHANGES_REQUIRED"},
        )

    def test_fanin_rejection_carries_partial_verdicts(self) -> None:
        joiner = self._joiner()
        results = (
            self._review(1, "TASK_APPROVED"),
            self._review(2, "TASK_CHANGES_REQUIRED"),
        )
        with mock.patch(
            "orchestrator.zhongshu_review.aggregate_task_review_results",
            side_effect=ValueError("fan-in rejected"),
        ):
            joined = joiner.join(results)

        self.assertEqual(joined.status, "FAILED")
        self.assertEqual(
            {entry["item_id"] for entry in joined.aggregate["task_reviews"]},
            {"item-01", "item-02"},
        )

    def test_failed_event_payload_carries_partial_verdicts(self) -> None:
        executor = _FailingExecutor()
        runner = NodeEffectRunner(executor=executor)
        outcome = runner.run_once(
            EffectRequest(
                effect_id="node:1",
                effect_type="node_dispatch",
                task_id="task-1",
                idempotency_key="node:1",
                payload={
                    "node_run_id": "node:1",
                    "phase": "ZHONGSHU",
                    "state": "ZHONGSHU_CRITIC",
                    "bindings": [
                        {
                            "worker_id": "zhongshu_critic-worker-01",
                            "agent_id": "c1",
                            "task_id": "task-1",
                            "request_id": "r1",
                            "role": "review-critic",
                            "phase": "ZHONGSHU",
                        }
                    ],
                },
            )
        )
        self.assertEqual(outcome.status, "FAILED")
        self.assertEqual(outcome.event_payload["node_run_id"], "node:1")
        self.assertEqual(
            outcome.event_payload["task_reviews"],
            [{"item_id": "item-01", "action": "TASK_APPROVED"}],
        )


class _FailingExecutor:
    def execute(self, node, context) -> NodeResult:
        return NodeResult(
            node.node_run_id,
            "FAILED",
            (),
            aggregate={
                "action": "FAIL",
                "task_reviews": [{"item_id": "item-01", "action": "TASK_APPROVED"}],
            },
            failure=FailureRecord(
                failure_id="node:1:w",
                stage="node_join",
                owner_component="test",
                task_id="task-1",
                state="ZHONGSHU_CRITIC",
                sequence=1,
                node_run_id="node:1",
                worker_id="zhongshu_critic-worker-01",
                effect_id=None,
                error_code="NODE_WORKER_FAILED",
                retryable=True,
                message="one worker failed",
                cause_type="WorkerResult",
            ),
        )


class ChildIssueFanoutTests(unittest.TestCase):
    def _runner(self, ids: str) -> AgentWorkerRunner:
        return AgentWorkerRunner(object(), agent_ids={"ZHONGSHU_ANALYST": ids})

    def test_single_identity_with_child_issues_fans_out(self) -> None:
        bindings = tuple(
            replace(_binding(index), issue_id=f"child-{index:02d}")
            for index in (1, 2, 3)
        )
        width = self._runner("analyst-1").parallel_width(
            "ZHONGSHU_ANALYST", bindings, "parent-1"
        )
        self.assertEqual(width, 3)

    def test_single_identity_without_child_issues_stays_serial(self) -> None:
        bindings = tuple(_binding(index) for index in (1, 2, 3))
        width = self._runner("analyst-1").parallel_width(
            "ZHONGSHU_ANALYST", bindings, "parent-1"
        )
        self.assertEqual(width, 1)

    def test_resolve_worker_agent_maps_by_index(self) -> None:
        runner = self._runner("a1,a2")
        self.assertEqual(
            runner.resolve_worker_agent("ZHONGSHU_ANALYST", "zhongshu_critic-worker-01"),
            "a1",
        )
        self.assertEqual(
            runner.resolve_worker_agent("ZHONGSHU_ANALYST", "zhongshu_critic-worker-02"),
            "a2",
        )

    def test_worker_dispatches_on_binding_child_issue(self) -> None:
        runner = _CapturingRunner()
        context = NodeContext(
            task_id="task-1",
            node_run_id="node:1",
            revision_id="rev-1",
            state="ZHONGSHU_ANALYST",
            issue_id="parent-1",
        )
        AgentNodeWorkerRunner(runner).run(
            replace(_binding(1), issue_id="child-01"), context
        )
        self.assertEqual(runner.requests[0].payload["issue_id"], "child-01")

    def test_worker_falls_back_to_context_issue(self) -> None:
        runner = _CapturingRunner()
        context = NodeContext(
            task_id="task-1",
            node_run_id="node:1",
            revision_id="rev-1",
            state="ZHONGSHU_ANALYST",
            issue_id="parent-1",
        )
        AgentNodeWorkerRunner(runner).run(_binding(1), context)
        self.assertEqual(runner.requests[0].payload["issue_id"], "parent-1")


class ParallelWidthTests(unittest.TestCase):
    def _runner(self, critic_ids: str) -> AgentWorkerRunner:
        return AgentWorkerRunner(object(), agent_ids={"ZHONGSHU_CRITIC": critic_ids})

    def _bindings(self, count: int):
        return tuple(_binding(index) for index in range(1, count + 1))

    def test_single_identity_serializes(self) -> None:
        width = self._runner("critic-1").parallel_width(
            "ZHONGSHU_CRITIC", self._bindings(6), "issue-1"
        )
        self.assertEqual(width, 1)

    def test_pool_runs_in_waves(self) -> None:
        width = self._runner("c1,c2,c3").parallel_width(
            "ZHONGSHU_CRITIC", self._bindings(14), "issue-1"
        )
        self.assertEqual(width, 3)

    def test_pool_larger_than_workers_is_capped(self) -> None:
        width = self._runner("c1,c2,c3,c4,c5,c6").parallel_width(
            "ZHONGSHU_CRITIC", self._bindings(4), "issue-1"
        )
        self.assertEqual(width, 4)

    def test_unconfigured_pool_uses_binding_identities(self) -> None:
        width = self._runner("").parallel_width(
            "ZHONGSHU_CRITIC", self._bindings(6), "issue-1"
        )
        self.assertEqual(width, 6)

    def test_agent_pool_summary_counts_identities(self) -> None:
        self.assertEqual(
            self._runner("c1,c2").agent_pool_summary(), {"ZHONGSHU_CRITIC": 2}
        )


if __name__ == "__main__":
    unittest.main()
