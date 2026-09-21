from __future__ import annotations

from dataclasses import replace
import unittest

from orchestrator.zhongshu_review import aggregate_task_review_results
from orchestrator.zhongshu_review_queue import (
    COMPLETED,
    TaskReviewQueue,
    build_review_jobs,
)


def _plan() -> dict:
    item_id = "item-0101"
    return {
        "plan_revision_id": "revision-1",
        "requirements": [{"requirement_id": "requirement-001", "statement": "one"}],
        "items": [
            {
                "item_id": item_id,
                "group_id": "group-000001",
                "title": item_id,
                "objective": f"objective-{item_id}",
                "source_requirement_ids": ["requirement-001"],
                "dependencies": [],
                "acceptance_signals": [f"accept-{item_id}"],
            }
        ],
        "groups": [
            {
                "group_id": "group-000001",
                "title": "group-000001",
                "objective": "objective-group-000001",
                "item_ids": [item_id],
            }
        ],
    }


def _completed(queue) -> TaskReviewQueue:
    return TaskReviewQueue(
        queue.revision_id,
        queue.plan_hash,
        [replace(job, status=COMPLETED) for job in queue.jobs],
    )


def _result(job, action: str, plan_hash: str) -> dict:
    return {
        "action": action,
        "review_job_id": job.review_job_id,
        "revision_id": job.revision_id,
        "plan_hash": plan_hash,
        "reviewed_plan_hash": plan_hash,
        "group_id": job.group_id,
        "item_id": job.item_id,
        "reviewed_task_hash": job.task_hash,
        "reviewed_dependency_hash": job.dependency_hash,
        "worker_id": f"critic-{job.item_id}",
        "findings": [],
        "review_checks": {},
    }


def _previous(*findings: dict) -> dict:
    return {"findings": list(findings)}


def _p0(item_id: str, finding_id: str = "finding-p0-1") -> dict:
    return {
        "finding_id": finding_id,
        "item_id": item_id,
        "group_id": "group-000001",
        "severity": "P0",
        "status": "OPEN",
        "decision": "OPEN",
        "category": "observability",
        "target": "item-0101",
        "claim": "acceptance signal cannot be verified",
    }


