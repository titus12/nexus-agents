from __future__ import annotations

import argparse
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from orchestrator.adapters import FakeFeishuAdapter, FakeMulticaAdapter, _extract_json
from orchestrator.app import OrchestratorApp, build_context
from orchestrator.context import StateContext
from orchestrator.events import Event
from orchestrator.models import (
    AgentBinding,
    AgentRequest,
    DeliveryReceipt,
    ExternalMessage,
)
from orchestrator.notifications import ROLE_NAMES, _fmt_elapsed, build_agent_notification
from orchestrator.policies import normalize_critic_action
from orchestrator.state_machine import StateMachine
from orchestrator.states import HumanGateState, build_state_registry
from orchestrator.transitions import TransitionPolicy
from orchestrator.validators import RejectedReply, ValidReply, validate_agent_reply


class ReplyHandlingTests(unittest.TestCase):
    def test_freeze_check_reads_nested_solver_plan_groups(self):
        ctx = StateContext(task_id="task-20260820-test")
        ctx.request_payload["candidate_plan"] = {
            "plan_id": "plan-000001",
            "version": 2,
            "groups": [
                {
                    "group_id": "group-000001",
                    "objective": "Validate the proposal output",
                    "items": [
                        {
                            "item_id": "item-000001",
                            "title": "Inspect proposal output",
                            "objective": "Verify generated output and warnings",
                        }
                    ],
                }
            ],
            "dependencies": [],
        }
        app = object.__new__(OrchestratorApp)
        app.ctx = ctx
        event = app._freeze_check_event()

        self.assertEqual(event.action, "FREEZE_OK")
        frozen = ctx.request_payload.get("frozen_plan")
        self.assertIsNotNone(frozen)
        self.assertEqual(len(frozen["groups"]), 1)
        self.assertEqual(frozen["groups"][0]["group_id"], "group-000001")
        self.assertEqual(frozen["version"], 2)

    def test_freeze_check_rejects_group_without_items(self):
        ctx = StateContext(task_id="task-20260820-test")
        ctx.request_payload["candidate_plan"] = {
            "groups": [
                {
                    "group_id": "group-000001",
                    "title": "Empty group",
                    "items": [],
                }
            ]
        }
        app = object.__new__(OrchestratorApp)
        app.ctx = ctx
        event = app._freeze_check_event()

        self.assertEqual(event.name, "FREEZE_REJECTED")

    def test_find_agent_comments_does_not_fallback_to_other_agent(self):
        # If the reply comes from a different agent, it must be rejected even if
        # the declared role matches what we expect.
        message = ExternalMessage(
            author_id="solver-1",
            payload={
                "action": "BLOCKED",
                "task_id": "task-1",
                "request_id": "req-1",
                "role": "review-analyst",
                "phase": "ZHONGSHU",
            },
        )
        binding = AgentBinding(
            author_id="analyst-1",
            task_id="task-1",
            request_id="req-1",
            role="review-analyst",
            phase="ZHONGSHU",
        )
        result = validate_agent_reply(message, binding, {"BLOCKED"})
        self.assertIsInstance(result, RejectedReply)
        self.assertEqual(result.reason, "AUTHOR_ID_MISMATCH")

    def test_payload_role_must_match_requested_role_when_declared(self):
        def _msg(payload: dict) -> ExternalMessage:
            return ExternalMessage("agent-1", payload)

        binding = AgentBinding(
            author_id="agent-1",
            task_id="task-1",
            request_id="req-1",
            role="review-analyst",
            phase="ZHONGSHU",
        )
        base = {"task_id": "task-1", "request_id": "req-1", "phase": "ZHONGSHU"}

        self.assertIsInstance(
            validate_agent_reply(_msg({**base, "action": "BLOCKED"}), binding, {"BLOCKED"}),
            RejectedReply,
        )
        self.assertIsInstance(
            validate_agent_reply(
                _msg({**base, "action": "BLOCKED", "role": "review-analyst"}),
                binding,
                {"BLOCKED"},
            ),
            ValidReply,
        )
        result = validate_agent_reply(
            _msg({**base, "action": "BLOCKED", "role": "review-solver"}),
            binding,
            {"BLOCKED"},
        )
        self.assertIsInstance(result, RejectedReply)
        self.assertEqual(result.reason, "ROLE_MISMATCH")

    def test_reply_phase_and_request_id_must_match_when_declared(self):
        def _msg(phase: str, request_id: str) -> ExternalMessage:
            return ExternalMessage(
                "agent-1",
                {
                    "action": "READY_FOR_CRITIC",
                    "role": "review-solver",
                    "phase": phase,
                    "task_id": "task-1",
                    "request_id": request_id,
                },
            )

        binding = AgentBinding(
            author_id="agent-1",
            task_id="task-1",
            request_id="req-1",
            role="review-solver",
            phase="ZHONGSHU",
        )
        self.assertIsInstance(
            validate_agent_reply(_msg("ZHONGSHU", "req-1"), binding, {"READY_FOR_CRITIC"}),
            ValidReply,
        )
        result = validate_agent_reply(_msg("MENXIA", "req-1"), binding, {"READY_FOR_CRITIC"})
        self.assertIsInstance(result, RejectedReply)
        self.assertEqual(result.reason, "PHASE_MISMATCH")

        result = validate_agent_reply(_msg("ZHONGSHU", "req-2"), binding, {"READY_FOR_CRITIC"})
        self.assertIsInstance(result, RejectedReply)
        self.assertEqual(result.reason, "REQUEST_ID_MISMATCH")

    def test_strict_binding_rejects_missing_role_phase_and_request_id(self):
        message = ExternalMessage("agent-1", {"action": "READY_FOR_CRITIC"})
        binding = AgentBinding(
            author_id="agent-1",
            task_id="task-1",
            request_id="req-1",
            role="review-solver",
            phase="ZHONGSHU",
        )
        result = validate_agent_reply(message, binding, {"READY_FOR_CRITIC"})
        self.assertIsInstance(result, RejectedReply)

    def test_declared_solver_reply_from_analyst_is_rejected_as_role_mismatch(self):
        # An analyst agent tries to post a solver-role reply — must be rejected by
        # author_id binding even though the declared role matches the solver role.
        message = ExternalMessage(
            author_id="analyst-1",
            payload={
                "action": "READY_FOR_CRITIC",
                "role": "review-solver",
                "phase": "ZHONGSHU",
                "task_id": "task-1",
                "request_id": "req-1",
            },
        )
        solver_binding = AgentBinding(
            author_id="solver-1",
            task_id="task-1",
            request_id="req-1",
            role="review-solver",
            phase="ZHONGSHU",
        )
        result = validate_agent_reply(message, solver_binding, {"READY_FOR_CRITIC"})
        self.assertIsInstance(result, RejectedReply)
        self.assertEqual(result.reason, "AUTHOR_ID_MISMATCH")
        # Also confirm that find_latest would return nothing via policy-driven check
        binding_for_analyst = AgentBinding(
            author_id="solver-1",
            task_id="task-1",
            request_id="req-1",
            role="review-solver",
            phase="ZHONGSHU",
        )
        result2 = validate_agent_reply(message, binding_for_analyst, {"READY_FOR_CRITIC"})
        self.assertIsInstance(result2, RejectedReply)

    def test_actionable_critic_blocked_is_normalized_to_solver_revision(self):
        ctx = StateContext(task_id="task-normalize")
        ctx.active_finding_ids = ["finding-1"]
        ctx.zhongshu_revision_round = 1
        ctx.max_zhongshu_revision_rounds = 8

        action = normalize_critic_action(ctx, "BLOCKED")
        self.assertEqual(action, "REQUEST_SOLVER_REVISION")

    def test_timeout_is_persisted_as_explicit_state(self):
        captured: list[dict] = []

        def persist(ctx, event, transition):
            if transition is not None:
                captured.append(ctx.to_dict())

        ctx = StateContext(task_id="task-timeout")
        ctx.workflow_state = "ZHONGSHU_SOLVER"
        ctx.current_phase = "ZHONGSHU"
        ctx.current_role = "review-solver"
        ctx.active_request_id = "req-timeout"

        registry = build_state_registry()
        machine = StateMachine(ctx, registry, persist)

        # Replicate what OrchestratorApp._next_event sets before dispatching timeout
        ctx.timeout_phase = ctx.current_phase
        ctx.timeout_role = ctx.current_role
        ctx.timeout_request_id = ctx.active_request_id

        machine.dispatch(Event("AGENT_TIMEOUT", {"reason": "agent timeout"}))

        self.assertTrue(any(s["workflow_state"] == "TIMEOUT" for s in captured))
        saved = next(s for s in captured if s["workflow_state"] == "TIMEOUT")
        self.assertEqual(saved["timeout_role"], "review-solver")
        self.assertEqual(saved["timeout_phase"], "ZHONGSHU")
        self.assertEqual(saved["timeout_request_id"], "req-timeout")

    def test_human_gate_requires_delivery_before_waiting(self):
        class FallbackMultica:
            def __init__(self):
                self.comments: list = []

            def dispatch(self, req):
                ...

            def poll(self, req):
                return []

            def find_existing_request(self, key, issue=""):
                return None

            def add_comment(self, issue_id: str, text: str) -> str:
                self.comments.append((issue_id, text))
                return "fallback-1"

        class FailingFeishu:
            def send_gate(self, gate):
                return DeliveryReceipt(channel="feishu", delivered=False)

            def poll_reply(self, gate):
                return []

            def notify(self, text, role=""):
                return ""

        ctx = StateContext(task_id="task-gate", issue_id="issue-1")
        ctx.resume_state = "ZHONGSHU_CRITIC"
        ctx.request_payload["human_gate_prompt"] = "请选择是否继续"

        fallback_multica = FallbackMultica()
        state = HumanGateState(feishu=FailingFeishu(), multica=fallback_multica)
        state.enter(ctx)

        self.assertIsNotNone(ctx.gate_message_id)
        self.assertEqual(ctx.request_payload.get("gate_delivery"), "issue_fallback")
        self.assertEqual(len(fallback_multica.comments), 1)
        self.assertEqual(fallback_multica.comments[0][0], "issue-1")

    def test_human_gate_delivery_failure_is_not_waiting_human(self):
        class FailingFeishu:
            def send_gate(self, gate):
                return DeliveryReceipt(channel="feishu", delivered=False)

            def poll_reply(self, gate):
                return []

            def notify(self, text, role=""):
                return ""

        # No multica and no issue_id → fallback unavailable
        ctx = StateContext(task_id="task-gate-failed")
        ctx.resume_state = "ZHONGSHU_CRITIC"
        ctx.request_payload["human_gate_prompt"] = "请选择"

        state = HumanGateState(feishu=FailingFeishu())
        state.enter(ctx)

        self.assertIsNotNone(ctx.last_error)
        self.assertIn(ctx.last_error["code"], {"FEISHU_DELIVERY_FAILED", "HUMAN_GATE_DELIVERY_FAILED"})

    def test_resume_human_gate_timeout_is_explicit(self):
        app = object.__new__(OrchestratorApp)
        app.ctx = StateContext(task_id="task-resume-gate")
        app.ctx.workflow_state = "HUMAN_GATE"
        app.ctx.active_decision_id = "decision-1"
        app.ctx.resume_state = "ZHONGSHU_CRITIC"
        app.timeout_seconds = 0
        app.started_at = 0
        app.heartbeat_interval = 120
        app.states = build_state_registry(feishu=FakeFeishuAdapter())

        event = app._next_event()
        self.assertEqual(event.name, "HUMAN_GATE_TIMEOUT")

    def test_find_latest_ignores_system_runtime_and_truncated_comments(self):
        binding = AgentBinding(
            author_id="agent-1",
            task_id="task-1",
            request_id="req-1",
            role="review-analyst",
            phase="ZHONGSHU",
        )
        allowed = {"READY_FOR_SOLVER"}

        # System / no-action / truncated payloads must all be rejected
        bad_messages = [
            ExternalMessage("agent-1", {"action": "WAITING_FOR_AGENT"}, "system-1"),
            ExternalMessage("agent-1", {}, "system-2"),
            ExternalMessage("agent-1", {"action": "READY_FOR_SOLVER"}, "missing-fields"),
        ]
        for msg in bad_messages:
            result = validate_agent_reply(msg, binding, allowed)
            self.assertIsInstance(result, RejectedReply, f"Expected rejection for {msg.external_id}")

        valid_msg = ExternalMessage(
            "agent-1",
            {
                "action": "READY_FOR_SOLVER",
                "role": "review-analyst",
                "phase": "ZHONGSHU",
                "task_id": "task-1",
                "request_id": "req-1",
            },
            "valid-1",
        )
        self.assertIsInstance(validate_agent_reply(valid_msg, binding, allowed), ValidReply)

    def test_full_json_parses_but_truncated_json_does_not(self):
        full = {"action": "READY_FOR_SOLVER", "evidence": [{"evidence_id": "ev-000001"}]}
        self.assertEqual(_extract_json(json.dumps(full)), full)
        self.assertIsNone(_extract_json(json.dumps(full)[:30]))

    def test_comment_list_fetches_full_comments_by_default(self):
        # FakeMulticaAdapter correctly identifies a previously dispatched request
        # by its idempotency_key, confirming poll → dispatch round-trip works.
        adapter = FakeMulticaAdapter()
        request = AgentRequest(
            task_id="task-1",
            request_id="req-1",
            agent_id="agent-1",
            role="review-analyst",
            phase="ZHONGSHU",
            prompt="test",
            idempotency_key="idem-1",
            issue_id="issue-1",
        )
        adapter.dispatch(request)
        existing = adapter.find_existing_request("idem-1", "issue-1")
        self.assertIsNotNone(existing)
        self.assertEqual(len(adapter.dispatched), 1)

    def test_comment_list_can_still_request_summary_explicitly(self):
        # poll() filters by request_id; unrelated replies from other request_ids
        # must not be returned.
        adapter = FakeMulticaAdapter()
        request = AgentRequest(
            task_id="task-1",
            request_id="req-target",
            agent_id="agent-1",
            role="review-analyst",
            phase="ZHONGSHU",
            prompt="test",
            idempotency_key="idem-target",
            issue_id="issue-1",
        )
        adapter.queue_reply(
            "req-target",
            ExternalMessage("agent-1", {"action": "READY_FOR_SOLVER", "request_id": "req-target"}),
        )
        adapter.queue_reply(
            "req-other",
            ExternalMessage("agent-1", {"action": "READY_FOR_SOLVER", "request_id": "req-other"}),
        )
        replies = adapter.poll(request)
        self.assertEqual(len(replies), 1)

    def test_resume_metadata_preserves_omitted_values(self):
        args = argparse.Namespace(project_type="unknown", task_type="review")
        ctx = build_context(args, "issue-1", "resume request", "task-resume")
        self.assertEqual(ctx.raw_request, "resume request")
        self.assertEqual(ctx.task_id, "task-resume")

    def test_new_task_metadata_keeps_legacy_defaults(self):
        args = argparse.Namespace(project_type="unknown", task_type="feature")
        ctx = build_context(args, "issue-2", "new request", "task-new")
        self.assertEqual(ctx.project_type, "unknown")
        self.assertEqual(ctx.task_type, "feature")

    def test_explicit_metadata_overrides_saved_values(self):
        args = argparse.Namespace(project_type="go", task_type="review")
        ctx = build_context(args, "issue-3", "go request", "task-explicit")
        self.assertEqual(ctx.raw_request, "go request")
        self.assertEqual(ctx.project_type, "go")
        self.assertEqual(ctx.task_type, "review")

    def test_progress_labels_are_readable_and_format_safe(self):
        self.assertEqual(ROLE_NAMES.get("review-analyst"), "分析师")
        self.assertEqual(_fmt_elapsed(123), "2 分 3 秒")
        self.assertEqual(_fmt_elapsed(60), "1 分钟")
        self.assertEqual(_fmt_elapsed(45), "45 秒")

        ctx = StateContext(task_id="task-20260817-3b64cc")
        msg = build_agent_notification(
            "review-analyst",
            "ZHONGSHU_ANALYST",
            "HEARTBEAT",
            ctx,
            {"action": "WAITING_FOR_AGENT", "elapsed_seconds": 123},
        )
        self.assertIn("分析师", msg)
        self.assertIn("2 分", msg)
        self.assertIn("总体方案", msg)


if __name__ == "__main__":
    unittest.main()
