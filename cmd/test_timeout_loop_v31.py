from __future__ import annotations

import tempfile
import time
import unittest
from datetime import datetime, timezone

from orchestrator.app import OrchestratorApp
from orchestrator.context import StateContext
from orchestrator.events import Event
from orchestrator.state_machine import StateMachine
from orchestrator.states import build_state_registry
from orchestrator.transitions import TransitionPolicy


class TimeoutLoopTests(unittest.TestCase):
    def test_timeout_uses_current_state_entered_at_not_process_start(self):
        with tempfile.TemporaryDirectory() as root:
            ctx = StateContext(
                task_id="task-1",
                workflow_state="ZHONGSHU_ANALYST",
                active_request_id="req-1",
                entered_at=datetime.now(timezone.utc).isoformat(),
            )
            app = OrchestratorApp(ctx, root=root, timeout_seconds=900, poll_interval=0)
            app.started_at = time.time() - 3600
            self.assertFalse(app._timed_out())

    def test_timeout_retry_limit_routes_to_blocked(self):
        ctx = StateContext(
            task_id="task-1",
            workflow_state="TIMEOUT",
            timeout_request_id="req-1",
            timeout_retry_count=3,
            max_timeout_retries=3,
        )
        event = Event("RETRY_LIMIT_REACHED")
        transition = TransitionPolicy.resolve(ctx, event)
        self.assertEqual(transition.to_state, "BLOCKED")


if __name__ == "__main__":
    unittest.main()
