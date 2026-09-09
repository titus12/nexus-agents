"""Immutable aggregates that represent the workflow's durable domain state."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field

from .findings import Finding


@dataclass(frozen=True)
class TaskIdentity:
    """Stable identity shared by every workflow event and effect."""

    task_id: str
    issue_id: str
    project: str
    request_id: str


@dataclass(frozen=True)
class ProgressState:
    """Position of the main linear FSM."""

    state: str
    sequence: int
    entered_at: str
    resume_state: str | None = None


@dataclass(frozen=True)
class DeliveryState:
    """Correlation and lifecycle data for the active external dispatch."""

    active_request_id: str | None = None
    dispatch_operation_id: str | None = None
    idempotency_key: str | None = None
    status: str = "IDLE"


@dataclass(frozen=True)
class RecoveryState:
    """Retry and blocking information owned by workflow recovery."""

    retry_count: int = 0
    max_retries: int = 3
    recoverable: bool = True
    blocked_reason: str | None = None


@dataclass(frozen=True)
class ReviewState:
    """Immutable review ledger projection used by state decisions."""

    revision_id: str
    active_group_id: str | None = None
    active_item_id: str | None = None
    findings: tuple[Finding, ...] = ()


@dataclass(frozen=True)
class HumanGateState:
    """Pending human decision and the state to resume afterwards."""

    decision_id: str
    reason_code: str
    resume_state: str


@dataclass(frozen=True)
class ProgressUpdate:
    """Typed changes to the progress aggregate."""

    state: str | None = None
    resume_state: str | None = None


@dataclass(frozen=True)
class ReviewUpdate:
    """Typed changes to the review aggregate."""

    revision_id: str | None = None
    active_group_id: str | None = None
    active_item_id: str | None = None
    findings: tuple[Finding, ...] | None = None


@dataclass(frozen=True)
class DeliveryUpdate:
    """Typed changes to the delivery aggregate."""

    active_request_id: str | None = None
    status: str | None = None


@dataclass(frozen=True)
class HumanGateUpdate:
    """Typed changes to the human-gate aggregate."""

    decision_id: str | None = None
    resume_state: str | None = None
    reason_code: str | None = None


@dataclass(frozen=True)
class RecoveryUpdate:
    """Typed changes to retry and blocking state."""

    retry_count: int | None = None
    blocked_reason: str | None = None


@dataclass(frozen=True)
class WorkflowContext:
    """Complete immutable workflow snapshot composed from bounded aggregates."""

    identity: TaskIdentity
    progression: ProgressState
    delivery: DeliveryState = field(default_factory=DeliveryState)
    recovery: RecoveryState = field(default_factory=RecoveryState)
    review: ReviewState | None = None
    human_gate: HumanGateState | None = None


def context_to_dto(context: WorkflowContext) -> dict[str, object]:
    """Serialize the immutable aggregate without exposing mutable internals."""

    return {
        "identity": asdict(context.identity),
        "progression": asdict(context.progression),
        "delivery": asdict(context.delivery),
        "recovery": asdict(context.recovery),
        "review": (
            {
                **asdict(context.review),
                "findings": [asdict(finding) for finding in context.review.findings],
            }
            if context.review
            else None
        ),
        "human_gate": asdict(context.human_gate) if context.human_gate else None,
    }


def context_from_dto(value: Mapping[str, object]) -> WorkflowContext:
    """Deserialize a DTO into fresh immutable value objects."""

    identity = _context_part(value, "identity", TaskIdentity)
    progression = _context_part(value, "progression", ProgressState)
    delivery = _context_part(value, "delivery", DeliveryState)
    recovery = _context_part(value, "recovery", RecoveryState)

    review_value = value.get("review")
    review = None
    if review_value is not None and not isinstance(review_value, Mapping):
        raise ValueError("context.review must be null or an object")
    if isinstance(review_value, Mapping):
        findings_value = review_value.get("findings", [])
        if not isinstance(findings_value, list):
            raise ValueError("review.findings must be an array")
        findings = tuple(Finding(**dict(item)) for item in findings_value)
        review = ReviewState(
            revision_id=str(review_value.get("revision_id") or ""),
            active_group_id=review_value.get("active_group_id"),
            active_item_id=review_value.get("active_item_id"),
            findings=findings,
        )

    gate_value = value.get("human_gate")
    if gate_value is not None and not isinstance(gate_value, Mapping):
        raise ValueError("context.human_gate must be null or an object")
    human_gate = (
        _context_part(gate_value, "human_gate", HumanGateState)
        if isinstance(gate_value, Mapping)
        else None
    )
    return WorkflowContext(identity, progression, delivery, recovery, review, human_gate)


def _context_part(value: Mapping[str, object], name: str, cls: type):
    item = value.get(name)
    if not isinstance(item, Mapping):
        raise ValueError(f"context.{name} must be an object")
    return cls(**dict(item))


__all__ = [
    "DeliveryState",
    "DeliveryUpdate",
    "HumanGateState",
    "HumanGateUpdate",
    "ProgressState",
    "ProgressUpdate",
    "RecoveryState",
    "RecoveryUpdate",
    "ReviewState",
    "ReviewUpdate",
    "TaskIdentity",
    "WorkflowContext",
    "context_from_dto",
    "context_to_dto",
]
