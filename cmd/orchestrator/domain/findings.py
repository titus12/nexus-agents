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
    "ACCEPT": "DEFERRED",
    "ACCEPTED": "DEFERRED",
    # The claim cannot be verified within this run (no authority to measure,
    # no data source).  Closing with a verification recipe is convergence,
    # not evasion: the recipe rides the resolution text as a follow-up.
    "WONT_VERIFY": "WONT_VERIFY",
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
    "WONT_VERIFY",
    # Solver disposition ledger lifecycle: the Solver rejected the finding and
    # the Critic has not settled it yet, or the settlement closed it for good.
    "REJECTED_PENDING",
    "CLOSED",
}


def normalize_finding_status(status: Any = None, decision: Any = None) -> str:
    """Return the lifecycle status of one finding observation.

    An explicit close decision wins over a bare ``status=OPEN`` echo: workers
    routinely emit the default ``OPEN`` together with ``decision=RESOLVED`` or
    ``decision=ACCEPTED_RISK``, and reading the echo would keep a finding the
    Critic just closed (or explicitly accepted) blocking the freeze forever.
    """

    raw_status = str(status or "").strip().upper()
    raw_decision = str(decision or "").strip().upper()
    mapped = _CRITIC_DECISION_STATUS.get(raw_decision)
    if raw_status in _FINDING_STATUSES:
        if raw_status == "OPEN" and mapped is not None and mapped != "OPEN":
            return mapped
        return raw_status
    return mapped or "OPEN"


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
    # Consecutive Critic rounds this finding has been re-raised unchanged.  A
    # high value means the Solver revision loop is not converging on it.
    stuck_rounds: int = 0
    resolution: str | None = None
    # Structured rejection bookkeeping from the Solver disposition ledger:
    # "absorbed" (merged into the plan), "rejected" (Solver pushed back, the
    # Critic settles it later) or "rejected-auto" (silent-round auto-close).
    disposition: str = ""
    supporting_evidence: tuple[str, ...] = ()
    verification: tuple[str, ...] = ()
    remaining_risk: str | None = None
    group_id: str = ""
    item_id: str = ""
    scope: str = ""
    related_item_ids: tuple[str, ...] = ()
    canonical_key: str = ""
    category: str = ""
    target: str = ""
    claim: str = ""
    required_action: str = ""
    impact: str = ""
    # Structured, mechanically checkable evidence demands: each entry names a
    # workspace path (and optionally a symbol) the evidence must cite.  The
    # fulfillment gate verifies existence in the mounted workspace, so the
    # demand survives any rewording of an "environment blocked me" excuse.
    evidence_targets: tuple[dict[str, str], ...] = ()

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
        for name in ("category", "target", "claim", "required_action", "impact"):
            data[name] = str(data.get(name) or "").strip()
        try:
            data["stuck_rounds"] = max(0, int(data.get("stuck_rounds") or 0))
        except (TypeError, ValueError):
            data["stuck_rounds"] = 0
        for name in ("supporting_evidence", "verification", "related_item_ids"):
            raw = data.get(name, ())
            if isinstance(raw, str) or raw is None:
                data[name] = () if raw is None else (str(raw),)
            elif isinstance(raw, (list, tuple)):
                data[name] = tuple(str(item) for item in raw)
            else:
                raise TypeError(f"finding field {name} must be an array")
        raw_targets = data.get("evidence_targets") or ()
        targets: list[dict[str, str]] = []
        if not isinstance(raw_targets, (list, tuple)):
            raise TypeError("finding field evidence_targets must be an array")
        for raw_target in raw_targets:
            if not isinstance(raw_target, Mapping) or not str(raw_target.get("path") or "").strip():
                raise ValueError("evidence target must be an object with a non-empty path")
            targets.append({
                "path": str(raw_target.get("path")).strip(),
                "symbol": str(raw_target.get("symbol") or "").strip(),
            })
        data["evidence_targets"] = tuple(targets)
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
        return self.status in {"RESOLVED", "WONT_FIX", "DEFERRED", "WONT_VERIFY"}


__all__ = ["Finding", "normalize_finding_status"]
