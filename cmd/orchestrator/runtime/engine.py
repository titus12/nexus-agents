"""Coordinator for one linear workflow event."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..domain.context import WorkflowContext
from ..domain.decisions import StateDecision
from ..domain.errors import InvariantViolation, PostCommitLeaseReleaseError
from ..domain.events import DomainEvent
from .repository import CommitResult, WorkflowRepository, WorkflowSnapshot


class DomainEventInbox(Protocol):
    def next(self, task_id: str) -> DomainEvent | None: ...


class WorkflowState(Protocol):
    def handle(self, context: WorkflowContext, event: DomainEvent) -> StateDecision: ...


class StateRegistry(Protocol):
    def get(self, state_name: str) -> WorkflowState: ...


class LockPort(Protocol):
    def acquire(self, task_id: str) -> None: ...

    def refresh(self, task_id: str) -> None: ...

    def release(self, task_id: str) -> None: ...


class ContextReducer(Protocol):
    def apply(self, snapshot: WorkflowSnapshot, decision: StateDecision) -> WorkflowSnapshot: ...


@dataclass(frozen=True)
class RunResult:
    task_id: str
    status: str
    sequence: int


class WorkflowEngine:
    """Advance exactly one domain event under one lock ownership interval."""

    def __init__(
        self,
        repository: WorkflowRepository,
        states: StateRegistry,
        lock: LockPort,
        reducer: ContextReducer,
    ) -> None:
        self._repository = repository
        self._states = states
        self._lock = lock
        self._reducer = reducer

    def dispatch(self, event: DomainEvent) -> StateDecision:
        if not isinstance(event, DomainEvent) or not event.task_id:
            raise InvariantViolation("dispatch requires a domain event with task_id")

        self._lock.acquire(event.task_id)
        commit_result: CommitResult | None = None
        try:
            before = self._repository.load(event.task_id)
            if before.task_id != event.task_id:
                raise InvariantViolation("repository snapshot task does not match event")
            if event.sequence != before.context.progression.sequence:
                raise InvariantViolation("domain event sequence does not match snapshot")
            state = self._states.get(before.context.progression.state)
            decision = state.handle(before.context, event)
            if not isinstance(decision, StateDecision):
                raise InvariantViolation("workflow state must return StateDecision")
            self._validate_decision(event.task_id, decision)
            after = self._reducer.apply(before, decision)
            self._validate_after(before, after)
            commit_result = self._repository.commit_transition(before, after, decision)
        except BaseException as error:
            try:
                self._lock.release(event.task_id)
            except BaseException as release_error:
                error.add_note(f"lock release failed: {release_error}")
            raise
        else:
            try:
                self._lock.release(event.task_id)
            except Exception as release_error:
                if commit_result is None:
                    raise
                raise PostCommitLeaseReleaseError(
                    task_id=event.task_id,
                    transition_id=commit_result.transition_id,
                    state=commit_result.snapshot.context.progression.state,
                    sequence=commit_result.snapshot.context.progression.sequence,
                    cause=release_error,
                ) from release_error
            return decision

    @staticmethod
    def _validate_decision(task_id: str, decision: StateDecision) -> None:
        for effect in decision.effects:
            if not effect.effect_id or not effect.effect_type or not effect.idempotency_key:
                raise InvariantViolation("effect intent identity is incomplete")
            if effect.task_id != task_id:
                raise InvariantViolation("effect intent task does not match event")

    @staticmethod
    def _validate_after(before: WorkflowSnapshot, after: WorkflowSnapshot) -> None:
        if not isinstance(after, WorkflowSnapshot):
            raise InvariantViolation("context reducer must return WorkflowSnapshot")
        if after.task_id != before.task_id:
            raise InvariantViolation("context reducer changed task identity")
        if after.context.progression.sequence != before.context.progression.sequence + 1:
            raise InvariantViolation("context reducer must advance sequence by one")
        if after.state_version != before.state_version + 1:
            raise InvariantViolation("context reducer must advance state_version by one")


__all__ = [
    "ContextReducer",
    "DomainEventInbox",
    "LockPort",
    "RunResult",
    "StateRegistry",
    "WorkflowEngine",
    "WorkflowState",
]
