"""Typed state outputs used by the workflow engine."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from .context import (
    AuditUpdate,
    DeliveryUpdate,
    HumanGateUpdate,
    ProgressUpdate,
    ParallelUpdate,
    RecoveryUpdate,
    ReviewUpdate,
)
from .events import TransitionRequest


@dataclass(frozen=True)
class ContextUpdate:
    """Explicit aggregate updates; arbitrary mapping patches are forbidden."""

    progression: ProgressUpdate | None = None
    review: ReviewUpdate | None = None
    delivery: DeliveryUpdate | None = None
    human_gate: HumanGateUpdate | None = None
    recovery: RecoveryUpdate | None = None
    parallel: ParallelUpdate | None = None
    audit: AuditUpdate | None = None


@dataclass(frozen=True)
class EffectRequest:
    """A durable request for one externally executed side effect."""

    effect_id: str
    effect_type: str
    task_id: str
    idempotency_key: str
    payload_ref: str | None = None
    payload: Mapping[str, object] = field(default_factory=dict)
    deadline_at: str | None = None

    def __post_init__(self) -> None:
        if not self.effect_id or not self.effect_type or not self.task_id:
            raise ValueError("effect identity is incomplete")
        if not self.idempotency_key:
            raise ValueError("effect idempotency_key is required")
        if not isinstance(self.payload, Mapping):
            raise TypeError("effect payload must be a mapping")
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


@dataclass(frozen=True)
class StateDecision:
    """The complete pure output of a workflow state handler."""

    transition: TransitionRequest | None = None
    update: ContextUpdate = field(default_factory=ContextUpdate)
    effects: tuple[EffectRequest, ...] = ()
    # The inbox event which caused this decision.  Persistence uses this
    # marker to make a crash between journal commit and inbox acknowledgement
    # harmless: replaying the same event becomes a no-op.
    source_event_id: str = ""


__all__ = ["ContextUpdate", "EffectRequest", "StateDecision"]
