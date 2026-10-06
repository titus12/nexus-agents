"""Empty Analyst escalations must repair themselves, never park the user."""
from dataclasses import replace
from datetime import datetime, timezone
import tempfile
import unittest

from orchestrator.adapters import FakeMulticaAdapter
from orchestrator.app import OrchestratorApp
from orchestrator.contracts.analyst_gate import analyst_gate_error, INVALID_ANALYST_GATE
from orchestrator.contracts.zhongshu_analyst import CONTRACT
from orchestrator.domain.context import ReviewState
from orchestrator.domain.events import DomainEvent
from orchestrator.domain.errors import CONTRACT_REJECTED_EVENT
from orchestrator.domain.states import ZhongshuAnalystState, StateRegistry
from orchestrator.notifications import build_notification_parts
from orchestrator.structured_output import validate_role_result_shape
from test_linear_fsm_entrypoint import _ScriptedMultica, _context
from test_human_document_loop import _drive


def _analyst_context():
    context = _context()
    return replace(context, progression=replace(context.progression, state="ZHONGSHU_ANALYST", sequence=1),
                   review=ReviewState())


def _payload():
    return {"action": "HUMAN_GATE", "mode": "REQUIREMENT_CONTRACT_ONLY",
            "summary": "已从请求提取三条审查需求", "questions_for_user": [],
            "requirements": [{"requirement_id": "RQ-001", "statement": "review"}]}


def _event(context, name, payload=None):
    return DomainEvent(name, context.identity.task_id, context.progression.sequence,
                       payload or {}, datetime.now(timezone.utc))


