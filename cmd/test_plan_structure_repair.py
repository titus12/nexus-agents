"""Offline regression of RTC 7c9d13: eight-member base, local findings.

Only identifiers, membership, and dependencies are copied from the incident;
all prose is synthetic. No run directory, model, service, or notification.
"""
from __future__ import annotations

import copy
import tempfile
from dataclasses import replace
from types import SimpleNamespace
import unittest

from orchestrator.domain.context import (
    ReviewState, ReviewTaskGroup, ReviewTaskItem, TaskIdentity,
    WorkflowContext, ProgressState, ZhongshuGroupState, apply_review_update, ParallelState, ZhongshuParallelLimits,
)
from orchestrator.domain.events import DomainEvent
from orchestrator.domain.findings import Finding
from orchestrator.domain.policies.solver_plan import (
    structural_integrity_errors, is_retryable_solver_reply_error,
)
from orchestrator.domain.policies.zhongshu_group import (
    review_preflight_violations, apply_group_verdict, current_member_hashes,
)
from orchestrator.domain.states import (
    ZhongshuSolverState, ZhongshuCriticState, ZhongshuFreezeCheckState,
    mechanical_freeze_ready,
)
from orchestrator.domain.zhongshu.solver import build_solver_dispatch, process_solver_reply


def incident_plan():
    dependencies = [[], [1], [1], [1], [1], [1], [1], [2, 3, 4, 5, 6, 7], [1], [8, 9]]
    return {
        "requirements": [{"requirement_id": "REQ-001", "text": "Produce a review plan"}],
        "items": [dict(item_id=f"ITEM-{n:03}", title=f"Task {n}", objective=f"Review {n}",
                       dependencies=[f"ITEM-{d:03}" for d in deps],
                       source_requirement_ids=["REQ-001"], acceptance_signals=[f"Result {n} is observable"])
                  for n, deps in enumerate(dependencies, 1)],
        "groups": [dict(group_id="GRP-001", item_ids=[f"ITEM-{n:03}" for n in (1, 2, 3, 4, 5, 6, 7, 9)]),
                   dict(group_id="GRP-002", item_ids=["ITEM-008", "ITEM-010"])],
    }


def doc(group, version=1):
    names = ("背景", "目标", "标识与范围", "状态与边界语义", "行为要求", "责任边界", "交叉不变量", "验收标准", "非目标")
    lines = [f"# {group['group_id']} 需求文档 [v{version}]"]
    for n, name in enumerate(names, 1):
        lines.append(f"## {n}. {name}")
        lines.append("\n".join(f"### {iid}\nResult {int(iid[-3:])} is observable" for iid in group["item_ids"])
                     if n == 8 else "按已确认需求执行。")
    return "\n".join(lines)


def review_for(plan):
    owners = {iid: g["group_id"] for g in plan["groups"] for iid in g["item_ids"]}
    return ReviewState(revision_id="R1", plan=copy.deepcopy(plan),
        requirements=tuple(plan["requirements"]),
        task_items=tuple(ReviewTaskItem(item_id=i["item_id"], group_id=owners[i["item_id"]], order=n,
            title=i["title"], objective=i["objective"], dependencies=tuple(i["dependencies"]),
            acceptance_signals=tuple(i["acceptance_signals"])) for n, i in enumerate(plan["items"], 1)),
        task_groups=tuple(ReviewTaskGroup(group_id=g["group_id"], item_ids=tuple(g["item_ids"]), order=n)
                          for n, g in enumerate(plan["groups"], 1)),
        zhongshu_groups=tuple(ZhongshuGroupState(group_id=g["group_id"], doc_markdown=doc(g), doc_version=1)
                              for g in plan["groups"]),
        findings=(Finding(finding_id="f-1", severity="P1", status="OPEN", group_id="GRP-002", item_id="ITEM-010"),))


def context_for(review, state="ZHONGSHU_SOLVER"):
    return WorkflowContext(identity=TaskIdentity("task-structure", "issue", "", "request"),
                           progression=ProgressState(state, 3, "2026-10-06T00:00:00Z"), review=review)


def event_for(name, payload):
    return DomainEvent(name, "task-structure", 3, payload, "2026-10-06T00:00:00Z")


def repaired_reply():
    plan = incident_plan()
    plan["groups"][0]["item_ids"] = [f"ITEM-{n:03}" for n in range(1, 7)]
    plan["groups"].append(dict(group_id="GRP-003", item_ids=["ITEM-007", "ITEM-009"]))
    return dict(action="READY_FOR_CRITIC", mode="TASK_GRAPH_FORMALIZATION_READ_ONLY", plan=plan,
                group_docs=[dict(group_id=g["group_id"], markdown=doc(g, 2 if g["group_id"] == "GRP-001" else 1))
                            for g in plan["groups"] if g["group_id"] != "GRP-002"])


