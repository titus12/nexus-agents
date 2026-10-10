from __future__ import annotations

import unittest

from orchestrator.acceptance_standards import acceptance_signal_gate
from orchestrator.domain.context import (
    ItemWorkflow,
    ProgressState,
    RequestState,
    ReviewState,
    TaskIdentity,
    WorkflowContext,
)
from orchestrator.domain.events import DomainEvent
from orchestrator.domain.findings import normalize_finding_status
from orchestrator.domain.policies.solver_plan import structural_integrity_errors
from orchestrator.domain.states import (
    ZhongshuSolverState,
    _must_requirement_removals,
)
from orchestrator.zhongshu_review import (
    aggregate_task_review_results,
    _blocking_findings,
)
from orchestrator.zhongshu_review_queue import build_review_jobs, structural_gate


def _completed(queue):
    from dataclasses import replace

    from orchestrator.zhongshu_review_queue import COMPLETED, TaskReviewQueue

    return TaskReviewQueue(
        queue.revision_id,
        queue.plan_hash,
        [replace(job, status=COMPLETED) for job in queue.jobs],
    )


def _plan(*, drop_item: str | None = None) -> dict:
    items = [
        {
            "item_id": "item-000001",
            "group_id": "group-000001",
            "title": "t1",
            "objective": "o1",
            "source_requirement_ids": ["req-001"],
            "dependencies": [],
            "acceptance_signals": ["确认字段存在"],
        },
        {
            "item_id": "item-000002",
            "group_id": "group-000001",
            "title": "t2",
            "objective": "o2",
            "source_requirement_ids": ["req-002"],
            "dependencies": [],
            "acceptance_signals": ["确认字段存在"],
        },
    ]
    kept = [item for item in items if item["item_id"] != drop_item]
    return {
        "plan_revision_id": "revision-2",
        "requirements": [
            {"requirement_id": "req-001", "statement": "s1", "priority": "must", "scope": "out" if drop_item == "item-000001" else "in"},
            {"requirement_id": "req-002", "statement": "s2", "priority": "should", "scope": "out" if drop_item == "item-000002" else "in"},
        ],
        "items": kept,
        "groups": [
            {
                "group_id": "group-000001",
                "title": "g1",
                "item_ids": [item["item_id"] for item in kept],
            }
        ],
    }


def _review_context() -> WorkflowContext:
    from orchestrator.domain.context import ReviewTaskItem

    return WorkflowContext(
        identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
        progression=ProgressState("ZHONGSHU_SOLVER", 10, "2026-09-18T00:00:00Z"),
        request=RequestState(raw_request="审查优化"),
        review=ReviewState(
            revision_id="task-1:2",
            requirements=(
                {"requirement_id": "req-001", "statement": "s1", "priority": "must", "scope": "in"},
                {"requirement_id": "req-002", "statement": "s2", "priority": "should", "scope": "in"},
            ),
            task_items=(
                ReviewTaskItem("item-000001", "group-000001", order=0, source_requirement_ids=("req-001",)),
                ReviewTaskItem("item-000002", "group-000001", order=1, source_requirement_ids=("req-002",)),
            ),
            item_workflows=(
                ItemWorkflow(item_id="item-000001"),
                ItemWorkflow(item_id="item-000002"),
            ),
        ),
    )


def _group_docs_entry(
    group_id: str = "group-000001",
    version: int = 1,
    acceptance: tuple[str, ...] = ("确认字段存在",),
) -> dict:
    sections = (
        "背景", "目标", "标识与范围", "状态与边界语义", "行为要求",
        "责任边界", "交叉不变量", "验收标准", "非目标",
    )
    lines = [f"# {group_id} 需求文档 [v{version}]"]
    for number, name in enumerate(sections, start=1):
        lines.append(f"## {number}. {name}")
        if name == "背景":
            lines.append(f"{group_id} 的上下文。")
        elif name == "验收标准":
            # §8 closes with the submitted plan's acceptance_signals verbatim.
            lines.extend(acceptance)
    return {"group_id": group_id, "markdown": "\n".join(lines)}


