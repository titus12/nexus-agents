"""Durable event input and effect-result normalization for the new runtime."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Protocol

from ..domain.events import DomainEvent
from .repository import EffectRecord, EffectResult, WorkflowRepository


class EventInbox(Protocol):
    def next(self, task_id: str) -> DomainEvent | None: ...

    def publish(self, event: DomainEvent) -> None: ...

    def ack(self, event: DomainEvent) -> None: ...


class JsonDomainEventInbox:
    """Repository-backed event inbox with ack records in the same journal."""

    def __init__(self, repository: WorkflowRepository) -> None:
        self._repository = repository

    def next(self, task_id: str) -> DomainEvent | None:
        events = self._repository.pending_domain_events(task_id)
        return events[0] if events else None

    def publish(self, event: DomainEvent) -> None:
        self._repository.append_domain_event(event)

    def ack(self, event: DomainEvent) -> None:
        self._repository.ack_domain_event(event)


class InMemoryDomainEventInbox:
    """Deterministic inbox for unit tests and local composition."""

    def __init__(self, events: tuple[DomainEvent, ...] = ()) -> None:
        self._events = deque(events)

    def next(self, task_id: str) -> DomainEvent | None:
        for event in self._events:
            if event.task_id == task_id:
                return event
        return None

    def publish(self, event: DomainEvent) -> None:
        if event.event_id and any(item.event_id == event.event_id for item in self._events):
            return
        self._events.append(event)

    def ack(self, event: DomainEvent) -> None:
        try:
            self._events.remove(event)
        except ValueError:
            return


def effect_result_event(result: EffectResult, effect: EffectRecord) -> DomainEvent | None:
    """Turn one terminal effect result into the next sequenced domain fact."""

    event_name = result.event_name
    if not event_name:
        return None
    payload: object = result.event_payload if result.event_payload is not None else {}
    if isinstance(payload, Mapping):
        payload = dict(payload)
    if result.failure is not None:
        if isinstance(payload, Mapping):
            payload = {**payload, "failure": asdict(result.failure)}
        else:
            payload = {"failure": asdict(result.failure), "result": payload}
    occurred_at = datetime.now(timezone.utc).isoformat()
    event_id = f"effect:{effect.effect_id}:{result.status}"
    return DomainEvent(
        name=event_name,
        task_id=effect.task_id,
        sequence=effect.sequence + 1,
        payload=payload,
        occurred_at=occurred_at,
        event_id=event_id,
    )


__all__ = [
    "EventInbox",
    "InMemoryDomainEventInbox",
    "JsonDomainEventInbox",
    "effect_result_event",
]