class PlanStructureRepairTests(unittest.TestCase):
    def test_entry_and_preflight_agree_on_incident_limit(self):
        p = incident_plan()
        self.assertEqual(structural_integrity_errors(p), ["GROUP_MAX_ITEMS:GRP-001:8"])
        self.assertIn("plan:GROUP_MAX_ITEMS:GRP-001:8", review_preflight_violations(review_for(p)))
        self.assertTrue(is_retryable_solver_reply_error("SOLVER_PLAN_STRUCTURE_INVALID:GROUP_MAX_ITEMS:GRP-001:8"))

    def test_initial_formalization_rejects_without_committing(self):
        p = incident_plan()
        reply = dict(action="READY_FOR_CRITIC", mode="TASK_GRAPH_FORMALIZATION_READ_ONLY", plan=p,
                     group_docs=[dict(group_id=g["group_id"], markdown=doc(g)) for g in p["groups"]])
        c = context_for(ReviewState(revision_id="R0"))
        decision = ZhongshuSolverState().handle(c, event_for("READY_FOR_CRITIC", reply))
        self.assertEqual(decision.transition.action, "RETRY")
        self.assertIsNone(decision.update.review)
        self.assertEqual(decision.update.recovery.reply_retry_count, 1)

    def test_local_findings_cannot_hide_global_repair_authority(self):
        c = context_for(review_for(incident_plan()), "ZHONGSHU_CRITIC")
        d = build_solver_dispatch(c, request_id="test", prompt=SimpleNamespace(content="prompt", references={}))
        dc = d.payload["dispatch_context"]
        self.assertEqual(dc["zhongshu_dispatch_mode"], "plan_structure_repair")
        self.assertEqual(dc["expected_reply_mode"], "TASK_GRAPH_FORMALIZATION_READ_ONLY")
        self.assertEqual(dc["structure_issues"], ["GROUP_MAX_ITEMS:GRP-001:8"])
        self.assertEqual(len(dc["current_formal_plan"]["items"]), 10)

    def test_repair_accepts_split_and_preserves_untouched_peer(self):
        review = review_for(incident_plan())
        result = process_solver_reply(repaired_reply(), review)
        self.assertEqual(result.error, "")
        self.assertEqual(len(result.materialized["items"]), 10)
        self.assertEqual(structural_integrity_errors(result.materialized), [])
        peer = next(r for r in result.group_doc_rows if r.group_id == "GRP-002")
        self.assertEqual(peer, review.zhongshu_groups[1])

    def test_repair_state_preserves_findings_and_peer_approval(self):
        review = review_for(incident_plan())
        # A peer approval remains valid because its members and doc do not change.
        review = replace(review, findings=())
        ledger, rows = apply_group_verdict(review, "GRP-002", "APPROVE_GROUP",
                                          member_hashes=current_member_hashes(review, "GRP-002"))
        review = replace(review, findings=review_for(incident_plan()).findings,
                         task_review_ledger=ledger, zhongshu_groups=rows)
        c = context_for(review)
        reply = repaired_reply()
        reply["finding_resolutions"] = [{"finding_id": "f-1", "response": "absorbed"}]
        decision = ZhongshuSolverState().handle(c, event_for("READY_FOR_CRITIC", reply))
        self.assertEqual(decision.transition.action, "READY_FOR_CRITIC")
        after = apply_review_update(review, decision.update.review)
        self.assertEqual(next(r for r in after.zhongshu_groups if r.group_id == "GRP-002"), rows[1])
        self.assertTrue(after.findings[0].active)
        self.assertEqual(len(after.task_items), 10)

    def test_structure_repair_cannot_change_signals_through_documents(self):
        reply = repaired_reply()
        reply["group_docs"][0]["markdown"] = reply["group_docs"][0]["markdown"].replace("Result 1 is observable", "Result 1 is observable\nSkip task 1")
        self.assertTrue(process_solver_reply(reply, review_for(incident_plan())).error.startswith("SOLVER_STRUCTURE_REPAIR_FROZEN:"))

    def test_local_inflight_failure_routes_without_node_failure_brake(self):
        c = context_for(review_for(incident_plan()))
        c = replace(c, recovery=replace(c.recovery, node_fail_streak=1, node_fail_state="ZHONGSHU_SOLVER"))
        d = ZhongshuSolverState().handle(c, event_for("FAIL", {"action": "FAIL", "error_code": "NODE_ITEM_REVISION_INVALID"}))
        self.assertEqual(d.transition.action, "REQUEST_STRUCTURE_REPAIR")
        self.assertEqual(d.effects[0].payload["dispatch_context"]["zhongshu_dispatch_mode"], "plan_structure_repair")

    def test_repair_cannot_rewrite_content_or_remove_work(self):
        for field, value in (("objective", "Changed scope"), ("dependencies", [])):
            reply = repaired_reply()
            reply["plan"]["items"][7][field] = value
            self.assertTrue(process_solver_reply(reply, review_for(incident_plan())).error.startswith("SOLVER_STRUCTURE_REPAIR_FROZEN:"))
        reply = repaired_reply()
        reply["plan"]["items"].pop()
        self.assertTrue(process_solver_reply(reply, review_for(incident_plan())).error.startswith("SOLVER_STRUCTURE_REPAIR_FROZEN:"))

    def test_frozen_group_is_protected(self):
        review = review_for(incident_plan())
        review = replace(review, zhongshu_groups=(replace(review.zhongshu_groups[0], stage="FROZEN"), review.zhongshu_groups[1]))
        self.assertTrue(process_solver_reply(repaired_reply(), review).error.startswith("SOLVER_STRUCTURE_REPAIR_FROZEN:"))

    def test_unchanged_group_ids_still_invalidate_changed_approval(self):
        review = replace(review_for(incident_plan()), findings=())
        for g in review.task_groups:
            ledger, rows = apply_group_verdict(review, g.group_id, "APPROVE_GROUP",
                                               member_hashes=current_member_hashes(review, g.group_id))
            review = replace(review, task_review_ledger=ledger, zhongshu_groups=rows)
        reply = repaired_reply()
        reply["plan"]["groups"].pop()
        reply["plan"]["groups"][1]["item_ids"].extend(["ITEM-007", "ITEM-009"])
        reply["group_docs"] = [dict(group_id=g["group_id"], markdown=doc(g, 2)) for g in reply["plan"]["groups"]]
        decision = ZhongshuSolverState().handle(context_for(review), event_for("READY_FOR_CRITIC", reply))
        self.assertEqual(decision.transition.action, "READY_FOR_CRITIC")
        after = apply_review_update(review, decision.update.review)
        self.assertEqual(len(after.zhongshu_groups), 2)
        self.assertTrue(all(r.stage == "REVIEWING" and not r.approved_surface_hash for r in after.zhongshu_groups))

    def test_invalid_repair_uses_bounded_reply_budget(self):
        c = context_for(review_for(incident_plan()))
        c = replace(c, recovery=replace(c.recovery, reply_retry_count=c.recovery.max_reply_retries))
        reply = repaired_reply()
        reply["plan"]["items"][0]["objective"] = "Changed scope"
        d = ZhongshuSolverState().handle(c, event_for("READY_FOR_CRITIC", reply))
        self.assertEqual(d.transition.action, "BLOCKED")
        self.assertIsNone(d.update.review)

    def test_infrastructure_failure_keeps_normal_recovery(self):
        c = context_for(review_for(incident_plan()))
        d = ZhongshuSolverState().handle(c, event_for("FAIL", {"action": "FAIL", "error_code": "REMOTE_RUN_FAILED", "retryable": True}))
        self.assertNotEqual(d.transition.action, "REQUEST_STRUCTURE_REPAIR")

    def test_human_feedback_retains_priority(self):
        c = context_for(review_for(incident_plan()))
        c = replace(c, review=replace(c.review, plan_review={"pending_feedback": ["Adjust the goal"]}))
        d = build_solver_dispatch(c, request_id="human", prompt=SimpleNamespace(content="prompt", references={}))
        self.assertEqual(d.payload["dispatch_context"]["zhongshu_dispatch_mode"], "human_plan_revision")

    def test_all_approved_incident_cannot_mechanically_freeze(self):
        review = replace(review_for(incident_plan()), findings=())
        for group in review.task_groups:
            ledger, rows = apply_group_verdict(review, group.group_id, "APPROVE_GROUP",
                                               member_hashes=current_member_hashes(review, group.group_id))
            review = replace(review, task_review_ledger=ledger, zhongshu_groups=rows)
        ready, reasons = mechanical_freeze_ready(context_for(review))
        self.assertFalse(ready)
        self.assertIn("GROUP_MAX_ITEMS:GRP-001:8", reasons)

    def test_stale_critic_approval_routes_to_repair(self):
        c = context_for(review_for(incident_plan()), "ZHONGSHU_CRITIC")
        d = ZhongshuCriticState().handle(c, event_for("NODE_COMPLETED", {"action": "APPROVE_CRITIC"}))
        self.assertEqual(d.transition.action, "REQUEST_STRUCTURE_REPAIR")
        self.assertEqual(d.effects[0].payload["dispatch_context"]["zhongshu_dispatch_mode"], "plan_structure_repair")

    def test_freeze_routes_to_repair_before_release(self):
        c = context_for(review_for(incident_plan()), "ZHONGSHU_FREEZE_CHECK")
        d = ZhongshuFreezeCheckState().handle(c, event_for("NODE_COMPLETED", {"action": "FREEZE_APPROVED"}))
        self.assertEqual(d.transition.action, "REQUEST_STRUCTURE_REPAIR")
        self.assertEqual(d.effects[0].payload["dispatch_context"]["zhongshu_dispatch_mode"], "plan_structure_repair")


