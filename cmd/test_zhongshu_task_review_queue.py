"""Notification-free regression checks for Zhongshu task review semantics.

This file intentionally contains only pure queue/aggregation checks. It does
not construct a Multica or Feishu adapter and is not run as part of the
implementation turn.
"""
from __future__ import annotations

import sys
import unittest

sys.path.insert(0, r"D:\workspace\src\nexus-agents\cmd")

from orchestrator.states import validate_zhongshu_task_critic_reply
from orchestrator.zhongshu_review import aggregate_task_review_results
from orchestrator.zhongshu_review_queue import (
    StaleReviewLease,
    build_review_jobs,
)


def _plan() -> dict:
    groups = []
    items = []
    for group_index, item_count in enumerate((3, 3, 4), 1):
        group_id = f"G{group_index}"
        group_items = []
        for item_index in range(1, item_count + 1):
            item_id = f"{group_id}-T{item_index}"
            item = {
                "item_id": item_id,
                "group_id": group_id,
                "title": item_id,
                "objective": f"objective-{item_id}",
                "source_requirement_ids": ["REQ-1"],
                "dependencies": [],
                "acceptance": [f"accept-{item_id}"],
            }
            items.append(item)
            group_items.append(item)
        groups.append({
            "group_id": group_id,
            "title": group_id,
            "objective": f"objective-{group_id}",
            "items": group_items,
        })
    return {
        "plan_revision_id": "revision-1",
        "requirements": [{"requirement_id": "REQ-1", "statement": "one requirement"}],
        "items": items,
        "groups": groups,
    }


class ZhongshuTaskReviewQueueTests(unittest.TestCase):
    def test_claims_are_unique_and_stale_lease_is_rejected(self):
        queue = build_review_jobs(_plan(), "revision-1", plan_hash="plan-hash")
        claimed = [queue.claim_next(f"slot-{index}") for index in range(1, 7)]
        self.assertEqual(len({job.review_job_id for job in claimed if job}), 6)
        self.assertEqual(queue.counts().get("RUNNING"), 6)

        first = claimed[0]
        assert first is not None
        with self.assertRaises(StaleReviewLease):
            queue.complete(first.review_job_id, "wrong-lease", first.attempt, "bad.json")
        queue.complete(first.review_job_id, first.lease_id, first.attempt, "result-1.json")

        next_job = queue.claim_next("slot-1")
        self.assertIsNotNone(next_job)
        self.assertNotIn(next_job.review_job_id, {job.review_job_id for job in claimed if job})

    def test_recovered_running_job_reuses_external_request_identity(self):
        queue = build_review_jobs(_plan(), "revision-1", plan_hash="plan-hash")
        original = queue.claim_next("slot-1")
        assert original is not None
        queue.recover_running()
        recovered = queue.claim_next("slot-2")
        assert recovered is not None
        self.assertEqual(recovered.attempt, original.attempt)
        self.assertEqual(recovered.request_id, original.request_id)
        self.assertEqual(recovered.idempotency_key, original.idempotency_key)
        self.assertNotEqual(recovered.lease_id, original.lease_id)

    def test_fanin_is_task_coverage_not_global_quorum(self):
        queue = build_review_jobs(_plan(), "revision-1", plan_hash="plan-hash")
        results = []
        for index, job in enumerate(queue.jobs, 1):
            claimed = queue.claim_next(f"slot-{(index - 1) % 6 + 1}")
            assert claimed is not None
            queue.complete(
                claimed.review_job_id,
                claimed.lease_id,
                claimed.attempt,
                f"result-{index}.json",
            )
            results.append({
                "action": "TASK_APPROVED",
                "revision_id": queue.revision_id,
                "plan_hash": queue.plan_hash,
                "reviewed_plan_hash": queue.plan_hash,
                "group_id": claimed.group_id,
                "item_id": claimed.item_id,
                "reviewed_task_hash": claimed.task_hash,
                "reviewed_dependency_hash": claimed.dependency_hash,
                "worker_id": f"slot-{(index - 1) % 6 + 1}",
                "findings": [],
                "review_checks": {
                    "requirement_coverage": "pass",
                    "boundary": "pass",
                    "dependencies": "pass",
                    "acceptance": "pass",
                    "risks": "pass",
                },
            })
        report = aggregate_task_review_results(
            queue.revision_id,
            queue.plan_hash,
            queue,
            results,
        )
        self.assertEqual(report["total_task_count"], 10)
        self.assertEqual(report["completed_task_count"], 10)
        self.assertEqual(report["action"], "APPROVE_FREEZE")
        self.assertTrue(report["task_review_complete"])

    def test_missing_result_cannot_approve(self):
        queue = build_review_jobs(_plan(), "revision-1", plan_hash="plan-hash")
        for index, job in enumerate(queue.jobs, 1):
            claimed = queue.claim_next(f"slot-{index}")
            assert claimed is not None
            queue.complete(claimed.review_job_id, claimed.lease_id, claimed.attempt, f"result-{index}.json")
        report = aggregate_task_review_results(
            queue.revision_id,
            queue.plan_hash,
            queue,
            [],
        )
        self.assertEqual(report["action"], "HUMAN_GATE")
        self.assertFalse(report["task_review_complete"])

    def test_task_finding_must_stay_with_assigned_item(self):
        queue = build_review_jobs(_plan(), "revision-1", plan_hash="plan-hash")
        job = queue.jobs[0]
        payload = {
            "action": "TASK_CHANGES_REQUIRED",
            "revision_id": job.revision_id,
            "plan_hash": queue.plan_hash,
            "reviewed_plan_hash": queue.plan_hash,
            "group_id": job.group_id,
            "item_id": job.item_id,
            "reviewed_task_hash": job.task_hash,
            "reviewed_dependency_hash": job.dependency_hash,
            "review_checks": {
                "requirement_coverage": "pass",
                "boundary": "fail",
                "dependencies": "pass",
                "acceptance": "pass",
                "risks": "pass",
            },
            "findings": [{
                "finding_id": "f-1",
                "group_id": job.group_id,
                "item_id": "another-task",
                "severity": "P1",
            }],
        }
        self.assertEqual(
            validate_zhongshu_task_critic_reply(payload, job),
            "ZHONGSHU_TASK_CRITIC_FINDING_ITEM_MISMATCH:0",
        )


if __name__ == "__main__":
    unittest.main()
