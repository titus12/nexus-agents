"""Regression checks for bounded request-to-plan workflow stabilization."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from orchestrator.domain.context import (
    MenxiaGroupState, ReviewState, ReviewTaskRecord, apply_review_update,
    context_from_dto, context_to_dto,
)
from orchestrator.domain.events import DomainEvent
from orchestrator.domain.findings import Finding
from orchestrator.domain.policies.menxia_group import apply_menxia_group_results
from orchestrator.domain.policies.zhongshu_group import apply_zhongshu_evidence_round
from orchestrator.domain.states import ZhongshuCriticState, _group_review_bindings
from orchestrator.domain.zhongshu.stages import resolve_reply_stage, SolverStage
from orchestrator.zhongshu_parallel import validate_zhongshu_requirement_contract
from test_zhongshu_freeze_groups import _context, _doc_row


def context(*, pending_peer=False, attempted=None, stuck=2):
    rows = (
        _doc_row("group-000001", "REVIEWING" if pending_peer else "CONVERGED",
                 ("A is observable", "B is observable")),
        _doc_row("group-000002", "REVIEWING", ("C is observable",)),
    )
    c = _context(rows, state="ZHONGSHU_CRITIC")
    f = Finding(finding_id="f2", item_id="item-000002", group_id="group-000002",
                severity="P0", claim="missing evidence", required_action="prove behavior",
                category="evidence_gap", stuck_rounds=stuck)
    ledger = () if pending_peer else (ReviewTaskRecord("item-000001", status="APPROVED"),)
    return replace(c, review=replace(c.review, findings=(f,), task_review_ledger=ledger,
                                     attempted_finding_ids=attempted))


def event(c, action="REQUEST_ANALYST_EVIDENCE"):
    return DomainEvent("NODE_COMPLETED", c.identity.task_id, c.progression.sequence,
                       {"action": action, "findings": [replace(c.review.findings[0], stuck_rounds=0).to_dict()],
                        "group_reviews": [{"group_id": "group-000002", "member_ids": ["item-000002"],
                                           "action": "REVISE_GROUP" if action == "REQUEST_SOLVER_REVISION" else action}]},
                       "2026-10-06T00:00:00Z")


class InitialEvidenceChannelTests(unittest.TestCase):
    def test_evidence_packet_is_not_a_formal_graph(self):
        review = replace(context().review, plan={"evidence_packet_version": 1}, findings=())
        self.assertEqual(resolve_reply_stage({}, review), SolverStage.FORMALIZE)

    def test_formal_graph_with_packet_provenance_is_still_a_revision(self):
        review = replace(context().review, plan={"evidence_packet_version": 1, "items": [{"item_id": "i1"}]}, findings=())
        self.assertEqual(resolve_reply_stage({}, review), SolverStage.REVISE)

    def test_active_critic_findings_do_not_reenter_formalization(self):
        review = replace(context().review, plan={"evidence_packet_version": 1})
        self.assertEqual(resolve_reply_stage({}, review), SolverStage.REVISE)


class PlanPipelineAcceptanceTests(unittest.TestCase):
    def test_fixed_request_repeats_with_real_evidence_gate_restart_and_draft_repair(self):
        from smoke_orchestrator_plan_pipeline import run_once
        with tempfile.TemporaryDirectory() as directory:
            for index in range(1, 4):
                with self.subTest(run=index):
                    output = Path(directory) / str(index)
                    report = run_once(output, index=index)
                    self.assertEqual(report["state"], "DONE")
                    self.assertEqual(report["model_calls"], 0)
                    self.assertTrue(report["contract_transport_guard_checked"])
                    self.assertTrue(report["bad_draft_injected"])
                    self.assertEqual(report["document_versions"], [3])
                    self.assertEqual(report["dispatches"].count("MENXIA_GROUP_SOLVER"), 3)
                    self.assertIn("HUMAN_GATE", [row["state"] for row in report["trace"]])
                    final = (output / "final-plan.md").read_text(encoding="utf-8")
                    self.assertIn("回退", final)
                    self.assertIn("cmd/run_tests.py", final)

class OpportunityAccountingTests(unittest.TestCase):
    def test_evidence_only_legacy_snapshot_does_not_age_solver_failure(self):
        c = context()
        update = ZhongshuCriticState()._review_update(c, event(c).payload)
        self.assertEqual(update.findings[0].stuck_rounds, 2)
        self.assertEqual(update.attempted_finding_ids, ())

    def test_solver_batch_is_consumed_by_one_review_only(self):
        c = context(attempted=("f2",), stuck=0)
        state = ZhongshuCriticState()
        first = state._review_update(c, event(c).payload)
        self.assertEqual(first.findings[0].stuck_rounds, 1)
        c = replace(c, review=apply_review_update(c.review, first))
        second = state._review_update(c, event(c).payload)
        self.assertEqual(second.findings[0].stuck_rounds, 1)

    def test_failed_review_keeps_opportunity_without_aging(self):
        c = context(attempted=("f2",), stuck=0)
        payload = {"action": "FAIL", "findings": []}
        first = ZhongshuCriticState()._review_update(c, payload)
        self.assertEqual(first.findings[0].stuck_rounds, 0)
        self.assertEqual(first.attempted_finding_ids, ("f2",))

    def test_partial_review_consumes_only_valid_group(self):
        c = context(pending_peer=True, attempted=("f1", "f2"), stuck=0)
        f1 = replace(c.review.findings[0], finding_id="f1", item_id="item-000001", group_id="group-000001")
        c = replace(c, review=replace(c.review, findings=(f1, c.review.findings[0])))
        payload = {"action": "FAIL", "findings": [f1.to_dict()],
                   "group_reviews": [{"group_id": "group-000001", "action": "REVISE_GROUP"}]}
        state = ZhongshuCriticState()
        first = state._review_update(c, payload)
        self.assertEqual({f.finding_id: f.stuck_rounds for f in first.findings}, {"f1": 1, "f2": 0})
        self.assertEqual(first.attempted_finding_ids, ("f2",))
        c = replace(c, review=apply_review_update(c.review, first))
        second = state._review_update(c, payload)
        self.assertEqual({f.finding_id: f.stuck_rounds for f in second.findings}, {"f1": 1, "f2": 0})

    def test_local_stuck_group_does_not_stop_pending_peer(self):
        c = context(pending_peer=True, attempted=("f2",))
        decision = ZhongshuCriticState().handle(c, event(c, "REQUEST_SOLVER_REVISION"))
        self.assertEqual(decision.transition.action, "CONTINUE_GROUP_REVIEW")
        rows = {row.group_id: row for row in decision.update.review.zhongshu_groups}
        self.assertEqual(rows["group-000002"].stage, "STALLED")
        dispatch = next(e for e in decision.effects if e.effect_type == "node_dispatch")
        self.assertEqual([b["group_id"] for b in dispatch.payload["bindings"]], ["group-000001"])

    def test_local_stuck_keeps_unreviewed_peer_opportunity(self):
        c = context(pending_peer=True, attempted=("f1", "f2"))
        peer = replace(c.review.findings[0], finding_id="f1", item_id="item-000001", group_id="group-000001", stuck_rounds=0)
        payload = event(c, "REQUEST_SOLVER_REVISION")
        c = replace(c, review=replace(c.review, findings=(peer, c.review.findings[0])))
        decision = ZhongshuCriticState().handle(c, payload)
        self.assertEqual(decision.update.review.attempted_finding_ids, ("f1",))

    def test_parked_group_is_never_selected_by_review_fallback(self):
        c = context(pending_peer=True)
        c = replace(c, review=replace(c.review, zhongshu_groups=tuple(
            replace(row, stage="STALLED") if row.group_id == "group-000002" else row
            for row in c.review.zhongshu_groups)))
        jobs = _group_review_bindings(c, c.review.revision_id, "h")
        self.assertEqual([job["group_id"] for job in jobs], ["group-000001"])

    def test_healthy_peer_repair_excludes_parked_group(self):
        c = context(pending_peer=True, attempted=("f2",))
        f1 = replace(c.review.findings[0], finding_id="f1", item_id="item-000001", group_id="group-000001", stuck_rounds=0)
        c = replace(c, review=replace(c.review, findings=(f1, c.review.findings[0])))
        review_event = event(c, "REQUEST_SOLVER_REVISION")
        review_event = replace(review_event, payload={**review_event.payload,
                               "findings": [replace(f, stuck_rounds=0).to_dict() for f in c.review.findings]})
        decision = ZhongshuCriticState().handle(c, review_event)
        self.assertEqual(decision.transition.action, "REQUEST_SOLVER_REVISION")
        dispatch = next(effect for effect in decision.effects if effect.effect_type == "node_dispatch")
        self.assertEqual([binding["group_id"] for binding in dispatch.payload["bindings"]], ["group-000001"])

    def test_production_keeps_approved_peer_and_collects_named_decision(self):
        c = context(attempted=("f2",))
        c = replace(c, parallel=replace(c.parallel, zhongshu=replace(c.parallel.zhongshu,
                                                                    plan_review_gate=True)))
        decision = ZhongshuCriticState().handle(c, event(c, "REQUEST_SOLVER_REVISION"))
        self.assertEqual(decision.transition.reason_code, "ZHONGSHU_GROUP_STALLED")
        self.assertFalse(decision.effects)
        self.assertEqual([row["group_id"] for row in decision.update.human_gate.zhongshu_group_parks],
                         ["group-000002"])
        rows = {row.group_id: row for row in decision.update.review.zhongshu_groups}
        self.assertEqual(rows["group-000001"].stage, "CONVERGED")

    def test_scripted_incremental_mode_can_freeze_ready_peer(self):
        c = context(attempted=("f2",))
        decision = ZhongshuCriticState().handle(c, event(c, "REQUEST_SOLVER_REVISION"))
        self.assertEqual(decision.transition.action, "APPROVE_CRITIC")

    def test_global_finding_without_owner_retains_full_run_gate(self):
        c = context(pending_peer=True, attempted=("f2",), stuck=3)
        c = replace(c, review=replace(c.review, findings=(replace(c.review.findings[0],
                                                                 item_id="", group_id=""),)))
        decision = ZhongshuCriticState().handle(c, event(c, "REQUEST_SOLVER_REVISION"))
        self.assertEqual(decision.transition.reason_code, "ZHONGSHU_STUCK_FINDING")


class EvidenceBudgetTests(unittest.TestCase):
    def test_repeated_evidence_routes_to_named_group_gate_without_solver_age(self):
        c = context(attempted=(), stuck=0)
        c = replace(c, parallel=replace(c.parallel, zhongshu=replace(c.parallel.zhongshu, plan_review_gate=True)))
        state = ZhongshuCriticState()
        for _ in range(3):
            decision = state.handle(c, event(c))
            c = replace(c, review=apply_review_update(c.review, decision.update.review))
        self.assertEqual(decision.transition.reason_code, "ZHONGSHU_GROUP_STALLED")
        self.assertFalse(decision.effects)
        self.assertEqual(c.review.findings[0].stuck_rounds, 0)
        rows = {row.group_id: row for row in c.review.zhongshu_groups}
        self.assertEqual(rows["group-000002"].blocked_reason, "ZHONGSHU_EVIDENCE_STALLED")
        self.assertEqual(rows["group-000002"].revision_round, 0)
        self.assertEqual(rows["group-000001"].stage, "CONVERGED")

    def test_repeated_evidence_without_progress_parks_only_requested_group(self):
        r = context(pending_peer=True, stuck=0).review
        for index in range(3):
            rows = apply_zhongshu_evidence_round(r, ("group-000002",), max_rounds=5)
            r = replace(r, zhongshu_groups=rows)
        self.assertEqual(rows[0].stage, "REVIEWING")
        self.assertEqual(rows[1].stage, "STALLED")
        self.assertEqual(rows[1].revision_round, 0)
        self.assertEqual(rows[1].evidence_round, 2)

    def test_new_quote_is_progress_but_new_id_for_same_quote_is_not(self):
        r = context(pending_peer=True, stuck=0).review
        for index, text in enumerate(("one", "two", "two")):
            r = replace(r, evidence_packet={"evidence_updates": [{"item_id": "item-000002",
                        "evidence_id": str(index), "source": "app.py:1", "quote": {"text": text}}]})
            rows = apply_zhongshu_evidence_round(r, ("group-000002",), max_rounds=5)
            r = replace(r, zhongshu_groups=rows)
            self.assertEqual(rows[1].evidence_stalled_rounds, 1 if index == 2 else 0)

    def test_novel_quotes_cannot_bypass_total_evidence_budget(self):
        r = context(pending_peer=True, stuck=0).review
        for index in range(4):
            r = replace(r, evidence_packet={"evidence_updates": [{"item_id": "item-000002",
                        "source": "app.py:1", "quote": {"text": str(index)}}]})
            rows = apply_zhongshu_evidence_round(r, ("group-000002",), max_rounds=3)
            r = replace(r, zhongshu_groups=rows)
        self.assertEqual(rows[1].stage, "STALLED")
        self.assertEqual(rows[1].evidence_stalled_rounds, 0)
        self.assertEqual(rows[1].evidence_round, 3)

    def test_invalid_initial_evidence_is_not_progress(self):
        r = context(pending_peer=True, stuck=0).review
        for index in range(3):
            r = replace(r, evidence_packet={"evidence_updates": [{"item_id": "item-000002",
                        "source": "app.py:1", "quote": {"text": str(index)},
                        "conclusion": "[UNVERIFIED-QUOTE] guess"}]})
            rows = apply_zhongshu_evidence_round(r, ("group-000002",), max_rounds=5)
            r = replace(r, zhongshu_groups=rows)
        self.assertEqual(rows[1].stage, "STALLED")

    def test_evidence_budget_survives_dto_round_trip(self):
        c = context()
        rows = apply_zhongshu_evidence_round(c.review, ("group-000002",), max_rounds=5)
        c = replace(c, review=replace(c.review, zhongshu_groups=rows))
        self.assertEqual(context_from_dto(context_to_dto(c)).review.zhongshu_groups, rows)


class MenxiaIdentityProgressTests(unittest.TestCase):
    def test_resolved_old_blockers_reset_stall_even_when_count_is_equal(self):
        r = ReviewState(revision_id="r", menxia_groups=(MenxiaGroupState(
            "g", stage="REVIEWING", revision_round=1, last_open_count=2),))
        rows = apply_menxia_group_results(r, [{"group_id": "g", "action": "REQUEST_SOLVER_REVISION",
                    "open_count": 2, "open_suggestion_ids": ["a", "b"]}], max_rounds=5)
        r = replace(r, menxia_groups=rows)
        rows = apply_menxia_group_results(r, [{"group_id": "g", "action": "REQUEST_SOLVER_REVISION",
                    "open_count": 2, "open_suggestion_ids": ["c", "d"]}], max_rounds=5)
        self.assertEqual(rows[0].stage, "SOLVING")
        self.assertEqual(rows[0].stalled_rounds, 0)

    def test_new_fingerprint_does_not_hide_unchanged_blockers(self):
        r = ReviewState(revision_id="r", menxia_groups=(MenxiaGroupState("g"),))
        for index in range(3):
            rows = apply_menxia_group_results(r, [{"group_id": "g", "action": "REQUEST_SOLVER_REVISION",
                        "open_count": 2, "open_suggestion_ids": ["a", "b"],
                        "fingerprint": str(index), "doc_version": index + 1}], max_rounds=5)
            r = replace(r, menxia_groups=rows)
        self.assertEqual(rows[0].stage, "ESCALATED")

    def test_stall_history_survives_normal_solver_and_analyst_hops(self):
        r = ReviewState(revision_id="r", menxia_groups=(MenxiaGroupState("g"),))
        for index in range(3):
            for action in ("REQUEST_SOLVER_REVISION", "READY_FOR_ANALYST", "EVIDENCE_SUFFICIENT"):
                rows = apply_menxia_group_results(r, [{"group_id": "g", "action": action,
                    "open_count": 2, "open_suggestion_ids": ["a", "b"]}], max_rounds=5)
                r = replace(r, menxia_groups=rows)
        self.assertEqual(rows[0].stage, "ESCALATED")

    def test_progress_does_not_remove_total_revision_budget(self):
        r = ReviewState(revision_id="r", menxia_groups=(MenxiaGroupState("g"),))
        for index in range(3):
            rows = apply_menxia_group_results(r, [{"group_id": "g", "action": "REQUEST_SOLVER_REVISION",
                "open_count": 1, "open_suggestion_ids": [str(index)]}], max_rounds=3)
            r = replace(r, menxia_groups=rows)
        self.assertEqual(rows[0].stage, "ESCALATED")
        self.assertEqual(rows[0].stalled_rounds, 0)

    def test_blocker_baseline_survives_dto_round_trip(self):
        c = context()
        r = replace(c.review, menxia_groups=(replace(MenxiaGroupState("g"),
                                                     last_open_suggestion_ids=("a", "b")),))
        c = replace(c, review=r)
        self.assertEqual(context_from_dto(context_to_dto(c)).review.menxia_groups, r.menxia_groups)


def requirement_payload(request, acceptance):
    return {"action": "REQUIREMENT_CONTRACT_READY", "requirements": [{
        "requirement_id": "r1", "statement": "评估优化空间", "source": request,
        "source_quote": request, "priority": "must", "scope": "in", "kind": "task",
        "acceptance_signal": acceptance,
    }]}


class RequirementBoundaryTests(unittest.TestCase):
    def test_cannot_invent_a_minimum_number_of_optimization_directions(self):
        request = "审查当前优化，是否还有优化空间？"
        error = validate_zhongshu_requirement_contract(
            requirement_payload(request, "列出至少3个优化方向"), original_request=request)
        self.assertIn("QUANTITY_UNSUPPORTED", error)

    def test_explicit_requested_quantity_is_allowed(self):
        request = "请列出至少3个优化方向"
        self.assertEqual(validate_zhongshu_requirement_contract(
            requirement_payload(request, "列出至少 3 个优化方向"), original_request=request), "")

    def test_chinese_numbers_and_english_counts(self):
        for request, acceptance in (("至少三个优化方向", "至少 3 个方向"),
                                    ("at least 3 options", "at least 3 options")):
            self.assertEqual(validate_zhongshu_requirement_contract(
                requirement_payload(request, acceptance), original_request=request), "")

    def test_quantity_in_other_requirement_cannot_authorize_this_one(self):
        request = "审查优化空间；另一个报告列出至少3个案例"
        payload = requirement_payload("审查优化空间", "至少3个优化方向")
        self.assertIn("QUANTITY_UNSUPPORTED", validate_zhongshu_requirement_contract(
            payload, original_request=request))

    def test_do_not_swap_minimum_and_maximum(self):
        request = "最多3个方向"
        self.assertIn("QUANTITY_UNSUPPORTED", validate_zhongshu_requirement_contract(
            requirement_payload(request, "至少3个方向"), original_request=request))


if __name__ == "__main__":
    unittest.main()
