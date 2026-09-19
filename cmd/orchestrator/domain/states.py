"""Pure concrete state objects for the linear workflow.

The objects in this module are domain policies only.  They create typed
``StateDecision`` values and never perform I/O, persistence, transport, or
concurrency work.  The runtime executes the returned effect intents through
typed ports after the decision has been durably committed.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, replace
import logging
from types import MappingProxyType
from typing import ClassVar, Protocol, runtime_checkable

from ..zhongshu_parallel import canonical_plan_hash
from ..zhongshu_review_queue import build_review_jobs, structural_gate
from ..zhongshu_solver_contract import ZHONGSHU_SOLVER_MAX_FINDINGS_PER_ROUND
from ..acceptance_standards import acceptance_standard_hint
from ..dispatch_envelope import build_envelope
from .context import (
    DeliveryUpdate,
    HumanGateUpdate,
    ParallelUpdate,
    ProgressUpdate,
    RecoveryUpdate,
    ReviewUpdate,
    ReviewState,
    ReviewTaskGroup,
    ReviewTaskItem,
    ReviewTaskRecord,
    WorkflowContext,
    apply_review_update,
)
from .decisions import ContextUpdate, EffectRequest, StateDecision
from .errors import (
    FailureRecord,
    InvariantViolation,
    is_infrastructure_failure,
    is_reply_failure,
)
from .events import DomainEvent, TransitionRequest
from .policies.solver_plan import is_retryable_solver_reply_error
from .policies.item_workflows import update_item_workflows
from .policies.zhongshu import (
    APPROVAL_ACTIONS,
    FREEZE_RETRY_ACTIONS,
    REVISION_ACTIONS,
    age_unresolved_findings,
    blocker_fingerprint,
    freeze_retry_allowed,
    merge_findings,
    revision_allowed,
)
from .policies.prompts import build_prompt
from .transitions import ALL_STATES, BUSINESS_STATES, SYSTEM_STATES, TransitionRegistry
from .zhongshu import (
    GateVerdict,
    SolverStage,
    build_solver_dispatch,
    evaluate_gate,
    fold_round,
    mark_followups,
    plan_artifact_effect,
    process_solver_reply,
    resolve_dispatch_stage,
)
from .zhongshu.solver import revision_scope as _revision_editable_item_ids
from .policies.item_revise import (
    contested_item_ids,
    item_findings,
    plan_item,
)


def _finding_payload(finding: object) -> dict[str, object]:
    """Serialize one finding for the Solver prompt context."""

    to_dict = getattr(finding, "to_dict", None)
    if callable(to_dict):
        return dict(to_dict())
    if isinstance(finding, Mapping):
        return dict(finding)
    return {}


def _resolve_plan_hash(payload: Mapping[str, object]) -> str | None:
    raw = payload.get("plan_hash") or payload.get("reviewed_plan_hash") or ""
    if raw:
        return str(raw)
    plan = payload.get("plan")
    if isinstance(plan, dict) and plan.get("items"):
        return canonical_plan_hash(plan)
    return None


# A worker reply that is contract-valid but unusable as a per-task verdict
# (bad identity/format).  It is retried on the dedicated reply budget rather
# than consuming the shared transient-failure budget.
_TASK_REVIEW_RESULT_INVALID = "NODE_TASK_REVIEW_RESULT_INVALID"


def _failure_error_code(payload: Mapping[str, object]) -> str:
    """Best-effort error code from a FAIL event payload."""

    raw = payload.get("failure")
    if isinstance(raw, Mapping):
        return str(raw.get("error_code") or "")
    return str(payload.get("error_code") or "")


def _failure_budget(error_code: object) -> str:
    """Return the retry budget that pays for one retryable failure.

    Routing and accounting both read this so they cannot disagree about which
    counter a retry charged; keeping them separate let a reply failure be routed
    to one budget while incrementing another.
    """

    code = str(error_code or "").strip()
    if code == _TASK_REVIEW_RESULT_INVALID or is_reply_failure(code):
        return "reply"
    if is_infrastructure_failure(code):
        return "external"
    return "convergence"


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

logger = logging.getLogger("review_orchestrator_fsm")


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
        ):
            error_code = _failure_error_code(event.payload)
            budget = _failure_budget(error_code)
            if budget == "reply":
                # A worker's or the agent's reply was unusable (format/shape),
                # not a real content disagreement.  Re-ask it on a dedicated,
                # bounded budget so a single bad reply does not escalate straight
                # to a human, while still refusing to loop forever.  Keeping this
                # off the runtime budget matters: an unrelated backend outage can
                # drain that budget first, and a reply syntax slip must still get
                # its own retry instead of failing the whole run.
                action = (
                    "RETRY"
                    if context.recovery.reply_retry_count
                    < context.recovery.max_reply_retries
                    else "HUMAN_GATE"
                )
            elif budget == "external":
                # The agent/transport runtime failed (remote run error, missing
                # result, timeout).  Retry on the external budget so backend
                # flakiness cannot exhaust the plan-convergence retries.
                if (
                    context.recovery.external_retry_count
                    < context.recovery.max_external_retries
                ):
                    action = "RETRY"
            elif context.recovery.retry_count < context.recovery.max_retries:
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
            failure = (
                update.recovery.last_failure
                if update.recovery is not None
                else self._failure_from_payload(context, payload)
            )
            budget = _failure_budget(
                failure.error_code if failure is not None else ""
            )
            if budget == "reply":
                recovery = RecoveryUpdate(
                    reply_retry_count=context.recovery.reply_retry_count + 1,
                    last_failure=failure,
                )
            elif budget == "external":
                recovery = RecoveryUpdate(
                    external_retry_count=context.recovery.external_retry_count + 1,
                    last_failure=failure,
                )
            else:
                recovery = RecoveryUpdate(
                    retry_count=context.recovery.retry_count + 1,
                    last_failure=failure,
                )
            update = replace(update, recovery=recovery)
        if event.name == "NODE_COMPLETED" and action not in {
            "RETRY", "BLOCK", "BLOCKED", "OPEN_HUMAN_GATE", "HUMAN_GATE",
        }:
            # A clean join pays the wave's reply debt: the reply-retry budget
            # is scoped to one dispatch wave, not a whole-run allowance, or a
            # late-phase role inherits the retries an earlier role already
            # spent (Analyst slips would drain the Critic's first wave).
            recovery = update.recovery or RecoveryUpdate()
            update = replace(
                update,
                recovery=replace(recovery, reply_retry_count=0),
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
            pending_requirements: tuple[dict[str, object], ...] = ()
            if update.review is not None and update.review.requirements is not None:
                pending_requirements = update.review.requirements
            elif context.review is not None:
                pending_requirements = context.review.requirements
            effective_review = apply_review_update(context.review, update.review)
            pending_review = None
            if (
                effective_review is not None
                and update.review is not None
                and update.review.task_items is not None
            ):
                pending_review = effective_review
            effect = self._dispatch_effect(
                context,
                target,
                revision_id=(
                    str(payload.get("revision_id") or "")
                    or (context.review.revision_id if context.review else "")
                ),
                plan_hash=(
                    _resolve_plan_hash(payload)
                    or (context.review.plan_hash if context.review else "")
                    or ""
                ),
                canonical_requirements=pending_requirements,
                pending_review=pending_review,
                effective_review=effective_review,
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
        raw_requirements = payload.get("requirements")
        has_review_data = any(
            key in payload
            for key in (
                "findings",
                "requirements",
                "plan",
                "plan_ref",
                "plan_hash",
                "task_graph_ref",
                "task_graph",
                "group_id",
                "item_id",
                "completed_item_id",
                # A failed task-review node still carries the verdicts its
                # successful workers produced; folding them into the ledger
                # here lets the retry re-dispatch only the missing tasks.
                "task_reviews",
            )
        )
        if not has_review_data:
            return None
        if raw_findings is not None and not isinstance(raw_findings, list):
            raise InvariantViolation("findings payload must be an array")
        requirements: tuple[dict[str, object], ...] | None = None
        if raw_requirements is not None:
            if not isinstance(raw_requirements, list):
                raise InvariantViolation("requirements payload must be an array")
            requirements = tuple(
                dict(item) for item in raw_requirements if isinstance(item, Mapping)
            )
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
        # A discarded task leaves the plan.  Its leftovers must leave with it,
        # or the stale ledger row keeps re-escalating ITEM_STALLED forever and
        # its open findings keep the graph "blocked" for an item no job will
        # ever review again.
        discarded_items = _discarded_item_ids(context, task_items)
        ledger_value = _task_review_ledger_update(context, payload)
        if discarded_items:
            if ledger_value is None:
                ledger_value = (
                    tuple(context.review.task_review_ledger) if context.review else ()
                )
            ledger_value = tuple(
                record
                for record in ledger_value
                if record.item_id not in discarded_items
            )
        workflows_value = update_item_workflows(
            context.review.item_workflows if context.review else (),
            payload.get("task_reviews"),
            attempted_item_ids=(
                context.review.attempted_item_ids if context.review else None
            ),
            max_rounds=(
                context.review.max_item_revision_rounds if context.review else 0
            ),
            failed_round=(str(payload.get("action") or "") == "FAIL"),
        )
        if discarded_items:
            if workflows_value is None:
                workflows_value = (
                    tuple(context.review.item_workflows) if context.review else ()
                )
            workflows_value = tuple(
                workflow
                for workflow in workflows_value
                if workflow.item_id not in discarded_items
            )
        if discarded_items:
            workflows_value = tuple(
                workflow
                for workflow in workflows_value
                if workflow.item_id not in discarded_items
            )
        merged_findings = _merge_and_close_findings(
            existing,
            findings,
            payload.get("task_reviews"),
            attempted_finding_ids=(
                context.review.attempted_finding_ids if context.review else None
            ),
        )
        if discarded_items:
            merged_findings = tuple(
                replace(
                    finding,
                    status="WONT_FIX",
                    resolution=finding.resolution
                    or "task discarded from plan; requirement scoped out with rationale",
                )
                if (finding.active and finding.item_id in discarded_items)
                else finding
                for finding in merged_findings
            )
        return ReviewUpdate(
            revision_id=(
                str(payload.get("revision_id") or "")
                or (context.review.revision_id if context.review else "")
                or f"{context.identity.task_id}:{context.progression.sequence + 1}"
            ),
            task_review_ledger=ledger_value,
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
            findings=merged_findings,
            plan_ref=str(plan_ref) if plan_ref else None,
            plan_hash=_resolve_plan_hash(payload),
            plan=dict(plan) if isinstance(plan, Mapping) else None,
            task_graph_ref=str(task_graph_ref) if task_graph_ref else None,
            task_items=task_items,
            task_groups=task_groups,
            completed_item_ids=tuple(completed),
            attempted_item_ids=_attempted_item_ids(existing, payload),
            attempted_finding_ids=_attempted_finding_ids(payload, existing),
            item_workflows=workflows_value,
            evidence_packet=(
                dict(payload["evidence_packet"])
                if isinstance(payload.get("evidence_packet"), Mapping)
                else None
            ),
            requirements=requirements,
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
        canonical_requirements: tuple[dict[str, object], ...] = (),
        pending_review: object | None = None,
        effective_review: ReviewState | None = None,
    ) -> EffectRequest:
        # Dispatch must observe the review as it will be *after* this decision's
        # update commits, not the pre-transition snapshot.  Otherwise the Critic's
        # verdict produced by the very event being handled never reaches the
        # outgoing Solver prompt (it saw ``Current review finding count: 0`` and
        # returned an unchanged plan every round).
        if effective_review is not None:
            context = replace(context, review=effective_review)
        role, phase = _ROLE_BY_STATE[target]
        request_id = f"{context.identity.task_id}:{target}:{context.progression.sequence + 1}"
        prompt = build_prompt(context, target_state=target)
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
        if target == "ZHONGSHU_CRITIC":
            task_bindings = _task_review_bindings(
                context, revision_id, plan_hash, pending_review
            )
            if task_bindings:
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
                        "dispatch_mode": "task_review",
                        "bindings": task_bindings,
                        "previous_review": _previous_review_snapshot(context),
                        "group_id": None,
                        "item_id": None,
                    },
                )
        if target == "ZHONGSHU_ANALYST" and not canonical_requirements:
            # First Zhongshu pass: extract the authoritative requirement contract
            # before any evidence lens runs, so the lenses never invent their own
            # (colliding) requirement ids.
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
                    "dispatch_context": {
                        "zhongshu_dispatch_mode": "requirement_contract",
                        "contract_mode": True,
                        "envelope": build_envelope(
                            ingredients=[
                                {"key": "request.raw_request", "source": "prompt.txt",
                                 "lifetime": "persisted"},
                            ],
                            tools={"editable": "none", "contract": "requirement_contract"},
                            product={"type": "requirement_contract",
                                     "contract": "requirement_contract"},
                        ),
                    },
                    "group_id": None,
                    "item_id": None,
                },
            )
        if worker_count > 1:
            contract_payload = (
                [dict(item) for item in canonical_requirements]
                if canonical_requirements
                else []
            )
            bindings = []
            for index in range(1, worker_count + 1):
                binding: dict[str, object] = {
                    "worker_id": f"{target.lower()}-worker-{index:02d}",
                    "agent_id": f"{phase.lower()}-{role.split('-')[-1]}-{index:02d}",
                    "task_id": context.identity.task_id,
                    "request_id": f"{request_id}:worker-{index:02d}",
                    "role": role,
                    "phase": phase,
                }
                if target == "ZHONGSHU_ANALYST" and contract_payload:
                    binding["dispatch_context"] = {
                        "requirement_contract": contract_payload,
                        "envelope": build_envelope(
                            ingredients=[
                                {"key": "requirement_contract", "source": "context.json",
                                 "lifetime": "persisted"},
                                {"key": "request.raw_request", "source": "prompt.txt",
                                 "lifetime": "persisted"},
                            ],
                            tools={"editable": "none", "contract": "evidence_lens"},
                            product={"type": "evidence_updates",
                                     "contract": "evidence_lens"},
                        ),
                    }
                bindings.append(binding)
            payload: dict[str, object] = {
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
            }
            if target == "ZHONGSHU_ANALYST" and contract_payload:
                payload["canonical_requirements"] = contract_payload
            return EffectRequest(
                effect_id=f"node:{request_id}",
                effect_type="node_dispatch",
                task_id=context.identity.task_id,
                idempotency_key=request_id,
                payload_ref=context.request.payload_ref,
                payload=payload,
            )
        agent_id = f"{phase.lower()}-{role.split('-')[-1]}"
        if target == "ZHONGSHU_SOLVER":
            # A2/P3: with the item workflow enabled, a revision edge fans one
            # Solver worker out per contested item instead of routing the
            # whole batch through the single writer.  Formalization stays
            # single-writer, and the flag keeps the legacy path available.
            if (
                context.parallel is not None
                and context.parallel.zhongshu.item_workflow_enabled
                and resolve_dispatch_stage(context.progression.state)
                is SolverStage.REVISE
            ):
                item_bindings = _item_revise_bindings(
                    context, revision_id, plan_hash, effective_review
                )
                if item_bindings:
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
                            "dispatch_mode": "item_revise",
                            "bindings": item_bindings,
                            "current_formal_plan": (
                                dict(context.review.plan)
                                if context.review is not None
                                and isinstance(context.review.plan, Mapping)
                                else None
                            ),
                            "group_id": None,
                            "item_id": None,
                        },
                    )
            # The Solver's two input channels are built in the zhongshu
            # package, one builder per stage; the stage is decided by the
            # transition edge, not by payload shape.
            return build_solver_dispatch(
                context,
                request_id=request_id,
                prompt=prompt,
                revision_id=revision_id,
                plan_hash=plan_hash,
                next_item=next_item,
            )
        payload: dict[str, object] = {
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
        }
        return EffectRequest(
            effect_id=f"dispatch:{request_id}",
            effect_type="agent_dispatch",
            task_id=context.identity.task_id,
            idempotency_key=request_id,
            payload_ref=context.request.payload_ref,
            payload=payload,
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

    def _blocked_decision(
        self,
        context: WorkflowContext,
        event: DomainEvent,
        reason: str,
    ) -> StateDecision:
        """Stop an unbounded convergence loop and record why for the operator."""

        decision = self._transition_decision(context, event, action="BLOCKED")
        return replace(
            decision,
            transition=TransitionRequest(action="BLOCKED", reason_code=reason),
            update=replace(
                decision.update,
                recovery=RecoveryUpdate(blocked_reason=reason),
            ),
        )

    def _human_gate_decision(
        self,
        context: WorkflowContext,
        event: DomainEvent,
        reason: str,
    ) -> StateDecision:
        """Hand the workflow to an operator with an explicit reason code."""

        decision = self._transition_decision(context, event, action="HUMAN_GATE")
        human_gate = decision.update.human_gate
        if human_gate is not None:
            human_gate = replace(human_gate, reason_code=reason)
        return replace(
            decision,
            transition=TransitionRequest(action="HUMAN_GATE", reason_code=reason),
            update=replace(
                decision.update,
                human_gate=human_gate,
                recovery=RecoveryUpdate(blocked_reason=reason),
            ),
        )

    def _stuck_finding_decision(
        self,
        context: WorkflowContext,
        event: DomainEvent,
    ) -> StateDecision:
        """Escalate a finding the Solver loop cannot resolve to a human."""

        return self._human_gate_decision(context, event, self.stuck_finding_reason)

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
    invalid_reason = "ZHONGSHU_SOLVER_REVISION_INVALID"

    def handle(self, context: WorkflowContext, event: DomainEvent) -> StateDecision:
        self._require_current_state(context)
        self._require_event_task(context, event)
        action = self._event_action(event)
        plan_effect: EffectRequest | None = None
        materialized_plan: Mapping[str, object] | None = None
        if (
            event.name == "NODE_COMPLETED"
            and action == "READY_FOR_CRITIC"
            and isinstance(event.payload, Mapping)
            and isinstance(event.payload.get("plan"), Mapping)
        ):
            # A2/P3 item-revise node: the joiner already materialized and
            # merged the per-item patches, so the reply validator must not
            # re-run; persist the merged plan and hash the artifact.
            payload = dict(event.payload)
            event = replace(
                event,
                payload={
                    **payload,
                    "changes": [],
                },
            )
            materialized_plan = payload["plan"]
            plan_effect = plan_artifact_effect(
                context.identity.task_id,
                context.progression.sequence,
                payload["plan"],
                str(payload.get("revision_id") or ""),
            )
        elif event.name != "TIMEOUT" and action == "READY_FOR_CRITIC":
            payload = dict(event.payload) if isinstance(event.payload, Mapping) else {}
            # Stage routing lives in the zhongshu package: the formalize
            # (Analyst) and revise (Critic) channels validate and materialize
            # through separate logic classes.
            outcome = process_solver_reply(payload, context.review)
            if outcome.error:
                if (
                    is_retryable_solver_reply_error(outcome.error)
                    and context.recovery.reply_retry_count
                    < context.recovery.max_reply_retries
                ):
                    return self._retry_solver_reply(context, event, outcome.error)
                logger.warning(
                    "ZHONGSHU_SOLVER_REVISION_REJECTED task_id=%s error=%s",
                    context.identity.task_id,
                    outcome.error,
                )
                return self._blocked_decision(context, event, self.invalid_reason)
            if outcome.materialized is not None:
                # Replace the reply with the orchestrator-owned full plan so
                # the review projection, plan hash, and next revision all use
                # the materialized graph rather than a raw patch.
                clean_payload = {
                    key: value
                    for key, value in outcome.payload.items()
                    if key not in {"plan_hash", "reviewed_plan_hash"}
                }
                event = replace(
                    event,
                    payload={
                        **clean_payload,
                        "plan": outcome.materialized,
                        "changes": [],
                    },
                )
                materialized_plan = outcome.materialized
                plan_effect = plan_artifact_effect(
                    context.identity.task_id,
                    context.progression.sequence,
                    outcome.materialized,
                    str(outcome.payload.get("revision_id") or ""),
                )
        decision = super().handle(context, event)
        if plan_effect is not None:
            decision = replace(decision, effects=(plan_effect, *decision.effects))
        if decision.transition.action == "READY_FOR_CRITIC" and materialized_plan is not None:
            removals = _must_requirement_removals(context, materialized_plan)
            if removals:
                # The Solver executed a discard recommendation against a task
                # that covers a must-priority requirement.  The pruned plan is
                # folded and persisted, but progress to the Critic waits for
                # the operator: dropping part of what the user explicitly
                # asked for is a scope decision, not a review verdict.
                logger.warning(
                    "ZHONGSHU_DISCARD_NEEDS_HUMAN task_id=%s removals=%s",
                    context.identity.task_id,
                    removals,
                )
                decision = self._human_gate_decision(
                    context, event, "ZHONGSHU_DISCARD_NEEDS_HUMAN"
                )
                decision = replace(decision, effects=(plan_effect, *decision.effects))
        return decision

    def _retry_solver_reply(
        self,
        context: WorkflowContext,
        event: DomainEvent,
        error: str,
    ) -> StateDecision:
        """Bounce a mechanically-invalid Solver reply back for one more attempt.

        ``RETRY`` parks the task in the internal ``RETRY_WAIT`` state, which the
        runner auto-resumes to the recorded ``resume_state`` (this state), so a
        fresh Solver dispatch is issued without involving a human.  The attempt
        is charged to the dedicated reply budget (shared with the Critic's
        unusable-reply path) so a run of protocol slips cannot spend the
        plan-convergence retries a real content disagreement still needs.
        """

        logger.warning(
            "ZHONGSHU_SOLVER_REPLY_RETRY task_id=%s error=%s retry=%s/%s",
            context.identity.task_id,
            error,
            context.recovery.reply_retry_count + 1,
            context.recovery.max_reply_retries,
        )
        decision = self._transition_decision(context, event, action="RETRY")
        failure = FailureRecord(
            failure_id=(
                f"solver-reply:{context.identity.task_id}:"
                f"{context.progression.sequence}"
            ),
            stage="agent_reply",
            owner_component=self.name,
            task_id=context.identity.task_id,
            state=self.name,
            sequence=context.progression.sequence,
            node_run_id=context.parallel.active_node_run_id,
            worker_id=None,
            effect_id=None,
            error_code=error,
            retryable=True,
            message=error,
            cause_type="AgentReply",
        )
        return replace(
            decision,
            update=replace(
                decision.update,
                recovery=RecoveryUpdate(
                    reply_retry_count=context.recovery.reply_retry_count + 1,
                    last_failure=failure,
                ),
            ),
        )


class ZhongshuCriticState(_ConcreteWorkflowState):
    name = "ZHONGSHU_CRITIC"
    supported_actions = (
        "APPROVE_CRITIC", "APPROVE_FREEZE", "REQUEST_ANALYST_EVIDENCE",
        "REQUEST_SOLVER_REVISION", "REQUEST_REGROUP", "TASK_APPROVED",
        "TASK_CHANGES_REQUIRED", "HUMAN_GATE", "BLOCKED", "RETRY", "OPEN_HUMAN_GATE",
    )
    exhausted_reason = "ZHONGSHU_REVISION_BUDGET_EXHAUSTED"
    no_progress_reason = "ZHONGSHU_NO_PROGRESS"
    stuck_finding_reason = "ZHONGSHU_STUCK_FINDING"
    freeze_incomplete_reason = "ZHONGSHU_FREEZE_INCOMPLETE"
    item_stalled_reason = "ZHONGSHU_ITEM_STALLED"
    followup_reason = "ZHONGSHU_FREEZE_WITH_FOLLOWUPS"

    def handle(self, context: WorkflowContext, event: DomainEvent) -> StateDecision:
        self._require_current_state(context)
        self._require_event_task(context, event)
        action = self._event_action(event)
        if event.name != "TIMEOUT" and action in APPROVAL_ACTIONS:
            decision = self._transition_decision(context, event, action=action)
            round_after = self._round_after(context, decision)
            pending = round_after.unapproved_item_ids
            if not pending:
                return decision
            # A review round re-reviews only the tasks that are unapproved or
            # whose content changed, so this round's verdicts can be a strict
            # subset of the graph.  Freezing on that subset would release
            # unreviewed work to Menxia, so the freeze is held back.
            if not round_after.active_blockers:
                logger.warning(
                    "ZHONGSHU_FREEZE_INCOMPLETE task_id=%s pending_items=%s",
                    context.identity.task_id,
                    list(pending),
                )
                return self._human_gate_decision(
                    context, event, self.freeze_incomplete_reason
                )
            logger.warning(
                "ZHONGSHU_APPROVAL_HELD task_id=%s pending_items=%s blockers=%s",
                context.identity.task_id,
                list(pending),
                round_after.active_blockers,
            )
            action = "REQUEST_SOLVER_REVISION"
        if event.name != "TIMEOUT" and action in REVISION_ACTIONS:
            if not revision_allowed(context.review):
                # The bounded Zhongshu revision budget is spent.  A further
                # revision request must not re-dispatch the Solver forever:
                # stop the loop and record the unresolved divergence so an
                # operator can intervene instead of burning agent cycles.
                return self._blocked_decision(context, event, self.exhausted_reason)
            decision = self._transition_decision(context, event, action=action)
            if context.review is not None:
                round_after = self._round_after(context, decision)
                verdict = self._gate_verdict(context, round_after)
                gate_decision = self._apply_gate_verdict(
                    context, event, decision, round_after, verdict, action
                )
                if gate_decision is not None:
                    return gate_decision
                review_update = decision.update.review
                if review_update is None:
                    review_update = ReviewUpdate(revision_id=context.review.revision_id)
                decision = replace(
                    decision,
                    update=replace(
                        decision.update,
                        review=replace(
                            review_update,
                            zhongshu_revision_round=(
                                context.review.zhongshu_revision_round + 1
                            ),
                            last_reply_fingerprint=blocker_fingerprint(
                                round_after.findings
                            ),
                        ),
                        recovery=RecoveryUpdate(
                            no_progress_count=verdict.no_progress_count,
                            # This branch rebuilds the recovery update from
                            # scratch, so the per-wave reply-debt reset from
                            # _transition_decision must be restated here.
                            reply_retry_count=0,
                        ),
                    ),
                )
            return decision
        return super().handle(context, event)

    def _round_after(self, context: WorkflowContext, decision: StateDecision):
        """Aggregated round the decision would persist (see ``zhongshu.critic``)."""

        review_update = decision.update.review
        ledger = (
            review_update.task_review_ledger
            if review_update is not None and review_update.task_review_ledger is not None
            else (tuple(context.review.task_review_ledger) if context.review else ())
        )
        findings = (
            review_update.findings
            if review_update is not None and review_update.findings is not None
            else (tuple(context.review.findings) if context.review else ())
        )
        items = (
            review_update.task_items
            if review_update is not None and review_update.task_items is not None
            else (context.review.task_items if context.review else ())
        )
        expected = tuple(
            dict.fromkeys(
                str(getattr(item, "item_id", "") or "").strip()
                for item in items or ()
                if str(getattr(item, "item_id", "") or "").strip()
            )
        )
        return fold_round(
            ledger=ledger,
            findings=findings,
            expected_item_ids=expected,
            max_item_rounds=(
                context.review.max_item_revision_rounds if context.review else 0
            ),
        )

    def _gate_verdict(self, context: WorkflowContext, round_after) -> GateVerdict:
        return evaluate_gate(
            round_after,
            revision_allowed=True,
            previous_fingerprint=(
                context.review.last_reply_fingerprint if context.review else None
            ),
            no_progress_count=context.recovery.no_progress_count,
            max_no_progress=context.recovery.max_no_progress,
            max_stuck_rounds=context.recovery.max_stuck_finding_rounds,
        )

    def _apply_gate_verdict(
        self,
        context: WorkflowContext,
        event: DomainEvent,
        decision: StateDecision,
        round_after,
        verdict: GateVerdict,
        action: str,
    ) -> StateDecision | None:
        """Render the gate verdict; ``None`` means the round proceeds."""

        if verdict.deferred_followup_unreviewed:
            logger.warning(
                "ZHONGSHU_FREEZE_WITH_FOLLOWUPS_DEFERRED task_id=%s unreviewed=%s",
                context.identity.task_id,
                list(verdict.deferred_followup_unreviewed),
            )
        if verdict.action == "APPROVE_CRITIC":
            # The aggregate asked for another revision, but every reviewed task
            # is already approved and no blocker survives the merge: there is
            # nothing left to revise, so freeze instead of spending a revision
            # round on a no-op.  An empty ledger is not evidence of approval,
            # so this only applies once per-task verdicts exist.
            logger.warning(
                "ZHONGSHU_FREEZE_WITHOUT_REVISION task_id=%s action=%s",
                context.identity.task_id,
                action,
            )
            return self._transition_decision(context, event, action="APPROVE_CRITIC")
        if verdict.action == "FREEZE_WITH_FOLLOWUPS":
            logger.warning(
                "ZHONGSHU_FREEZE_WITH_FOLLOWUPS task_id=%s p1_followups=%s",
                context.identity.task_id,
                list(verdict.followup_finding_ids),
            )
            freeze = self._transition_decision(context, event, action="APPROVE_CRITIC")
            review_update = freeze.update.review
            if review_update is None:
                review_update = ReviewUpdate(
                    revision_id=context.review.revision_id if context.review else ""
                )
            return replace(
                freeze,
                transition=TransitionRequest(
                    action="APPROVE_CRITIC", reason_code=self.followup_reason
                ),
                update=replace(
                    freeze.update,
                    review=replace(
                        review_update,
                        findings=mark_followups(
                            round_after.findings,
                            verdict.followup_finding_ids,
                            "冻结时接受为跟进项：预算内未收敛的 P1",
                        ),
                    ),
                ),
            )
        if verdict.action == "HUMAN_GATE":
            if verdict.reason_code == "ZHONGSHU_STUCK_FINDING":
                # The same finding survived several Solver revisions.  More
                # rounds are not evidence of convergence, so stop and hand
                # the unresolved finding to a human with an explicit reason.
                logger.warning(
                    "ZHONGSHU_STUCK_FINDING task_id=%s rounds=%s findings=%s",
                    context.identity.task_id,
                    context.recovery.max_stuck_finding_rounds,
                    list(verdict.stuck_finding_ids),
                )
                return self._stuck_finding_decision(context, event)
            # One task has been rejected for too many consecutive rounds while
            # the graph is otherwise progressing.  More graph-level rounds
            # would only hide it: hand the specific task to a human with an
            # explicit reason.
            logger.warning(
                "ZHONGSHU_ITEM_STALLED task_id=%s items=%s rounds=%s",
                context.identity.task_id,
                list(round_after.stalled_item_ids),
                context.review.max_item_revision_rounds if context.review else 0,
            )
            return self._human_gate_decision(context, event, self.item_stalled_reason)
        if verdict.action == "BLOCKED":
            return self._blocked_decision(context, event, verdict.reason_code)
        return None

class ZhongshuFreezeCheckState(_ConcreteWorkflowState):
    name = "ZHONGSHU_FREEZE_CHECK"
    supported_actions = (
        "FREEZE_APPROVED", "APPROVE_FREEZE", "FREEZE_OK", "FREEZE_REJECTED",
        "RETRY", "BLOCK", "OPEN_HUMAN_GATE",
    )
    exhausted_reason = "ZHONGSHU_FREEZE_BUDGET_EXHAUSTED"

    def handle(self, context: WorkflowContext, event: DomainEvent) -> StateDecision:
        self._require_current_state(context)
        self._require_event_task(context, event)
        action = self._event_action(event)
        if event.name != "TIMEOUT" and action in FREEZE_RETRY_ACTIONS:
            if not freeze_retry_allowed(context.review):
                # The bounded freeze-retry budget is spent: stop bouncing the
                # plan between freeze check and Solver and hand it to an
                # operator instead.
                return self._blocked_decision(context, event, self.exhausted_reason)
            decision = self._transition_decision(context, event, action=action)
            if context.review is not None:
                review_update = decision.update.review
                if review_update is None:
                    review_update = ReviewUpdate(revision_id=context.review.revision_id)
                decision = replace(
                    decision,
                    update=replace(
                        decision.update,
                        review=replace(
                            review_update,
                            freeze_check_attempt=(context.review.freeze_check_attempt + 1),
                        ),
                    ),
                )
            return decision
        return super().handle(context, event)


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


_EVIDENCE_RECORD_FIELDS = (
    "evidence_id", "requirement_id", "item_id", "decision_relevance",
    "conclusion", "source", "confidence",
)
_EVIDENCE_TEXT_LIMITS = {"conclusion": 400, "source": 200}
_MAX_ITEM_EVIDENCE_RECORDS = 8


def _item_evidence_context(
    review: object,
    item: ReviewTaskItem | None,
    expected_lenses: int,
) -> dict[str, object]:
    """Per-item slice of the Analyst evidence packet for one task-review job.

    The task-review Critic used to judge tasks with no investigation context at
    all, so whole rounds came back as ``REQUEST_ANALYST_EVIDENCE`` for evidence
    the run already possessed.  The binding therefore carries the item's slice:
    evidence records addressed to the item (or to one of its source
    requirements), the lens workers that contributed, and a gap marker when
    fewer lens workers delivered than were dispatched, so the Critic can weigh
    coverage instead of re-requesting the missing investigation.
    """

    packet = getattr(review, "evidence_packet", None)
    if not isinstance(packet, Mapping) or item is None:
        return {"records": [], "lens_workers": [], "lens_gaps": 0}
    records: list[dict[str, object]] = []
    raw_updates = packet.get("evidence_updates")
    if isinstance(raw_updates, list):
        for value in raw_updates:
            if not isinstance(value, Mapping):
                continue
            record_item = str(value.get("item_id") or "").strip()
            requirement = str(value.get("requirement_id") or "").strip()
            if record_item and record_item != item.item_id:
                continue
            if not record_item and requirement not in item.source_requirement_ids:
                continue
            record: dict[str, object] = {}
            for key in _EVIDENCE_RECORD_FIELDS:
                raw = value.get(key)
                if raw in (None, "", [], {}):
                    continue
                if isinstance(raw, str):
                    limit = _EVIDENCE_TEXT_LIMITS.get(key)
                    record[key] = (
                        " ".join(raw.split())[:limit] if limit else " ".join(raw.split())
                    )
                else:
                    record[key] = raw
            if record:
                records.append(record)
            if len(records) >= _MAX_ITEM_EVIDENCE_RECORDS:
                break
    worker_evidence = packet.get("worker_evidence")
    lens_workers = (
        sorted(str(key).strip() for key in worker_evidence if str(key).strip())
        if isinstance(worker_evidence, Mapping)
        else []
    )
    lens_gaps = max(0, expected_lenses - len(lens_workers)) if expected_lenses else 0
    return {"records": records, "lens_workers": lens_workers, "lens_gaps": lens_gaps}


def _item_revise_capsule(
    item: Mapping[str, object],
    findings: tuple[dict[str, object], ...],
) -> str:
    """Instruction capsule for one item-scoped Solver worker (A2/P3)."""

    item_id = str(item.get("item_id") or "")
    lines = [
        f"Item revision job: {item_id} in group {item.get('group_id') or '-'}.",
        f"Title: {item.get('title') or ''}",
        f"Objective: {item.get('objective') or ''}",
    ]
    dependencies = item.get("dependencies") or []
    if dependencies:
        lines.append(f"Dependencies: {', '.join(str(value) for value in dependencies)}")
    if findings:
        lines.append(f"Resolve these {len(findings)} findings for this item:")
        for finding in findings:
            demand = (
                str(finding.get("required_action") or "")
                or str(finding.get("claim") or "")
            )
            lines.append(
                f"- {finding.get('finding_id')} "
                f"[{finding.get('severity')}] {' '.join(demand.split())[:220]}"
            )
    lines.append(
        "Rewrite only this item so every finding is addressed; keep item_id and "
        "group_id unchanged, and rewrite acceptance signals observably (per-item "
        "verifiable, explicit units, cited evidence sources, UNKNOWN for runtime "
        "facts). Return the patched item object plus one finding_resolution per "
        "finding; do not touch other items."
    )
    return "\n".join(lines)


def _item_revise_bindings(
    context: WorkflowContext,
    revision_id: str,
    plan_hash: str,
    review_override: object | None = None,
) -> list[dict[str, object]] | None:
    """One Solver binding per contested item for the item-revise node."""

    review = review_override if review_override is not None else context.review
    if review is None:
        return None
    contested = contested_item_ids(review.task_review_ledger)
    if not contested:
        return None
    expected_lenses = (
        context.parallel.zhongshu.analyst_default_workers
        if context.parallel is not None
        else 0
    )
    base_request_id = f"{context.identity.task_id}:ZHONGSHU_SOLVER:{context.progression.sequence + 1}"
    bindings: list[dict[str, object]] = []
    for index, item_id in enumerate(contested, start=1):
        item = plan_item(review.plan, item_id)
        if item is None:
            continue
        findings = item_findings(review.findings, item_id)
        evidence = _item_evidence_context(
            review,
            type(
                "ItemShim",
                (),
                {
                    "item_id": item_id,
                    "source_requirement_ids": tuple(
                        str(value) for value in (item.get("source_requirement_ids") or ())
                    ),
                },
            )(),
            expected_lenses,
        )
        bindings.append(
            {
                "worker_id": f"zhongshu_solver-worker-{index:02d}",
                "agent_id": f"zhongshu-solver-{index:02d}",
                "task_id": context.identity.task_id,
                "request_id": f"{base_request_id}:worker-{index:02d}",
                "role": "review-solver",
                "phase": "ZHONGSHU",
                "group_id": str(item.get("group_id") or ""),
                "item_id": item_id,
                "prompt_ref": _item_revise_capsule(item, findings),
                "dispatch_context": {
                    "zhongshu_dispatch_mode": "item_revise",
                    "revision_id": revision_id,
                    "plan_hash": plan_hash,
                    "group_id": str(item.get("group_id") or ""),
                    "item_id": item_id,
                    "item": item,
                    "item_findings": list(findings),
                    "item_evidence": evidence["records"],
                    "envelope": build_envelope(
                        ingredients=[
                            {"key": "review.plan", "source": "context.json",
                             "lifetime": "persisted",
                             "slice": f"item:{item_id}"},
                            {"key": "review.findings", "source": "context.json",
                             "lifetime": "persisted",
                             "slice": f"item:{item_id}+active"},
                            {"key": "review.item_evidence", "source": "context.json",
                             "lifetime": "persisted",
                             "slice": f"item:{item_id}"},
                            {"key": "acceptance_standards", "source": "prompt.txt",
                             "lifetime": "persisted"},
                        ],
                        tools={"editable": f"items:{item_id}",
                               "contract": "nexus.zhongshu.item_solver.v1"},
                        product={"type": "item_patch",
                                 "contract": "nexus.zhongshu.item_solver.v1"},
                    ),
                },
            }
        )
    return bindings or None


def _previous_review_snapshot(context: WorkflowContext) -> dict[str, object] | None:
    """Dispatch-time snapshot of the prior review for the Critic fan-in.

    The join needs the previous round's findings and task-review rows to
    (a) stop worker-local finding ids from colliding with the ids the last
    round already stored and (b) explain why an approved task re-entered the
    contested set.  The snapshot is taken when the dispatch effect is built,
    so the fan-in sees exactly what the reviewed round started from.
    """

    review = context.review
    if review is None:
        return None
    findings = [asdict(finding) for finding in review.findings]
    ledger = [asdict(record) for record in review.task_review_ledger]
    if not findings and not ledger:
        return None
    return {"findings": findings, "task_review_ledger": ledger}


def _task_review_bindings(
    context: WorkflowContext,
    revision_id: str,
    plan_hash: str,
    review_override: object | None = None,
) -> list[dict[str, object]] | None:
    """Build one review binding per task item for ZHONGSHU task-queue mode.

    ``review_override`` lets the caller pass a review aggregate projected from
    the transition's own update (task_items learned in the same step), because
    ``context.review`` is still the pre-update snapshot when the effect is
    built.
    """

    review = review_override if review_override is not None else context.review
    if review is None or not review.task_items:
        return None
    revision = revision_id or review.revision_id
    if not revision:
        return None
    effective_plan_hash = plan_hash or (review.plan_hash or "")
    plan = {
        "items": [
            {
                "item_id": item.item_id,
                "group_id": item.group_id,
                "title": item.title,
                "objective": item.objective,
                "dependencies": list(item.dependencies),
                "source_requirement_ids": list(item.source_requirement_ids),
                "acceptance_signals": list(item.acceptance_signals),
            }
            for item in review.task_items
        ],
        "groups": [
            {"group_id": group.group_id, "item_ids": list(group.item_ids)}
            for group in review.task_groups
        ],
        "requirements": [],
    }
    queue = build_review_jobs(plan, revision, plan_hash=effective_plan_hash)
    issues = structural_gate(plan)
    logger.info(
        "ZHONGSHU_TASK_REVIEW_QUEUE task_id=%s revision_id=%s plan_hash=%s "
        "jobs=%s pending=%s structural_issues=%s",
        context.identity.task_id,
        revision,
        effective_plan_hash,
        len(queue.jobs),
        queue.counts().get("PENDING", 0),
        len(issues),
    )
    for issue in issues:
        logger.warning(
            "ZHONGSHU_STRUCTURE_GATE task_id=%s revision_id=%s issue=%s",
            context.identity.task_id,
            revision,
            issue,
        )
    selected = _select_review_jobs(queue.jobs, review)
    logger.info(
        "ZHONGSHU_TASK_REVIEW_SCOPE task_id=%s revision_id=%s total=%s "
        "to_review=%s carried=%s",
        context.identity.task_id,
        revision,
        len(queue.jobs),
        len(selected),
        len(queue.jobs) - len(selected),
    )
    item_by_id = {item.item_id: item for item in review.task_items}
    group_by_id = {group.group_id: group for group in review.task_groups}
    findings_by_item: dict[str, list[object]] = {}
    for finding in review.findings:
        if getattr(finding, "active", False) and getattr(finding, "item_id", ""):
            findings_by_item.setdefault(str(finding.item_id), []).append(finding)
    base_request_id = f"{context.identity.task_id}:ZHONGSHU_CRITIC:{context.progression.sequence + 1}"
    expected_lenses = (
        context.parallel.zhongshu.analyst_default_workers
        if context.parallel is not None
        else 0
    )
    bindings: list[dict[str, object]] = []
    for index, job in enumerate(selected, start=1):
        item = item_by_id.get(job.item_id)
        group = group_by_id.get(job.group_id)
        evidence = _item_evidence_context(review, item, expected_lenses)
        dispatch_context = {
            "zhongshu_dispatch_mode": "task_review",
            "review_job_id": job.review_job_id,
            "revision_id": revision,
            "plan_hash": effective_plan_hash,
            "group_id": job.group_id,
            "item_id": job.item_id,
            "task_hash": job.task_hash,
            "dependency_hash": job.dependency_hash,
            "structural_issues": issues,
            "active_findings": [
                _finding_payload(finding)
                for finding in findings_by_item.get(job.item_id, [])
            ],
            "item_evidence": evidence["records"],
            "envelope": build_envelope(
                ingredients=[
                    {"key": "task_capsule", "source": "prompt.txt", "lifetime": "persisted",
                     "slice": f"job:{job.review_job_id}"},
                    {"key": "review.item_evidence", "source": "context.json",
                     "lifetime": "persisted", "slice": f"item:{job.item_id}"},
                    {"key": "review.findings", "source": "context.json",
                     "lifetime": "persisted", "slice": f"item:{job.item_id}+active"},
                    {"key": "acceptance_standards", "source": "prompt.txt",
                     "lifetime": "persisted"},
                ],
                tools={"editable": "none", "contract": "task_review"},
                product={"type": "task_review_verdict", "contract": "task_review"},
            ),
        }
        if evidence["lens_gaps"]:
            dispatch_context["evidence_gaps"] = (
                f"expected {expected_lenses} analyst lens workers but evidence "
                f"covers {len(evidence['lens_workers'])}; coverage may be partial "
                f"({', '.join(evidence['lens_workers']) or 'none'} delivered)"
            )
        bindings.append(
            {
                "worker_id": f"zhongshu_critic-worker-{index:02d}",
                "agent_id": f"zhongshu-critic-{index:02d}",
                "task_id": context.identity.task_id,
                "request_id": f"{base_request_id}:worker-{index:02d}",
                "role": "review-critic",
                "phase": "ZHONGSHU",
                "group_id": job.group_id,
                "item_id": job.item_id,
                "prompt_ref": _task_capsule_text(
                    job, item, group, evidence["records"]
                ),
                "dispatch_context": dispatch_context,
            }
        )
    return bindings


def _select_review_jobs(
    jobs: object,
    review: object,
) -> list[object]:
    """Re-review a job only when its content, dependencies, or verdict changed."""

    ledger = {
        record.item_id: record for record in getattr(review, "task_review_ledger", ())
    }
    selected: list[object] = []
    for job in jobs:
        record = ledger.get(job.item_id)
        if (
            record is None
            or record.status != "APPROVED"
            or record.task_hash != job.task_hash
            or record.dependency_hash != job.dependency_hash
        ):
            selected.append(job)
    if not selected:
        return list(jobs)
    return selected


def _merge_and_close_findings(
    existing: tuple[object, ...],
    incoming: tuple[object, ...],
    task_reviews: object,
    *,
    attempted_finding_ids: Iterable[str] | None = None,
) -> tuple[object, ...]:
    """Merge findings and close those owned by a task the Critic approved."""

    merged = merge_findings(existing, incoming)
    if isinstance(task_reviews, list) and task_reviews:
        approved = {
            str(entry.get("item_id"))
            for entry in task_reviews
            if isinstance(entry, Mapping)
            and str(entry.get("action") or "") == "TASK_APPROVED"
            and entry.get("item_id")
        }
        if approved:
            merged = tuple(
                replace(
                    finding,
                    status="RESOLVED",
                    resolution=finding.resolution or "task approved in review",
                )
                if (finding.active and finding.item_id in approved)
                else finding
                for finding in merged
            )
    return age_unresolved_findings(
        existing,
        merged,
        incoming,
        attempted_finding_ids=attempted_finding_ids,
    )


def _attempted_finding_ids(
    payload: Mapping[str, object],
    existing: tuple[object, ...],
) -> tuple[str, ...] | None:
    """Findings the Solver was actually asked to resolve, for fair stuck counting.

    A revision was dictated a batch, so its echo names exactly the findings the
    round attempted.  A full re-plan (no batch) had a chance at every active
    finding.  ``None`` — payloads without Solver output — keeps the previously
    recorded attempt instead of pretending the round changed it.
    """

    batch = payload.get("finding_batch")
    if isinstance(batch, Mapping):
        selected = batch.get("selected_finding_ids")
        if isinstance(selected, list):
            return tuple(
                dict.fromkeys(
                    str(value).strip() for value in selected if str(value).strip()
                )
            )
        return None
    if payload.get("plan") is not None or payload.get("task_graph") is not None:
        return (
            tuple(
                dict.fromkeys(
                    str(getattr(finding, "finding_id", "") or "").strip()
                    for finding in existing or ()
                    if getattr(finding, "active", False)
                    and str(getattr(finding, "finding_id", "") or "").strip()
                )
            )
            or None
        )
    return None


def _attempted_item_ids(
    findings: tuple[object, ...],
    payload: Mapping[str, object],
) -> tuple[str, ...] | None:
    """Items a Solver revision was actually asked to fix.

    Taken from the revision's bounded finding batch: the Critic can only fairly
    count a rejection against a task the Solver was given a chance to repair.
    Returns ``None`` when the reply carries no batch, leaving the previously
    recorded attempt untouched instead of pretending nothing was attempted.
    """

    batch = payload.get("finding_batch")
    if not isinstance(batch, Mapping):
        return None
    selected = batch.get("selected_finding_ids")
    if not isinstance(selected, list):
        return None
    selected_ids = {str(value).strip() for value in selected if str(value).strip()}
    if not selected_ids:
        return ()
    item_of: dict[str, str] = {}
    for finding in findings or ():
        finding_id = str(getattr(finding, "finding_id", "") or "")
        if not finding_id:
            continue
        item_id = str(getattr(finding, "item_id", "") or "").strip()
        if not item_id:
            item_id = str(getattr(finding, "target", "") or "").strip().split("/")[-1]
        if item_id:
            item_of[finding_id] = item_id
    return tuple(
        dict.fromkeys(
            item_of[finding_id]
            for finding_id in sorted(selected_ids)
            if item_of.get(finding_id)
        )
    )


def _discarded_item_ids(
    context: WorkflowContext,
    new_task_items: object,
) -> frozenset[str]:
    """Task items the new plan dropped relative to the current state."""

    review = context.review
    if review is None or not review.task_items or not new_task_items:
        return frozenset()
    old_ids = {item.item_id for item in review.task_items}
    new_ids = {item.item_id for item in new_task_items}
    return frozenset(old_ids - new_ids)


def _must_requirement_removals(
    context: WorkflowContext,
    graph: Mapping[str, object],
) -> list[tuple[str, str]]:
    """Return (item_id, requirement_id) for must-covered items the plan drops.

    A discard recommendation executed by the Solver is a scope change.  When
    the removed task covers a must-priority requirement — something the user
    explicitly asked for — the removal must not reach the Critic unconfirmed.
    """

    review = context.review
    if review is None or not isinstance(graph, Mapping):
        return []
    old_items = {item.item_id: item for item in review.task_items}
    if not old_items:
        return []
    new_items, _ = _task_graph_projection(graph)
    removed = sorted(set(old_items) - {item.item_id for item in new_items})
    if not removed:
        return []
    requirements = {
        str(req.get("requirement_id")): req
        for req in (graph.get("requirements") or [])
        if isinstance(req, Mapping) and req.get("requirement_id")
    }
    if not requirements:
        # A revised graph may omit the requirement list; the authoritative
        # priorities live in the review state.
        requirements = {
            str(req.get("requirement_id")): dict(req)
            for req in (review.requirements or ())
            if isinstance(req, Mapping) and req.get("requirement_id")
        }
    pairs: list[tuple[str, str]] = []
    for item_id in removed:
        old_item = old_items[item_id]
        for requirement_id in old_item.source_requirement_ids:
            requirement = requirements.get(str(requirement_id)) or {}
            if str(requirement.get("priority") or "").strip().lower() == "must":
                pairs.append((item_id, str(requirement_id)))
    return pairs


def _task_review_ledger_update(
    context: WorkflowContext,
    payload: Mapping[str, object],
) -> tuple[ReviewTaskRecord, ...] | None:
    """Fold one round of per-task Critic verdicts into the review ledger.

    Approval is a ratchet: a task the Critic already accepted stays accepted
    while its reviewed content and dependency endpoints hash the same.  Without
    it a later round that re-dispatches the same task can demote an approval on
    a differently-worded but content-identical verdict, so the graph oscillates
    between APPROVED and CHANGES_REQUIRED and never reaches the freeze.

    The rejection counter only advances for tasks the Solver's last bounded
    finding batch actually asked to fix.  A batch covers part of the graph, so
    counting every verdict would let a task that was deferred to a later batch
    burn its stall budget while the Solver was never asked to touch it.

    A protocol-failed wave (aggregate action ``FAIL``) records verdicts for
    state-keeping only: the approval ratchet still lets the retry skip
    re-dispatching approved tasks, but the retry re-reviews non-approved tasks
    and charges them then -- so one worker's protocol slip must not spend
    other items' stall budget twice.
    """

    raw = payload.get("task_reviews")
    if not isinstance(raw, list) or not raw:
        return None
    failed_round = str(payload.get("action") or "") == "FAIL"
    current = {
        record.item_id: record
        for record in (
            context.review.task_review_ledger if context.review else ()
        )
    }
    attempted = {
        str(item).strip()
        for item in (
            context.review.attempted_item_ids if context.review else ()
        )
        if str(item).strip()
    }
    updated = False
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        item_id = str(entry.get("item_id") or "")
        if not item_id:
            continue
        action = str(entry.get("action") or "")
        task_hash = str(entry.get("reviewed_task_hash") or "")
        dependency_hash = str(entry.get("reviewed_dependency_hash") or "")
        previous = current.get(item_id)
        if (
            previous is not None
            and previous.status == "APPROVED"
            and previous.task_hash == task_hash
            and previous.dependency_hash == dependency_hash
        ):
            logger.info(
                "ZHONGSHU_TASK_APPROVAL_HELD task_id=%s item_id=%s action=%s",
                context.identity.task_id,
                item_id,
                action,
            )
            continue
        approved = action == "TASK_APPROVED"
        # An unknown attempt set (legacy state, or a round with no batch) keeps
        # the historical behaviour of counting every rejection.
        counted = not attempted or item_id in attempted
        if approved:
            rounds = 0
        elif failed_round:
            # The retry charges this task when its re-review joins cleanly;
            # folding the failed wave must not double-spend the budget.
            rounds = previous.changes_rounds if previous is not None else 0
            logger.info(
                "ZHONGSHU_TASK_REVIEW_FAILED_ROUND task_id=%s item_id=%s "
                "rounds=%s verdict=%s",
                context.identity.task_id,
                item_id,
                rounds,
                action,
            )
        elif counted:
            rounds = previous.changes_rounds + 1 if previous is not None else 1
        else:
            rounds = previous.changes_rounds if previous is not None else 0
            logger.info(
                "ZHONGSHU_TASK_STALL_DEFERRED task_id=%s item_id=%s rounds=%s",
                context.identity.task_id,
                item_id,
                rounds,
            )
        current[item_id] = ReviewTaskRecord(
            item_id=item_id,
            task_hash=task_hash,
            dependency_hash=dependency_hash,
            status=("APPROVED" if approved else "CHANGES_REQUIRED"),
            changes_rounds=rounds,
        )
        updated = True
    return tuple(current.values()) if updated else None


def _task_capsule_text(
    job: object,
    item: ReviewTaskItem | None,
    group: ReviewTaskGroup | None,
    evidence_records: object = (),
) -> str:
    lines = [
        f"Review job: {getattr(job, 'review_job_id', '')}",
        f"Review only task {getattr(job, 'item_id', '')} "
        f"in group {getattr(job, 'group_id', '')}.",
    ]
    if group is not None:
        lines.append(f"Group {group.group_id} items: {', '.join(group.item_ids)}")
    if item is not None:
        if item.title:
            lines.append(f"Task title: {item.title}")
        if item.objective:
            lines.append(f"Task objective: {item.objective}")
        if item.dependencies:
            lines.append(f"Dependencies: {', '.join(item.dependencies)}")
        if item.source_requirement_ids:
            lines.append(
                f"Source requirements: {', '.join(item.source_requirement_ids)}"
            )
        if item.acceptance_signals:
            lines.append(
                f"Acceptance signals: {'; '.join(item.acceptance_signals)}"
            )
    records = list(evidence_records or ())
    if records:
        lines.append(
            f"Investigation evidence ({len(records)} records) for this task is in "
            "dispatch_context.item_evidence; base your verdict on it instead of "
            "requesting evidence you already have."
        )
    lines.append(acceptance_standard_hint())
    lines.append(
        "Return TASK_APPROVED only when no active P0/P1 finding applies to this "
        "task. If the task itself is fine but the run lacks the investigation "
        "needed to judge it, return REQUEST_ANALYST_EVIDENCE instead: the "
        "Analyst collects evidence, while the Solver can only edit plan text "
        "and cannot manufacture evidence. Otherwise return "
        "TASK_CHANGES_REQUIRED with review_checks and task-scoped findings. "
        "Do not echo revision ids or hashes; the orchestrator stamps them."
    )
    return "\n".join(lines)


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
        related = (
            raw_group.get("related_items")
            or raw_group.get("items")
            or raw_group.get("item_ids")
            or []
        )
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
        source_requirements = raw_item.get("source_requirement_ids", [])
        if isinstance(source_requirements, str):
            source_requirements = [source_requirements]
        if not isinstance(source_requirements, list):
            raise InvariantViolation("task graph item source_requirement_ids must be an array")
        acceptance = raw_item.get("acceptance_signals", raw_item.get("acceptance", []))
        if isinstance(acceptance, str):
            acceptance = [acceptance]
        if not isinstance(acceptance, list):
            raise InvariantViolation("task graph item acceptance_signals must be an array")
        items.append(
            ReviewTaskItem(
                item_id=item_id,
                group_id=str(raw_item.get("group_id") or group_by_item.get(item_id) or "group-001"),
                dependencies=tuple(sorted({str(value) for value in dependencies if str(value)})),
                order=index,
                title=str(raw_item.get("title") or ""),
                objective=str(raw_item.get("objective") or ""),
                source_requirement_ids=tuple(
                    str(value) for value in source_requirements if str(value)
                ),
                acceptance_signals=tuple(
                    str(value) for value in acceptance if str(value)
                ),
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


