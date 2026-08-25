from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Finding:
    finding_id: str
    severity: str
    status: str = "OPEN"
    owner_role: str = ""
    source_phase: str = ""
    current_phase: str = ""
    parent_finding_id: str | None = None
    revision_round: int = 0
    resolution: str | None = None
    supporting_evidence: list[str] = field(default_factory=list)
    verification: list[str] = field(default_factory=list)
    remaining_risk: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Finding":
        return cls(**{name: value[name] for name in cls.__dataclass_fields__ if name in value})

    @property
    def active(self) -> bool:
        return self.status in {"OPEN", "ASSIGNED_TO_ANALYST", "ASSIGNED_TO_SOLVER", "IN_REVIEW", "REOPENED"}

    @property
    def resolved(self) -> bool:
        return self.status in {"RESOLVED", "WONT_FIX", "DEFERRED"}


@dataclass(frozen=True)
class AgentBinding:
    author_id: str
    task_id: str
    request_id: str
    role: str
    phase: str


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


@dataclass(frozen=True)
class DispatchReceipt:
    operation_id: str
    external_message_id: str = ""
    confirmed: bool = True


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
