from __future__ import annotations

import unittest

from orchestrator.zhongshu_review import aggregate_task_review_results
from orchestrator.zhongshu_review_queue import (
    PENDING,
    RUNNING,
    StaleReviewLease,
    build_review_jobs,
    canonical_hash,
    structural_gate,
)
from orchestrator.domain.policies.zhongshu import merge_findings
from orchestrator.domain.states import _task_graph_projection


def _plan(*, nested: bool = False) -> dict:
    groups = []
    items = []
    for group_index, item_count in enumerate((3, 3, 4), 1):
        group_id = f"group-{group_index:06d}"
        group_items = []
        for item_index in range(1, item_count + 1):
            item_id = f"item-{group_index:02d}{item_index:02d}"
            item = {
                "item_id": item_id,
                "group_id": group_id,
                "title": item_id,
                "objective": f"objective-{item_id}",
                "source_requirement_ids": ["requirement-001"],
                "dependencies": [],
                "acceptance_signals": [f"accept-{item_id}"],
            }
            items.append(item)
            group_items.append(item)
        groups.append(
            {
                "group_id": group_id,
                "title": group_id,
                "objective": f"objective-{group_id}",
                **({"items": group_items} if nested else {"item_ids": [i["item_id"] for i in group_items]}),
            }
        )
    return {
        "plan_revision_id": "revision-1",
        "requirements": [{"requirement_id": "requirement-001", "statement": "one"}],
        "items": items,
        "groups": groups,
    }


