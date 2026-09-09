"""Typed failures with enough correlation data for precise attribution."""

from __future__ import annotations

from dataclasses import dataclass
import uuid


@dataclass(frozen=True)
class FailureRecord:
    """Durable description of one failure at one workflow boundary."""

    failure_id: str
    stage: str
    owner_component: str
    task_id: str
    state: str
    sequence: int
    node_run_id: str | None
    worker_id: str | None
    effect_id: str | None
    error_code: str
    retryable: bool
    message: str
    cause_type: str
    external_id: str | None = None

    @classmethod
    def from_effect_exception(cls, effect: object, error: Exception) -> "FailureRecord":
        request = getattr(effect, "request", None)
        effect_type = str(
            getattr(request, "effect_type", None)
            or getattr(effect, "effect_type", None)
            or "unknown_effect"
        )
        return cls(
            failure_id=uuid.uuid4().hex,
            stage="effect",
            owner_component=effect_type,
            task_id=str(getattr(effect, "task_id", "")),
            state=str(getattr(effect, "state", "")),
            sequence=int(getattr(effect, "sequence", 0) or 0),
            node_run_id=getattr(effect, "node_run_id", None),
            worker_id=getattr(effect, "worker_id", None),
            effect_id=getattr(effect, "effect_id", None),
            error_code=str(getattr(error, "error_code", type(error).__name__.upper())),
            retryable=bool(getattr(error, "retryable", False)),
            message=str(error),
            cause_type=type(error).__name__,
            external_id=getattr(effect, "external_id", None),
        )


class DomainError(Exception):
    """Base class for failures that may carry a durable failure record."""

    error_code = "DOMAIN_ERROR"

    def __init__(self, message: str = "", failure: FailureRecord | None = None):
        super().__init__(message)
        self.failure = failure


class TransportError(DomainError):
    error_code = "TRANSPORT_ERROR"


class ReplyBindingError(DomainError):
    error_code = "REPLY_BINDING_ERROR"


class ReplyValidationError(DomainError):
    error_code = "REPLY_VALIDATION_ERROR"


class WorkerTimeoutError(DomainError):
    error_code = "WORKER_TIMEOUT"


class LeaseLostError(DomainError):
    error_code = "LEASE_LOST"


class PostCommitLeaseReleaseError(LeaseLostError):
    """The transition is durable, but ownership cleanup failed afterwards."""

    error_code = "POST_COMMIT_LEASE_RELEASE_FAILED"

    def __init__(
        self,
        *,
        task_id: str,
        transition_id: str,
        state: str,
        sequence: int,
        cause: BaseException,
    ) -> None:
        self.task_id = task_id
        self.transition_id = transition_id
        self.state = state
        self.sequence = sequence
        self.committed = True
        failure = FailureRecord(
            failure_id=uuid.uuid4().hex,
            stage="lock",
            owner_component="workflow_engine",
            task_id=task_id,
            state=state,
            sequence=sequence,
            node_run_id=None,
            worker_id=None,
            effect_id=None,
            error_code=self.error_code,
            retryable=False,
            message=str(cause),
            cause_type=type(cause).__name__,
        )
        super().__init__(
            f"transition {transition_id!r} committed, but lock release failed: {cause}",
            failure=failure,
        )


class PersistenceError(DomainError):
    error_code = "PERSISTENCE_ERROR"


class InvariantViolation(DomainError):
    error_code = "INVARIANT_VIOLATION"


class HumanGateDeliveryError(DomainError):
    error_code = "HUMAN_GATE_DELIVERY_ERROR"


__all__ = [
    "DomainError",
    "FailureRecord",
    "HumanGateDeliveryError",
    "InvariantViolation",
    "LeaseLostError",
    "PostCommitLeaseReleaseError",
    "PersistenceError",
    "ReplyBindingError",
    "ReplyValidationError",
    "TransportError",
    "WorkerTimeoutError",
]
