from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from orchestrator.adapters import MulticaCliAdapter
from orchestrator.models import AgentRequest
from orchestrator.models import AgentBinding, ExternalMessage
from orchestrator.validators import validate_agent_reply, ValidReply


class MulticaPollFilterTests(unittest.TestCase):
    def test_dispatch_comment_without_action_is_not_agent_reply(self):
        adapter = MulticaCliAdapter("test-transport")
        request = AgentRequest(
            task_id="task-1",
            issue_id="SER-1",
            request_id="task-1:ZHONGSHU_ANALYST:1:req",
            agent_id="agent-1",
            role="review-analyst",
            phase="ZHONGSHU",
            prompt="do work",
            idempotency_key="task-1:ZHONGSHU_ANALYST:1",
        )
        comments = [{
            "id": "dispatch-1",
            "author_id": "orchestrator-member",
            "content": (
                '{"task_id":"task-1","request_id":"task-1:ZHONGSHU_ANALYST:1:req",'
                '"role":"review-analyst","phase":"ZHONGSHU","prompt":"do work",'
                '"idempotency_key":"task-1:ZHONGSHU_ANALYST:1"}'
            ),
        }]
        with patch.object(adapter, "_run", return_value=comments):
            self.assertEqual(adapter.poll(request), [])

    def test_agent_reply_with_action_is_returned(self):
        adapter = MulticaCliAdapter("test-transport")
        request = AgentRequest(
            task_id="task-1",
            issue_id="SER-1",
            request_id="task-1:ZHONGSHU_ANALYST:1:req",
            agent_id="agent-1",
            role="review-analyst",
            phase="ZHONGSHU",
            prompt="do work",
            idempotency_key="task-1:ZHONGSHU_ANALYST:1",
        )
        comments = [{
            "id": "reply-1",
            "author_id": "agent-1",
            "content": (
                '{"task_id":"task-1","request_id":"task-1:ZHONGSHU_ANALYST:1:req",'
                '"role":"review-analyst","phase":"ZHONGSHU","action":"READY_FOR_SOLVER"}'
            ),
        }]
        with patch.object(adapter, "_run", return_value=comments):
            replies = adapter.poll(request)
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0].payload["action"], "READY_FOR_SOLVER")

    def test_poll_targets_dispatch_thread_instead_of_full_issue_history(self):
        adapter = MulticaCliAdapter("test-transport")
        request = AgentRequest(
            task_id="task-1",
            issue_id="SER-1",
            request_id="task-1:ZHONGSHU_SOLVER:2:req",
            agent_id="agent-1",
            role="review-solver",
            phase="ZHONGSHU",
            prompt="do work",
            idempotency_key="task-1:ZHONGSHU_SOLVER:2",
            sent_after="2026-08-26T03:24:47Z",
            dispatch_external_message_id="dispatch-1",
        )
        with patch.object(adapter, "_run", side_effect=[[], []]) as run:
            self.assertEqual(adapter.poll(request), [])
        self.assertEqual(run.call_args_list[0].args, (
            "issue",
            "comment",
            "list",
            "SER-1",
            "--thread",
            "dispatch-1",
            "--tail",
            "30",
            "--since",
            "2026-08-26T03:24:47Z",
            "--output",
            "json",
        ))
        self.assertEqual(run.call_args_list[1].args, (
            "issue", "runs", "SER-1", "--output", "json"
        ))

    def test_poll_retries_once_after_read_timeout(self):
        adapter = MulticaCliAdapter("test-transport")
        request = AgentRequest(
            task_id="task-1",
            issue_id="SER-1",
            request_id="task-1:ZHONGSHU_ANALYST:1:req",
            agent_id="agent-1",
            role="review-analyst",
            phase="ZHONGSHU",
            prompt="do work",
            idempotency_key="task-1:ZHONGSHU_ANALYST:1",
            dispatch_external_message_id="dispatch-1",
        )
        timeout = RuntimeError("multica CLI timed out after 60s: multica issue comment list")
        with patch.object(adapter, "_run", side_effect=[timeout, [], []]) as run:
            with patch("orchestrator.adapters.time.sleep") as sleep:
                with self.assertLogs("review_orchestrator_fsm", level="WARNING") as captured:
                    self.assertEqual(adapter.poll(request), [])
        self.assertEqual(run.call_count, 3)
        sleep.assert_called_once_with(2)
        self.assertIn("MULTICA_CLI_RETRY", "\n".join(captured.output))

    def test_read_and_write_commands_use_separate_timeouts(self):
        with patch.dict(
            "os.environ",
            {
                "MULTICA_CLI_TIMEOUT_SEC": "30",
                "MULTICA_CLI_READ_TIMEOUT_SEC": "60",
            },
            clear=True,
        ):
            adapter = MulticaCliAdapter("test-transport")
            completed = SimpleNamespace(returncode=0, stdout="[]", stderr="")
            with patch("orchestrator.adapters.subprocess.run", return_value=completed) as run:
                adapter._run("issue", "comment", "list", "SER-1", "--output", "json")
                adapter._run("issue", "update", "SER-1", "--assignee-id", "agent-1", "--output", "json")
        self.assertEqual(run.call_args_list[0].kwargs["timeout"], 60.0)
        self.assertEqual(run.call_args_list[1].kwargs["timeout"], 30.0)

    def test_missing_role_and_phase_are_inferred_after_strong_binding(self):
        result = validate_agent_reply(
            ExternalMessage(
                "agent-1",
                {
                    "task_id": "task-1",
                    "request_id": "req-1",
                    "action": "READY_FOR_SOLVER",
                },
            ),
            AgentBinding(
                author_id="agent-1",
                task_id="task-1",
                request_id="req-1",
                role="review-analyst",
                phase="ZHONGSHU",
            ),
            {"READY_FOR_SOLVER"},
        )
        self.assertIsInstance(result, ValidReply)
        self.assertEqual(result.payload["role"], "review-analyst")
        self.assertEqual(result.payload["phase"], "ZHONGSHU")

    def test_explicit_wrong_role_is_still_rejected(self):
        result = validate_agent_reply(
            ExternalMessage(
                "agent-1",
                {
                    "task_id": "task-1",
                    "request_id": "req-1",
                    "role": "review-critic",
                    "phase": "ZHONGSHU",
                    "action": "READY_FOR_SOLVER",
                },
            ),
            AgentBinding(
                author_id="agent-1",
                task_id="task-1",
                request_id="req-1",
                role="review-analyst",
                phase="ZHONGSHU",
            ),
            {"READY_FOR_SOLVER"},
        )
        self.assertEqual(getattr(result, "reason", ""), "ROLE_MISMATCH")

    def test_unbound_agent_report_is_attached_to_bound_structured_reply(self):
        adapter = MulticaCliAdapter("test-transport")
        request = AgentRequest(
            task_id="task-1",
            issue_id="SER-1",
            request_id="task-1:ZHONGSHU_ANALYST:1:req",
            agent_id="agent-1",
            role="review-analyst",
            phase="ZHONGSHU",
            prompt="do work",
            idempotency_key="task-1:ZHONGSHU_ANALYST:1",
        )
        comments = [
            {
                "id": "report-1",
                "author_id": "agent-1",
                "created_at": "2026-08-24T10:00:01Z",
                "content": "完整分析：发现五类知识库覆盖问题。",
            },
            {
                "id": "reply-1",
                "author_id": "agent-1",
                "created_at": "2026-08-24T10:00:02Z",
                "content": (
                    '{"action":"READY_FOR_SOLVER","task_id":"task-1",'
                    '"request_id":"task-1:ZHONGSHU_ANALYST:1:req"}'
                ),
            },
        ]
        with patch.object(adapter, "_run", return_value=comments):
            replies = adapter.poll(request)
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0].payload["action"], "READY_FOR_SOLVER")
        self.assertEqual(replies[0].payload["supplemental_reports"], ["完整分析：发现五类知识库覆盖问题。"])


