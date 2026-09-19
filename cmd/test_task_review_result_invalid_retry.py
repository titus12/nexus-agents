from __future__ import annotations

import unittest

from orchestrator.runtime.agent_effects import AgentNodeJoiner
from orchestrator.runtime.nodes import WorkerResult
from orchestrator.zhongshu_review_queue import build_review_jobs

REVISION = "rev-1"
PLAN_HASH = "plan-hash"


def _plan() -> dict:
    items = [
        {
            "item_id": "item-1",
            "group_id": "group-1",
            "title": "one",
            "objective": "do one",
            "source_requirement_ids": ["req-1"],
            "dependencies": [],
            "acceptance_signals": ["observable"],
        },
        {
            "item_id": "item-2",
            "group_id": "group-1",
            "title": "two",
            "objective": "do two",
            "source_requirement_ids": ["req-1"],
            "dependencies": [],
            "acceptance_signals": ["observable"],
        },
    ]
    return {
        "requirements": [{"requirement_id": "req-1", "statement": "one"}],
        "items": items,
        "groups": [{"group_id": "group-1", "item_ids": ["item-1", "item-2"]}],
    }


def _completed_queue():
    from dataclasses import replace

    from orchestrator.zhongshu_review_queue import COMPLETED, TaskReviewQueue

    queue = build_review_jobs(_plan(), REVISION, plan_hash=PLAN_HASH)
    return TaskReviewQueue(
        queue.revision_id,
        queue.plan_hash,
        [replace(job, status=COMPLETED) for job in queue.jobs],
    )


def _valid_result(job) -> dict:
    return {
        "action": "TASK_APPROVED",
        "revision_id": REVISION,
        "plan_hash": PLAN_HASH,
        "reviewed_plan_hash": PLAN_HASH,
        "group_id": job.group_id,
        "item_id": job.item_id,
        "reviewed_task_hash": job.task_hash,
        "reviewed_dependency_hash": job.dependency_hash,
        "worker_id": f"critic-{job.item_id}",
        "findings": [],
        "review_checks": {},
    }


def _joiner(queue) -> AgentNodeJoiner:
    return AgentNodeJoiner(
        "node:task-1:ZHONGSHU_CRITIC:6",
        task_id="task-1",
        state="ZHONGSHU_CRITIC",
        sequence=6,
        revision_id=REVISION,
        plan_hash=PLAN_HASH,
        task_review_queue=queue,
    )


class TaskReviewResultInvalidTests(unittest.TestCase):
    def test_usable_reply_with_empty_identity_is_corrected(self) -> None:
        queue = _completed_queue()
        first, second = queue.jobs
        good = _valid_result(first)
        # item-2's worker returned a contract-valid envelope (BLOCKED) whose
        # echoed identity fields are blank transcription slips; the dispatch
        # pins what the worker reviews, so the joiner corrects them from the
        # queue job instead of voiding the whole review wave.
        bad = {
            "action": "BLOCKED",
            "revision_id": "",
            "plan_hash": "",
            "reviewed_plan_hash": "",
            "group_id": second.group_id,
            "item_id": second.item_id,
            "reviewed_task_hash": "",
            "reviewed_dependency_hash": "",
            "findings": [],
            "review_checks": {},
        }
        results = (
            WorkerResult("critic-1", "SUCCEEDED", None, result_payload=good),
            WorkerResult("critic-2", "SUCCEEDED", None, result_payload=bad),
        )

        node_result = _joiner(queue).join(results)

        self.assertEqual(node_result.status, "SUCCEEDED")
        self.assertEqual(node_result.aggregate.get("action"), "BLOCKED")
        self.assertEqual(len(node_result.aggregate.get("identity_corrections") or []), 1)

    def test_result_for_unknown_job_still_triggers_retryable_failure(self) -> None:
        queue = _completed_queue()
        first, second = queue.jobs
        good = _valid_result(first)
        bad = _valid_result(second)
        bad["review_job_id"] = ""
        bad["item_id"] = "item-does-not-exist"
        results = (
            WorkerResult("critic-1", "SUCCEEDED", None, result_payload=good),
            WorkerResult("critic-2", "SUCCEEDED", None, result_payload=bad),
        )

        node_result = _joiner(queue).join(results)

        self.assertEqual(node_result.status, "FAILED")
        self.assertIsNotNone(node_result.failure)
        self.assertEqual(
            node_result.failure.error_code, "NODE_TASK_REVIEW_RESULT_INVALID"
        )
        self.assertTrue(node_result.failure.retryable)
        accepted = {
            entry["item_id"] for entry in node_result.aggregate["task_reviews"]
        }
        self.assertEqual(accepted, {first.item_id})

    def test_all_usable_replies_join_normally(self) -> None:
        queue = _completed_queue()
        results = tuple(
            WorkerResult(
                f"critic-{job.item_id}",
                "SUCCEEDED",
                None,
                result_payload=_valid_result(job),
            )
            for job in queue.jobs
        )

        node_result = _joiner(queue).join(results)

        self.assertEqual(node_result.status, "SUCCEEDED")
        self.assertEqual(node_result.aggregate.get("action"), "APPROVE_CRITIC")

    def test_binding_context_stamps_the_routing_key(self) -> None:
        # The binding's dispatch record owns the routing key: a reply that
        # never names its job is still routed to the right queue job, and the
        # identity fields are stamped from the authoritative job data.
        queue = _completed_queue()
        contexts = {}
        for worker_index, job in enumerate(queue.jobs, 1):
            contexts[f"critic-{worker_index:02d}"] = {
                "review_job_id": job.review_job_id,
                "item_id": job.item_id,
                "task_hash": job.task_hash,
                "dependency_hash": job.dependency_hash,
            }
        joiner = AgentNodeJoiner(
            "node:task-1:ZHONGSHU_CRITIC:6",
            task_id="task-1",
            state="ZHONGSHU_CRITIC",
            sequence=6,
            revision_id=REVISION,
            plan_hash=PLAN_HASH,
            task_review_queue=queue,
            binding_contexts=contexts,
        )
        results = tuple(
            WorkerResult(
                f"critic-{index:02d}",
                "SUCCEEDED",
                None,
                result_payload={
                    "action": "TASK_CHANGES_REQUIRED",
                    "findings": [
                        {
                            "finding_id": f"f-{index}",
                            "severity": "P1",
                            "claim": f"finding on {job.item_id}",
                        }
                    ],
                    "review_checks": {},
                },
            )
            for index, job in enumerate(queue.jobs, 1)
        )

        node_result = joiner.join(results)

        self.assertEqual(node_result.status, "SUCCEEDED")
        self.assertEqual(node_result.aggregate.get("action"), "REQUEST_SOLVER_REVISION")
        stamped_items = sorted(
            entry["item_id"] for entry in node_result.aggregate["task_reviews"]
        )
        self.assertEqual(stamped_items, sorted(job.item_id for job in queue.jobs))
        for entry in node_result.aggregate["task_reviews"]:
            self.assertTrue(entry["reviewed_task_hash"])
            self.assertTrue(entry["reviewed_dependency_hash"])


if __name__ == "__main__":
    unittest.main()
