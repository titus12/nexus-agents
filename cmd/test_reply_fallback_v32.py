from __future__ import annotations

import unittest

from orchestrator.adapters import MulticaCliAdapter
from orchestrator.models import AgentRequest


class ReplyFallbackV32Tests(unittest.TestCase):
    def test_unbound_root_reply_is_not_rebound_to_parent_request(self):
        adapter = MulticaCliAdapter("test-transport")
        request = AgentRequest(
            task_id="parent-task",
            issue_id="SER-529",
            request_id="parent-task:ZHONGSHU_ANALYST:1:req",
            agent_id="analyst-agent",
            role="review-analyst",
            phase="ZHONGSHU",
            prompt="do work",
            idempotency_key="parent-task:ZHONGSHU_ANALYST:1",
            sent_after="2026-08-28T03:00:00Z",
            dispatch_external_message_id="dispatch-1",
        )
        def run(*args):
            if "--thread" in args:
                return []
            if "--recent" in args:
                return [{
                    "id": "unbound-root",
                    "author_id": "analyst-agent",
                    "created_at": "2026-08-28T03:01:00Z",
                    "content": '{"action":"READY_FOR_SOLVER","plan":{}}',
                }]
            if args[:2] == ("issue", "runs"):
                return []
            raise AssertionError(args)

        adapter._run = run
        replies = adapter.poll(request)
        self.assertEqual(replies, [])

    def test_thread_bound_reply_is_rebound_to_parent_request(self):
        adapter = MulticaCliAdapter("test-transport")
        request = AgentRequest(
            task_id="parent-task",
            issue_id="SER-529",
            request_id="parent-task:ZHONGSHU_ANALYST:1:req",
            agent_id="analyst-agent",
            role="review-analyst",
            phase="ZHONGSHU",
            prompt="do work",
            idempotency_key="parent-task:ZHONGSHU_ANALYST:1",
            sent_after="2026-08-28T03:00:00Z",
            dispatch_external_message_id="dispatch-1",
        )

        def run(*args):
            if "--thread" in args:
                return [{
                    "id": "root-reply",
                    "author_id": "analyst-agent",
                    "parent_id": "dispatch-1",
                    "created_at": "2026-08-28T03:02:00Z",
                    "content": '{"action":"READY_FOR_SOLVER","plan":{}}',
                }]
            raise AssertionError(args)

        adapter._run = run
        replies = adapter.poll(request)
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0].payload["task_id"], "parent-task")
        self.assertEqual(
            replies[0].payload["request_id"],
            "parent-task:ZHONGSHU_ANALYST:1:req",
        )

    def test_explicitly_top_level_reply_in_thread_is_ignored(self):
        adapter = MulticaCliAdapter("test-transport")
        request = AgentRequest(
            task_id="task-1",
            issue_id="SER-529",
            request_id="task-1:MENXIA_ITEM_CRITIC:1:req",
            agent_id="critic-agent",
            role="review-critic",
            phase="MENXIA",
            prompt="do work",
            idempotency_key="task-1:MENXIA_ITEM_CRITIC:1",
            sent_after="2026-08-28T03:00:00Z",
            dispatch_external_message_id="dispatch-1",
        )

        def run(*args):
            if "--thread" in args:
                return [{
                    "id": "top-level-reply",
                    "author_id": "critic-agent",
                    "parent_id": None,
                    "created_at": "2026-08-28T03:02:00Z",
                    "content": '{"action":"REVISE_ITEM","findings":[]}',
                }]
            if "--recent" in args:
                return [{
                    "id": "top-level-reply",
                    "author_id": "critic-agent",
                    "parent_id": None,
                    "created_at": "2026-08-28T03:02:00Z",
                    "content": '{"action":"REVISE_ITEM","findings":[]}',
                }]
            raise AssertionError(args)

        adapter._run = run
        self.assertEqual(adapter.poll(request), [])


if __name__ == "__main__":
    unittest.main()
