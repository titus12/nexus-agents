from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from orchestrator.adapters import MulticaCliAdapter
from orchestrator.comment_feed import IncrementalCommentFeed
from orchestrator.models import AgentRequest


class IncrementalCommentFeedTests(unittest.TestCase):
    def test_timestamp_overlap_is_deduplicated_by_comment_id(self) -> None:
        feed = IncrementalCommentFeed(overlap_seconds=2)
        batches = [
            [
                {"id": "c1", "created_at": "2026-09-03T10:00:00Z"},
                {"id": "c2", "created_at": "2026-09-03T10:00:01Z"},
            ],
            [
                {"id": "c1", "created_at": "2026-09-03T10:00:00Z"},
                {"id": "c2", "created_at": "2026-09-03T10:00:01Z"},
                {"id": "c3", "created_at": "2026-09-03T10:00:02Z"},
            ],
        ]
        since_values: list[str] = []

        def fetch(since: str) -> list[dict[str, str]]:
            since_values.append(since)
            return batches.pop(0)

        self.assertEqual([item["id"] for item in feed.read("SER-1:task-1", fetch)], ["c1", "c2"])
        self.assertEqual([item["id"] for item in feed.read("SER-1:task-1", fetch)], ["c3"])
        self.assertEqual(since_values[0], "")
        self.assertEqual(since_values[1], "2026-09-03T09:59:59+00:00")

    def test_scopes_have_independent_cursors(self) -> None:
        feed = IncrementalCommentFeed()
        calls: list[tuple[str, str]] = []

        def fetch_for(scope: str):
            def fetch(since: str):
                calls.append((scope, since))
                return [{"id": scope, "created_at": "2026-09-03T10:00:00Z"}]

            return fetch

        feed.read("SER-1:task-1", fetch_for("a"))
        feed.read("SER-1:task-2", fetch_for("b"))
        self.assertEqual(calls, [("a", ""), ("b", "")])

    def test_cursor_survives_adapter_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state_path = Path(temporary) / "comment-cursor.json"
            first = IncrementalCommentFeed(state_path=state_path)
            first.read(
                "SER-1:task-1",
                lambda _since: [{"id": "c1", "created_at": "2026-09-03T10:00:00Z"}],
            )
            second = IncrementalCommentFeed(state_path=state_path)
            self.assertEqual(
                second.read(
                    "SER-1:task-1",
                    lambda _since: [{"id": "c1", "created_at": "2026-09-03T10:00:00Z"}],
                ),
                [],
            )

    def test_root_recovery_uses_cursor_and_rejects_unbound_reply(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            adapter = MulticaCliAdapter(Path(temporary) / "transport")
            request = AgentRequest(
                task_id="task-1",
                issue_id="SER-1",
                request_id="req-current",
                agent_id="agent-1",
                role="review-analyst",
                phase="ZHONGSHU",
                prompt="do work",
                idempotency_key="key-1",
                sent_after="2026-09-03T10:00:00Z",
                dispatch_external_message_id="dispatch-1",
            )
            calls: list[tuple[str, ...]] = []

            def run(*args: str):
                calls.append(args)
                if "--thread" in args:
                    return [{
                        "id": "thread-old",
                        "author_id": "agent-1",
                        "created_at": "2026-09-03T10:00:01Z",
                        "content": '{"action":"READY_FOR_SOLVER","request_id":"req-old"}',
                    }]
                if args[:2] == ("issue", "runs"):
                    return []
                return [{
                    "id": "root-unbound",
                    "author_id": "agent-1",
                    "created_at": "2026-09-03T10:00:02Z",
                    "content": '{"action":"READY_FOR_SOLVER","request_id":"req-old"}',
                }]

            with patch.object(adapter, "_run", side_effect=run):
                self.assertEqual(adapter.poll(request), [])
            root_calls = [call for call in calls if call[:3] == ("issue", "comment", "list") and "--thread" not in call]
            self.assertTrue(root_calls)
            self.assertIn("--since", root_calls[0])
            self.assertNotIn("100", root_calls[0])

    def test_request_scopes_do_not_hide_each_others_comments(self) -> None:
        feed = IncrementalCommentFeed()
        comments = [
            {"id": "reply-a", "created_at": "2026-09-03T10:00:01Z"},
            {"id": "reply-b", "created_at": "2026-09-03T10:00:02Z"},
        ]
        self.assertEqual(
            [item["id"] for item in feed.read("issue:request-a", lambda _since: comments)],
            ["reply-a", "reply-b"],
        )
        self.assertEqual(
            [item["id"] for item in feed.read("issue:request-b", lambda _since: comments)],
            ["reply-a", "reply-b"],
        )

    def test_mixed_timezone_offsets_use_chronological_cursor_order(self) -> None:
        feed = IncrementalCommentFeed()
        feed.read(
            "issue:request",
            lambda _since: [{"id": "later", "created_at": "2026-09-03T10:00:00+02:00"}],
        )
        since: list[str] = []
        feed.read(
            "issue:request",
            lambda value: since.append(value) or [{"id": "new", "created_at": "2026-09-03T09:30:00Z"}],
        )
        self.assertTrue(since[0].startswith("2026-09-03T09:59:58+02:00"))


if __name__ == "__main__":
    unittest.main()
