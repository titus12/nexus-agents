from __future__ import annotations

import unittest

from orchestrator.domain.context import (
    HumanGateState,
    ProgressState,
    RecoveryState,
    RequestState,
    ReviewState,
    ReviewTaskGroup,
    ReviewTaskItem,
    TaskIdentity,
    WorkflowContext,
)
from orchestrator.domain.errors import FailureRecord
from orchestrator.domain.events import DomainEvent
from orchestrator.notifications import _presentation_event_name, build_notification


def _context(
    state: str,
    review: ReviewState | None,
) -> WorkflowContext:
    return WorkflowContext(
        identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
        progression=ProgressState(state, 5, "2026-09-11T00:00:00+00:00"),
        request=RequestState(raw_request="审查 orchestrator 优化"),
        review=review,
    )


def _review(
    *,
    findings: tuple[dict, ...] = (),
    active_item_id: str | None = None,
    active_group_id: str | None = None,
    completed: tuple[str, ...] = (),
) -> ReviewState:
    return ReviewState(
        revision_id="rev-1",
        active_item_id=active_item_id,
        active_group_id=active_group_id,
        findings=findings,  # type: ignore[arg-type]
        task_items=(
            ReviewTaskItem("item-000001", "group-000001", order=0),
            ReviewTaskItem("item-000002", "group-000001", order=1),
            ReviewTaskItem("item-000003", "group-000002", order=2),
            ReviewTaskItem("item-000007", "group-000002", order=3),
            ReviewTaskItem("item-000009", "group-000003", order=4),
        ),
        task_groups=(
            ReviewTaskGroup("group-000001", ("item-000001", "item-000002"), order=0),
            ReviewTaskGroup("group-000002", ("item-000003", "item-000007"), order=1),
            ReviewTaskGroup("group-000003", ("item-000009",), order=2),
        ),
        completed_item_ids=completed,
    )


def _event(payload: dict) -> DomainEvent:
    return DomainEvent(
        name="NODE_COMPLETED",
        task_id="task-1",
        sequence=5,
        payload=payload,
        occurred_at="2026-09-11T00:00:00+00:00",
    )


class HumanGateReasonNotificationTests(unittest.TestCase):
    def test_human_gate_shows_the_escalation_reason(self) -> None:
        context = WorkflowContext(
            identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
            progression=ProgressState("HUMAN_GATE", 7, "2026-09-11T00:00:00+00:00"),
            request=RequestState(raw_request="审查 orchestrator 优化"),
            human_gate=HumanGateState(
                decision_id="task-1:human-gate:7",
                reason_code="ZHONGSHU_STUCK_FINDING",
                resume_state="ZHONGSHU_CRITIC",
            ),
        )

        text = build_notification(
            "HUMAN_GATE",
            "HUMAN_GATE",
            _event({"action": "HUMAN_GATE"}),
            context,
        )

        self.assertIn("ZHONGSHU_STUCK_FINDING", text)


class RetryBudgetNotificationTests(unittest.TestCase):
    def _context(self, recovery: RecoveryState) -> WorkflowContext:
        return WorkflowContext(
            identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
            progression=ProgressState("ZHONGSHU_SOLVER", 6, "2026-09-11T00:00:00+00:00"),
            request=RequestState(raw_request="审查 orchestrator 优化"),
            recovery=recovery,
        )

    def _failure(self, code: str) -> FailureRecord:
        return FailureRecord(
            failure_id="f-1",
            stage="agent_reply",
            owner_component="ZHONGSHU_SOLVER",
            task_id="task-1",
            state="ZHONGSHU_SOLVER",
            sequence=6,
            node_run_id="node-1",
            worker_id=None,
            effect_id=None,
            error_code=code,
            retryable=True,
            message=code,
            cause_type="AgentReply",
        )

    def _resume(self) -> DomainEvent:
        return DomainEvent(
            name="RESUME",
            task_id="task-1",
            sequence=6,
            payload={"action": "RESUME"},
            occurred_at="2026-09-11T00:00:00+00:00",
        )

    def _text(self, recovery: RecoveryState) -> str:
        return build_notification(
            "RETRY_WAIT",
            "RETRY_RESUMED",
            self._resume(),
            self._context(recovery),
        )

    def test_reply_slip_reports_the_reply_budget(self) -> None:
        text = self._text(
            RecoveryState(
                reply_retry_count=1,
                max_reply_retries=3,
                last_failure=self._failure("SOLVER_REVISION_NO_RESPONSE:active=f-1"),
            )
        )
        self.assertIn("重试次数：1/3", text)

    def test_infrastructure_retry_reports_the_external_budget(self) -> None:
        text = self._text(
            RecoveryState(
                external_retry_count=2,
                max_external_retries=3,
                last_failure=self._failure("REMOTE_RUN_FAILED"),
            )
        )
        self.assertIn("重试次数：2/3", text)

    def test_content_retry_reports_the_convergence_budget(self) -> None:
        text = self._text(
            RecoveryState(
                retry_count=1,
                max_retries=3,
                last_failure=self._failure("TASK_CHANGES_REQUIRED"),
            )
        )
        self.assertIn("重试次数：1/3", text)


