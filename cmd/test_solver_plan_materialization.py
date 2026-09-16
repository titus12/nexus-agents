from __future__ import annotations

from dataclasses import replace
import unittest

from orchestrator.domain.context import (
    ProgressState,
    RecoveryState,
    ReviewState,
    TaskIdentity,
    WorkflowContext,
)
from orchestrator.domain.decisions import EffectRequest
from orchestrator.domain.events import DomainEvent
from orchestrator.domain.findings import Finding
from orchestrator.domain.policies.solver_plan import (
    apply_solver_changes,
    carry_forward_revision_items,
    is_retryable_solver_reply_error,
    materialize_solver_reply,
    normalize_solver_finding_ids,
    resolve_finding_ids,
    solver_batch_coverage_error,
    solver_revision_response_error,
    structural_integrity_errors,
)
from orchestrator.domain.states import ZhongshuSolverState
from orchestrator.runtime.plan_effects import PlanArtifactRunner
from orchestrator.runtime.ports import ArtifactInput, ArtifactReceipt
from orchestrator.runtime.reducer import LinearContextReducer
from orchestrator.runtime.repository import WorkflowSnapshot


class _FakeArtifacts:
    def __init__(self) -> None:
        self.writes: list[ArtifactInput] = []

    def write(self, artifact: ArtifactInput) -> ArtifactReceipt:
        self.writes.append(artifact)
        return ArtifactReceipt(artifact_id="artifact-1", digest="digest-1")

    def read(self, task_id: str, artifact_id: str) -> bytes:
        return b""


def _item(item_id: str, objective: str = "Do one") -> dict[str, object]:
    return {
        "item_id": item_id,
        "title": item_id,
        "objective": objective,
        "source_requirement_ids": [],
        "dependencies": [],
        "acceptance_signals": ["observable"],
        "unknowns": [],
        "risks": [],
        "parallelizable": True,
    }


def _plan() -> dict[str, object]:
    return {
        "requirements": [],
        "items": [_item("item-1"), _item("item-2")],
        "groups": [{"group_id": "group-1", "item_ids": ["item-1", "item-2"]}],
        "dependencies": [],
        "scope": {},
        "unknowns": [],
        "risks": [],
    }


def _finding(finding_id: str, item_id: str = "item-1") -> Finding:
    return Finding(
        finding_id=finding_id,
        severity="P1",
        status="OPEN",
        group_id="group-1",
        item_id=item_id,
        claim="blocking",
        required_action="fix",
    )


