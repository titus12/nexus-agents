"""Immutable domain events and requested workflow actions."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DomainEvent:
    """A normalized fact presented to the current workflow state."""

    name: str
    task_id: str
    sequence: int
    payload: object
    occurred_at: str


@dataclass(frozen=True)
class TransitionRequest:
    """An action selected by a state, resolved by the transition registry."""

    action: str
    reason_code: str


__all__ = ["DomainEvent", "TransitionRequest"]