class ReviewProgressNotificationTests(unittest.TestCase):
    def test_critic_result_lists_passed_and_failed_items(self) -> None:
        payload = {
            "action": "REQUEST_SOLVER_REVISION",
            "findings": [
                {"finding_id": "f-1", "severity": "P0", "item_id": "item-000002",
                 "group_id": "group-000001", "status": "OPEN", "owner_role": "review-solver"},
                {"finding_id": "f-2", "severity": "P1", "item_id": "item-000007",
                 "group_id": "group-000002", "status": "OPEN", "owner_role": "review-solver"},
            ],
        }
        text = build_notification(
            "ZHONGSHU_CRITIC",
            "AGENT_REPLY_ACCEPTED",
            _event(payload),
            _context("ZHONGSHU_CRITIC", _review()),
        )
        self.assertIn("任务级审查：通过 3/5，未通过 2", text)
        self.assertIn("未通过：item-000002（P0,规划师）", text)
        self.assertIn("item-000007（P1,规划师）", text)
        self.assertIn("通过：item-000001、item-000003、item-000009", text)
        self.assertIn("分组：G1 1/2、G2 1/2、G3 1/1", text)
        self.assertNotIn('"finding_id"', text)

    def test_unscoped_findings_are_reported_as_global(self) -> None:
        payload = {
            "action": "REQUEST_SOLVER_REVISION",
            "findings": [
                {"finding_id": "f-1", "severity": "P0", "status": "OPEN",
                 "claim": "MENXIA 并行未真正启用", "required_action": "修复 _dispatch_effect"},
                {"finding_id": "f-2", "severity": "P1", "status": "OPEN"},
            ],
        }
        text = build_notification(
            "ZHONGSHU_CRITIC",
            "AGENT_REPLY_ACCEPTED",
            _event(payload),
            _context("ZHONGSHU_CRITIC", _review()),
        )
        self.assertIn("全局未解决：P0×1、P1×1，共 2 项", text)
        self.assertIn("MENXIA 并行未真正启用", text)
        self.assertNotIn("任务级审查：通过 5/5", text)

    def test_solver_revision_shows_current_targets(self) -> None:
        review = _review(
            findings=(
                {"finding_id": "f-1", "severity": "P0", "item_id": "item-000002",
                 "group_id": "group-000001", "status": "OPEN"},
            )
        )
        text = build_notification(
            "ZHONGSHU_SOLVER",
            "STATE_ENTER",
            _event({"action": "REQUEST_SOLVER_REVISION"}),
            _context("ZHONGSHU_SOLVER", review),
        )
        self.assertIn("规划师本轮待处理：任务 item-000002", text)

    def test_resolved_finding_is_not_blocking(self) -> None:
        review = _review(
            findings=(
                {"finding_id": "f-1", "severity": "P0", "item_id": "item-000002",
                 "group_id": "group-000001", "status": "RESOLVED"},
            )
        )
        text = build_notification(
            "ZHONGSHU_CRITIC",
            "AGENT_REPLY_ACCEPTED",
            _event({"action": "APPROVE_CRITIC"}),
            _context("ZHONGSHU_CRITIC", review),
        )
        self.assertIn("任务级审查：全部通过（5 项）", text)

    def test_all_passed_lists_item_titles(self) -> None:
        review = ReviewState(
            revision_id="rev-1",
            task_items=(
                ReviewTaskItem("item-000001", "group-000001", order=0,
                               title="Verify Menxia Concurrent Execution"),
                ReviewTaskItem("item-000002", "group-000001", order=1,
                               title="Verify Solver Prompt Incrementalization"),
            ),
            task_groups=(
                ReviewTaskGroup("group-000001", ("item-000001", "item-000002"), order=0),
            ),
        )
        text = build_notification(
            "ZHONGSHU_CRITIC",
            "AGENT_REPLY_ACCEPTED",
            _event({"action": "APPROVE_CRITIC"}),
            _context("ZHONGSHU_CRITIC", review),
        )
        self.assertIn("任务级审查：全部通过（2 项）", text)
        self.assertIn(
            "通过：item-000001（Verify Menxia Concurrent Execution）、"
            "item-000002（Verify Solver Prompt Incrementalization）",
            text,
        )

    def test_menxia_item_shows_active_item_and_completion(self) -> None:
        review = _review(active_item_id="item-000002", active_group_id="group-000001", completed=("item-000001",))
        text = build_notification(
            "MENXIA_ITEM_SOLVER",
            "STATE_ENTER",
            _event({"action": "START_ITEM"}),
            _context("MENXIA_ITEM_SOLVER", review),
        )
        self.assertIn("当前条目：item-000002（组 G1）", text)
        self.assertIn("门下省完成：1/5", text)

    def test_non_review_state_keeps_raw_findings(self) -> None:
        text = build_notification(
            "ZHONGSHU_ANALYST",
            "AGENT_REPLY_ACCEPTED",
            _event({"action": "READY_FOR_SOLVER", "findings": [{"finding_id": "f-1"}]}),
            _context("ZHONGSHU_ANALYST", None),
        )
        self.assertIn("findings", text)

    def test_no_review_state_omits_progress_block(self) -> None:
        text = build_notification(
            "ZHONGSHU_ANALYST",
            "AGENT_REPLY_ACCEPTED",
            _event({"action": "READY_FOR_SOLVER"}),
            _context("ZHONGSHU_ANALYST", None),
        )
        self.assertNotIn("审查进度", text)