class AcceptanceSignalGateTests(unittest.TestCase):
    def test_measurement_claim_without_recipe_is_flagged(self) -> None:
        plan = {"items": [
            {"item_id": "item-1", "acceptance_signals": ["确认整体时长提升 50-70%"]},
        ]}
        self.assertEqual(
            acceptance_signal_gate(plan),
            ["ACCEPTANCE_SIGNAL_UNVERIFIABLE:item-1:0"],
        )

    def test_recipe_marker_and_unknown_are_escapes(self) -> None:
        plan = {"items": [
            {"item_id": "item-1", "acceptance_signals": [
                "提升 50% UNKNOWN（验证方法：对象=app.py:189；命令=压测；指标=P95/ms；基线=main；预期=差值）",
            ]},
            {"item_id": "item-2", "acceptance_signals": ["核实提升声称，无实证标注 UNKNOWN"]},
        ]}
        self.assertEqual(acceptance_signal_gate(plan), [])

    def test_static_unit_mention_is_not_flagged(self) -> None:
        plan = {"items": [
            {"item_id": "item-1", "acceptance_signals": ["确认 elapsed_ms 字段存在且为整数"]},
        ]}
        self.assertEqual(acceptance_signal_gate(plan), [])

    def test_structural_integrity_blocks_unverifiable_signal(self) -> None:
        plan = _plan()
        plan["items"][0]["acceptance_signals"] = ["确认缩短 30%"]
        issues = structural_integrity_errors(plan)
        self.assertTrue(
            any(issue.startswith("ACCEPTANCE_SIGNAL_UNVERIFIABLE:item-000001") for issue in issues)
        )

    def test_scope_out_requirement_does_not_need_coverage(self) -> None:
        plan = _plan(drop_item="item-000001")
        issues = structural_integrity_errors(plan)
        self.assertEqual(issues, [])
        plan_kept = _plan(drop_item=None)
        del plan_kept["items"][0]
        plan_kept["groups"][0]["item_ids"] = ["item-000002"]
        # REQUIREMENT_UNCOVERED is advisory (surfaced to the Critic), so it is
        # only visible in the raw structural gate, not the blocking filter.
        issues = structural_gate(plan_kept)
        self.assertIn("REQUIREMENT_UNCOVERED:req-001", issues)


class DiscardRecommendationTests(unittest.TestCase):
    def _queue_and_results(self, action: str, with_finding: bool):
        queue = _completed(build_review_jobs(_plan(), "revision-1", plan_hash="hash-1"))
        results = []
        for job in queue.jobs:
            result = {
                "action": action if job.item_id == "item-000001" else "TASK_APPROVED",
                "review_job_id": job.review_job_id,
                "group_id": job.group_id,
                "item_id": job.item_id,
                "findings": [],
                "review_checks": {},
            }
            if job.item_id == "item-000001":
                result["summary"] = "该任务对本次审查问题不重要"
                if with_finding:
                    result["findings"] = [{
                        "finding_id": "finding-drop-1",
                        "severity": "P1",
                        "category": "task_discard",
                        "claim": "该任务对本次审查问题不重要",
                    }]
            results.append(result)
        return queue, results

    def test_discard_is_folded_into_a_finding_and_revision_request(self) -> None:
        queue, results = self._queue_and_results("REQUEST_TASK_DISCARD", with_finding=False)
        report = aggregate_task_review_results(
            queue.revision_id, queue.plan_hash, queue, results
        )
        self.assertEqual(report["action"], "REQUEST_SOLVER_REVISION")
        discard = [f for f in report["findings"] if f.get("category") == "task_discard"]
        self.assertEqual(len(discard), 1)
        self.assertEqual(discard[0]["item_id"], "item-000001")
        self.assertIn("规划师复核", discard[0]["required_action"])
        review = next(
            entry for entry in report["task_reviews"] if entry["item_id"] == "item-000001"
        )
        self.assertEqual(review["action"], "TASK_CHANGES_REQUIRED")

    def test_worker_supplied_discard_finding_is_kept(self) -> None:
        queue, results = self._queue_and_results("REQUEST_TASK_DISCARD", with_finding=True)
        report = aggregate_task_review_results(
            queue.revision_id, queue.plan_hash, queue, results
        )
        discard = [f for f in report["findings"] if f.get("category") == "task_discard"]
        self.assertEqual([f["finding_id"] for f in discard], ["finding-drop-1"])

    def test_wont_verify_closes_the_finding(self) -> None:
        self.assertEqual(normalize_finding_status(decision="WONT_VERIFY"), "WONT_VERIFY")
        queue = _completed(build_review_jobs(_plan(), "revision-1", plan_hash="hash-1"))
        results = []
        for index, job in enumerate(queue.jobs, 1):
            result = {
                "action": "TASK_CHANGES_REQUIRED" if index == 1 else "TASK_APPROVED",
                "review_job_id": job.review_job_id,
                "group_id": job.group_id,
                "item_id": job.item_id,
                "findings": [],
                "review_checks": {},
            }
            if index == 1:
                result["findings"] = [{
                    "finding_id": "finding-v1",
                    "severity": "P1",
                    "category": "acceptance",
                    "claim": "无法在轮内实测",
                    "decision": "WONT_VERIFY",
                    "resolution": "验证方法：压测对比 app.py:189，基线 main",
                }]
            results.append(result)
        report = aggregate_task_review_results(
            queue.revision_id, queue.plan_hash, queue, results
        )
        closed = next(f for f in report["findings"] if f["finding_id"] == "finding-v1")
        self.assertEqual(closed["status"], "WONT_VERIFY")
        self.assertEqual(_blocking_findings(report["findings"]), [])