class MulticaExecutionCorrelationTests(unittest.TestCase):
    def test_root_reply_is_recovered_only_from_matching_execution(self):
        class StubAdapter(MulticaCliAdapter):
            def __init__(self):
                super().__init__(log_dir="test-transport-correlation")
                self.calls = []

            def _run(self, *args):
                self.calls.append(args)
                if args[:3] == ("issue", "comment", "list") and "--thread" in args:
                    return []
                if args[:2] == ("issue", "runs"):
                    return [{
                        "status": "completed",
                        "trigger_comment_id": "dispatch-1",
                        "delivered_comment_ids": ["root-reply-1"],
                    }]
                if args[:3] == ("issue", "comment", "list"):
                    return [
                        {
                            "id": "root-reply-1",
                            "author_id": "agent-1",
                            "created_at": "2026-08-27T03:35:25Z",
                            "content": '{"action":"READY_FOR_SOLVER","plan":{}}',
                        },
                        {
                            "id": "unrelated-root",
                            "author_id": "agent-1",
                            "created_at": "2026-08-27T03:35:26Z",
                            "content": '{"action":"READY_FOR_SOLVER","plan":{"wrong":true}}',
                        },
                    ]
                raise AssertionError(args)

        adapter = StubAdapter()
        request = AgentRequest(
            task_id="task-1",
            issue_id="SER-1",
            request_id="task-1:ZHONGSHU_ANALYST:1:req",
            agent_id="agent-1",
            role="review-analyst",
            phase="ZHONGSHU",
            prompt="do work",
            idempotency_key="task-1:ZHONGSHU_ANALYST:1",
            sent_after="2026-08-27T03:35:00Z",
            dispatch_external_message_id="dispatch-1",
        )
        replies = adapter.poll(request)
        self.assertEqual([reply.external_id for reply in replies], ["root-reply-1"])
        self.assertIn(("issue", "runs", "SER-1", "--output", "json"), adapter.calls)

    def test_uncorrelated_root_reply_is_ignored(self):
        class StubAdapter(MulticaCliAdapter):
            def __init__(self):
                super().__init__(log_dir="test-transport-correlation")

            def _run(self, *args):
                if args[:3] == ("issue", "comment", "list") and "--thread" in args:
                    return []
                if args[:2] == ("issue", "runs"):
                    return [{
                        "status": "completed",
                        "trigger_comment_id": "dispatch-1",
                        "delivered_comment_ids": ["different-reply"],
                    }]
                if args[:3] == ("issue", "comment", "list"):
                    return [{
                        "id": "unrelated-root",
                        "author_id": "agent-1",
                        "created_at": "2026-08-27T03:35:25Z",
                        "content": '{"action":"READY_FOR_SOLVER","plan":{}}',
                    }]
                raise AssertionError(args)

        adapter = StubAdapter()
        request = AgentRequest(
            task_id="task-1",
            issue_id="SER-1",
            request_id="task-1:ZHONGSHU_ANALYST:1:req",
            agent_id="agent-1",
            role="review-analyst",
            phase="ZHONGSHU",
            prompt="do work",
            idempotency_key="task-1:ZHONGSHU_ANALYST:1",
            dispatch_external_message_id="dispatch-1",
        )
        self.assertEqual(adapter.poll(request), [])


if __name__ == "__main__":
    unittest.main()