class StructureRepairApplicationTests(unittest.TestCase):
    """Bounded real-app driver: a regression fails instead of waiting forever."""

    def drive(self, app):
        app._ensure_start_event()
        task_id = app.context.identity.task_id
        for _ in range(200):
            c = app.repository.load(task_id).context
            if c.progression.state == "DONE":
                return c
            self.assertNotIn(c.progression.state, {"BLOCKED", "FAILED", "HUMAN_GATE"}, c.recovery)
            event = app.inbox.next(task_id)
            if event:
                app.engine.dispatch(event)
                app.inbox.ack(event)
                app.effects.execute_pending(task_id)
            elif app.repository.pending_effects(task_id):
                app.effects.execute_pending(task_id)
            elif c.progression.state == "RETRY_WAIT":
                app.resume()
            else:
                self.fail(f"No work in {c.progression.state}")
        self.fail("Did not converge in 200 application steps")

    def test_first_oversized_reply_is_reasked_and_plan_reaches_done(self):
        from orchestrator.app import OrchestratorApp
        from orchestrator.adapters import FakeMulticaAdapter
        from test_linear_fsm_entrypoint import _ScriptedMultica, _context, _entry_plan

        class Adapter(_ScriptedMultica):
            solver_calls = 0

            def dispatch(self, request):
                if request.target_state != "ZHONGSHU_SOLVER":
                    return super().dispatch(request)
                receipt = FakeMulticaAdapter.dispatch(self, request)
                self.solver_calls += 1
                p = incident_plan() if self.solver_calls == 1 else repaired_reply()["plan"]
                p["requirements"] = _entry_plan()["requirements"]
                for item in p["items"]:
                    item["source_requirement_ids"] = ["req-000001"]
                self._reply(request, dict(action="READY_FOR_CRITIC", mode="TASK_GRAPH_FORMALIZATION_READ_ONLY", plan=p,
                    group_docs=[dict(group_id=g["group_id"], markdown=doc(g)) for g in p["groups"]]))
                return receipt

        with tempfile.TemporaryDirectory() as directory:
            adapter = Adapter()
            app = OrchestratorApp(_context(), root=directory, multica=adapter, poll_interval=0, timeout_seconds=2)
            final = self.drive(app)
            self.assertEqual(adapter.solver_calls, 2)
            self.assertEqual(len(final.review.task_items), 10)
            self.assertEqual(len(final.review.task_groups), 3)
            self.assertEqual(structural_integrity_errors(final.review.plan), [])

    def test_resuming_invalid_critic_uses_preflight_then_regrouping(self):
        from orchestrator.app import OrchestratorApp
        from orchestrator.adapters import FakeMulticaAdapter
        from test_linear_fsm_entrypoint import _ScriptedMultica

        class Adapter(_ScriptedMultica):
            solver_modes = []

            def dispatch(self, request):
                if request.target_state != "ZHONGSHU_SOLVER":
                    return super().dispatch(request)
                receipt = FakeMulticaAdapter.dispatch(self, request)
                self.solver_modes.append(request.context.get("zhongshu_dispatch_mode"))
                self._reply(request, repaired_reply())
                return receipt

        c = context_for(replace(review_for(incident_plan()), findings=()), "RETRY_WAIT")
        c = replace(c, progression=replace(c.progression, resume_state="ZHONGSHU_CRITIC"),
                    parallel=ParallelState(zhongshu=ZhongshuParallelLimits(plan_review_gate=False)))
        with tempfile.TemporaryDirectory() as directory:
            adapter = Adapter()
            app = OrchestratorApp(c, root=directory, multica=adapter, poll_interval=0, timeout_seconds=2)
            app.resume()
            final = self.drive(app)
            self.assertEqual(adapter.solver_modes, ["plan_structure_repair"])
            self.assertEqual(adapter.dispatched[0].target_state, "ZHONGSHU_SOLVER")
            self.assertEqual(len(final.review.task_groups), 3)


if __name__ == "__main__":
    unittest.main()
