"""Canonical structured-plan regressions; no models or external notifications."""
from __future__ import annotations

import copy
from dataclasses import replace
import unittest
import tempfile

from orchestrator.domain.planning.compiler import commit_plan, PlanCompileError
from orchestrator.domain.planning.model import PlanRevision, CANONICAL_PLAN_SCHEMA
from orchestrator.domain.context import (
    ReviewState, ReviewUpdate, apply_review_update, context_to_dto, context_from_dto,
)
from orchestrator.domain.zhongshu.solver import process_solver_reply
from test_plan_structure_repair import repaired_reply, review_for, context_for as legacy_context_for


def context_for(review, state="ZHONGSHU_SOLVER"):
    context = legacy_context_for(review, state)
    return replace(context, request=replace(context.request, plan_schema=review.plan_schema))


def draft():
    return repaired_reply()["plan"]


def canonical_review():
    return apply_review_update(ReviewState(revision_id="R0", plan_schema=CANONICAL_PLAN_SCHEMA),
                              ReviewUpdate(revision_id="R1", plan=draft(), plan_base_ref=""))


class PlanRevisionTests(unittest.TestCase):
    def test_revision_is_deeply_isolated_and_dto_round_trips(self):
        p = draft()
        revision = commit_plan(None, p, expected_ref="")
        p["items"][0]["objective"] = "outside mutation"
        exposed = revision.plan
        exposed["items"][0]["objective"] = "copy mutation"
        self.assertEqual(revision.plan["items"][0]["objective"], "Review 1")
        self.assertEqual(PlanRevision.from_dict(revision.to_dict()), revision)

    def test_unchanged_revision_is_idempotent_and_changed_revision_has_parent(self):
        first = commit_plan(None, draft(), expected_ref="")
        self.assertEqual(commit_plan(first, first.plan, expected_ref=first.reference), first)
        changed = first.plan
        changed["items"][0]["objective"] = "New review"
        second = commit_plan(first, changed, expected_ref=first.reference)
        self.assertEqual(second.number, 2)
        self.assertEqual(second.parent_ref, first.reference)
        self.assertNotEqual(first.content_hash, second.content_hash)

    def test_stale_or_missing_base_is_rejected(self):
        first = commit_plan(None, draft(), expected_ref="")
        for expected in (None, "old-revision"):
            with self.subTest(base=expected), self.assertRaises(PlanCompileError):
                commit_plan(first, first.plan, expected_ref=expected)

    def test_duplicate_items_and_group_ids_are_rejected(self):
        for key in ("items", "groups"):
            p = draft()
            p[key].append(copy.deepcopy(p[key][0]))
            with self.subTest(key=key), self.assertRaises(PlanCompileError):
                commit_plan(None, p, expected_ref="")

    def test_requirement_baseline_and_frozen_work_are_preserved(self):
        first = commit_plan(None, draft(), expected_ref="")
        changed = first.plan
        changed["requirements"][0]["text"] = "Invented scope"
        with self.assertRaises(PlanCompileError):
            commit_plan(first, changed, expected_ref=first.reference, requirements=first.plan["requirements"])
        changed = first.plan
        changed["items"][0]["objective"] = "Changed frozen task"
        with self.assertRaises(PlanCompileError):
            commit_plan(first, changed, expected_ref=first.reference, frozen_group_ids=["GRP-001"])

    def test_snapshot_round_trip_preserves_revision_and_old_dtos_stay_legacy(self):
        c = context_for(canonical_review())
        saved = context_to_dto(c)
        restored = context_from_dto(saved)
        self.assertEqual(restored.review.plan_revision, c.review.plan_revision)
        old = context_to_dto(context_for(review_for(draft())))
        old["review"].pop("plan_schema", None)
        old["review"].pop("plan_revision", None)
        self.assertEqual(context_from_dto(old).review.plan_schema, "legacy-v1")

    def test_mirror_tampering_or_unknown_policy_is_rejected_on_load(self):
        c = context_for(canonical_review())
        saved = context_to_dto(c)
        saved["review"]["plan"]["items"][0]["objective"] = "Tampered mirror"
        with self.assertRaises(ValueError):
            context_from_dto(saved)
        saved = context_to_dto(c)
        saved["review"]["plan_revision"]["policy_version"] = "unknown"
        with self.assertRaises(ValueError):
            context_from_dto(saved)

    def test_projection_only_business_write_is_rejected(self):
        review = canonical_review()
        items = (replace(review.task_items[0], objective="Bypass"),) + review.task_items[1:]
        with self.assertRaises(PlanCompileError):
            apply_review_update(review, ReviewUpdate(revision_id="R2", task_items=items))

    def test_first_formalization_uses_structured_signals_without_markdown(self):
        review = ReviewState(revision_id="R0", plan_schema=CANONICAL_PLAN_SCHEMA)
        result = process_solver_reply({"action": "READY_FOR_CRITIC", "mode": "TASK_GRAPH_FORMALIZATION_READ_ONLY",
                                      "plan": draft(), "group_docs": []}, review)
        self.assertEqual(result.error, "")
        self.assertEqual(len(result.group_doc_rows), 3)
        self.assertEqual(result.materialized["items"][0]["acceptance_signals"], ["Result 1 is observable"])

    def test_model_markdown_cannot_rewrite_structured_signals(self):
        review = ReviewState(revision_id="R0", plan_schema=CANONICAL_PLAN_SCHEMA)
        reply = repaired_reply()
        reply["group_docs"][0]["markdown"] = reply["group_docs"][0]["markdown"].replace("Result 1 is observable", "Invented gate")
        result = process_solver_reply(reply, review)
        self.assertEqual(result.error, "")
        self.assertEqual(result.materialized["items"][0]["acceptance_signals"], ["Result 1 is observable"])
        self.assertNotIn("Invented gate", result.group_doc_rows[0].doc_markdown)

    def test_changed_group_invalidates_only_its_document_and_approval(self):
        review = canonical_review()
        rows = tuple(replace(r, stage="FROZEN", approved_surface_hash="approved") for r in review.zhongshu_groups)
        # Open the changed group explicitly; frozen peers must survive.
        rows = (replace(rows[0], stage="REVIEWING"),) + rows[1:]
        review = replace(review, zhongshu_groups=rows)
        changed = review.plan_revision.plan
        changed["items"][0]["objective"] = "Revised task"
        next_review = apply_review_update(review, ReviewUpdate(revision_id="R2", plan=changed,
                                            plan_base_ref=review.plan_revision.reference))
        self.assertEqual(next_review.zhongshu_groups[0].doc_version, 2)
        self.assertEqual(next_review.zhongshu_groups[0].approved_surface_hash, "")
        self.assertEqual(next_review.zhongshu_groups[1:], review.zhongshu_groups[1:])

    def test_evidence_packet_does_not_replace_committed_plan(self):
        review = canonical_review()
        packet = {"candidate_items": [], "candidate_groups": [], "facts": ["new evidence"]}
        updated = apply_review_update(review, ReviewUpdate(revision_id="R2", plan=packet, evidence_packet=packet))
        self.assertEqual(updated.plan_revision, review.plan_revision)
        self.assertEqual(updated.plan, review.plan)
        self.assertEqual(updated.evidence_packet, packet)

    def test_generated_documents_and_schema_are_verified_on_restore(self):
        for key, bad in (("doc_markdown", "forged"), ("doc_source_hash", "forged"), ("doc_version", 0)):
            saved = context_to_dto(context_for(canonical_review()))
            saved["review"]["zhongshu_groups"][0][key] = bad
            with self.subTest(field=key), self.assertRaises(ValueError):
                context_from_dto(saved)
        saved = context_to_dto(context_for(canonical_review()))
        saved["request"]["plan_schema"] = "legacy-v1"
        with self.assertRaises(ValueError):
            context_from_dto(saved)

    def test_stale_solver_result_uses_bounded_retry_without_committing(self):
        from orchestrator.domain.states import ZhongshuSolverState
        from test_plan_structure_repair import event_for
        review = canonical_review()
        c = context_for(review)
        payload = dict(action="READY_FOR_CRITIC", plan=draft(), group_docs=[], plan_base_ref="old")
        decision = ZhongshuSolverState().handle(c, event_for("READY_FOR_CRITIC", payload))
        self.assertEqual(decision.transition.action, "RETRY")
        self.assertIsNone(decision.update.review)
        self.assertEqual(review.plan_revision.number, 1)
        c = replace(c, recovery=replace(c.recovery, reply_retry_count=c.recovery.max_reply_retries))
        decision = ZhongshuSolverState().handle(c, event_for("READY_FOR_CRITIC", payload))
        self.assertEqual(decision.transition.action, "BLOCKED")

    def test_approval_snapshot_binds_the_revision_even_when_content_repeats(self):
        from orchestrator.domain.plan_review import new_snapshot, matches_current
        review = canonical_review()
        packet = new_snapshot(context_for(review))
        review = replace(review, plan_review=packet)
        self.assertTrue(matches_current(review))
        changed = review.plan_revision.plan
        changed["items"][0]["objective"] = "Intermediate edit"
        second = apply_review_update(review, ReviewUpdate(revision_id="R2", plan=changed,
                                    plan_base_ref=review.plan_revision.reference))
        third = apply_review_update(second, ReviewUpdate(revision_id="R3", plan=review.plan,
                                    plan_base_ref=second.plan_revision.reference))
        # Reset document versions/hashes in a fabricated old approval snapshot:
        # even identical plan content cannot reuse an earlier revision approval.
        old_packet = {**packet, "group_hashes": {r.group_id: r.doc_hash for r in third.zhongshu_groups}}
        self.assertFalse(matches_current(replace(third, plan_review=old_packet)))

    def test_ordinary_revision_cannot_regroup_or_rewrite_global_fields(self):
        review = canonical_review()
        review = replace(review, findings=review_for(draft()).findings)
        for mutation in ("groups", "metadata"):
            changed = review.plan_revision.plan
            if mutation == "groups":
                changed["groups"][0]["item_ids"].remove("ITEM-001")
                changed["groups"][1]["item_ids"].append("ITEM-001")
                changed["items"][0]["group_id"] = "GRP-002"
            else:
                changed["plan_id"] = "forged"
            reply = dict(action="READY_FOR_CRITIC", plan=changed, group_docs=[],
                finding_batch=dict(selected_finding_ids=["f-1"], remaining_finding_ids=[]),
                finding_resolutions=[dict(finding_id="f-1", response="absorbed", note="fixed")])
            result = process_solver_reply(reply, review)
            self.assertTrue(result.error, mutation)

    def test_empty_finding_scope_cannot_edit_business_content(self):
        review = canonical_review()
        changed = review.plan_revision.plan
        changed["items"][0]["objective"] = "Unasked revision"
        result = process_solver_reply(dict(action="READY_FOR_CRITIC", plan=changed, group_docs=[]), review)
        self.assertEqual(result.error, "")
        self.assertEqual(result.materialized, review.plan)

    def test_compiler_rejects_acceptance_heading_injection_and_missing_signals(self):
        for signal in ([], ["# Injected heading"], ["line one\nline two"], ["a" * 301]):
            p = draft()
            p["items"][0]["acceptance_signals"] = signal
            with self.subTest(signal=signal), self.assertRaises(PlanCompileError):
                commit_plan(None, p, expected_ref="")

    def test_analyst_and_solver_cannot_forge_peer_plan_or_approval_writes(self):
        from orchestrator.domain.states import ZhongshuSolverState
        review = canonical_review()
        c = context_for(review)
        payload = dict(action="READY_FOR_CRITIC", plan=review.plan, plan_base_ref=review.plan_revision.reference,
            group_reviews=[dict(group_id="GRP-001", action="APPROVE_GROUP")],
            salvaged_group_rows=[dict(group_id="GRP-001", stage="FROZEN")])
        update = ZhongshuSolverState._review_update(c, payload)
        committed = apply_review_update(review, update)
        self.assertEqual(committed.task_review_ledger, ())
        self.assertTrue(all(r.stage == "REVIEWING" for r in committed.zhongshu_groups))
        analyst = context_for(ReviewState(revision_id="R0", plan_schema=CANONICAL_PLAN_SCHEMA), "ZHONGSHU_ANALYST")
        update = ZhongshuSolverState._review_update(analyst, dict(action="READY_FOR_SOLVER", plan=draft()))
        self.assertIsNone(apply_review_update(analyst.review, update).plan_revision)

    def test_frozen_sources_include_global_metadata_and_default_requirement_baseline(self):
        first = commit_plan(None, draft(), expected_ref="")
        changed = first.plan
        changed["plan_id"] = "new global scope"
        with self.assertRaises(PlanCompileError):
            commit_plan(first, changed, expected_ref=first.reference, frozen_group_ids=["GRP-001"])
        changed = first.plan
        changed["requirements"][0]["text"] = "new baseline"
        with self.assertRaises(PlanCompileError):
            commit_plan(first, changed, expected_ref=first.reference)

    def test_json_identity_distinguishes_booleans_from_numbers(self):
        p = draft()
        p["limit"] = 1
        first = commit_plan(None, p, expected_ref="")
        changed = first.plan
        changed["limit"] = True
        second = commit_plan(first, changed, expected_ref=first.reference)
        self.assertEqual(second.number, 2)
        self.assertIs(second.plan["limit"], True)
        review = apply_review_update(ReviewState(revision_id="R0", plan_schema=CANONICAL_PLAN_SCHEMA),
                                    ReviewUpdate(revision_id="R1", plan=p, plan_base_ref=""))
        saved = context_to_dto(context_for(review))
        saved["review"]["plan"]["limit"] = True
        with self.assertRaises(ValueError):
            context_from_dto(saved)
        p = draft()
        p["requirements"][0]["limit"] = 1
        first = commit_plan(None, p, expected_ref="")
        changed = first.plan
        changed["requirements"][0]["limit"] = True
        with self.assertRaises(PlanCompileError):
            commit_plan(first, changed, expected_ref=first.reference)

    def test_completed_and_salvaged_transport_results_use_dispatched_base(self):
        from orchestrator.runtime.agent_effects import AgentWorkerRunner
        from test_linear_fsm_parallel_effects import _Transport, _request

        class Transport(_Transport):
            def poll(self, request):
                return tuple(replace(reply, payload=dict(action="READY_FOR_CRITIC", plan_base_ref="forged"))
                             for reply in super().poll(request))

        base = canonical_review().plan_revision.reference
        original = _request()
        request = replace(original, payload={**original.payload, "target_state": "ZHONGSHU_SOLVER",
            "dispatch_context": dict(plan_schema=CANONICAL_PLAN_SCHEMA, plan_base_ref=base)})
        for status in ("COMPLETED", "FAILED"):
            outcome = AgentWorkerRunner(Transport(status=status), timeout_seconds=1).run_once(request)
            self.assertEqual(outcome.status, "SUCCEEDED")
            self.assertEqual(outcome.event_payload["plan_base_ref"], base)


