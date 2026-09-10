from __future__ import annotations

import logging
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from orchestrator.adapters import FakeFeishuAdapter, MulticaCliAdapter
from orchestrator.app import OrchestratorApp
from orchestrator.context import StateContext
from orchestrator.persistence import JsonStateStore


class ExplodingMultica:
    def dispatch(self, _request):
        raise AssertionError("dispatch must not run")

    def find_existing_request(self, _idempotency_key, _issue_id=""):
        return None

    def poll(self, _request):
        raise ValueError("unexpected poll failure")


class RuntimeDiagnosticsTests(unittest.TestCase):
    def test_unhandled_exception_is_logged_and_persisted(self):
        with tempfile.TemporaryDirectory() as root:
            ctx = StateContext(
                task_id="task-crash",
                issue_id="issue-1",
                workflow_state="ZHONGSHU_ANALYST",
                current_phase="ZHONGSHU",
                current_role="review-analyst",
                expected_agent_id="agent-analyst",
                active_request_id="req-1",
                dispatch_status="confirmed",
                entered_at=datetime.now(timezone.utc).isoformat(),
            )
            app = OrchestratorApp(
                ctx,
                root=root,
                multica=ExplodingMultica(),
                feishu=FakeFeishuAdapter(),
                poll_interval=0,
                timeout_seconds=900,
            )
            with self.assertLogs("review_orchestrator_fsm", level=logging.INFO) as captured:
                ok = app.run()
            self.assertFalse(ok)
            self.assertTrue(any("PROCESS_CRASHED" in line for line in captured.output))
            self.assertTrue(any("PROCESS_EXIT" in line for line in captured.output))
            loaded = JsonStateStore(f"{root}/task-crash").load()
            self.assertEqual(loaded.last_error["code"], "UNHANDLED_EXCEPTION")
            self.assertEqual(loaded.last_error["error_type"], "ValueError")
            self.assertEqual(loaded.workflow_state, "ZHONGSHU_ANALYST")
            self.assertTrue(loaded.recoverable)

    def test_multica_cli_timeout_becomes_runtime_error(self):
        with tempfile.TemporaryDirectory() as root:
            adapter = MulticaCliAdapter(root)
            adapter.cli_timeout_seconds = 0.01
            with patch(
                "orchestrator.adapters.subprocess.run",
                side_effect=subprocess.TimeoutExpired(["multica", "issue"], 0.01),
            ):
                with self.assertLogs("review_orchestrator_fsm", level=logging.INFO) as captured:
                    with self.assertRaises(RuntimeError):
                        adapter._run("issue", "get", "SER-1", "--output", "json")
            self.assertTrue(any("MULTICA_CLI_TIMEOUT" in line for line in captured.output))

    def test_state_elapsed_uses_state_entered_at(self):
        with tempfile.TemporaryDirectory() as root:
            ctx = StateContext(
                task_id="task-elapsed",
                active_request_id="req-1",
                entered_at=datetime.now(timezone.utc).isoformat(),
            )
            app = OrchestratorApp(ctx, root=root, poll_interval=0, timeout_seconds=900)
            self.assertLess(app._state_elapsed_seconds(), 5)

    def test_dispatch_error_event_preserves_reason_in_payload(self):
        with tempfile.TemporaryDirectory() as root:
            ctx = StateContext(
                task_id="task-dispatch-error",
                issue_id="issue-1",
                workflow_state="ZHONGSHU_SOLVER",
                current_phase="ZHONGSHU",
                current_role="review-solver",
                active_request_id="req-1",
                dispatch_status="error",
                last_error={"code": "MULTICA_ERROR", "message": "poll failed"},
            )
            app = OrchestratorApp(ctx, root=root, poll_interval=0, timeout_seconds=900)
            event = app._next_event()
            self.assertEqual(event.name, "MULTICA_ERROR")
            self.assertEqual(event.reason, "poll failed")

    def test_agent_timeout_event_preserves_reason_in_payload(self):
        with tempfile.TemporaryDirectory() as root:
            ctx = StateContext(
                task_id="task-agent-timeout",
                issue_id="issue-1",
                workflow_state="ZHONGSHU_SOLVER",
                current_phase="ZHONGSHU",
                current_role="review-solver",
                active_request_id="req-1",
                dispatch_status="confirmed",
                entered_at="2020-01-01T00:00:00+00:00",
            )
            app = OrchestratorApp(ctx, root=root, poll_interval=0, timeout_seconds=1)
            event = app._next_event()
            self.assertEqual(event.name, "AGENT_TIMEOUT")
            self.assertEqual(event.reason, "agent timeout")


if __name__ == "__main__":
    unittest.main()

