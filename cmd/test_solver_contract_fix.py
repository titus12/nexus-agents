from __future__ import annotations

import json
import unittest

from orchestrator.states import (
    _attach_repair_feedback,
    _compact_solver_previous_plan,
    _normalize_solver_payload,
    _validate_solver_plan,
    _validate_state_payload,
)
from orchestrator.context import StateContext
from orchestrator.zhongshu_parallel import canonical_plan_hash
from orchestrator.zhongshu_solver_contract import zhongshu_solver_runtime_rules


class SolverContractNormalizationTests(unittest.TestCase):
    @staticmethod
    def _item(item_id, title, objective):
        return {
            "item_id": item_id,
            "title": title,
            "objective": objective,
            "source_requirement_ids": ["REQ-1"],
            "dependencies": [],
            "acceptance_signals": ["Observable result"],
            "unknowns": [],
            "risks": [],
            "parallelizable": True,
        }

    def _payload(self, group_items):
        items = [
            self._item("item-1", "One", "Do one"),
            self._item("item-2", "Two", "Do two"),
        ]
        return {
            "action": "READY_FOR_CRITIC",
            "plan": {
                "requirements": [{
                    "requirement_id": "REQ-1",
                    "statement": "Requirement",
                    "priority": "must",
                    "scope": "in",
                }],
                "items": items,
                "groups": [
                    {"group_id": "group-1", "items": group_items},
                ],
            },
        }

    def test_string_group_items_are_not_repaired(self):
        normalized, notes = _normalize_solver_payload(
            self._payload(["item-1", "item-2"])
        )
        self.assertEqual(notes, [])
        self.assertEqual(
            normalized["plan"]["groups"][0]["items"],
            ["item-1", "item-2"],
        )
        self.assertEqual(
            _validate_state_payload("ZHONGSHU_SOLVER", normalized),
            "SOLVER_GROUP_ITEM_INVALID:0:0",
        )

    def test_item_ids_are_expanded_from_the_single_item_index(self):
        payload = self._payload([])
        payload["plan"]["groups"][0] = {
            "group_id": "group-1",
            "item_ids": ["item-1", "item-2"],
        }
        normalized, notes = _normalize_solver_payload(payload)
        self.assertEqual(notes, ["groups[0].item_ids->items"])
        self.assertNotIn("item_ids", normalized["plan"]["groups"][0])
        self.assertEqual(_validate_state_payload("ZHONGSHU_SOLVER", normalized), "")

    def test_unknown_reference_is_not_guessed(self):
        normalized, notes = _normalize_solver_payload(
            self._payload(["item-1", "item-unknown"])
        )
        self.assertEqual(notes, [])
        self.assertEqual(
            _validate_state_payload("ZHONGSHU_SOLVER", normalized),
            "SOLVER_GROUP_ITEM_INVALID:0:0",
        )

    def test_non_solver_action_is_unchanged(self):
        payload = self._payload(["item-1", "item-2"])
        payload["action"] = "BLOCKED"
        normalized, notes = _normalize_solver_payload(payload)
        self.assertEqual(normalized, payload)
        self.assertEqual(notes, [])

    def test_solver_rejects_requirement_statement_rewrite(self):
        payload = self._payload([
            self._item("item-1", "One", "Do one"),
            self._item("item-2", "Two", "Do two"),
        ])
        payload["plan"]["requirements"][0]["statement"] = "changed"
        analyst = {
            "requirements": [{
                "requirement_id": "REQ-1",
                "statement": "original",
                "priority": "must",
                "scope": "in",
            }]
        }
        self.assertEqual(
            _validate_solver_plan(payload, analyst),
            "SOLVER_REQUIREMENT_CONTENT_MISMATCH:REQ-1",
        )

    def test_solver_never_allows_additional_requirements(self):
        payload = self._payload([
            self._item("item-1", "One", "Do one"),
            self._item("item-2", "Two", "Do two"),
        ])
        payload["plan"]["requirements"].append({
            "requirement_id": "REQ-2",
            "statement": "Derived optimization",
            "priority": "should",
            "scope": "in",
        })
        analyst = {
            "requirements": [{
                "requirement_id": "REQ-1",
                "statement": "Requirement",
                "priority": "must",
                "scope": "in",
            }]
        }
        self.assertEqual(
            _validate_solver_plan(
                payload,
                analyst,
            ),
            "SOLVER_REQUIREMENTS_INCOMPLETE",
        )

    def test_solver_normalization_binds_requirement_ids_to_analyst(self):
        payload = self._payload([
            self._item("item-1", "One", "Do one"),
            self._item("item-2", "Two", "Do two"),
        ])
        payload["plan"]["requirements"].append({
            "requirement_id": "REQ-2",
            "statement": "Derived optimization",
            "priority": "should",
            "scope": "in",
        })
        analyst = {
            "requirements": [{
                "requirement_id": "REQ-1",
                "statement": "Requirement",
                "priority": "must",
                "scope": "in",
            }]
        }
        normalized, notes = _normalize_solver_payload(payload, analyst)
        self.assertIn("requirements<-analyst_plan_authoritative", notes)
        self.assertEqual(normalized["plan"]["requirements"], analyst["requirements"])
        self.assertEqual(_validate_solver_plan(normalized, analyst), "")

    def test_solver_rejects_unknown_dependency(self):
        payload = self._payload([
            self._item("item-1", "One", "Do one"),
            self._item("item-2", "Two", "Do two"),
        ])
        payload["plan"]["items"][0]["dependencies"] = ["missing-task"]
        payload["plan"]["groups"][0]["items"][0]["dependencies"] = ["missing-task"]
        self.assertEqual(
            _validate_state_payload("ZHONGSHU_SOLVER", payload),
            "SOLVER_DEPENDENCY_UNKNOWN:missing-task",
        )

    def test_solver_rejects_dependency_cycle(self):
        payload = self._payload([
            self._item("item-1", "One", "Do one"),
            self._item("item-2", "Two", "Do two"),
        ])
        payload["plan"]["items"][0]["dependencies"] = ["item-2"]
        payload["plan"]["items"][1]["dependencies"] = ["item-1"]
        payload["plan"]["groups"][0]["items"] = [
            self._item("item-1", "One", "Do one"),
            self._item("item-2", "Two", "Do two"),
        ]
        payload["plan"]["groups"][0]["items"][0]["dependencies"] = ["item-2"]
        payload["plan"]["groups"][0]["items"][1]["dependencies"] = ["item-1"]
        self.assertEqual(
            _validate_state_payload("ZHONGSHU_SOLVER", payload),
            "SOLVER_DEPENDENCY_CYCLE:item-1:cycle=item-1->item-2->item-1",
        )

    def test_solver_accepts_one_way_dependency(self):
        payload = self._payload([
            self._item("item-1", "One", "Do one"),
            self._item("item-2", "Two", "Do two"),
        ])
        payload["plan"]["items"][1]["dependencies"] = ["item-1"]
        payload["plan"]["groups"][0]["items"] = [
            self._item("item-1", "One", "Do one"),
            self._item("item-2", "Two", "Do two"),
        ]
        payload["plan"]["groups"][0]["items"][1]["dependencies"] = ["item-1"]
        self.assertEqual(_validate_state_payload("ZHONGSHU_SOLVER", payload), "")

    def test_solver_rejects_transitive_dependency_cycle_with_path(self):
        payload = self._payload([
            self._item("item-1", "One", "Do one"),
            self._item("item-2", "Two", "Do two"),
        ])
        payload["plan"]["items"].append(self._item("item-3", "Three", "Do three"))
        payload["plan"]["items"][0]["dependencies"] = ["item-2"]
        payload["plan"]["items"][1]["dependencies"] = ["item-3"]
        payload["plan"]["items"][2]["dependencies"] = ["item-1"]
        payload["plan"]["groups"][0]["items"] = [
            self._item("item-1", "One", "Do one"),
            self._item("item-2", "Two", "Do two"),
            self._item("item-3", "Three", "Do three"),
        ]
        payload["plan"]["groups"][0]["items"][0]["dependencies"] = ["item-2"]
        payload["plan"]["groups"][0]["items"][1]["dependencies"] = ["item-3"]
        payload["plan"]["groups"][0]["items"][2]["dependencies"] = ["item-1"]
        self.assertEqual(
            _validate_state_payload("ZHONGSHU_SOLVER", payload),
            "SOLVER_DEPENDENCY_CYCLE:item-1:cycle=item-1->item-2->item-3->item-1",
        )

    def test_solver_retry_receives_dependency_cycle_feedback(self):
        ctx = StateContext(
            task_id="task-1",
            workflow_state="ZHONGSHU_SOLVER",
            resume_state="ZHONGSHU_SOLVER",
            last_error={
                "reason": "SOLVER_DEPENDENCY_CYCLE:item-1:cycle=item-1->item-2->item-1",
            },
        )
        repaired = _attach_repair_feedback('{"action":"READY_FOR_CRITIC"}', ctx)
        repair = json.loads(repaired)["contract_repair"]
        self.assertIn(
            "SOLVER_DEPENDENCY_CYCLE:item-1:cycle=item-1->item-2->item-1",
            repair["errors"],
        )
        self.assertTrue(any("DAG" in instruction or "topological" in instruction for instruction in repair["instruction"]))

    def test_solver_retry_receives_group_transport_feedback(self):
        ctx = StateContext(
            task_id="task-1",
            workflow_state="ZHONGSHU_SOLVER",
            resume_state="ZHONGSHU_SOLVER",
            last_error={"reason": "SOLVER_GROUP_ITEM_MISMATCH:item-1"},
        )
        repaired = _attach_repair_feedback('{"action":"READY_FOR_CRITIC"}', ctx)
        repair = json.loads(repaired)["contract_repair"]
        self.assertIn("SOLVER_GROUP_ITEM_MISMATCH:item-1", repair["errors"])
        self.assertTrue(any("item_ids" in instruction for instruction in repair["instruction"]))

    def test_solver_retry_receives_requirement_boundary_feedback(self):
        ctx = StateContext(
            task_id="task-1",
            workflow_state="ZHONGSHU_SOLVER",
            resume_state="ZHONGSHU_SOLVER",
            last_error={"reason": "SOLVER_REQUIREMENTS_INCOMPLETE"},
        )
        repaired = _attach_repair_feedback('{"action":"READY_FOR_CRITIC"}', ctx)
        repair = json.loads(repaired)["contract_repair"]
        self.assertIn("SOLVER_REQUIREMENTS_INCOMPLETE", repair["errors"])
        self.assertTrue(any("plan.items" in instruction for instruction in repair["instruction"]))

    def test_solver_retry_receives_dynamic_requirement_schema_feedback(self):
        ctx = StateContext(
            task_id="task-1",
            workflow_state="ZHONGSHU_SOLVER",
            resume_state="ZHONGSHU_SOLVER",
            last_error={
                "reason": (
                    "STRUCTURED_ROLE_CONTRACT_INVALID:"
                    "plan.requirements: more than maxItems;"
                    "plan.requirements[1].requirement_id: invalid enum"
                )
            },
        )
        repaired = _attach_repair_feedback('{"action":"READY_FOR_CRITIC"}', ctx)
        repair = json.loads(repaired)["contract_repair"]
        self.assertTrue(any("source_requirement_ids" in instruction for instruction in repair["instruction"]))

    def test_solver_rejects_group_copy_that_changes_task(self):
        payload = self._payload([
            self._item("item-1", "One", "Do one"),
            self._item("item-2", "Two", "Do two"),
        ])
        payload["plan"]["groups"][0]["items"][0]["objective"] = "different"
        self.assertEqual(
            _validate_state_payload("ZHONGSHU_SOLVER", payload),
            "SOLVER_GROUP_ITEM_MISMATCH:item-1",
        )

    def test_solver_prompt_rules_and_previous_plan_use_compact_group_ids(self):
        rules = "\n".join(zhongshu_solver_runtime_rules())
        self.assertIn("MUST use item_ids", rules)
        self.assertIn("Do not emit groups[*].items", rules)

        compact = _compact_solver_previous_plan({
            "items": [{"item_id": "item-1", "title": "One", "objective": "Do one"}],
            "groups": [{
                "group_id": "group-1",
                "items": [{"item_id": "item-1", "title": "One", "objective": "Do one"}],
            }],
        })
        self.assertEqual(compact["groups"], [{"group_id": "group-1", "item_ids": ["item-1"]}])

    def test_solver_rejects_incomplete_task_fields(self):
        payload = self._payload([
            self._item("item-1", "One", "Do one"),
            self._item("item-2", "Two", "Do two"),
        ])
        payload["plan"]["items"][0].pop("acceptance_signals")
        self.assertEqual(
            _validate_state_payload("ZHONGSHU_SOLVER", payload),
            "SOLVER_ITEM_FIELD_MISSING:0:acceptance_signals",
        )

    def test_revision_noop_materializes_orchestrator_owned_plan(self):
        current_plan = self._payload([
            self._item("item-1", "One", "Do one"),
            self._item("item-2", "Two", "Do two"),
        ])["plan"]
        normalized, notes = _normalize_solver_payload(
            {"action": "READY_FOR_CRITIC", "changes": []},
            current_plan=current_plan,
        )
        self.assertEqual(notes, ["plan<-current_plan+changes"])
        self.assertEqual(normalized["plan"], current_plan)
        self.assertEqual(_validate_state_payload("ZHONGSHU_SOLVER", normalized), "")

    def test_revision_item_patch_updates_top_level_and_group_copy(self):
        current_plan = self._payload([
            self._item("item-1", "One", "Do one"),
            self._item("item-2", "Two", "Do two"),
        ])["plan"]
        normalized, notes = _normalize_solver_payload(
            {
                "action": "READY_FOR_CRITIC",
                "changes": [{
                    "op": "replace_item_fields",
                    "item_id": "item-1",
                    "fields": {"objective": "Do one better"},
                }],
            },
            current_plan=current_plan,
        )
        self.assertEqual(notes, ["plan<-current_plan+changes"])
        self.assertEqual(normalized["plan"]["items"][0]["objective"], "Do one better")
        self.assertEqual(normalized["plan"]["groups"][0]["items"][0], normalized["plan"]["items"][0])
        self.assertEqual(_validate_state_payload("ZHONGSHU_SOLVER", normalized), "")

    def test_revision_rejects_untyped_or_unknown_change(self):
        current_plan = self._payload([
            self._item("item-1", "One", "Do one"),
            self._item("item-2", "Two", "Do two"),
        ])["plan"]
        normalized, _ = _normalize_solver_payload(
            {
                "action": "READY_FOR_CRITIC",
                "changes": [{"op": "replace_item_fields", "item_id": "missing", "fields": {"title": "x"}}],
            },
            current_plan=current_plan,
        )
        self.assertEqual(
            _validate_state_payload("ZHONGSHU_SOLVER", normalized),
            "SOLVER_CHANGE_ITEM_UNKNOWN:missing",
        )

    def test_revision_accepts_complete_plan_with_empty_changes(self):
        current_plan = self._payload([
            self._item("item-1", "One", "Do one"),
            self._item("item-2", "Two", "Do two"),
        ])["plan"]
        normalized, _ = _normalize_solver_payload(
            {
                "action": "READY_FOR_CRITIC",
                "plan": current_plan,
                "changes": [],
            },
            current_plan=current_plan,
        )
        self.assertEqual(normalized["plan"], current_plan)
        self.assertEqual(normalized["changes"], [])
        self.assertEqual(_validate_state_payload("ZHONGSHU_SOLVER", normalized), "")

    def test_revision_rejects_complete_plan_with_non_empty_changes(self):
        current_plan = self._payload([
            self._item("item-1", "One", "Do one"),
            self._item("item-2", "Two", "Do two"),
        ])["plan"]
        normalized, _ = _normalize_solver_payload(
            {
                "action": "READY_FOR_CRITIC",
                "plan": current_plan,
                "changes": [{"op": "replace_plan_fields", "fields": {"scope": {}}}],
            },
            current_plan=current_plan,
        )
        self.assertEqual(
            _validate_state_payload("ZHONGSHU_SOLVER", normalized),
            "SOLVER_PLAN_AND_CHANGES_AMBIGUOUS",
        )

    def test_solver_rejects_top_level_implementation_detail(self):
        payload = self._payload([
            self._item("item-1", "One", "Do one"),
            self._item("item-2", "Two", "Do two"),
        ])
        payload["implementation_proposal"] = {"files": ["x.go"]}
        self.assertEqual(
            _validate_state_payload("ZHONGSHU_SOLVER", payload),
            "SOLVER_IMPLEMENTATION_DETAIL_FORBIDDEN",
        )

    def test_serial_critic_must_bind_findings_to_current_plan(self):
        plan = {"items": [], "groups": []}
        ctx = StateContext(task_id="task-1", request_payload={"candidate_plan": plan})
        self.assertEqual(
            _validate_state_payload(
                "ZHONGSHU_CRITIC",
                {"action": "APPROVE_FREEZE", "findings": []},
                ctx,
            ),
            "ZHONGSHU_CRITIC_PLAN_HASH_MISSING",
        )
        self.assertEqual(
            _validate_state_payload(
                "ZHONGSHU_CRITIC",
                {
                    "action": "APPROVE_FREEZE",
                    "findings": [],
                    "reviewed_plan_hash": canonical_plan_hash(plan),
                },
                ctx,
            ),
            "",
        )

    def test_revision_requires_each_active_finding_resolution(self):
        current_plan = self._payload([
            self._item("item-1", "One", "Do one"),
            self._item("item-2", "Two", "Do two"),
        ])["plan"]
        review = {
            "action": "REQUEST_SOLVER_REVISION",
            "findings": [{
                "finding_id": "finding-1",
                "status": "open",
                "severity": "P1",
                "next_action": "REQUEST_SOLVER_REVISION",
            }],
        }
        ctx = StateContext(task_id="task-1", request_payload={
            "analyst_plan": {"requirements": current_plan["requirements"]},
            "candidate_plan": current_plan,
            "zhongshu_critic_review": review,
        })
        payload = {"action": "READY_FOR_CRITIC", "changes": []}
        normalized, _ = _normalize_solver_payload(
            payload,
            current_plan=current_plan,
            revision_requirements=[{"finding_id": "finding-1"}],
        )
        self.assertEqual(
            _validate_state_payload("ZHONGSHU_SOLVER", normalized, ctx),
            "SOLVER_FINDING_RESOLUTIONS_MISSING",
        )
        payload["finding_resolutions"] = [{
            "finding_id": "finding-1",
            "status": "unresolved",
            "response": "The evidence is still insufficient.",
        }]
        normalized, _ = _normalize_solver_payload(
            payload,
            current_plan=current_plan,
            revision_requirements=[{"finding_id": "finding-1"}],
        )
        self.assertEqual(_validate_state_payload("ZHONGSHU_SOLVER", normalized, ctx), "")

    def test_unresolved_revision_routes_to_analyst(self):
        current_plan = self._payload([
            self._item("item-1", "One", "Do one"),
            self._item("item-2", "Two", "Do two"),
        ])["plan"]
        payload = {
            "action": "READY_FOR_CRITIC",
            "changes": [],
            "finding_resolutions": [{
                "finding_id": "finding-1",
                "status": "unresolved",
                "response": "Code-level evidence is still missing.",
            }],
        }
        normalized, _ = _normalize_solver_payload(
            payload,
            current_plan=current_plan,
            revision_requirements=[{"finding_id": "finding-1"}],
        )
        self.assertEqual(normalized["action"], "REQUEST_ANALYST_EVIDENCE")
        self.assertEqual(normalized["plan"], current_plan)
        self.assertEqual(_validate_state_payload("ZHONGSHU_SOLVER", normalized), "")

    def test_resolved_revision_stays_ready_for_critic(self):
        current_plan = self._payload([
            self._item("item-1", "One", "Do one"),
            self._item("item-2", "Two", "Do two"),
        ])["plan"]
        payload = {
            "action": "READY_FOR_CRITIC",
            "changes": [],
            "finding_resolutions": [{
                "finding_id": "finding-1",
                "status": "resolved",
                "response": "The required plan change is complete.",
            }],
        }
        normalized, _ = _normalize_solver_payload(
            payload,
            current_plan=current_plan,
            revision_requirements=[{"finding_id": "finding-1"}],
        )
        self.assertEqual(normalized["action"], "READY_FOR_CRITIC")
        self.assertEqual(_validate_state_payload("ZHONGSHU_SOLVER", normalized), "")

    def test_blocking_unknowns_do_not_skip_critic_or_create_gate(self):
        payload = self._payload([
            self._item("item-1", "One", "Do one"),
            self._item("item-2", "Two", "Do two"),
        ])
        payload["plan"]["unknowns"] = [{
            "unknown_id": "unknown-severity-contract",
            "statement": "Finding severity contract is not confirmed",
            "blocking": True,
        }]

        normalized, notes = _normalize_solver_payload(payload)

        self.assertEqual(normalized["action"], "READY_FOR_CRITIC")
        self.assertNotIn("human_gate", normalized)
        self.assertEqual(
            normalized["blocking_unknowns"],
            payload["plan"]["unknowns"],
        )
        self.assertEqual(notes, ["blocking_unknowns_preserved_for_critic"])
        self.assertEqual(_validate_state_payload("ZHONGSHU_SOLVER", normalized), "")


if __name__ == "__main__":
    unittest.main()
