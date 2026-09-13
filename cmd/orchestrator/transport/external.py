"""External transport envelopes kept at the adapter boundary.

These records describe the existing Multica/Feishu protocols. They are not
workflow state and must not be imported by the domain or runtime reducer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AgentBinding:
    author_id: str
    task_id: str
    request_id: str
    role: str
    phase: str
    target_state: str = ""
    target_role: str = ""


@dataclass(frozen=True)
class AgentRequest:
    task_id: str
    request_id: str
    agent_id: str
    role: str
    phase: str
    prompt: str
    idempotency_key: str
    issue_id: str = ""
    sent_after: str = ""
    dispatch_external_message_id: str = ""
    context: dict[str, Any] = field(default_factory=dict)
    target_state: str = ""
    target_role: str = ""
    structured_output: dict[str, Any] | None = None


@dataclass(frozen=True)
class DispatchReceipt:
    operation_id: str
    external_message_id: str = ""
    confirmed: bool = True
    request_id: str = ""
    issue_id: str = ""


@dataclass(frozen=True)
class ExternalMessage:
    author_id: str
    payload: dict[str, Any]
    external_id: str = ""
    raw_content: str = ""


@dataclass(frozen=True)
class DeliveryReceipt:
    channel: str
    message_id: str = ""
    delivered: bool = True


@dataclass(frozen=True)
class HumanGate:
    decision_id: str
    task_id: str
    resume_state: str
    prompt: str
    message_id: str = ""


@dataclass(frozen=True)
class HumanReply:
    decision_id: str
    answer: str
    author_id: str = ""


__all__ = [
    "AgentBinding",
    "AgentRequest",
    "DeliveryReceipt",
    "DispatchReceipt",
    "ExternalMessage",
    "HumanGate",
    "HumanReply",
]
