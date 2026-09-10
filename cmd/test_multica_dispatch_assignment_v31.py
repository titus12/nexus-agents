from __future__ import annotations

import unittest
from unittest.mock import patch

from orchestrator.adapters import MulticaCliAdapter
from orchestrator.transport.external import AgentRequest


class MulticaDispatchAssignmentTests(unittest.TestCase):
    def test_dispatch_adds_structured_comment_before_assigning_agent(self):
        adapter = MulticaCliAdapter("test-transport")
        request = AgentRequest(
            task_id="task-1",
            issue_id="SER-1",
            request_id="task-1:ZHONGSHU_ANALYST:1:req",
            agent_id="agent-1",
            role="review-analyst",
            phase="OTHER",
            prompt="analyze",
            idempotency_key="task-1:ZHONGSHU_ANALYST:1",
        )
        calls: list[tuple[str, ...]] = []

        def fake_run(*args):
            calls.append(args)
            if args[:2] == ("issue", "get"):
                return {"assignee_id": ""}
            return {"id": "comment-1"}

        with patch.object(adapter, "_run", side_effect=fake_run):
            adapter.dispatch(request)

        self.assertEqual(calls[0][:4], (
            "issue", "comment", "add", "SER-1"
        ))
        self.assertEqual(calls[1][:5], (
            "issue", "get", "SER-1", "--output", "json"
        ))
        self.assertEqual(calls[2][:5], (
            "issue", "update", "SER-1", "--assignee-id", "agent-1"
        ))

    def test_null_assignee_is_treated_as_unassigned(self):
        adapter = MulticaCliAdapter("test-transport-null-assignee")
        with patch.object(
            adapter,
            "_run_with_read_retry",
            return_value={"assignee": None},
        ):
            self.assertFalse(adapter._is_current_assignee("SER-1", "agent-1"))

    def test_assignee_lookup_failure_is_unknown_not_unassigned(self):
        adapter = MulticaCliAdapter("test-transport-assignee-error")
        with patch.object(
            adapter,
            "_run_with_read_retry",
            side_effect=RuntimeError("temporary read failure"),
        ):
            self.assertIsNone(adapter._is_current_assignee("SER-1", "agent-1"))


if __name__ == "__main__":
    unittest.main()
