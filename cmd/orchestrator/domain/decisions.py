"""Typed state outputs used by the workflow engine."""

from __future__ import annotations

from dataclasses import dataclass, field

from .context import (
    DeliveryUpdate,
    HumanGateUpdate,
    ProgressUpdate,
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


@dataclass(frozen=True)
class EffectRequest:
    """A durable request for one externally executed side effect."""

    effect_id: str
    effect_type: str
    task_id: str
    idempotency_key: str
    payload_ref: str | None = None


@dataclass(frozen=True)
class StateDecision:
    """The complete pure output of a workflow state handler."""

    transition: TransitionRequest | None = None
    update: ContextUpdate = field(default_factory=ContextUpdate)
    effects: tuple[EffectRequest, ...] = ()


__all__ = ["ContextUpdate", "EffectRequest", "StateDecision"]
