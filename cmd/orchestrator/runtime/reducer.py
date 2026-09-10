"""Pure application of a state decision to an immutable workflow snapshot."""

from __future__ import annotations

from dataclasses import replace

from ..domain.context import (
    AuditState,
    DeliveryState,
    HumanGateState,
    ProgressState,
    ParallelState,
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

        next_context = replace(
            context,
            progression=ProgressState(
                state=target_state,
                sequence=context.progression.sequence + 1,
                entered_at=(
                    update.progression.entered_at
                    if update.progression is not None
                    and update.progression.entered_at is not None
                    else context.progression.entered_at
                ),
                resume_state=resume_state,
            ),
            delivery=self._delivery(context.delivery, update),
            recovery=self._recovery(context.recovery, update),
            review=self._review(context.review, update),
            human_gate=self._human_gate(context.human_gate, update),
            parallel=self._parallel(context.parallel, update),
            audit=self._audit(context.audit, update),
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
            dispatch_operation_id=(
                update.delivery.dispatch_operation_id
                if update.delivery.dispatch_operation_id is not None
                else current.dispatch_operation_id
            ),
            external_message_id=(
                update.delivery.external_message_id
                if update.delivery.external_message_id is not None
                else current.external_message_id
            ),
            idempotency_key=(
                update.delivery.idempotency_key
                if update.delivery.idempotency_key is not None
                else current.idempotency_key
            ),
            phase=(update.delivery.phase if update.delivery.phase is not None else current.phase),
            role=(update.delivery.role if update.delivery.role is not None else current.role),
            expected_agent_id=(
                update.delivery.expected_agent_id
                if update.delivery.expected_agent_id is not None
                else current.expected_agent_id
            ),
            last_sent_at=(
                update.delivery.last_sent_at
                if update.delivery.last_sent_at is not None
                else current.last_sent_at
            ),
            attempt=(
                update.delivery.attempt
                if update.delivery.attempt is not None
                else current.attempt
            ),
            last_result_ref=(
                update.delivery.last_result_ref
                if update.delivery.last_result_ref is not None
                else current.last_result_ref
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
            external_retry_count=(
                update.recovery.external_retry_count
                if update.recovery.external_retry_count is not None
                else current.external_retry_count
            ),
            reply_retry_count=(
                update.recovery.reply_retry_count
                if update.recovery.reply_retry_count is not None
                else current.reply_retry_count
            ),
            timeout_retry_count=(
                update.recovery.timeout_retry_count
                if update.recovery.timeout_retry_count is not None
                else current.timeout_retry_count
            ),
            last_failure=(
                update.recovery.last_failure
                if update.recovery.last_failure is not None
                else current.last_failure
            ),
            no_progress_count=(
                update.recovery.no_progress_count
                if update.recovery.no_progress_count is not None
                else current.no_progress_count
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
            zhongshu_revision_round=(current.zhongshu_revision_round if current else 0),
            max_zhongshu_revision_rounds=(
                current.max_zhongshu_revision_rounds if current else 8
            ),
            freeze_check_attempt=(current.freeze_check_attempt if current else 0),
            max_freeze_check_attempts=(
                current.max_freeze_check_attempts if current else 2
            ),
            item_revision_round=(current.item_revision_round if current else 0),
            max_item_revision_rounds=(current.max_item_revision_rounds if current else 3),
            last_reply_fingerprint=(current.last_reply_fingerprint if current else ""),
            plan_ref=(
                update.review.plan_ref
                if update.review.plan_ref is not None
                else current.plan_ref if current else None
            ),
            plan_hash=(
                update.review.plan_hash
                if update.review.plan_hash is not None
                else current.plan_hash if current else None
            ),
            task_graph_ref=(
                update.review.task_graph_ref
                if update.review.task_graph_ref is not None
                else current.task_graph_ref if current else None
            ),
            task_items=(
                update.review.task_items
                if update.review.task_items is not None
                else current.task_items if current else ()
            ),
            task_groups=(
                update.review.task_groups
                if update.review.task_groups is not None
                else current.task_groups if current else ()
            ),
            completed_item_ids=(
                update.review.completed_item_ids
                if update.review.completed_item_ids is not None
                else current.completed_item_ids if current else ()
            ),
        )

    @staticmethod
    def _human_gate(current: HumanGateState | None, update: ContextUpdate) -> HumanGateState | None:
        if update.human_gate is None:
            return current
        if update.human_gate.clear_gate:
            return None
        decision_id = update.human_gate.decision_id or (current.decision_id if current else "")
        resume_state = update.human_gate.resume_state or (
            current.resume_state if current else ""
        )
        if not decision_id or not resume_state:
            raise InvariantViolation("human gate update requires decision_id and resume_state")
        return HumanGateState(
            decision_id=decision_id,
            reason_code=(
                update.human_gate.reason_code
                if update.human_gate.reason_code is not None
                else current.reason_code if current else ""
            ),
            resume_state=resume_state,
            message_id=(
                update.human_gate.message_id
                if update.human_gate.message_id is not None
                else current.message_id if current else None
            ),
        )

    @staticmethod
    def _parallel(current: ParallelState, update: ContextUpdate) -> ParallelState:
        if update.parallel is None:
            return current
        active_node_run_id = current.active_node_run_id
        if update.parallel.clear_active_node_run:
            active_node_run_id = None
        if update.parallel.active_node_run_id is not None:
            active_node_run_id = update.parallel.active_node_run_id
        return replace(current, active_node_run_id=active_node_run_id)

    @staticmethod
    def _audit(current: AuditState, update: ContextUpdate) -> AuditState:
        if update.audit is None:
            return current
        keys = current.sent_notification_keys
        notification_key = update.audit.sent_notification_key
        if notification_key and notification_key not in keys:
            keys = (*keys, notification_key)
        for notification_key in update.audit.sent_notification_keys:
            if notification_key and notification_key not in keys:
                keys = (*keys, notification_key)
        return replace(
            current,
            sent_notification_keys=keys,
            heartbeat_count=(
                update.audit.heartbeat_count
                if update.audit.heartbeat_count is not None
                else current.heartbeat_count
            ),
            last_heartbeat_at=(
                update.audit.last_heartbeat_at
                if update.audit.last_heartbeat_at is not None
                else current.last_heartbeat_at
            ),
            reply_history_ref=(
                update.audit.reply_history_ref
                if update.audit.reply_history_ref is not None
                else current.reply_history_ref
            ),
            last_artifact_id=(
                update.audit.last_artifact_id
                if update.audit.last_artifact_id is not None
                else current.last_artifact_id
            ),
            updated_at=(
                update.audit.updated_at
                if update.audit.updated_at is not None
                else current.updated_at
            ),
        )


__all__ = ["LinearContextReducer"]
