import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, r"D:\workspace\src\nexus-agents\cmd")

from orchestrator.concurrency import ConcurrencyAdmission, ConcurrencyLimits
from orchestrator.lifecycle import LifecyclePaths
from orchestrator.models import AgentRequest, DispatchReceipt, ExternalMessage
from orchestrator.parallel_runtime import (
    ParallelCoordinatorDriver,
    ParallelWorker,
    external_target_parallelism,
)


class ParallelRuntimeCorrelationTests(unittest.TestCase):
    def test_shared_external_target_is_serialized(self):
        requests = [
            AgentRequest(
                task_id="task-1",
                request_id=f"req-{index}",
                agent_id="agent-1",
                role="review-analyst",
                phase="ZHONGSHU_ANALYST",
                prompt="work",
                idempotency_key=f"key-{index}",
                issue_id="issue-1",
            )
            for index in (1, 2)
        ]
        workers = [
            ParallelWorker(f"worker-{index}", "ZHONGSHU_ANALYST", "review-analyst", request)
            for index, request in enumerate(requests, start=1)
        ]
        self.assertEqual(external_target_parallelism(workers), 1)
        seen = []
        with tempfile.TemporaryDirectory() as temp:
            lifecycle = LifecyclePaths(Path(temp) / "task")
            lifecycle.ensure()

            def dispatch(request):
                seen.append(("dispatch", request.request_id))
                return DispatchReceipt("op", f"trigger-{request.request_id}")

            def poll(request):
                seen.append(("poll", request.request_id))
                return [ExternalMessage(
                    "agent-1",
                    {
                        "action": "READY_FOR_SOLVER",
                        "request_id": request.request_id,
                        "phase": request.phase,
                    },
                    request.dispatch_external_message_id,
                )]

            driver = ParallelCoordinatorDriver(
                task_id="task-1",
                lifecycle=lifecycle,
                admission=ConcurrencyAdmission(ConcurrencyLimits(global_max=6, per_task_max=6)),
                dispatch=dispatch,
                poll=poll,
                poll_interval=0,
                timeout_seconds=1,
            )
            result = driver.run(
                phase="ZHONGSHU_ANALYST",
                revision_id="revision-1",
                workers=workers,
                fan_in=lambda values: {"action": "READY_FOR_SOLVER"},
                max_workers=external_target_parallelism(workers),
            )
        self.assertEqual(len(result.completed), 2)
        self.assertEqual([kind for kind, _request_id in seen], [
            "dispatch", "poll", "dispatch", "poll",
        ])

        workers[1] = ParallelWorker(
            "worker-2",
            "ZHONGSHU_ANALYST",
            "review-analyst",
            replace(requests[1], agent_id="agent-2"),
        )
        self.assertEqual(external_target_parallelism(workers), 2)

    def test_dispatch_receipt_is_used_for_poll_and_attempt_isolated(self):
        with tempfile.TemporaryDirectory() as temp:
            lifecycle = LifecyclePaths(Path(temp) / "task")
            lifecycle.ensure()
            seen = []

            def dispatch(request):
                seen.append(("dispatch", request.request_id, request.idempotency_key))
                return DispatchReceipt("op", f"trigger-{request.request_id}")

            def poll(request):
                seen.append(("poll", request.request_id, request.dispatch_external_message_id))
                return [
                    ExternalMessage(
                        "agent-1",
                        {
                            "action": "READY_FOR_SOLVER",
                            "request_id": request.request_id,
                            "phase": request.phase,
                        },
                        request.dispatch_external_message_id,
                    )
                ]

            request = AgentRequest(
                task_id="task-1",
                request_id="task-1:ZHONGSHU_ANALYST:revision-1:1",
                agent_id="agent-1",
                role="review-analyst",
                phase="ZHONGSHU",
                prompt="work",
                idempotency_key="task-1:worker-1",
                issue_id="issue-1",
            )
            driver = ParallelCoordinatorDriver(
                task_id="task-1",
                lifecycle=lifecycle,
                admission=ConcurrencyAdmission(ConcurrencyLimits(global_max=6, per_task_max=6)),
                dispatch=dispatch,
                poll=poll,
                poll_interval=0,
                timeout_seconds=1,
            )
            result = driver.run(
                phase="ZHONGSHU",
                revision_id="revision-1",
                workers=[ParallelWorker("worker-1", "ZHONGSHU_ANALYST", "review-analyst", request)],
                fan_in=lambda values: {"action": "READY_FOR_SOLVER"},
            )
            self.assertEqual(len(result.completed), 1)
            self.assertEqual([item[0] for item in seen], ["dispatch", "poll"])
            self.assertTrue(seen[1][1].endswith(":attempt-1"))
            self.assertEqual(seen[1][2], "trigger-" + seen[0][1])

    def test_missing_dispatch_receipt_is_retried_three_times_and_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            lifecycle = LifecyclePaths(Path(temp) / "task")
            lifecycle.ensure()
            dispatches = []

            def dispatch(request):
                dispatches.append(request.request_id)
                return DispatchReceipt("op", "")

            request = AgentRequest(
                task_id="task-1",
                request_id="task-1:ZHONGSHU_ANALYST:revision-1:1",
                agent_id="agent-1",
                role="review-analyst",
                phase="ZHONGSHU",
                prompt="work",
                idempotency_key="task-1:worker-1",
                issue_id="issue-1",
            )
            driver = ParallelCoordinatorDriver(
                task_id="task-1",
                lifecycle=lifecycle,
                admission=ConcurrencyAdmission(ConcurrencyLimits(global_max=6, per_task_max=6)),
                dispatch=dispatch,
                poll=lambda request: [],
                poll_interval=0,
                timeout_seconds=1,
            )
            result = driver.run(
                phase="ZHONGSHU",
                revision_id="revision-1",
                workers=[ParallelWorker("worker-1", "ZHONGSHU_ANALYST", "review-analyst", request)],
                fan_in=lambda values: {"action": "BLOCKED"},
            )
            self.assertEqual(len(result.completed), 0)
            self.assertEqual(len(result.failed), 1)
            self.assertEqual(len(dispatches), 3)
            self.assertEqual(len(set(dispatches)), 3)

    def test_run_override_can_fail_fast_without_changing_driver_default(self):
        with tempfile.TemporaryDirectory() as temp:
            lifecycle = LifecyclePaths(Path(temp) / "task")
            lifecycle.ensure()
            dispatches = []

            def dispatch(request):
                dispatches.append(request.request_id)
                return DispatchReceipt("op", "")

            request = AgentRequest(
                task_id="task-1",
                request_id="task-1:ZHONGSHU_ANALYST:revision-1:1",
                agent_id="agent-1",
                role="review-analyst",
                phase="ZHONGSHU",
                prompt="work",
                idempotency_key="task-1:worker-1",
                issue_id="issue-1",
            )
            driver = ParallelCoordinatorDriver(
                task_id="task-1",
                lifecycle=lifecycle,
                admission=ConcurrencyAdmission(ConcurrencyLimits(global_max=6, per_task_max=6)),
                dispatch=dispatch,
                poll=lambda request: [],
                poll_interval=0,
                timeout_seconds=1,
                max_attempts=3,
            )
            result = driver.run(
                phase="ZHONGSHU",
                revision_id="revision-1",
                workers=[ParallelWorker("worker-1", "ZHONGSHU_ANALYST", "review-analyst", request)],
                fan_in=lambda values: {"action": "BLOCKED"},
                max_attempts=1,
            )
            self.assertEqual(len(result.failed), 1)
            self.assertEqual(len(dispatches), 1)
        self.assertEqual(driver.max_attempts, 3)

    def test_reported_worker_id_cannot_be_spoofed(self):
        with tempfile.TemporaryDirectory() as temp:
            lifecycle = LifecyclePaths(Path(temp) / "task")
            lifecycle.ensure()
            request = AgentRequest(
                task_id="task-1",
                request_id="task-1:ZHONGSHU_ANALYST:revision-1:1",
                agent_id="agent-1",
                role="review-analyst",
                phase="ZHONGSHU",
                prompt="work",
                idempotency_key="task-1:worker-1",
                issue_id="issue-1",
            )
            driver = ParallelCoordinatorDriver(
                task_id="task-1",
                lifecycle=lifecycle,
                admission=ConcurrencyAdmission(ConcurrencyLimits(global_max=6, per_task_max=6)),
                dispatch=lambda _request: DispatchReceipt("op", "trigger"),
                poll=lambda poll_request: [ExternalMessage(
                    "agent-1",
                    {
                        "action": "READY_FOR_SOLVER",
                        "request_id": poll_request.request_id,
                        "phase": poll_request.phase,
                        "worker_id": "worker-evil",
                    },
                    "trigger",
                )],
                poll_interval=0,
                timeout_seconds=1,
            )
            result = driver.run(
                phase="ZHONGSHU",
                revision_id="revision-1",
                workers=[ParallelWorker("worker-1", "ZHONGSHU_ANALYST", "review-analyst", request)],
                fan_in=lambda values: {"action": "BLOCKED"},
            )
            self.assertEqual(len(result.completed), 0)
            self.assertEqual(len(result.failed), 1)

    def test_wrong_actual_author_is_rejected_by_parallel_transport_binding(self):
        with tempfile.TemporaryDirectory() as temp:
            lifecycle = LifecyclePaths(Path(temp) / "task")
            lifecycle.ensure()
            request = AgentRequest(
                task_id="task-1",
                request_id="task-1:ZHONGSHU_ANALYST:revision-1:1",
                agent_id="agent-1",
                role="review-analyst",
                phase="ZHONGSHU",
                prompt="work",
                idempotency_key="task-1:worker-1",
                issue_id="issue-1",
                target_state="ZHONGSHU_ANALYST",
            )
            driver = ParallelCoordinatorDriver(
                task_id="task-1",
                lifecycle=lifecycle,
                admission=ConcurrencyAdmission(ConcurrencyLimits(global_max=6, per_task_max=6)),
                dispatch=lambda _request: DispatchReceipt("op", "trigger"),
                poll=lambda poll_request: [ExternalMessage(
                    "agent-evil",
                    {
                        "action": "READY_FOR_SOLVER",
                        "request_id": poll_request.request_id,
                        "phase": poll_request.phase,
                    },
                    "trigger",
                )],
                poll_interval=0,
                timeout_seconds=1,
            )
            result = driver.run(
                phase="ZHONGSHU",
                revision_id="revision-1",
                workers=[ParallelWorker("worker-1", "ZHONGSHU_ANALYST", "review-analyst", request)],
                fan_in=lambda values: {"action": "BLOCKED"},
                max_attempts=1,
            )
            self.assertEqual(len(result.completed), 0)
            self.assertEqual(len(result.failed), 1)
            self.assertIn("REPLY_BINDING_ERROR", result.failed[0]["error"])

    def test_correct_actual_author_is_accepted_and_binding_metadata_is_projected(self):
        with tempfile.TemporaryDirectory() as temp:
            lifecycle = LifecyclePaths(Path(temp) / "task")
            lifecycle.ensure()
            captured = []
            request = AgentRequest(
                task_id="task-1",
                request_id="task-1:ZHONGSHU_ANALYST:revision-1:1",
                agent_id="agent-1",
                role="review-analyst",
                phase="ZHONGSHU",
                prompt="work",
                idempotency_key="task-1:worker-1",
                issue_id="issue-1",
                target_state="ZHONGSHU_ANALYST",
                context={"group_id": "group-1", "item_id": "item-1"},
            )
            driver = ParallelCoordinatorDriver(
                task_id="task-1",
                lifecycle=lifecycle,
                admission=ConcurrencyAdmission(ConcurrencyLimits(global_max=6, per_task_max=6)),
                dispatch=lambda _request: DispatchReceipt("op", "trigger"),
                poll=lambda poll_request: [ExternalMessage(
                    "agent-1",
                    {
                        "action": "READY_FOR_SOLVER",
                        "request_id": poll_request.request_id,
                        "phase": poll_request.phase,
                    },
                    "trigger",
                )],
                poll_interval=0,
                timeout_seconds=1,
            )
            result = driver.run(
                phase="ZHONGSHU",
                revision_id="revision-1",
                workers=[ParallelWorker("worker-1", "ZHONGSHU_ANALYST", "review-analyst", request)],
                fan_in=lambda values: {"action": "BLOCKED"},
                validate=lambda payload, _worker: captured.append(payload),
                max_attempts=1,
            )
            self.assertEqual(len(result.completed), 1)
            self.assertEqual(len(captured), 1)
            self.assertEqual(captured[0]["task_id"], "task-1")
            self.assertEqual(captured[0]["request_id"], "task-1:ZHONGSHU_ANALYST:revision-1:1:attempt-1")
            self.assertEqual(captured[0]["phase"], "ZHONGSHU")
            self.assertEqual(captured[0]["revision_id"], "revision-1")
            self.assertEqual(captured[0]["group_id"], "group-1")
            self.assertEqual(captured[0]["item_id"], "item-1")


if __name__ == "__main__":
    unittest.main()