class ResumeNotificationTests(unittest.TestCase):
    def _resume(self, payload: dict | None = None) -> DomainEvent:
        return DomainEvent(
            name="RESUME",
            task_id="task-1",
            sequence=4,
            payload=payload or {},
            occurred_at="2026-09-11T00:00:00+00:00",
        )

    def test_auto_retry_resume_is_not_reported_as_a_human_decision(self) -> None:
        text = build_notification(
            "RETRY_WAIT",
            "RETRY_RESUMED",
            self._resume(),
            _context("RETRY_WAIT", None),
        )
        self.assertIn("系统自动重试", text)
        self.assertNotIn("已收到人工决策", text)

    def test_human_gate_resume_is_reported_as_a_human_decision(self) -> None:
        text = build_notification(
            "HUMAN_GATE",
            "HUMAN_DECISION_RECEIVED",
            self._resume({"answer": "继续"}),
            _context("HUMAN_GATE", None),
        )
        self.assertIn("已收到人工决策", text)
        self.assertIn("继续", text)

    def test_presentation_event_name_distinguishes_resume_source(self) -> None:
        resume = self._resume()
        self.assertEqual(
            _presentation_event_name(resume, "HUMAN_GATE"), "HUMAN_DECISION_RECEIVED"
        )
        self.assertEqual(
            _presentation_event_name(resume, "BLOCKED"), "HUMAN_DECISION_RECEIVED"
        )
        self.assertEqual(
            _presentation_event_name(resume, "RETRY_WAIT"), "RETRY_RESUMED"
        )


if __name__ == "__main__":
    unittest.main()
