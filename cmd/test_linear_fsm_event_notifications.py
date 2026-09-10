from __future__ import annotations

import unittest

from orchestrator.domain.context import (
    ProgressState,
    RequestState,
    TaskIdentity,
    WorkflowContext,
)
from orchestrator.domain.events import DomainEvent
from orchestrator.notifications import DirectNotificationEmitter
from orchestrator.runtime.ports import NotificationReceipt
from orchestrator.runtime.engine import WorkflowEngine
from orchestrator.runtime.reducer import LinearContextReducer
from orchestrator.runtime.repository import CommitResult, WorkflowSnapshot
from orchestrator.domain.states import StateRegistry


def context(state: str, sequence: int) -> WorkflowContext:
    return WorkflowContext(
        identity=TaskIdentity("task-1", "issue-1", "project", "request-1"),
        progression=ProgressState(state, sequence, "2026-09-10T00:00:00+00:00"),
        request=RequestState(raw_request="检查当前项目"),
    )


class RecordingPort:
    def __init__(self) -> None:
        self.requests = []

    def send(self, request):
        self.requests.append(request)
        return NotificationReceipt(request.notification_key, delivered=True)


class Repository:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.decision = None

    def load(self, task_id):
        return self.snapshot

    def is_event_processed(self, event_id):
        return False

    def commit_transition(self, before, after, decision):
        self.snapshot = after
        self.decision = decision
        return CommitResult("transition-1", after, ())


class Lock:
    def acquire(self, task_id):
        return None

    def release(self, task_id):
        return None


class DirectEventNotificationTests(unittest.TestCase):
    def test_start_transition_sends_state_entry_directly(self):
        port = RecordingPort()
        emitter = DirectNotificationEmitter(port)
        keys = emitter.emit(
            context("REQUEST_INTAKE", 0),
            DomainEvent("START", "task-1", 0, {"raw_request": "检查当前项目"}, "now"),
            context("ZHONGSHU_ANALYST", 1),
        )
        self.assertEqual(len(port.requests), 1)
        self.assertIn("task-1", port.requests[0].body)
        self.assertIn("ZHONGSHU_ANALYST", port.requests[0].body)
        self.assertEqual(keys, ("task-1:1:state-enter:STATE_ENTER",))

    def test_agent_action_sends_reply_and_next_state_entry(self):
        port = RecordingPort()
        emitter = DirectNotificationEmitter(port)
        keys = emitter.emit(
            context("ZHONGSHU_ANALYST", 1),
            DomainEvent("READY_FOR_SOLVER", "task-1", 1, {"action": "READY_FOR_SOLVER", "summary": "证据齐全"}, "now"),
            context("ZHONGSHU_SOLVER", 2),
        )
        self.assertEqual(len(port.requests), 2)
        self.assertEqual(len(keys), 2)
        self.assertIn("ZHONGSHU_ANALYST", port.requests[0].body)
        self.assertIn("ZHONGSHU_SOLVER", port.requests[1].body)

    def test_hidden_event_does_not_send(self):
        port = RecordingPort()
        emitter = DirectNotificationEmitter(port)
        keys = emitter.emit(
            context("ZHONGSHU_ANALYST", 1),
            DomainEvent("DISPATCH_END", "task-1", 1, {}, "now"),
            context("ZHONGSHU_ANALYST", 1),
        )
        self.assertEqual(keys, ())
        self.assertEqual(port.requests, [])

    def test_existing_audit_key_is_not_sent_again(self):
        port = RecordingPort()
        emitter = DirectNotificationEmitter(port)
        before = context("REQUEST_INTAKE", 0)
        after = context("ZHONGSHU_ANALYST", 1)
        after = WorkflowContext(
            after.identity,
            after.progression,
            request=after.request,
            audit=after.audit.__class__(sent_notification_keys=("task-1:1:state-enter:STATE_ENTER",)),
        )
        keys = emitter.emit(before, DomainEvent("START", "task-1", 0, {}, "now"), after)
        self.assertEqual(keys, ())
        self.assertEqual(port.requests, [])

    def test_engine_persists_direct_notification_keys_in_transition(self):
        port = RecordingPort()
        initial = WorkflowSnapshot("task-1", context("REQUEST_INTAKE", 0), 0)
        repository = Repository(initial)
        engine = WorkflowEngine(
            repository,
            StateRegistry.default(),
            Lock(),
            LinearContextReducer(),
            DirectNotificationEmitter(port),
        )
        engine.dispatch(DomainEvent("START", "task-1", 0, {}, "now", "event-1"))
        self.assertEqual(len(port.requests), 1)
        self.assertIn(
            "task-1:1:state-enter:STATE_ENTER",
            repository.snapshot.context.audit.sent_notification_keys,
        )


if __name__ == "__main__":
    unittest.main()
