from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import AgentBinding, ExternalMessage


@dataclass(frozen=True)
class ValidReply:
    payload: dict[str, Any]
    external_id: str = ""


@dataclass(frozen=True)
class RejectedReply:
    reason: str
    payload: dict[str, Any]
    external_id: str = ""


def validate_agent_reply(
    message: ExternalMessage,
    expected: AgentBinding,
    allowed_actions: set[str],
) -> ValidReply | RejectedReply:
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
