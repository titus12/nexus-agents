"""Immutable finding value objects used by the review aggregate."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping


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
    raw_status = str(status or "").strip().upper()
    if raw_status in _FINDING_STATUSES:
        return raw_status
    raw_decision = str(decision or "").strip().upper()
    return _CRITIC_DECISION_STATUS.get(raw_decision, "OPEN")


@dataclass(frozen=True)
class Finding:
    """A scoped review finding with immutable identity and lifecycle fields."""

    finding_id: str
    severity: str
    status: str = "OPEN"
    owner_role: str = ""
    source_phase: str = ""
    current_phase: str = ""
    parent_finding_id: str | None = None
    revision_round: int = 0
    resolution: str | None = None
    supporting_evidence: tuple[str, ...] = ()
    verification: tuple[str, ...] = ()
    remaining_risk: str | None = None
    group_id: str = ""
    item_id: str = ""
    scope: str = ""
    related_item_ids: tuple[str, ...] = ()
    canonical_key: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Finding":
        if not isinstance(value, Mapping):
            raise TypeError("finding must be an object")
        data = dict(value)
        data["finding_id"] = str(data.get("finding_id") or "").strip()
        data["severity"] = str(data.get("severity") or "").strip()
        data["status"] = normalize_finding_status(
            status=data.get("status"),
            decision=data.get("decision"),
        )
        data["group_id"] = str(data.get("group_id") or "").strip()
        data["item_id"] = str(data.get("item_id") or "").strip()
        data["scope"] = str(data.get("scope") or "").strip()
        for name in ("supporting_evidence", "verification", "related_item_ids"):
            raw = data.get(name, ())
            if isinstance(raw, str) or raw is None:
                data[name] = () if raw is None else (str(raw),)
            elif isinstance(raw, (list, tuple)):
                data[name] = tuple(str(item) for item in raw)
            else:
                raise TypeError(f"finding field {name} must be an array")
        allowed = set(cls.__dataclass_fields__)
        data = {name: data[name] for name in allowed if name in data}
        if not data["finding_id"]:
            raise ValueError("finding_id is required")
        if not data["severity"]:
            raise ValueError("finding severity is required")
        return cls(**data)

    def identity_key(self) -> tuple[str, str, str]:
        if self.item_id and not self.group_id:
            raise ValueError("finding item_id requires group_id")
        return self.group_id, self.item_id, self.finding_id

    @property
    def active(self) -> bool:
        return self.status in {
            "OPEN",
            "ASSIGNED_TO_ANALYST",
            "ASSIGNED_TO_SOLVER",
            "IN_REVIEW",
            "REOPENED",
        }

    @property
    def resolved(self) -> bool:
        return self.status in {"RESOLVED", "WONT_FIX", "DEFERRED"}


__all__ = ["Finding", "normalize_finding_status"]
