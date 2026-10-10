"""Regressions for the 2026-09-30 live Zhongshu stalls (no external calls)."""
from dataclasses import replace
import unittest
import tempfile
import hashlib
from unittest.mock import patch

from orchestrator.adapters import _apply_quote_gate
from orchestrator.agent_result_file import AgentResultFileError
from orchestrator.contracts.zhongshu_critic import group_action_with_followups
from orchestrator.domain.context import ZhongshuGroupState
from orchestrator.domain.errors import UNSTRUCTURED_REPLY_EVENT
from orchestrator.domain.policies.zhongshu_group import apply_zhongshu_round
from orchestrator.runtime.agent_effects import AgentWorkerRunner, AgentNodeJoiner
from orchestrator.runtime.concurrency import ConcurrencyLimits, RuntimeConcurrencyAdmission
from orchestrator.runtime.ports import AdmissionKey, RemoteRunStatus
from orchestrator.transport.external import AgentRequest, HumanGate
from orchestrator.transport import RawTransportReply
from orchestrator.zhongshu_parallel import validate_zhongshu_requirement_contract
from test_zhongshu_group_policy import _review, _open_blockers
from test_linear_fsm_parallel_effects import _Transport, _request
from test_plan_review_gate import _context, _freeze_event
from orchestrator.domain.states import StateRegistry
from test_human_gate_reply_poll import PollReplyAdapterTests
from test_zhongshu_group_pipeline_e2e import _GroupPipelineMultica, _context as group_context
from orchestrator.app import OrchestratorApp
from test_zhongshu_convergence_e2e import _ConvergingMultica, _context as convergence_context, _zhongshu_doc


class RequirementProvenanceTests(unittest.TestCase):
    def payload(self, quote="Review workflow efficiency"):
        return {"action": "REQUIREMENT_CONTRACT_READY", "requirements": [{
            "requirement_id": "req-1", "statement": "Review workflow efficiency",
            "source": "request.raw_request", "source_quote": quote,
            "priority": "must", "scope": "in", "kind": "task",
            "acceptance_signal": "Report causes with evidence",
        }]}

    def test_rejects_invented_runtime_fact_as_request_quote(self):
        payload = self.payload("menxia.enabled=false")
        error = validate_zhongshu_requirement_contract(payload, original_request="Review workflow efficiency")
        self.assertIn("SOURCE_QUOTE_INVALID", error)
        request = AgentRequest("t", "r", "a", "review-analyst", "ZHONGSHU", "", "r",
                               context={"original_request": "Review workflow efficiency"})
        with self.assertRaisesRegex(AgentResultFileError, "SOURCE_QUOTE_INVALID"):
            _apply_quote_gate(request, payload)

    def test_exact_request_quote_passes_and_legacy_contract_stays_readable(self):
        self.assertEqual(validate_zhongshu_requirement_contract(self.payload(), original_request="Review workflow efficiency"), "")
        payload = self.payload()
        del payload["requirements"][0]["source_quote"]
        self.assertEqual(validate_zhongshu_requirement_contract(payload), "")


class GroupEfficiencyTests(unittest.TestCase):
    def test_revision_critic_receives_new_document_and_matching_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter = _ConvergingMultica()
            app = OrchestratorApp(convergence_context(), root=directory,
                                  multica=adapter, poll_interval=0, timeout_seconds=5)
            self.assertTrue(app.run())
        reviews = [request for request in adapter.dispatched
                   if request.context.get("zhongshu_dispatch_mode") == "group_review"]
        self.assertEqual(len(reviews), 2)
        for request, version in zip(reviews, (1, 2)):
            expected = _zhongshu_doc(version=version, acceptance=("A is observable", "B is observable"))
            self.assertIn(expected, request.prompt)
            self.assertEqual(request.context["doc_hash"], hashlib.sha256(expected.encode("utf-8")).hexdigest())

    def test_real_fsm_freezes_p2_only_group_without_solver_revision(self):
        class FollowupMultica(_GroupPipelineMultica):
            def _reply(self, request, payload):
                if request.context.get("zhongshu_dispatch_mode") == "group_review":
                    for finding in payload.get("findings", []):
                        finding["severity"] = "P2"
                        finding["claim"] = "optional acceptance wording clarification"
                return super()._reply(request, payload)

        with tempfile.TemporaryDirectory() as directory:
            adapter = FollowupMultica()
            app = OrchestratorApp(group_context(), root=directory, multica=adapter,
                                  poll_interval=0, timeout_seconds=5)
            self.assertTrue(app.run())
            context = app.repository.load("task-group-e2e").context
        self.assertEqual(context.progression.state, "DONE")
        self.assertEqual(adapter.group_revise_dispatches, [])
        self.assertTrue(all(row.stage == "FROZEN" for row in context.review.zhongshu_groups))
        self.assertTrue(any(finding.severity == "P2" for finding in context.review.findings))

    def test_p2_revision_is_retained_as_followup_without_a_revision_wave(self):
        joiner = object.__new__(AgentNodeJoiner)
        joiner._binding_contexts = {"w": {"group_id": "g", "member_ids": ["i"]}}
        joiner._node_run_id = "n"
        result = joiner._join_group_review((), [{"group_id": "g", "action": "REVISE_GROUP",
            "findings": [{"finding_id": "f", "item_id": "i", "severity": "P2", "status": "OPEN"}]}])
        self.assertEqual(result.aggregate["action"], "APPROVE_CRITIC")
        self.assertEqual(result.aggregate["findings"][0]["status"], "OPEN")

    def test_p1_and_historical_blockers_cannot_be_promoted(self):
        p2 = [{"severity": "P2", "status": "OPEN"}]
        p1 = [{"severity": "P1", "status": "OPEN"}]
        self.assertEqual(group_action_with_followups("REVISE_GROUP", p1, []), "REVISE_GROUP")
        self.assertEqual(group_action_with_followups("REVISE_GROUP", p2, p1), "REVISE_GROUP")
        self.assertEqual(group_action_with_followups("REVISE_GROUP", [], []), "REVISE_GROUP")

    def test_equal_count_with_old_finding_closed_is_progress(self):
        initial = _review(findings=_open_blockers(1))
        rows = apply_zhongshu_round(initial, attempted_item_ids=("item-000001",), max_rounds=8)
        new = replace(initial.findings[0], finding_id="finding-new")
        next_review = replace(initial, zhongshu_groups=rows, findings=(new,))
        row = apply_zhongshu_round(next_review, attempted_item_ids=("item-000001",), max_rounds=8)[0]
        self.assertEqual(row.stalled_rounds, 0)
        restored = ZhongshuGroupState.from_dict(row.to_dict())
        self.assertEqual(restored.last_open_finding_ids, ("finding-new",))

    def test_legacy_p2_revision_has_a_nonzero_progress_baseline(self):
        review = _review(findings=(replace(_open_blockers(1)[0], severity="P2"),))
        row = apply_zhongshu_round(review, attempted_item_ids=("item-000001",), max_rounds=8)[0]
        self.assertEqual(row.last_open_count, 1)


