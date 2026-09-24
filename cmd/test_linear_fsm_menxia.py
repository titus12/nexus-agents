from __future__ import annotations

import unittest

from orchestrator.domain.context import (
    ProgressState,
    ReviewState,
    ReviewTaskGroup,
    ReviewTaskItem,
    TaskIdentity,
    WorkflowContext,
)
from orchestrator.domain.events import DomainEvent
from orchestrator.domain.states import MenxiaGroupGateState
from orchestrator.runtime.reducer import LinearContextReducer
from orchestrator.runtime.repository import WorkflowSnapshot


class MenxiaGraphTests(unittest.TestCase):
    def test_group_gate_resumes_the_group_pipeline(self):
        identity = TaskIdentity("task-menxia", "issue", "", "request")
        review = ReviewState(
            revision_id="revision-1",
            active_group_id="group-1",
            active_item_id="item-1",
            task_items=(
                ReviewTaskItem("item-1", "group-1", (), 0),
                ReviewTaskItem("item-2", "group-1", ("item-1",), 1),
            ),
            task_groups=(ReviewTaskGroup("group-1", ("item-1", "item-2"), 0),),
            completed_item_ids=("item-1",),
        )
        snapshot = WorkflowSnapshot(
            "task-menxia",
            WorkflowContext(
                identity,
                ProgressState("MENXIA_GROUP_GATE", 0, "t0"),
                review=review,
            ),
            0,
        )

        decision = MenxiaGroupGateState().handle(
            snapshot.context,
            DomainEvent("APPROVE_GROUP", "task-menxia", 0, {}, "t1", "event-1"),
        )
        after = LinearContextReducer().apply(snapshot, decision)

        # One pending item remains, so the gate re-enters the group pipeline
        # through the shared-document Solver instead of finishing.  In
        # menxia-group-pipeline mode the wave carries the group per binding;
        # the top-level payload group_id stays None by design.
        self.assertEqual(decision.transition.action, "NEXT_ITEM")
        self.assertEqual(after.context.progression.state, "MENXIA_GROUP_SOLVER")
        self.assertEqual(
            decision.effects[0].payload["dispatch_mode"], "menxia_group_pipeline"
        )
        self.assertEqual(
            decision.effects[0].payload["bindings"][0]["group_id"], "group-1"
        )


if __name__ == "__main__":
    unittest.main()