class MustRequirementRemovalTests(unittest.TestCase):
    def test_dropping_must_item_is_detected(self) -> None:
        context = _review_context()
        pairs = _must_requirement_removals(context, _plan(drop_item="item-000001"))
        self.assertEqual(pairs, [("item-000001", "req-001")])

    def test_dropping_non_must_item_is_not_detected(self) -> None:
        context = _review_context()
        pairs = _must_requirement_removals(context, _plan(drop_item="item-000002"))
        self.assertEqual(pairs, [])

    def test_no_removal_no_detection(self) -> None:
        context = _review_context()
        self.assertEqual(_must_requirement_removals(context, _plan()), [])


class SolverStateDiscardGateTests(unittest.TestCase):
    def _event(self, plan: dict) -> DomainEvent:
        return DomainEvent(
            "NODE_COMPLETED",
            "task-1",
            10,
            {
                "action": "READY_FOR_CRITIC",
                "plan": plan,
                "plan_hash": "new-hash",
                "revision_id": "task-1:2",
                "summary": "discard executed",
            },
            "2026-09-18T00:00:00Z",
        )

    def test_must_removal_opens_the_human_gate(self) -> None:
        context = _review_context()
        state = ZhongshuSolverState()
        decision = state.handle(context, self._event(_plan(drop_item="item-000001")))
        self.assertEqual(decision.transition.action, "HUMAN_GATE")
        self.assertEqual(
            decision.transition.reason_code, "ZHONGSHU_DISCARD_NEEDS_HUMAN"
        )
        self.assertEqual(decision.update.human_gate.reason_code, "ZHONGSHU_DISCARD_NEEDS_HUMAN")
        # The pruned plan is still folded so the confirmation resumes with it.
        self.assertEqual(len(decision.update.review.task_items), 1)
        self.assertTrue(
            any(effect.effect_type == "plan_artifact" for effect in decision.effects)
        )

    def test_non_must_removal_proceeds_to_critic(self) -> None:
        context = _review_context()
        state = ZhongshuSolverState()
        decision = state.handle(context, self._event(_plan(drop_item="item-000002")))
        self.assertEqual(decision.transition.action, "READY_FOR_CRITIC")

    def test_discarded_item_leftovers_are_pruned(self) -> None:
        from orchestrator.domain.context import ReviewTaskRecord

        context = _review_context()
        review = context.review
        context = WorkflowContext(
            identity=context.identity,
            progression=context.progression,
            request=context.request,
            review=ReviewState(
                revision_id=review.revision_id,
                requirements=review.requirements,
                task_items=review.task_items,
                item_workflows=review.item_workflows,
                task_review_ledger=(
                    ReviewTaskRecord(
                        item_id="item-000001",
                        task_hash="h1",
                        dependency_hash="d1",
                        status="CHANGES_REQUIRED",
                        changes_rounds=5,
                    ),
                    ReviewTaskRecord(
                        item_id="item-000002",
                        task_hash="h2",
                        dependency_hash="d2",
                        status="CHANGES_REQUIRED",
                        changes_rounds=1,
                    ),
                ),
                findings=(
                    {
                        "finding_id": "finding-a",
                        "severity": "P1",
                        "item_id": "item-000001",
                        "group_id": "group-000001",
                        "status": "OPEN",
                        "claim": "旧任务的问题",
                    },
                    {
                        "finding_id": "finding-b",
                        "severity": "P1",
                        "item_id": "item-000002",
                        "group_id": "group-000001",
                        "status": "OPEN",
                        "claim": "保留任务的问题",
                    },
                ),  # type: ignore[arg-type]
            ),
        )
        state = ZhongshuSolverState()
        decision = state.handle(context, self._event(_plan(drop_item="item-000001")))
        update = decision.update.review
        ledger_ids = {record.item_id for record in update.task_review_ledger}
        workflow_ids = {w.item_id for w in update.item_workflows}
        self.assertNotIn("item-000001", ledger_ids)
        self.assertNotIn("item-000001", workflow_ids)
        self.assertIn("item-000002", ledger_ids)
        closed = next(f for f in update.findings if f.finding_id == "finding-a")
        self.assertEqual(closed.status, "WONT_FIX")
        kept = next(f for f in update.findings if f.finding_id == "finding-b")
        self.assertEqual(kept.status, "OPEN")

    def test_removal_detection_falls_back_to_review_requirements(self) -> None:
        context = _review_context()
        plan = _plan(drop_item="item-000001")
        del plan["requirements"]
        pairs = _must_requirement_removals(context, plan)
        self.assertEqual(pairs, [("item-000001", "req-001")])