class SolverPlanPolicyTests(unittest.TestCase):
    def test_noop_revision_materializes_current_plan(self) -> None:
        plan, error = materialize_solver_reply({"action": "READY_FOR_CRITIC", "changes": []}, _plan())
        self.assertEqual(error, "")
        self.assertEqual(plan, _plan())

    def test_replace_item_fields_patches_item(self) -> None:
        plan, error = apply_solver_changes(
            _plan(),
            [{"op": "replace_item_fields", "item_id": "item-1", "fields": {"objective": "Do one better"}}],
        )
        self.assertEqual(error, "")
        self.assertEqual(plan["items"][0]["objective"], "Do one better")

    def test_unknown_item_is_rejected(self) -> None:
        _, error = apply_solver_changes(
            _plan(),
            [{"op": "replace_item_fields", "item_id": "missing", "fields": {"title": "x"}}],
        )
        self.assertEqual(error, "SOLVER_CHANGE_ITEM_UNKNOWN:missing")

    def test_plan_wins_over_descriptive_changes_on_revision(self) -> None:
        revised = _plan()
        revised["items"][0]["objective"] = "Fixed"
        plan, error = materialize_solver_reply(
            {
                "plan": revised,
                "changes": [
                    {"target": "item-1", "fields": ["objective"], "description": "fixed"}
                ],
            },
            _plan(),
        )
        self.assertEqual(error, "")
        self.assertEqual(plan["items"][0]["objective"], "Fixed")

    def test_plan_is_used_even_without_current_plan(self) -> None:
        plan, error = materialize_solver_reply(
            {"plan": _plan(), "changes": [{"op": "replace_plan_fields", "fields": {"scope": {}}}]},
            None,
        )
        self.assertEqual(error, "")
        self.assertIsNotNone(plan)

    def test_typed_changes_apply_when_no_plan_is_supplied(self) -> None:
        plan, error = materialize_solver_reply(
            {"changes": [{"op": "replace_item_fields", "item_id": "item-1", "fields": {"objective": "Fixed"}}]},
            _plan(),
        )
        self.assertEqual(error, "")
        self.assertEqual(plan["items"][0]["objective"], "Fixed")

    def test_changes_without_current_plan_is_rejected(self) -> None:
        _, error = materialize_solver_reply(
            {"changes": [{"op": "replace_plan_fields", "fields": {"scope": {}}}]}, None
        )
        self.assertEqual(error, "SOLVER_CURRENT_PLAN_MISSING")

    def test_coverage_error_reports_missing_and_unknown(self) -> None:
        error = solver_batch_coverage_error(
            ["f-1"],
            {"finding_batch": {"selected_finding_ids": ["f-2"], "remaining_finding_ids": []}},
        )
        self.assertEqual(
            error, "SOLVER_FINDING_BATCH_COVERAGE_INCOMPLETE:missing=['f-1'],unknown=['f-2']"
        )

    def test_coverage_ok_partitions_exactly(self) -> None:
        error = solver_batch_coverage_error(
            ["f-1", "f-2"],
            {"finding_batch": {"selected_finding_ids": ["f-1"], "remaining_finding_ids": ["f-2"]}},
        )
        self.assertEqual(error, "")

    def test_revision_response_error_flags_silent_noop(self) -> None:
        error = solver_revision_response_error(
            ["f-1"], True, {"changes": [], "finding_resolutions": []}
        )
        self.assertEqual(error, "SOLVER_REVISION_NO_RESPONSE:active=f-1")

    def test_revision_response_error_accepts_declared_resolutions(self) -> None:
        error = solver_revision_response_error(
            ["f-1"],
            True,
            {"changes": [], "finding_resolutions": [{"finding_id": "f-1"}]},
        )
        self.assertEqual(error, "")

    def test_revision_response_error_ignores_non_revision(self) -> None:
        error = solver_revision_response_error(["f-1"], False, {"changes": []})
        self.assertEqual(error, "")

    def test_revision_response_error_ignores_no_active_findings(self) -> None:
        error = solver_revision_response_error([], True, {"changes": []})
        self.assertEqual(error, "")

    def test_batch_errors_are_retryable_reply_slips(self) -> None:
        self.assertTrue(
            is_retryable_solver_reply_error(
                "SOLVER_FINDING_BATCH_COVERAGE_INCOMPLETE:missing=[],unknown=[]"
            )
        )
        self.assertTrue(
            is_retryable_solver_reply_error("SOLVER_FINDING_BATCH_OVERLAP:['f-1']")
        )
        self.assertFalse(is_retryable_solver_reply_error("TASK_REVIEW_RESULT_INVALID"))

    def test_resolve_finding_ids_accepts_exact_and_unique_prefix(self) -> None:
        active = ["finding-000001", "finding-b141e6cfb253c123706e"]
        self.assertEqual(
            resolve_finding_ids(["finding-000001", "finding-b141e6cf"], active),
            ["finding-000001", "finding-b141e6cfb253c123706e"],
        )

    def test_resolve_finding_ids_matches_case_and_whitespace_insensitively(self) -> None:
        active = ["finding-b141e6cfb253c123706e"]
        self.assertEqual(
            resolve_finding_ids(["  FINDING-B141E6CF  "], active),
            ["finding-b141e6cfb253c123706e"],
        )

    def test_resolve_finding_ids_does_not_guess_ambiguous_prefixes(self) -> None:
        active = ["finding-b141e6cfb253c123706e", "finding-b141e6cfb253c123706e-2"]
        self.assertEqual(
            resolve_finding_ids(["finding-b141e6cf"], active), ["finding-b141e6cf"]
        )

    def test_resolve_finding_ids_prefers_exact_over_suffixed_sibling(self) -> None:
        active = ["finding-b141e6cfb253c123706e", "finding-b141e6cfb253c123706e-2"]
        self.assertEqual(
            resolve_finding_ids(["finding-b141e6cfb253c123706e"], active),
            ["finding-b141e6cfb253c123706e"],
        )

    def test_resolve_finding_ids_passes_unknown_ids_through(self) -> None:
        self.assertEqual(resolve_finding_ids(["finding-nope"], ["f-1"]), ["finding-nope"])

    def test_resolve_finding_ids_tolerates_missing_prefix_and_wrappers(self) -> None:
        active = ["finding-b141e6cfb253c123706e"]
        self.assertEqual(
            resolve_finding_ids(
                ["b141e6cf", "`finding-b141e6cfb253c123706e`", "\"FINDING-B141E6CF\""],
                active,
            ),
            ["finding-b141e6cfb253c123706e"] * 3,
        )

    def test_resolve_finding_ids_never_resolves_an_empty_key(self) -> None:
        self.assertEqual(resolve_finding_ids(["finding-"], ["finding-000001"]), ["finding-"])

    def test_coverage_accepts_abbreviated_ids(self) -> None:
        active = [
            "finding-000001",
            "finding-b141e6cfb253c123706e",
            "finding-f9a314bfcf679d936cd6",
        ]
        error = solver_batch_coverage_error(
            active,
            {
                "finding_batch": {
                    "selected_finding_ids": ["finding-000001", "finding-b141e6cf"],
                    "remaining_finding_ids": ["finding-f9a314bf"],
                }
            },
        )
        self.assertEqual(error, "")

    def test_coverage_still_reports_genuinely_unknown_ids(self) -> None:
        error = solver_batch_coverage_error(
            ["finding-000001"],
            {
                "finding_batch": {
                    "selected_finding_ids": ["finding-000001"],
                    "remaining_finding_ids": ["finding-nope"],
                }
            },
        )
        self.assertEqual(
            error,
            "SOLVER_FINDING_BATCH_COVERAGE_INCOMPLETE:missing=[],unknown=['finding-nope']",
        )

    def test_coverage_accepts_the_633b71_truncated_batch(self) -> None:
        """Replay of task-20260915-633b71's Solver finding_batch verbatim."""

        active = [
            "finding-000001",
            "finding-000002",
            "finding-000004",
            "finding-000005",
            "finding-b141e6cfb253c123706e",
            "finding-b462e3a2053ca51160d3",
            "finding-cd812b4f3e14a216f155",
            "finding-d0b1a4ea90b7cff74c69",
            "finding-f9a314bfcf679d936cd6",
        ]
        payload = {
            "finding_batch": {
                "selected_finding_ids": [
                    "finding-000002",
                    "finding-f9a314bf",
                    "finding-000001",
                    "finding-cd812b4f",
                    "finding-d0b1a4ea",
                    "finding-000004",
                ],
                "remaining_finding_ids": [
                    "finding-000005",
                    "finding-b141e6cf",
                    "finding-b462e3a2",
                ],
            }
        }
        self.assertEqual(solver_batch_coverage_error(active, payload), "")

    def test_normalize_solver_finding_ids_canonicalizes_batch_and_resolutions(self) -> None:
        active = ["finding-000001", "finding-b141e6cfb253c123706e"]
        normalized = normalize_solver_finding_ids(
            {
                "finding_batch": {
                    "selected_finding_ids": ["finding-b141e6cf", "finding-000001", "finding-000001"],
                    "remaining_finding_ids": [],
                },
                "finding_resolutions": [
                    {"finding_id": "finding-b141e6cf", "response": "fixed"}
                ],
            },
            active,
        )

        self.assertEqual(
            normalized["finding_batch"]["selected_finding_ids"],
            ["finding-b141e6cfb253c123706e", "finding-000001"],
        )
        self.assertEqual(
            normalized["finding_resolutions"][0]["finding_id"],
            "finding-b141e6cfb253c123706e",
        )

    def test_normalize_solver_finding_ids_leaves_other_fields_intact(self) -> None:
        payload = {"action": "READY_FOR_CRITIC", "changes": [{"op": "noop"}]}
        normalized = normalize_solver_finding_ids(payload, ["finding-000001"])
        self.assertEqual(normalized, payload)

    def test_replace_plan_fields_cannot_touch_topology(self) -> None:
        _, error = apply_solver_changes(
            _plan(),
            [{"op": "replace_plan_fields", "fields": {"items": []}}],
        )
        self.assertEqual(error, "SOLVER_CHANGE_PLAN_FIELD_FORBIDDEN:['items']")

    def test_structural_integrity_detects_group_unknown_item(self) -> None:
        plan = _plan()
        plan["groups"] = [{"group_id": "group-1", "item_ids": ["item-1", "missing"]}]
        errors = structural_integrity_errors(plan)
        self.assertIn("GROUP_UNKNOWN_ITEM:group-1->missing", errors)

    def test_structural_integrity_detects_dependency_cycle(self) -> None:
        plan = _plan()
        plan["items"][0]["dependencies"] = ["item-2"]
        plan["items"][1]["dependencies"] = ["item-1"]
        errors = structural_integrity_errors(plan)
        self.assertTrue(
            any(error.startswith("DEPENDENCY_CYCLE:") for error in errors), errors
        )

    def test_structural_integrity_accepts_valid_plan(self) -> None:
        self.assertEqual(structural_integrity_errors(_plan()), [])

    def test_scoped_revision_carries_untouched_items(self) -> None:
        current = _plan()
        revised = _plan()
        revised["items"][0]["objective"] = "Fixed"
        revised["items"][1]["objective"] = "Reworded"

        merged = carry_forward_revision_items(revised, current, {"item-1"})
        by_id = {item["item_id"]: item for item in merged["items"]}

        self.assertEqual(by_id["item-1"]["objective"], "Fixed")
        self.assertEqual(by_id["item-2"]["objective"], "Do one")

    def test_scoped_revision_keeps_out_of_scope_dependency_changes(self) -> None:
        current = _plan()
        revised = _plan()
        revised["items"][1]["dependencies"] = ["item-1"]

        merged = carry_forward_revision_items(revised, current, {"item-1"})
        by_id = {item["item_id"]: item for item in merged["items"]}

        self.assertEqual(by_id["item-2"]["dependencies"], ["item-1"])

    def test_scoped_revision_reinstates_dropped_untouched_item(self) -> None:
        current = _plan()
        revised = _plan()
        del revised["items"][1]

        merged = carry_forward_revision_items(revised, current, {"item-1"})

        self.assertEqual([item["item_id"] for item in merged["items"]], ["item-1", "item-2"])

    def test_materialize_scoped_revision_carries_untouched_items(self) -> None:
        current = _plan()
        revised = _plan()
        revised["items"][0]["objective"] = "Fixed"
        revised["items"][1]["objective"] = "Reworded"

        plan, error = materialize_solver_reply(
            {"plan": revised}, current, editable_item_ids={"item-1"}
        )
        by_id = {item["item_id"]: item for item in plan["items"]}

        self.assertEqual(error, "")
        self.assertEqual(by_id["item-1"]["objective"], "Fixed")
        self.assertEqual(by_id["item-2"]["objective"], "Do one")

    def test_materialize_without_scope_keeps_full_plan(self) -> None:
        current = _plan()
        revised = _plan()
        revised["items"][1]["objective"] = "Reworded"

        plan, _ = materialize_solver_reply({"plan": revised}, current)

        self.assertEqual(plan["items"][1]["objective"], "Reworded")


class SolverStateMaterializationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = ZhongshuSolverState()
        self.reducer = LinearContextReducer()

    def _context(self, plan: dict[str, object] | None, findings: tuple[Finding, ...] = ()) -> WorkflowContext:
        return WorkflowContext(
            identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
            progression=ProgressState("ZHONGSHU_SOLVER", 5, "2026-09-13T00:00:00Z"),
            review=ReviewState(
                revision_id="task-1:ZHONGSHU_ANALYST:2",
                findings=findings,
                plan=plan,
                plan_hash="plan-hash",
            ),
        )

    def _event(self, payload: dict[str, object]) -> DomainEvent:
        return DomainEvent("READY_FOR_CRITIC", "task-1", 5, payload, "2026-09-13T00:00:00Z")

    def test_bounded_changes_materialize_into_review_plan(self) -> None:
        context = self._context(_plan(), (_finding("f-1"),))
        event = self._event(
            {
                "action": "READY_FOR_CRITIC",
                "changes": [
                    {"op": "replace_item_fields", "item_id": "item-1", "fields": {"objective": "Fixed"}}
                ],
                "finding_batch": {"selected_finding_ids": ["f-1"], "remaining_finding_ids": []},
            }
        )
        decision = self.state.handle(context, event)

        self.assertEqual(decision.transition.action, "READY_FOR_CRITIC")
        self.assertEqual(decision.update.review.plan["items"][0]["objective"], "Fixed")

        after = self.reducer.apply(WorkflowSnapshot("task-1", context, 0), decision)
        self.assertEqual(after.context.progression.state, "ZHONGSHU_CRITIC")
        self.assertEqual(after.context.review.plan["items"][0]["objective"], "Fixed")

    def test_incomplete_batch_retries_solver(self) -> None:
        context = self._context(_plan(), (_finding("f-1"),))
        event = self._event(
            {
                "action": "READY_FOR_CRITIC",
                "changes": [],
                "finding_batch": {"selected_finding_ids": ["f-other"], "remaining_finding_ids": []},
            }
        )
        decision = self.state.handle(context, event)

        self.assertEqual(decision.transition.action, "RETRY")
        self.assertEqual(decision.update.recovery.reply_retry_count, 1)
        self.assertIsNone(decision.update.recovery.retry_count)
        self.assertEqual(decision.update.progression.resume_state, "ZHONGSHU_SOLVER")
        self.assertFalse(decision.effects)

    def test_incomplete_batch_blocks_once_reply_budget_spent(self) -> None:
        context = replace(
            self._context(_plan(), (_finding("f-1"),)),
            recovery=RecoveryState(reply_retry_count=3, max_reply_retries=3),
        )
        event = self._event(
            {
                "action": "READY_FOR_CRITIC",
                "changes": [],
                "finding_batch": {"selected_finding_ids": ["f-other"], "remaining_finding_ids": []},
            }
        )
        decision = self.state.handle(context, event)

        self.assertEqual(decision.transition.action, "BLOCKED")
        self.assertEqual(
            decision.transition.reason_code, "ZHONGSHU_SOLVER_REVISION_INVALID"
        )

    def test_reply_slip_does_not_spend_the_convergence_budget(self) -> None:
        context = replace(
            self._context(_plan(), (_finding("f-1"),)),
            recovery=RecoveryState(retry_count=3, max_retries=3),
        )
        event = self._event(
            {
                "action": "READY_FOR_CRITIC",
                "changes": [],
                "finding_batch": {"selected_finding_ids": ["f-other"], "remaining_finding_ids": []},
            }
        )
        decision = self.state.handle(context, event)

        # An exhausted convergence budget must not turn a mechanical slip into
        # a block while the dedicated reply budget is still available.
        self.assertEqual(decision.transition.action, "RETRY")
        self.assertEqual(decision.update.recovery.reply_retry_count, 1)

    def test_abbreviated_finding_ids_do_not_block_the_revision(self) -> None:
        """Replay of task-20260915-633b71: the Solver abbreviated hash ids."""

        full_a = "finding-b141e6cfb253c123706e"
        full_b = "finding-f9a314bfcf679d936cd6"
        context = self._context(
            _plan(),
            (_finding(full_a, "item-1"), _finding(full_b, "item-2")),
        )
        revised = _plan()
        revised["items"][0]["objective"] = "Fixed"
        revised["items"][1]["objective"] = "Reworded"
        event = self._event(
            {
                "action": "READY_FOR_CRITIC",
                "plan": revised,
                "finding_batch": {
                    "selected_finding_ids": ["finding-b141e6cf"],
                    "remaining_finding_ids": ["finding-f9a314bf"],
                },
            }
        )
        decision = self.state.handle(context, event)

        self.assertEqual(decision.transition.action, "READY_FOR_CRITIC")
        by_id = {
            item["item_id"]: item for item in decision.update.review.plan["items"]
        }
        # The abbreviated selected id must still scope the revision to item-1.
        self.assertEqual(by_id["item-1"]["objective"], "Fixed")
        self.assertEqual(by_id["item-2"]["objective"], "Do one")

    def test_revision_that_ignores_active_findings_retries_solver(self) -> None:
        context = self._context(_plan(), (_finding("f-1"),))
        event = self._event(
            {
                "action": "READY_FOR_CRITIC",
                "changes": [],
                "finding_resolutions": [],
                "summary": "Revision round with no active findings. Plan unchanged.",
            }
        )
        decision = self.state.handle(context, event)

        self.assertEqual(decision.transition.action, "RETRY")
        self.assertEqual(decision.update.recovery.reply_retry_count, 1)
        self.assertIsNone(decision.update.recovery.retry_count)
        self.assertEqual(decision.update.progression.resume_state, "ZHONGSHU_SOLVER")
        self.assertFalse(decision.effects)

    def test_revision_with_finding_resolutions_proceeds(self) -> None:
        context = self._context(_plan(), (_finding("f-1"),))
        event = self._event(
            {
                "action": "READY_FOR_CRITIC",
                "changes": [],
                "finding_resolutions": [
                    {
                        "finding_id": "f-1",
                        "response": "routed to analyst for missing evidence",
                        "owner_role": "review-analyst",
                        "next_action": "collect evidence",
                    }
                ],
            }
        )
        decision = self.state.handle(context, event)

        self.assertEqual(decision.transition.action, "READY_FOR_CRITIC")

    def test_revision_with_plan_uses_the_plan(self) -> None:
        context = self._context(_plan(), (_finding("f-1"),))
        revised = _plan()
        revised["items"][0]["objective"] = "Fixed"
        event = self._event(
            {
                "action": "READY_FOR_CRITIC",
                "plan": revised,
                "changes": [
                    {"target": "item-1", "fields": ["objective"], "description": "fixed"}
                ],
            }
        )
        decision = self.state.handle(context, event)

        self.assertEqual(decision.transition.action, "READY_FOR_CRITIC")
        self.assertEqual(decision.update.review.plan["items"][0]["objective"], "Fixed")

    def test_scoped_revision_only_applies_selected_item_changes(self) -> None:
        context = self._context(
            _plan(), (_finding("f-1", "item-1"), _finding("f-2", "item-2"))
        )
        revised = _plan()
        revised["items"][0]["objective"] = "Fixed"
        revised["items"][1]["objective"] = "Reworded"
        event = self._event(
            {
                "action": "READY_FOR_CRITIC",
                "plan": revised,
                "finding_batch": {
                    "selected_finding_ids": ["f-1"],
                    "remaining_finding_ids": ["f-2"],
                },
            }
        )
        decision = self.state.handle(context, event)

        self.assertEqual(decision.transition.action, "READY_FOR_CRITIC")
        by_id = {
            item["item_id"]: item for item in decision.update.review.plan["items"]
        }
        self.assertEqual(by_id["item-1"]["objective"], "Fixed")
        self.assertEqual(by_id["item-2"]["objective"], "Do one")

    def test_retryable_reply_blocks_once_reply_budget_spent(self) -> None:
        context = replace(
            self._context(_plan(), (_finding("f-1"),)),
            recovery=RecoveryState(reply_retry_count=3, max_reply_retries=3),
        )
        event = self._event(
            {
                "action": "READY_FOR_CRITIC",
                "changes": [
                    {"op": "replace_item_fields", "item_id": "missing", "fields": {"objective": "x"}}
                ],
            }
        )
        decision = self.state.handle(context, event)

        self.assertEqual(decision.transition.action, "BLOCKED")
        self.assertEqual(
            decision.transition.reason_code, "ZHONGSHU_SOLVER_REVISION_INVALID"
        )

    def test_plan_artifact_effect_is_emitted(self) -> None:
        context = self._context(_plan(), (_finding("f-1"),))
        event = self._event(
            {
                "action": "READY_FOR_CRITIC",
                "changes": [
                    {"op": "replace_item_fields", "item_id": "item-1", "fields": {"objective": "Fixed"}}
                ],
            }
        )
        decision = self.state.handle(context, event)

        plan_effects = [
            effect for effect in decision.effects if effect.effect_type == "plan_artifact"
        ]
        self.assertEqual(len(plan_effects), 1)
        self.assertEqual(
            plan_effects[0].payload["plan"]["items"][0]["objective"], "Fixed"
        )

    def test_structurally_invalid_plan_blocks(self) -> None:
        cycle_plan = _plan()
        cycle_plan["items"][0]["dependencies"] = ["item-2"]
        cycle_plan["items"][1]["dependencies"] = ["item-1"]
        context = self._context(None)
        event = self._event({"action": "READY_FOR_CRITIC", "plan": cycle_plan})

        decision = self.state.handle(context, event)

        self.assertEqual(decision.transition.action, "BLOCKED")
        self.assertEqual(
            decision.transition.reason_code, "ZHONGSHU_SOLVER_REVISION_INVALID"
        )


class PlanArtifactRunnerTests(unittest.TestCase):
    def _request(self, plan: object) -> EffectRequest:
        return EffectRequest(
            effect_id="plan:task-1:6",
            effect_type="plan_artifact",
            task_id="task-1",
            idempotency_key="task-1:policy-plan:6",
            payload={"plan": plan, "plan_hash": "h", "revision_id": "rev-1"},
        )

    def test_writes_plan_artifact_without_emitting_event(self) -> None:
        artifacts = _FakeArtifacts()
        outcome = PlanArtifactRunner(artifacts).run_once(self._request(_plan()))

        self.assertEqual(outcome.status, "SUCCEEDED")
        self.assertIsNone(outcome.event_name)
        self.assertEqual(len(artifacts.writes), 1)
        self.assertIn(b'"item-1"', artifacts.writes[0].content)

    def test_missing_port_is_a_noop(self) -> None:
        outcome = PlanArtifactRunner(None).run_once(self._request(_plan()))
        self.assertEqual(outcome.status, "SUCCEEDED")
        self.assertIsNone(outcome.event_name)


if __name__ == "__main__":
    unittest.main()
