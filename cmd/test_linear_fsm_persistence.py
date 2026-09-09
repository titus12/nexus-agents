from __future__ import annotations

import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from orchestrator.domain.context import ProgressState, TaskIdentity, WorkflowContext
from orchestrator.domain.decisions import EffectRequest, StateDecision
from orchestrator.domain.errors import PersistenceError
from orchestrator.runtime.repository import (
    EffectResult,
    JsonWorkflowRepository,
    WorkflowSnapshot,
)


class JournalFirstRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_root = Path(__file__).parent / "test-runs"
        self._temp_root.mkdir(parents=True, exist_ok=True)
        # Windows test sandboxes may deny chmod/rmtree on temporary folders.
        # Use a unique workspace-local directory and leave it for the existing
        # test-artifact cleanup convention instead of hanging in teardown.
        self.root = self._temp_root / f"repo-{uuid.uuid4().hex}"
        self.root.mkdir(parents=True, exist_ok=True)
        self.repository = JsonWorkflowRepository(self.root)
        self.before = self.snapshot("REQUEST_INTAKE", 0, 0)
        self.after = self.snapshot("ZHONGSHU_ANALYST", 1, 1)
        self.repository.initialize(self.before)

    @staticmethod
    def snapshot(state: str, sequence: int, state_version: int) -> WorkflowSnapshot:
        identity = TaskIdentity(
            task_id="task-1",
            issue_id="issue-1",
            project="project-1",
            request_id="request-1",
        )
        return WorkflowSnapshot(
            task_id="task-1",
            context=WorkflowContext(
                identity=identity,
                progression=ProgressState(
                    state=state,
                    sequence=sequence,
                    entered_at="2026-09-09T00:00:00Z",
                    resume_state=("ZHONGSHU_ANALYST" if state == "PERSISTENCE_DEGRADED" else None),
                ),
            ),
            state_version=state_version,
        )

    def test_journal_append_failure_cannot_advance_snapshot(self) -> None:
        with patch.object(
            self.repository,
            "_append_record",
            side_effect=PersistenceError("injected journal failure"),
        ):
            with self.assertRaises(PersistenceError) as raised:
                self.repository.commit_transition(self.before, self.after, StateDecision())

        self.assertIsNotNone(raised.exception.failure)
        self.assertEqual(raised.exception.failure.stage, "persistence")
        self.assertEqual(raised.exception.failure.task_id, "task-1")
        self.assertEqual(self.repository.load("task-1"), self.before)
        self.assertFalse(self.repository.event_path.exists())

    def test_snapshot_replacement_failure_recovers_from_journal(self) -> None:
        with patch.object(
            self.repository,
            "_atomic_write",
            side_effect=[PersistenceError("injected snapshot failure")],
        ):
            with self.assertRaises(PersistenceError):
                self.repository.commit_transition(self.before, self.after, StateDecision())

        recovered = JsonWorkflowRepository(self.root).load("task-1")
        self.assertEqual(recovered, self.after)

    def test_duplicate_transition_is_idempotent(self) -> None:
        first = self.repository.commit_transition(self.before, self.after, StateDecision())
        second = self.repository.commit_transition(self.before, self.after, StateDecision())

        self.assertEqual(second, first)
        self.assertEqual(len(self.repository.event_path.read_text(encoding="utf-8").splitlines()), 1)

    def test_same_transition_id_with_different_content_is_rejected(self) -> None:
        self.repository.commit_transition(self.before, self.after, StateDecision())
        conflicting_after = self.snapshot("ZHONGSHU_SOLVER", 1, 1)

        with self.assertRaises(PersistenceError):
            self.repository.commit_transition(
                self.before,
                conflicting_after,
                StateDecision(),
            )

    def test_pending_effect_survives_restart_and_result_is_terminal(self) -> None:
        effect = EffectRequest(
            effect_id="effect-1",
            effect_type="dispatch_agent",
            task_id="task-1",
            idempotency_key="task-1:dispatch:1",
        )
        self.repository.commit_transition(
            self.before,
            self.after,
            StateDecision(effects=(effect,)),
        )
        restarted = JsonWorkflowRepository(self.root)
        pending = restarted.pending_effects("task-1")
        self.assertEqual([item.effect_id for item in pending], ["effect-1"])
        restarted.mark_effect_running("effect-1")
        self.assertEqual(restarted.pending_effects("task-1")[0].status, "RUNNING")
        restarted.commit_effect_result(EffectResult("effect-1", "task-1", "SUCCEEDED"))
        self.assertEqual(restarted.pending_effects("task-1"), ())

    def test_malformed_middle_journal_record_fails_closed(self) -> None:
        self.repository.commit_transition(self.before, self.after, StateDecision())
        valid_line = self.repository.event_path.read_text(encoding="utf-8").strip()
        self.repository.event_path.write_text(
            valid_line + "\n{not-json}\n" + valid_line + "\n",
            encoding="utf-8",
        )

        with self.assertRaises(PersistenceError):
            self.repository.load("task-1")

    def test_persistence_degraded_context_round_trips_resume_state(self) -> None:
        degraded = self.snapshot("PERSISTENCE_DEGRADED", 1, 1)
        repository = JsonWorkflowRepository(
            self._temp_root / f"repo-degraded-{uuid.uuid4().hex}"
        )
        repository.initialize(degraded)

        loaded = repository.load("task-1")
        self.assertEqual(loaded.context.progression.state, "PERSISTENCE_DEGRADED")
        self.assertEqual(loaded.context.progression.resume_state, "ZHONGSHU_ANALYST")
        with self.assertRaises(AttributeError):
            loaded.context.progression.state = "DONE"  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
