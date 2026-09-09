from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError

from orchestrator.domain.errors import ReplyBindingError, ReplyValidationError
from orchestrator.transport import (
    RawTransportReply,
    ReplyBinding,
    ReplyEnvelope,
    ReplyNormalizer,
)


class CanonicalTransportReplyTests(unittest.TestCase):
    def setUp(self):
        self.normalizer = ReplyNormalizer()
        self.binding = ReplyBinding(
            task_id="task-1",
            request_id="request-1",
            author_id="agent-1",
            phase="ZHONGSHU",
            role="ANALYST",
            target_state="ZHONGSHU_ANALYST",
        )

    def raw(self, **overrides):
        values = {
            "author_id": "agent-1",
            "external_message_id": "message-1",
            "request_id": "request-1",
            "payload": {"action": "READY_FOR_SOLVER"},
            "received_at": "2026-09-09T00:00:00Z",
            "source": "test",
        }
        values.update(overrides)
        return RawTransportReply(**values)

    def normalize(self, raw=None):
        return self.normalizer.normalize(
            raw or self.raw(),
            self.binding,
            lambda payload: {"action": payload["action"]},
        )

    def test_returns_canonical_typed_envelope_and_preserves_author(self):
        envelope = self.normalize()

        self.assertIsInstance(envelope, ReplyEnvelope)
        self.assertEqual(envelope.task_id, "task-1")
        self.assertEqual(envelope.request_id, "request-1")
        self.assertEqual(envelope.author_id, "agent-1")
        self.assertEqual(envelope.external_message_id, "message-1")
        self.assertEqual(envelope.phase, "ZHONGSHU")
        self.assertEqual(envelope.role, "ANALYST")
        self.assertEqual(envelope.target_state, "ZHONGSHU_ANALYST")
        self.assertEqual(envelope.payload, {"action": "READY_FOR_SOLVER"})

    def test_wrong_author_is_rejected(self):
        with self.assertRaises(ReplyBindingError):
            self.normalize(self.raw(author_id="agent-2"))

    def test_explicit_binding_mismatch_is_rejected(self):
        for field, value in (
            ("request_id", "request-2"),
            ("task_id", "task-2"),
            ("phase", "MENXIA"),
            ("role", "CRITIC"),
            ("target_state", "ZHONGSHU_CRITIC"),
        ):
            with self.subTest(field=field):
                with self.assertRaises(ReplyBindingError):
                    self.normalize(self.raw(**{field: value}))

    def test_payload_binding_mismatch_is_rejected(self):
        for field, value in (
            ("request_id", "request-2"),
            ("task_id", "task-2"),
            ("phase", "MENXIA"),
            ("role", "CRITIC"),
            ("target_state", "ZHONGSHU_CRITIC"),
        ):
            with self.subTest(field=field):
                with self.assertRaises(ReplyBindingError):
                    self.normalize(self.raw(payload={"action": "OK", field: value}))

    def test_missing_transport_binding_fields_are_backfilled(self):
        envelope = self.normalize(
            self.raw(
                request_id=None,
                task_id=None,
                phase=None,
                role=None,
                target_state=None,
                payload={"action": "READY_FOR_SOLVER"},
            )
        )

        self.assertEqual(envelope.task_id, "task-1")
        self.assertEqual(envelope.request_id, "request-1")
        self.assertEqual(envelope.phase, "ZHONGSHU")
        self.assertEqual(envelope.role, "ANALYST")
        self.assertEqual(envelope.target_state, "ZHONGSHU_ANALYST")

    def test_explicit_task_id_conflict_is_not_overwritten_by_backfill(self):
        with self.assertRaises(ReplyBindingError):
            self.normalize(
                self.raw(
                    request_id=None,
                    task_id=None,
                    payload={"action": "OK", "task_id": "task-2"},
                )
            )

    def test_explicit_empty_binding_is_rejected(self):
        with self.assertRaises(ReplyValidationError):
            self.normalize(self.raw(request_id=""))

        with self.assertRaises(ReplyValidationError):
            self.normalizer.normalize(
                self.raw(),
                ReplyBinding(
                    task_id="",
                    request_id="request-1",
                    author_id="agent-1",
                    phase="ZHONGSHU",
                    role="ANALYST",
                    target_state="ZHONGSHU_ANALYST",
                ),
                lambda payload: payload,
            )

    def test_conflicting_top_level_and_payload_metadata_is_rejected(self):
        with self.assertRaises(ReplyBindingError):
            self.normalize(
                self.raw(
                    request_id="request-1",
                    payload={"action": "OK", "request_id": "request-2"},
                )
            )

    def test_decoder_failure_is_wrapped_as_validation_error(self):
        def decoder(_payload):
            raise ValueError("invalid role result")

        with self.assertRaises(ReplyValidationError) as raised:
            self.normalizer.normalize(self.raw(), self.binding, decoder)

        self.assertIn("role payload decoding failed", str(raised.exception))

    def test_reply_contracts_are_immutable(self):
        raw = self.raw()
        envelope = self.normalize(raw)

        with self.assertRaises(FrozenInstanceError):
            raw.author_id = "agent-2"
        with self.assertRaises(FrozenInstanceError):
            self.binding.role = "CRITIC"
        with self.assertRaises(FrozenInstanceError):
            envelope.target_state = "DONE"
        with self.assertRaises(TypeError):
            raw.payload["action"] = "MUTATED"


if __name__ == "__main__":
    unittest.main()
