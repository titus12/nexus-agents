from __future__ import annotations

import unittest

from orchestrator.adapters import FakeFeishuAdapter
from orchestrator.app import OrchestratorApp
from orchestrator.context import StateContext
from orchestrator.events import Event
from orchestrator.states import ZhongshuAnalystState


class HeartbeatAndTimeoutTests(unittest.TestCase):
    def test_request_intake_has_no_role_completion_notification(self):
        feishu = FakeFeishuAdapter()
        state = ZhongshuAnalystState(feishu=feishu)
        ctx = StateContext(task_id="task-1", workflow_state="REQUEST_INTAKE")
        state.exit(ctx, Event("AGENT_REPLY_ACCEPTED", {"action": "START"}))
        self.assertFalse(hasattr(feishu, "messages"))

    def test_timeout_role_with_review_prefix_resumes_analyst(self):
        ctx = StateContext(
            task_id="task-1",
            workflow_state="TIMEOUT",
            timeout_phase="ZHONGSHU",
            timeout_role="review-analyst",
            timeout_request_id="req-1",
        )
        app = OrchestratorApp(ctx, root="test-runs", poll_interval=0)
        self.assertEqual(app._resume_state_for_timeout(), "ZHONGSHU_ANALYST")


if __name__ == "__main__":
    unittest.main()
