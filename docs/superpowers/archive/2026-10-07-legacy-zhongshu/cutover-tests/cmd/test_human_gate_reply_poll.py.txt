"""The human gate must actually consume Feishu replies and resume.

Live regression (task-20260921-8f18de seq 18): the operator replied to the
gate message, but ``poll_reply`` had no caller, so the run loop slept forever
and the promised "reply anything to continue" never happened.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ.setdefault("NEXUS_TEST_NO_EXTERNAL_NOTIFICATIONS", "1")

from orchestrator.adapters import FeishuHttpAdapter
from orchestrator.app import OrchestratorApp
from orchestrator.runtime.notification_effects import (
    FeishuNotificationPort,
    NullNotificationPort,
)
from orchestrator.transport.external import HumanGate, HumanReply


def _allow_adapter_poll() -> object:
    """Temporarily lift the offline guard for adapter-level poll tests.

    These tests stub the HTTP layer entirely (see ``PollReplyAdapterTests``),
    so no real Feishu traffic can happen; the offline mode itself is exercised
    by ``NullNotificationPort`` and the process-wide test runner flag.  The
    env patch is scoped to the calling test and restored automatically, which
    avoids any cross-module pollution of ``feishu_notifications_enabled``.
    """

    return mock.patch.dict(
        os.environ,
        {"NEXUS_TEST_NO_EXTERNAL_NOTIFICATIONS": "", "ENABLE_FEISHU_NOTIFICATIONS": "1"},
    )


def _items_payload(items: list[dict]) -> dict:
    return {"code": 0, "data": {"items": items}}


class _FakeGatePort(NullNotificationPort):
    """Notification port whose poll_reply returns queued replies once."""

    def __init__(self, replies: list[HumanReply]) -> None:
        self._replies = list(replies)

    def poll_reply(self, gate: HumanGate) -> list:
        replies, self._replies = self._replies, []
        return replies


class PollReplyAdapterTests(unittest.TestCase):
    """The Feishu adapter locates the gate message by its decision id."""

    def setUp(self) -> None:
        _allow_adapter_poll().start()
        self.addCleanup(mock.patch.stopall)

    def _adapter(self, items: list[dict]) -> FeishuHttpAdapter:
        adapter = FeishuHttpAdapter()
        adapter.chat_id = "chat-1"
        adapter._token = lambda role: "token"  # type: ignore[method-assign]
        adapter._request = lambda *args, **kwargs: _items_payload(items)  # type: ignore[method-assign]
        return adapter

    def test_locates_gate_message_and_consumes_threaded_reply(self) -> None:
        decision = "task-1:human-gate:18"
        adapter = self._adapter([
            {"message_id": "om_old", "parent_id": "", "root_id": "",
             "body": {"content": "【夜罢｜需要人工决策】旧消息 决策编号：task-1:human-gate:16"}},
            {"message_id": "om_gate", "parent_id": "", "root_id": "",
             "body": {"content": f"需要人工决策 决策编号：{decision}"}},
            {"message_id": "om_reply", "parent_id": "om_gate", "root_id": "om_gate",
             "body": {"content": "继续"}},
            {"message_id": "om_noise", "parent_id": "", "root_id": "",
             "body": {"content": "随便的群聊消息"}},
        ])
        gate = HumanGate(decision_id=decision, task_id="task-1", resume_state="ZHONGSHU_CRITIC", prompt="")
        replies = adapter.poll_reply(gate)
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0].answer, "继续")
        self.assertEqual(replies[0].decision_id, decision)

    def test_known_message_id_still_matches_direct_replies(self) -> None:
        adapter = self._adapter([
            {"message_id": "om_gate", "parent_id": "", "root_id": "",
             "body": {"content": "需要人工决策"}},
            {"message_id": "om_reply", "parent_id": "om_gate", "root_id": "om_gate",
             "body": {"content": "通过"}},
        ])
        gate = HumanGate(
            decision_id="task-1:human-gate:18", task_id="task-1",
            resume_state="ZHONGSHU_CRITIC", prompt="", message_id="om_gate",
        )
        replies = adapter.poll_reply(gate)
        self.assertEqual([r.answer for r in replies], ["通过"])

    def test_quoting_the_decision_id_counts_without_threading(self) -> None:
        decision = "task-1:human-gate:18"
        adapter = self._adapter([
            {"message_id": "om_gate", "parent_id": "", "root_id": "",
             "body": {"content": f"需要人工决策 决策编号：{decision}"}},
            {"message_id": "om_flat", "parent_id": "", "root_id": "",
             "body": {"content": f"{decision} 继续"}},
        ])
        gate = HumanGate(decision_id=decision, task_id="task-1", resume_state="X", prompt="")
        replies = adapter.poll_reply(gate)
        self.assertEqual([r.answer for r in replies], ["继续"])


class GatePollResumeTests(unittest.TestCase):
    """The run loop's gate poller turns a Feishu reply into a RESUME event."""

    def _app(self, tmp_root: Path, port) -> OrchestratorApp:
        from orchestrator.domain.context import (
            HumanGateState,
            ProgressState,
            ReviewState,
            TaskIdentity,
            WorkflowContext,
        )
        from orchestrator.runtime.repository import (
            JsonWorkflowRepository,
            WorkflowSnapshot,
        )

        context = WorkflowContext(
            identity=TaskIdentity("task-gate", "issue-1", "project-1", "request-1"),
            progression=ProgressState("HUMAN_GATE", 18, "2026-09-21T17:54:46Z"),
            review=ReviewState(revision_id="rev"),
            human_gate=HumanGateState(
                decision_id="task-gate:human-gate:18",
                reason_code="ZHONGSHU_STUCK_FINDING",
                resume_state="ZHONGSHU_CRITIC",
                message_id=None,
            ),
        )
        repository = JsonWorkflowRepository(tmp_root / "task-gate")
        repository.initialize(WorkflowSnapshot(
            task_id="task-gate", state_version=0, context=context,
        ))
        return OrchestratorApp(
            context,
            str(tmp_root),
            multica=type("M", (), {"get_issue": lambda *a, **k: {}, "create_issue": lambda *a, **k: "i"})(),
            notification_port=port,
            poll_interval=0,
        )

    def test_reply_is_consumed_into_a_resume_event(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            app = self._app(
                Path(tmp),
                _FakeGatePort([HumanReply("task-gate:human-gate:18", "继续")]),
            )
            app._last_gate_poll = 0.0
            app._gate_poll_interval = 0.0
            app._poll_human_gate_reply("task-gate")
            pending = app.repository.pending_domain_events("task-gate")
            self.assertEqual([event.name for event in pending], ["RESUME"])
            self.assertEqual(pending[0].payload.get("answer"), "继续")

    def test_empty_poll_publishes_nothing(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            app = self._app(Path(tmp), _FakeGatePort([]))
            app._last_gate_poll = 0.0
            app._gate_poll_interval = 0.0
            app._poll_human_gate_reply("task-gate")
            self.assertEqual(app.repository.pending_domain_events("task-gate"), ())

    def test_rate_limit_skips_the_immediate_next_poll(self) -> None:
        import tempfile
        import time as time_module

        with tempfile.TemporaryDirectory() as tmp:
            app = self._app(
                Path(tmp),
                _FakeGatePort([HumanReply("task-gate:human-gate:18", "继续")]),
            )
            app._gate_poll_interval = 3600.0
            app._last_gate_poll = time_module.monotonic()
            app._poll_human_gate_reply("task-gate")
            self.assertEqual(app.repository.pending_domain_events("task-gate"), ())

    def test_port_without_poll_reply_is_ignored(self) -> None:
        import tempfile

        class SendOnlyPort(NullNotificationPort):
            poll_reply = None  # type: ignore[assignment]

        with tempfile.TemporaryDirectory() as tmp:
            app = self._app(Path(tmp), SendOnlyPort())
            app._last_gate_poll = 0.0
            app._gate_poll_interval = 0.0
            app._poll_human_gate_reply("task-gate")
            self.assertEqual(app.repository.pending_domain_events("task-gate"), ())


class PortWiringTests(unittest.TestCase):
    """Both notification ports expose the gate reply poller."""

    def test_null_port_returns_no_replies(self) -> None:
        gate = HumanGate(decision_id="d", task_id="t", resume_state="", prompt="")
        self.assertEqual(NullNotificationPort().poll_reply(gate), [])

    def test_feishu_port_delegates_to_the_adapter(self) -> None:
        class StubAdapter:
            def __init__(self) -> None:
                self.seen: HumanGate | None = None

            def send_gate(self, gate: HumanGate):
                raise AssertionError("not used here")

            def poll_reply(self, gate: HumanGate):
                self.seen = gate
                return [HumanReply(gate.decision_id, "ok")]

        stub = StubAdapter()
        gate = HumanGate(decision_id="d", task_id="t", resume_state="", prompt="")
        replies = FeishuNotificationPort(stub).poll_reply(gate)
        self.assertEqual([r.answer for r in replies], ["ok"])
        self.assertIs(stub.seen, gate)


if __name__ == "__main__":
    unittest.main()
