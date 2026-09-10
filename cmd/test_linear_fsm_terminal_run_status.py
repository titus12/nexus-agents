from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch

from orchestrator.adapters import MulticaCliAdapter
from orchestrator.transport.external import AgentRequest


class TerminalRunStatusTests(TestCase):
    def request(self) -> AgentRequest:
        return AgentRequest(
            task_id="task-1",
            issue_id="SER-705",
            request_id="task-1:ZHONGSHU_ANALYST:1:req",
            agent_id="agent-analyst",
            role="review-analyst",
            phase="ZHONGSHU",
            prompt="review",
            idempotency_key="idem-1",
            sent_after="2026-09-10T02:36:04Z",
            dispatch_external_message_id="dispatch-comment-that-is-not-on-run",
        )

    def test_failed_run_without_comment_ids_is_terminal(self) -> None:
        adapter = MulticaCliAdapter("test-terminal-status")
        failed_run = {
            "agent_id": "agent-analyst",
            "created_at": "2026-09-10T02:36:04Z",
            "status": "error",
            "trigger_comment_id": None,
            "coalesced_comment_ids": None,
            "delivered_comment_ids": [],
        }
        with patch.object(adapter, "_run_with_read_retry", return_value=[failed_run]):
            self.assertEqual(adapter.get_run_status(self.request()), "failed")

    def test_ambiguous_fallback_does_not_claim_another_run(self) -> None:
        adapter = MulticaCliAdapter("test-terminal-status-ambiguous")
        runs = [
            {
                "agent_id": "agent-analyst",
                "created_at": "2026-09-10T02:36:04Z",
                "status": "running",
            },
            {
                "agent_id": "agent-analyst",
                "created_at": "2026-09-10T02:36:05Z",
                "status": "error",
            },
        ]
        with patch.object(adapter, "_run_with_read_retry", return_value=runs):
            self.assertEqual(adapter.get_run_status(self.request()), "unknown")


if __name__ == "__main__":
    import unittest

    unittest.main()
