from __future__ import annotations

import json
import unittest

from orchestrator.context import StateContext
from orchestrator.states import _attach_repair_feedback


class RepairMvpTests(unittest.TestCase):
    def _ctx(self, code: str, reason: str) -> StateContext:
        return StateContext(
            task_id="task-1",
            raw_request="check the issue",
            workflow_state="ZHONGSHU_SOLVER",
            last_error={"code": code, "reason": reason},
            request_payload={
                "last_rejected_reply": {
                    "action": "READY_FOR_CRITIC",
                    "plan": {"items": ["item-1"]},
                }
            },
        )

    def test_contract_error_attaches_original_reply_and_reason(self):
        value = json.loads(
            _attach_repair_feedback(
                '{"task":"check","role":"ZHONGSHU_SOLVER"}',
                self._ctx("AGENT_REPLY_CONTRACT_REJECTED", "SOLVER_ITEMS_MISSING"),
            )
        )
        self.assertEqual(
            value["repair_feedback"]["validation_error"],
            "SOLVER_ITEMS_MISSING",
        )
        self.assertEqual(
            value["repair_feedback"]["original_reply"]["action"],
            "READY_FOR_CRITIC",
        )

    def test_invalid_action_is_repairable(self):
        value = json.loads(
            _attach_repair_feedback(
                '{"task":"check"}',
                self._ctx("AGENT_REPLY_REJECTED", "INVALID_ACTION"),
            )
        )
        self.assertIn("repair_feedback", value)

    def test_binding_error_is_not_repaired_by_llm(self):
        prompt = '{"task":"check"}'
        self.assertEqual(
            _attach_repair_feedback(
                prompt,
                self._ctx("AGENT_REPLY_REJECTED", "AUTHOR_ID_MISMATCH"),
            ),
            prompt,
        )


if __name__ == "__main__":
    unittest.main()
