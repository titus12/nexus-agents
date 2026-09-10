from __future__ import annotations

import unittest

from orchestrator.context import StateContext
from orchestrator.notifications import build_agent_notification
from orchestrator.states import DoneState, _build_final_delivery


class FinalDeliveryTests(unittest.TestCase):
    def test_builds_final_delivery_from_candidate_plan(self):
        ctx = StateContext(
            task_id="task-1",
            workflow_state="DONE",
            request_payload={
                "candidate_plan": {
                    "groups": [
                        {
                            "group_id": "group-1",
                            "title": "Group one",
                            "items": [
                                {"item_id": "item-1", "title": "Item one"}
                            ],
                        }
                    ],
                    "items": [
                        {"item_id": "item-1", "title": "Item one"}
                    ],
                }
            },
        )
        delivery = _build_final_delivery(ctx)
        self.assertEqual(delivery["groups"][0]["items"][0]["item_id"], "item-1")
        self.assertEqual(delivery["review_results"]["status"], "APPROVED")

    def test_done_notification_contains_plan_when_payload_has_no_delivery(self):
        ctx = StateContext(
            task_id="task-1",
            workflow_state="DONE",
            request_payload={
                "candidate_plan": {
                    "groups": [
                        {
                            "group_id": "group-1",
                            "title": "Group one",
                            "items": [
                                {"item_id": "item-1", "title": "Item one"}
                            ],
                        }
                    ],
                    "items": [
                        {"item_id": "item-1", "title": "Item one"}
                    ],
                }
            },
            last_agent_payload={"action": "APPROVE_GROUP"},
        )
        delivery = _build_final_delivery(ctx)
        text = build_agent_notification(
            "review-critic",
            "DONE",
            "AGENT_REPLY_ACCEPTED",
            ctx,
            {"action": "DONE", "final_delivery": delivery},
        )
        self.assertIn("最终交付方案", text)
        self.assertIn("item-1", text)
        self.assertIn("Item one", text)


if __name__ == "__main__":
    unittest.main()
