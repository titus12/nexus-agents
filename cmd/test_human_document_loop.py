"""Real application loop using scripted LLM replies; never contacts Feishu."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
import re
from unittest.mock import patch

from orchestrator.app import OrchestratorApp, _with_parallel_flag_overrides
from orchestrator.domain.context import context_to_dto, context_from_dto
from orchestrator.domain.events import DomainEvent
from orchestrator.domain.plan_review import text_hash, group_chapter, matches_current
from orchestrator.domain.states import StateRegistry
from orchestrator.domain.menxia_doc import menxia_approval_blockers
from orchestrator.transport.external import HumanReply
from test_plan_review_gate import _ReviewPort
from test_zhongshu_convergence_e2e import _ConvergingMultica, _context, _plan, _zhongshu_doc


class _HumanRevisionMultica(_ConvergingMultica):
    def __init__(self):
        super().__init__()
        self.human_requests = []
        self.item_two_rejections = 1

    def _reply_solver(self, request):
        if request.context.get("zhongshu_dispatch_mode") != "human_plan_revision":
            return super()._reply_solver(request)
        self.human_requests.append(request)
        feedback = request.context["human_review_feedback"]
        plan = request.context["current_formal_plan"]
        plan = {**plan, "items": [dict(item) for item in plan["items"]]}
        if any("增加任务" in entry["answer"] for entry in feedback):
            plan["items"].append({**plan["items"][0], "item_id": "item-000003", "title": "Added task",
                                  "objective": "补充核验", "acceptance_signals": ["C is observable"]})
            plan["groups"] = [{"group_id": "group-000001", "item_ids": [item["item_id"] for item in plan["items"]]}]
        if any("重新分组" in entry["answer"] for entry in feedback):
            for index, item in enumerate(plan["items"], 2):
                item["group_id"] = f"group-{index:06d}"
            plan["groups"] = [{"group_id": item["group_id"], "item_ids": [item["item_id"]]} for item in plan["items"]]
        summary = "\n".join(entry["answer"] for entry in feedback)
        group_docs = []
        for group in plan["groups"]:
            group_id = group["group_id"]
            previous = (request.context.get("group_docs_authoritative") or {}).get(group_id, "")
            marker = re.search(r"\[v(\d+)\]", previous)
            version = int(marker.group(1)) + 1 if marker else 1
            acceptance = tuple(line for item in plan["items"] if item["group_id"] == group_id
                               for line in ["### " + item["item_id"], *item["acceptance_signals"]])
            doc = _zhongshu_doc(version, acceptance).replace("group-000001", group_id)
            doc = doc.replace(group_id + " 的上下文。", group_id + " 的上下文。\n已修订：" + summary)
            group_docs.append({"group_id": group_id, "markdown": doc})
        self._reply(request, {"action": "READY_FOR_CRITIC", "mode": "TASK_GRAPH_FORMALIZATION_READ_ONLY",
                              "plan": plan, "changes": [], "summary": summary,
                              "group_docs": group_docs})


def _gated_context():
    context = _context()
    return replace(context, parallel=replace(context.parallel,
                   zhongshu=replace(context.parallel.zhongshu, plan_review_gate=True)))


def _drive(app, limit=80):
    task_id = app.context.identity.task_id
    app._ensure_start_event()
    for _ in range(limit):
        event = app.inbox.next(task_id)
        if event:
            app.engine.dispatch(event)
            app.inbox.ack(event)
            app.effects.execute_pending(task_id)
            continue
        if app.repository.pending_effects(task_id):
            app.effects.execute_pending(task_id)
            continue
        context = app.repository.load(task_id).context
        if context.progression.state == "RETRY_WAIT":
            app.resume()
            continue
        if context.progression.state in {"HUMAN_GATE", "DONE", "BLOCKED"}:
            return context
        raise AssertionError("No work pending in " + context.progression.state)
    raise AssertionError("Loop did not converge within bounded scripted steps")


class HumanDocumentLoopTests(unittest.TestCase):
    def test_malformed_revision_retries_solver_and_preserves_feedback_without_critic(self):
        class MalformedOnce(_HumanRevisionMultica):
            bad_reply = False

            def _reply(self, request, payload):
                if request.context.get("zhongshu_dispatch_mode") == "human_plan_revision" and not self.bad_reply:
                    self.bad_reply = True
                    payload = {**payload, "group_docs": []}
                return super()._reply(request, payload)

        with tempfile.TemporaryDirectory() as directory:
            adapter = MalformedOnce()
            app = OrchestratorApp(_gated_context(), root=directory, multica=adapter, poll_interval=0)
            first = _drive(app)
            before = len(adapter.dispatched)
            app.resume("补充责任边界")
            revised = _drive(app)
            self.assertEqual(revised.human_gate.reason_code, "PLAN_REVIEW")
            self.assertEqual(revised.review.plan_review["version"], first.review.plan_review["version"] + 1)
            self.assertEqual([r.target_state for r in adapter.dispatched[before:]], ["ZHONGSHU_SOLVER", "ZHONGSHU_SOLVER"])
            self.assertTrue(all(r.context["human_review_feedback"][0]["answer"] == "补充责任边界"
                                for r in adapter.human_requests))
            self.assertEqual(revised.review.task_review_ledger, ())

    def test_production_gate_cannot_be_disabled_by_legacy_flags(self):
        with patch.dict("os.environ", {"NEXUS_TEST_NO_EXTERNAL_NOTIFICATIONS": "0",
                                       "ZHONGSHU_PLAN_REVIEW_GATE": "0"}):
            context = _with_parallel_flag_overrides(_context())
            self.assertTrue(context.parallel.zhongshu.plan_review_gate)

    def test_dependency_groups_freeze_to_closure_before_offer(self):
        from test_zhongshu_freeze_groups import _context as grouped, _doc_row, _event
        context = grouped((_doc_row("group-000001", "CONVERGED", ("A is observable", "B is observable")),
                           _doc_row("group-000002", "CONVERGED", ("C is observable",))))
        context = replace(context, parallel=replace(context.parallel,
                           zhongshu=replace(context.parallel.zhongshu, plan_review_gate=True)))
        decision = StateRegistry.default().get("ZHONGSHU_FREEZE_CHECK").handle(context, _event("FREEZE_APPROVED"))
        self.assertEqual(decision.transition.action, "PLAN_REVIEW")
        self.assertTrue(all(row.stage == "FROZEN" for row in decision.update.review.zhongshu_groups))
        self.assertEqual([effect.effect_type for effect in decision.effects], ["plan_review_doc"])
        packet = decision.update.review.plan_review["markdown"]
        self.assertIn("requirement-group:group-000001:begin", packet)
        self.assertIn("requirement-group:group-000002:begin", packet)

    def test_multiple_natural_feedback_rounds_then_exact_pass_same_document(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter, port = _HumanRevisionMultica(), _ReviewPort()
            app = OrchestratorApp(_gated_context(), root=directory, multica=adapter,
                                  notification_port=port, poll_interval=0, timeout_seconds=5)
            first = _drive(app)
            self.assertEqual(first.human_gate.reason_code, "PLAN_REVIEW")
            self.assertFalse(any(r.target_state.startswith("MENXIA") for r in adapter.dispatched))
            for index, answer in enumerate(("补充边界说明。" * 100, "增加任务，补充 C 的核验", "同意"), 2):
                before = len(adapter.dispatched)
                self.assertTrue(app.resume(answer))
                context = _drive(app)
                self.assertEqual([request.target_state for request in adapter.dispatched[before:]], ["ZHONGSHU_SOLVER"])
                self.assertEqual(context.progression.state, "HUMAN_GATE")
                self.assertEqual(context.human_gate.reason_code, "PLAN_REVIEW")
                self.assertEqual(context.review.plan_review["version"], index)
                self.assertEqual(adapter.human_requests[-1].context["human_review_feedback"][0]["answer"], answer)
                self.assertIn(answer, context.review.plan_review["markdown"])
                self.assertFalse(any(r.target_state.startswith("MENXIA") for r in adapter.dispatched))
                self.assertTrue(matches_current(context.review, context.human_gate.document_hash))
                self.assertEqual(context.review.zhongshu_revision_round, 0)
            packet = context.review.plan_review
            self.assertEqual(len(context.review.task_items), 3)
            restarted = OrchestratorApp(context, root=directory, multica=adapter,
                                        notification_port=port, poll_interval=0, timeout_seconds=5)
            self.assertEqual(restarted.repository.load(context.identity.task_id).context.review.plan_review, packet)
            self.assertTrue(restarted.resume("通过"))
            final = _drive(restarted)
            self.assertEqual(final.progression.state, "DONE")
            menxia = [r for r in adapter.dispatched if r.target_state.startswith("MENXIA_GROUP_")
                      and "requirement_markdown" in r.context]
            self.assertTrue(menxia)
            for request in menxia:
                self.assertEqual(request.context["requirement_markdown"], packet["markdown"])
                self.assertEqual(request.context["requirements_hash"], text_hash(packet["markdown"]))
            root = Path(directory) / context.identity.task_id
            self.assertEqual((root / "zhongshu-requirements.md").read_bytes(), packet["markdown"].encode("utf-8"))
            self.assertEqual(len(list((root / "requirements").glob("*.md"))), 4)
            critic = [r for r in adapter.dispatched if r.context.get("zhongshu_dispatch_mode") == "group_review"]
            self.assertEqual(len(critic), 1)  # Initial planning still uses Critic.
            self.assertEqual(critic[0].context["human_review_feedback"], [])

    def test_human_revision_can_regroup_and_discard_stale_frozen_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter = _HumanRevisionMultica()
            app = OrchestratorApp(_gated_context(), root=directory, multica=adapter, poll_interval=0)
            _drive(app)
            self.assertTrue(app.resume("重新分组，让两个方向独立审查"))
            context = _drive(app)
            self.assertEqual(context.human_gate.reason_code, "PLAN_REVIEW")
            self.assertEqual({row.group_id for row in context.review.zhongshu_groups}, {"group-000002", "group-000003"})
            self.assertTrue(all(row.stage == "FROZEN" and row.doc_version == 1 for row in context.review.zhongshu_groups))
            self.assertFalse(any(r.target_state.startswith("MENXIA") for r in adapter.dispatched))
            self.assertTrue(matches_current(context.review, context.human_gate.document_hash))

    def test_stale_pass_empty_reply_and_multiple_feedback_dedup(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter, port = _HumanRevisionMultica(), _ReviewPort()
            app = OrchestratorApp(_gated_context(), root=directory, multica=adapter,
                                  notification_port=port, poll_interval=0)
            context = _drive(app)
            self.assertFalse(app.resume(""))
            changed = replace(context, review=replace(context.review, plan={**context.review.plan, "plan_id": "changed"}))
            event = DomainEvent(name="RESUME", task_id=context.identity.task_id,
                                sequence=context.progression.sequence, payload={"answer": "通过"},
                                occurred_at=context.progression.entered_at)
            self.assertIsNone(StateRegistry.default().get("HUMAN_GATE").handle(changed, event).transition)
            changed_doc = replace(context, review=replace(context.review, zhongshu_groups=(
                replace(context.review.zhongshu_groups[0], doc_markdown=context.review.zhongshu_groups[0].doc_markdown + "\n变更"),)))
            self.assertIsNone(StateRegistry.default().get("HUMAN_GATE").handle(changed_doc, event).transition)
            disguised = replace(event, name="NODE_COMPLETED", payload={"action": "RESUME", "answer": "通过。"})
            self.assertEqual(StateRegistry.default().get("HUMAN_GATE").handle(context, disguised).transition.action,
                             "PLAN_REVIEW_REJECTED")
            gate_id = context.human_gate.decision_id
            port.replies = [HumanReply("old-gate", "通过", "u", "old"),
                            HumanReply(gate_id, "通过", "u", "pass"),
                            HumanReply(gate_id, "补充第二条", "u", "b"),
                            HumanReply(gate_id, "补充第一条", "u", "a"),
                            HumanReply(gate_id, "补充第一条", "u", "a")]
            app._poll_human_gate_reply(context.identity.task_id)
            revised = _drive(app)
            feedback = adapter.human_requests[0].context["human_review_feedback"]
            self.assertEqual([entry["external_id"] for entry in feedback], ["a", "b"])
            self.assertEqual(revised.review.plan_review["version"], 2)
            self.assertEqual(context_from_dto(context_to_dto(revised)).review.plan_review, revised.review.plan_review)
            self.assertTrue(all(entry["document_hash"] == context.review.plan_review["hash"] for entry in feedback))
            self.assertFalse(any(r.target_state.startswith("MENXIA") for r in adapter.dispatched))
            # A supplement sent while the LLM was busy on v1 is recovered on
            # the next gate, while v1's pass cannot authorize v2.
            port.replies = [HumanReply(gate_id, "通过", "u", "old-pass"),
                            HumanReply(gate_id, "修订期间补充的第三条", "u", "late"),
                            HumanReply(gate_id, "补充第一条", "u", "a")]
            app._last_gate_poll = 0
            app._poll_human_gate_reply(context.identity.task_id)
            next_review = _drive(app)
            self.assertEqual(next_review.review.plan_review["version"], 3)
            late = adapter.human_requests[-1].context["human_review_feedback"]
            self.assertEqual([entry["external_id"] for entry in late], ["late"])
            self.assertEqual(late[0]["document_version"], 1)
            self.assertEqual(late[0]["document_hash"], context.review.plan_review["hash"])
            port.replies = [HumanReply(gate_id, "通过", "u", "old-pass"),
                            HumanReply(gate_id, "补充第一条", "u", "a")]
            app._last_gate_poll = 0
            app._poll_human_gate_reply(context.identity.task_id)
            self.assertIsNone(app.inbox.next(context.identity.task_id))

    def test_acceptance_mapping_uses_only_own_group_chapter(self):
        chapter = _zhongshu_doc(1, ("A is observable",))
        other = chapter.replace("group-000001", "group-000002").replace("A is observable", "Other signal")
        packet = ("<!-- requirement-group:group-000001:begin -->\n" + chapter
                  + "\n<!-- requirement-group:group-000001:end -->\n"
                  + "<!-- requirement-group:group-000002:begin -->\n" + other
                  + "\n<!-- requirement-group:group-000002:end -->")
        self.assertEqual(group_chapter(packet, "group-000001"), chapter)
        errors = menxia_approval_blockers("### 验收映射\n- Other signal -> 实测",
                                         requirement_markdown=packet, group_id="group-000001")
        self.assertTrue(any("orphan" in error for error in errors), errors)
        self.assertTrue(menxia_approval_blockers("", requirement_markdown=packet, group_id="missing"))
