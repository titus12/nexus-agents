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
from orchestrator.domain.states import ZhongshuCriticState
from orchestrator.runtime.reducer import LinearContextReducer
from orchestrator.runtime.repository import WorkflowSnapshot


class LinearReducerTests(unittest.TestCase):
    def setUp(self) -> None:
        identity = TaskIdentity("task-1", "issue-1", "project-1", "request-1")
        self.snapshot = WorkflowSnapshot(
            "task-1",
            WorkflowContext(
                identity,
                ProgressState("REQUEST_INTAKE", 0, "2026-09-09T00:00:00Z"),
            ),
            0,
        )
        self.reducer = LinearContextReducer()

    def test_registered_transition_advances_only_new_snapshot(self) -> None:
        decision = StateDecision(TransitionRequest("START", "request_started"))

        after = self.reducer.apply(self.snapshot, decision)

        self.assertEqual(after.context.progression.state, "ZHONGSHU_ANALYST")
        self.assertEqual(after.context.progression.sequence, 1)
        self.assertEqual(after.state_version, 1)
        self.assertEqual(self.snapshot.context.progression.state, "REQUEST_INTAKE")

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
                ProgressState("ZHONGSHU_CRITIC", 0, "now"),
            ),
            0,
        )
        decision = ZhongshuCriticState().handle(
            critic.context,
            DomainEvent("OPEN_HUMAN_GATE", "task-1", 0, {}, "now"),
        )

        after = self.reducer.apply(critic, decision)
        self.assertEqual(after.context.progression.state, "HUMAN_GATE")
        self.assertEqual(after.context.progression.resume_state, "ZHONGSHU_CRITIC")

    def test_resume_uses_recorded_resume_state_and_preserves_gate(self) -> None:
        gated = WorkflowSnapshot(
            "task-1",
            WorkflowContext(
                self.snapshot.context.identity,
                ProgressState("HUMAN_GATE", 1, "now", "ZHONGSHU_SOLVER"),
                human_gate=HumanGateState("decision-1", "blocked", "ZHONGSHU_SOLVER"),
            ),
            1,
        )

        after = self.reducer.apply(
            gated,
            StateDecision(TransitionRequest("RESUME", "human_decision")),
        )

        self.assertEqual(after.context.progression.state, "ZHONGSHU_SOLVER")
        self.assertIsNone(after.context.progression.resume_state)
        self.assertEqual(after.context.human_gate.decision_id, "decision-1")


if __name__ == "__main__":
    unittest.main()
