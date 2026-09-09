"""Immutable transport reply contracts.

This module is deliberately limited to data contracts.  It does not know how
messages are fetched, dispatched, or persisted.  Transport adapters may use
``RawTransportReply`` while the workflow consumes ``ReplyEnvelope``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Generic, TypeVar


TPayload = TypeVar("TPayload")


@dataclass(frozen=True)
class RawTransportReply:
    """A complete reply as observed at the transport boundary.

    ``author_id`` is the identity reported by the transport and must remain
    authoritative.  It must never be replaced with the identity expected by a
    request.  The optional binding fields support legacy transports that omit
    some metadata; the normalizer is the only place allowed to backfill them.
    """

    author_id: str
    external_message_id: str
    request_id: str | None
    payload: Mapping[str, object]
    received_at: str
    source: str
    task_id: str | None = None
    phase: str | None = None
    role: str | None = None
    target_state: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.payload, Mapping):
            raise TypeError("payload must be a Mapping[str, object]")
        # A frozen dataclass protects attributes, but not a mutable mapping
        # supplied by a caller.  Copy it at the boundary so the raw reply is
        # a stable observation of the external message.
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


@dataclass(frozen=True)
class ReplyBinding:
    """The request binding against which one transport reply is checked."""

    task_id: str
    request_id: str
    author_id: str
    phase: str
    role: str
    target_state: str


@dataclass(frozen=True)
class ReplyEnvelope(Generic[TPayload]):
    """A validated reply carrying a role-specific typed payload."""

    task_id: str
    request_id: str
    author_id: str
    external_message_id: str
    phase: str
    role: str
    target_state: str
    payload: TPayload
    received_at: str
    source: str


__all__ = [
    "RawTransportReply",
    "ReplyBinding",
    "ReplyEnvelope",
    "TPayload",
]
