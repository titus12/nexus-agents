from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from orchestrator.adapters import MulticaCliAdapter, _response_contract_for
from orchestrator.context import StateContext
from orchestrator.states import (
    ZhongshuSolverState,
    _compact_solver_previous_plan,
    _validate_state_payload,
)
from orchestrator.zhongshu_solver_contract import (
    ZHONGSHU_SOLVER_FORBIDDEN_FIELDS,
    ZHONGSHU_SOLVER_REQUIRED_ITEM_FIELDS,
    zhongshu_solver_runtime_rules,
)
from orchestrator.structured_output import role_result_template, validate_role_result_shape


ANALYST_PLAN = {
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


class SolverPromptTests(unittest.TestCase):
    def test_runtime_contract_has_quality_fields_and_forbids_implementation_design(self):
        rules = "\n".join(zhongshu_solver_runtime_rules())
        self.assertIn("coverage", rules)
        self.assertIn("dependency", rules)
        self.assertIn("acceptance_signals", rules)
        self.assertIn("Do not emit implementation_proposal", rules)
        self.assertEqual(
            ZHONGSHU_SOLVER_REQUIRED_ITEM_FIELDS,
            (
                "item_id",
                "title",
                "objective",
                "source_requirement_ids",
                "dependencies",
                "acceptance_signals",
                "unknowns",
                "risks",
                "parallelizable",
            ),
        )
        self.assertIn("implementation_proposal", ZHONGSHU_SOLVER_FORBIDDEN_FIELDS)
        self.assertIn("file_changes", ZHONGSHU_SOLVER_FORBIDDEN_FIELDS)

    def test_skill_document_has_same_boundary(self):
        skill = Path(__file__).parents[1] / "docs/multi/zhongshu-solver-skill.md"
        text = skill.read_text(encoding="utf-8")
        self.assertIn("任务图质量负责人", text)
        self.assertIn("不负责实现方案", text)
        self.assertIn("NEEDS_MORE_EVIDENCE", text)
        self.assertIn("source_requirement_ids", text)
        self.assertNotIn("OPTION_DESIGN", text)
        self.assertNotIn("OPTION_COMPARISON", text)
        self.assertIn("Solver 不负责实现方案", text)

    def _ctx(self) -> StateContext:
        return StateContext(
            task_id="task-1",
            issue_id="issue-1",
            raw_request="检查初始化 Proposal 的知识库覆盖问题",
            workflow_state="ZHONGSHU_SOLVER",
            current_phase="ZHONGSHU",
            current_role="review-solver",
            expected_agent_id="solver-agent",
            active_request_id="req-1",
            dispatch_idempotency_key="task-1:ZHONGSHU_SOLVER:2",
            request_payload={"analyst_plan": ANALYST_PLAN},
        )

    def test_solver_receives_complete_analyst_plan_without_legacy_fields(self):
        request = ZhongshuSolverState().request(self._ctx())
        payload = json.loads(request.prompt)
        self.assertEqual(payload["role"], "ZHONGSHU_SOLVER")
        self.assertEqual(payload["mode"], "TASK_GRAPH_FORMALIZATION_READ_ONLY")
        self.assertEqual(
            set(payload["upstream"]),
            {"task_graph", "critic_findings", "repair_scope"},
        )
        self.assertEqual(
            payload["upstream"]["task_graph"]["requirements"],
            ANALYST_PLAN["requirements"],
        )
        self.assertEqual(
            payload["upstream"]["task_graph"]["candidate_items"],
            ANALYST_PLAN["candidate_items"],
        )
        self.assertNotIn("previous_plan", payload["upstream"])
        self.assertNotIn("human_decision", payload["upstream"])
        self.assertNotIn("max_tool_calls", request.prompt)

    def test_human_decision_is_not_leaked_into_solver_graph_context(self):
        ctx = self._ctx()
        ctx.request_payload["human_decision"] = {
            "answer": "C",
            "selected_option": {"id": "OPTION-C", "label": "分阶段回补"},
        }
        payload = json.loads(ZhongshuSolverState().request(ctx).prompt)
        self.assertNotIn("human_decision", payload["upstream"])

    def test_solver_prompt_keeps_worker_index_without_replaying_worker_evidence(self):
        ctx = self._ctx()
        ctx.request_payload["analyst_plan"] = {
            **ANALYST_PLAN,
            "worker_evidence": {
                "zhongshu_analyst-1": {
                    "evidence_updates": [{
                        "evidence_id": "ev-worker-1",
                        "source": "worker-only-source",
                        "conclusion": "worker-only-body-must-not-replay",
                    }],
                    "confirmed_facts": [],
                },
                "zhongshu_analyst-2": {
                    "evidence_updates": [{"evidence_id": "ev-worker-2"}],
                    "confirmed_facts": [],
                },
            },
        }

        request = ZhongshuSolverState().request(ctx)
        payload = json.loads(request.prompt)
        graph = payload["upstream"]["task_graph"]
        self.assertNotIn("worker_evidence", graph)
        self.assertEqual(graph["worker_evidence_index"]["worker_count"], 2)
        self.assertEqual(graph["worker_evidence_index"]["evidence_count"], 2)
        self.assertEqual(
            graph["worker_evidence_index"]["workers"][0]["evidence_ids"],
            ["ev-worker-1"],
        )
        self.assertNotIn("worker-only-body-must-not-replay", request.prompt)

    def test_multica_resume_prompt_does_not_replay_full_analyst_plan(self):
        ctx = self._ctx()
        ctx.last_error = {
            "code": "MULTICA_ERROR",
            "message": "multica issue comment list timed out",
        }
        request = ZhongshuSolverState().request(ctx)
        payload = json.loads(request.prompt)
        self.assertEqual(payload["mode"], "TASK_GRAPH_FORMALIZATION_READ_ONLY_RESUME")
        self.assertNotIn("upstream", payload)
        self.assertNotIn("analyst_plan", payload)
        self.assertLess(len(request.prompt), 3000)

    def test_solver_response_contract_has_plan_fields(self):
        request = ZhongshuSolverState().request(self._ctx())
        contract = _response_contract_for(request)
        self.assertEqual(
            contract["allowed_actions"],
            [
                "READY_FOR_CRITIC",
                "REQUEST_ANALYST_EVIDENCE",
                "NEEDS_MORE_EVIDENCE",
                "HUMAN_GATE",
                "BLOCKED",
            ],
        )
        for field in ("plan", "changes", "dependencies", "scope", "unknowns", "risks", "next_actions"):
            self.assertIn(field, contract["optional"])
        for field in ("options", "comparison", "recommendation", "implementation_proposal"):
            self.assertNotIn(field, contract["optional"])
        self.assertEqual(
            contract["required_by_action"]["READY_FOR_CRITIC"],
            ["action", "plan"],
        )
        self.assertIn("changes=[] is valid", contract["instruction"])

    def test_solver_prompt_requires_requirements_and_top_level_items(self):
        request = ZhongshuSolverState().request(self._ctx())
        self.assertIn("plan.requirements", request.prompt)
        self.assertIn("plan.items", request.prompt)
        self.assertIn("match by item_id", request.prompt)
        self.assertIn("NEEDS_MORE_EVIDENCE", request.prompt)
        self.assertNotIn('"options"', request.prompt)
        self.assertNotIn('"recommendation"', request.prompt)

    def test_solver_prompt_and_schema_bind_requirements_to_analyst(self):
        request = ZhongshuSolverState().request(self._ctx())
        payload = json.loads(request.prompt)
        boundary = payload["requirement_boundary"]
        self.assertEqual(boundary["allowed_requirement_ids"], ["REQ-1"])
        self.assertEqual(boundary["cardinality"], 1)
        self.assertIn("Evidence-derived opportunities are plan.items", boundary["rule"])

        requirements_schema = (
            request.structured_output["schema"]["properties"]["plan"]
            ["properties"]["requirements"]
        )
        self.assertEqual(requirements_schema["minItems"], 1)
        self.assertEqual(requirements_schema["maxItems"], 1)
        self.assertEqual(
            requirements_schema["items"]["properties"]["requirement_id"]["enum"],
            ["REQ-1"],
        )

    def test_bound_solver_schema_rejects_an_extra_requirement(self):
        request = ZhongshuSolverState().request(self._ctx())
        result = role_result_template(
            "ZHONGSHU",
            "review-solver",
            task_id=request.task_id,
            request_id=request.request_id,
            state="ZHONGSHU_SOLVER",
            role_mode=request.structured_output["role_mode"],
            schema_hash=request.structured_output["schema_hash"],
            action="READY_FOR_CRITIC",
        )
        result["plan"] = {
            "requirements": [
                dict(ANALYST_PLAN["requirements"][0]),
                {
                    **dict(ANALYST_PLAN["requirements"][0]),
                    "requirement_id": "REQ-DERIVED",
                },
            ],
            "items": [],
            "groups": [],
            "dependencies": [],
            "scope": {},
            "unknowns": [],
            "risks": [],
        }
        error = validate_role_result_shape(
            result,
            phase="ZHONGSHU",
            role="review-solver",
            state="ZHONGSHU_SOLVER",
            role_mode=request.structured_output["role_mode"],
            expected_schema_hash=request.structured_output["schema_hash"],
            schema=request.structured_output["schema"],
        )
        self.assertIn("plan.requirements: more than maxItems", error)
        self.assertIn("plan.requirements[1].requirement_id: invalid enum", error)

    def test_solver_prompt_matches_validator_group_items_contract(self):
        request = ZhongshuSolverState().request(self._ctx())
        payload = json.loads(request.prompt)
        example_group = payload["output_example"]["plan"]["groups"][0]
        self.assertIn("item_ids", example_group)
        self.assertNotIn("items", example_group)
        self.assertIn("non-empty items array of item objects", request.prompt)

    def test_solver_repair_prompt_names_exact_contract_errors(self):
        ctx = self._ctx()
        ctx.last_error = {
            "code": "AGENT_REPLY_CONTRACT_REJECTED",
            "reason": "SOLVER_GROUP_ITEMS_MISSING:1",
        }
        request = ZhongshuSolverState().request(ctx)
        self.assertIn("SOLVER_GROUP_ITEMS_MISSING", request.prompt)
        self.assertIn("compact group item_ids", request.prompt)
        self.assertIn("SOLVER_PLAN_MISSING", request.prompt)

    def test_dispatch_writes_solver_specific_contract(self):
        with tempfile.TemporaryDirectory() as root:
            adapter = MulticaCliAdapter(root)
            request = ZhongshuSolverState().request(self._ctx())
            with patch.object(
                adapter,
                "_run",
                side_effect=[{"id": "issue-updated"}, {"id": "dispatch-comment"}],
            ):
                adapter.dispatch(request)
            files = list(Path(root).glob("dispatch_*.json"))
            self.assertEqual(len(files), 1)
            value = json.loads(files[0].read_text(encoding="utf-8"))
            self.assertIn("structured_output", value)
            self.assertNotIn("structured_output", value["response_contract"])
            self.assertNotIn("structured_output", value["transport"])
            self.assertLess(
                len(files[0].read_bytes()),
                24 * 1024,
                "dispatch envelope must stay below the Windows command-line safety budget",
            )
            if "prompt_ref" in value:
                manifest_path = Path(value["prompt_ref"]["manifest_path"])
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                prompt_path = manifest_path.parent / manifest["files"][0]["name"]
                prompt = json.loads(prompt_path.read_text(encoding="utf-8"))
            else:
                prompt = json.loads(value["prompt"])
            self.assertEqual(
                prompt["upstream"]["task_graph"]["requirements"],
                ANALYST_PLAN["requirements"],
            )


    def test_revision_previous_plan_is_compact_and_preserves_item_index(self):
        previous = {
            "plan_id": "PLAN-1",
            "version": 2,
            "phase": "ZHONGSHU",
            "project_context": {"large": "background"},
            "confirmed_facts": [{"evidence_id": "ev-1"}],
            "selected_direction": {"option_id": "OPTION-A"},
            "groups": [{
                "group_id": "group-1",
                "title": "Core",
                "objective": "Core objective",
                "items": [{
                    "item_id": "item-1",
                    "title": "Item",
                    "objective": "Item objective",
                    "dependencies": [],
                    "acceptance_signals": ["large detail"],
                }],
            }],
            "items": [{
                "item_id": "item-1",
                "title": "Item",
                "objective": "Item objective",
                "dependencies": [],
                "acceptance_signals": ["large detail"],
            }],
            "scope": {"in_scope": ["x"], "out_of_scope": ["y"]},
            "plan_status": "DRAFT",
        }
        compact = _compact_solver_previous_plan(previous)
        self.assertEqual(compact["plan_id"], "PLAN-1")
        self.assertEqual(compact["groups"][0]["items"][0]["item_id"], "item-1")
        self.assertNotIn("confirmed_facts", compact)
        self.assertNotIn("project_context", compact)
        self.assertLess(
            len(json.dumps(compact, ensure_ascii=False)),
            len(json.dumps(previous, ensure_ascii=False)),
        )

    def test_solver_ready_for_critic_rejects_empty_group_items(self):
        payload = {
            "action": "READY_FOR_CRITIC",
            "plan": {
                "requirements": [{
                    "requirement_id": "REQ-1",
                    "statement": "Requirement",
                    "priority": "must",
                    "scope": "in",
                }],
                "items": [{
                    "item_id": "item-1",
                    "title": "Item",
                    "objective": "Objective",
                    "source_requirement_ids": ["REQ-1"],
                    "dependencies": [],
                    "acceptance_signals": ["Observable result"],
                    "unknowns": [],
                    "risks": [],
                    "parallelizable": True,
                }],
                "groups": [{"group_id": "group-1", "items": []}],
            },
        }
        self.assertEqual(
            _validate_state_payload("ZHONGSHU_SOLVER", payload),
            "SOLVER_GROUP_ITEMS_MISSING:0",
        )

    def _valid_solver_payload(self) -> dict:
        item = {
            "item_id": "item-1",
            "title": "Item",
            "objective": "Objective",
            "source_requirement_ids": ["REQ-1"],
            "dependencies": [],
            "acceptance_signals": ["Observable result"],
            "unknowns": [],
            "risks": [],
            "parallelizable": True,
        }
        return {
            "action": "READY_FOR_CRITIC",
            "plan": {
                "requirements": [{
                    "requirement_id": "REQ-1",
                    "statement": "Requirement",
                    "priority": "must",
                    "scope": "in",
                }],
                "items": [item],
                "groups": [{"group_id": "group-1", "items": [item]}],
            },
        }

    def test_solver_ready_for_critic_rejects_missing_requirements(self):
        payload = self._valid_solver_payload()
        payload["plan"].pop("requirements")
        self.assertEqual(
            _validate_state_payload("ZHONGSHU_SOLVER", payload),
            "SOLVER_REQUIREMENTS_MISSING",
        )

    def test_solver_ready_for_critic_rejects_implementation_details(self):
        payload = self._valid_solver_payload()
        payload["plan"]["items"][0]["implementation_proposal"] = {}
        self.assertEqual(
            _validate_state_payload("ZHONGSHU_SOLVER", payload),
            "SOLVER_IMPLEMENTATION_DETAIL_FORBIDDEN",
        )

    def test_solver_ready_for_critic_rejects_missing_top_level_items(self):
        payload = self._valid_solver_payload()
        payload["plan"].pop("items")
        self.assertEqual(
            _validate_state_payload("ZHONGSHU_SOLVER", payload),
            "SOLVER_ITEMS_MISSING",
        )

    def test_solver_ready_for_critic_rejects_incomplete_requirements(self):
        payload = self._valid_solver_payload()
        payload["plan"]["requirements"] = [dict(ANALYST_PLAN["requirements"][0])]
        self.assertEqual(_validate_state_payload("ZHONGSHU_SOLVER", payload, self._ctx()), "")
        payload["plan"]["requirements"] = [{"requirement_id": "REQ-2"}]
        self.assertEqual(
            _validate_state_payload(
                "ZHONGSHU_SOLVER",
                payload,
                self._ctx(),
            ),
            "SOLVER_REQUIREMENTS_INCOMPLETE",
        )

    def test_solver_ready_for_critic_rejects_item_index_mismatch(self):
        payload = self._valid_solver_payload()
        payload["plan"]["items"] = [
            dict(payload["plan"]["items"][0], item_id="item-2")
        ]
        self.assertEqual(
            _validate_state_payload("ZHONGSHU_SOLVER", payload),
            "SOLVER_ITEMS_INDEX_MISMATCH",
        )


if __name__ == "__main__":
    unittest.main()
