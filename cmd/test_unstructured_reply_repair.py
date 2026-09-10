from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from orchestrator.adapters import MulticaCliAdapter
from orchestrator.models import AgentRequest
from orchestrator.states import BaseState, _attach_repair_feedback
from orchestrator.context import StateContext


class UnstructuredReplyRepairTests(unittest.TestCase):
    def request(self) -> AgentRequest:
        return AgentRequest(
            task_id="task-1",
            issue_id="SER-1",
            request_id="task-1:ZHONGSHU_SOLVER:1:req",
            agent_id="agent-1",
            role="review-solver",
            phase="ZHONGSHU",
            prompt="do work",
            idempotency_key="task-1:ZHONGSHU_SOLVER:1",
            sent_after="2026-08-29T01:00:00Z",
            dispatch_external_message_id="dispatch-1",
        )

    def test_unique_bound_unstructured_reply_is_returned_for_repair(self):
        adapter = MulticaCliAdapter("test-transport")
        comments = [{
            "id": "reply-1",
            "author_id": "agent-1",
            "created_at": "2026-08-29T01:01:00Z",
            "content": "The solver found the root cause.",
        }]
        with patch.object(adapter, "_run", return_value=comments):
            replies = adapter.poll(self.request())
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0].payload["action"], "__UNSTRUCTURED_REPLY__")
        self.assertEqual(
            replies[0].payload["raw_reply"],
            "The solver found the root cause.",
        )

    def test_ambiguous_unstructured_replies_remain_pending(self):
        adapter = MulticaCliAdapter("test-transport")
        comments = [
            {
                "id": "reply-1",
                "author_id": "agent-1",
                "created_at": "2026-08-29T01:01:00Z",
                "content": "first",
            },
            {
                "id": "reply-2",
                "author_id": "agent-1",
                "created_at": "2026-08-29T01:02:00Z",
                "content": "second",
            },
        ]
        with patch.object(adapter, "_run", return_value=comments):
            self.assertEqual(adapter.poll(self.request()), [])

    def test_unstructured_update_does_not_double_count_retry(self):
        class FakeMultica:
            def poll(self, _request):
                from orchestrator.models import ExternalMessage

                return [
                    ExternalMessage(
                        "agent-1",
                        {
                            "action": "__UNSTRUCTURED_REPLY__",
                            "raw_reply": "The solver found the root cause.",
                        },
                        "reply-1",
                    )
                ]

        ctx = StateContext(
            task_id="task-1",
            workflow_state="ZHONGSHU_SOLVER",
            current_role="review-solver",
            current_phase="ZHONGSHU",
            expected_agent_id="agent-1",
            active_request_id="task-1:ZHONGSHU_SOLVER:1:req",
        )
        state = BaseState(multica=FakeMultica())
        event = state.update(ctx)
        self.assertEqual(event.name, "AGENT_REPLY_REJECTED")
        self.assertEqual(ctx.reply_retry_count, 0)

    def test_unstructured_error_attaches_original_reply(self):
        ctx = StateContext(
            task_id="task-1",
            raw_request="check the issue",
            workflow_state="ZHONGSHU_SOLVER",
            last_error={
                "code": "AGENT_REPLY_UNSTRUCTURED",
                "reason": "REPLY_BODY_NOT_STRUCTURED",
            },
            request_payload={
                "last_rejected_reply": {
                    "action": "__UNSTRUCTURED_REPLY__",
                    "raw_reply": "The solver found the root cause.",
                }
            },
        )
        value = json.loads(
            _attach_repair_feedback(
                '{"task":"check","role":"ZHONGSHU_SOLVER"}',
                ctx,
            )
        )
        self.assertEqual(
            value["repair_feedback"]["validation_error"],
            "REPLY_BODY_NOT_STRUCTURED",
        )
        self.assertEqual(
            value["repair_feedback"]["original_reply"]["raw_reply"],
            "The solver found the root cause.",
        )


if __name__ == "__main__":
    unittest.main()