class CanonicalApplicationTests(unittest.TestCase):
    def pump_until_gate(self, app):
        app._ensure_start_event()
        for _ in range(100):
            state = app.repository.load(app.context.identity.task_id).context.progression.state
            if state == "HUMAN_GATE":
                return app.repository.load(app.context.identity.task_id).context
            self.assertNotIn(state, ("BLOCKED", "FAILED", "DONE"))
            self.step(app)
        self.fail("canonical application exceeded bounded gate pump")

    def step(self, app):
        task_id = app.context.identity.task_id
        event = app.inbox.next(task_id)
        if event:
            app.engine.dispatch(event)
            app.inbox.ack(event)
            app.effects.execute_pending(task_id)
        elif app.repository.pending_effects(task_id):
            app.effects.execute_pending(task_id)
        elif app.repository.load(task_id).context.progression.state == "RETRY_WAIT":
            app.resume()
        else:
            self.fail("no event or effect can advance the canonical application")

    def test_real_application_completes_with_compiled_documents_and_trusted_base(self):
        from orchestrator.app import OrchestratorApp
        from orchestrator.adapters import FakeMulticaAdapter
        from test_linear_fsm_entrypoint import _ScriptedMultica, _context, _entry_plan
        from test_plan_structure_repair import StructureRepairApplicationTests

        class Adapter(_ScriptedMultica):
            def dispatch(self, request):
                if request.target_state != "ZHONGSHU_SOLVER":
                    return super().dispatch(request)
                receipt = FakeMulticaAdapter.dispatch(self, request)
                self._reply(request, dict(action="READY_FOR_CRITIC", mode="TASK_GRAPH_FORMALIZATION_READ_ONLY",
                    plan=_entry_plan(), group_docs=[], plan_base_ref="model-spoof"))
                return receipt

        context = _context()
        context = replace(context, request=replace(context.request, plan_schema=CANONICAL_PLAN_SCHEMA))
        with tempfile.TemporaryDirectory() as directory:
            adapter = Adapter()
            app = OrchestratorApp(context, root=directory, multica=adapter, poll_interval=0, timeout_seconds=2)
            final = StructureRepairApplicationTests().drive(app)
            self.assertEqual(final.review.plan_revision.number, 1)
            self.assertEqual(final.review.plan_revision.plan, _entry_plan())
            solver = [r for r in adapter.dispatched if r.target_state == "ZHONGSHU_SOLVER"]
            self.assertEqual(len(solver), 1)
            self.assertEqual(solver[0].context["plan_base_ref"], "")
            self.assertEqual(solver[0].context["document_policy"], "derived_from_plan")
            self.assertEqual(context_from_dto(context_to_dto(final)).review.plan_revision, final.review.plan_revision)
            import json
            artifacts = [json.loads(p.read_text(encoding="utf-8")) for p in (app.root / "artifacts").rglob("*.json")]
            revisions = [v for v in artifacts if isinstance(v, dict) and v.get("schema_version") == CANONICAL_PLAN_SCHEMA]
            self.assertTrue(any(PlanRevision.from_dict(v) == final.review.plan_revision for v in revisions))

    def test_critic_revision_commits_structured_signals_with_parallel_flags_enabled(self):
        from orchestrator.app import OrchestratorApp
        from test_zhongshu_convergence_e2e import _ConvergingMultica, _context, _plan
        from test_plan_structure_repair import StructureRepairApplicationTests

        class Adapter(_ConvergingMultica):
            def _reply_solver(self, request):
                p = _plan()
                reply = dict(action="READY_FOR_CRITIC", plan=p, group_docs=[])
                if request.context.get("solver_stage") == "revise":
                    p["items"][1]["acceptance_signals"] = ["Revised B latency is observable via elapsed_ms"]
                    reply["finding_batch"] = dict(selected_finding_ids=["finding-item2"], remaining_finding_ids=[])
                    reply["finding_resolutions"] = [dict(finding_id="finding-item2", response="absorbed", note="fixed",
                        changed_fields=["acceptance_signals"], evidence=[], owner_role="review-solver", next_action="none")]
                self._reply(request, reply)

        context = _context()
        context = replace(context, request=replace(context.request, plan_schema=CANONICAL_PLAN_SCHEMA),
            parallel=replace(context.parallel, zhongshu=replace(context.parallel.zhongshu, item_workflow_enabled=True)))
        with tempfile.TemporaryDirectory() as directory:
            adapter = Adapter()
            app = OrchestratorApp(context, root=directory, multica=adapter, poll_interval=0, timeout_seconds=2)
            final = StructureRepairApplicationTests().drive(app)
            self.assertEqual(final.review.plan_revision.number, 2)
            self.assertEqual(final.review.task_items[1].acceptance_signals, ("Revised B latency is observable via elapsed_ms",))
            solver = [r for r in adapter.dispatched if r.target_state == "ZHONGSHU_SOLVER"]
            self.assertEqual(len(solver), 2)
            self.assertTrue(all(r.context.get("plan_schema") == CANONICAL_PLAN_SCHEMA for r in solver))
            self.assertTrue(all(r.context.get("zhongshu_dispatch_mode") not in ("group_revise", "item_revise") for r in solver))

    def test_human_revision_restarts_at_exact_revision_and_requires_fresh_approval(self):
        from orchestrator.app import OrchestratorApp
        from orchestrator.adapters import FakeMulticaAdapter
        from orchestrator.domain.plan_review import matches_current
        from test_linear_fsm_entrypoint import _ScriptedMultica, _context, _entry_plan
        from test_plan_structure_repair import StructureRepairApplicationTests

        class Adapter(_ScriptedMultica):
            calls = 0
            def dispatch(self, request):
                if request.target_state != "ZHONGSHU_SOLVER":
                    return super().dispatch(request)
                receipt = FakeMulticaAdapter.dispatch(self, request)
                self.calls += 1
                p = _entry_plan()
                if self.calls > 1:
                    p["items"][0]["objective"] = "Refined Task A objective"
                self._reply(request, dict(action="READY_FOR_CRITIC", mode="TASK_GRAPH_FORMALIZATION_READ_ONLY",
                                         plan=p, group_docs=[]))
                return receipt

        context = _context()
        context = replace(context, request=replace(context.request, plan_schema=CANONICAL_PLAN_SCHEMA),
                          parallel=replace(context.parallel, zhongshu=replace(context.parallel.zhongshu, plan_review_gate=True)))
        with tempfile.TemporaryDirectory() as directory:
            adapter = Adapter()
            app = OrchestratorApp(context, root=directory, multica=adapter, poll_interval=0, timeout_seconds=2)
            first = self.pump_until_gate(app)
            self.assertEqual(first.human_gate.reason_code, "PLAN_REVIEW")
            self.assertTrue(matches_current(first.review))
            self.assertFalse(app.resume())
            self.assertTrue(app.resume("拒绝：细化 Task A 目标"))
            self.step(app)
            second = self.pump_until_gate(app)
            self.assertEqual(second.review.plan_revision.number, 2)
            self.assertEqual(second.review.zhongshu_groups[0].doc_version, 2)
            self.assertEqual(second.review.zhongshu_groups[0].stage, "FROZEN")
            self.assertEqual(second.review.plan_review["approved_hash"], "")
            self.assertTrue(matches_current(second.review))
            self.assertNotEqual(first.human_gate.document_hash, second.human_gate.document_hash)
            restored = OrchestratorApp(second, root=directory, multica=adapter, poll_interval=0, timeout_seconds=2)
            self.assertTrue(restored.resume("通过"))
            self.step(restored)
            for _ in range(100):
                final = restored.repository.load(context.identity.task_id).context
                if final.progression.state == "DONE":
                    break
                self.assertNotIn(final.progression.state, ("BLOCKED", "FAILED", "HUMAN_GATE"),
                                 repr(final.review.menxia_groups))
                self.step(restored)
            else:
                self.fail("restarted application exceeded bounded pump")
            self.assertEqual(final.review.plan_revision, second.review.plan_revision)
            self.assertEqual(adapter.calls, 2)


if __name__ == "__main__":
    unittest.main()
