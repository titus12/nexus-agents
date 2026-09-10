from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .transport.external import AgentBinding, ExternalMessage
from .transport import RawTransportReply, ReplyBinding, ReplyEnvelope, ReplyNormalizer


@dataclass(frozen=True)
class ValidReply:
    payload: dict[str, Any]
    external_id: str = ""


@dataclass(frozen=True)
class RejectedReply:
    reason: str
    payload: dict[str, Any]
    external_id: str = ""


def normalize_agent_reply(
    message: ExternalMessage,
    expected: AgentBinding,
) -> ReplyEnvelope[dict[str, object]]:
    """Normalize one direct reply into the canonical transport envelope.

    This is the direct-path adapter.  It deliberately delegates all author and
    binding checks to :class:`ReplyNormalizer`; the only direct-path concern is
    translating the legacy ``ExternalMessage`` shape and copying its payload.
    The legacy ``validate_agent_reply`` function below remains available for
    existing callers and must not be extended with new transport semantics.
    """

    raw = RawTransportReply(
        author_id=message.author_id,
        external_message_id=message.external_id,
        request_id=None,
        payload=message.payload,
        received_at="",
        source="direct",
    )
    binding = ReplyBinding(
        task_id=expected.task_id,
        request_id=expected.request_id,
        author_id=expected.author_id,
        phase=expected.phase,
        role=expected.role,
        target_state=expected.target_state,
    )

    def copy_payload(payload: Mapping[str, object]) -> dict[str, object]:
        return dict(payload)

    return ReplyNormalizer().normalize(raw, binding, copy_payload)


def validate_agent_reply(
    message: ExternalMessage,
    expected: AgentBinding,
    allowed_actions: set[str],
) -> ValidReply | RejectedReply:
    """Legacy compatibility validator for existing direct-path callers.

    New code must use :func:`normalize_agent_reply`.  The return type and
    reason precedence here are intentionally frozen for compatibility with the
    existing orchestrator and its tests.
    """

    payload = message.payload
    if message.author_id != expected.author_id:
        return RejectedReply("AUTHOR_ID_MISMATCH", payload, message.external_id)
    if str(payload.get("task_id", "")) != expected.task_id:
        return RejectedReply("TASK_ID_MISMATCH", payload, message.external_id)
    if str(payload.get("request_id", "")) != expected.request_id:
        return RejectedReply("REQUEST_ID_MISMATCH", payload, message.external_id)
    target_state = str(payload.get("target_state") or "")
    if target_state and expected.target_state and target_state != expected.target_state:
        return RejectedReply("TARGET_STATE_MISMATCH", payload, message.external_id)
    target_role = str(payload.get("target_role") or "")
    if target_role and expected.target_role and target_role != expected.target_role:
        return RejectedReply("TARGET_ROLE_MISMATCH", payload, message.external_id)
    normalized = dict(payload)
    # Missing role/phase may be inferred only after transport correlation.
    if not payload.get("role"):
        normalized["role"] = expected.role
        normalized["binding_normalized"] = True
    elif str(payload.get("role")) != expected.role:
        return RejectedReply("ROLE_MISMATCH", payload, message.external_id)
    if not payload.get("phase"):
        normalized["phase"] = expected.phase
        normalized["binding_normalized"] = True
    elif str(payload.get("phase")) != expected.phase:
        return RejectedReply("PHASE_MISMATCH", payload, message.external_id)
    action = str(payload.get("action", ""))
    if action not in allowed_actions:
        return RejectedReply("INVALID_ACTION", payload, message.external_id)
    return ValidReply(normalized, message.external_id)
