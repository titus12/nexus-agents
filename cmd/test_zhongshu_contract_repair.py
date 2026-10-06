"""Regression for the three defects observed in task-20261005-55ce4b."""
from dataclasses import replace
import tempfile
import unittest

from orchestrator.acceptance_standards import acceptance_signal_gate
from orchestrator.adapters import FakeMulticaAdapter
from orchestrator.app import OrchestratorApp
from orchestrator.contracts.zhongshu_solver import CONTRACT
from orchestrator.domain.context import ReviewState
from orchestrator.domain.policies.solver_plan import (
    carry_forward_group_docs, is_retryable_solver_reply_error,
    structural_integrity_errors,
)
from orchestrator.domain.zhongshu.solver import process_solver_reply
from orchestrator.domain.zhongshu_doc import (
    ZhongshuRequirementDoc, doc_violation_details,
)
from orchestrator.domain.zhongshu_doc_repair import repair_group_doc
from orchestrator.zhongshu_review_queue import structural_gate
from test_human_document_loop import _drive, _HumanRevisionMultica, _gated_context
from test_linear_fsm_entrypoint import _ScriptedMultica, _context
from test_zhongshu_doc import _doc_markdown, _review

QUALITATIVE_SIGNAL = (
    "评审报告对提高组间并发或放宽agent pool宽度的可行性给出有依据的结论，"
    "识别可改进点或明确写明无进一步优化空间"
)


def _plan(split=False):
    assignments = [("T-001", "GRP-001", ["T-005"]),
                   ("T-002", "GRP-001", ["T-005"]),
                   ("T-003", "GRP-002", ["T-005"]),
                   ("T-004", "GRP-003", ["T-005"]),
                   ("T-005", "GRP-004", []),
                   ("T-006", "GRP-005" if split else "GRP-004",
                    ["T-001", "T-002", "T-003", "T-004"])]
    items = [{"item_id": item, "group_id": group, "title": item,
              "objective": item, "dependencies": deps,
              "source_requirement_ids": ["req-000001"],
              "acceptance_signals": [QUALITATIVE_SIGNAL if item == "T-003"
                                     else item + " is observable"]}
             for item, group, deps in assignments]
    groups = [{"group_id": group, "item_ids": [i["item_id"] for i in items
                                              if i["group_id"] == group]}
              for group in dict.fromkeys(i["group_id"] for i in items)]
    return {"items": items, "groups": groups, "requirements": [], "dependencies": []}


def _docs(plan, version=1):
    return [{"group_id": g["group_id"], "markdown": _doc_markdown(
        group_id=g["group_id"], version=version,
        acceptance_by_item={i["item_id"]: tuple(i["acceptance_signals"])
                            for i in plan["items"] if i["group_id"] == g["group_id"]})}
            for g in plan["groups"]]


class GroupIdentityTests(unittest.TestCase):
    def test_decorations_preserve_ids_with_internal_and_trailing_underscores(self):
        for group in ("GRP-001", "review_a.1_", "g1"):
            for wrap in ("**", "__", "`", "_"):
                for title in (f"# {wrap}{group}{wrap} 需求文档 [v1]",
                              f"# {wrap}{group} 需求文档 [v1]{wrap}"):
                    with self.subTest(title=title):
                        self.assertEqual(ZhongshuRequirementDoc.parse(title).group_id, group)
                        repair = repair_group_doc(title, group_id=group, version=1,
                                                  member_item_ids=())
                        self.assertEqual(repair.markdown, f"# {group} 需求文档 [v1]")

    def test_supported_ids_parse_and_fold_without_fake_repair(self):
        for group in ("GRP-001", "group-00001", "g1", "review_a.1"):
            with self.subTest(group=group):
                doc = _doc_markdown(group_id=group)
                repair = repair_group_doc(doc, group_id=group, version=1,
                                          member_item_ids=())
                self.assertEqual(repair.repairs, ())
                self.assertEqual(ZhongshuRequirementDoc.parse(doc).group_id, group)
                folded, error = carry_forward_group_docs(
                    [{"group_id": group, "markdown": doc}], (),
                    editable_group_ids=(group,), required_group_ids=(group,),
                    review=_review(group_id=group))
                self.assertEqual(error, "")
                self.assertEqual(folded[group]["markdown"], doc)

    def test_foreign_title_is_never_silently_rebound(self):
        for title in ("# GRP-002 需求文档 [v1]", "# **GRP-002** 需求文档"):
            with self.subTest(title=title):
                doc = _doc_markdown(group_id="GRP-002")
                doc = title + "\n" + doc.split("\n", 1)[1]
                repair = repair_group_doc(doc, group_id="GRP-001", version=1,
                                          member_item_ids=())
                self.assertEqual(repair.markdown, doc)
                self.assertEqual(repair.repairs, ())

    def test_bad_title_feedback_contains_group_and_actual_line(self):
        details = doc_violation_details("bad header\n", group_id="GRP-001")
        feedback = details[0].render(with_expected=True)
        self.assertIn("GRP-001", feedback)
        self.assertIn("bad header", feedback)
        self.assertIn("需求文档 [v1]", feedback)

    def test_schema_rejects_unsafe_ids_at_both_paths(self):
        for group in ("bad id", "../x", "中文", ""):
            payload = CONTRACT.example_payload()
            payload["plan"]["groups"] = [{"group_id": group, "item_ids": []}]
            payload["group_docs"] = [{"group_id": group, "markdown": "x"}]
            errors = CONTRACT.validate(payload)
            self.assertTrue(any("plan.groups[0].group_id" in e for e in errors), errors)
            self.assertTrue(any("group_docs[0].group_id" in e for e in errors), errors)


