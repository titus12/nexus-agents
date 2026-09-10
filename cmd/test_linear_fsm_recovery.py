from __future__ import annotations

import tempfile
import unittest

from orchestrator.domain.context import ProgressState, TaskIdentity, WorkflowContext
from orchestrator.domain.decisions import StateDecision
from orchestrator.domain.events import DomainEvent
from orchestrator.runtime.repository import JsonWorkflowRepository, WorkflowSnapshot


class JournalRecoveryTests(unittest.TestCase):
    def test_commit_before_ack_replay_is_filtered_by_source_event_id(self):
        with tempfile.TemporaryDirectory() as directory:
            identity = TaskIdentity("task-recovery", "issue", "", "request")
            before = WorkflowSnapshot(
                "task-recovery",
                WorkflowContext(identity, ProgressState("REQUEST_INTAKE", 0, "t0")),
                0,
            )
            after = WorkflowSnapshot(
                "task-recovery",
                WorkflowContext(identity, ProgressState("ZHONGSHU_ANALYST", 1, "t1")),
                1,
            )
            repository = JsonWorkflowRepository(directory)
            repository.initialize(before)
            repository.commit_transition(
                before,
                after,
                StateDecision(source_event_id="event-1"),
            )
            event = DomainEvent("START", "task-recovery", 0, {}, "t0", "event-1")
            repository.append_domain_event(event)

            self.assertTrue(repository.is_event_processed("event-1"))
            self.assertEqual(repository.pending_domain_events("task-recovery"), ())
            self.assertEqual(len(repository.event_path.read_text(encoding="utf-8").splitlines()), 2)

    def test_duplicate_event_id_is_idempotent_but_conflicting_payload_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = JsonWorkflowRepository(directory)
            event = DomainEvent("START", "task-1", 0, {"a": 1}, "t0", "event-1")
            repository.append_domain_event(event)
            repository.append_domain_event(event)
            self.assertEqual(len(repository.event_path.read_text(encoding="utf-8").splitlines()), 1)
            with self.assertRaises(Exception):
                repository.append_domain_event(
                    DomainEvent("START", "task-1", 0, {"a": 2}, "t0", "event-1")
                )


if __name__ == "__main__":
    unittest.main()
