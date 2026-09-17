from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from orchestrator.adapters import MulticaCliAdapter
from orchestrator.structured_output import (
    build_structured_output_spec,
    role_result_template,
)
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


class SolverRevisionReplyPollTests(unittest.TestCase):
    """A delivered-but-unusable reply must never look like a missing result.

    Production incident: the Solver answered a revision with the mode its own
    Skill documents (``..._READ_ONLY_RESUME``) while the dispatch advertised the
    initial mode.  The reply was rejected and dropped without a counter, so the
    effect reported ``AGENT_RESULT_MISSING`` and burned the infrastructure retry
    budget three times before failing the task.
    """

    INITIAL = "TASK_GRAPH_FORMALIZATION_READ_ONLY"
    RESUME = "TASK_GRAPH_FORMALIZATION_READ_ONLY_RESUME"
    FOREIGN = "REVIEW_ONE_TASK"

    def _spec(self):
        spec = build_structured_output_spec(
            "ZHONGSHU", "review-solver", {"active_runtime_state": "ZHONGSHU_SOLVER"}
        )
        assert spec is not None
        return spec

    def _request(self, spec) -> AgentRequest:
        return AgentRequest(
            task_id="task-rev",
            issue_id="SER-1208",
            request_id="req-rev",
            agent_id="solver-agent",
            role="review-solver",
            phase="ZHONGSHU",
            prompt="short prompt",
            idempotency_key="key-rev",
            target_state="ZHONGSHU_SOLVER",
            structured_output=spec.to_dict(),
            context={},
        )

    def _payload(self, spec, mode: str) -> dict:
        return role_result_template(
            "ZHONGSHU",
            "review-solver",
            task_id="task-rev",
            request_id="req-rev",
            state="ZHONGSHU_SOLVER",
            role_mode=mode,
            schema_hash=spec.schema_hash,
            action="READY_FOR_CRITIC",
        )

    def _poll(self, mode: str):
        spec = self._spec()
        request = self._request(spec)
        payload = self._payload(spec, mode)
        with tempfile.TemporaryDirectory() as temporary:
            adapter = MulticaCliAdapter(Path(temporary) / "transport")
            with patch.object(
                adapter,
                "_run_with_read_retry",
                return_value=[{
                    "id": "solver-reply",
                    "author_id": "solver-agent",
                    "created_at": "2026-09-16T10:51:27Z",
                    "content": json.dumps(payload, ensure_ascii=False),
                }],
            ):
                return adapter.poll(request)

    def test_revision_mode_reply_is_delivered_as_a_valid_result(self) -> None:
        messages = self._poll(self.RESUME)

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].payload["action"], "READY_FOR_CRITIC")
        self.assertEqual(messages[0].payload["mode"], self.RESUME)

    def test_undeclared_mode_reply_becomes_a_reply_shape_failure(self) -> None:
        messages = self._poll(self.FOREIGN)

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].payload["action"], "__CONTRACT_REJECTED__")
        self.assertIn("role mode mismatch", messages[0].payload["contract_rejection"])

    def test_rejected_result_file_becomes_a_reply_shape_failure(self) -> None:
        """A file the agent wrote but the validator refused is not a missing result."""

        spec = self._spec()
        request = self._request(spec)
        with tempfile.TemporaryDirectory() as temporary:
            adapter = MulticaCliAdapter(Path(temporary) / "transport")
            result_path = adapter.prompt_bundle_builder.result_path(
                "task-rev", "req-rev"
            )
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_text(
                json.dumps(self._payload(spec, self.FOREIGN)),
                encoding="utf-8",
            )
            with patch.object(adapter, "_run_with_read_retry", return_value=[]):
                messages = adapter.poll(request)

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].payload["action"], "__CONTRACT_REJECTED__")
        self.assertIn("role mode mismatch", messages[0].payload["contract_rejection"])


if __name__ == "__main__":
    unittest.main()
