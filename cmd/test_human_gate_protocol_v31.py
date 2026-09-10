from __future__ import annotations

import unittest
from unittest.mock import patch

from orchestrator.adapters import FakeFeishuAdapter, FeishuHttpAdapter
from orchestrator.app import (
    OrchestratorApp,
    _default_human_gate_resume_state,
    _human_gate_from_payload,
    _human_gate_prompt,
)
from orchestrator.context import StateContext
from orchestrator.events import Event
from orchestrator.models import HumanGate, HumanReply
from orchestrator.state_machine import StateMachine
from orchestrator.states import build_state_registry


class HumanGateProtocolTests(unittest.TestCase):
    def test_normalizes_critic_next_action_to_human_gate(self):
        payload = {
            "action": "HUMAN_GATE",
            "next_actions": [{
                "action": "HUMAN_GATE",
                "question": "请选择交付方向",
                "options": [
                    {"id": "OPTION-A", "label": "完整回补"},
                    {"id": "OPTION-B", "label": "差异清单"},
                    {"id": "OPTION-C", "label": "分阶段回补"},
                ],
            }],
        }
        gate = _human_gate_from_payload(payload, "fallback")
        self.assertEqual(gate["question"], "请选择交付方向")
        self.assertEqual(len(gate["options"]), 3)
        prompt = _human_gate_prompt(gate, "fallback")
        self.assertIn("A. 完整回补", prompt)
        self.assertIn("B. 差异清单", prompt)
        self.assertIn("C. 分阶段回补", prompt)

    def test_missing_gate_question_renders_review_blockers_without_changing_options(self):
        payload = {
            "action": "HUMAN_GATE",
            "findings": [
                {
                    "finding_id": "finding-000001",
                    "target": "group-000001/item-000001",
                    "claim": "性能验收缺少确定性的基线和阶段耗时配置。",
                    "severity": "P1",
                    "status": "OPEN",
                },
            ],
            "task_queue_counts": {"COMPLETED": 4, "HUMAN_GATE": 2},
            "missing_evidence": ["需要补充竞争场景下的全局限流验收。"],
        }

        gate = _human_gate_from_payload(payload, "原始请求")
        prompt = _human_gate_prompt(gate, "原始请求")

        self.assertIn("中书省审查发现未解决的问题", gate["question"])
        self.assertIn("4/6", gate["question"])
        self.assertIn("group-000001/item-000001", gate["question"])
        self.assertIn("缺失证据", gate["question"])
        self.assertEqual(gate["options"], [])
        self.assertNotIn("原始请求", prompt)

    def test_zhongshu_critic_gate_resumes_solver(self):
        self.assertEqual(
            _default_human_gate_resume_state("ZHONGSHU_CRITIC"),
            "ZHONGSHU_SOLVER",
        )

    def test_human_decision_is_persisted_for_next_agent(self):
        feishu = FakeFeishuAdapter()
        ctx = StateContext(
            task_id="task-1",
            workflow_state="HUMAN_GATE",
            resume_state="ZHONGSHU_SOLVER",
            request_payload={
                "human_gate_next_state": "ZHONGSHU_SOLVER",
                "human_gate_source_state": "ZHONGSHU_CRITIC",
                "human_gate": {
                    "question": "请选择交付方向",
                    "options": [
                        {"id": "OPTION-A", "label": "完整回补"},
                        {"id": "OPTION-B", "label": "差异清单"},
                        {"id": "OPTION-C", "label": "分阶段回补"},
                    ],
                },
                "human_gate_prompt": "请选择交付方向",
            },
        )
        states = build_state_registry(feishu=feishu)
        machine = StateMachine(ctx, states)
        machine.start()
        decision_id = ctx.active_decision_id
        feishu.queue_reply(decision_id, HumanReply(decision_id, "C", "user-1"))
        event = states["HUMAN_GATE"].update(ctx)
        transition = machine.dispatch(event)
        self.assertEqual(transition.to_state, "ZHONGSHU_SOLVER")
        decision = ctx.request_payload["human_decision"]
        self.assertEqual(decision["answer"], "C")
        self.assertEqual(decision["selected_option"]["id"], "OPTION-C")
        self.assertEqual(decision["resume_state"], "ZHONGSHU_SOLVER")

    def test_feishu_accepts_direct_reply_to_gate_message(self):
        adapter = FeishuHttpAdapter()
        gate = HumanGate(
            decision_id="decision-1",
            task_id="task-1",
            resume_state="ZHONGSHU_SOLVER",
            prompt="请选择",
            message_id="gate-message-1",
        )
        response = {
            "data": {
                "items": [
                    {
                        "message_id": "reply-1",
                        "parent_id": "gate-message-1",
                        "body": {"content": '{"text":"C"}'},
                        "sender": {"id": "user-1"},
                    }
                ]
            }
        }
        with patch.object(adapter, "_token", return_value="token"):
            with patch.object(adapter, "_request", return_value=response):
                replies = adapter.poll_reply(gate)
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0].answer, "C")
        self.assertEqual(replies[0].author_id, "user-1")


if __name__ == "__main__":
    unittest.main()
