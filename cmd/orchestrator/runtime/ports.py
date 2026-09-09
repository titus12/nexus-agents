"""Typed ports for one external operation at a time.

The records in this module describe transport requests and receipts only.  A
port implementation may perform I/O, but it must not advance workflow state
or mutate a workflow context.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..transport.replies import RawTransportReply


@dataclass(frozen=True)
class AgentRequest:
    task_id: str
    request_id: str
    agent_id: str
    payload_ref: str


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

    def dispatch(self, request: AgentRequest) -> DispatchReceipt: ...

    def poll(self, request: PollRequest) -> tuple[RawTransportReply, ...]: ...

    def lookup(self, operation_id: str) -> DispatchReceipt | None: ...


class NotificationPort(Protocol):
    """One-call boundary for notification delivery."""

    def send(self, request: NotificationRequest) -> NotificationReceipt: ...


class ArtifactPort(Protocol):
    """One-call boundary for durable artifact writes."""

    def write(self, artifact: ArtifactInput) -> ArtifactReceipt: ...


__all__ = [
    "AgentRequest",
    "AgentTransportPort",
    "ArtifactInput",
    "ArtifactPort",
    "ArtifactReceipt",
    "DispatchReceipt",
    "NotificationPort",
    "NotificationReceipt",
    "NotificationRequest",
    "PollRequest",
]
