from __future__ import annotations

import unittest
from unittest.mock import patch

from orchestrator.adapters import FakeFeishuAdapter, FeishuHttpAdapter
from orchestrator.context import StateContext
from orchestrator.events import Event
from orchestrator.models import HumanReply
from orchestrator.notifications import build_agent_notification
from orchestrator.state_machine import StateMachine
from orchestrator.states import build_state_registry


class NotificationsAndGateTests(unittest.TestCase):
    def test_role_notification_contains_real_solver_content_and_is_bounded(self):
        ctx = StateContext(
            task_id="task-1",
            raw_request="检查前端初始化 Proposal 的知识库功能描述覆盖情况",
            active_group_id="group-1",
            active_item_id="item-1",
            request_payload={
                "active_group": {
                    "group_id": "group-1",
                    "title": "初始化流程",
                    "objective": "覆盖初始化阶段的知识库描述",
                },
                "active_item": {
                    "item_id": "item-1",
                    "title": "补充知识库描述",
                    "objective": "补齐缺失的功能说明",
                },
            },
        )
        text = build_agent_notification(
            "review-solver",
            "ZHONGSHU_SOLVER",
            "AGENT_REPLY_ACCEPTED",
            ctx,
            {
                "action": "READY_FOR_CRITIC",
                "plan": {
                    "groups": [{
                        "title": "初始化",
                        "objective": "补齐知识库描述",
                        "items": [{"item_id": "item-1"}],
                    }]
                },
            },
        )
        self.assertIn("规划师", text)
        self.assertIn("本轮目标", text)
        self.assertIn("任务内容：补充知识库描述", text)
        self.assertIn("任务目标：补齐缺失的功能说明", text)
        self.assertIn("1 个组", text)
        self.assertIn("补齐知识库描述", text)
        self.assertLessEqual(len(text), 1400)

    def test_notification_logs_success_with_message_id(self):
        class RecordingFeishu:
            def notify(self, text: str, role: str) -> str:
                return "msg-123"

        state = build_state_registry(feishu=RecordingFeishu())["ZHONGSHU_ANALYST"]
        ctx = StateContext(task_id="task-1", workflow_state="ZHONGSHU_ANALYST")
        with self.assertLogs("review_orchestrator_fsm", level="INFO") as captured:
            state.enter(ctx)
        output = "\n".join(captured.output)
        self.assertIn("FEISHU_NOTIFY_START", output)
        self.assertIn("FEISHU_NOTIFY_SUCCESS", output)
        self.assertIn("message_id=msg-123", output)

    def test_heartbeat_log_uses_heartbeat_event_name(self):
        class RecordingFeishu:
            def notify(self, text: str, role: str) -> str:
                return "msg-heartbeat"

        state = build_state_registry(feishu=RecordingFeishu())["ZHONGSHU_ANALYST"]
        ctx = StateContext(task_id="task-1", workflow_state="ZHONGSHU_ANALYST")
        state.enter(ctx)
        with self.assertLogs("review_orchestrator_fsm", level="INFO") as captured:
            state.notify_heartbeat(ctx, 10)
        output = "\n".join(captured.output)
        self.assertIn("event=HEARTBEAT", output)

    def test_notification_logs_failure_when_adapter_returns_empty_id(self):
        class RecordingFeishu:
            def notify(self, text: str, role: str) -> str:
                return ""

        state = build_state_registry(feishu=RecordingFeishu())["ZHONGSHU_ANALYST"]
        ctx = StateContext(task_id="task-1", workflow_state="ZHONGSHU_ANALYST")
        with self.assertLogs("review_orchestrator_fsm", level="WARNING") as captured:
            state.enter(ctx)
        output = "\n".join(captured.output)
        self.assertIn("FEISHU_NOTIFY_FAILED", output)
        self.assertIn("reason=empty_message_id", output)

    def test_http_adapter_logs_missing_chat_id_without_message_body(self):
        with patch.dict("os.environ", {}, clear=True):
            adapter = FeishuHttpAdapter()
            with self.assertLogs("review_orchestrator_fsm", level="WARNING") as captured:
                receipt = adapter.send_text("secret notification body", "solver")
        self.assertFalse(receipt.delivered)
        output = "\n".join(captured.output)
        self.assertIn("FEISHU_HTTP_SEND_FAILED", output)
        self.assertIn("reason=missing_chat_id", output)
        self.assertNotIn("secret notification body", output)

    def test_feishu_human_gate_reply_returns_decision_event(self):
        feishu = FakeFeishuAdapter()
        ctx = StateContext(
            task_id="task-1",
            workflow_state="HUMAN_GATE",
            resume_state="ZHONGSHU_CRITIC",
            request_payload={"human_gate_prompt": "请选择 A 或 B"},
        )
        states = build_state_registry(feishu=feishu)
        machine = StateMachine(ctx, states)
        machine.start()
        decision_id = ctx.active_decision_id
        self.assertEqual(len(feishu.gates), 1)
        feishu.queue_reply(decision_id, HumanReply(decision_id, "A", "user-1"))
        event = states["HUMAN_GATE"].update(ctx)
        self.assertEqual(event.name, "HUMAN_DECISION_RECEIVED")
        transition = machine.dispatch(event)
        self.assertEqual(transition.to_state, "ZHONGSHU_CRITIC")

    def test_p2_risk_acceptance_defers_finding_and_continues(self):
        feishu = FakeFeishuAdapter()
        ctx = StateContext(
            task_id="task-1",
            workflow_state="HUMAN_GATE",
            resume_state="MENXIA_ITEM_CRITIC",
            request_payload={
                "human_gate_purpose": "P2_RISK",
                "human_gate_next_state": "MENXIA_GROUP_GATE",
                "human_gate_prompt": "接受 P2 风险？",
            },
            findings=[{
                "finding_id": "f-2",
                "severity": "P2",
                "status": "OPEN",
            }],
        )
        states = build_state_registry(feishu=feishu)
        machine = StateMachine(ctx, states)
        machine.start()
        decision_id = ctx.active_decision_id
        feishu.queue_reply(decision_id, HumanReply(decision_id, "B", "user-1"))
        event = states["HUMAN_GATE"].update(ctx)
        transition = machine.dispatch(event)
        self.assertEqual(transition.to_state, "MENXIA_GROUP_GATE")
        self.assertEqual(ctx.resolved_finding_ids, ["f-2"])

    def test_menxia_analyst_notification_names_review_object_and_not_solver_action(self):
        ctx = StateContext(
            task_id="task-1",
            raw_request="检查初始化 Proposal",
            workflow_state="MENXIA_ITEM_ANALYST",
            current_phase="MENXIA",
            current_role="review-analyst",
            active_group_id="group-2",
            active_item_id="item-2",
            request_payload={
                "active_group": {"title": "校验", "objective": "补充校验逻辑"},
                "active_item": {"title": "补充校验描述", "objective": "覆盖校验内容"},
                "implementation_proposal": {
                    "files": ["init.go"],
                    "changes": "补充知识库初始化校验",
                    "tests": ["TestInitKnowledge"],
                },
            },
        )
        text = build_agent_notification(
            "review-analyst",
            "MENXIA_ITEM_ANALYST",
            "STATE_ENTER",
            ctx,
        )
        self.assertIn("审查对象", text)
        self.assertIn("补充知识库初始化校验", text)
        self.assertIn("正向检查", text)
        self.assertNotIn("FEASIBLE", text)


if __name__ == "__main__":
    unittest.main()
