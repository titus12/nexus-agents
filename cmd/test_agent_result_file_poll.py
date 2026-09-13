from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from orchestrator.adapters import MulticaCliAdapter
from orchestrator.transport.external import AgentRequest


class InlineResultPollTests(unittest.TestCase):
    def _inline_request(self, task_id: str, request_id: str) -> AgentRequest:
        return AgentRequest(
            task_id=task_id,
            issue_id=f"SER-{task_id}",
            request_id=request_id,
            agent_id="agent-inline",
            role="review-analyst",
            phase="ZHONGSHU",
            prompt="short prompt",
            idempotency_key=f"key-{request_id}",
        )

    def test_poll_persists_inline_result_under_orchestrator_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = self._inline_request("task-inline", "req-inline")
            adapter = MulticaCliAdapter(root / "transport")
            payload = {
                "task_id": "task-inline",
                "request_id": "req-inline",
                "phase": "ZHONGSHU",
                "role": "review-analyst",
                "action": "READY_FOR_SOLVER",
                "summary": "complete result",
            }
            with patch.object(
                adapter,
                "_run_with_read_retry",
                return_value=[{
                    "id": "inline-reply",
                    "author_id": "agent-inline",
                    "created_at": "2026-09-02T08:00:00Z",
                    "content": json.dumps(payload, ensure_ascii=False),
                }],
            ):
                messages = adapter.poll(request)

            result_path = adapter.prompt_bundle_builder.result_path(
                "task-inline",
                "req-inline",
            )
            self.assertEqual(len(messages), 1)
            self.assertEqual(messages[0].payload["result_source"], "orchestrator_inline")
            self.assertTrue(result_path.is_file())
            self.assertNotEqual(result_path.read_bytes()[:3], b"\xef\xbb\xbf")
            self.assertEqual(
                json.loads(result_path.read_text(encoding="utf-8"))["summary"],
                "complete result",
            )

    def test_poll_ignores_unstructured_markdown_reply(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = self._inline_request("task-md", "req-md")
            adapter = MulticaCliAdapter(root / "transport")
            with patch.object(
                adapter,
                "_run_with_read_retry",
                return_value=[{
                    "id": "markdown-reply",
                    "author_id": "agent-inline",
                    "created_at": "2026-09-02T08:00:00Z",
                    "content": "## Review\n\nNo structured result here.",
                }],
            ):
                messages = adapter.poll(request)

            self.assertEqual(len(messages), 1)
            self.assertEqual(messages[0].payload["action"], "__UNSTRUCTURED_REPLY__")
            result_path = adapter.prompt_bundle_builder.result_path("task-md", "req-md")
            self.assertFalse(result_path.is_file())

    def test_poll_dedupes_duplicate_inline_results(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = self._inline_request("task-dup", "req-dup")
            adapter = MulticaCliAdapter(root / "transport")
            payload = {
                "task_id": "task-dup",
                "request_id": "req-dup",
                "phase": "ZHONGSHU",
                "role": "review-analyst",
                "action": "READY_FOR_SOLVER",
                "summary": "duplicate",
            }
            content = json.dumps(payload, ensure_ascii=False)

            def comment(comment_id: str) -> dict:
                return {
                    "id": comment_id,
                    "author_id": "agent-inline",
                    "created_at": "2026-09-02T08:00:00Z",
                    "content": content,
                }

            with patch.object(
                adapter,
                "_run_with_read_retry",
                return_value=[comment("c1"), comment("c2")],
            ):
                messages = adapter.poll(request)

            self.assertEqual(len(messages), 1)
            self.assertEqual(messages[0].payload["result_source"], "orchestrator_inline")

    def test_poll_rejects_inline_result_with_wrong_request_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = self._inline_request("task-mismatch", "req-mismatch")
            adapter = MulticaCliAdapter(root / "transport")
            payload = {
                "task_id": "task-mismatch",
                "request_id": "req-other",
                "phase": "ZHONGSHU",
                "role": "review-analyst",
                "action": "READY_FOR_SOLVER",
            }
            with patch.object(
                adapter,
                "_run_with_read_retry",
                return_value=[{
                    "id": "mismatched-reply",
                    "author_id": "agent-inline",
                    "created_at": "2026-09-02T08:00:00Z",
                    "content": json.dumps(payload, ensure_ascii=False),
                }],
            ):
                messages = adapter.poll(request)

            self.assertEqual(messages, [])


if __name__ == "__main__":
    unittest.main()
