from __future__ import annotations

import json
import unittest

from orchestrator.context import StateContext
from orchestrator.states import (
    MenxiaItemSolverState,
    _critic_finding_ids,
    _menxia_item_solver_capsule,
)


class MenxiaItemPromptTests(unittest.TestCase):
    def _ctx(self) -> StateContext:
        item = {
            "item_id": "item-1",
            "title": "incremental read",
            "objective": "reduce duplicate scans",
            "evidence_basis": ["ev-2", "ev-1"],
            "scope": "current item",
            "risk_signals": ["late replies must not be lost"],
        }
        group = {
            "group_id": "group-1",
            "title": "poll optimization",
            "objective": "reduce polling cost",
            "items": [item],
            "shared_acceptance": ["no missed replies"],
        }
        return StateContext(
            task_id="task-1",
            raw_request="optimize polling",
            workflow_state="MENXIA_ITEM_SOLVER",
            current_phase="MENXIA",
            current_role="review-solver",
            expected_agent_id="solver",
            active_group_id="group-1",
            active_item_id="item-1",
            active_request_id="req-1",
            request_payload={
                "analyst_plan": {
                    "confirmed_facts": [
                        {"evidence_id": "ev-1", "statement": "fact one", "source": "a.py:1"},
                        {"evidence_id": "ev-2", "statement": "fact two", "source": "b.py:2"},
                        {"evidence_id": "ev-unused", "statement": "unused fact", "source": "c.py:3"},
                    ]
                },
                "candidate_plan": {"all_items": ["item-1", "item-2"]},
                "frozen_plan": {"all_groups": ["group-1", "group-2"]},
                "zhongshu_critic_review": {"findings": ["global"]},
                "active_group": group,
                "active_item": item,
                "item_implementation_proposals": {
                    "item-other": {"objective": "must not leak"},
                    "item-1": {"objective": "previous proposal"},
                },
                "item_analyst_reviews": {"item-1": {"action": "EVIDENCE_SUFFICIENT"}},
                "item_critic_reviews": {"item-other": {"action": "REVISE_ITEM"}},
            },
        )

    def test_prompt_is_scoped_to_current_item_and_evidence(self):
        request = MenxiaItemSolverState().request(self._ctx())
        payload = request.context
        inputs = payload["inputs"]
        self.assertEqual(payload["scope"], {"group_id": "group-1", "item_id": "item-1"})
        self.assertEqual(inputs["item"]["item_id"], "item-1")
        self.assertEqual([fact["evidence_id"] for fact in inputs["evidence"]], ["ev-2", "ev-1"])
        self.assertEqual(inputs["previous_item_proposal"]["objective"], "previous proposal")
        self.assertEqual(inputs["analyst_review"]["action"], "EVIDENCE_SUFFICIENT")
        self.assertIsNone(inputs["critic_review"])
        self.assertNotIn("candidate_plan", payload["inputs"])
        self.assertNotIn("frozen_plan", payload["inputs"])

    def test_prompt_is_much_smaller_than_full_request_payload(self):
        ctx = self._ctx()
        request = MenxiaItemSolverState().request(ctx)
        full = json.dumps(ctx.request_payload, ensure_ascii=False, separators=(",", ":"))
        self.assertLess(len(request.prompt), 200)
        self.assertLess(len(json.dumps(request.context, ensure_ascii=False).encode("utf-8")), 24 * 1024)

    def test_capsule_does_not_include_other_item_results(self):
        capsule = _menxia_item_solver_capsule(self._ctx())
        self.assertNotIn("item-other", json.dumps(capsule, ensure_ascii=False))

    def test_critic_finding_ids_are_stable_and_deduplicated(self):
        review = {
            "findings": [
                {"finding_id": "F-2", "title": "second"},
                {"id": "F-1", "title": "first"},
                {"finding_id": "F-2", "title": "duplicate"},
                "invalid",
            ]
        }
        self.assertEqual(_critic_finding_ids(review), ["F-2", "F-1"])


if __name__ == "__main__":
    unittest.main()

