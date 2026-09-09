"""Pure application of a state decision to an immutable workflow snapshot."""

from __future__ import annotations

from dataclasses import replace

from ..domain.context import (
    DeliveryState,
    HumanGateState,
    ProgressState,
    RecoveryState,
    ReviewState,
    WorkflowContext,
)
from ..domain.decisions import ContextUpdate, StateDecision
from ..domain.errors import InvariantViolation
from ..domain.transitions import TransitionRegistry
from .repository import WorkflowSnapshot


class LinearContextReducer:
    """Apply one typed decision without mutating the source snapshot."""

    def __init__(self, transitions: TransitionRegistry | None = None) -> None:
        self._transitions = transitions or TransitionRegistry.default()

    def apply(self, snapshot: WorkflowSnapshot, decision: StateDecision) -> WorkflowSnapshot:
        if not isinstance(snapshot, WorkflowSnapshot):
            raise InvariantViolation("reducer requires WorkflowSnapshot")
        if not isinstance(decision, StateDecision):
            raise InvariantViolation("reducer requires StateDecision")

        context = snapshot.context
        update = decision.update
        target_state = context.progression.state
        resume_state = context.progression.resume_state
        if decision.transition is not None:
            resume_for_transition = (
                context.progression.resume_state
                if decision.transition.action == "RESUME"
                else None
            )
            target_state = self._transitions.target_for(
                context.progression.state,
                decision.transition.action,
                resume_for_transition,
            )
            if target_state not in {"HUMAN_GATE", "RETRY_WAIT", "BLOCKED", "PERSISTENCE_DEGRADED"}:
                resume_state = None

        if update.progression is not None:
            resume_state = (
                update.progression.resume_state
                if update.progression.resume_state is not None
                else resume_state
            )
            requested_state = update.progression.state
            if requested_state is not None and requested_state != target_state:
                raise InvariantViolation(
                    "progression update conflicts with registered transition"
                )
        if target_state in {"HUMAN_GATE", "RETRY_WAIT", "BLOCKED", "PERSISTENCE_DEGRADED"}:
            if not isinstance(resume_state, str) or not resume_state:
                raise InvariantViolation(
                    f"state {target_state!r} requires a recorded resume_state"
                )

        next_context = WorkflowContext(
            identity=context.identity,
            progression=ProgressState(
                state=target_state,
                sequence=context.progression.sequence + 1,
                entered_at=context.progression.entered_at,
                resume_state=resume_state,
            ),
            delivery=self._delivery(context.delivery, update),
            recovery=self._recovery(context.recovery, update),
            review=self._review(context.review, update),
            human_gate=self._human_gate(context.human_gate, update),
        )
        return WorkflowSnapshot(
            task_id=snapshot.task_id,
            context=next_context,
            state_version=snapshot.state_version + 1,
        )

    @staticmethod
    def _delivery(current: DeliveryState, update: ContextUpdate) -> DeliveryState:
        if update.delivery is None:
            return current
        return replace(
            current,
            active_request_id=(
                update.delivery.active_request_id
                if update.delivery.active_request_id is not None
                else current.active_request_id
            ),
            status=(
                update.delivery.status
                if update.delivery.status is not None
                else current.status
            ),
        )

    @staticmethod
    def _recovery(current: RecoveryState, update: ContextUpdate) -> RecoveryState:
        if update.recovery is None:
            return current
        return replace(
            current,
            retry_count=(
                update.recovery.retry_count
                if update.recovery.retry_count is not None
                else current.retry_count
            ),
            blocked_reason=(
                update.recovery.blocked_reason
                if update.recovery.blocked_reason is not None
                else current.blocked_reason
            ),
        )

    @staticmethod
    def _review(current: ReviewState | None, update: ContextUpdate) -> ReviewState | None:
        if update.review is None:
            return current
        revision_id = update.review.revision_id or (current.revision_id if current else "")
        if not revision_id:
            raise InvariantViolation("review update requires revision_id")
        return ReviewState(
            revision_id=revision_id,
            active_group_id=(
                update.review.active_group_id
                if update.review.active_group_id is not None
                else current.active_group_id if current else None
            ),
            active_item_id=(
                update.review.active_item_id
                if update.review.active_item_id is not None
                else current.active_item_id if current else None
            ),
            findings=(
                update.review.findings
                if update.review.findings is not None
                else current.findings if current else ()
            ),
        )

    @staticmethod
    def _human_gate(current: HumanGateState | None, update: ContextUpdate) -> HumanGateState | None:
        if update.human_gate is None:
            return current
        decision_id = update.human_gate.decision_id or (current.decision_id if current else "")
        resume_state = update.human_gate.resume_state or (
            current.resume_state if current else ""
        )
        if not decision_id or not resume_state:
            raise InvariantViolation("human gate update requires decision_id and resume_state")
        return HumanGateState(
            decision_id=decision_id,
            reason_code=current.reason_code if current else "",
            resume_state=resume_state,
        )


__all__ = ["LinearContextReducer"]