class AnalystGateTests(unittest.TestCase):
    def test_transport_rejects_empty_and_accepts_real_question(self):
        payload = CONTRACT.example_payload()
        payload.update(action="HUMAN_GATE", mode="REQUIREMENT_CONTRACT_ONLY",
                       summary="需要确认审查范围", questions_for_user=[],
                       evidence_updates=[], finding_responses=[])
        payload["structured_output_schema_hash"] = "a" * 64
        kwargs = dict(phase="ZHONGSHU", role="review-analyst", state="ZHONGSHU_ANALYST",
                      role_mode="REQUIREMENT_CONTRACT_ONLY")
        self.assertIn(INVALID_ANALYST_GATE, validate_role_result_shape(payload, **kwargs))
        payload["questions_for_user"] = ["是否包含线上压测？"]
        self.assertEqual(validate_role_result_shape(payload, **kwargs), "")
        payload.update(action="REQUIREMENT_CONTRACT_READY", questions_for_user=[])
        self.assertEqual(validate_role_result_shape(payload, **kwargs), "")

    def test_blank_placeholders_and_absent_reason_rejected(self):
        for questions in ([], [" "], [{}], [None, False, 1], [{"question": " "}]):
            self.assertIn(INVALID_ANALYST_GATE, analyst_gate_error({**_payload(), "questions_for_user": questions}))
        self.assertTrue(analyst_gate_error({**_payload(), "summary": "", "questions_for_user": ["范围？"]}))
        self.assertEqual(analyst_gate_error({**_payload(), "summary": "", "questions_for_user": [
            {"question": "范围？", "reason": "两种范围影响边界"}]}), "")

    def test_direct_reply_retries_without_folding_rejected_requirements(self):
        context = _analyst_context()
        event = _event(context, "HUMAN_GATE", _payload())
        decision = ZhongshuAnalystState().handle(context, event)
        self.assertEqual(decision.transition.action, "RETRY")
        self.assertIsNone(decision.update.review)
        self.assertEqual(decision.update.recovery.reply_retry_count, 1)
        self.assertIn("REQUIREMENT_CONTRACT_READY", decision.update.recovery.last_failure.message)
        self.assertFalse(decision.effects)

    def test_event_action_cannot_bypass_guard_when_payload_action_absent(self):
        context = _analyst_context()
        payload = _payload()
        del payload["action"]
        decision = ZhongshuAnalystState().handle(context, _event(context, "HUMAN_GATE", payload))
        self.assertEqual(decision.transition.action, "RETRY")
        self.assertIsNone(decision.update.review)

    def test_transport_failure_does_not_hit_generic_two_failure_human_brake(self):
        context = _analyst_context()
        context = replace(context, recovery=replace(context.recovery, node_fail_state="ZHONGSHU_ANALYST",
                                                    node_fail_streak=1, reply_retry_count=1))
        event = _event(context, "FAIL", {"retryable": True,
                            "error_code": "AGENT_REPLY_CONTRACT_REJECTED",
                            "reason": analyst_gate_error(_payload())})
        decision = ZhongshuAnalystState().handle(context, event)
        self.assertEqual(decision.transition.action, "RETRY")
        self.assertEqual(decision.update.recovery.reply_retry_count, 2)

    def test_budget_exhaustion_blocks_with_diagnosis_and_no_review_mutation(self):
        context = _analyst_context()
        context = replace(context, recovery=replace(context.recovery,
                            reply_retry_count=context.recovery.max_reply_retries))
        decision = ZhongshuAnalystState().handle(context, _event(context, "HUMAN_GATE", _payload()))
        self.assertEqual(decision.transition.reason_code, "ANALYST_HUMAN_GATE_RETRIES_EXHAUSTED")
        self.assertIsNone(decision.update.review)
        self.assertFalse(decision.update.recovery.last_failure.retryable)

    def test_real_clarification_enters_gate_and_displays_question(self):
        context = _analyst_context()
        payload = {**_payload(), "questions_for_user": ["是否包含线上压测？"], "summary": "请求未说明压测范围"}
        event = _event(context, "HUMAN_GATE", payload)
        decision = ZhongshuAnalystState().handle(context, event)
        self.assertEqual(decision.transition.action, "HUMAN_GATE")
        context = replace(context, progression=replace(context.progression, state="HUMAN_GATE"))
        text = "\n".join(build_notification_parts("HUMAN_GATE", "HUMAN_GATE", event, context))
        self.assertIn("是否包含线上压测？", text)
        self.assertIn("请求未说明压测范围", text)

    def test_first_dispatch_explicitly_instructs_success_action(self):
        context = _context()
        decision = StateRegistry.default().get("REQUEST_INTAKE").handle(
            context, _event(context, "START"))
        prompt = decision.effects[0].payload["prompt_ref"]
        self.assertIn("Completed extraction MUST return action=REQUIREMENT_CONTRACT_READY", prompt)

    def test_scripted_application_repairs_once_and_reaches_plan_review(self):
        class WrongOnce(_ScriptedMultica):
            wrong = False

            def dispatch(self, request):
                if request.context.get("contract_mode") and not self.wrong:
                    self.wrong = True
                    receipt = FakeMulticaAdapter.dispatch(self, request)
                    self._reply(request, _payload())
                    return receipt
                return super().dispatch(request)

        context = _context()
        context = replace(context, parallel=replace(context.parallel, zhongshu=replace(
            context.parallel.zhongshu, plan_review_gate=True)))
        with tempfile.TemporaryDirectory() as directory:
            adapter = WrongOnce()
            app = OrchestratorApp(context, root=directory, multica=adapter, poll_interval=0)
            final = _drive(app, 160)
            self.assertEqual(final.human_gate.reason_code, "PLAN_REVIEW")
            contracts = [r for r in adapter.dispatched if r.context.get("contract_mode")]
            self.assertEqual(len(contracts), 2)
            self.assertIn(INVALID_ANALYST_GATE, contracts[1].prompt)
            self.assertFalse(any(r.target_state.startswith("MENXIA") for r in adapter.dispatched))

    def test_application_transport_rejections_exhaust_budget_without_human_gate(self):
        class AlwaysRejected(_ScriptedMultica):
            def dispatch(self, request):
                if request.context.get("contract_mode"):
                    receipt = FakeMulticaAdapter.dispatch(self, request)
                    self._reply(request, {"action": CONTRACT_REJECTED_EVENT,
                                          "contract_rejection": analyst_gate_error(_payload())})
                    return receipt
                return super().dispatch(request)

        context = _context()
        with tempfile.TemporaryDirectory() as directory:
            adapter = AlwaysRejected()
            app = OrchestratorApp(context, root=directory, multica=adapter, poll_interval=0)
            final = _drive(app, 80)
            self.assertEqual(final.progression.state, "BLOCKED")
            self.assertEqual(final.recovery.blocked_reason, "ANALYST_HUMAN_GATE_RETRIES_EXHAUSTED")
            self.assertEqual(final.recovery.reply_retry_count, context.recovery.max_reply_retries)
            self.assertEqual(len(adapter.dispatched), context.recovery.max_reply_retries + 1)
            self.assertFalse(final.review and final.review.requirements)
            self.assertFalse(final.human_gate)

    def test_wave_rejection_preserves_each_workers_feedback(self):
        context = _analyst_context()
        event = _event(context, "FAIL", {"retryable": True, "failure": {
            "error_code": "AGENT_REPLY_CONTRACT_REJECTED", "retryable": True,
            "message": "peer output was malformed", "worker_rejections": (
                ("worker-01", "peer output was malformed"),
                ("worker-02", analyst_gate_error(_payload())),
            )}})
        decision = ZhongshuAnalystState().handle(context, event)
        self.assertEqual(decision.transition.action, "RETRY")
        self.assertEqual(decision.update.recovery.last_failure.worker_rejections,
                         event.payload["failure"]["worker_rejections"])
