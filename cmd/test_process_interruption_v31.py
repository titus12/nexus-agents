from __future__ import annotations

import tempfile
import unittest

from orchestrator.app import OrchestratorApp
from orchestrator.context import StateContext
from orchestrator.persistence import JsonStateStore


class ProcessInterruptionTests(unittest.TestCase):
    def test_interruption_is_persisted_without_marking_task_corrupted(self):
        with tempfile.TemporaryDirectory() as root:
            ctx = StateContext(
                task_id="task-1",
                workflow_state="ZHONGSHU_ANALYST",
                active_request_id="request-1",
            )
            app = OrchestratorApp(ctx, root=root, poll_interval=0)
            app.record_interruption("operator interrupted process")
            loaded = JsonStateStore(root + "/task-1").load()
            self.assertEqual(loaded.last_error["code"], "PROCESS_INTERRUPTED")
            self.assertEqual(loaded.workflow_state, "ZHONGSHU_ANALYST")
            self.assertTrue(loaded.recoverable)


if __name__ == "__main__":
    unittest.main()
