"""Immutable finding value objects used by the review aggregate."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from typing import Any, Iterable, Mapping


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


# A finding whose owner is only named inside the free-text ``target`` (the
# critic contract historically omitted ``item_id``) must still resolve to the
# exact plan item id.  Anything parsed from ``target`` is matched as a whole
# token: a truncated fragment like ``item-000004.acceptance_signals and
# task_review_ledger`` is not an item id and must never be mistaken for one.
_ITEM_ID_PATTERN = re.compile(r"\b(item-[A-Za-z0-9][A-Za-z0-9_-]*)\b")
_GROUP_ID_PATTERN = re.compile(r"\b(group-[A-Za-z0-9][A-Za-z0-9_-]*)\b")


def _finding_field(finding: object, name: str) -> str:
    if isinstance(finding, Mapping):
        value = finding.get(name)
    else:
        value = getattr(finding, name, None)
    return str(value or "").strip()


def _is_clean_token(value: str) -> bool:
    """True for a bare id token, False for a truncated free-text fragment."""

    return bool(value) and not any(ch.isspace() or ch in "/" for ch in value)


def resolve_finding_item_id(
    finding: object,
    known_item_ids: Iterable[str] | None = None,
) -> str:
    """Return the plan item id a finding owns.

    Prefers the explicit ``item_id`` field; falls back to extracting an
    ``item-...`` token from the free-text ``target``.  When ``known_item_ids``
    is supplied, candidates are intersected with it so a target that mentions
    several ids resolves to the one the plan actually owns.  The historical
    ``target.split("/")[-1]`` fallback returned garbage like
    ``item-000004.acceptance_signals and task_review_ledger``, which silently
    excluded the item from scoped solver revisions (live incident
    task-20260926-35833d).
    """

    explicit = _finding_field(finding, "item_id")
    if _is_clean_token(explicit):
        return explicit
    target = _finding_field(finding, "target")
    candidates = list(dict.fromkeys(_ITEM_ID_PATTERN.findall(target)))
    if known_item_ids is not None:
        known = {str(value).strip() for value in known_item_ids if str(value).strip()}
        for candidate in candidates:
            if candidate in known:
                return candidate
    if candidates:
        return candidates[0]
    return ""


def resolve_finding_group_id(finding: object) -> str:
    """Return the group id named by the finding, deriving it from ``target``."""

    explicit = _finding_field(finding, "group_id")
    if _is_clean_token(explicit):
        return explicit
    match = _GROUP_ID_PATTERN.search(_finding_field(finding, "target"))
    return match.group(1) if match else ""


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
        data["group_id"] = resolve_finding_group_id(data)
        data["item_id"] = resolve_finding_item_id(data)
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


__all__ = [
    "Finding",
    "normalize_finding_status",
    "resolve_finding_group_id",
    "resolve_finding_item_id",
]
