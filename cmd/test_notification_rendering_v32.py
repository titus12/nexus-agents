from __future__ import annotations

import unittest

from orchestrator.context import StateContext
from orchestrator.notifications import (
    build_agent_notification,
    build_zhongshu_parallel_notification,
    should_emit_notification,
)


def context(item_id: str = "item-000005", phase: str = "MENXIA") -> StateContext:
    return StateContext(
        task_id="task-1",
        raw_request="修复知识库功能描述覆盖不全问题",
        current_phase=phase,
        active_group_id="group-000002",
        active_item_id=item_id,
        request_payload={
            "active_group": {
                "group_id": "group-000002",
                "title": "知识库同步",
                "objective": "补充领域描述",
            },
            "active_item": {
                "item_id": item_id,
                "title": "补充领域元数据",
                "objective": "让领域文档通过校验",
            },
        },
    )


class NotificationRenderingV32Tests(unittest.TestCase):
    def test_zhongshu_parallel_status_renders_retry_and_conflict(self):
        text = build_zhongshu_parallel_notification(
            context(phase="ZHONGSHU"),
            "ZHONGSHU_FANIN_COMPLETED",
            {
                "phase": "ZHONGSHU_CRITIC",
                "revision_id": "plan-rev-003",
                "workers": [
                    {"worker_id": "critic-evidence", "status": "COMPLETED", "attempt": 1},
                    {
                        "worker_id": "critic-architecture",
                        "status": "RETRYING",
                        "attempt": 2,
                        "max_attempts": 3,
                        "error": "invalid JSON",
                    },
                ],
                "unresolved_conflicts": ["critic-conflict-0001"],
                "action": "REQUEST_SOLVER_REVISION",
            },
        )
        self.assertIn("中书省并发进度", text)
        self.assertIn("critic-architecture", text)
        self.assertIn("第 2/3 次", text)
        self.assertIn("未解决冲突：1", text)
        self.assertIn("REQUEST_SOLVER_REVISION", text)

    def test_done_state_emits_final_delivery_only(self):
        from orchestrator.states import DoneState

        class RecordingFeishu:
            def __init__(self):
                self.messages = []

            def notify(self, text, role):
                self.messages.append((text, role))
                return "done-message"

        feishu = RecordingFeishu()
        ctx = context()
        ctx.workflow_state = "DONE"
        ctx.last_agent_payload = {
            "action": "APPROVE_GROUP",
            "final_delivery": {
                "items": ["item-000005：补充领域元数据"],
                "remaining_risks": ["需要回归验证"],
            },
        }
        DoneState(feishu=feishu).enter(ctx)
        self.assertEqual(len(feishu.messages), 1)
        self.assertEqual(feishu.messages[0][1], "critic")
        self.assertIn("最终交付", feishu.messages[0][0])

    def test_solver_message_shows_actionable_plan_without_raw_payload(self):
        text = build_agent_notification(
            "review-solver",
            "MENXIA_ITEM_SOLVER",
            "AGENT_REPLY_ACCEPTED",
            context(),
            {
                "action": "FEASIBLE",
                "implementation": {
                    "objective": "补充领域元数据",
                    "affected_modules": ["KnowledgeBase/project/domains/review-orchestration"],
                    "steps": ["补充 frontmatter", "运行 validator"],
                    "verification": ["运行文档校验"],
                    "rollback": ["恢复原文档"],
                },
            },
        )
        self.assertIn("item-000005", text)
        self.assertIn("执行方案", text)
        self.assertIn("涉及位置", text)
        self.assertIn("验证方式", text)
        self.assertIn("回滚方式", text)
        self.assertNotIn('"implementation"', text)
        self.assertNotIn("request_id", text)
        self.assertLessEqual(len(text), 1400)

    def test_analyst_message_has_verdict_and_evidence_gap(self):
        text = build_agent_notification(
            "review-analyst",
            "MENXIA_ITEM_ANALYST",
            "AGENT_REPLY_ACCEPTED",
            context(),
            {
                "action": "EVIDENCE_SUFFICIENT",
                "confirmed_facts": ["入口文档缺少标准 frontmatter"],
                "missing_evidence": ["需要确认子页面是否存在同类问题"],
                "questions_for_solver": ["补充子页面检查范围"],
            },
        )
        self.assertIn("证据审查", text)
        self.assertIn("证据审查通过", text)
        self.assertIn("证据缺口", text)
        self.assertIn("后续要求", text)

    def test_analyst_nested_plan_shows_evidence(self):
        text = build_agent_notification(
            "review-analyst",
            "ZHONGSHU_ANALYST",
            "AGENT_REPLY_ACCEPTED",
            context(phase="ZHONGSHU"),
            {
                "action": "READY_FOR_SOLVER",
                "plan": {
                    "confirmed_facts": [
                        {
                            "evidence_id": "ev-000001",
                            "statement": "日志显示轮询阶段存在重复全量扫描",
                            "source": "orchestrator.log:10-20",
                        }
                    ],
                    "unknowns": [{"statement": "尚未确认生产环境表现"}],
                    "questions_for_solver": ["是否保留 fallback 扫描"],
                    "requirement_trace": {
                        "status": "partial",
                        "covered": ["REQ-001"],
                    },
                },
            },
        )
        self.assertIn("轮询阶段存在重复全量扫描", text)
        self.assertIn("尚未确认生产环境表现", text)
        self.assertIn("是否保留 fallback 扫描", text)
        self.assertIn("REQ-001", text)

    def test_critic_message_has_actionable_finding(self):
        text = build_agent_notification(
            "review-critic",
            "MENXIA_ITEM_CRITIC",
            "AGENT_REPLY_ACCEPTED",
            context(),
            {
                "action": "REQUEST_SOLVER_REVISION",
                "findings": [
                    {
                        "title": "缺少失败回滚策略",
                        "required_change": "补充验证失败后的恢复步骤",
                    }
                ],
            },
        )
        self.assertIn("方案审查", text)
        self.assertIn("需要规划师修订", text)
        self.assertIn("缺少失败回滚策略", text)
        self.assertIn("要求：补充验证失败后的恢复步骤", text)

    def test_group_and_final_messages_are_compact(self):
        group = build_agent_notification(
            "review-critic",
            "MENXIA_GROUP_GATE",
            "AGENT_REPLY_ACCEPTED",
            context(),
            {
                "action": "APPROVE_GROUP",
                "completed_item_count": "3/3",
                "revision_count": 1,
                "group_consistency": "依赖已闭合",
            },
        )
        self.assertIn("组级结果", group)
        self.assertIn("当前组审查通过", group)
        self.assertNotIn("item-000001", group)

        final = build_agent_notification(
            "review-critic",
            "DONE",
            "AGENT_REPLY_ACCEPTED",
            context(),
            {
                "action": "DONE",
                "final_delivery": {
                    "items": ["item-000005：补充领域元数据"],
                    "remaining_risks": ["需要回归验证"],
                },
            },
        )
        self.assertIn("最终交付", final)
        self.assertIn("方案通过不代表代码已实现或测试已执行", final)

    def test_hidden_events_are_not_emitted(self):
        self.assertTrue(should_emit_notification("HEARTBEAT"))
        self.assertFalse(should_emit_notification("POLL_START"))
        self.assertFalse(should_emit_notification("DISPATCH_END"))
        self.assertFalse(should_emit_notification("UNKNOWN_INTERNAL_EVENT"))

    def test_rejection_and_retry_notifications_are_distinct(self):
        ctx = context(phase="ZHONGSHU")
        ctx.workflow_state = "ZHONGSHU_ANALYST"
        ctx.last_error = {
            "code": "AGENT_REPLY_CONTRACT_REJECTED",
            "reason": "ANALYST_PLAN_MISSING_FIELDS:recommendation",
        }
        rejected = build_agent_notification(
            "review-analyst",
            "ZHONGSHU_ANALYST",
            "AGENT_REPLY_REJECTED",
            ctx,
            {},
        )
        self.assertIn("回复需要修正", rejected)
        self.assertIn("ANALYST_PLAN_MISSING_FIELDS", rejected)

        ctx.reply_retry_count = 1
        retry = build_agent_notification(
            "review-analyst",
            "ZHONGSHU_ANALYST",
            "STATE_ENTER",
            ctx,
            {},
        )
        self.assertIn("回复修正重试", retry)
        self.assertIn("不是新的任务", retry)
        self.assertNotIn("｜开始处理】", retry)


if __name__ == "__main__":
    unittest.main()
