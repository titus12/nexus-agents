from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from orchestrator.adapters import MulticaCliAdapter
from orchestrator.domain.errors import is_runtime_failure_notice
from orchestrator.transport.external import AgentRequest

WATCHDOG = (
    "agent produced no new messages for 10m0s and message queue was empty; "
    "force-stopped by idle watchdog"
)
CRASH = "reasonix ended the prompt with stopReason=error"
API_400 = json.dumps(
    {
        "type": "error",
        "status": 400,
        "error": {
            "type": "invalid_request_error",
            "message": "The 'gpt-5.6-luna' model is not supported.",
        },
    }
)


class RuntimeNoticeClassifierTests(unittest.TestCase):
    def test_known_runtime_notices_are_recognised(self) -> None:
        for body in (WATCHDOG, CRASH, API_400, f"\n{CRASH}\n"):
            with self.subTest(body=body[:40]):
                self.assertTrue(is_runtime_failure_notice(body))

    def test_real_replies_are_not_notices(self) -> None:
        broken_reply = '{"action": "READY_FOR_SOLVER", "summary": "x" "y": 1}'
        for body in (
            "## Review\n\nNo structured result here.",
            '{"action": "READY_FOR_SOLVER", "summary": "stopReason=error"}',
            broken_reply,
            "",
            None,
        ):
            with self.subTest(body=str(body)[:40]):
                self.assertFalse(is_runtime_failure_notice(body))

    def test_long_body_mentioning_marker_is_not_a_notice(self) -> None:
        body = "分析报告 " * 400 + " stopReason=error appears in the log"
        self.assertFalse(is_runtime_failure_notice(body))


class RuntimeNoticePollTests(unittest.TestCase):
    def _request(self) -> AgentRequest:
        return AgentRequest(
            task_id="task-notice",
            issue_id="SER-task-notice",
            request_id="req-notice",
            agent_id="agent-notice",
            role="review-solver",
            phase="ZHONGSHU",
            prompt="short prompt",
            idempotency_key="key-req-notice",
        )

    def _poll(self, adapter: MulticaCliAdapter, content: str):
        with patch.object(
            adapter,
            "_run_with_read_retry",
            return_value=[{
                "id": "reply-1",
                "author_id": "agent-notice",
                "created_at": "2026-09-30T08:00:00Z",
                "content": content,
            }],
        ):
            return adapter.poll(self._request())

    def test_runtime_notice_is_not_reported_as_unstructured_reply(self) -> None:
        for body in (WATCHDOG, CRASH, API_400):
            with self.subTest(body=body[:40]), tempfile.TemporaryDirectory() as temporary:
                adapter = MulticaCliAdapter(Path(temporary) / "transport")

                self.assertEqual(self._poll(adapter, body), [])

    def test_unparseable_reply_is_kept_in_full(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            adapter = MulticaCliAdapter(Path(temporary) / "transport")
            body = '{"action": "READY_FOR_SOLVER", "summary": "' + "长" * 900 + '" "x": 1}'

            messages = self._poll(adapter, body)

            self.assertEqual(len(messages), 1)
            payload = messages[0].payload
            self.assertEqual(payload["action"], "__UNSTRUCTURED_REPLY__")
            self.assertEqual(len(payload["raw_reply"]), 500)
            self.assertTrue(payload["raw_reply_truncated"])
            saved = Path(payload["raw_reply_path"])
            self.assertTrue(saved.is_file())
            self.assertEqual(saved.read_text(encoding="utf-8"), body)


if __name__ == "__main__":
    unittest.main()
