"""Immutable domain events and requested workflow actions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class DomainEvent:
    """A normalized fact presented to the current workflow state."""

    name: str
    task_id: str
    sequence: int
    payload: object
    occurred_at: str
    event_id: str = ""

    def __post_init__(self) -> None:
        if not self.name or not self.task_id:
            raise ValueError("domain event name and task_id are required")
        if self.sequence < 0:
            raise ValueError("domain event sequence must be non-negative")

    @property
    def payload_mapping(self) -> Mapping[str, object]:
        if not isinstance(self.payload, Mapping):
            raise TypeError("domain event payload must be a mapping")
        return self.payload


@dataclass(frozen=True)
class TransitionRequest:
    """An action selected by a state, resolved by the transition registry."""

    action: str
    reason_code: str


__all__ = ["DomainEvent", "TransitionRequest"]