class SolverSignalGateRetryTests(unittest.TestCase):
    """The acceptance-signal gate bounces to the Solver before blocking."""

    def _unverifiable_plan(self) -> dict:
        plan = _plan()
        plan["items"][0]["acceptance_signals"] = [
            "预期观测量：阶段切换间隙耗时减少 ≥30%"
        ]
        return plan

    def _event(self, plan: dict) -> DomainEvent:
        return DomainEvent(
            "READY_FOR_CRITIC",
            "task-1",
            10,
            {
                "action": "READY_FOR_CRITIC",
                "plan": plan,
                "plan_hash": "new-hash",
                "revision_id": "task-1:2",
                "summary": "plan revision",
                "group_docs": [
                    _group_docs_entry(
                        acceptance=(
                            "预期观测量：阶段切换间隙耗时减少 ≥30%",
                            "确认字段存在",
                        )
                    )
                ],
            },
            "2026-09-18T00:00:00Z",
        )

    def test_first_gate_rejection_retries_on_the_reply_budget(self) -> None:
        context = _review_context()
        decision = ZhongshuSolverState().handle(
            context, self._event(self._unverifiable_plan())
        )
        self.assertEqual(decision.transition.action, "RETRY")
        self.assertEqual(decision.update.recovery.reply_retry_count, 1)
        self.assertIn(
            "ACCEPTANCE_SIGNAL_UNVERIFIABLE:item-000001:0",
            decision.update.recovery.last_failure.message,
        )

    def test_gate_rejection_blocks_after_the_reply_budget(self) -> None:
        from dataclasses import replace as dc_replace

        from orchestrator.domain.context import RecoveryState

        base = _review_context()
        context = dc_replace(
            base,
            recovery=RecoveryState(reply_retry_count=3, max_reply_retries=3),
        )
        decision = ZhongshuSolverState().handle(
            context, self._event(self._unverifiable_plan())
        )
        self.assertEqual(decision.transition.action, "BLOCKED")


if __name__ == "__main__":
    unittest.main()