class ZhongshuDeltaDispositionTests(unittest.TestCase):
    """The delta-reply contract: active P0s must be explicitly dispositioned."""

    def test_silent_p0_on_contested_item_is_rejected(self) -> None:
        queue = _completed(build_review_jobs(_plan(), "revision-1", plan_hash="plan-hash"))
        job = queue.jobs[0]
        result = _result(job, "TASK_CHANGES_REQUIRED", queue.plan_hash)
        report = aggregate_task_review_results(
            queue.revision_id,
            queue.plan_hash,
            queue,
            [result],
            previous=_previous(_p0(job.item_id)),
        )
        reasons = {
            str(entry.get("reason") or "") for entry in report["rejected_reviews"]
        }
        self.assertIn("TASK_REVIEW_P0_DISPOSITION_MISSING", reasons)
        self.assertFalse(report["task_review_complete"])

    def test_p0_reraise_with_canonical_id_passes(self) -> None:
        queue = _completed(build_review_jobs(_plan(), "revision-1", plan_hash="plan-hash"))
        job = queue.jobs[0]
        result = _result(job, "TASK_CHANGES_REQUIRED", queue.plan_hash)
        result["findings"] = [
            {
                "finding_id": "finding-p0-1",
                "severity": "P0",
                "category": "observability",
                "target": "item-0101",
                "claim": "acceptance signal cannot be verified",
            }
        ]
        report = aggregate_task_review_results(
            queue.revision_id,
            queue.plan_hash,
            queue,
            [result],
            previous=_previous(_p0(job.item_id)),
        )
        self.assertEqual(report["rejected_reviews"], [])
        self.assertTrue(report["task_review_complete"])
        self.assertEqual(report["action"], "REQUEST_SOLVER_REVISION")
        self.assertEqual(
            report["active_p0_p1_finding_ids"], ["finding-p0-1"]
        )

    def test_p0_accept_response_defers_the_finding(self) -> None:
        queue = _completed(build_review_jobs(_plan(), "revision-1", plan_hash="plan-hash"))
        job = queue.jobs[0]
        result = _result(job, "TASK_CHANGES_REQUIRED", queue.plan_hash)
        result["finding_responses"] = [
            {
                "finding_id": "finding-p0-1",
                "response": "ACCEPT",
                "note": "follow up after execution",
            }
        ]
        report = aggregate_task_review_results(
            queue.revision_id,
            queue.plan_hash,
            queue,
            [result],
            previous=_previous(_p0(job.item_id)),
        )
        self.assertEqual(report["rejected_reviews"], [])
        by_id = {
            item["finding_id"]: item for item in report["findings"]
        }
        self.assertEqual(by_id["finding-p0-1"]["status"], "DEFERRED")
        self.assertEqual(report["active_p0_p1_finding_ids"], [])
        self.assertEqual(
            report["finding_dispositions"],
            [
                {
                    "review_job_id": job.review_job_id,
                    "item_id": job.item_id,
                    "finding_id": "finding-p0-1",
                    "response": "DEFERRED",
                }
            ],
        )

    def test_p0_resolved_response_closes_the_finding(self) -> None:
        queue = _completed(build_review_jobs(_plan(), "revision-1", plan_hash="plan-hash"))
        job = queue.jobs[0]
        result = _result(job, "TASK_APPROVED", queue.plan_hash)
        result["finding_responses"] = [
            {
                "finding_id": "finding-p0-1",
                "response": "RESOLVED",
                "note": "signal now names the baseline metric",
            }
        ]
        report = aggregate_task_review_results(
            queue.revision_id,
            queue.plan_hash,
            queue,
            [result],
            previous=_previous(_p0(job.item_id)),
        )
        self.assertEqual(report["rejected_reviews"], [])
        by_id = {
            item["finding_id"]: item for item in report["findings"]
        }
        self.assertEqual(by_id["finding-p0-1"]["status"], "RESOLVED")
        self.assertIn(
            "baseline metric", by_id["finding-p0-1"].get("resolution") or ""
        )
        self.assertEqual(report["action"], "APPROVE_FREEZE")

    def test_p1_silence_is_tolerated(self) -> None:
        queue = _completed(build_review_jobs(_plan(), "revision-1", plan_hash="plan-hash"))
        job = queue.jobs[0]
        result = _result(job, "TASK_CHANGES_REQUIRED", queue.plan_hash)
        previous_row = _p0(job.item_id)
        previous_row["finding_id"] = "finding-p1-1"
        previous_row["severity"] = "P1"
        report = aggregate_task_review_results(
            queue.revision_id,
            queue.plan_hash,
            queue,
            [result],
            previous=_previous(previous_row),
        )
        self.assertEqual(report["rejected_reviews"], [])
        self.assertTrue(report["task_review_complete"])

    def test_resolved_p0_stops_gating_later_rounds(self) -> None:
        # A P0 closed in an earlier round (RESOLVED status in the ledger) is
        # not "active", so a fresh verdict need not disposition it again.
        queue = _completed(build_review_jobs(_plan(), "revision-1", plan_hash="plan-hash"))
        job = queue.jobs[0]
        result = _result(job, "TASK_CHANGES_REQUIRED", queue.plan_hash)
        closed = _p0(job.item_id)
        closed["status"] = closed["decision"] = "RESOLVED"
        report = aggregate_task_review_results(
            queue.revision_id,
            queue.plan_hash,
            queue,
            [result],
            previous=_previous(closed),
        )
        self.assertEqual(report["rejected_reviews"], [])
        self.assertTrue(report["task_review_complete"])


class ZhongshuSuggestionContractTests(unittest.TestCase):
    """Critic findings on a contested verdict carry an actionable suggestion."""

    def test_finding_schema_advertises_required_action(self) -> None:
        from orchestrator.contracts.zhongshu_critic import CONTRACT, FIELDS

        finding_fields = FIELDS["findings"]
        self.assertIn("required_action", str(finding_fields))
        self.assertIn("Direction rule", " ".join(CONTRACT.prompt_rules))

    def test_contested_verdict_without_suggestion_is_logged(self) -> None:
        queue = _completed(build_review_jobs(_plan(), "revision-1", plan_hash="plan-hash"))
        job = queue.jobs[0]
        result = _result(job, "TASK_CHANGES_REQUIRED", queue.plan_hash)
        result["findings"] = [
            {"finding_id": "f-1", "severity": "P1", "claim": "no suggestion here"}
        ]
        report = aggregate_task_review_results(
            queue.revision_id, queue.plan_hash, queue, [result]
        )
        self.assertEqual(report["action"], "REQUEST_SOLVER_REVISION")
        self.assertEqual(report["rejected_reviews"], [])


if __name__ == "__main__":
    unittest.main()