class AdmissionRoleTests(unittest.TestCase):
    def test_role_limit_applies_across_actual_phase_names(self):
        admission = RuntimeConcurrencyAdmission(ConcurrencyLimits(global_max=6, per_task_max=6, analyst_max=1))
        self.assertIsNotNone(admission.try_acquire(AdmissionKey("t1", "ZHONGSHU", "w1", "r", role="review-analyst")))
        self.assertIsNone(admission.try_acquire(AdmissionKey("t2", "MENXIA", "w2", "r", role="review-analyst")))
        self.assertIsNotNone(admission.try_acquire(AdmissionKey("t2", "MENXIA", "w3", "r", role="review-critic")))


class RemoteFailureTests(unittest.TestCase):
    def test_error_comment_cannot_hide_the_idle_watchdog(self):
        transport = _Transport("FAILED")
        transport.status = lambda request: RemoteRunStatus(request.request_id, request.operation_id, "FAILED",
            error_message="force-stopped by idle watchdog after 10m0s")
        transport.poll = lambda request: (RawTransportReply("review-analyst", "e", request.request_id,
            {"action": UNSTRUCTURED_REPLY_EVENT}, "", "test"),)
        result = AgentWorkerRunner(transport, timeout_seconds=1, poll_interval=0).run_once(_request())
        self.assertEqual(result.status, "FAILED")
        self.assertEqual(result.failure.error_code, "REMOTE_RUN_FAILED")
        self.assertIn("idle watchdog", result.failure.message)


class AutomaticPlanGateTests(unittest.TestCase):
    def test_legacy_null_requires_human_review_for_all_task_types(self):
        context = _context(gate=None)
        state = StateRegistry.default().get("ZHONGSHU_FREEZE_CHECK")
        self.assertEqual(state.handle(context, _freeze_event()).transition.action, "PLAN_REVIEW")
        context = replace(context, request=replace(context.request, task_type="implementation"))
        self.assertEqual(state.handle(context, _freeze_event()).transition.action, "PLAN_REVIEW")


class PaginatedGateTests(PollReplyAdapterTests):
    def test_recovers_reply_and_gate_beyond_the_latest_fifty_with_dedup(self):
        decision = "task-1:human-gate:18"
        reply = {"message_id": "reply", "parent_id": "gate", "body": {"content": "approve"}}
        pages = [
            {"code": 0, "data": {"items": [{"message_id": f"noise-{i}"} for i in range(50)], "has_more": True, "page_token": "p2"}},
            {"code": 0, "data": {"items": [reply], "has_more": True, "page_token": "p3"}},
            {"code": 0, "data": {"items": [reply, {"message_id": "gate", "sender": {"sender_type": "app"},
                "body": {"content": decision}}], "has_more": False}},
        ]
        adapter = self._adapter([])
        with patch.object(adapter, "_request", side_effect=pages) as http:
            replies = adapter.poll_reply(HumanGate(decision, "task-1", "next", ""))
        self.assertEqual([reply.answer for reply in replies], ["approve"])
        self.assertIn("page_token=p2", http.call_args_list[1].args[1])


if __name__ == "__main__":
    unittest.main()
