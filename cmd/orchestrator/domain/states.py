"""Pure concrete state objects for the linear workflow.

The objects in this module are domain policies only.  They create typed
``StateDecision`` values and never perform I/O, persistence, transport, or
concurrency work.  The runtime executes the returned effect intents through
typed ports after the decision has been durably committed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from types import MappingProxyType
from typing import ClassVar, Protocol, runtime_checkable

from .context import (
    DeliveryUpdate,
    HumanGateUpdate,
    ParallelUpdate,
    ProgressUpdate,
    RecoveryUpdate,
    ReviewUpdate,
    ReviewTaskGroup,
    ReviewTaskItem,
    WorkflowContext,
)
from .decisions import ContextUpdate, EffectRequest, StateDecision
from .errors import FailureRecord, InvariantViolation
from .events import DomainEvent, TransitionRequest
from .policies.zhongshu import merge_findings
from .policies.prompts import build_prompt
from .transitions import ALL_STATES, BUSINESS_STATES, SYSTEM_STATES, TransitionRegistry


_ROLE_BY_STATE: dict[str, tuple[str, str]] = {
    "ZHONGSHU_ANALYST": ("review-analyst", "ZHONGSHU"),
    "ZHONGSHU_SOLVER": ("review-solver", "ZHONGSHU"),
    "ZHONGSHU_CRITIC": ("review-critic", "ZHONGSHU"),
    "ZHONGSHU_FREEZE_CHECK": ("review-critic", "ZHONGSHU"),
    "MENXIA_ITEM_SOLVER": ("review-solver", "MENXIA"),
    "MENXIA_ITEM_ANALYST": ("review-analyst", "MENXIA"),
    "MENXIA_ITEM_CRITIC": ("review-critic", "MENXIA"),
    "MENXIA_GROUP_GATE": ("review-critic", "MENXIA"),
}
_PARALLEL_WORKER_LIMIT_FIELD = {
    "ZHONGSHU_ANALYST": "analyst_default_workers",
    "ZHONGSHU_CRITIC": "critic_default_workers",
}


@runtime_checkable
class WorkflowState(Protocol):
    """Interface implemented by every concrete workflow state."""

    name: str

    def enter(self, context: WorkflowContext) -> StateDecision: ...

    def handle(
        self,
        context: WorkflowContext,
        event: DomainEvent,
    ) -> StateDecision: ...

    def on_timeout(self, context: WorkflowContext) -> StateDecision: ...

    def on_resume(self, context: WorkflowContext) -> StateDecision: ...


class _ConcreteWorkflowState:
    """Small shared guard/decision helper used by concrete state objects."""

    name: ClassVar[str]
    supported_actions: ClassVar[tuple[str, ...]] = ()
    requires_resume_state: ClassVar[bool] = False
    _transitions: ClassVar[TransitionRegistry] = TransitionRegistry.default()

    def enter(self, context: WorkflowContext) -> StateDecision:
        self._require_current_state(context)
        if self.requires_resume_state:
            self._require_recorded_resume_state(context)
        return StateDecision()

    def handle(
        self,
        context: WorkflowContext,
        event: DomainEvent,
    ) -> StateDecision:
        self._require_current_state(context)
        self._require_event_task(context, event)
        action = self._event_action(event)
        if event.name == "TIMEOUT":
            timeout = self.on_timeout(context)
            return replace(
                timeout,
                update=replace(
                    timeout.update,
                    progression=replace(
                        timeout.update.progression or ProgressUpdate(),
                        entered_at=event.occurred_at,
                    ),
                ),
            )
        if (
            event.name == "FAIL"
            and isinstance(event.payload, Mapping)
            and bool(event.payload.get("retryable"))
            and context.recovery.retry_count < context.recovery.max_retries
        ):
            action = "RETRY"
        if action not in self.supported_actions and action not in {"FAIL", "CANCEL"}:
            raise InvariantViolation(
                f"event {event.name!r} is not supported by state {self.name!r}"
            )
        return self._transition_decision(context, event, action=action)

    def on_timeout(self, context: WorkflowContext) -> StateDecision:
        self._require_current_state(context)
        failure = FailureRecord(
            failure_id=f"timeout:{context.identity.task_id}:{context.progression.sequence}",
            stage="remote_run",
            owner_component=self.name,
            task_id=context.identity.task_id,
            state=self.name,
            sequence=context.progression.sequence,
            node_run_id=context.parallel.active_node_run_id,
            worker_id=None,
            effect_id=None,
            error_code="AGENT_TIMEOUT",
            retryable=context.recovery.timeout_retry_count < context.recovery.max_timeout_retries,
            message=f"agent timeout in {self.name}",
            cause_type="Timeout",
        )
        action = "RETRY" if failure.retryable else "FAIL"
        return StateDecision(
            transition=TransitionRequest(action=action, reason_code=failure.error_code),
            update=ContextUpdate(
                progression=ProgressUpdate(resume_state=self.name),
                recovery=RecoveryUpdate(
                    timeout_retry_count=context.recovery.timeout_retry_count + 1,
                    last_failure=failure,
                ),
            ),
        )

    def on_resume(self, context: WorkflowContext) -> StateDecision:
        self._require_current_state(context)
        raise InvariantViolation(
            f"resume handling is not supported for state {self.name!r}"
        )

    def _transition_decision(
        self,
        context: WorkflowContext,
        event: DomainEvent,
        *,
        action: str | None = None,
    ) -> StateDecision:
        payload = event.payload if isinstance(event.payload, Mapping) else {}
        action = action or event.name
        target = self._transitions.target_for(
            self.name,
            action,
            context.progression.resume_state if action == "RESUME" else None,
        )
        update = self._payload_update(context, event)
        if action in {"RETRY", "BLOCK", "BLOCKED", "OPEN_HUMAN_GATE", "HUMAN_GATE"}:
            # System states must carry an explicit, validated return point.
            update = ContextUpdate(
                progression=ProgressUpdate(resume_state=self.name),
                recovery=update.recovery,
                human_gate=update.human_gate,
                parallel=update.parallel,
                review=update.review,
                delivery=update.delivery,
                audit=update.audit,
            )
        if action in {"OPEN_HUMAN_GATE", "HUMAN_GATE"} and isinstance(payload, Mapping):
            decision_id = str(
                payload.get("decision_id")
                or f"{context.identity.task_id}:human-gate:{context.progression.sequence + 1}"
            )
            update = replace(
                update,
                progression=ProgressUpdate(resume_state=self.name),
                human_gate=HumanGateUpdate(
                    decision_id=decision_id,
                    resume_state=self.name,
                    reason_code=self._reason_code(event),
                    message_id=(
                        str(payload["message_id"])
                        if payload.get("message_id") else None
                    ),
                ),
            )
        if action == "RETRY" and event.name == "FAIL":
            update = replace(
                update,
                recovery=RecoveryUpdate(
                    retry_count=context.recovery.retry_count + 1,
                    last_failure=(
                        update.recovery.last_failure
                        if update.recovery is not None
                        else self._failure_from_payload(context, payload)
                    ),
                ),
            )
        if action == "RESUME" and self.name == "HUMAN_GATE":
            update = replace(
                update,
                human_gate=HumanGateUpdate(clear_gate=True),
            )
        progression = update.progression or ProgressUpdate()
        update = replace(
            update,
            progression=replace(
                progression,
                entered_at=event.occurred_at or progression.entered_at,
            ),
        )
        if target not in {"HUMAN_GATE", "RETRY_WAIT", "BLOCKED", "PERSISTENCE_DEGRADED", "FAILED", "CANCELLED", "DONE"}:
            effect = self._dispatch_effect(
                context,
                target,
                revision_id=(
                    str(payload.get("revision_id") or "")
                    or (context.review.revision_id if context.review else "")
                ),
                plan_hash=(
                    str(payload.get("plan_hash") or payload.get("reviewed_plan_hash") or "")
                    or (context.review.plan_hash if context.review else "")
                ),
            )
            review_update = update.review
            if target == "MENXIA_ITEM_SOLVER" and context.review is not None:
                next_item = context.review.next_menxia_item()
                if next_item is not None:
                    review_update = ReviewUpdate(
                        revision_id=context.review.revision_id,
                        active_group_id=next_item.group_id,
                        active_item_id=next_item.item_id,
                    )
            update = ContextUpdate(
                progression=update.progression,
                review=review_update,
                recovery=update.recovery,
                human_gate=update.human_gate,
                parallel=(
                    ParallelUpdate(
                        active_node_run_id=str(effect.payload.get("node_run_id")),
                    )
                    if effect.effect_type == "node_dispatch"
                    else ParallelUpdate(clear_active_node_run=True)
                ),
                delivery=DeliveryUpdate(
                    active_request_id=str(effect.payload.get("request_id") or ""),
                    dispatch_operation_id=None,
                    idempotency_key=effect.idempotency_key,
                    status="PENDING",
                    phase=str(effect.payload.get("phase") or ""),
                    role=str(effect.payload.get("role") or ""),
                    expected_agent_id=str(effect.payload.get("agent_id") or ""),
                    attempt=context.delivery.attempt + 1,
                ),
                audit=update.audit,
            )
        elif target in {"HUMAN_GATE", "DONE"}:
            if target == "HUMAN_GATE":
                notification_key = f"{context.identity.task_id}:human-gate:{context.progression.sequence + 1}"
                decision_id = str(payload.get("decision_id") or notification_key)
                update = replace(
                    update,
                    delivery=DeliveryUpdate(
                        active_request_id=decision_id,
                        idempotency_key=notification_key,
                        status="PENDING",
                        phase="HUMAN_GATE",
                        role="human-gate",
                    ),
                )
            effect = None
        else:
            effect = None
        return StateDecision(
            transition=TransitionRequest(
                action=action,
                reason_code=self._reason_code(event),
            ),
            update=update,
            effects=(effect,) if effect is not None else (),
        )

    def _payload_update(self, context: WorkflowContext, event: DomainEvent) -> ContextUpdate:
        payload = dict(event.payload) if isinstance(event.payload, Mapping) else {}
        if event.name == "APPROVE_ITEM" and context.review is not None:
            payload.setdefault("group_id", context.review.active_group_id)
            payload.setdefault("item_id", context.review.active_item_id)
            payload.setdefault("completed_item_id", context.review.active_item_id)
        review_update = self._review_update(context, payload)
        recovery_update = None
        delivery_update = None
        if event.name not in {
            "RETRY", "BLOCK", "BLOCKED", "OPEN_HUMAN_GATE", "HUMAN_GATE",
            "FAIL", "CANCEL", "AGENT_FAILED", "NODE_FAILED",
        }:
            delivery_update = DeliveryUpdate(
                status="COMPLETED",
                active_request_id=(
                    str(payload["request_id"]) if payload.get("request_id") else None
                ),
                dispatch_operation_id=(
                    str(payload["operation_id"]) if payload.get("operation_id") else None
                ),
                external_message_id=(
                    str(payload["external_message_id"])
                    if payload.get("external_message_id")
                    else None
                ),
                last_result_ref=(
                    str(payload["result_artifact_id"])
                    if payload.get("result_artifact_id")
                    else None
                ),
            )
        if event.name in {"FAIL", "AGENT_FAILED", "NODE_FAILED"}:
            recovery_update = RecoveryUpdate(last_failure=self._failure_from_payload(context, payload))
            delivery_update = DeliveryUpdate(
                status="FAILED",
                active_request_id=(
                    str(payload["request_id"]) if payload.get("request_id") else None
                ),
                dispatch_operation_id=(
                    str(payload["operation_id"]) if payload.get("operation_id") else None
                ),
                external_message_id=(
                    str(payload["external_message_id"])
                    if payload.get("external_message_id")
                    else None
                ),
                last_result_ref=(
                    str(payload["result_artifact_id"])
                    if payload.get("result_artifact_id")
                    else None
                ),
            )
        return ContextUpdate(
            review=review_update,
            recovery=recovery_update,
            delivery=delivery_update,
            parallel=(
                ParallelUpdate(clear_active_node_run=True)
                if event.name == "NODE_COMPLETED"
                else None
            ),
        )

    @staticmethod
    def _review_update(
        context: WorkflowContext,
        payload: Mapping[str, object],
    ) -> ReviewUpdate | None:
        raw_findings = payload.get("findings")
        has_review_data = any(
            key in payload
            for key in (
                "findings",
                "plan",
                "plan_ref",
                "plan_hash",
                "task_graph_ref",
                "task_graph",
                "group_id",
                "item_id",
                "completed_item_id",
            )
        )
        if not has_review_data:
            return None
        if raw_findings is not None and not isinstance(raw_findings, list):
            raise InvariantViolation("findings payload must be an array")
        from .findings import Finding

        findings = tuple(Finding.from_dict(dict(item)) for item in (raw_findings or []))
        existing = context.review.findings if context.review else ()
        plan = payload.get("plan")
        task_graph = payload.get("task_graph")
        plan_ref = payload.get("plan_ref") or payload.get("result_artifact_id")
        task_graph_ref = payload.get("task_graph_ref")
        if task_graph_ref is None and isinstance(task_graph, Mapping):
            task_graph_ref = plan_ref
        graph = plan if isinstance(plan, Mapping) else task_graph
        task_items, task_groups = _task_graph_projection(graph)
        existing_completed = context.review.completed_item_ids if context.review else ()
        completed = list(existing_completed)
        completed_id = payload.get("completed_item_id")
        if not completed_id and payload.get("action") == "APPROVE_ITEM":
            completed_id = payload.get("item_id") or (
                context.review.active_item_id if context.review else None
            )
        if completed_id and str(completed_id) not in completed:
            completed.append(str(completed_id))
        return ReviewUpdate(
            revision_id=(
                str(payload.get("revision_id") or "")
                or (context.review.revision_id if context.review else "")
                or f"{context.identity.task_id}:{context.progression.sequence + 1}"
            ),
            active_group_id=(
                str(payload["group_id"])
                if payload.get("group_id")
                else context.review.active_group_id if context.review else None
            ),
            active_item_id=(
                str(payload["item_id"])
                if payload.get("item_id")
                else context.review.active_item_id if context.review else None
            ),
            findings=merge_findings(existing, findings),
            plan_ref=str(plan_ref) if plan_ref else None,
            plan_hash=(
                str(payload.get("plan_hash") or payload.get("reviewed_plan_hash") or "")
                or None
            ),
            task_graph_ref=str(task_graph_ref) if task_graph_ref else None,
            task_items=task_items,
            task_groups=task_groups,
            completed_item_ids=tuple(completed),
        )


    @staticmethod
    def _failure_from_payload(
        context: WorkflowContext,
        payload: Mapping[str, object],
    ) -> FailureRecord:
        raw = payload.get("failure")
        if isinstance(raw, Mapping):
            data = dict(raw)
            data.setdefault("failure_id", f"failure:{context.identity.task_id}:{context.progression.sequence}")
            data.setdefault("stage", "workflow")
            data.setdefault("owner_component", context.progression.state)
            data.setdefault("task_id", context.identity.task_id)
            data.setdefault("state", context.progression.state)
            data.setdefault("sequence", context.progression.sequence)
            data.setdefault("node_run_id", context.parallel.active_node_run_id)
            data.setdefault("worker_id", None)
            data.setdefault("effect_id", None)
            data.setdefault("error_code", "WORKFLOW_FAILURE")
            data.setdefault("retryable", False)
            data.setdefault("message", str(payload.get("reason") or "workflow failure"))
            data.setdefault("cause_type", "DomainEvent")
            return FailureRecord(**data)
        return FailureRecord(
            failure_id=f"failure:{context.identity.task_id}:{context.progression.sequence}",
            stage="workflow",
            owner_component=context.progression.state,
            task_id=context.identity.task_id,
            state=context.progression.state,
            sequence=context.progression.sequence,
            node_run_id=context.parallel.active_node_run_id,
            worker_id=None,
            effect_id=None,
            error_code=str(payload.get("error_code") or "WORKFLOW_FAILURE"),
            retryable=bool(payload.get("retryable", False)),
            message=str(payload.get("reason") or "workflow failure"),
            cause_type="DomainEvent",
        )

    @staticmethod
    def _dispatch_effect(
        context: WorkflowContext,
        target: str,
        *,
        revision_id: str = "",
        plan_hash: str = "",
    ) -> EffectRequest:
        role, phase = _ROLE_BY_STATE[target]
        request_id = f"{context.identity.task_id}:{target}:{context.progression.sequence + 1}"
        prompt = build_prompt(context)
        next_item = (
            context.review.next_menxia_item()
            if target == "MENXIA_ITEM_SOLVER" and context.review is not None
            else None
        )
        worker_limit_field = _PARALLEL_WORKER_LIMIT_FIELD.get(target)
        if (
            worker_limit_field is not None
            and context.parallel is not None
            and context.parallel.zhongshu.enabled
        ):
            worker_count = max(
                1,
                int(getattr(context.parallel.zhongshu, worker_limit_field)),
            )
        else:
            worker_count = 1
        if worker_count > 1:
            bindings = [
                {
                    "worker_id": f"{target.lower()}-worker-{index:02d}",
                    "agent_id": f"{phase.lower()}-{role.split('-')[-1]}-{index:02d}",
                    "task_id": context.identity.task_id,
                    "request_id": f"{request_id}:worker-{index:02d}",
                    "role": role,
                    "phase": phase,
                }
                for index in range(1, worker_count + 1)
            ]
            return EffectRequest(
                effect_id=f"node:{request_id}",
                effect_type="node_dispatch",
                task_id=context.identity.task_id,
                idempotency_key=request_id,
                payload_ref=context.request.payload_ref,
                payload={
                    "issue_id": context.identity.issue_id,
                    "request_id": request_id,
                    "phase": phase,
                    "role": role,
                    "target_state": target,
                    "state": target,
                    "node_run_id": f"node:{request_id}",
                    "prompt_ref": prompt.content,
                    "request_payload_ref": context.request.payload_ref or "",
                    "revision_id": revision_id,
                    "plan_hash": plan_hash,
                    "sequence": context.progression.sequence,
                    "bindings": bindings,
                    "group_id": next_item.group_id if next_item else None,
                    "item_id": next_item.item_id if next_item else None,
                },
            )
        agent_id = f"{phase.lower()}-{role.split('-')[-1]}"
        return EffectRequest(
            effect_id=f"dispatch:{request_id}",
            effect_type="agent_dispatch",
            task_id=context.identity.task_id,
            idempotency_key=request_id,
            payload_ref=context.request.payload_ref,
            payload={
                "issue_id": context.identity.issue_id,
                "request_id": request_id,
                "agent_id": agent_id,
                "role": role,
                "phase": phase,
                "target_state": target,
                "prompt_ref": prompt.content,
                "request_payload_ref": context.request.payload_ref or "",
                "references": dict(prompt.references),
                "revision_id": revision_id,
                "plan_hash": plan_hash,
                "sequence": context.progression.sequence,
                "group_id": next_item.group_id if next_item else None,
                "item_id": next_item.item_id if next_item else None,
            },
        )

    @staticmethod
    def _event_action(event: DomainEvent) -> str:
        if event.name != "NODE_COMPLETED":
            return event.name
        payload = event.payload if isinstance(event.payload, Mapping) else {}
        aggregate = payload.get("aggregate")
        action = payload.get("action")
        if not action and isinstance(aggregate, Mapping):
            action = aggregate.get("action")
        if not isinstance(action, str) or not action:
            raise InvariantViolation("node completion must contain one domain action")
        return action

    @staticmethod
    def _reason_code(event: DomainEvent) -> str:
        if isinstance(event.payload, Mapping):
            reason_code = event.payload.get("reason_code")
            if isinstance(reason_code, str) and reason_code:
                return reason_code
        return event.name

    def _require_current_state(self, context: WorkflowContext) -> None:
        current_state = context.progression.state
        if current_state != self.name:
            raise InvariantViolation(
                f"state object {self.name!r} cannot operate on context in "
                f"{current_state!r}"
            )

    @staticmethod
    def _require_event_task(context: WorkflowContext, event: DomainEvent) -> None:
        if event.task_id != context.identity.task_id:
            raise InvariantViolation(
                f"event task {event.task_id!r} does not match context task "
                f"{context.identity.task_id!r}"
            )

    def _require_recorded_resume_state(self, context: WorkflowContext) -> None:
        resume_state = context.progression.resume_state
        if not isinstance(resume_state, str) or not resume_state:
            raise InvariantViolation(
                f"state {self.name!r} requires a recorded resume_state"
            )


class RequestIntakeState(_ConcreteWorkflowState):
    name = "REQUEST_INTAKE"
    supported_actions = ("START",)


class ZhongshuAnalystState(_ConcreteWorkflowState):
    name = "ZHONGSHU_ANALYST"
    supported_actions = (
        "READY_FOR_SOLVER", "EVIDENCE_PACKET_READY", "REQUIREMENT_CONTRACT_READY",
        "EVIDENCE_SUPPLEMENT_READY", "HUMAN_GATE", "BLOCKED", "RETRY", "OPEN_HUMAN_GATE",
    )


class ZhongshuSolverState(_ConcreteWorkflowState):
    name = "ZHONGSHU_SOLVER"
    supported_actions = (
        "READY_FOR_CRITIC", "REQUEST_ANALYST_EVIDENCE", "NEEDS_MORE_EVIDENCE",
        "HUMAN_GATE", "BLOCKED", "RETRY", "OPEN_HUMAN_GATE",
    )


class ZhongshuCriticState(_ConcreteWorkflowState):
    name = "ZHONGSHU_CRITIC"
    supported_actions = (
        "APPROVE_CRITIC", "APPROVE_FREEZE", "REQUEST_ANALYST_EVIDENCE",
        "REQUEST_SOLVER_REVISION", "REQUEST_REGROUP", "TASK_APPROVED",
        "TASK_CHANGES_REQUIRED", "HUMAN_GATE", "BLOCKED", "RETRY", "OPEN_HUMAN_GATE",
    )


class ZhongshuFreezeCheckState(_ConcreteWorkflowState):
    name = "ZHONGSHU_FREEZE_CHECK"
    supported_actions = (
        "FREEZE_APPROVED", "APPROVE_FREEZE", "FREEZE_OK", "FREEZE_REJECTED",
        "RETRY", "BLOCK", "OPEN_HUMAN_GATE",
    )


class MenxiaItemSolverState(_ConcreteWorkflowState):
    name = "MENXIA_ITEM_SOLVER"
    supported_actions = (
        "FEASIBLE", "READY_FOR_ANALYST", "READY_FOR_CRITIC", "HUMAN_GATE", "BLOCKED",
        "RETRY", "BLOCK", "OPEN_HUMAN_GATE",
    )


class MenxiaItemAnalystState(_ConcreteWorkflowState):
    name = "MENXIA_ITEM_ANALYST"
    supported_actions = (
        "EVIDENCE_SUFFICIENT", "READY_FOR_CRITIC", "NEEDS_MORE_EVIDENCE",
        "REQUEST_SOLVER_REVISION", "HUMAN_GATE", "BLOCKED", "RETRY", "BLOCK", "OPEN_HUMAN_GATE",
    )


class MenxiaItemCriticState(_ConcreteWorkflowState):
    name = "MENXIA_ITEM_CRITIC"
    supported_actions = (
        "APPROVE_ITEM", "REVISE_ITEM", "SPLIT_ITEM", "MERGE_ITEM", "REMOVE_ITEM",
        "REQUEST_SOLVER_REVISION", "HUMAN_GATE", "BLOCKED", "RETRY", "BLOCK", "OPEN_HUMAN_GATE",
    )


class MenxiaGroupGateState(_ConcreteWorkflowState):
    name = "MENXIA_GROUP_GATE"
    supported_actions = (
        "COMPLETE", "APPROVE_GROUP", "APPROVE_FREEZE", "NEXT_ITEM", "REQUEST_GROUP_REVISION",
        "HUMAN_GATE", "BLOCKED", "RETRY", "BLOCK", "OPEN_HUMAN_GATE",
    )

    def handle(self, context: WorkflowContext, event: DomainEvent) -> StateDecision:
        self._require_current_state(context)
        self._require_event_task(context, event)
        action = self._event_action(event)
        if action in {"COMPLETE", "APPROVE_GROUP"} and context.review is not None:
            pending = [
                item
                for item in context.review.task_items
                if item.item_id not in set(context.review.completed_item_ids)
            ]
            if pending:
                if context.review.next_menxia_item() is None:
                    raise InvariantViolation("Menxia task graph has no dependency-ready next item")
                action = "NEXT_ITEM"
        if action not in self.supported_actions and action not in {"FAIL", "CANCEL"}:
            raise InvariantViolation(
                f"event {event.name!r} is not supported by state {self.name!r}"
            )
        return self._transition_decision(context, event, action=action)


class DoneState(_ConcreteWorkflowState):
    name = "DONE"


class HumanGateWorkflowState(_ConcreteWorkflowState):
    name = "HUMAN_GATE"
    supported_actions = ("RESUME", "CANCEL")
    requires_resume_state = True

    def on_resume(self, context: WorkflowContext) -> StateDecision:
        self._require_current_state(context)
        self._require_recorded_resume_state(context)
        return StateDecision(
            transition=TransitionRequest(
                action="RESUME",
                reason_code="HUMAN_DECISION_RECEIVED",
            ),
            update=ContextUpdate(human_gate=HumanGateUpdate(clear_gate=True)),
        )


class RetryWaitState(_ConcreteWorkflowState):
    name = "RETRY_WAIT"
    supported_actions = ("RESUME", "CANCEL")
    requires_resume_state = True

    def on_resume(self, context: WorkflowContext) -> StateDecision:
        self._require_current_state(context)
        self._require_recorded_resume_state(context)
        return StateDecision(
            transition=TransitionRequest(action="RESUME", reason_code="RETRY_READY")
        )


class BlockedState(_ConcreteWorkflowState):
    name = "BLOCKED"
    supported_actions = ("RESUME", "CANCEL")
    requires_resume_state = True

    def on_resume(self, context: WorkflowContext) -> StateDecision:
        self._require_current_state(context)
        self._require_recorded_resume_state(context)
        return StateDecision(
            transition=TransitionRequest(action="RESUME", reason_code="UNBLOCKED")
        )


class CancelledState(_ConcreteWorkflowState):
    name = "CANCELLED"


class FailedState(_ConcreteWorkflowState):
    name = "FAILED"


class PersistenceDegradedState(_ConcreteWorkflowState):
    name = "PERSISTENCE_DEGRADED"
    supported_actions = ("RESUME", "CANCEL")
    requires_resume_state = True

    def on_resume(self, context: WorkflowContext) -> StateDecision:
        self._require_current_state(context)
        self._require_recorded_resume_state(context)
        return StateDecision(
            transition=TransitionRequest(
                action="RESUME",
                reason_code="PERSISTENCE_RECOVERED",
            )
        )


def _task_graph_projection(
    graph: object,
) -> tuple[tuple[ReviewTaskItem, ...] | None, tuple[ReviewTaskGroup, ...] | None]:
    """Project a solver task graph into immutable Menxia scheduling data."""

    if not isinstance(graph, Mapping):
        return None, None
    raw_items = graph.get("candidate_items") or graph.get("items") or []
    raw_groups = graph.get("candidate_groups") or graph.get("groups") or []
    if not isinstance(raw_items, list) or not isinstance(raw_groups, list):
        raise InvariantViolation("task graph items and groups must be arrays")
    group_by_item: dict[str, str] = {}
    groups: list[ReviewTaskGroup] = []
    for index, raw_group in enumerate(raw_groups):
        if not isinstance(raw_group, Mapping):
            raise InvariantViolation("task graph group must be an object")
        group_id = str(raw_group.get("candidate_group_id") or raw_group.get("group_id") or "").strip()
        if not group_id:
            continue
        related = raw_group.get("related_items") or raw_group.get("items") or []
        if not isinstance(related, list):
            raise InvariantViolation("task graph group items must be an array")
        item_ids = tuple(
            str(item.get("item_id") or item.get("task_id") or "")
            if isinstance(item, Mapping) else str(item)
            for item in related
        )
        group_by_item.update({item_id: group_id for item_id in item_ids if item_id})
        groups.append(
            ReviewTaskGroup(
                group_id=group_id,
                item_ids=tuple(item_id for item_id in item_ids if item_id),
                order=int(raw_group.get("suggested_order", index) or index),
            )
        )
    items: list[ReviewTaskItem] = []
    for index, raw_item in enumerate(raw_items):
        if not isinstance(raw_item, Mapping):
            raise InvariantViolation("task graph item must be an object")
        item_id = str(raw_item.get("item_id") or raw_item.get("task_id") or "").strip()
        if not item_id:
            continue
        dependencies = raw_item.get("dependencies", [])
        if isinstance(dependencies, str):
            dependencies = [dependencies]
        if not isinstance(dependencies, list):
            raise InvariantViolation("task graph item dependencies must be an array")
        items.append(
            ReviewTaskItem(
                item_id=item_id,
                group_id=str(raw_item.get("group_id") or group_by_item.get(item_id) or "group-001"),
                dependencies=tuple(sorted({str(value) for value in dependencies if str(value)})),
                order=index,
            )
        )
    if not items and not groups:
        return None, None
    if not groups:
        by_group: dict[str, list[str]] = {}
        for item in items:
            by_group.setdefault(item.group_id, []).append(item.item_id)
        groups = [
            ReviewTaskGroup(group_id=group_id, item_ids=tuple(item_ids), order=index)
            for index, (group_id, item_ids) in enumerate(sorted(by_group.items()))
        ]
    return tuple(items) or None, tuple(groups) or None


class StateRegistry:
    """Read-only lookup of one concrete object per declared state."""

    def __init__(self, states: Mapping[str, WorkflowState]) -> None:
        state_map = dict(states)
        if set(state_map) != set(ALL_STATES):
            missing = sorted(set(ALL_STATES) - set(state_map))
            extra = sorted(set(state_map) - set(ALL_STATES))
            raise InvariantViolation(
                f"state registry must cover ALL_STATES; missing={missing}, extra={extra}"
            )
        for name, state in state_map.items():
            if state.name != name:
                raise InvariantViolation(
                    f"state registry key {name!r} does not match object name {state.name!r}"
                )
        self._states = MappingProxyType(state_map)

    @classmethod
    def default(cls) -> "StateRegistry":
        """Create the complete business/system state object registry."""

        state_objects: tuple[WorkflowState, ...] = (
            RequestIntakeState(),
            ZhongshuAnalystState(),
            ZhongshuSolverState(),
            ZhongshuCriticState(),
            ZhongshuFreezeCheckState(),
            MenxiaItemSolverState(),
            MenxiaItemAnalystState(),
            MenxiaItemCriticState(),
            MenxiaGroupGateState(),
            DoneState(),
            HumanGateWorkflowState(),
            RetryWaitState(),
            BlockedState(),
            CancelledState(),
            FailedState(),
            PersistenceDegradedState(),
        )
        return cls({state.name: state for state in state_objects})

    def get(self, state_name: str) -> WorkflowState:
        try:
            return self._states[state_name]
        except KeyError as error:
            raise InvariantViolation(f"unknown workflow state {state_name!r}") from error

    def state_for(self, state_name: str) -> WorkflowState:
        """Named alias for callers that prefer domain terminology."""

        return self.get(state_name)

    def names(self) -> tuple[str, ...]:
        return tuple(state.name for state in self._states.values())

    def __contains__(self, state_name: object) -> bool:
        return state_name in self._states

    def __len__(self) -> int:
        return len(self._states)


__all__ = [
    "BlockedState",
    "CancelledState",
    "DoneState",
    "FailedState",
    "HumanGateWorkflowState",
    "MenxiaGroupGateState",
    "MenxiaItemAnalystState",
    "MenxiaItemCriticState",
    "MenxiaItemSolverState",
    "PersistenceDegradedState",
    "RequestIntakeState",
    "RetryWaitState",
    "StateRegistry",
    "WorkflowState",
    "ZhongshuAnalystState",
    "ZhongshuCriticState",
    "ZhongshuFreezeCheckState",
    "ZhongshuSolverState",
]