class GroupTopologyTests(unittest.TestCase):
    def test_group_membership_references_work_without_item_group_id(self):
        plan = _plan()
        for item in plan["items"]:
            del item["group_id"]
        self.assertTrue(any(i.startswith("GROUP_DEPENDENCY_CYCLE:")
                            for i in structural_integrity_errors(plan)))

    def test_deep_group_dag_does_not_exhaust_recursion(self):
        size = 1500
        plan = {"items": [{"item_id": f"T-{i}", "group_id": f"G-{i}",
                           "dependencies": [f"T-{i-1}"] if i else [],
                           "source_requirement_ids": ["req-1"]}
                          for i in range(size)], "groups": []}
        self.assertEqual(structural_integrity_errors(plan), [])

    def test_item_dag_that_projects_to_group_cycle_is_rejected(self):
        issues = structural_gate(_plan())
        self.assertFalse(any(i.startswith("DEPENDENCY_CYCLE:") for i in issues))
        cycles = [i for i in structural_integrity_errors(_plan())
                  if i.startswith("GROUP_DEPENDENCY_CYCLE:")]
        self.assertTrue(cycles, issues)
        self.assertTrue(any("GRP-001" in i and "GRP-004" in i for i in cycles))
        error = "SOLVER_PLAN_STRUCTURE_INVALID:" + ";".join(cycles)
        self.assertTrue(is_retryable_solver_reply_error(error))

    def test_split_prerequisite_and_summary_allows_valid_plan(self):
        self.assertEqual(structural_integrity_errors(_plan(split=True)), [])
        result = process_solver_reply({"plan": _plan(True), "group_docs": _docs(_plan(True)),
                                       "action": "READY_FOR_CRITIC"}, ReviewState())
        self.assertEqual(result.error, "")

    def test_internal_dependency_is_not_a_group_self_cycle(self):
        plan = _plan(True)
        plan["items"][1]["dependencies"].append("T-001")
        self.assertEqual(structural_integrity_errors(plan), [])

    def test_invalid_topology_bounces_before_critic(self):
        result = process_solver_reply({"plan": _plan(), "group_docs": _docs(_plan()),
                                       "action": "READY_FOR_CRITIC"}, ReviewState())
        self.assertIn("GROUP_DEPENDENCY_CYCLE:", result.error)
        self.assertTrue(is_retryable_solver_reply_error(result.error))


class QualitativeAcceptanceTests(unittest.TestCase):
    def _gate(self, text):
        return acceptance_signal_gate({"items": [{"item_id": "T-003",
                                                   "acceptance_signals": [text]}]})

    def test_qualitative_feasibility_review_does_not_claim_measured_improvement(self):
        for text in (QUALITATIVE_SIGNAL, "评估降低延迟的可行性并给出源码依据",
                     "报告分析提高吞吐是否有优化空间，给出有依据的结论"):
            with self.subTest(text=text):
                self.assertEqual(self._gate(text), [])

    def test_feasibility_word_is_not_a_blanket_escape_for_outcome_claims(self):
        for text in ("评估可行性，确认吞吐提升20%", "可行性报告保证降低延迟",
                     "评估降低延迟的可行性，确认延迟缩短", "吞吐提高一倍",
                     "吞吐提高", "延迟缩短30%", "report confirms 2x faster"):
            with self.subTest(text=text):
                self.assertEqual(self._gate(text), ["ACCEPTANCE_SIGNAL_UNVERIFIABLE:T-003:0"])

    def test_real_measurement_or_demotion_still_passes(self):
        for text in ("吞吐提升20%，实测来源：run-1", "吞吐提升20% 待实测",
                     "确认 elapsed_ms 字段存在且为整数"):
            self.assertEqual(self._gate(text), [])