class ZhongshuTaskReviewQueueV2Tests(unittest.TestCase):
    def test_build_flattens_every_task(self) -> None:
        queue = build_review_jobs(_plan(), "revision-1", plan_hash="plan-hash")
        self.assertEqual(len(queue.jobs), 10)
        self.assertEqual(queue.counts().get(PENDING), 10)
        self.assertTrue(all(job.task_hash for job in queue.jobs))
        self.assertTrue(all(job.review_job_id.startswith("zhongshu:revision-1:") for job in queue.jobs))

    def test_claim_is_unique_and_stale_lease_is_rejected(self) -> None:
        queue = build_review_jobs(_plan(), "revision-1", plan_hash="plan-hash")
        claimed = [queue.claim_next(f"slot-{index}") for index in range(1, 7)]
        self.assertEqual(len({job.review_job_id for job in claimed if job}), 6)
        self.assertEqual(queue.counts().get(RUNNING), 6)

        first = claimed[0]
        assert first is not None
        with self.assertRaises(StaleReviewLease):
            queue.complete(first.review_job_id, "wrong-lease", first.attempt, "bad.json")
        queue.complete(first.review_job_id, first.lease_id, first.attempt, "result-1.json")

        seventh = queue.claim_next("slot-1")
        assert seventh is not None
        self.assertNotIn(
            seventh.review_job_id,
            {job.review_job_id for job in claimed if job},
        )
        self.assertEqual(queue.counts().get("COMPLETED"), 1)

    def test_recovered_running_job_reuses_external_request(self) -> None:
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

    def test_full_coverage_approves_freeze(self) -> None:
        queue = build_review_jobs(_plan(), "revision-1", plan_hash="plan-hash")
        results = []
        for index, _ in enumerate(queue.jobs, 1):
            claimed = queue.claim_next(f"slot-{(index - 1) % 6 + 1}")
            assert claimed is not None
            queue.complete(
                claimed.review_job_id,
                claimed.lease_id,
                claimed.attempt,
                f"result-{index}.json",
            )
            results.append(_result(claimed, "TASK_APPROVED", queue.plan_hash))
        report = aggregate_task_review_results(
            queue.revision_id, queue.plan_hash, queue, results
        )
        self.assertEqual(report["total_task_count"], 10)
        self.assertEqual(report["completed_task_count"], 10)
        self.assertTrue(report["task_review_complete"])
        self.assertEqual(report["action"], "APPROVE_FREEZE")
        self.assertEqual(report["affected_item_ids"], [])

    def test_task_change_binds_finding_to_item(self) -> None:
        queue = build_review_jobs(_plan(), "revision-1", plan_hash="plan-hash")
        job = queue.jobs[0]
        claimed = queue.claim_next("slot-1")
        assert claimed is not None
        queue.complete(claimed.review_job_id, claimed.lease_id, claimed.attempt, "r.json")
        result = _result(claimed, "TASK_CHANGES_REQUIRED", queue.plan_hash)
        result["findings"] = [
            {"finding_id": "f-1", "severity": "P0", "claim": "boundary unclear"}
        ]
        others = []
        for index, job in enumerate(queue.jobs[1:], 2):
            done = queue.claim_next(f"slot-{index}")
            assert done is not None
            queue.complete(done.review_job_id, done.lease_id, done.attempt, "r.json")
            others.append(_result(job, "TASK_APPROVED", queue.plan_hash))
        report = aggregate_task_review_results(
            queue.revision_id, queue.plan_hash, queue, [result] + others
        )
        self.assertEqual(report["action"], "REQUEST_SOLVER_REVISION")
        self.assertEqual(report["affected_item_ids"], [claimed.item_id])
        finding = report["findings"][0]
        self.assertEqual(finding["item_id"], claimed.item_id)
        self.assertEqual(finding["group_id"], claimed.group_id)

    def test_missing_result_cannot_approve(self) -> None:
        queue = build_review_jobs(_plan(), "revision-1", plan_hash="plan-hash")
        for index, _ in enumerate(queue.jobs, 1):
            claimed = queue.claim_next(f"slot-{index}")
            assert claimed is not None
            queue.complete(claimed.review_job_id, claimed.lease_id, claimed.attempt, "r.json")
        report = aggregate_task_review_results(
            queue.revision_id, queue.plan_hash, queue, []
        )
        self.assertEqual(report["action"], "HUMAN_GATE")
        self.assertFalse(report["task_review_complete"])

    def test_result_with_wrong_group_id_is_rejected_not_raised(self) -> None:
        # A result that names a different group id (e.g. a zero-padded variant)
        # must be reconciled by item id and rejected as an identity mismatch,
        # never abort the whole node join with an uncaught ReviewQueueError.
        queue = build_review_jobs(_plan(), "revision-1", plan_hash="plan-hash")
        results = []
        for index, job in enumerate(queue.jobs, 1):
            claimed = queue.claim_next(f"slot-{(index - 1) % 6 + 1}")
            assert claimed is not None
            queue.complete(
                claimed.review_job_id, claimed.lease_id, claimed.attempt, "r.json"
            )
            result = _result(claimed, "TASK_APPROVED", queue.plan_hash)
            if index == 1:
                result["group_id"] = "group-001"
            results.append(result)

        report = aggregate_task_review_results(
            queue.revision_id, queue.plan_hash, queue, results
        )
        self.assertFalse(report["task_review_complete"])
        self.assertEqual(report["action"], "HUMAN_GATE")
        reasons = {
            entry.get("reason") for entry in report["rejected_reviews"]
        }
        self.assertIn("TASK_REVIEW_RESULT_IDENTITY_MISMATCH", reasons)

    def test_projection_reads_group_item_ids(self) -> None:
        # Solver-style plans carry group membership in groups[*].item_ids and
        # leave items without a group_id; the projection must still bind each
        # item to its real group instead of falling back to a default.
        plan = _plan()
        for item in plan["items"]:
            item.pop("group_id", None)
        items, groups = _task_graph_projection(plan)
        group_by_item = {item.item_id: item.group_id for item in items}
        self.assertEqual(group_by_item["item-0101"], "group-000001")
        self.assertEqual(group_by_item["item-0201"], "group-000002")
        group_members = {group.group_id: group.item_ids for group in groups}
        self.assertIn("item-0101", group_members["group-000001"])
        self.assertIn("item-0201", group_members["group-000002"])

    def test_nested_and_flat_plans_flatten_identically(self) -> None:
        flat = build_review_jobs(_plan(), "revision-1", plan_hash="h")
        nested = build_review_jobs(_plan(nested=True), "revision-1", plan_hash="h")
        self.assertEqual(
            [(j.item_id, j.group_id, j.task_hash) for j in flat.jobs],
            [(j.item_id, j.group_id, j.task_hash) for j in nested.jobs],
        )

    def test_structural_gate_flags_orphan_coverage_and_cycle(self) -> None:
        plan = _plan()
        plan["items"][0]["source_requirement_ids"] = []
        plan["requirements"].append({"requirement_id": "requirement-999", "statement": "uncovered"})
        plan["items"][1]["dependencies"] = [plan["items"][2]["item_id"]]
        plan["items"][2]["dependencies"] = [plan["items"][1]["item_id"]]
        issues = structural_gate(plan)
        self.assertIn("ITEM_WITHOUT_SOURCE_REQUIREMENT:" + plan["items"][0]["item_id"], issues)
        self.assertIn("REQUIREMENT_UNCOVERED:requirement-999", issues)
        self.assertTrue(any(issue.startswith("DEPENDENCY_CYCLE:") for issue in issues))

    def test_structural_gate_passes_clean_plan(self) -> None:
        self.assertEqual(structural_gate(_plan()), [])


    def test_previous_ledger_keeps_reraised_opinion_and_renumbers_collision(self) -> None:
        # Round 2 worker ids are worker-local: an id the last round already
        # stored must keep pointing at the old opinion, and a different opinion
        # reusing that id must be renumbered instead of storing two records
        # with one id.
        queue = build_review_jobs(_plan(), "revision-1", plan_hash="plan-hash")
        first, second = queue.jobs[0], queue.jobs[1]
        for job in queue.jobs:
            claimed = queue.claim_next(f"slot-{job.item_id}")
            assert claimed is not None
            queue.complete(claimed.review_job_id, claimed.lease_id, claimed.attempt, "r.json")
        previous = {
            "findings": [
                {
                    "finding_id": "finding-000001",
                    "severity": "P1",
                    "status": "OPEN",
                    "group_id": first.group_id,
                    "item_id": first.item_id,
                    "category": "evidence",
                    "target": first.item_id,
                    "claim": "第一轮的旧意见",
                    "stuck_rounds": 1,
                },
            ],
        }
        re_raise = _result(first, "TASK_CHANGES_REQUIRED", queue.plan_hash)
        re_raise["findings"] = [
            {
                "finding_id": "finding-000009",
                "severity": "P1",
                "group_id": first.group_id,
                "item_id": first.item_id,
                "category": "evidence",
                "target": first.item_id,
                "claim": "同一意见换了措辞",
            },
        ]
        collision = _result(second, "TASK_CHANGES_REQUIRED", queue.plan_hash)
        collision["findings"] = [
            {
                "finding_id": "finding-000001",
                "severity": "P1",
                "group_id": second.group_id,
                "item_id": second.item_id,
                "category": "evidence",
                "target": second.item_id,
                "claim": "另一个条目上的全新意见，复用了同一个本地编号",
            },
        ]
        rest = [
            _result(job, "TASK_APPROVED", queue.plan_hash)
            for job in queue.jobs[2:]
        ]
        report = aggregate_task_review_results(
            queue.revision_id,
            queue.plan_hash,
            queue,
            [re_raise, collision] + rest,
            previous=previous,
        )
        output_ids = [finding["finding_id"] for finding in report["findings"]]
        self.assertEqual(len(output_ids), len(set(output_ids)))
        kept = next(
            finding
            for finding in report["findings"]
            if finding["item_id"] == first.item_id
        )
        self.assertEqual(kept["finding_id"], "finding-000001")
        renumbered = next(
            finding
            for finding in report["findings"]
            if finding["item_id"] == second.item_id
        )
        self.assertNotEqual(renumbered["finding_id"], "finding-000001")
        self.assertTrue(renumbered["finding_id"].startswith("finding-"))

    def test_reworded_observation_reuses_the_stored_identity(self) -> None:
        # A critic worker reuses the id it saw last round but re-words
        # category/claim, which changes the semantic key.  The matched record
        # must keep the stored canonical key so the ledger fold replaces the
        # old record instead of accumulating live siblings under one id.
        queue = build_review_jobs(_plan(), "revision-1", plan_hash="plan-hash")
        contested = queue.jobs[0]
        for job in queue.jobs:
            claimed = queue.claim_next(f"slot-{job.item_id}")
            assert claimed is not None
            queue.complete(claimed.review_job_id, claimed.lease_id, claimed.attempt, "r.json")

        def _round_result(category: str, claim: str) -> dict:
            result = _result(contested, "TASK_CHANGES_REQUIRED", queue.plan_hash)
            result["findings"] = [
                {
                    "finding_id": "finding-aaa1",
                    "severity": "P1",
                    "group_id": contested.group_id,
                    "item_id": contested.item_id,
                    "category": category,
                    "target": contested.item_id,
                    "claim": claim,
                },
            ]
            return result

        round1 = aggregate_task_review_results(
            queue.revision_id,
            queue.plan_hash,
            queue,
            [_round_result("evidence", "第一轮措辞")]
            + [_result(job, "TASK_APPROVED", queue.plan_hash) for job in queue.jobs[1:]],
        )
        stored = round1["findings"]
        self.assertEqual([finding["finding_id"] for finding in stored], ["finding-aaa1"])
        stored_key = stored[0]["canonical_key"]

        round2 = aggregate_task_review_results(
            queue.revision_id,
            queue.plan_hash,
            queue,
            [_round_result("acceptance", "第二轮换了措辞")]
            + [_result(job, "TASK_APPROVED", queue.plan_hash) for job in queue.jobs[1:]],
            previous={"findings": stored},
        )
        self.assertEqual([finding["finding_id"] for finding in round2["findings"]], ["finding-aaa1"])
        self.assertEqual(round2["findings"][0]["canonical_key"], stored_key)

        merged = merge_findings(stored, round2["findings"])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].finding_id, "finding-aaa1")

    def test_regression_reasons_explain_recontested_tasks(self) -> None:
        queue = build_review_jobs(_plan(), "revision-1", plan_hash="plan-hash")
        job_dep, job_content, job_rereview, job_new = queue.jobs[:4]
        for job in queue.jobs:
            claimed = queue.claim_next(f"slot-{job.item_id}")
            assert claimed is not None
            queue.complete(claimed.review_job_id, claimed.lease_id, claimed.attempt, "r.json")
        previous = {
            "task_review_ledger": [
                {
                    "item_id": job_dep.item_id,
                    "status": "APPROVED",
                    "task_hash": job_dep.task_hash,
                    "dependency_hash": "upstream-old-hash",
                },
                {
                    "item_id": job_content.item_id,
                    "status": "APPROVED",
                    "task_hash": "content-old-hash",
                    "dependency_hash": job_content.dependency_hash,
                },
                {
                    "item_id": job_rereview.item_id,
                    "status": "APPROVED",
                    "task_hash": job_rereview.task_hash,
                    "dependency_hash": job_rereview.dependency_hash,
                },
                {
                    "item_id": job_new.item_id,
                    "status": "CHANGES_REQUIRED",
                    "task_hash": job_new.task_hash,
                    "dependency_hash": job_new.dependency_hash,
                },
            ],
        }
        results = [
            _result(job_dep, "TASK_CHANGES_REQUIRED", queue.plan_hash),
            _result(job_content, "TASK_CHANGES_REQUIRED", queue.plan_hash),
            _result(job_rereview, "TASK_CHANGES_REQUIRED", queue.plan_hash),
            _result(job_new, "TASK_CHANGES_REQUIRED", queue.plan_hash),
        ] + [
            _result(job, "TASK_APPROVED", queue.plan_hash)
            for job in queue.jobs[4:]
        ]
        report = aggregate_task_review_results(
            queue.revision_id, queue.plan_hash, queue, results, previous=previous
        )
        self.assertEqual(
            report["affected_item_reasons"],
            {
                job_dep.item_id: "dependency_changed",
                job_content.item_id: "content_changed",
                job_rereview.item_id: "re_reviewed",
            },
        )


def _result(job, action: str, plan_hash: str) -> dict:
    return {
        "action": action,
        "revision_id": job.revision_id,
        "plan_hash": plan_hash,
        "reviewed_plan_hash": plan_hash,
        "group_id": job.group_id,
        "item_id": job.item_id,
        "reviewed_task_hash": job.task_hash,
        "reviewed_dependency_hash": job.dependency_hash,
        "worker_id": job.worker_id,
        "findings": [],
        "review_checks": {},
    }


if __name__ == "__main__":
    unittest.main()
