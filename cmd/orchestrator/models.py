from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


_CRITIC_DECISION_STATUS = {
    "APPROVE": "RESOLVED",
    "RESOLVED": "RESOLVED",
    "WONT_FIX": "WONT_FIX",
    "DEFERRED": "DEFERRED",
    "ACCEPTED_RISK": "DEFERRED",
}
_FINDING_STATUSES = {
    "OPEN",
    "ASSIGNED_TO_ANALYST",
    "ASSIGNED_TO_SOLVER",
    "IN_REVIEW",
    "REOPENED",
    "RESOLVED",
    "WONT_FIX",
    "DEFERRED",
}


def normalize_finding_status(status: Any = None, decision: Any = None) -> str:
    """Normalize Critic's decision/status vocabulary into Finding state."""
    raw_status = str(status or "").strip().upper()
    if raw_status in _FINDING_STATUSES:
        return raw_status
    raw_decision = str(decision or "").strip().upper()
    return _CRITIC_DECISION_STATUS.get(raw_decision, "OPEN")


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
    # Task-level review findings are bound to the group and item that was
    # reviewed. A blank scope remains valid for legacy/global gate findings.
    # Keep these fields at the end for positional-constructor compatibility.
    group_id: str = ""
    item_id: str = ""
    scope: str = ""
    related_item_ids: list[str] = field(default_factory=list)
    canonical_key: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Finding":
        data = {
            name: value[name]
            for name in cls.__dataclass_fields__
            if name in value
        }
        data["status"] = normalize_finding_status(
            status=value.get("status"),
            decision=value.get("decision"),
        )
        data["group_id"] = str(data.get("group_id") or "").strip()
        data["item_id"] = str(data.get("item_id") or "").strip()
        data["scope"] = str(data.get("scope") or "").strip()
        related = data.get("related_item_ids")
        data["related_item_ids"] = [str(item) for item in related] if isinstance(related, list) else []
        data["canonical_key"] = str(data.get("canonical_key") or "").strip()
        return cls(**data)

    def identity_key(self) -> tuple[str, str, str]:
        """Return the durable identity, rejecting an item without a group."""
        if self.item_id and not self.group_id:
            raise ValueError("finding item_id requires group_id")
        return (self.group_id, self.item_id, self.finding_id)

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
    # When a recovery lookup reuses an older attempt, polling must retain the
    # original request correlation ID even though the current attempt has a
    # different diagnostic request ID.
    request_id: str = ""


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
