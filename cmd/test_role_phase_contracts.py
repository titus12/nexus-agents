from __future__ import annotations

import copy
import unittest
from typing import Any

from orchestrator.contracts import contract_for_state
from orchestrator.context import StateContext
from orchestrator.states import _attach_repair_feedback
from orchestrator.structured_output import build_structured_output_spec, validate_role_result_shape


CONTRACT_STATES = (
    "ZHONGSHU_ANALYST",
    "ZHONGSHU_SOLVER",
    "ZHONGSHU_CRITIC",
    "MENXIA_ITEM_SOLVER",
    "MENXIA_ITEM_ANALYST",
    "MENXIA_ITEM_CRITIC",
    "MENXIA_GROUP_GATE",
)


def valid_payload(state: str) -> dict[str, Any]:
    return copy.deepcopy(contract_for_state(state).example_payload())


class RolePhaseContractTests(unittest.TestCase):
    def test_all_states_have_distinct_contract_ids(self):
        contracts = [contract_for_state(state) for state in CONTRACT_STATES]
        self.assertEqual(len({contract.contract_id for contract in contracts}), 7)

    def test_every_example_payload_is_valid(self):
        for state in CONTRACT_STATES:
            with self.subTest(state=state):
                self.assertEqual(contract_for_state(state).validate(valid_payload(state)), ())

    def test_contract_id_is_a_root_hard_gate(self):
        payload = valid_payload("ZHONGSHU_ANALYST")
        payload.pop("contract_id")
        errors = contract_for_state("ZHONGSHU_ANALYST").validate(payload)
        self.assertIn("contract_id: required", errors)

    def test_missing_nested_decision_relevance_is_rejected(self):
        payload = valid_payload("ZHONGSHU_ANALYST")
        payload["evidence_updates"] = [{
            "evidence_id": "ev-1",
            "requirement_id": "REQ-001",
            "source": "a.py:1",
            "conclusion": "verified",
        }]
        errors = contract_for_state("ZHONGSHU_ANALYST").validate(payload)
        self.assertIn("evidence_updates[0].decision_relevance: required", errors)

    def test_analyst_schema_forbids_task_candidates(self):
        payload = valid_payload("ZHONGSHU_ANALYST")
        payload["candidate_items"] = [{"item_id": "must-not-exist"}]
        payload["candidate_groups"] = [{"candidate_group_id": "must-not-exist"}]
        errors = contract_for_state("ZHONGSHU_ANALYST").validate(payload)
        self.assertIn("candidate_items: more than maxItems", errors)
        self.assertIn("candidate_groups: more than maxItems", errors)

    def test_legacy_relevance_alias_is_rejected(self):
        payload = valid_payload("ZHONGSHU_ANALYST")
        update = payload["evidence_updates"][0]
        update["relevance"] = "coverage"
        errors = contract_for_state("ZHONGSHU_ANALYST").validate(payload)
        self.assertIn("evidence_updates[0].relevance: forbidden", errors)

    def test_illegal_decision_relevance_is_rejected(self):
        payload = valid_payload("ZHONGSHU_ANALYST")
        payload["evidence_updates"][0]["decision_relevance"] = "other"
        errors = contract_for_state("ZHONGSHU_ANALYST").validate(payload)
        self.assertIn(
            "evidence_updates[0].decision_relevance: invalid enum",
            errors,
        )

    def test_prompt_schema_and_runtime_validator_share_nested_contract(self):
        spec = build_structured_output_spec(
            "ZHONGSHU",
            "review-analyst",
            {"active_runtime_state": "ZHONGSHU_ANALYST", "zhongshu_dispatch_mode": "evidence_collection"},
        )
        self.assertIsNotNone(spec)
        evidence_schema = spec.schema["properties"]["evidence_updates"]["items"]
        self.assertIn("decision_relevance", evidence_schema["required"])

        payload = valid_payload("ZHONGSHU_ANALYST")
        payload["task_id"] = "task-1"
        payload["request_id"] = "request-1"
        payload["structured_output_schema_hash"] = spec.schema_hash
        payload["state"] = "ZHONGSHU_ANALYST"
        payload["mode"] = "EVIDENCE_COLLECTION_READ_ONLY"
        self.assertEqual(
            validate_role_result_shape(
                payload,
                phase="ZHONGSHU",
                role="review-analyst",
                state="ZHONGSHU_ANALYST",
                role_mode="EVIDENCE_COLLECTION_READ_ONLY",
                expected_schema_hash=spec.schema_hash,
            ),
            "",
        )
        del payload["evidence_updates"][0]["decision_relevance"]
        self.assertIn(
            "evidence_updates[0].decision_relevance: required",
            validate_role_result_shape(
                payload,
                phase="ZHONGSHU",
                role="review-analyst",
                state="ZHONGSHU_ANALYST",
                role_mode="EVIDENCE_COLLECTION_READ_ONLY",
                expected_schema_hash=spec.schema_hash,
            ),
        )

    def test_retry_prompt_contains_only_targeted_contract_paths(self):
        context = StateContext(
            task_id="task-1",
            workflow_state="ZHONGSHU_ANALYST",
            current_phase="ZHONGSHU",
            current_role="review-analyst",
            resume_state="ZHONGSHU_ANALYST",
            last_error={
                "code": "AGENT_REPLY_PROTOCOL_REJECTED",
                "reason": "STRUCTURED_ROLE_CONTRACT_INVALID:evidence_updates[0].decision_relevance: required",
            },
        )
        repaired = _attach_repair_feedback('{"task":"keep"}', context)
        self.assertIn("contract_repair", repaired)
        self.assertIn("evidence_updates[0].decision_relevance", repaired)
        self.assertNotIn("last_rejected_reply", repaired)

    def test_solver_groups_use_item_ids_as_the_only_transport_shape(self):
        payload = valid_payload("ZHONGSHU_SOLVER")
        payload["plan"] = {
            "requirements": [{
                "requirement_id": "REQ-001",
                "statement": "Requirement",
                "source": "user",
                "priority": "must",
                "scope": "in",
                "kind": "task",
                "acceptance_signal": "Observable result",
            }],
            "items": [{
                "item_id": "item-001",
                "title": "Task",
                "objective": "Do the task",
                "source_requirement_ids": ["REQ-001"],
                "dependencies": [],
                "acceptance_signals": ["Observable result"],
                "unknowns": [],
                "risks": [],
                "parallelizable": True,
            }],
            "groups": [{"group_id": "group-001", "item_ids": ["item-001"]}],
            "dependencies": [],
            "scope": {},
            "unknowns": [],
            "risks": [],
        }
        self.assertEqual(contract_for_state("ZHONGSHU_SOLVER").validate(payload), ())

        payload["plan"]["groups"][0].pop("item_ids")
        payload["plan"]["groups"][0]["items"] = [payload["plan"]["items"][0]]
        errors = contract_for_state("ZHONGSHU_SOLVER").validate(payload)
        self.assertIn("plan.groups[0].item_ids: required", errors)
        self.assertIn("plan.groups[0].items: forbidden", errors)

    def test_solver_compact_group_shape_is_the_runtime_structured_contract(self):
        spec = build_structured_output_spec(
            "ZHONGSHU",
            "review-solver",
            {"active_runtime_state": "ZHONGSHU_SOLVER"},
        )
        payload = copy.deepcopy(contract_for_state("ZHONGSHU_SOLVER").example_payload())
        payload.update({
            "task_id": "task-1",
            "request_id": "request-1",
            "state": "ZHONGSHU_SOLVER",
            "role": "review-solver",
            "structured_output_schema_hash": spec.schema_hash,
        })
        payload["plan"] = {
            "requirements": [],
            "items": [],
            "groups": [{"group_id": "group-001", "item_ids": ["item-001"]}],
            "dependencies": [],
            "scope": {},
            "unknowns": [],
            "risks": [],
        }
        self.assertEqual(
            validate_role_result_shape(
                payload,
                phase="ZHONGSHU",
                role="review-solver",
                state="ZHONGSHU_SOLVER",
                role_mode="TASK_GRAPH_FORMALIZATION_READ_ONLY",
                expected_schema_hash=spec.schema_hash,
            ),
            "",
        )
        payload["plan"]["groups"][0].pop("item_ids")
        payload["plan"]["groups"][0]["items"] = []
        shape_error = validate_role_result_shape(
            payload,
            phase="ZHONGSHU",
            role="review-solver",
            state="ZHONGSHU_SOLVER",
            role_mode="TASK_GRAPH_FORMALIZATION_READ_ONLY",
            expected_schema_hash=spec.schema_hash,
        )
        self.assertIn("plan.groups[0].item_ids: required", shape_error)
        self.assertIn("plan.groups[0].items: forbidden", shape_error)


if __name__ == "__main__":
    unittest.main()
