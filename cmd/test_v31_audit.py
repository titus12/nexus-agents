from __future__ import annotations

import unittest

from orchestrator.context import StateContext
from orchestrator.events import Event
from orchestrator.states import build_state_registry
from orchestrator.transitions import TransitionPolicy


class V31AuditTests(unittest.TestCase):
    def test_every_declared_state_has_lifecycle_methods(self):
        registry = build_state_registry()
        required = {
            "REQUEST_INTAKE",
            "ZHONGSHU_ANALYST",
            "ZHONGSHU_SOLVER",
            "ZHONGSHU_CRITIC",
            "ZHONGSHU_FREEZE_CHECK",
            "MENXIA_ITEM_SOLVER",
            "MENXIA_ITEM_ANALYST",
            "MENXIA_ITEM_CRITIC",
            "MENXIA_GROUP_GATE",
            "HUMAN_GATE",
            "TIMEOUT",
            "HUMAN_GATE_TIMEOUT",
            "HUMAN_GATE_ERROR",
            "INVALID_AGENT_REPLY",
            "MULTICA_ERROR",
            "BLOCKED",
            "DONE",
            "CANCELLED",
            "STATE_CORRUPTED",
        }
        self.assertEqual(required, set(registry))
        for state in registry.values():
            self.assertTrue(callable(state.enter))
            self.assertTrue(callable(state.update))
            self.assertTrue(callable(state.exit))

    def test_v31_recovery_exits_are_explicit(self):
        cases = [
            ("HUMAN_GATE", Event("HUMAN_GATE_TIMEOUT"), "HUMAN_GATE_TIMEOUT"),
            ("TIMEOUT", Event("RESUME", {"resume_state": "ZHONGSHU_SOLVER"}), "ZHONGSHU_SOLVER"),
            ("INVALID_AGENT_REPLY", Event("RETRY", {"resume_state": "ZHONGSHU_SOLVER"}), "ZHONGSHU_SOLVER"),
            ("MULTICA_ERROR", Event("RETRY", {"resume_state": "ZHONGSHU_SOLVER"}), "ZHONGSHU_SOLVER"),
            ("MENXIA_ITEM_CRITIC", Event("AGENT_REPLY_ACCEPTED", {"action": "ITEM_REVISION_LIMIT"}), "BLOCKED"),
        ]
        for state, event, target in cases:
            ctx = StateContext(task_id="task-1", workflow_state=state)
            transition = TransitionPolicy.resolve(ctx, event)
            self.assertEqual(transition.to_state, target)

    def test_cancel_is_allowed_from_every_non_terminal_state(self):
        for state in TransitionPolicy.NON_TERMINAL:
            ctx = StateContext(task_id="task-1", workflow_state=state)
            transition = TransitionPolicy.resolve(ctx, Event("CANCEL_REQUESTED"))
            self.assertEqual(transition.to_state, "CANCELLED")


if __name__ == "__main__":
    unittest.main()
