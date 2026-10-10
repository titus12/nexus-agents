from __future__ import annotations

import unittest

from orchestrator.domain.context import (
    HumanGateState,
    ProgressState,
    ProgressUpdate,
    TaskIdentity,
    WorkflowContext,
)
from orchestrator.domain.decisions import ContextUpdate, StateDecision
from orchestrator.domain.errors import InvariantViolation
from orchestrator.domain.events import DomainEvent, TransitionRequest
from orchestrator.domain.states import MenxiaGroupCriticState
from orchestrator.runtime.reducer import LinearContextReducer
from orchestrator.runtime.repository import WorkflowSnapshot


class LinearReducerTests(unittest.TestCase):
    def setUp(self) -> None:
        identity = TaskIdentity("task-1", "issue-1", "project-1", "request-1")
        self.snapshot = WorkflowSnapshot(
            "task-1",
            WorkflowContext(
                identity,
                ProgressState("MENXIA_GROUP_SOLVER", 0, "2026-09-09T00:00:00Z"),
            ),
            0,
        )
        self.reducer = LinearContextReducer()

    def test_registered_transition_advances_only_new_snapshot(self) -> None:
        decision = StateDecision(TransitionRequest("READY_FOR_ANALYST", "draft_ready"))

        after = self.reducer.apply(self.snapshot, decision)

        self.assertEqual(after.context.progression.state, "MENXIA_GROUP_ANALYST")
        self.assertEqual(after.context.progression.sequence, 1)
        self.assertEqual(after.state_version, 1)
        self.assertEqual(self.snapshot.context.progression.state, "MENXIA_GROUP_SOLVER")

    def test_progression_update_cannot_bypass_transition_registry(self) -> None:
        decision = StateDecision(
            update=ContextUpdate(
                progression=ProgressUpdate(state="DONE"),
            )
        )

        with self.assertRaises(InvariantViolation):
            self.reducer.apply(self.snapshot, decision)

    def test_human_gate_requires_resume_metadata(self) -> None:
        decision = StateDecision(TransitionRequest("OPEN_HUMAN_GATE", "blocked"))

        with self.assertRaises(InvariantViolation):
            self.reducer.apply(self.snapshot, decision)

    def test_business_state_declares_resume_point_for_system_transition(self) -> None:
        critic = WorkflowSnapshot(
            "task-1",
            WorkflowContext(
                self.snapshot.context.identity,
                ProgressState("MENXIA_GROUP_CRITIC", 0, "now"),
            ),
            0,
        )
        decision = MenxiaGroupCriticState().handle(
            critic.context,
            DomainEvent("OPEN_HUMAN_GATE", "task-1", 0, {}, "now"),
        )

        after = self.reducer.apply(critic, decision)
        self.assertEqual(after.context.progression.state, "HUMAN_GATE")
        self.assertEqual(after.context.progression.resume_state, "MENXIA_GROUP_CRITIC")

    def test_human_gate_metadata_is_preserved_when_event_provides_it(self) -> None:
        critic = WorkflowSnapshot(
            "task-1",
            WorkflowContext(
                self.snapshot.context.identity,
                ProgressState("MENXIA_GROUP_CRITIC", 0, "now"),
            ),
            0,
        )
        decision = MenxiaGroupCriticState().handle(
            critic.context,
            DomainEvent(
                "OPEN_HUMAN_GATE",
                "task-1",
                0,
                {"decision_id": "decision-1", "reason_code": "CRITIC_BLOCKED"},
                "now",
            ),
        )

        after = self.reducer.apply(critic, decision)

        self.assertEqual(after.context.human_gate.decision_id, "decision-1")
        self.assertEqual(after.context.human_gate.reason_code, "CRITIC_BLOCKED")
        self.assertEqual(after.context.human_gate.resume_state, "MENXIA_GROUP_CRITIC")

    def test_resume_uses_recorded_resume_state_and_preserves_gate(self) -> None:
        gated = WorkflowSnapshot(
            "task-1",
            WorkflowContext(
                self.snapshot.context.identity,
                ProgressState("HUMAN_GATE", 1, "now", "MENXIA_GROUP_SOLVER"),
                human_gate=HumanGateState("decision-1", "blocked", "MENXIA_GROUP_SOLVER"),
            ),
            1,
        )

        after = self.reducer.apply(
            gated,
            StateDecision(TransitionRequest("RESUME", "human_decision")),
        )

        self.assertEqual(after.context.progression.state, "MENXIA_GROUP_SOLVER")
        self.assertIsNone(after.context.progression.resume_state)
        self.assertEqual(after.context.human_gate.decision_id, "decision-1")


if __name__ == "__main__":
    unittest.main()
