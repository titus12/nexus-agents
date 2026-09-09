from __future__ import annotations

import unittest

from orchestrator.domain.context import ProgressState, TaskIdentity, WorkflowContext
from orchestrator.domain.decisions import StateDecision
from orchestrator.domain.errors import InvariantViolation
from orchestrator.domain.events import DomainEvent
from orchestrator.domain.states import StateRegistry
from orchestrator.domain.transitions import BUSINESS_STATES, SYSTEM_STATES


class LinearStateMatrixTests(unittest.TestCase):
    def context(self, state: str, resume_state: str | None = None) -> WorkflowContext:
        return WorkflowContext(
            identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
            progression=ProgressState(state, 0, "2026-09-09T00:00:00Z", resume_state),
        )

    def test_every_registered_state_is_a_concrete_object(self) -> None:
        registry = StateRegistry.default()
        self.assertEqual(set(registry.names()), set(BUSINESS_STATES + SYSTEM_STATES))
        self.assertEqual(len(registry), len(BUSINESS_STATES + SYSTEM_STATES))

    def test_each_nonterminal_business_state_accepts_only_its_declared_action(self) -> None:
        registry = StateRegistry.default()
        for state_name in BUSINESS_STATES[:-1]:
            state = registry.get(state_name)
            action = state.supported_actions[0]
            with self.subTest(state=state_name, action=action):
                decision = state.handle(
                    self.context(state_name),
                    DomainEvent(action, "task-1", 0, {}, "2026-09-09T00:00:00Z"),
                )
                self.assertIsInstance(decision, StateDecision)
                self.assertEqual(decision.transition.action, action)

    def test_system_resume_states_require_and_use_resume_state(self) -> None:
        registry = StateRegistry.default()
        for state_name in ("HUMAN_GATE", "RETRY_WAIT", "BLOCKED", "PERSISTENCE_DEGRADED"):
            state = registry.get(state_name)
            with self.subTest(state=state_name):
                decision = state.on_resume(self.context(state_name, "ZHONGSHU_SOLVER"))
                self.assertEqual(decision.transition.action, "RESUME")
                with self.assertRaises(InvariantViolation):
                    state.on_resume(self.context(state_name))

    def test_state_object_rejects_foreign_task_and_unsupported_action(self) -> None:
        state = StateRegistry.default().get("ZHONGSHU_ANALYST")
        with self.assertRaises(InvariantViolation):
            state.handle(
                self.context("ZHONGSHU_ANALYST"),
                DomainEvent("READY_FOR_SOLVER", "other-task", 0, {}, "now"),
            )
        with self.assertRaises(InvariantViolation):
            state.handle(
                self.context("ZHONGSHU_ANALYST"),
                DomainEvent("APPROVE_CRITIC", "task-1", 0, {}, "now"),
            )


if __name__ == "__main__":
    unittest.main()
