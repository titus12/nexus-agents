from __future__ import annotations

import json
import unittest

from orchestrator.context import StateContext
from orchestrator.states import ZhongshuSolverState


class SolverRevisionPromptTests(unittest.TestCase):
    def test_critic_findings_are_explicit_revision_requirements(self):
        ctx = StateContext(
            task_id="task-1",
            raw_request="review",
            workflow_state="ZHONGSHU_SOLVER",
            current_phase="ZHONGSHU",
            current_role="review-solver",
            expected_agent_id="solver",
            active_request_id="req-1",
            request_payload={
                "analyst_plan": {"requirements": [], "confirmed_facts": [], "candidate_items": [], "candidate_groups": []},
                "zhongshu_critic_review": {
                    "action": "REQUEST_SOLVER_REVISION",
                    "findings": [{
                        "finding_id": "finding-1",
                        "severity": "P1",
                        "status": "open",
                        "title": "Incorrect scope",
                        "required_action": "Limit the claim",
                        "next_action": "REQUEST_SOLVER_REVISION",
                    }],
                },
            },
        )
        payload = json.loads(ZhongshuSolverState().request(ctx).prompt)
        protocol = payload["revision_protocol"]
        self.assertTrue(protocol["required_for_ready_for_critic"])
        self.assertEqual(protocol["finding_requirements"][0]["finding_id"], "finding-1")
        self.assertIn("finding_resolution", protocol["instruction"])
        self.assertIn("never omit", protocol["instruction"])

    def test_revision_prompt_keeps_complete_plan_for_noop_or_deferred_revision(self):
        item = {
            "item_id": "item-1",
            "title": "one task",
            "objective": "one outcome",
            "source_requirement_ids": ["req-1"],
            "dependencies": [],
            "acceptance_signals": ["observable result"],
            "unknowns": [],
            "risks": [],
            "parallelizable": True,
        }
        candidate_plan = {
            "requirements": [{"requirement_id": "req-1", "statement": "one requirement"}],
            "items": [item],
            "groups": [{"group_id": "group-1", "title": "one group", "items": [item]}],
            "dependencies": [],
            "scope": {"in_scope": ["the task"]},
            "unknowns": [],
            "risks": [],
        }
        ctx = StateContext(
            task_id="task-1",
            raw_request="review",
            workflow_state="ZHONGSHU_SOLVER",
            current_phase="ZHONGSHU",
            current_role="review-solver",
            expected_agent_id="solver",
            active_request_id="req-1",
            request_payload={
                "analyst_plan": {"requirements": [], "confirmed_facts": [], "candidate_items": [], "candidate_groups": []},
                "candidate_plan": candidate_plan,
                "zhongshu_critic_review": {
                    "action": "APPROVE_FREEZE",
                    "findings": [{
                        "finding_id": "finding-deferred",
                        "severity": "P2",
                        "status": "DEFERRED",
                        "next_action": "",
                    }],
                },
            },
        )
        payload = json.loads(ZhongshuSolverState().request(ctx).prompt)
        current_plan = payload["upstream"]["task_graph"]["current_formal_plan"]
        for field in ("requirements", "items", "groups", "dependencies", "scope", "unknowns", "risks"):
            self.assertIn(field, current_plan)
        self.assertTrue(payload["response_contract"]["success"]["complete_plan_materialized_by_orchestrator"])
        self.assertIn("changes", payload["output_example"])
        self.assertIn("finding_resolutions never replaces both plan and changes", payload["revision_protocol"]["instruction"])


if __name__ == "__main__":
    unittest.main()