class _TopologyRepairMultica(_ScriptedMultica):
    def __init__(self):
        super().__init__()
        self.solver_calls = 0

    def dispatch(self, request):
        if request.target_state != "ZHONGSHU_SOLVER":
            return super().dispatch(request)
        receipt = FakeMulticaAdapter.dispatch(self, request)
        self.solver_calls += 1
        plan = _plan(split=self.solver_calls > 1)
        self._reply(request, {"action": "READY_FOR_CRITIC", "plan": plan,
                              "changes": [], "group_docs": _docs(plan)})
        return receipt


class ScriptedApplicationTests(unittest.TestCase):
    def test_repeated_cycle_exhausts_budget_without_committing_invalid_plan(self):
        class NeverRepairs(_TopologyRepairMultica):
            def dispatch(self, request):
                if request.target_state != "ZHONGSHU_SOLVER":
                    return super().dispatch(request)
                receipt = FakeMulticaAdapter.dispatch(self, request)
                self.solver_calls += 1
                plan = _plan()
                self._reply(request, {"action": "READY_FOR_CRITIC", "plan": plan,
                                      "changes": [], "group_docs": _docs(plan)})
                return receipt

        with tempfile.TemporaryDirectory() as directory:
            adapter = NeverRepairs()
            context = _context()
            app = OrchestratorApp(context, root=directory, multica=adapter, poll_interval=0)
            final = _drive(app, limit=150)
            self.assertEqual(final.progression.state, "BLOCKED")
            self.assertEqual(adapter.solver_calls, context.recovery.max_reply_retries + 1)
            self.assertFalse(any(r.target_state in {"ZHONGSHU_CRITIC", "ZHONGSHU_FREEZE_CHECK"}
                                 or r.target_state.startswith("MENXIA") for r in adapter.dispatched))
            self.assertIsNone(final.review.plan)
            self.assertEqual(final.review.zhongshu_groups, ())

    def test_human_cycle_retries_only_solver_and_preserves_the_reviewed_document(self):
        class HumanCycleOnce(_HumanRevisionMultica):
            human_attempts = 0

            def _reply_solver(self, request):
                if request.context.get("zhongshu_dispatch_mode") != "human_plan_revision":
                    return super()._reply_solver(request)
                self.human_requests.append(request)
                self.human_attempts += 1
                plan = _plan(split=self.human_attempts > 1)
                self._reply(request, {"action": "READY_FOR_CRITIC",
                                      "mode": "TASK_GRAPH_FORMALIZATION_READ_ONLY",
                                      "plan": plan, "group_docs": _docs(plan), "changes": []})

        with tempfile.TemporaryDirectory() as directory:
            adapter = HumanCycleOnce()
            app = OrchestratorApp(_gated_context(), root=directory, multica=adapter,
                                  poll_interval=0)
            first = _drive(app)
            before = len(adapter.dispatched)
            self.assertTrue(app.resume("重新分组"))
            final = _drive(app, limit=150)
            self.assertEqual(final.progression.state, "HUMAN_GATE")
            self.assertEqual(final.human_gate.reason_code, "PLAN_REVIEW")
            self.assertEqual(final.review.plan_review["version"],
                             first.review.plan_review["version"] + 1)
            self.assertEqual([r.target_state for r in adapter.dispatched[before:]],
                             ["ZHONGSHU_SOLVER"] * 2)
            self.assertEqual(adapter.human_requests[1].context["current_requirements_document"],
                             first.review.plan_review["markdown"])
            self.assertEqual(adapter.human_requests[1].context["human_review_feedback"],
                             adapter.human_requests[0].context["human_review_feedback"])

    def test_cycle_retries_solver_then_all_groups_freeze_and_reach_human(self):
        context = _context()
        context = replace(context, parallel=replace(context.parallel, zhongshu=replace(
            context.parallel.zhongshu, plan_review_gate=True)))
        adapter = _TopologyRepairMultica()
        with tempfile.TemporaryDirectory() as directory:
            app = OrchestratorApp(context, root=directory, multica=adapter, poll_interval=0)
            final = _drive(app, limit=150)
            self.assertEqual(final.progression.state, "HUMAN_GATE")
            self.assertEqual(final.human_gate.reason_code, "PLAN_REVIEW")
            self.assertEqual(adapter.solver_calls, 2)
            self.assertEqual(len(final.review.zhongshu_groups), 5)
            self.assertTrue(all(g.stage == "FROZEN" for g in final.review.zhongshu_groups))
            targets = [r.target_state for r in adapter.dispatched]
            first = targets.index("ZHONGSHU_SOLVER")
            self.assertEqual(targets[first:first + 2], ["ZHONGSHU_SOLVER"] * 2)
            retry = adapter.dispatched[first + 1]
            self.assertIn("GROUP_DEPENDENCY_CYCLE", str(retry.context) + str(retry.prompt))
            self.assertFalse(any(t.startswith("MENXIA") for t in targets))
