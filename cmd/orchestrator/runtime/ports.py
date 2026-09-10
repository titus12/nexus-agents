"""Typed ports for one external operation at a time.

The records in this module describe transport requests and receipts only.  A
port implementation may perform I/O, but it must not advance workflow state
or mutate a workflow context.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from ..domain.events import DomainEvent
from ..transport.replies import RawTransportReply


@dataclass(frozen=True)
class AgentDispatchRequest:
    """Canonical request handed to an agent transport adapter."""

    task_id: str
    issue_id: str
    request_id: str
    agent_id: str
    role: str
    phase: str
    prompt_ref: str
    idempotency_key: str
    target_state: str = ""
    revision_id: str = ""
    plan_hash: str = ""
    sent_after: str = ""


@dataclass(frozen=True)
class PollRequest:
    task_id: str
    request_id: str
    operation_id: str


@dataclass(frozen=True)
class DispatchReceipt:
    operation_id: str
    external_message_id: str
    confirmed: bool
    request_id: str = ""


@dataclass(frozen=True)
class RemoteRunStatus:
    """Terminality observed for one externally dispatched run."""

    request_id: str
    operation_id: str | None
    status: Literal["RUNNING", "COMPLETED", "FAILED", "UNKNOWN"]
    result_ref: str | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class AdmissionKey:
    task_id: str
    phase: str
    worker_id: str
    revision_id: str
    external_target_key: str = ""


@dataclass(frozen=True)
class LeaseReceipt:
    lease_id: str
    key: AdmissionKey
    acquired_at: str
    expires_at: str


@dataclass(frozen=True)
class NotificationRequest:
    task_id: str
    notification_key: str
    body: str


@dataclass(frozen=True)
class NotificationReceipt:
    notification_key: str
    delivered: bool


@dataclass(frozen=True)
class ArtifactInput:
    task_id: str
    name: str
    content: bytes


@dataclass(frozen=True)
class ArtifactReceipt:
    artifact_id: str
    digest: str


class AgentTransportPort(Protocol):
    """One-call boundary for agent dispatch, polling, and lookup."""

    def dispatch(self, request: AgentDispatchRequest) -> DispatchReceipt: ...

    def find_existing(self, request: AgentDispatchRequest) -> DispatchReceipt | None: ...

    def poll(self, request: PollRequest) -> tuple[RawTransportReply, ...]: ...

    def lookup(self, operation_id: str) -> DispatchReceipt | None: ...

    def status(self, request: PollRequest) -> RemoteRunStatus: ...

    def recover_completed(self, request: AgentDispatchRequest) -> RawTransportReply | None:
        """Return a completed result when the remote artifact already exists, without dispatching again."""
        return None


class DomainEventInbox(Protocol):
    def next(self, task_id: str) -> DomainEvent | None: ...

    def publish(self, event: DomainEvent) -> None: ...

    def ack(self, event: DomainEvent) -> None: ...


class LockPort(Protocol):
    """Durable task-lock boundary used by the workflow engine."""

    def acquire(self, task_id: str) -> None: ...

    def refresh(self, task_id: str) -> None: ...

    def release(self, task_id: str) -> None: ...


class ConcurrencyAdmissionPort(Protocol):
    def acquire(self, key: AdmissionKey, deadline: float) -> LeaseReceipt | None: ...

    def refresh(self, lease_id: str) -> bool: ...

    def release(self, lease_id: str) -> bool: ...


class NotificationPort(Protocol):
    """One-call boundary for notification delivery."""

    def send(self, request: NotificationRequest) -> NotificationReceipt: ...


class ArtifactPort(Protocol):
    """One-call boundary for durable artifact writes."""

    def write(self, artifact: ArtifactInput) -> ArtifactReceipt: ...

    def read(self, task_id: str, artifact_id: str) -> bytes: ...


__all__ = [
    "AgentDispatchRequest",
    "AgentTransportPort",
    "AdmissionKey",
    "ArtifactInput",
    "ArtifactPort",
    "ArtifactReceipt",
    "DispatchReceipt",
    "DomainEventInbox",
    "LockPort",
    "LeaseReceipt",
    "NotificationPort",
    "NotificationReceipt",
    "NotificationRequest",
    "PollRequest",
    "RemoteRunStatus",
]
