from __future__ import annotations

import unittest

from orchestrator.context import StateContext
from orchestrator.events import Event
from orchestrator.models import AgentBinding, ExternalMessage
from orchestrator.states import _validate_state_payload
from orchestrator.validators import RejectedReply, validate_agent_reply


class RequestIdentityTests(unittest.TestCase):
    def test_explicit_target_mismatch_is_rejected(self) -> None:
        result = validate_agent_reply(
            ExternalMessage(
                "agent-1",
                {
                    "task_id": "task-1",
                    "request_id": "req-1",
                    "target_state": "MENXIA_ITEM_ANALYST",
                    "target_role": "review-analyst",
                    "action": "FEASIBLE",
                },
            ),
            AgentBinding(
                "agent-1", "task-1", "req-1", "review-solver", "MENXIA",
                target_state="MENXIA_ITEM_SOLVER",
                target_role="review-solver",
            ),
            {"FEASIBLE"},
        )
        self.assertIsInstance(result, RejectedReply)
        self.assertEqual(result.reason, "TARGET_STATE_MISMATCH")


class SolverFindingCoverageTests(unittest.TestCase):
    def test_solver_must_cover_active_critic_findings(self) -> None:
        ctx = StateContext(
            task_id="task-1",
            workflow_state="MENXIA_ITEM_SOLVER",
            current_phase="MENXIA",
            current_role="review-solver",
            active_item_id="item-1",
            request_payload={
                "item_critic_reviews": {
                    "item-1": {
                        "findings": [
                            {"finding_id": "finding-1", "status": "OPEN"},
                            {"finding_id": "finding-2", "status": "RESOLVED"},
                        ]
                    }
                }
            },
        )
        self.assertEqual(
            _validate_state_payload(
                "MENXIA_ITEM_SOLVER",
                {"action": "FEASIBLE", "implementation_proposal": {}},
                ctx,
            ),
            "SOLVER_CRITIC_RESPONSES_MISSING",
        )
        self.assertEqual(
            _validate_state_payload(
                "MENXIA_ITEM_SOLVER",
                {
                    "action": "FEASIBLE",
                    "implementation_proposal": {},
                    "responses_to_critic": [
                        {"finding_id": "finding-1", "status": "resolved"}
                    ],
                },
                ctx,
            ),
            "",
        )


if __name__ == "__main__":
    unittest.main()
