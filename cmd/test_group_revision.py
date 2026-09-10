from __future__ import annotations

import unittest

from orchestrator.app import OrchestratorApp
from orchestrator.context import StateContext
from orchestrator.events import Event
from orchestrator.states import _allowed_actions, _normalize_agent_reply_action
from orchestrator.transitions import TransitionPolicy


class GroupRevisionTests(unittest.TestCase):
    def context(self) -> StateContext:
        return StateContext(
            task_id="task-1",
            workflow_state="MENXIA_GROUP_GATE",
            current_phase="MENXIA",
            current_role="review-critic",
            group_index=0,
            item_index=None,
            active_group_id="group-000001",
            active_item_id=None,
            request_payload={
                "frozen_plan": {
                    "groups": [{
                        "group_id": "group-000001",
                        "items": [
                            {"item_id": "item-000001", "title": "first"},
                            {"item_id": "item-000002", "title": "second"},
                        ],
                    }]
                }
            },
        )

    def test_legacy_group_revision_action_is_normalized(self):
        payload = _normalize_agent_reply_action(
            "MENXIA_GROUP_GATE",
            {"action": "REVISE_GROUP", "group_id": "group-000001"},
        )
        self.assertEqual(payload["action"], "REQUEST_GROUP_REVISION")
        self.assertEqual(payload["original_action"], "REVISE_GROUP")
        self.assertTrue(payload["action_normalized"])
        self.assertIn("REQUEST_GROUP_REVISION", _allowed_actions("MENXIA_GROUP_GATE"))

    def test_group_revision_transitions_to_item_solver(self):
        ctx = self.context()
        transition = TransitionPolicy.resolve(
            ctx,
            Event("AGENT_REPLY_ACCEPTED", {
                "action": "REQUEST_GROUP_REVISION",
                "item_id": "item-000001",
            }),
        )
        self.assertEqual(transition.to_state, "MENXIA_ITEM_SOLVER")

    def test_group_revision_restarts_current_group_item_without_advancing_group(self):
        ctx = self.context()
        app = object.__new__(OrchestratorApp)
        app.ctx = ctx
        event = Event("AGENT_REPLY_ACCEPTED", {
            "action": "REQUEST_GROUP_REVISION",
            "item_id": "item-000002",
        })
        app._decorate_progress_event(event)
        self.assertEqual(event.payload["next_state"], "MENXIA_ITEM_SOLVER")
        self.assertEqual(ctx.group_index, 0)
        self.assertEqual(ctx.item_index, 1)
        self.assertEqual(ctx.active_group_id, "group-000001")
        self.assertEqual(ctx.active_item_id, "item-000002")


if __name__ == "__main__":
    unittest.main()
