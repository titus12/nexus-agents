from __future__ import annotations

import json
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from orchestrator.adapters import FakeFeishuAdapter, FeishuHttpAdapter
from orchestrator.app import OrchestratorApp, _human_gate_from_payload, _human_gate_prompt
from orchestrator.context import StateContext
from orchestrator.models import HumanGate, HumanReply
from orchestrator.state_machine import StateMachine
from orchestrator.states import build_state_registry


QUESTIONS = [
    {
        "id": "q-000002",
        "question": "五个新 Domain 的最低文档深度如何确定？",
        "options": [
            {"id": "OPTION-A", "label": "index.md 加主干文档", "impact": "成本适中。"},
            {"id": "OPTION-B", "label": "仅 index.md 占位", "impact": "最快但覆盖浅。"},
            {"id": "OPTION-C", "label": "完整代码级文档", "impact": "覆盖最深。"},
        ],
    },
    {
        "id": "q-000003",
        "question": "Proposal 验证是否只覆盖 initialize？",
        "options": [
            {"id": "OPTION-A", "label": "只验证 initialize", "impact": "范围较小。"},
            {"id": "OPTION-B", "label": "initialize 与 enrich 都验证", "impact": "覆盖更完整。"},
        ],
    },
]


class HumanGateQueueTests(unittest.TestCase):
    def test_dict_questions_are_readable_and_queued(self):
        gate = _human_gate_from_payload(
            {"action": "HUMAN_GATE", "questions_for_user": QUESTIONS},
            "fallback",
        )
        self.assertEqual(gate["question"], QUESTIONS[0]["question"])
        self.assertEqual(gate["question_id"], "q-000002")
        self.assertEqual(len(gate["remaining_questions"]), 1)
        prompt = _human_gate_prompt(gate, "fallback")
        self.assertIn("问题 1/2", prompt)
        self.assertIn("A. index.md 加主干文档", prompt)
        self.assertIn("影响：成本适中。", prompt)
        self.assertNotIn("{'id':", prompt)
        self.assertIn("无需携带决策编号", prompt)

    def test_direct_reply_strips_feishu_mention(self):
        adapter = FeishuHttpAdapter()
        gate = HumanGate(
            "decision-1",
            "task-1",
            "ZHONGSHU_SOLVER",
            "prompt",
            "gate-message-1",
        )
        response = {
            "data": {
                "items": [{
                    "message_id": "reply-1",
                    "parent_id": "gate-message-1",
                    "body": {"content": '{"text":"@_user_1  A"}'},
                    "sender": {"id": "user-1"},
                }]
            }
        }
        with patch.object(adapter, "_token", return_value="token"):
            with patch.object(adapter, "_request", return_value=response):
                replies = adapter.poll_reply(gate)
        self.assertEqual(replies[0].answer, "A")

    def test_two_questions_are_asked_in_sequence_and_accumulated(self):
        feishu = FakeFeishuAdapter()
        first = _human_gate_from_payload(
            {"action": "HUMAN_GATE", "questions_for_user": QUESTIONS},
            "fallback",
        )
        queue = first.pop("remaining_questions")
        ctx = StateContext(
            task_id="task-1",
            workflow_state="HUMAN_GATE",
            resume_state="ZHONGSHU_SOLVER",
            request_payload={
                "human_gate": first,
                "human_gate_queue": queue,
                "human_gate_prompt": _human_gate_prompt(first, "fallback"),
                "human_gate_next_state": "ZHONGSHU_SOLVER",
                "human_gate_source_state": "ZHONGSHU_CRITIC",
            },
        )
        states = build_state_registry(feishu=feishu)
        machine = StateMachine(ctx, states)
        machine.start()

        first_decision = ctx.active_decision_id
        feishu.queue_reply(first_decision, HumanReply(first_decision, "@_user_1 A", "user-1"))
        first_event = states["HUMAN_GATE"].update(ctx)
        first_transition = machine.dispatch(first_event)
        self.assertEqual(first_transition.to_state, "HUMAN_GATE")
        self.assertEqual(ctx.request_payload["human_gate"]["question_id"], "q-000003")
        self.assertEqual(ctx.request_payload["human_decisions"][0]["answer"], "A")
        self.assertEqual(
            ctx.request_payload["human_decisions"][0]["selected_option"]["id"],
            "OPTION-A",
        )

        second_decision = ctx.active_decision_id
        feishu.queue_reply(second_decision, HumanReply(second_decision, "B", "user-1"))
        second_event = states["HUMAN_GATE"].update(ctx)
        second_transition = machine.dispatch(second_event)
        self.assertEqual(second_transition.to_state, "ZHONGSHU_SOLVER")
        self.assertEqual(len(ctx.request_payload["human_decisions"]), 2)
        self.assertEqual(
            ctx.request_payload["human_decisions"][1]["selected_option"]["id"],
            "OPTION-B",
        )

    def test_next_event_builds_human_gate_timeout_event(self):
        with tempfile.TemporaryDirectory() as root:
            ctx = StateContext(
                task_id="task-timeout",
                workflow_state="HUMAN_GATE",
                active_decision_id="decision-1",
                entered_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
            )
            app = OrchestratorApp(
                ctx,
                root=root,
                feishu=FakeFeishuAdapter(),
                poll_interval=0,
                timeout_seconds=900,
            )
            event = app._next_event()
            self.assertEqual(event.name, "HUMAN_GATE_TIMEOUT")
            self.assertEqual(event.payload["reason"], "human decision timeout")

    def test_gate_timeout_uses_gate_entered_at_not_process_start(self):
        with tempfile.TemporaryDirectory() as root:
            ctx = StateContext(
                task_id="task-1",
                workflow_state="HUMAN_GATE",
                active_decision_id="decision-1",
                entered_at=datetime.now(timezone.utc).isoformat(),
            )
            app = OrchestratorApp(ctx, root=root, poll_interval=0, timeout_seconds=900)
            app.started_at = time.time() - 3600
            self.assertFalse(app._human_gate_expired())


if __name__ == "__main__":
    unittest.main()
