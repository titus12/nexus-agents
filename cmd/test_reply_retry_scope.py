from __future__ import annotations

import unittest

from orchestrator.context import StateContext
from orchestrator.events import Event
from orchestrator.state_machine import StateMachine
from orchestrator.transitions import Transition


class ReplyRetryScopeTests(unittest.TestCase):
    def machine(self, state: str, count: int) -> StateMachine:
        return StateMachine(
            StateContext(
                task_id="task-1",
                workflow_state=state,
                reply_retry_count=count,
            ),
            {},
        )

    def test_new_agent_state_resets_reply_retry_count(self):
        machine = self.machine("ZHONGSHU_CRITIC", 3)
        machine._apply_counters(
            Event("AGENT_REPLY_ACCEPTED", {"action": "REQUEST_SOLVER_REVISION"}),
            Transition(
                "ZHONGSHU_CRITIC",
                "AGENT_REPLY_ACCEPTED",
                "ZHONGSHU_SOLVER",
            ),
        )
        self.assertEqual(machine.ctx.reply_retry_count, 0)

    def test_new_menxia_item_state_resets_reply_retry_count(self):
        machine = self.machine("MENXIA_GROUP_GATE", 2)
        machine._apply_counters(
            Event("AGENT_REPLY_ACCEPTED", {"action": "APPROVE_GROUP"}),
            Transition(
                "MENXIA_GROUP_GATE",
                "AGENT_REPLY_ACCEPTED",
                "MENXIA_ITEM_SOLVER",
            ),
        )
        self.assertEqual(machine.ctx.reply_retry_count, 0)

    def test_invalid_reply_retry_chain_keeps_count(self):
        machine = self.machine("INVALID_AGENT_REPLY", 1)
        machine._apply_counters(
            Event("RETRY", {"resume_state": "MENXIA_ITEM_ANALYST"}),
            Transition(
                "INVALID_AGENT_REPLY",
                "RETRY",
                "MENXIA_ITEM_ANALYST",
            ),
        )
        self.assertEqual(machine.ctx.reply_retry_count, 2)


if __name__ == "__main__":
    unittest.main()
