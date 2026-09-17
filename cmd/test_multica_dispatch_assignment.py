from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from orchestrator.adapters import MulticaCliAdapter
from orchestrator.transport.external import AgentRequest


def _request(**overrides) -> AgentRequest:
    base = {
        "task_id": "task-1",
        "issue_id": "SER-1",
        "request_id": "task-1:ZHONGSHU_ANALYST:1:req",
        "agent_id": "agent-1",
        "role": "review-analyst",
        "phase": "OTHER",
        "prompt": "analyze",
        "idempotency_key": "task-1:ZHONGSHU_ANALYST:1",
    }
    base.update(overrides)
    return AgentRequest(**base)


class MulticaDispatchAssignmentTests(unittest.TestCase):
    """Dispatch must snapshot runs, assign the agent, then post the request.

    Multica triggers the Agent runtime on issue assignment, so the assignment
    has to happen before the structured comment is posted; the run that the
    assignment fires is later correlated by the run-id watermark rather than by
    a trigger comment id.
    """

    def _adapter(self, directory: str) -> MulticaCliAdapter:
        return MulticaCliAdapter(Path(directory) / "transport")

    def test_dispatch_snapshots_then_assigns_then_posts(self) -> None:
        calls: list[tuple[str, ...]] = []
        with tempfile.TemporaryDirectory() as directory:
            adapter = self._adapter(directory)

            def fake_run(*args, cwd=None):
                calls.append(args)
                if args[:2] == ("issue", "runs"):
                    return {"runs": [{"id": "run-before"}]}
                return {"id": "comment-1"}

            with patch.object(adapter, "_run", side_effect=fake_run):
                receipt = adapter.dispatch(_request())

            self.assertEqual(receipt.external_message_id, "comment-1")
            self.assertEqual(receipt.issue_id, "SER-1")

        self.assertEqual(calls[0][:3], ("issue", "runs", "SER-1"))
        self.assertEqual(
            calls[1][:5],
            ("issue", "update", "SER-1", "--assignee-id", "agent-1"),
        )
        self.assertEqual(calls[2][:4], ("issue", "comment", "add", "SER-1"))
        self.assertEqual(len(calls), 3)

    def test_the_posted_comment_carries_the_structured_dispatch_payload(self) -> None:
        captured: dict[str, str] = {}
        with tempfile.TemporaryDirectory() as directory:
            adapter = self._adapter(directory)

            def fake_run(*args, cwd=None):
                if args[:2] == ("issue", "runs"):
                    return {"runs": []}
                if args[:2] == ("issue", "comment") and "--content-file" in args:
                    path = Path(args[args.index("--content-file") + 1])
                    if not path.is_absolute():
                        # The CLI runs from the adapter's scratch directory.
                        path = adapter.log_dir / path
                    captured["content"] = path.read_text(encoding="utf-8")
                return {"id": "comment-1"}

            with patch.object(adapter, "_run", side_effect=fake_run):
                adapter.dispatch(_request())

        body = json.loads(captured["content"])
        self.assertEqual(
            body["transport"]["request_id"], "task-1:ZHONGSHU_ANALYST:1:req"
        )
        self.assertEqual(
            body["transport"]["reply_correlation_id"],
            "task-1:ZHONGSHU_ANALYST:1:req",
        )
        self.assertEqual(body["transport"]["reply_mode"], "REPLY_TO_TRIGGER_COMMENT")
        self.assertIn("prompt_ref", body)
        self.assertIn("response_contract", body)

    def test_a_zhongshu_role_without_a_skill_binding_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            adapter = self._adapter(directory)

            with self.assertRaises(RuntimeError) as raised:
                adapter.dispatch(_request(phase="ZHONGSHU"))

        self.assertEqual(str(raised.exception), "ACTIVE_RUNTIME_SKILL_BINDING_MISSING")


if __name__ == "__main__":
    unittest.main()
