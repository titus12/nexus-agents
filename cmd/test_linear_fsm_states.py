from __future__ import annotations

import unittest

from orchestrator.domain.context import ProgressState, TaskIdentity, WorkflowContext
from orchestrator.domain.decisions import StateDecision
from orchestrator.domain.errors import InvariantViolation
from orchestrator.domain.events import DomainEvent
from orchestrator.domain.policies.prompts import build_prompt
from orchestrator.domain.states import StateRegistry, ZhongshuSolverState
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


class PromptTargetStateTests(unittest.TestCase):
    """The dispatched prompt must name the target state, not the source state."""

    def test_prompt_names_dispatch_target_not_source(self) -> None:
        context = WorkflowContext(
            identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
            progression=ProgressState("ZHONGSHU_CRITIC", 6, "2026-09-14T00:00:00Z"),
        )
        prompt = build_prompt(context, target_state="ZHONGSHU_SOLVER")
        self.assertEqual(prompt.state, "ZHONGSHU_SOLVER")
        self.assertEqual(prompt.role, "review-solver")
        self.assertIn("State: ZHONGSHU_SOLVER", prompt.content)
        self.assertNotIn("State: ZHONGSHU_CRITIC", prompt.content)

    def test_prompt_defaults_to_current_state(self) -> None:
        context = WorkflowContext(
            identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
            progression=ProgressState("ZHONGSHU_ANALYST", 1, "2026-09-14T00:00:00Z"),
        )
        self.assertIn("State: ZHONGSHU_ANALYST", build_prompt(context).content)

    def test_solver_dispatch_prompt_uses_solver_state(self) -> None:
        context = WorkflowContext(
            identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
            progression=ProgressState("ZHONGSHU_CRITIC", 6, "2026-09-14T00:00:00Z"),
        )
        effect = ZhongshuSolverState._dispatch_effect(context, "ZHONGSHU_SOLVER")
        self.assertIn("State: ZHONGSHU_SOLVER", effect.payload["prompt_ref"])
        self.assertNotIn("State: ZHONGSHU_CRITIC", effect.payload["prompt_ref"])


if __name__ == "__main__":
    unittest.main()
