from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from orchestrator.adapters import MulticaCliAdapter
from orchestrator.transport.external import AgentRequest


class RunWatermarkCorrelationTests(unittest.TestCase):
    """Assignment-triggered runs carry no trigger_comment_id.

    They must be correlated by run-id position, never by comparing the
    orchestrator's sub-second ``sent_after`` with Multica's
    second-granularity ``created_at``.
    """

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._temporary.cleanup)
        self.log_dir = Path(self._temporary.name)

    def _adapter(self) -> MulticaCliAdapter:
        return MulticaCliAdapter(self.log_dir)

    @staticmethod
    def _request(**overrides) -> AgentRequest:
        values = {
            "task_id": "task-1",
            "issue_id": "SER-1",
            "request_id": "task-1:ZHONGSHU_SOLVER:4",
            "agent_id": "solver-agent",
            "role": "review-solver",
            "phase": "ZHONGSHU",
            "prompt": "solve",
            "idempotency_key": "task-1:ZHONGSHU_SOLVER:4",
            "sent_after": "2026-09-11T08:57:00.610688+00:00",
        }
        values.update(overrides)
        return AgentRequest(**values)

    def test_assignment_run_is_matched_by_run_id_delta(self):
        adapter = self._adapter()
        adapter._run_watermarks["task-1:ZHONGSHU_SOLVER:4"] = ["analyst-run-1"]
        runs = [
            {
                "id": "solver-run-1",
                "agent_id": "solver-agent",
                "kind": "direct",
                "status": "running",
                "created_at": "2026-09-11T08:57:00Z",
            }
        ]

        with patch.object(adapter, "_run_with_read_retry", return_value=runs):
            self.assertEqual(adapter.get_run_status(self._request()), "running")

    def test_stale_run_not_stolen_while_dispatch_pending(self):
        adapter = self._adapter()
        adapter._run_watermarks["task-1:ZHONGSHU_SOLVER:4"] = ["analyst-run-1"]
        runs = [
            {
                "id": "analyst-run-1",
                "agent_id": "solver-agent",
                "kind": "direct",
                "status": "completed",
                "created_at": "2026-09-11T08:50:00Z",
            }
        ]

        with patch.object(adapter, "_run_with_read_retry", return_value=runs):
            self.assertEqual(adapter.get_run_status(self._request()), "unknown")

    def test_comment_run_is_not_claimed_by_watermark(self):
        adapter = self._adapter()
        adapter._run_watermarks["task-1:ZHONGSHU_SOLVER:4"] = []
        runs = [
            {
                "id": "comment-run-1",
                "agent_id": "solver-agent",
                "kind": "comment",
                "status": "running",
                "created_at": "2026-09-11T08:50:00Z",
            }
        ]

        with patch.object(adapter, "_run_with_read_retry", return_value=runs):
            self.assertEqual(adapter.get_run_status(self._request()), "unknown")

    def test_assignment_run_detection(self):
        self.assertTrue(MulticaCliAdapter._is_assignment_run({"kind": "direct"}))
        self.assertTrue(
            MulticaCliAdapter._is_assignment_run(
                {"attribution": {"evidence": {"kind": "issue_assignment"}}}
            )
        )
        self.assertFalse(MulticaCliAdapter._is_assignment_run({"kind": "comment"}))

    def test_exact_trigger_match_wins_over_watermark(self):
        adapter = self._adapter()
        adapter._run_watermarks["task-1:ZHONGSHU_SOLVER:4"] = []
        runs = [
            {
                "id": "comment-run-1",
                "agent_id": "analyst-agent",
                "status": "completed",
                "trigger_comment_id": "cmt-1",
                "created_at": "2026-09-11T08:57:00Z",
            }
        ]
        request = self._request(dispatch_external_message_id="cmt-1")

        with patch.object(adapter, "_run_with_read_retry", return_value=runs):
            self.assertEqual(adapter.get_run_status(request), "completed")

    def test_restart_fallback_tolerates_second_truncation(self):
        adapter = self._adapter()
        runs = [
            {
                "id": "solver-run-1",
                "agent_id": "solver-agent",
                "status": "running",
                "created_at": "2026-09-11T08:57:00Z",
            }
        ]

        with patch.object(adapter, "_run_with_read_retry", return_value=runs):
            self.assertEqual(adapter.get_run_status(self._request()), "running")

    def test_watermark_is_captured_and_persisted(self):
        adapter = self._adapter()
        existing = [{"id": "run-a"}, {"id": "run-b"}]

        with patch.object(adapter, "_run_with_read_retry", return_value=existing):
            adapter._capture_run_watermark("SER-1", "task-1:ZHONGSHU_SOLVER:4")

        self.assertEqual(
            set(adapter._run_watermarks["task-1:ZHONGSHU_SOLVER:4"]),
            {"run-a", "run-b"},
        )

        reloaded = MulticaCliAdapter(self.log_dir)
        self.assertEqual(
            set(reloaded._run_watermarks["task-1:ZHONGSHU_SOLVER:4"]),
            {"run-a", "run-b"},
        )

    def test_failed_watermark_lookup_does_not_block_dispatch(self):
        adapter = self._adapter()

        with patch.object(
            adapter,
            "_run_with_read_retry",
            side_effect=RuntimeError("temporary read failure"),
        ):
            adapter._capture_run_watermark("SER-1", "task-1:ZHONGSHU_SOLVER:4")

        self.assertNotIn("task-1:ZHONGSHU_SOLVER:4", adapter._run_watermarks)


if __name__ == "__main__":
    unittest.main()
