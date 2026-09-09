from __future__ import annotations

import unittest

from orchestrator.domain.errors import ReplyBindingError
from orchestrator.models import AgentBinding, ExternalMessage
from orchestrator.transport import ReplyEnvelope
from orchestrator.validators import (
    RejectedReply,
    ValidReply,
    normalize_agent_reply,
    validate_agent_reply,
)


class CanonicalDirectTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.binding = AgentBinding(
            author_id="agent-1",
            task_id="task-1",
            request_id="request-1",
            role="review-analyst",
            phase="ZHONGSHU",
            target_state="ZHONGSHU_ANALYST",
        )

    def message(self, payload: dict[str, object] | None = None) -> ExternalMessage:
        return ExternalMessage(
            author_id="agent-1",
            external_id="message-1",
            payload=payload
            or {
                "task_id": "task-1",
                "request_id": "request-1",
                "phase": "ZHONGSHU",
                "role": "review-analyst",
                "target_state": "ZHONGSHU_ANALYST",
                "action": "READY_FOR_SOLVER",
            },
        )

    def test_normalizes_direct_reply_and_preserves_transport_identity(self) -> None:
        envelope = normalize_agent_reply(self.message(), self.binding)

        self.assertIsInstance(envelope, ReplyEnvelope)
        self.assertEqual(envelope.author_id, "agent-1")
        self.assertEqual(envelope.external_message_id, "message-1")
        self.assertEqual(envelope.task_id, "task-1")
        self.assertEqual(envelope.request_id, "request-1")
        self.assertEqual(envelope.target_state, "ZHONGSHU_ANALYST")
        self.assertEqual(envelope.payload["action"], "READY_FOR_SOLVER")

    def test_wrong_actual_author_is_rejected(self) -> None:
        message = ExternalMessage(
            author_id="other-agent",
            external_id="message-1",
            payload=self.message().payload,
        )

        with self.assertRaises(ReplyBindingError):
            normalize_agent_reply(message, self.binding)

    def test_explicit_binding_mismatches_are_rejected(self) -> None:
        for field, value in (
            ("task_id", "task-2"),
            ("request_id", "request-2"),
            ("phase", "MENXIA"),
            ("role", "review-critic"),
            ("target_state", "ZHONGSHU_CRITIC"),
        ):
            with self.subTest(field=field):
                payload = dict(self.message().payload)
                payload[field] = value
                with self.assertRaises(ReplyBindingError):
                    normalize_agent_reply(self.message(payload), self.binding)

    def test_missing_binding_fields_are_backfilled_by_canonical_normalizer(self) -> None:
        payload = {"action": "READY_FOR_SOLVER"}

        envelope = normalize_agent_reply(self.message(payload), self.binding)

        self.assertEqual(envelope.task_id, "task-1")
        self.assertEqual(envelope.request_id, "request-1")
        self.assertEqual(envelope.phase, "ZHONGSHU")
        self.assertEqual(envelope.role, "review-analyst")
        self.assertEqual(envelope.target_state, "ZHONGSHU_ANALYST")


class LegacyDirectValidatorCompatibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.binding = AgentBinding(
            author_id="agent-1",
            task_id="task-1",
            request_id="request-1",
            role="review-analyst",
            phase="ZHONGSHU",
            target_state="ZHONGSHU_ANALYST",
        )

    def test_legacy_missing_binding_semantics_remain_unchanged(self) -> None:
        result = validate_agent_reply(
            ExternalMessage("agent-1", {"action": "READY_FOR_SOLVER"}),
            self.binding,
            {"READY_FOR_SOLVER"},
        )

        self.assertIsInstance(result, RejectedReply)
        self.assertEqual(result.reason, "TASK_ID_MISMATCH")

    def test_legacy_role_and_phase_backfill_remain_unchanged(self) -> None:
        result = validate_agent_reply(
            ExternalMessage(
                "agent-1",
                {
                    "task_id": "task-1",
                    "request_id": "request-1",
                    "action": "READY_FOR_SOLVER",
                },
            ),
            self.binding,
            {"READY_FOR_SOLVER"},
        )

        self.assertIsInstance(result, ValidReply)
        self.assertEqual(result.payload["role"], "review-analyst")
        self.assertEqual(result.payload["phase"], "ZHONGSHU")

    def test_legacy_explicit_mismatch_reasons_remain_unchanged(self) -> None:
        cases = (
            ("task_id", "task-2", "TASK_ID_MISMATCH"),
            ("request_id", "request-2", "REQUEST_ID_MISMATCH"),
            ("phase", "MENXIA", "PHASE_MISMATCH"),
            ("role", "review-critic", "ROLE_MISMATCH"),
            ("action", "UNKNOWN", "INVALID_ACTION"),
        )
        base = {
            "task_id": "task-1",
            "request_id": "request-1",
            "phase": "ZHONGSHU",
            "role": "review-analyst",
            "action": "READY_FOR_SOLVER",
        }

        for field, value, reason in cases:
            with self.subTest(field=field):
                payload = dict(base)
                payload[field] = value
                result = validate_agent_reply(
                    ExternalMessage("agent-1", payload),
                    self.binding,
                    {"READY_FOR_SOLVER"},
                )
                self.assertIsInstance(result, RejectedReply)
                self.assertEqual(result.reason, reason)


if __name__ == "__main__":
    unittest.main()
