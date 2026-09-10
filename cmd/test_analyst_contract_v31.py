from __future__ import annotations

import json
import unittest

from orchestrator.context import StateContext
from orchestrator.states import (
    ZhongshuAnalystState,
    _validate_analyst_plan,
)
from orchestrator.structured_output import build_structured_output_spec, role_modes


VALID_PLAN = {
    "plan_id": "ANALYSIS-001",
    "version": 1,
    "phase": "ZHONGSHU",
    "problem_interpretation": "检查 Proposal 覆盖缺口",
    "objective": "形成有证据支持的候选任务和分组",
    "success_definition": "Solver 无需重新调查即可制定方案",
    "requirements": [{
        "requirement_id": "REQ-1",
        "statement": "补齐覆盖",
        "source": "user",
        "priority": "must",
        "scope": "in",
        "acceptance_signal": "形成缺口清单"
    }],
    "goals": ["识别缺口"],
    "non_goals": ["不修改代码"],
    "project_context": {"project_type": "go"},
    "confirmed_facts": [{
        "evidence_id": "ev-1",
        "statement": "设计缺少知识库页面",
        "source_type": "code",
        "source": "design/pages",
        "confidence": 0.99,
        "relevance": "证明覆盖缺口"
    }],
    "conflicts": [],
    "candidate_directions": [],
    "selected_direction": {},
    "alternatives": [],
    "comparison": [],
    "recommendation": {},
    "candidate_items": [{
        "item_id": "item-1",
        "title": "页面覆盖",
        "problem_addressed": "页面缺失",
        "objective": "补齐页面说明",
        "basis_evidence": ["ev-1"],
        "why_needed": "保持一致",
        "dependencies": [],
        "acceptance_signals": ["存在明确页面清单"],
        "risk_signals": []
    }],
    "candidate_groups": [{
        "candidate_group_id": "group-1",
        "title": "页面补全",
        "objective": "补齐页面覆盖",
        "reason": "共享同一目标",
        "related_items": ["item-1"],
        "basis_evidence": ["ev-1"],
        "dependencies": [],
        "suggested_order": 1
    }],
    "dependencies": [],
    "constraints": ["只读"],
    "scope": {"in_scope": [], "out_of_scope": [], "protected_paths": []},
    "assumptions": [],
    "unknowns": [],
    "risks": [],
    "questions_for_solver": ["如何正式分组？"],
    "candidate_verification_questions": ["是否覆盖全部需求？"],
    "questions_for_user": []
}


class AnalystContractTests(unittest.TestCase):
    def test_analyst_prompt_requires_bounded_evidence_contract(self):
        ctx = StateContext(
            task_id="task-1",
            raw_request="检查覆盖问题",
            workflow_state="ZHONGSHU_ANALYST",
            current_phase="ZHONGSHU",
            current_role="review-analyst",
            active_request_id="req-1",
        )
        payload = json.loads(ZhongshuAnalystState().request(ctx).prompt)
        self.assertEqual(payload["role"], "ZHONGSHU_ANALYST")
        self.assertEqual(payload["mode"], "EVIDENCE_COLLECTION_READ_ONLY")
        self.assertEqual(payload["required_response_schema"]["task_proposals"], [])
        self.assertEqual(payload["required_response_schema"]["candidate_items"], [])
        self.assertEqual(payload["required_response_schema"]["candidate_groups"], [])
        self.assertIn("evidence_updates", payload["required_response_schema"])
        rules = "\n".join(payload["working_rules"])
        self.assertIn("Do not emit implementation_proposal", rules)
        self.assertIn("Do not create, name, group", rules)

    def test_analyst_protocol_has_no_task_discovery_mode(self):
        spec = build_structured_output_spec(
            "ZHONGSHU",
            "review-analyst",
            {
                "active_runtime_state": "ZHONGSHU_ANALYST",
                "zhongshu_dispatch_mode": "evidence_collection",
            },
        )
        self.assertIsNotNone(spec)
        self.assertNotIn("TASK_DISCOVERY_READ_ONLY", role_modes("ZHONGSHU", "review-analyst"))
        self.assertNotIn(
            "TASK_PROPOSALS_READY",
            spec.schema["properties"]["action"]["enum"],
        )

    def test_legacy_task_plan_is_rejected(self):
        self.assertEqual(
            _validate_analyst_plan({"action": "READY_FOR_SOLVER", "plan": VALID_PLAN}),
            "ZHONGSHU_ANALYST_TASK_GRAPH_FORBIDDEN",
        )

    def test_missing_plan_is_rejected(self):
        self.assertEqual(
            _validate_analyst_plan({"action": "READY_FOR_SOLVER"}),
            "ANALYST_PLAN_MISSING",
        )

    def test_legacy_task_plan_missing_field_is_still_rejected(self):
        plan = dict(VALID_PLAN)
        plan.pop("requirements")
        reason = _validate_analyst_plan({"action": "READY_FOR_SOLVER", "plan": plan})
        self.assertEqual(reason, "ZHONGSHU_ANALYST_TASK_GRAPH_FORBIDDEN")

    def test_legacy_task_plan_group_scope_is_rejected(self):
        plan = json.loads(json.dumps(VALID_PLAN))
        plan["candidate_groups"][0]["related_items"] = ["item-missing"]
        reason = _validate_analyst_plan({"action": "READY_FOR_SOLVER", "plan": plan})
        self.assertEqual(reason, "ZHONGSHU_ANALYST_TASK_GRAPH_FORBIDDEN")


if __name__ == "__main__":
    unittest.main()
