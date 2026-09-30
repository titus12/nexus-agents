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
from ..zhongshu_review_queue import (
    build_group_capsule,
    build_group_review_jobs,
    build_review_jobs,
    group_surface_hash,
    structural_gate,
)
from ..zhongshu_review_queue import _dependency_cycles
from ..zhongshu_solver_contract import ZHONGSHU_SOLVER_MAX_FINDINGS_PER_ROUND
from ..acceptance_standards import acceptance_standard_hint
from ..dispatch_envelope import build_envelope
from .context import (
    DeliveryUpdate,
    HumanGateUpdate,
    MenxiaGroupState,
    MenxiaItemState,
    ParallelUpdate,
    ProgressUpdate,
    RecoveryUpdate,
    ReviewUpdate,
    ReviewState,
    ReviewTaskGroup,
    ReviewTaskItem,
    ReviewTaskRecord,
    WorkflowContext,
    ZhongshuGroupState,
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
from .findings import resolve_finding_item_id
from .policies.solver_plan import is_retryable_solver_reply_error
from .policies.item_workflows import update_item_workflows
from .policies.zhongshu import (
    APPROVAL_ACTIONS,
    FREEZE_RETRY_ACTIONS,
    MAX_EVIDENCE_RECORDS,
    MENXIA_FREEZE_ENTRY_ACTIONS,
    REVISION_ACTIONS,
    age_unresolved_findings,
    apply_dispositions,
    blocker_fingerprint,
    freeze_retry_allowed,
    merge_findings,
    project_evidence_record,
    rebind_restatement_findings,
    remint_incoming_finding_ids,
    revision_allowed,
    settle_rejected_findings,
    unapproved_item_ids,
)
from .policies.zhongshu_group import (
    apply_group_verdict,
    apply_zhongshu_group_resets,
    apply_zhongshu_round,
    current_member_hashes,
    freeze_ready_groups,
    group_approval_held,
    group_item_ids,
    group_of_item,
    group_open_blockers,
    review_preflight_violations,
    zhongshu_drainout_parked,
    zhongshu_group_resets_from_payload,
)
from .zhongshu_doc import group_doc_violations
from .policies.analyst_partition import (
    expected_analyst_lenses,
    partition_active_findings,
)
from .policies.prompts import build_prompt, retry_feedback
from .transitions import ALL_STATES, BUSINESS_STATES, SYSTEM_STATES, TransitionRegistry
from .zhongshu import (
    GateVerdict,
    SolverStage,
    build_solver_dispatch,
    evaluate_gate,
    fold_item_revise_group_docs,
    fold_round,
    mark_followups,
    plan_artifact_effect,
    process_solver_reply,
    resolve_dispatch_stage,
    review_has_active_findings,
)
from .zhongshu.solver import revision_scope as _revision_editable_item_ids
from .policies.item_revise import (
    contested_item_ids,
    item_findings,
    plan_item,
)
from .policies.menxia import (
    MENXIA_ACTIVE_STAGES,
    MENXIA_ITEM_TARGETS,
    MENXIA_STAGE_BY_TARGET,
    MENXIA_TERMINAL_STAGES,
    apply_menxia_item_results,
    findings_for_scope,
    menxia_group_readiness,
    menxia_has_blockers,
    menxia_stage_census,
    merge_menxia_evidence,
    ready_stage_items,
    remove_plan_item,
)
from .policies.menxia_group import (
    MENXIA_GROUP_ACTIVE_STAGES,
    MENXIA_GROUP_STAGE_BY_TARGET,
    MENXIA_GROUP_TARGETS,
    apply_menxia_group_results,
    menxia_group_pipeline_readiness,
    menxia_group_stage_census,
    menxia_groups_have_blockers,
    ready_stage_groups,
)
from .menxia_doc import render_initial

_MENXIA_CONTRACT_BY_TARGET = {
    "MENXIA_ITEM_SOLVER": "nexus.menxia.item_solver.v1",
    "MENXIA_ITEM_ANALYST": "nexus.menxia.item_analyst.v1",
    "MENXIA_ITEM_CRITIC": "nexus.menxia.item_critic.v1",
    "MENXIA_GROUP_SOLVER": "nexus.menxia.group_solver.v1",
    "MENXIA_GROUP_ANALYST": "nexus.menxia.group_analyst.v1",
    "MENXIA_GROUP_CRITIC": "nexus.menxia.group_critic.v1",
    "MENXIA_GROUP_GATE": "nexus.menxia.group_gate.v1",
}
_MENXIA_PRODUCT_BY_TARGET = {
    "MENXIA_ITEM_SOLVER": "menxia_item_proposal",
    "MENXIA_ITEM_ANALYST": "menxia_item_evidence",
    "MENXIA_ITEM_CRITIC": "menxia_item_review",
    "MENXIA_GROUP_SOLVER": "menxia_group_doc",
    "MENXIA_GROUP_ANALYST": "menxia_group_doc",
    "MENXIA_GROUP_CRITIC": "menxia_group_doc",
    "MENXIA_GROUP_GATE": "menxia_group_gate",
}


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
    "MENXIA_GROUP_SOLVER": ("review-solver", "MENXIA"),
    "MENXIA_GROUP_ANALYST": ("review-analyst", "MENXIA"),
    "MENXIA_GROUP_CRITIC": ("review-critic", "MENXIA"),
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

    def _next_node_fail_streak(self, context: WorkflowContext) -> int:
        """Consecutive-FAIL count this state would reach with one more FAIL.

        The streak is keyed by state and deliberately ignores the error code:
        a fold rejection followed by a timeout is still one state failing
        deterministically twice in a row (task-20260928-835a07, waves 6→8).
        """

        if context.recovery.node_fail_state == self.name:
            return context.recovery.node_fail_streak + 1
        return 1

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
            # Deterministic-failure brake: back-to-back wave failures in one
            # state mean the same payload keeps producing the same outcome,
            # so spending another budgeted wave is pure waste (incident
            # task-20260928-835a07: waves 10/12/14 re-ran a solver revision
            # that could never finish inside its dispatch deadline).  A
            # max_node_fail_streak of 0 disables the escalation.
            streak = self._next_node_fail_streak(context)
            if (
                context.recovery.max_node_fail_streak > 0
                and streak >= context.recovery.max_node_fail_streak
            ):
                decision = self._human_gate_decision(
                    context, event, "NODE_FAIL_STREAK_EXHAUSTED"
                )
                recovery = decision.update.recovery or RecoveryUpdate()
                return replace(
                    decision,
                    update=replace(
                        decision.update,
                        recovery=replace(
                            recovery,
                            node_fail_streak=streak,
                            node_fail_state=self.name,
                        ),
                    ),
                )
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
            # The streak must persist on the retry itself: the next FAIL in
            # this state has to see "one failure already happened here" to
            # trip the deterministic-failure brake.
            streak = self._next_node_fail_streak(context)
            if budget == "reply":
                recovery = RecoveryUpdate(
                    reply_retry_count=context.recovery.reply_retry_count + 1,
                    last_failure=failure,
                    node_fail_streak=streak,
                    node_fail_state=self.name,
                )
            elif budget == "external":
                recovery = RecoveryUpdate(
                    external_retry_count=context.recovery.external_retry_count + 1,
                    last_failure=failure,
                    node_fail_streak=streak,
                    node_fail_state=self.name,
                )
            else:
                recovery = RecoveryUpdate(
                    retry_count=context.recovery.retry_count + 1,
                    last_failure=failure,
                    node_fail_streak=streak,
                    node_fail_state=self.name,
                )
            update = replace(update, recovery=recovery)
        if (
            event.name == "NODE_COMPLETED"
            or str(getattr(event, "event_id", "") or "").endswith(":SUCCEEDED")
        ) and action not in {
            "RETRY", "BLOCK", "BLOCKED", "OPEN_HUMAN_GATE", "HUMAN_GATE",
        }:
            # A clean join pays the wave's reply debt: the reply-retry budget
            # is scoped to one dispatch wave, not a whole-run allowance, or a
            # late-phase role inherits the retries an earlier role already
            # spent (task-20260928-eaea40: three Solver doc retries drained
            # the budget and the Critic's first wave hit the human gate after
            # a single failure).  Both fan-in waves (NODE_COMPLETED) and
            # single-dispatch successes (`effect:dispatch:...:SUCCEEDED`)
            # count as clean joins.
            recovery = update.recovery or RecoveryUpdate()
            update = replace(
                update,
                recovery=replace(
                    recovery,
                    reply_retry_count=0,
                    # A clean join proves the state is not failing
                    # deterministically: start the node-FAIL brake over.
                    node_fail_streak=0,
                    node_fail_state="",
                    # Drop the stale [Retry feedback] so a later FAIL in
                    # this state re-asks with its own error text, not the
                    # previous wave's.
                    clear_last_failure=True,
                ),
            )
            if context.review is not None:
                # The evidence round this wave answered (if any) is consumed:
                # drop the requester so the NEXT state's demand is not
                # misrouted by a stale value.  An update that both sets and
                # clears resolves by the reducer precedence (set wins).
                review = update.review or ReviewUpdate(
                    revision_id=context.review.revision_id
                )
                update = replace(
                    update,
                    review=replace(review, clear_evidence_requester=True),
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
                # The revision that will be live once this decision commits:
                # the pre-update review has none on the first dispatch, and
                # the empty value made the node fall back to its request id,
                # so salvage stamped under it never matched the retry's
                # revision (task-20260929-dad75d).
                revision_id=(
                    str(payload.get("revision_id") or "")
                    or (effective_review.revision_id if effective_review else "")
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
            if target in MENXIA_ITEM_TARGETS and context.review is not None:
                limits = (
                    context.parallel.menxia if context.parallel is not None else None
                )
                rows = ready_stage_items(
                    context.review, target=target, limits=limits
                )
                if rows:
                    base = (
                        review_update
                        if review_update is not None
                        else ReviewUpdate(revision_id=context.review.revision_id)
                    )
                    review_update = replace(
                        base,
                        active_group_id=rows[0].group_id,
                        active_item_id=rows[0].item_id,
                    )
            elif target in MENXIA_GROUP_TARGETS and context.review is not None:
                rows = ready_stage_groups(context.review, target=target)
                if rows:
                    base = (
                        review_update
                        if review_update is not None
                        else ReviewUpdate(revision_id=context.review.revision_id)
                    )
                    review_update = replace(
                        base,
                        active_group_id=rows[0].group_id,
                        active_item_id=None,
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
        if (
            event.name == "NODE_COMPLETED"
            and self._event_action(event) == "REQUEST_ANALYST_EVIDENCE"
            and context.review is not None
        ):
            # Record who demanded this evidence round (2026-09-28 Fix 3): the
            # Analyst's evidence wave folds and routes back to this state
            # directly instead of detouring through the Solver.  Generic so
            # all three requesters (SOLVER/CRITIC/FREEZE_CHECK) are recorded
            # by the same rule.
            base = review_update or ReviewUpdate(
                revision_id=context.review.revision_id
            )
            review_update = replace(base, evidence_requester=self.name)
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
                # Menxia stage-barrier waves fold per-item verdicts (one row
                # per dispatched item) into the menxia pipeline records.
                "menxia_item_results",
                # Group-pipeline waves fold per-group verdicts (one row per
                # dispatched group) into the group pipeline records.
                "menxia_group_results",
                # A failed task-review node still carries the verdicts its
                # successful workers produced; folding them into the ledger
                # here lets the retry re-dispatch only the missing tasks.
                "task_reviews",
                # Same for a failed Analyst evidence wave: the surviving
                # workers' evidence payloads ride the FAIL aggregate so the
                # retry only re-dispatches the missing workers.
                "salvaged_worker_payloads",
                # A partially salvaged group-revision wave folds the fully
                # valid groups' patches and requirement documents; the retry
                # then re-dispatches only the groups still missing.
                "salvaged_group_rows",
                # The Analyst evidence packet folds on its own key: the
                # Critic-routed packet carries no ``plan`` (Fix 3 keeps it
                # away from review.plan), so the packet key alone must
                # trigger the fold.
                "evidence_packet",
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
        raw_menxia_results = payload.get("menxia_item_results")
        menxia_results = (
            [dict(row) for row in raw_menxia_results if isinstance(row, Mapping)]
            if isinstance(raw_menxia_results, list)
            else []
        )
        raw_group_results = payload.get("menxia_group_results")
        group_results = (
            [dict(row) for row in raw_group_results if isinstance(row, Mapping)]
            if isinstance(raw_group_results, list)
            else []
        )
        removed_ids = [
            str(row.get("item_id") or "")
            for row in menxia_results
            if str(row.get("action") or "") == "REMOVE_ITEM"
        ]
        removed_ids = [item_id for item_id in removed_ids if item_id]
        if removed_ids and context.review is not None:
            # REMOVE_ITEM is a first-class fold: the item leaves the plan
            # projection so the group gate never counts it as pending again.
            effective_plan = plan if isinstance(plan, Mapping) else context.review.plan
            for item_id in removed_ids:
                patched = remove_plan_item(effective_plan, item_id)
                if patched is not None:
                    effective_plan = patched
            plan = effective_plan
            if task_items is None:
                task_items = context.review.task_items
            task_items = tuple(
                item for item in task_items if item.item_id not in removed_ids
            )
        existing_completed = context.review.completed_item_ids if context.review else ()
        completed = list(existing_completed)
        completed_id = payload.get("completed_item_id")
        if not completed_id and payload.get("action") == "APPROVE_ITEM":
            completed_id = payload.get("item_id") or (
                context.review.active_item_id if context.review else None
            )
        if completed_id and str(completed_id) not in completed:
            completed.append(str(completed_id))
        if payload.get("action") == "APPROVE_GROUP" and context.review is not None:
            # A group approval completes every member item at once.  The
            # active group is the one the approving wave just reviewed.
            group_id = str(
                payload.get("group_id")
                or context.review.active_group_id
                or ""
            )
            if group_id:
                completed.extend(
                    item.item_id
                    for item in context.review.task_items
                    if item.group_id == group_id and item.item_id not in completed
                )
        for row in menxia_results:
            if str(row.get("action") or "") != "APPROVE_ITEM":
                continue
            item_id = str(row.get("item_id") or "")
            if item_id and item_id not in completed:
                completed.append(item_id)
        salvaged_rows = _salvaged_task_review_rows(context, payload, task_items)
        if salvaged_rows and str(payload.get("action") or "") == "FAIL":
            # A failed wave's salvaged verdicts carry their findings; without
            # this fold the findings existed only inside the dead round's
            # aggregate and vanished with it.
            salvaged_findings = []
            for row in salvaged_rows:
                for item in row.get("findings") or []:
                    if isinstance(item, Mapping):
                        try:
                            salvaged_findings.append(Finding.from_dict(dict(item)))
                        except (TypeError, ValueError):
                            continue
            if salvaged_findings:
                findings = findings + tuple(salvaged_findings)
        # A discarded task leaves the plan.  Its leftovers must leave with it,
        # or the stale ledger row keeps re-escalating ITEM_STALLED forever and
        # its open findings keep the graph "blocked" for an item no job will
        # ever review again.
        discarded_items = _discarded_item_ids(context, task_items)
        if removed_ids:
            # A removed item's ledger row must go with it, or the stale row
            # keeps the gate counting the item as unresolved.
            discarded_items = tuple(discarded_items) + tuple(removed_ids)
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
        group_rows_value = None
        group_reviews = payload.get("group_reviews")
        if isinstance(group_reviews, list) and group_reviews and context.review is not None:
            # One group verdict per row projects the whole member ledger
            # (2026-09-26 group pipeline): APPROVE_GROUP marks every member
            # approved, REVISE_GROUP resets the group for a revision wave.
            acc_ledger = (
                ledger_value
                if ledger_value is not None
                else tuple(context.review.task_review_ledger)
            )
            acc_rows = tuple(getattr(context.review, "zhongshu_groups", ()) or ())
            if not acc_rows:
                seed = getattr(context.review, "seed_zhongshu_groups", None)
                if callable(seed):
                    acc_rows = tuple(seed())
            for row in group_reviews:
                if not isinstance(row, Mapping):
                    continue
                group_id = str(row.get("group_id") or "")
                action = str(row.get("action") or "")
                if not group_id or action not in ("APPROVE_GROUP", "REVISE_GROUP"):
                    continue
                view = replace(
                    context.review,
                    task_review_ledger=acc_ledger,
                    zhongshu_groups=acc_rows,
                )
                ledger_next, rows_next = apply_group_verdict(
                    view,
                    group_id,
                    action,
                    member_hashes=current_member_hashes(view, group_id),
                )
                acc_ledger = ledger_next
                acc_rows = rows_next
            ledger_value = acc_ledger
            group_rows_value = acc_rows
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
            remint_findings=bool(
                payload.get("task_reviews") or payload.get("group_reviews")
            ),
            task_id=context.identity.task_id if context.identity else "",
        )
        raw_resolutions = payload.get("finding_resolutions")
        if isinstance(raw_resolutions, list) and raw_resolutions:
            # Solver round: the reply's disposition ledger closes absorbed
            # findings and parks rejections for the Critic to settle.
            merged_findings = apply_dispositions(
                merged_findings,
                [row for row in raw_resolutions if isinstance(row, Mapping)],
                round=(context.review.zhongshu_revision_round if context.review else 0),
            )
        elif isinstance(payload.get("task_reviews"), list) and payload["task_reviews"]:
            # Critic round: settle the rejections the Solver parked last round.
            reraised_keys = {
                str(getattr(finding, "canonical_key", "") or "").strip()
                for finding in findings
                if str(getattr(finding, "canonical_key", "") or "").strip()
            }
            critic_responses = [
                dict(entry)
                for review_row in payload["task_reviews"]
                if isinstance(review_row, Mapping)
                for entry in (review_row.get("finding_responses") or [])
                if isinstance(entry, Mapping)
            ]
            merged_findings = settle_rejected_findings(
                merged_findings, critic_responses, reraised_keys
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
        menxia_value = None
        if menxia_results and context.review is not None:
            menxia_value = apply_menxia_item_results(
                context.review,
                menxia_results,
                max_rounds=(
                    context.review.max_item_revision_rounds if context.review else 0
                ),
            )
        group_value = None
        if group_results and context.review is not None:
            group_value = apply_menxia_group_results(
                context.review,
                group_results,
                max_rounds=context.review.max_menxia_group_rounds,
            )
        salvaged_group_rows_payload = payload.get("salvaged_group_rows")
        if isinstance(salvaged_group_rows_payload, list) and salvaged_group_rows_payload:
            # A partially salvaged group-revision wave folds the fully valid
            # groups' requirement documents as version-chain rows, matching
            # what a successful wave would have produced.  Without this the
            # next §8 projection would rewrite the salvaged items' acceptance
            # signals back to the stale document (task-20260927-616863).
            base_rows = (
                group_rows_value
                if group_rows_value is not None
                else tuple(getattr(context.review, "zhongshu_groups", ()) or ())
            )
            by_group = {
                str(getattr(row, "group_id", "") or ""): row for row in base_rows
            }
            for entry in salvaged_group_rows_payload:
                if not isinstance(entry, Mapping):
                    continue
                group_id = str(entry.get("group_id") or "").strip()
                if not group_id:
                    continue
                try:
                    by_group[group_id] = ZhongshuGroupState.from_dict(entry)
                except ValueError:
                    continue
            group_rows_value = tuple(by_group.values())
        return ReviewUpdate(
            revision_id=(
                str(payload.get("revision_id") or "")
                or (context.review.revision_id if context.review else "")
                or f"{context.identity.task_id}:{context.progression.sequence + 1}"
            ),
            task_review_ledger=ledger_value,
            zhongshu_groups=group_rows_value,
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
            menxia_items=menxia_value,
            menxia_groups=group_value,
            salvaged_task_reviews=salvaged_rows,
            salvaged_worker_payloads=_salvaged_analyst_worker_payloads(
                context, payload
            ),
            evidence_packet=_menxia_evidence_packet_update(context, payload),
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
        fast_track = (
            context.parallel is not None and context.parallel.zhongshu.fast_track
        )
        if target == "ZHONGSHU_CRITIC" and fast_track:
            # Fast track: the Critic approves the whole plan without a review
            # wave.  The synthetic effect produces the same NODE_COMPLETED a
            # live worker aggregate would, and the Critic state routes it as
            # APPROVE_CRITIC.
            return EffectRequest(
                effect_id=f"fast-track:{request_id}",
                effect_type="fast_track",
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
                    "action": "APPROVE_CRITIC",
                    "revision_id": revision_id,
                    "plan_hash": plan_hash,
                    "sequence": context.progression.sequence,
                    "group_id": None,
                    "item_id": None,
                },
            )
        if target == "ZHONGSHU_FREEZE_CHECK" and fast_track:
            # Fast track: the freeze check releases the plan immediately so
            # the run reaches the Menxia group pipeline without a critic run.
            return EffectRequest(
                effect_id=f"fast-track:{request_id}",
                effect_type="fast_track",
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
                    "action": "FREEZE_APPROVED",
                    "revision_id": revision_id,
                    "plan_hash": plan_hash,
                    "sequence": context.progression.sequence,
                    "group_id": None,
                    "item_id": None,
                },
            )
        if (
            target == "ZHONGSHU_FREEZE_CHECK"
            and context.parallel is not None
            and context.parallel.zhongshu.mechanical_freeze
            and context.review is not None
            and context.review.zhongshu_groups
        ):
            # Mechanical freeze (2026-09-28): when the objective preconditions
            # already hold, the freeze-check agent hop adds no information —
            # synthesize the same FREEZE_APPROVED event a live worker would
            # return.  The real release semantics stay with
            # _group_freeze_decision (ready computation + document
            # verification), which re-checks everything on the way in.
            ready, failures = mechanical_freeze_ready(context)
            if ready:
                logger.info(
                    "ZHONGSHU_FREEZE_MECHANICAL_APPROVED task_id=%s revision_id=%s skipped_agent_hop=1",
                    context.identity.task_id,
                    revision_id,
                )
                return EffectRequest(
                    effect_id=f"mechanical-freeze:{request_id}",
                    effect_type="mechanical_freeze",
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
                        "action": "FREEZE_APPROVED",
                        "revision_id": revision_id,
                        "plan_hash": plan_hash,
                        "sequence": context.progression.sequence,
                        "group_id": None,
                        "item_id": None,
                    },
                )
            logger.info(
                "ZHONGSHU_FREEZE_MECHANICAL_PRECHECK_FAILED task_id=%s revision_id=%s failures=%s",
                context.identity.task_id,
                revision_id,
                failures,
            )
            prompt = replace(
                prompt,
                content=(
                    prompt.content
                    + "\n[Freeze precheck] Mechanical freeze preconditions unmet: "
                    + "; ".join(failures)
                    + "\nRe-verify each condition above before issuing your freeze verdict.\n"
                ),
            )
        if target == "ZHONGSHU_CRITIC":
            review_unit = _review_dispatch_unit(context)
            if review_unit == "group":
                task_bindings = _group_review_bindings(
                    context, revision_id, plan_hash, pending_review
                )
                critic_dispatch_mode = "group_review"
            else:
                task_bindings = _task_review_bindings(
                    context, revision_id, plan_hash, pending_review
                )
                critic_dispatch_mode = "task_review"
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
                        "dispatch_mode": critic_dispatch_mode,
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
        if target in MENXIA_ITEM_TARGETS and context.review is not None:
            limits = context.parallel.menxia if context.parallel is not None else None
            if limits is not None and limits.enabled:
                bindings = _menxia_item_bindings(
                    context,
                    target,
                    request_id=request_id,
                    revision_id=revision_id,
                    plan_hash=plan_hash or "",
                )
                if bindings:
                    rows = (
                        context.review.menxia_items
                        or context.review.seed_menxia_items()
                    )
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
                            "dispatch_mode": "menxia_item_pipeline",
                            "bindings": bindings,
                            # Stage snapshot for the fan-in reduction: rows not
                            # dispatched in this wave cannot change during it,
                            # so the joiner can compute the post-wave census as
                            # snapshot ∪ mapped worker results.
                            "menxia_stage_census": {
                                row.item_id: row.stage for row in rows
                            },
                            "group_id": None,
                            "item_id": None,
                        },
                    )
        if target in MENXIA_GROUP_TARGETS and context.review is not None:
            limits = context.parallel.menxia if context.parallel is not None else None
            if limits is not None and limits.enabled:
                bindings = _menxia_group_bindings(
                    context,
                    target,
                    request_id=request_id,
                    revision_id=revision_id,
                    plan_hash=plan_hash or "",
                )
                if bindings:
                    rows = (
                        context.review.menxia_groups
                        or context.review.seed_menxia_groups()
                    )
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
                            "dispatch_mode": "menxia_group_pipeline",
                            "bindings": bindings,
                            # Stage snapshot keyed by group: rows not
                            # dispatched in this wave cannot change during
                            # it, so the joiner computes the post-wave
                            # census as snapshot ∪ mapped results.
                            "menxia_stage_census": {
                                row.group_id: row.stage for row in rows
                            },
                            "group_id": None,
                            "item_id": None,
                        },
                    )
        if target == "MENXIA_GROUP_GATE" and context.review is not None:
            limits = context.parallel.menxia if context.parallel is not None else None
            if limits is not None and limits.enabled:
                gate_dispatch = _menxia_group_gate_dispatch(
                    context,
                    request_id=request_id,
                    prompt=prompt,
                    revision_id=revision_id,
                    plan_hash=plan_hash or "",
                )
                if gate_dispatch is not None:
                    return gate_dispatch
        if worker_count > 1:
            contract_payload = (
                [dict(item) for item in canonical_requirements]
                if canonical_requirements
                else []
            )
            bindings = []
            salvaged_workers = (
                _salvaged_analyst_workers(context, revision_id)
                if target == "ZHONGSHU_ANALYST"
                else {}
            )
            # Evidence round answering the Critic: each worker takes its own
            # slice of the open findings; workers without one are not dispatched.
            finding_slices = (
                partition_active_findings(context.review, worker_count)
                if target == "ZHONGSHU_ANALYST"
                and str(getattr(context.review, "evidence_requester", "") or "")
                == "ZHONGSHU_CRITIC"
                else {}
            )
            worker_indices = tuple(finding_slices) or tuple(range(1, worker_count + 1))
            wave_worker_ids = {
                f"{target.lower()}-worker-{index:02d}" for index in worker_indices
            }
            salvaged_workers = {
                worker_id: row
                for worker_id, row in salvaged_workers.items()
                if worker_id in wave_worker_ids
            }
            if len(salvaged_workers) >= len(wave_worker_ids):
                # Defensive: a salvage set covering every slot would dispatch
                # an empty wave; a full re-run is the safe fallback.
                salvaged_workers = {}
            for index in worker_indices:
                worker_id = f"{target.lower()}-worker-{index:02d}"
                slice_findings = finding_slices.get(index)
                if worker_id in salvaged_workers:
                    # This worker's evidence was salvaged from the failed
                    # round; only the missing slots are re-dispatched.
                    continue
                binding: dict[str, object] = {
                    "worker_id": worker_id,
                    "agent_id": f"{phase.lower()}-{role.split('-')[-1]}-{index:02d}",
                    "task_id": context.identity.task_id,
                    "request_id": f"{request_id}:worker-{index:02d}",
                    "role": role,
                    "phase": phase,
                    # Per-worker retry feedback: each re-asked lens must see
                    # its own rejection, not the wave's first error.
                    "prompt_ref": build_prompt(
                        context,
                        target_state=target,
                        worker_id=worker_id,
                        evidence_findings=slice_findings,
                    ).content,
                }
                if target == "ZHONGSHU_ANALYST" and contract_payload:
                    analyst_binding_context: dict[str, object] = {
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
                    evidence_demand = _analyst_evidence_demand(
                        context, slice_findings
                    )
                    if evidence_demand is not None:
                        analyst_binding_context["evidence_demand"] = evidence_demand
                    binding["dispatch_context"] = analyst_binding_context
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
            if target == "ZHONGSHU_ANALYST":
                # Who demanded this evidence round decides where the folded
                # packet routes back (2026-09-28 Fix 3).
                payload["evidence_requester"] = str(
                    getattr(context.review, "evidence_requester", "") or ""
                )
            if salvaged_workers:
                payload["salvaged_worker_payloads"] = [
                    salvaged_workers[worker_id]
                    for worker_id in sorted(salvaged_workers)
                ]
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
            review_unit = _review_dispatch_unit(context)
            is_revision = (
                resolve_dispatch_stage(
                    context.progression.state,
                    has_active_findings=review_has_active_findings(
                        context.review
                    ),
                )
                is SolverStage.REVISE
            )
            # 2026-09-26 group pipeline: one Solver worker per affected group,
            # dispatched concurrently across groups (the group-revision wave).
            # The demand is "active findings exist", not the transition edge:
            # a solver retry edge re-enters this state and must not fall back
            # to the single writer.  The explicit A2/P3 item-workflow flag
            # keeps precedence for its own tests.
            if (
                review_unit == "group"
                and not (
                    context.parallel is not None
                    and context.parallel.zhongshu.item_workflow_enabled
                )
                and effective_review is not None
                and review_has_active_findings(effective_review)
            ):
                editable_by_group: dict[str, list[str]] = {}
                for finding in getattr(effective_review, "findings", ()) or ():
                    if not getattr(finding, "active", False):
                        continue
                    item_id = str(getattr(finding, "item_id", "") or "")
                    if not item_id:
                        continue
                    group_id = group_of_item(effective_review, item_id)
                    if group_id:
                        editable_by_group.setdefault(group_id, [])
                        if item_id not in editable_by_group[group_id]:
                            editable_by_group[group_id].append(item_id)
                if editable_by_group:
                    group_bindings = _group_revise_bindings(
                        context,
                        revision_id,
                        plan_hash,
                        affected_groups=tuple(sorted(editable_by_group)),
                        editable_by_group=editable_by_group,
                        review_override=effective_review,
                    )
                    if group_bindings:
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
                                "dispatch_mode": "group_revise",
                                "bindings": group_bindings,
                                "previous_review": _previous_review_snapshot(context),
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
            # A2/P3: with the item workflow enabled, a revision edge fans one
            # Solver worker out per contested item instead of routing the
            # whole batch through the single writer.  Formalization stays
            # single-writer, and the flag keeps the legacy path available.
            if (
                context.parallel is not None
                and context.parallel.zhongshu.item_workflow_enabled
                and is_revision
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
        if target in MENXIA_ITEM_TARGETS and context.review is not None:
            # Even the serial path must tell the agent which item it works on
            # (the prompt bundle alone has no per-item slice).
            item_id = str(payload.get("item_id") or "") or str(
                context.review.active_item_id or ""
            )
            dispatch_context = _menxia_item_dispatch_context(
                context,
                target,
                item_id=item_id,
                group_id="",
                revision_id=revision_id,
                plan_hash=plan_hash or "",
            )
            if dispatch_context is not None:
                payload["dispatch_context"] = dispatch_context
                payload["item_id"] = item_id or None
                payload["group_id"] = dispatch_context.get("group_id") or None
        if target in MENXIA_GROUP_TARGETS and context.review is not None:
            # Serial path: the agent still needs the group slice — the shared
            # document travels in the dispatch context.
            rows = (
                context.review.menxia_groups or context.review.seed_menxia_groups()
            )
            group_id = str(context.review.active_group_id or "") or (
                rows[0].group_id if rows else ""
            )
            dispatch_context = _menxia_group_dispatch_context(
                context,
                target,
                group_id=group_id,
                revision_id=revision_id,
                plan_hash=plan_hash or "",
            )
            if dispatch_context is not None:
                payload["dispatch_context"] = dispatch_context
                payload["group_id"] = group_id or None
        if target == "ZHONGSHU_ANALYST":
            evidence_demand = _analyst_evidence_demand(context)
            if evidence_demand is not None:
                payload["dispatch_context"] = {"evidence_demand": evidence_demand}
            payload["evidence_requester"] = str(
                getattr(context.review, "evidence_requester", "") or ""
            )
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
        *,
        no_progress_count: int | None = None,
    ) -> StateDecision:
        """Hand the workflow to an operator with an explicit reason code."""

        decision = self._transition_decision(context, event, action="HUMAN_GATE")
        human_gate = decision.update.human_gate
        if human_gate is not None:
            human_gate = replace(human_gate, reason_code=reason)
        # Preserve whatever the payload fold already staged (e.g. the FAIL
        # event's last_failure record) and stamp the reason on top: a fresh
        # RecoveryUpdate would silently drop the failure evidence the operator
        # needs at the gate.
        recovery = replace(
            decision.update.recovery or RecoveryUpdate(), blocked_reason=reason
        )
        if no_progress_count is not None:
            # The round that raised the gate still happened: persist its
            # no-progress age, or every resume restarts the counter at zero
            # and a chronically rejected finding gates forever instead of
            # reaching the bounded freeze-with-followups exit.
            recovery = replace(recovery, no_progress_count=no_progress_count)
        return replace(
            decision,
            transition=TransitionRequest(action="HUMAN_GATE", reason_code=reason),
            update=replace(
                decision.update,
                human_gate=human_gate,
                recovery=recovery,
            ),
        )

    def _menxia_wave_decision(
        self,
        context: WorkflowContext,
        event: DomainEvent,
    ) -> StateDecision | None:
        """Stage-barrier override for a menxia item wave completion.

        Returns a decision only for the drain-out tail: when no item is left
        in any active stage and at least one is parked (ESCALATED/BLOCKED),
        the run goes to a human with MENXIA_GROUP_GATE as the resume point so
        the group gate can run after the operator decides.  Everything else
        (including per-item budget escalation, which just parks the item) is
        routed by the joiner's aggregate action.
        """

        if event.name != "NODE_COMPLETED" or context.review is None:
            return None
        if context.parallel is None or not context.parallel.menxia.enabled:
            return None
        payload = event.payload if isinstance(event.payload, Mapping) else {}
        raw_results = payload.get("menxia_item_results")
        if not isinstance(raw_results, list) or not raw_results:
            return None
        results = [dict(row) for row in raw_results if isinstance(row, Mapping)]
        post = apply_menxia_item_results(
            context.review,
            results,
            max_rounds=context.review.max_item_revision_rounds,
        )
        census = menxia_stage_census(post)
        if any(census.get(stage) for stage in MENXIA_ACTIVE_STAGES):
            return None
        if not menxia_has_blockers(post):
            return None
        decision = self._transition_decision(context, event, action="OPEN_HUMAN_GATE")
        human_gate = decision.update.human_gate
        progression = decision.update.progression
        if human_gate is not None:
            human_gate = replace(
                human_gate,
                resume_state="MENXIA_GROUP_GATE",
                reason_code="MENXIA_GROUP_BLOCKED",
            )
        if progression is not None:
            progression = replace(progression, resume_state="MENXIA_GROUP_GATE")
        return replace(
            decision,
            transition=TransitionRequest(
                action="OPEN_HUMAN_GATE",
                reason_code="MENXIA_GROUP_BLOCKED",
            ),
            update=replace(
                decision.update,
                human_gate=human_gate,
                progression=progression,
                recovery=RecoveryUpdate(blocked_reason="MENXIA_GROUP_BLOCKED"),
            ),
        )

    def _menxia_group_wave_decision(
        self,
        context: WorkflowContext,
        event: DomainEvent,
    ) -> StateDecision | None:
        """Stage-barrier override for a menxia group wave completion.

        Mirrors the item wave decision: only the drain-out tail — no group
        left in any active stage and at least one parked (ESCALATED/BLOCKED)
        — routes to a human with MENXIA_GROUP_GATE as the resume point.
        Everything else is routed by the joiner's aggregate action.
        """

        if event.name != "NODE_COMPLETED" or context.review is None:
            return None
        if context.parallel is None or not context.parallel.menxia.enabled:
            return None
        payload = event.payload if isinstance(event.payload, Mapping) else {}
        raw_results = payload.get("menxia_group_results")
        if not isinstance(raw_results, list) or not raw_results:
            return None
        results = [dict(row) for row in raw_results if isinstance(row, Mapping)]
        post = apply_menxia_group_results(
            context.review,
            results,
            max_rounds=context.review.max_menxia_group_rounds,
        )
        census = menxia_group_stage_census(post)
        if any(census.get(stage) for stage in MENXIA_GROUP_ACTIVE_STAGES):
            return None
        if not menxia_groups_have_blockers(post):
            return None
        decision = self._transition_decision(context, event, action="OPEN_HUMAN_GATE")
        human_gate = decision.update.human_gate
        progression = decision.update.progression
        if human_gate is not None:
            human_gate = replace(
                human_gate,
                resume_state="MENXIA_GROUP_GATE",
                reason_code="MENXIA_GROUP_BLOCKED",
            )
        if progression is not None:
            progression = replace(progression, resume_state="MENXIA_GROUP_GATE")
        return replace(
            decision,
            transition=TransitionRequest(
                action="OPEN_HUMAN_GATE",
                reason_code="MENXIA_GROUP_BLOCKED",
            ),
            update=replace(
                decision.update,
                human_gate=human_gate,
                progression=progression,
                recovery=RecoveryUpdate(blocked_reason="MENXIA_GROUP_BLOCKED"),
            ),
        )

    def _stuck_finding_decision(
        self,
        context: WorkflowContext,
        event: DomainEvent,
        *,
        no_progress_count: int | None = None,
    ) -> StateDecision:
        """Escalate a finding the Solver loop cannot resolve to a human."""

        return self._human_gate_decision(
            context,
            event,
            self.stuck_finding_reason,
            no_progress_count=no_progress_count,
        )

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
        # The evidence packet folding back to the Critic that demanded it
        # (2026-09-28 Fix 3) instead of detouring through the Solver.
        "EVIDENCE_PACKET_READY_FOR_CRITIC",
        "HUMAN_GATE", "BLOCKED", "RETRY", "OPEN_HUMAN_GATE",
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
        group_doc_rows: tuple[ZhongshuGroupState, ...] = ()
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
            # The item patch may rewrite acceptance signals; the owning
            # group's requirement document must be re-authored in the same
            # reply and supersede the authoritative version.
            doc_error, item_doc_rows = fold_item_revise_group_docs(
                payload, context.review, materialized_plan
            )
            if doc_error:
                if (
                    is_retryable_solver_reply_error(doc_error)
                    and context.recovery.reply_retry_count
                    < context.recovery.max_reply_retries
                ):
                    return self._retry_solver_reply(context, event, doc_error)
                logger.warning(
                    "ZHONGSHU_SOLVER_REVISION_REJECTED task_id=%s error=%s",
                    context.identity.task_id,
                    doc_error,
                )
                return self._blocked_decision(context, event, self.invalid_reason)
            group_doc_rows = item_doc_rows
        elif event.name != "TIMEOUT" and action == "READY_FOR_CRITIC":
            payload = dict(event.payload) if isinstance(event.payload, Mapping) else {}
            # Stage routing lives in the zhongshu package: the formalize
            # (Analyst) and revise (Critic) channels validate and materialize
            # through separate logic classes.
            outcome = process_solver_reply(payload, context.review)
            group_doc_rows = outcome.group_doc_rows
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
        if group_doc_rows and decision.transition.action == "READY_FOR_CRITIC":
            # The reply's requirement documents fold into the authoritative
            # group rows (plan Task 5): the freeze check re-verifies from
            # these rows, so they must ride with the same review update.
            review_update = decision.update.review
            if review_update is None:
                review_update = ReviewUpdate(
                    revision_id=context.review.revision_id if context.review else ""
                )
            decision = replace(
                decision,
                update=replace(
                    decision.update,
                    review=replace(
                        review_update, zhongshu_groups=group_doc_rows
                    ),
                ),
            )
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
        decision = self._with_gate_resets(context, event, decision)
        return decision

    def _with_gate_resets(
        self,
        context: WorkflowContext,
        event: DomainEvent,
        decision: StateDecision,
    ) -> StateDecision:
        """Persist gate-issued directed resets carried by a resume payload."""

        resets = zhongshu_group_resets_from_payload(event.payload)
        if not resets or context.review is None:
            return decision
        rows = apply_zhongshu_group_resets(
            context.review.seed_zhongshu_groups(), resets
        )
        review_update = decision.update.review
        if review_update is None:
            review_update = ReviewUpdate(revision_id=context.review.revision_id)
        return replace(
            decision,
            update=replace(
                decision.update,
                review=replace(review_update, zhongshu_groups=rows),
            ),
        )

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
        if (
            event.name == "NODE_COMPLETED"
            and context.parallel is not None
            and context.parallel.zhongshu.fast_track
        ):
            # Fast track: the Critic approves the whole plan without a review
            # wave.  The normal approval path below holds the freeze whenever
            # the ledger still has unapproved tasks; a fast-track run never
            # reviews them, so the pending check must be bypassed entirely.
            logger.info(
                "ZHONGSHU_FAST_TRACK_APPROVED task_id=%s sequence=%s",
                context.identity.task_id,
                context.progression.sequence,
            )
            return self._transition_decision(context, event, action="APPROVE_CRITIC")
        if event.name != "TIMEOUT" and action in APPROVAL_ACTIONS:
            decision = self._transition_decision(context, event, action=action)
            round_after = self._round_after(context, decision)
            pending = self._pending_unfrozen_items(context, round_after)
            if not pending:
                # Fold the group rows on the approval path too: groups whose
                # members are all approved with no open blocker move to
                # CONVERGED without consuming budget; FROZEN rows stay put.
                review_after = self._merged_review_after(context, decision)
                return self._with_group_rows(
                    context, decision, self._fold_group_rows(review_after)
                )
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
            if context.review is not None and not self._group_revision_allowed(
                context.review
            ):
                # The bounded Zhongshu revision budget is spent.  A further
                # revision request must not re-dispatch the Solver forever:
                # stop the loop and record the unresolved divergence so an
                # operator can intervene instead of burning agent cycles.
                return self._blocked_decision(context, event, self.exhausted_reason)
            decision = self._transition_decision(context, event, action=action)
            if context.review is not None:
                review_after = self._merged_review_after(context, decision)
                group_rows = self._fold_group_rows_with_resets(event, review_after)
                if group_rows is not None:
                    review_after = replace(review_after, zhongshu_groups=group_rows)
                round_after = self._round_after(context, decision)
                verdict = self._gate_verdict(context, round_after)
                gate_decision = self._apply_gate_verdict(
                    context, event, decision, round_after, verdict, action,
                    review_after=review_after,
                )
                if gate_decision is not None:
                    return self._with_group_rows(context, gate_decision, group_rows)
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
                            zhongshu_groups=group_rows,
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
        if (
            event.name != "TIMEOUT"
            and action == "REQUEST_ANALYST_EVIDENCE"
            and context.review is not None
        ):
            # An evidence round is a legitimate progress path, but it must be
            # bound by the same convergence fuses as a revision round.  The
            # live run task-20260920-94c51b spun Critic -> Analyst -> Solver
            # forever because this aggregate action never entered a gated
            # branch: the stuck fuse threshold had already been crossed
            # (stuck_rounds=4 > max 3) and the no-progress / stall exits were
            # unreachable, while zhongshu_revision_round stayed frozen so the
            # revision budget could not bound the loop either.
            decision = self._transition_decision(context, event, action=action)
            review_after = self._merged_review_after(context, decision)
            group_rows = self._fold_group_rows_with_resets(event, review_after)
            if group_rows is not None:
                review_after = replace(review_after, zhongshu_groups=group_rows)
            round_after = self._round_after(context, decision)
            verdict = self._gate_verdict(context, round_after)
            gate_decision = self._apply_gate_verdict(
                context, event, decision, round_after, verdict, action,
                review_after=review_after,
            )
            if gate_decision is not None:
                return self._with_group_rows(context, gate_decision, group_rows)
            review_update = decision.update.review
            if review_update is None:
                review_update = ReviewUpdate(revision_id=context.review.revision_id)
            decision = replace(
                decision,
                update=replace(
                    decision.update,
                    review=replace(
                        review_update,
                        last_reply_fingerprint=blocker_fingerprint(
                            round_after.findings
                        ),
                        zhongshu_groups=group_rows,
                    ),
                    recovery=RecoveryUpdate(
                        no_progress_count=verdict.no_progress_count,
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

    def _merged_review_after(self, context: WorkflowContext, decision: StateDecision):
        """Review projection the decision would persist, for group folds.

        ``_round_after`` folds the gate inputs (ledger/findings/items); this
        companion returns a full ``ReviewState`` so the group pipeline helpers
        can consume it unchanged.
        """

        review = context.review
        if review is None:
            return None
        update = decision.update.review
        return replace(
            review,
            task_review_ledger=(
                update.task_review_ledger
                if update is not None and update.task_review_ledger is not None
                else review.task_review_ledger
            ),
            findings=(
                update.findings
                if update is not None and update.findings is not None
                else review.findings
            ),
            task_items=(
                update.task_items
                if update is not None and update.task_items is not None
                else review.task_items
            ),
            attempted_item_ids=(
                update.attempted_item_ids
                if update is not None and update.attempted_item_ids is not None
                else review.attempted_item_ids
            ),
            # Group rows folded by the same event (the group verdict
            # projection) must survive into the group pipeline folds below:
            # rebuilding from a row-less review would re-seed fresh rows and
            # drop the approval ratchet hash.
            zhongshu_groups=(
                update.zhongshu_groups
                if update is not None and update.zhongshu_groups is not None
                else review.zhongshu_groups
            ),
        )

    @staticmethod
    def _fold_group_rows(review_after):
        """Fold one Critic round into the group rows (seeds missing rows)."""

        if review_after is None:
            return None
        return apply_zhongshu_round(
            review_after,
            attempted_item_ids=review_after.attempted_item_ids,
            max_rounds=review_after.max_zhongshu_group_rounds,
        )

    def _fold_group_rows_with_resets(self, event, review_after):
        """Fold the round, then apply any gate-issued directed resets.

        The reset overrides the fold result for the named groups: a group the
        human unstuck gets a fresh budget even when this round's scope covers
        it (mirrors the Menxia gate's ``budget_reset``).
        """

        group_rows = self._fold_group_rows(review_after)
        resets = zhongshu_group_resets_from_payload(event.payload)
        if not resets or review_after is None or group_rows is None:
            return group_rows
        return apply_zhongshu_group_resets(group_rows, resets)

    @staticmethod
    def _with_group_rows(context: WorkflowContext, decision: StateDecision, group_rows):
        """Attach folded group rows to a (gate) decision's review update."""

        if group_rows is None:
            return decision
        review_update = decision.update.review
        if review_update is not None and review_update.zhongshu_groups:
            # The decision already carries rows (e.g. the follow-up freeze
            # overrides them); do not clobber them with the plain fold.
            return decision
        if review_update is None:
            review_update = ReviewUpdate(
                revision_id=context.review.revision_id if context.review else ""
            )
        return replace(
            decision,
            update=replace(
                decision.update,
                review=replace(review_update, zhongshu_groups=group_rows),
            ),
        )

    def _pending_unfrozen_items(
        self, context: WorkflowContext, round_after
    ) -> tuple[str, ...]:
        """Unapproved items outside FROZEN groups.

        A frozen group's shared document is authoritative and its items are
        never re-reviewed, so its unapproved ledger entries must not hold the
        freeze back any more.
        """

        pending = round_after.unapproved_item_ids
        review = context.review
        if review is None or not pending:
            return pending
        frozen = {
            row.group_id
            for row in review.seed_zhongshu_groups()
            if row.stage == "FROZEN"
        }
        if not frozen:
            return pending
        return tuple(
            item_id
            for item_id in pending
            if group_of_item(review, item_id) not in frozen
        )

    @staticmethod
    def _group_revision_allowed(review: ReviewState) -> bool:
        """Any non-terminal group still has group-wide revision budget."""

        rows = review.seed_zhongshu_groups()
        if not rows:
            # Legacy snapshots without a task graph keep the global budget.
            return revision_allowed(review)
        return any(
            row.stage in ("REVIEWING", "CONVERGED")
            and row.revision_round < review.max_zhongshu_group_rounds
            for row in rows
        )

    def _group_drainout_decision(
        self,
        context: WorkflowContext,
        event: DomainEvent,
        review_after,
    ) -> StateDecision:
        """Every remaining group is parked: hand the run to an operator.

        The gate request lists each parked group's open findings and
        unapproved items, plus the resettable markers the directed unstick
        flow (Task 7) consumes on resume.
        """

        parks = tuple(
            {
                "group_id": row.group_id,
                "stage": row.stage,
                "revision_round": row.revision_round,
                "stalled_rounds": row.stalled_rounds,
                "open_finding_ids": tuple(
                    str(getattr(finding, "finding_id", "") or "")
                    for finding in group_open_blockers(review_after, row.group_id)
                    if str(getattr(finding, "finding_id", "") or "")
                ),
                "unapproved_item_ids": unapproved_item_ids(
                    review_after.task_review_ledger,
                    group_item_ids(review_after, row.group_id),
                ),
            }
            for row in review_after.zhongshu_groups
            if row.stage in ("STALLED", "BLOCKED")
        )
        logger.warning(
            "ZHONGSHU_GROUP_STALLED task_id=%s groups=%s",
            context.identity.task_id,
            [park["group_id"] for park in parks],
        )
        decision = self._transition_decision(context, event, action="OPEN_HUMAN_GATE")
        human_gate = decision.update.human_gate
        if human_gate is not None:
            human_gate = replace(
                human_gate,
                reason_code="ZHONGSHU_GROUP_STALLED",
                zhongshu_group_parks=parks,
            )
        return replace(
            decision,
            transition=TransitionRequest(
                action="OPEN_HUMAN_GATE",
                reason_code="ZHONGSHU_GROUP_STALLED",
            ),
            update=replace(
                decision.update,
                human_gate=human_gate,
                recovery=RecoveryUpdate(blocked_reason="ZHONGSHU_GROUP_STALLED"),
            ),
        )

    def _gate_verdict(self, context: WorkflowContext, round_after) -> GateVerdict:
        # The global fingerprint fuse is retired only where the group pipeline
        # owns convergence (group rows exist).  Legacy snapshots without a
        # task graph keep the fuse so their bound stays equivalent.
        has_groups = bool(
            context.review.seed_zhongshu_groups() if context.review else ()
        )
        return evaluate_gate(
            round_after,
            revision_allowed=True,
            previous_fingerprint=(
                context.review.last_reply_fingerprint if context.review else None
            ),
            no_progress_count=context.recovery.no_progress_count,
            max_no_progress=context.recovery.max_no_progress,
            max_stuck_rounds=context.recovery.max_stuck_finding_rounds,
            use_fingerprint_fuse=not has_groups,
        )

    def _apply_gate_verdict(
        self,
        context: WorkflowContext,
        event: DomainEvent,
        decision: StateDecision,
        round_after,
        verdict: GateVerdict,
        action: str,
        review_after=None,
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
            # Groups owning accepted follow-ups are done: the Critic decided
            # their residual P1s are acceptable, so no further revision round
            # can ever converge them (the deferred finding no longer blocks
            # while the item stays unapproved in the ledger).  Mark them
            # CONVERGED so the freeze check can release them instead of
            # bouncing back to the Solver in an unwinnable loop.
            followup_groups = {
                str(getattr(finding, "group_id", "") or "").strip()
                for finding in round_after.findings
                if str(getattr(finding, "finding_id", "") or "")
                in set(verdict.followup_finding_ids)
            }
            followup_groups.discard("")
            rows = tuple(
                getattr(review_after, "zhongshu_groups", None) or ()
            )
            if followup_groups and rows:
                rows = tuple(
                    replace(row, stage="CONVERGED")
                    if row.group_id in followup_groups
                    and row.stage not in ("FROZEN", "CONVERGED")
                    else row
                    for row in rows
                )
                review_update = replace(review_update, zhongshu_groups=rows)
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
        if review_after is not None and zhongshu_drainout_parked(review_after):
            # Every non-frozen group is parked (STALLED/BLOCKED): no group can
            # make progress without a human, so surface the per-group blockers
            # instead of looping or dying in a generic budget block.
            return self._group_drainout_decision(context, event, review_after)
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
                return self._stuck_finding_decision(
                    context,
                    event,
                    no_progress_count=verdict.no_progress_count,
                )
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
            return self._human_gate_decision(
                context,
                event,
                self.item_stalled_reason,
                no_progress_count=verdict.no_progress_count,
            )
        if verdict.action == "BLOCKED":
            return self._blocked_decision(context, event, verdict.reason_code)
        return None

class ZhongshuFreezeCheckState(_ConcreteWorkflowState):
    name = "ZHONGSHU_FREEZE_CHECK"
    supported_actions = (
        "FREEZE_APPROVED", "APPROVE_FREEZE", "FREEZE_OK", "FREEZE_REJECTED",
        "REQUEST_ANALYST_EVIDENCE", "REQUEST_SOLVER_REVISION",
        "HUMAN_GATE", "PLAN_REVIEW", "BLOCKED",
        "RETRY", "BLOCK", "OPEN_HUMAN_GATE",
    )
    exhausted_reason = "ZHONGSHU_FREEZE_BUDGET_EXHAUSTED"
    doc_form_reason = "ZHONGSHU_DOC_FORM_INVALID"
    global_check_reason = "ZHONGSHU_FREEZE_GLOBAL_INVALID"

    def handle(self, context: WorkflowContext, event: DomainEvent) -> StateDecision:
        decision = self._handle_freeze(context, event)
        return self._with_plan_review(context, decision)

    def _with_plan_review(
        self,
        context: WorkflowContext,
        decision: StateDecision,
    ) -> StateDecision:
        """Attach the plan-review document and pause for human sign-off.

        Freeze approval is the Zhongshu completion point: the thin plan-review
        document (zhongshu-plan-review.md) is persisted here, and with the
        plan-review gate on the run pauses at HUMAN_GATE so the operator can
        read and sign off before the Menxia hand-over (2026-09-27 thin doc).
        """

        if decision.transition.action not in (
            "FREEZE_APPROVED",
            "APPROVE_FREEZE",
            "FREEZE_OK",
        ):
            return decision
        from ..zhongshu_plan_doc import (
            plan_review_doc_effect,
            render_plan_review_doc,
        )

        doc_text = ""
        try:
            doc_text = render_plan_review_doc(context)
        except Exception:  # a rendering slip must not block the freeze path
            logger.exception(
                "ZHONGSHU_PLAN_DOC_RENDER_FAILED task_id=%s",
                context.identity.task_id,
            )
        effects = list(decision.effects)
        if doc_text:
            effects.append(
                plan_review_doc_effect(
                    context.identity.task_id,
                    context.progression.sequence,
                    doc_text,
                )
            )
        gate_on = (
            context.parallel is None or context.parallel.zhongshu.plan_review_gate
        ) and not (
            context.parallel is not None and context.parallel.zhongshu.fast_track
        )
        if not gate_on:
            return replace(decision, effects=tuple(effects))
        # The approval decision already carries the Menxia dispatch effect
        # built for its original target; drop it here or the wave completes
        # into the gate and crashes it.  RESUME rebuilds the dispatch when the
        # run actually hands over.
        effects = [
            effect
            for effect in effects
            if getattr(effect, "effect_type", "")
            not in ("node_dispatch", "agent_dispatch")
        ]
        decision_id = (
            f"{context.identity.task_id}:plan-review:"
            f"{context.progression.sequence + 1}"
        )
        return replace(
            decision,
            transition=TransitionRequest(
                action="PLAN_REVIEW",
                reason_code="PLAN_REVIEW",
            ),
            effects=tuple(effects),
            update=replace(
                decision.update,
                progression=ProgressUpdate(resume_state="MENXIA_GROUP_SOLVER"),
                human_gate=HumanGateUpdate(
                    decision_id=decision_id,
                    resume_state="MENXIA_GROUP_SOLVER",
                    reason_code="PLAN_REVIEW",
                ),
            ),
        )

    def _handle_freeze(self, context: WorkflowContext, event: DomainEvent) -> StateDecision:
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
        if (
            event.name != "TIMEOUT"
            and action in MENXIA_FREEZE_ENTRY_ACTIONS
            and (context.parallel is None or not context.parallel.menxia.enabled)
        ):
            # The menxia machinery is disabled: the shared-document group
            # pipeline cannot run (its bindings and document join are gated
            # off), and the legacy item serial chain is retired.  Stop loudly
            # instead of silently shipping a review with no documents.
            return self._blocked_decision(
                context, event, "MENXIA_PARALLEL_DISABLED"
            )
        if event.name != "TIMEOUT" and action in MENXIA_FREEZE_ENTRY_ACTIONS:
            group_decision = self._group_freeze_decision(context, event, action)
            if group_decision is not None:
                return group_decision
        return super().handle(context, event)

    def _group_freeze_decision(
        self,
        context: WorkflowContext,
        event: DomainEvent,
        action: str,
    ) -> StateDecision | None:
        """Group-gated freeze release; ``None`` means legacy passthrough.

        A group-aware run (explicit group rows, no fast track) releases only
        ready groups: CONVERGED with every dependency group FROZEN and a
        re-verified document.  Global graph checks run once before the first
        group freezes.  Ready groups freeze and hand over to Menxia in the
        same decision (early hand-over): the group pipeline wave only
        dispatches FROZEN groups while the rest keep grinding in Zhongshu
        and the gate bounces the run back once its waves wrap up.  Only a
        freeze approval with nothing frozen at all loops back to the Solver
        naming the remaining groups.
        """

        review = context.review
        if review is None or not review.zhongshu_groups:
            return None
        if context.parallel is not None and context.parallel.zhongshu.fast_track:
            # Fast track auto-approves the freeze: every group freezes in one
            # stroke without document verification, so the group gate sees a
            # fully frozen board and the FROZEN-gated seeding opens the wave
            # for all groups at once.
            folded = tuple(
                replace(row, stage="FROZEN") for row in review.zhongshu_groups
            )
            decision = self._transition_decision(
                replace(context, review=replace(review, zhongshu_groups=folded)),
                event,
                action=action,
            )
            return self._attach_group_rows(context, decision, folded)
        rows = {row.group_id: row for row in review.zhongshu_groups}
        resets = zhongshu_group_resets_from_payload(event.payload)
        if resets:
            rows = {
                row.group_id: row
                for row in apply_zhongshu_group_resets(rows.values(), resets)
            }
        review_rows = replace(review, zhongshu_groups=tuple(rows.values()))
        if not any(row.stage == "FROZEN" for row in rows.values()):
            issues = _zhongshu_graph_issues(review)
            if issues:
                logger.warning(
                    "ZHONGSHU_FREEZE_GLOBAL_INVALID task_id=%s issues=%s",
                    context.identity.task_id,
                    list(issues),
                )
                decision = self._transition_decision(
                    context, event, action="FREEZE_REJECTED"
                )
                decision = self._attach_group_rows(
                    context, decision, tuple(rows.values())
                )
                return self._with_effect_payload(
                    decision,
                    reason_code=self.global_check_reason,
                    extra={"zhongshu_global_issues": list(issues)},
                )
        ready = freeze_ready_groups(review_rows)
        rejections = []
        for row in ready:
            violations = self._doc_violations(review_rows, row)
            if violations:
                rejections.append(
                    {"group_id": row.group_id, "violations": list(violations)}
                )
        if rejections:
            logger.warning(
                "ZHONGSHU_DOC_FORM_INVALID task_id=%s groups=%s",
                context.identity.task_id,
                [entry["group_id"] for entry in rejections],
            )
            # Violating groups fold back to REVIEWING so the next Solver
            # round re-authors their documents.
            for entry in rejections:
                row = rows[entry["group_id"]]
                rows[entry["group_id"]] = ZhongshuGroupState(
                    group_id=row.group_id,
                    doc_version=row.doc_version,
                    doc_hash=row.doc_hash,
                    doc_markdown=row.doc_markdown,
                    doc_source_hash=row.doc_source_hash,
                )
            decision = self._transition_decision(
                context, event, action="FREEZE_REJECTED"
            )
            decision = self._attach_group_rows(
                context, decision, tuple(rows.values())
            )
            return self._with_effect_payload(
                decision,
                reason_code=self.doc_form_reason,
                extra={"zhongshu_freeze_rejections": rejections},
            )
        for row in ready:
            rows[row.group_id] = replace(row, stage="FROZEN")
        folded = tuple(rows.values())
        remaining = tuple(
            row.group_id for row in folded if row.stage != "FROZEN"
        )
        if any(row.stage == "FROZEN" for row in folded):
            # Partial release: frozen groups enter the group pipeline now
            # (the wave dispatches only FROZEN groups); the remaining groups
            # keep converging in Zhongshu and the gate bounces the run back
            # when the pipeline waves wrap up.
            final_action = action
        else:
            # Nothing froze in this round: keep grinding the Solver instead
            # of entering Menxia with an empty wave.
            final_action = "REQUEST_SOLVER_REVISION"
        decision = self._transition_decision(
            # The dispatch effect must observe the folded rows (FROZEN
            # groups) so the group wave seeds exactly the handed-over
            # groups; the plain fold below keeps the committed update.
            replace(context, review=replace(review, zhongshu_groups=folded)),
            event,
            action=final_action,
        )
        decision = self._attach_group_rows(context, decision, folded)
        if remaining:
            logger.info(
                "ZHONGSHU_FREEZE_PARTIAL task_id=%s frozen=%s remaining=%s",
                context.identity.task_id,
                [row.group_id for row in folded if row.stage == "FROZEN"],
                list(remaining),
            )
            decision = self._with_effect_payload(
                decision,
                extra={"zhongshu_remaining_groups": list(remaining)},
            )
        return decision

    @staticmethod
    def _doc_violations(review: ReviewState, row) -> tuple[str, ...]:
        if not str(row.doc_markdown or "").strip():
            return ("doc_missing",)
        previous = row.doc_version - 1 if row.doc_version > 0 else None
        return group_doc_violations(
            row.doc_markdown,
            previous_version=previous,
            review=review,
            group_id=row.group_id,
        )

    @staticmethod
    def _attach_group_rows(
        context: WorkflowContext,
        decision: StateDecision,
        group_rows,
    ) -> StateDecision:
        review_update = decision.update.review
        if review_update is None:
            review_update = ReviewUpdate(
                revision_id=context.review.revision_id if context.review else ""
            )
        return replace(
            decision,
            update=replace(
                decision.update,
                review=replace(review_update, zhongshu_groups=group_rows),
            ),
        )

    @staticmethod
    def _with_effect_payload(
        decision: StateDecision,
        *,
        reason_code: str = "",
        extra: dict[str, object],
    ) -> StateDecision:
        effects = tuple(
            replace(effect, payload={**effect.payload, **extra})
            if isinstance(effect.payload, Mapping)
            else effect
            for effect in decision.effects
        )
        transition = (
            replace(decision.transition, reason_code=reason_code)
            if reason_code
            else decision.transition
        )
        return replace(decision, transition=transition, effects=effects)


class MenxiaItemSolverState(_ConcreteWorkflowState):
    name = "MENXIA_ITEM_SOLVER"
    supported_actions = (
        "FEASIBLE", "READY_FOR_ANALYST", "READY_FOR_CRITIC", "HUMAN_GATE", "BLOCKED",
        "RETRY", "BLOCK", "OPEN_HUMAN_GATE",
    )

    def handle(self, context: WorkflowContext, event: DomainEvent) -> StateDecision:
        self._require_current_state(context)
        self._require_event_task(context, event)
        decision = self._menxia_wave_decision(context, event)
        if decision is not None:
            return decision
        return super().handle(context, event)


class MenxiaItemAnalystState(_ConcreteWorkflowState):
    name = "MENXIA_ITEM_ANALYST"
    supported_actions = (
        "EVIDENCE_SUFFICIENT", "READY_FOR_CRITIC", "NEEDS_MORE_EVIDENCE",
        "REQUEST_SOLVER_REVISION", "HUMAN_GATE", "BLOCKED", "RETRY", "BLOCK", "OPEN_HUMAN_GATE",
    )

    def handle(self, context: WorkflowContext, event: DomainEvent) -> StateDecision:
        self._require_current_state(context)
        self._require_event_task(context, event)
        decision = self._menxia_wave_decision(context, event)
        if decision is not None:
            return decision
        return super().handle(context, event)


class MenxiaItemCriticState(_ConcreteWorkflowState):
    name = "MENXIA_ITEM_CRITIC"
    supported_actions = (
        "APPROVE_ITEM", "REVISE_ITEM", "SPLIT_ITEM", "MERGE_ITEM", "REMOVE_ITEM",
        "REQUEST_SOLVER_REVISION", "HUMAN_GATE", "BLOCKED", "RETRY", "BLOCK", "OPEN_HUMAN_GATE",
    )

    def handle(self, context: WorkflowContext, event: DomainEvent) -> StateDecision:
        self._require_current_state(context)
        self._require_event_task(context, event)
        decision = self._menxia_wave_decision(context, event)
        if decision is not None:
            return decision
        return super().handle(context, event)


class MenxiaGroupSolverState(_ConcreteWorkflowState):
    """Group pipeline Solver state (shared document model).

    The group advances as one pipeline record; the wave decision intercepts
    only the drain-out tail (parked groups with nothing active), exactly
    like the item pipeline.
    """

    name = "MENXIA_GROUP_SOLVER"
    supported_actions = (
        "FEASIBLE", "READY_FOR_ANALYST", "READY_FOR_CRITIC", "HUMAN_GATE", "BLOCKED",
        "RETRY", "BLOCK", "OPEN_HUMAN_GATE",
    )

    def handle(self, context: WorkflowContext, event: DomainEvent) -> StateDecision:
        self._require_current_state(context)
        self._require_event_task(context, event)
        decision = self._menxia_group_wave_decision(context, event)
        if decision is not None:
            return decision
        return super().handle(context, event)


class MenxiaGroupAnalystState(_ConcreteWorkflowState):
    name = "MENXIA_GROUP_ANALYST"
    supported_actions = (
        "EVIDENCE_SUFFICIENT", "READY_FOR_CRITIC", "NEEDS_MORE_EVIDENCE",
        "REQUEST_SOLVER_REVISION", "HUMAN_GATE", "BLOCKED", "RETRY", "BLOCK", "OPEN_HUMAN_GATE",
    )

    def handle(self, context: WorkflowContext, event: DomainEvent) -> StateDecision:
        self._require_current_state(context)
        self._require_event_task(context, event)
        decision = self._menxia_group_wave_decision(context, event)
        if decision is not None:
            return decision
        return super().handle(context, event)


class MenxiaGroupCriticState(_ConcreteWorkflowState):
    name = "MENXIA_GROUP_CRITIC"
    supported_actions = (
        "APPROVE_GROUP", "REQUEST_SOLVER_REVISION", "REQUEST_EVIDENCE",
        "HUMAN_GATE", "BLOCKED", "RETRY", "BLOCK", "OPEN_HUMAN_GATE",
    )

    def handle(self, context: WorkflowContext, event: DomainEvent) -> StateDecision:
        self._require_current_state(context)
        self._require_event_task(context, event)
        decision = self._menxia_group_wave_decision(context, event)
        if decision is not None:
            return decision
        return super().handle(context, event)


class MenxiaGroupGateState(_ConcreteWorkflowState):
    name = "MENXIA_GROUP_GATE"
    supported_actions = (
        "COMPLETE", "APPROVE_GROUP", "APPROVE_FREEZE", "NEXT_ITEM", "REQUEST_GROUP_REVISION",
        "REQUEST_NEXT_GROUP",
        "HUMAN_GATE", "BLOCKED", "RETRY", "BLOCK", "OPEN_HUMAN_GATE",
    )
    group_blocked_reason = "MENXIA_GROUP_BLOCKED"

    def handle(self, context: WorkflowContext, event: DomainEvent) -> StateDecision:
        self._require_current_state(context)
        self._require_event_task(context, event)
        action = self._event_action(event)
        review = context.review
        menxia_enabled = (
            review is not None
            and context.parallel is not None
            and context.parallel.menxia.enabled
        )
        group_rows = (
            tuple(review.menxia_groups or review.seed_menxia_groups())
            if review is not None and menxia_enabled and not review.menxia_items
            else ()
        )
        if action in {"COMPLETE", "APPROVE_GROUP"} and review is not None:
            if group_rows:
                # Shared-document mode: a group's approval completes all of
                # its items in one stroke; whatever is neither approved nor
                # parked goes back through the group pipeline.
                approved_groups = {
                    row.group_id for row in group_rows if row.stage == "APPROVED"
                }
                parked_groups = {
                    row.group_id
                    for row in group_rows
                    if row.stage in ("BLOCKED", "ESCALATED")
                }
                completed = set(review.completed_item_ids) | {
                    item.item_id
                    for item in review.task_items
                    if item.group_id in approved_groups
                }
                pending = [
                    item
                    for item in review.task_items
                    if item.item_id not in completed
                ]
                if pending:
                    if all(item.group_id in parked_groups for item in pending):
                        # Nothing resumable remains: the parked groups need
                        # a human, not another solver round.
                        return self._human_gate_decision(
                            context, event, self.group_blocked_reason
                        )
                    action = "NEXT_ITEM"
                if any(
                    row.stage != "FROZEN"
                    for row in (review.zhongshu_groups or ())
                ):
                    # Early hand-over: the menxia wave for the handed-over
                    # groups wrapped up, but unfrozen groups still converge
                    # in Zhongshu.  Bounce the run back to its Critic (the
                    # ping-pong edge) instead of finishing or re-entering
                    # the group pipeline with nothing seeded.
                    action = "REQUEST_NEXT_GROUP"
                decision = self._transition_decision(context, event, action=action)
                if completed != set(review.completed_item_ids):
                    base = (
                        decision.update.review
                        if decision.update.review is not None
                        else ReviewUpdate(revision_id=review.revision_id)
                    )
                    decision = replace(
                        decision,
                        update=replace(
                            decision.update,
                            review=replace(
                                base,
                                completed_item_ids=tuple(sorted(completed)),
                            ),
                        ),
                    )
                return decision
            parked = (
                {
                    row.item_id
                    for row in review.menxia_items
                    if row.stage in ("BLOCKED", "ESCALATED")
                }
                if menxia_enabled
                else set()
            )
            completed = set(review.completed_item_ids)
            pending = [
                item
                for item in review.task_items
                if item.item_id not in completed and item.item_id not in parked
            ]
            if pending:
                if review.next_menxia_item() is None:
                    raise InvariantViolation("Menxia task graph has no dependency-ready next item")
                action = "NEXT_ITEM"
        if action == "REQUEST_GROUP_REVISION" and menxia_enabled:
            payload = event.payload if isinstance(event.payload, Mapping) else {}
            if group_rows:
                named = _menxia_gate_revision_group_ids(payload, review)
                if not named:
                    # A revision demand without actionable groups cannot
                    # restart any Solver wave; hand the group to an operator.
                    return self._human_gate_decision(
                        context, event, self.group_blocked_reason
                    )
                event = replace(
                    event,
                    payload={
                        **payload,
                        "menxia_group_results": [
                            {
                                "group_id": group_id,
                                "action": "REQUEST_SOLVER_REVISION",
                                # A gate-issued revision demand grants a
                                # fresh attempt: un-park parked rows.
                                "budget_reset": True,
                            }
                            for group_id in named
                        ],
                    },
                )
                action = "REQUEST_GROUP_REVISION"
            else:
                named = _menxia_gate_revision_item_ids(payload)
                if not named:
                    # A revision demand without actionable items cannot restart
                    # any Solver wave; hand the group to an operator instead.
                    return self._human_gate_decision(
                        context, event, self.group_blocked_reason
                    )
                event = replace(
                    event,
                    payload={
                        **payload,
                        "menxia_item_results": [
                            {
                                "item_id": item_id,
                                "action": "REQUEST_SOLVER_REVISION",
                                # A gate-issued revision demand grants a fresh
                                # attempt: un-park escalated/blocked rows.
                                "budget_reset": True,
                            }
                            for item_id in named
                        ],
                    },
                )
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
    supported_actions = ("RESUME", "CANCEL", "PLAN_REVIEW_REJECTED")
    requires_resume_state = True

    def handle(
        self,
        context: WorkflowContext,
        event: DomainEvent,
    ) -> StateDecision:
        if event.name == "PLAN_REVIEW_REJECTED":
            # The operator reviewed the thin plan document and sent it back:
            # route to the planner with the note recorded as an active finding
            # so the next revision round sees it (2026-09-27 thin doc).
            self._require_current_state(context)
            self._require_event_task(context, event)
            payload = event.payload if isinstance(event.payload, Mapping) else {}
            answer = str(payload.get("answer") or "").strip()
            from .findings import Finding

            finding = Finding(
                finding_id=f"human-review-{context.progression.sequence}",
                severity="P1",
                status="OPEN",
                category="human_review",
                claim=answer[:400] or "人工预审退回：方案需修订",
                required_action="按人工批注修订方案后重新送审",
            )
            existing = context.review.findings if context.review else ()
            revision_id = (
                context.review.revision_id
                if context.review
                else f"{context.identity.task_id}:{context.progression.sequence + 1}"
            )
            return StateDecision(
                transition=TransitionRequest(
                    action="PLAN_REVIEW_REJECTED",
                    reason_code="PLAN_REVIEW_REJECTED",
                ),
                update=ContextUpdate(
                    human_gate=HumanGateUpdate(clear_gate=True),
                    review=ReviewUpdate(
                        revision_id=revision_id,
                        findings=tuple(existing) + (finding,),
                    ),
                ),
            )
        return super().handle(context, event)

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


# Evidence-packet projection fields/limits live in policies.zhongshu and are
# shared with the plan-level Critic prompt section; keep the local alias so the
# per-item slice keeps its historical bound name.
_MAX_ITEM_EVIDENCE_RECORDS = MAX_EVIDENCE_RECORDS


def _menxia_evidence_packet_update(
    context: WorkflowContext,
    payload: Mapping[str, object],
) -> dict[str, object] | None:
    """Evidence packet for this fold (``None`` keeps the current packet).

    Zhongshu folds carry the packet directly; menxia item waves carry
    ``menxia_evidence`` contributions that must be merged into the existing
    packet so the Analyst's fresh evidence reaches the item Critic without
    discarding the zhongshu records.
    """

    direct = payload.get("evidence_packet")
    if isinstance(direct, Mapping):
        return dict(direct)
    raw = payload.get("menxia_evidence")
    if not isinstance(raw, list) or not raw:
        return None
    return merge_menxia_evidence(
        context.review.evidence_packet if context.review else None,
        [row for row in raw if isinstance(row, Mapping)],
    )


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

    Selection against the record cap is demand-first: the Critic names the
    evidence it still misses inside its findings' claims and supporting
    evidence, and packet order alone hid exactly those records behind the cap
    (task-menxia-t1: the Critic re-demanded ``ev-workflow-state-001`` and
    ``ev-w02-005`` while the 8-record slice carried the packet's first 8 of
    18).  Records named by this item's open findings outrank item-addressed
    records, which outrank requirement-level ones.
    """

    packet = getattr(review, "evidence_packet", None)
    if not isinstance(packet, Mapping) or item is None:
        return {"records": [], "lens_workers": [], "lens_gaps": 0, "finding_responses": []}
    records: list[dict[str, object]] = []
    raw_updates = packet.get("evidence_updates")
    if not isinstance(raw_updates, list):
        raw_updates = []
    demanded_blob = " ".join(
        " ".join(part.split())
        for finding in getattr(review, "findings", ())
        if str(getattr(finding, "item_id", "")) == item.item_id
        and bool(getattr(finding, "active", True))
        for part in (
            str(getattr(finding, "claim", "") or ""),
            str(getattr(finding, "required_action", "") or ""),
            *(str(eid) for eid in getattr(finding, "supporting_evidence", ()) or ()),
        )
    )
    prioritized: list[tuple[int, dict[str, object]]] = []
    for value in raw_updates:
        if not isinstance(value, Mapping):
            continue
        record_item = str(value.get("item_id") or "").strip()
        requirement = str(value.get("requirement_id") or "").strip()
        if record_item and record_item != item.item_id:
            continue
        if not record_item and requirement not in item.source_requirement_ids:
            continue
        record = project_evidence_record(value)
        if not record:
            continue
        evidence_id = str(record.get("evidence_id") or "")
        if evidence_id and evidence_id in demanded_blob:
            tier = 0
        elif record_item:
            tier = 1
        else:
            tier = 2
        prioritized.append((tier, record))
    prioritized.sort(key=lambda pair: pair[0])
    records = [record for _, record in prioritized[:_MAX_ITEM_EVIDENCE_RECORDS]]
    worker_evidence = packet.get("worker_evidence")
    lens_workers = (
        sorted(str(key).strip() for key in worker_evidence if str(key).strip())
        if isinstance(worker_evidence, Mapping)
        else []
    )
    lens_gaps = max(0, expected_lenses - len(lens_workers)) if expected_lenses else 0
    # Deliver the Analyst's answers to this item's findings next to the
    # evidence they cite: the Critic dispositions what it can see, so an
    # answered finding must not be re-requested as if it were still open.
    item_finding_ids = {
        str(finding.finding_id)
        for finding in getattr(review, "findings", ())
        if str(getattr(finding, "item_id", "")) == item.item_id
    }
    responses: list[dict[str, object]] = []
    raw_responses = packet.get("finding_responses")
    if isinstance(raw_responses, list) and item_finding_ids:
        for value in raw_responses:
            if not isinstance(value, Mapping):
                continue
            if str(value.get("finding_id") or "") not in item_finding_ids:
                continue
            response: dict[str, object] = {}
            for key in ("finding_id", "answer", "evidence_ids", "suggested_disposition", "note"):
                raw = value.get(key)
                if raw in (None, "", [], {}):
                    continue
                response[key] = raw
            if "finding_id" in response:
                responses.append(response)
    return {
        "records": records,
        "lens_workers": lens_workers,
        "lens_gaps": lens_gaps,
        "finding_responses": responses,
    }


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
    expected_lenses = _expected_analyst_lenses(context)
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
                    "finding_responses": evidence["finding_responses"],
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


def _repair_menxia_stage_gap(
    review: ReviewState,
    target: str,
    rows: tuple[MenxiaItemState, ...],
) -> tuple[MenxiaItemState, ...]:
    """Resume repair for snapshots seeded before the pipeline records existed.

    A legacy snapshot restored mid-item has no menxia rows, so the stage wave
    for the resumed state would dispatch nothing.  Seed the active item into
    the resumed state's stage so the run continues exactly where it paused.
    """

    if rows or not review.active_item_id:
        return rows
    item = next(
        (entry for entry in review.task_items if entry.item_id == review.active_item_id),
        None,
    )
    if item is None:
        return rows
    return (
        MenxiaItemState(
            item_id=item.item_id,
            group_id=item.group_id,
            stage=MENXIA_STAGE_BY_TARGET[target],
        ),
    )


def _menxia_item_capsule(
    target: str,
    item: ReviewTaskItem,
    row: MenxiaItemState,
) -> str:
    """Instruction capsule for one item-scoped menxia worker."""

    lines = [
        (
            f"Menxia item pipeline job: {item.item_id} in group {item.group_id} "
            f"(stage {row.stage}, revision round {row.revision_round})."
        ),
        f"Title: {item.title}",
        f"Objective: {item.objective}",
    ]
    if item.dependencies:
        lines.append(f"Dependencies: {', '.join(item.dependencies)}")
    if target == "MENXIA_ITEM_SOLVER":
        lines.append(
            "Produce the implementation proposal for this item only; answer "
            "every item finding in responses_to_critic."
        )
    elif target == "MENXIA_ITEM_ANALYST":
        lines.append(
            "Run the evidence review for this item only; do not modify the plan."
        )
    else:
        lines.append(
            "Review this item only; every required change must be observable "
            "through the verification plan."
        )
        lines.append(
            "The item objective is frozen at plan-freeze time; runtime "
            "counters quoted inside it (state versions, hashes, component "
            "counts) are historical anchors, not current-state claims. Judge "
            "evidence against the latest verified snapshot and the current "
            "proposal, not the frozen text's volatile counters."
        )
    return "\n".join(lines)


def _menxia_item_dispatch_context(
    context: WorkflowContext,
    target: str,
    *,
    item_id: str,
    group_id: str,
    revision_id: str,
    plan_hash: str,
    row: MenxiaItemState | None = None,
    item_override: ReviewTaskItem | None = None,
    expected_lenses: int = 0,
) -> dict[str, object] | None:
    """Per-item dispatch context shared by wave bindings and serial dispatch."""

    review = context.review
    if review is None or not item_id:
        return None
    item = item_override
    if item is None:
        item = next(
            (entry for entry in review.task_items if entry.item_id == item_id),
            None,
        )
    if item is None:
        return None
    group_id = group_id or item.group_id
    if row is None:
        row = next(
            (entry for entry in review.menxia_items if entry.item_id == item_id),
            None,
        )
    if row is None:
        row = {state.item_id: state for state in review.seed_menxia_items()}.get(item_id)
    item_findings = findings_for_scope(
        review.findings, group_id=group_id, item_id=item_id
    )
    evidence = _item_evidence_context(review, item, expected_lenses)
    contract_id = _MENXIA_CONTRACT_BY_TARGET[target]
    editable = "none" if target == "MENXIA_ITEM_ANALYST" else f"items:{item_id}"
    solver_proposal = row.last_solver_proposal if row is not None else None
    envelope_ingredients = [
        {"key": "review.plan", "source": "context.json",
         "lifetime": "persisted",
         "slice": f"item:{item_id}"},
        {"key": "review.findings", "source": "context.json",
         "lifetime": "persisted",
         "slice": f"item:{item_id}+active"},
        {"key": "review.item_evidence", "source": "context.json",
         "lifetime": "persisted",
         "slice": f"item:{item_id}"},
    ]
    context_payload: dict[str, object] = {
        "menxia_dispatch_mode": "item_pipeline",
        "stage": MENXIA_STAGE_BY_TARGET[target],
        "revision_id": revision_id,
        "plan_hash": plan_hash,
        "group_id": group_id,
        "item_id": item_id,
        "revision_round": row.revision_round if row is not None else 0,
        "item": asdict(item),
        "item_findings": [_finding_payload(finding) for finding in item_findings],
        "item_evidence": evidence["records"],
        "finding_responses": evidence["finding_responses"],
    }
    if target == "MENXIA_ITEM_CRITIC":
        # The Critic reviews the proposal against fresh evidence; without the
        # persisted proposal it re-demands work the run already finished.
        if isinstance(solver_proposal, Mapping):
            context_payload["item_solver_proposal"] = dict(solver_proposal)
            envelope_ingredients.append(
                {"key": "review.item_solver_proposal", "source": "context.json",
                 "lifetime": "persisted",
                 "slice": f"item:{item_id}"},
            )
    context_payload["envelope"] = build_envelope(
        ingredients=envelope_ingredients,
        tools={"editable": editable, "contract": contract_id},
        product={
            "type": _MENXIA_PRODUCT_BY_TARGET[target],
            "contract": contract_id,
        },
    )
    return context_payload


def _menxia_item_bindings(
    context: WorkflowContext,
    target: str,
    *,
    request_id: str,
    revision_id: str,
    plan_hash: str,
) -> list[dict[str, object]]:
    """One wave binding per item currently sitting in the state's stage."""

    review = context.review
    if review is None:
        return []
    limits = context.parallel.menxia if context.parallel is not None else None
    rows = _repair_menxia_stage_gap(
        review,
        target,
        ready_stage_items(review, target=target, limits=limits),
    )
    if not rows:
        return []
    role, phase = _ROLE_BY_STATE[target]
    item_by_id = {item.item_id: item for item in review.task_items}
    expected_lenses = _expected_analyst_lenses(context)
    bindings: list[dict[str, object]] = []
    for index, row in enumerate(rows, start=1):
        item = item_by_id.get(row.item_id)
        if item is None:
            continue
        bindings.append(
            {
                "worker_id": f"menxia_{str(row.stage).lower()}-worker-{index:02d}",
                "agent_id": f"{phase.lower()}-{role.split('-')[-1]}-{index:02d}",
                "task_id": context.identity.task_id,
                "request_id": f"{request_id}:worker-{index:02d}",
                "role": role,
                "phase": phase,
                "group_id": row.group_id,
                "item_id": row.item_id,
                "prompt_ref": _menxia_item_capsule(target, item, row),
                "dispatch_context": _menxia_item_dispatch_context(
                    context,
                    target,
                    item_id=row.item_id,
                    group_id=row.group_id,
                    revision_id=revision_id,
                    plan_hash=plan_hash,
                    row=row,
                    item_override=item,
                    expected_lenses=expected_lenses,
                ),
            }
        )
    return bindings


def _menxia_group_baseline(review: ReviewState, group_id: str) -> str:
    """Requirement baseline a group's v1 document starts from."""

    lines: list[str] = []
    for item in sorted(
        (entry for entry in review.task_items if entry.group_id == group_id),
        key=lambda entry: (entry.order, entry.item_id),
    ):
        lines.append(f"### {item.item_id} {item.title}".strip())
        if item.objective:
            lines.append(item.objective)
        lines.append("")
    return "\n".join(lines).strip()


def _menxia_group_capsule(
    target: str,
    group_id: str,
    row: MenxiaGroupState,
    member_count: int,
) -> str:
    """Instruction capsule for one group-scoped menxia worker."""

    lines = [
        (
            f"Menxia group pipeline job: group {group_id} "
            f"(stage {row.stage}, revision round {row.revision_round}, "
            f"{member_count} items share one plan document)."
        ),
    ]
    if target == "MENXIA_GROUP_SOLVER":
        if row.doc_version == 0 and not row.doc_markdown.strip():
            lines.append(
                "First round: the document you receive is the v1 skeleton. "
                "Fill in the plan body and keep the title version at [v1]; "
                "only later revision rounds advance the version and declare "
                "changed sections in touched_scope."
            )
        else:
            lines.append(
                "Produce the next version of the group document: update the "
                "plan body, absorb or decline suggestions by id, and declare "
                "every body section you changed in touched_scope."
            )
    elif target == "MENXIA_GROUP_ANALYST":
        lines.append(
            "Review the group document against the requirement and append "
            "suggestions to the suggestion section only."
        )
    else:
        lines.append(
            "Attack the group document's body and append suggestions to the "
            "suggestion section only; route evidence gaps to REQUEST_EVIDENCE."
        )
    return "\n".join(lines)


def _menxia_group_dispatch_context(
    context: WorkflowContext,
    target: str,
    *,
    group_id: str,
    revision_id: str,
    plan_hash: str,
    row: MenxiaGroupState | None = None,
) -> dict[str, object] | None:
    """Per-group dispatch context (three-segment bundle, shared doc model)."""

    review = context.review
    if review is None or not group_id:
        return None
    if row is None:
        row = next(
            (entry for entry in review.menxia_groups if entry.group_id == group_id),
            None,
        )
    if row is None:
        row = {state.group_id: state for state in review.seed_menxia_groups()}.get(
            group_id
        )
    if row is None:
        return None
    members = [
        item for item in review.task_items if item.group_id == group_id
    ]
    group_findings = findings_for_scope(review.findings, group_id=group_id)
    doc_markdown = row.doc_markdown
    if not doc_markdown.strip() and target == "MENXIA_GROUP_SOLVER":
        # First round: the Solver receives a v1 skeleton it expands into the
        # first real document version.
        doc_markdown = render_initial(
            group_id=group_id,
            baseline_markdown=_menxia_group_baseline(review, group_id),
        )
    contract_id = _MENXIA_CONTRACT_BY_TARGET[target]
    editable = "none" if target != "MENXIA_GROUP_SOLVER" else f"groups:{group_id}"
    return {
        "menxia_dispatch_mode": "group_pipeline",
        "stage": MENXIA_GROUP_STAGE_BY_TARGET[target],
        "revision_id": revision_id,
        "plan_hash": plan_hash,
        "group_id": group_id,
        "revision_round": row.revision_round,
        "doc_version": row.doc_version,
        "doc_hash": row.doc_hash,
        "doc_markdown": doc_markdown,
        "requirement_markdown": row.requirement_markdown,
        "items": [asdict(item) for item in members],
        "group_findings": [_finding_payload(finding) for finding in group_findings],
        "envelope": build_envelope(
            ingredients=[
                {"key": "review.plan", "source": "context.json",
                 "lifetime": "persisted",
                 "slice": f"group:{group_id}"},
                {"key": "review.findings", "source": "context.json",
                 "lifetime": "persisted",
                 "slice": f"group:{group_id}+active"},
                {"key": "review.group_document", "source": "context.json",
                 "lifetime": "persisted"},
            ],
            tools={"editable": editable, "contract": contract_id},
            product={
                "type": _MENXIA_PRODUCT_BY_TARGET[target],
                "contract": contract_id,
            },
        ),
    }


def _menxia_group_bindings(
    context: WorkflowContext,
    target: str,
    *,
    request_id: str,
    revision_id: str,
    plan_hash: str,
) -> list[dict[str, object]]:
    """One wave binding per group currently sitting in the state's stage."""

    review = context.review
    if review is None:
        return []
    limits = context.parallel.menxia if context.parallel is not None else None
    rows = ready_stage_groups(review, target=target, limits=limits)
    if not rows:
        return []
    role, phase = _ROLE_BY_STATE[target]
    member_count = sum(
        1 for item in review.task_items if item.group_id == rows[0].group_id
    )
    bindings: list[dict[str, object]] = []
    for index, row in enumerate(rows, start=1):
        dispatch_context = _menxia_group_dispatch_context(
            context,
            target,
            group_id=row.group_id,
            revision_id=revision_id,
            plan_hash=plan_hash,
            row=row,
        )
        if dispatch_context is None:
            continue
        bindings.append(
            {
                "worker_id": f"menxia_group_{str(row.stage).lower()}-worker-{index:02d}",
                "agent_id": f"{phase.lower()}-{role.split('-')[-1]}-{index:02d}",
                "task_id": context.identity.task_id,
                "request_id": f"{request_id}:worker-{index:02d}",
                "role": role,
                "phase": phase,
                "group_id": row.group_id,
                "item_id": None,
                "prompt_ref": _menxia_group_capsule(
                    target, row.group_id, row, member_count
                ),
                "dispatch_context": dispatch_context,
            }
        )
    return bindings


def _menxia_group_gate_dispatch(
    context: WorkflowContext,
    *,
    request_id: str,
    prompt: object,
    revision_id: str,
    plan_hash: str,
) -> EffectRequest | None:
    """Single gate-agent dispatch, run only when every group is terminal."""

    review = context.review
    if review is None:
        return None
    # Shared-document mode: the group pipeline rows decide readiness.  The
    # item-row path only remains for a legacy review frozen before the
    # group pipeline existed.
    group_rows = (
        () if review.menxia_items
        else review.menxia_groups or review.seed_menxia_groups()
    )
    if group_rows:
        reports = menxia_group_pipeline_readiness(review, group_rows)
        stage_by_group = {row.group_id: row.stage for row in group_rows}
        round_by_group = {row.group_id: row.revision_round for row in group_rows}
        verdict_by_group = {row.group_id: row.last_verdict for row in group_rows}
        reason_by_group = {row.group_id: row.blocked_reason for row in group_rows}
        terminal_by_group = {
            group_id: stage in ("APPROVED", "BLOCKED", "ESCALATED")
            for group_id, stage in stage_by_group.items()
        }
    else:
        rows = review.menxia_items or review.seed_menxia_items()
        reports = menxia_group_readiness(review, rows)
        stage_by_group = {}
        round_by_group = {}
        verdict_by_group = {}
        reason_by_group = {}
        terminal_by_group = {}
    if not reports or not all(bool(report["ready"]) for report in reports):
        return None
    role, phase = _ROLE_BY_STATE["MENXIA_GROUP_GATE"]
    contract_id = _MENXIA_CONTRACT_BY_TARGET["MENXIA_GROUP_GATE"]
    # The gate agent judges group readiness; without a per-item review map it
    # used to demand decisions for terminal items (e.g. ESCALATED rows) the
    # pipeline had already parked, blocking convergence on phantom gaps.
    row_by_item = {row.item_id: row for row in (review.menxia_items or ())}
    item_reviews = [
        {
            "item_id": item.item_id,
            "group_id": item.group_id,
            "stage": (
                row_by_item[item.item_id].stage
                if item.item_id in row_by_item
                else stage_by_group.get(item.group_id, "SOLVING")
            ),
            "revision_round": (
                row_by_item[item.item_id].revision_round
                if item.item_id in row_by_item
                else round_by_group.get(item.group_id, 0)
            ),
            "last_verdict": (
                row_by_item[item.item_id].last_verdict
                if item.item_id in row_by_item
                else verdict_by_group.get(item.group_id, "")
            ),
            "blocked_reason": (
                row_by_item[item.item_id].blocked_reason
                if item.item_id in row_by_item
                else reason_by_group.get(item.group_id, "")
            ),
            "terminal": (
                row_by_item[item.item_id].stage in MENXIA_TERMINAL_STAGES
                if item.item_id in row_by_item
                else terminal_by_group.get(item.group_id, False)
            ),
            "title": item.title,
            "objective": item.objective,
        }
        for item in sorted(review.task_items, key=lambda entry: (entry.order, entry.item_id))
    ]
    group_context = [
        {
            "group_id": group.group_id,
            "order": group.order,
            "item_ids": list(group.item_ids),
        }
        for group in sorted(review.task_groups, key=lambda entry: (entry.order, entry.group_id))
    ]
    return EffectRequest(
        effect_id=f"dispatch:{request_id}",
        effect_type="agent_dispatch",
        task_id=context.identity.task_id,
        idempotency_key=request_id,
        payload_ref=context.request.payload_ref,
        payload={
            "issue_id": context.identity.issue_id,
            "request_id": request_id,
            "agent_id": f"{phase.lower()}-{role.split('-')[-1]}",
            "role": role,
            "phase": phase,
            "target_state": "MENXIA_GROUP_GATE",
            "prompt_ref": prompt.content,
            "request_payload_ref": context.request.payload_ref or "",
            "references": dict(prompt.references),
            "revision_id": revision_id,
            "plan_hash": plan_hash,
            "sequence": context.progression.sequence,
            "dispatch_context": {
                "menxia_dispatch_mode": "group_gate",
                "revision_id": revision_id,
                "plan_hash": plan_hash,
                "groups": [dict(report) for report in reports],
                "group_context": group_context,
                "item_reviews": item_reviews,
                "envelope": build_envelope(
                    ingredients=[
                        {"key": "review.findings", "source": "context.json",
                         "lifetime": "persisted"},
                        {"key": "review.task_items", "source": "context.json",
                         "lifetime": "persisted"},
                        {"key": "review.item_reviews", "source": "context.json",
                         "lifetime": "persisted"},
                        {"key": "review.group_context", "source": "context.json",
                         "lifetime": "persisted"},
                    ],
                    tools={"editable": "none", "contract": contract_id},
                    product={
                        "type": _MENXIA_PRODUCT_BY_TARGET["MENXIA_GROUP_GATE"],
                        "contract": contract_id,
                    },
                ),
            },
            "group_id": None,
            "item_id": None,
        },
    )


def _menxia_gate_revision_item_ids(payload: Mapping[str, object]) -> list[str]:
    """Extract the items a group gate verdict sends back to its Solver."""

    named: list[str] = []
    for source in (
        payload.get("revision_item_ids"),
        (
            payload.get("group_consistency", {}).get("revision_item_ids")
            if isinstance(payload.get("group_consistency"), Mapping)
            else None
        ),
    ):
        if isinstance(source, str):
            source = [source]
        if isinstance(source, list):
            named.extend(str(value).strip() for value in source if str(value).strip())
    changes = payload.get("required_changes")
    if isinstance(changes, list):
        for change in changes:
            if isinstance(change, Mapping):
                # Verdicts name items via either ``item_id`` or ``target``;
                # group-level targets (group-*) cannot restart a Solver wave.
                for key in ("item_id", "target"):
                    item_id = str(change.get(key) or "").strip()
                    if item_id.startswith("item-"):
                        named.append(item_id)
    consistency = payload.get("group_consistency")
    if isinstance(consistency, Mapping):
        decisions = consistency.get("item_decisions")
        if isinstance(decisions, list):
            for decision in decisions:
                if not isinstance(decision, Mapping):
                    continue
                if str(decision.get("action") or "") != "REVISE_ITEM":
                    continue
                item_id = str(decision.get("item_id") or "").strip()
                if item_id.startswith("item-"):
                    named.append(item_id)
    ordered: list[str] = []
    seen: set[str] = set()
    for item_id in named:
        if item_id not in seen:
            seen.add(item_id)
            ordered.append(item_id)
    return ordered


def _menxia_gate_revision_group_ids(
    payload: Mapping[str, object],
    review: ReviewState,
) -> list[str]:
    """Extract the groups a group-gate verdict sends back to its Solver.

    The verdict may name groups directly (``group_ids`` / group-level
    ``required_changes`` targets) or items; item-level names map back to
    their owning group so a mixed verdict still restarts whole groups.
    """

    named: list[str] = []
    for source in (payload.get("group_ids"), payload.get("groups")):
        if isinstance(source, str):
            source = [source]
        if isinstance(source, list):
            named.extend(str(value).strip() for value in source if str(value).strip())
    for change in payload.get("required_changes") or []:
        if not isinstance(change, (Mapping, str)):
            continue
        # A verdict may name its targets only inside the free-form
        # required_changes entries; accept the declared shapes: a bare
        # "group-..." string or an object with a group-level target field.
        candidates = (
            (change.get("target"), change.get("group_id"))
            if isinstance(change, Mapping)
            else (change,)
        )
        for value in candidates:
            token = str(value or "").strip()
            if token.startswith("group-"):
                named.append(token)
    group_prefix_ids = [
        group_id for group_id in named if group_id.startswith("group-")
    ]
    item_ids = _menxia_gate_revision_item_ids(payload)
    group_by_item = {item.item_id: item.group_id for item in review.task_items}
    ordered: list[str] = []
    seen: set[str] = set()
    for group_id in group_prefix_ids + [
        group_by_item.get(item_id, "") for item_id in item_ids
    ]:
        if group_id and group_id not in seen:
            seen.add(group_id)
            ordered.append(group_id)
    return ordered


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
    salvaged = [dict(row) for row in review.salvaged_task_reviews]
    group_rows = [asdict(row) for row in getattr(review, "zhongshu_groups", ()) or ()]
    packet = (
        review.evidence_packet
        if isinstance(getattr(review, "evidence_packet", None), Mapping)
        else {}
    )
    responses = [
        dict(entry)
        for entry in packet.get("finding_responses") or []
        if isinstance(entry, Mapping) and str(entry.get("finding_id") or "").strip()
    ]
    if (
        not findings
        and not ledger
        and not salvaged
        and not responses
        and not group_rows
    ):
        return None
    return {
        "findings": findings,
        "task_review_ledger": ledger,
        "salvaged_task_reviews": salvaged,
        "finding_responses": responses,
        "zhongshu_groups": group_rows,
    }


def _expected_analyst_lenses(context: WorkflowContext) -> int:
    """Analyst lens workers an evidence wave over the open findings dispatches."""

    if context.parallel is None:
        return 0
    review = context.review
    return expected_analyst_lenses(
        context.parallel.zhongshu.analyst_default_workers,
        (
            finding
            for finding in (review.findings if review is not None else ())
            if finding.active
        ),
    )


def _analyst_evidence_demand(
    context: WorkflowContext, findings: tuple[object, ...] | None = None
) -> dict[str, object] | None:
    """Dispatch-time snapshot of the Critic's demands for the Analyst fan-out.

    The demand rides the dispatch record so the transport gate can verify
    fulfillment mechanically (the targets exist in the workspace and the
    reply cites them) without parsing any excuse wording, and so the reply
    contract can require one ``finding_responses`` entry per listed finding.
    ``findings`` narrows the demand to one fan-out worker's slice.
    """

    review = context.review
    if review is None:
        return None
    active = (
        [finding for finding in review.findings if finding.active]
        if findings is None
        else list(findings)
    )
    if not active:
        return None
    demand: list[dict[str, object]] = []
    targets: list[dict[str, str]] = []
    seen_targets: set[tuple[str, str]] = set()
    for finding in active:
        finding_targets = [
            dict(target)
            for target in (finding.evidence_targets or ())
            if str(target.get("path") or "").strip()
        ]
        demand.append({
            "finding_id": finding.finding_id,
            "severity": finding.severity,
            "item_id": finding.item_id,
            "claim": finding.claim,
            "required_action": finding.required_action,
            "evidence_targets": finding_targets,
        })
        for target in finding_targets:
            key = (str(target.get("path") or ""), str(target.get("symbol") or ""))
            if key not in seen_targets:
                seen_targets.add(key)
                targets.append({"path": key[0], "symbol": key[1]})
    return {
        "active_findings": demand,
        "evidence_targets": targets,
    }


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
    # Frozen-zone protection: groups the freeze check handed to Menxia are
    # not re-reviewed; the Zhongshu Critic wave only covers the groups that
    # are still converging (early hand-over ping-pong).
    frozen_groups = {
        row.group_id
        for row in getattr(review, "zhongshu_groups", ()) or ()
        if getattr(row, "stage", "") == "FROZEN"
    }
    if frozen_groups:
        selected = [
            job for job in selected if job.group_id not in frozen_groups
        ]
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
    expected_lenses = _expected_analyst_lenses(context)
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
            "finding_responses": evidence["finding_responses"],
            # The Analyst answers cite the requirement contract (statement /
            # priority / scope per requirement); without it here the Critic
            # cannot verify those citations and re-demands evidence it already
            # holds (task-20260929-c261a8).
            "requirement_contract": [
                dict(entry)
                for entry in (getattr(review, "requirements", ()) or ())
            ],
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
                    job, item, group, evidence["records"],
                    active_findings=dispatch_context["active_findings"],
                    finding_responses=dispatch_context["finding_responses"],
                ),
                "dispatch_context": dispatch_context,
            }
        )
    return bindings


def _review_dispatch_unit(context: WorkflowContext) -> str:
    """Review wave granularity: "group" (2026-09-26) or legacy "item".

    The explicit A2/P3 item-workflow flag pins the whole item-granular path
    (per-item revision stoves and per-item task review) regardless of the
    review unit, so its dedicated suites keep exercising the rollback path.
    """

    if context.parallel is None:
        return "group"
    if context.parallel.zhongshu.item_workflow_enabled:
        return "item"
    return str(getattr(context.parallel.zhongshu, "review_unit", "") or "group")


def _group_member_dicts(review: object, group_id: str) -> list[dict[str, object]]:
    members = []
    for item in getattr(review, "task_items", ()) or ():
        if str(getattr(item, "group_id", "") or "") != group_id:
            continue
        members.append(
            {
                "item_id": item.item_id,
                "group_id": item.group_id,
                "title": item.title,
                "objective": item.objective,
                "dependencies": list(item.dependencies),
                "source_requirement_ids": list(item.source_requirement_ids),
                "acceptance_signals": list(item.acceptance_signals),
            }
        )
    return members


def _requirement_contract_markdown(review: object, limit: int = 6000) -> str:
    """Backfill a group capsule's empty requirement document slot.

    The capsule's [Requirement document] section normally carries the group's
    own document; until the Solver has authored one the slot was empty, so a
    Critic demanding "the authoritative requirement text" could not be
    answered from the bundle (task-20260929-c261a8).  Ship the canonical
    requirement contract instead — the same entries every ZHONGSHU bundle
    already carries — so every role reads one source of truth.
    """

    lines = ["[Authoritative requirement contract]"]
    for entry in getattr(review, "requirements", ()) or ():
        if isinstance(entry, dict):
            row = entry
        else:
            row = {
                key: getattr(entry, key, "")
                for key in (
                    "requirement_id", "statement", "priority", "scope",
                    "kind", "source", "acceptance_signal",
                )
            }
        requirement_id = str(row.get("requirement_id") or "").strip()
        statement = str(row.get("statement") or "").strip()
        if not requirement_id and not statement:
            continue
        meta = "/".join(
            str(row.get(key) or "").strip()
            for key in ("priority", "scope", "kind")
        ).strip("/")
        lines.append(f"- {requirement_id} ({meta}): {statement}")
        source = str(row.get("source") or "").strip()
        if source:
            lines.append(f"  source: {source}")
        signal = str(row.get("acceptance_signal") or "").strip()
        if signal:
            lines.append(f"  acceptance: {signal}")
    if len(lines) == 1:
        return ""
    text = "\n".join(lines)
    if len(text) > limit:
        text = text[:limit].rstrip() + "\n…(truncated)"
    return text


def _group_capsule_inputs(
    review: object, group_id: str
) -> tuple[list[dict[str, object]], list[tuple[str, str]], dict[str, str]]:
    """Split members into changed (verbatim) and held (surface+hash)."""

    ledger = {
        record.item_id: record
        for record in getattr(review, "task_review_ledger", ()) or ()
    }
    hashes = current_member_hashes(review, group_id)
    full: list[dict[str, object]] = []
    surface: list[tuple[str, str]] = []
    for entry in _group_member_dicts(review, group_id):
        item_id = str(entry["item_id"])
        digest = hashes.get(item_id, "")
        record = ledger.get(item_id)
        if (
            record is not None
            and str(getattr(record, "status", "") or "") == "APPROVED"
            and str(getattr(record, "task_hash", "") or "") == digest
        ):
            surface.append((item_id, digest))
        else:
            full.append(entry)
    return full, surface, hashes


def _group_review_bindings(
    context: WorkflowContext,
    revision_id: str,
    plan_hash: str,
    review_override: object | None = None,
) -> list[dict[str, object]] | None:
    """Build one review binding per group for the REVIEW_GROUP wave.

    Frozen groups (handed to Menxia) and groups whose surface hash still
    matches their last approval never dispatch (the group approval
    ratchet); groups failing the LLM-free preflight are routed out of the
    review wave so a doomed dispatch costs no worker round.
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
    doc_hashes = {
        row.group_id: str(getattr(row, "doc_hash", "") or "")
        for row in getattr(review, "zhongshu_groups", ()) or ()
    }
    doc_markdown = {
        row.group_id: str(getattr(row, "doc_markdown", "") or "")
        for row in getattr(review, "zhongshu_groups", ()) or ()
    }
    jobs = build_group_review_jobs(
        plan,
        revision,
        plan_hash=effective_plan_hash,
        doc_hashes=doc_hashes,
    )
    for entry in review_preflight_violations(review):
        logger.warning(
            "ZHONGSHU_REVIEW_PREFLIGHT task_id=%s violation=%s",
            context.identity.task_id,
            entry,
        )
    unfit = {
        entry.split(":", 1)[0]
        for entry in review_preflight_violations(review)
        if entry.startswith("group-")
    }
    frozen_groups = {
        row.group_id
        for row in getattr(review, "zhongshu_groups", ()) or ()
        if getattr(row, "stage", "") == "FROZEN"
    }
    selected = []
    for job in jobs:
        if job.group_id in frozen_groups or job.group_id in unfit:
            continue
        if group_approval_held(review, job.group_id):
            logger.info(
                "ZHONGSHU_GROUP_APPROVAL_HELD task_id=%s group_id=%s",
                context.identity.task_id,
                job.group_id,
            )
            continue
        selected.append(job)
    if not selected:
        # Nothing is contested but the wave still must produce a verdict
        # (the Menxia gate bounce re-entering the Critic, for one): fall
        # back to re-reviewing every fit, non-frozen group, mirroring the
        # legacy ``_select_review_jobs`` empty-selection fallback.  A wave
        # with nothing fit at all falls back to the non-frozen groups so the
        # FSM always gets a verdict to route.
        selected = [
            job
            for job in jobs
            if job.group_id not in frozen_groups and job.group_id not in unfit
        ]
    if not selected:
        selected = [
            job for job in jobs if job.group_id not in frozen_groups
        ]
    logger.info(
        "ZHONGSHU_GROUP_REVIEW_SCOPE task_id=%s revision_id=%s total=%s "
        "to_review=%s carried=%s",
        context.identity.task_id,
        revision,
        len(jobs),
        len(selected),
        len(jobs) - len(selected),
    )
    if not selected:
        return []
    findings_by_item: dict[str, list[object]] = {}
    for finding in getattr(review, "findings", ()) or ():
        if getattr(finding, "active", False) and getattr(finding, "item_id", ""):
            findings_by_item.setdefault(str(finding.item_id), []).append(finding)
    base_request_id = (
        f"{context.identity.task_id}:ZHONGSHU_CRITIC:{context.progression.sequence + 1}"
    )
    expected_lenses = _expected_analyst_lenses(context)
    bindings: list[dict[str, object]] = []
    for index, job in enumerate(selected, start=1):
        members_full, members_surface, member_hashes = _group_capsule_inputs(
            review, job.group_id
        )
        member_ids = [
            str(entry["item_id"]) for entry in _group_member_dicts(review, job.group_id)
        ]
        item_by_id = {
            item.item_id: item
            for item in getattr(review, "task_items", ()) or ()
            if str(getattr(item, "group_id", "") or "") == job.group_id
        }
        evidence_by_item: dict[str, list[dict[str, object]]] = {}
        finding_responses: list[dict[str, object]] = []
        evidence_rows: list[tuple[str, tuple[str, ...]]] = []
        for item_id in member_ids:
            evidence = _item_evidence_context(
                review, item_by_id.get(item_id), expected_lenses
            )
            evidence_by_item[item_id] = evidence["records"]
            finding_responses.extend(evidence["finding_responses"])
            evidence_rows.append(
                (
                    item_id,
                    tuple(
                        str(record.get("evidence_id") or "")
                        for record in evidence["records"]
                        if isinstance(record, dict)
                    ),
                )
            )
        group_findings = [
            _finding_payload(finding)
            for item_id in member_ids
            for finding in findings_by_item.get(item_id, [])
        ]
        dispatch_context: dict[str, object] = {
            "zhongshu_dispatch_mode": "group_review",
            "review_job_id": job.review_job_id,
            "revision_id": revision,
            "plan_hash": effective_plan_hash,
            "group_id": job.group_id,
            "group_hash": job.task_hash,
            "doc_hash": job.doc_hash,
            "member_ids": member_ids,
            "member_hashes": member_hashes,
            "active_findings": group_findings,
            "item_evidence_by_item": evidence_by_item,
            "finding_responses": finding_responses,
            # Analyst answers quote/cite this contract; the Critic must be
            # able to open the same entries to verify them (see the
            # task-review sibling above).
            "requirement_contract": [
                dict(entry)
                for entry in (getattr(review, "requirements", ()) or ())
            ],
                "envelope": build_envelope(
                    ingredients=[
                        {"key": "group_capsule", "source": "prompt.txt", "lifetime": "persisted",
                         "slice": f"group:{job.group_id}"},
                        {"key": "review.group_doc", "source": "context.json",
                         "lifetime": "persisted", "slice": f"group:{job.group_id}"},
                        {"key": "review.findings", "source": "context.json",
                         "lifetime": "persisted", "slice": f"group:{job.group_id}+active"},
                        {"key": "acceptance_standards", "source": "prompt.txt",
                         "lifetime": "persisted"},
                        {"key": "retry_feedback", "source": "prompt.txt",
                         "lifetime": "transient"},
                    ],
                    tools={"editable": "none", "contract": "group_review"},
                    product={"type": "group_review_verdict", "contract": "group_review"},
                ),
            }
        bindings.append(
            {
                "worker_id": f"zhongshu_critic-worker-{index:02d}",
                "agent_id": f"zhongshu-critic-{index:02d}",
                "task_id": context.identity.task_id,
                "request_id": f"{base_request_id}:group-{index:02d}",
                "role": "review-critic",
                "phase": "ZHONGSHU",
                "group_id": job.group_id,
                "item_id": "",
                # The capsule is the whole prompt.txt for a group worker, so
                # the re-ask feedback must ride inside it: without this a
                # rejected reply is re-sampled blind and the run re-pays the
                # same failure on the reply budget (task-20260927-de54aa
                # waves 8/10 died unstructured with an empty feedback channel).
                "prompt_ref": build_group_capsule(
                    group_id=job.group_id,
                    revision_id=revision,
                    doc_markdown=(
                        str(doc_markdown.get(job.group_id, "") or "")
                        or _requirement_contract_markdown(review)
                    ),
                    members_full=members_full,
                    members_surface=members_surface,
                    findings=group_findings,
                    finding_responses=finding_responses,
                    evidence=evidence_rows,
                ) + retry_feedback(
                    context,
                    "ZHONGSHU_CRITIC",
                    worker_id=f"zhongshu_critic-worker-{index:02d}",
                ),
                "dispatch_context": dispatch_context,
            }
        )
    return bindings


def _group_revise_bindings(
    context: WorkflowContext,
    revision_id: str,
    plan_hash: str,
    *,
    affected_groups: Iterable[str],
    editable_by_group: Mapping[str, Iterable[str]],
    review_override: object | None = None,
) -> list[dict[str, object]]:
    """One group-revision solver binding per affected group (parallel wave)."""

    review = review_override if review_override is not None else context.review
    revision = revision_id or (review.revision_id if review else "")
    effective_plan_hash = plan_hash or (review.plan_hash if review else "")
    findings_by_item: dict[str, list[object]] = {}
    for finding in getattr(review, "findings", ()) or ():
        if getattr(finding, "active", False) and getattr(finding, "item_id", ""):
            findings_by_item.setdefault(str(finding.item_id), []).append(finding)
    doc_markdown = {
        row.group_id: str(getattr(row, "doc_markdown", "") or "")
        for row in getattr(review, "zhongshu_groups", ()) or ()
    }
    base_request_id = (
        f"{context.identity.task_id}:ZHONGSHU_SOLVER:{context.progression.sequence + 1}"
    )
    bindings: list[dict[str, object]] = []
    for index, group_id in enumerate(
        dict.fromkeys(str(value).strip() for value in affected_groups if str(value).strip()),
        start=1,
    ):
        editable = sorted(
            {
                str(value).strip()
                for value in editable_by_group.get(group_id, ())
                if str(value).strip()
            }
        )
        editable_set = set(editable)
        member_dicts = _group_member_dicts(review, group_id)
        members_full, members_surface, member_hashes = _group_capsule_inputs(
            review, group_id
        )
        batch_findings = [
            _finding_payload(finding)
            for entry in member_dicts
            for finding in findings_by_item.get(str(entry["item_id"]), [])
        ]
        bindings.append(
            {
                "worker_id": f"zhongshu_solver-worker-{index:02d}",
                "agent_id": f"zhongshu-solver-{index:02d}",
                "task_id": context.identity.task_id,
                "request_id": f"{base_request_id}:group-{index:02d}",
                "role": "review-solver",
                "phase": "ZHONGSHU",
                "group_id": group_id,
                "item_id": "",
                "prompt_ref": build_group_capsule(
                    group_id=group_id,
                    revision_id=revision,
                    doc_markdown=(
                        str(doc_markdown.get(group_id, "") or "")
                        or _requirement_contract_markdown(review)
                    ),
                    members_full=[
                        entry
                        for entry in members_full
                        if str(entry["item_id"]) in editable_set
                    ],
                    members_surface=[
                        (item_id, digest)
                        for item_id, digest in members_surface
                        if item_id not in editable_set
                    ],
                    findings=batch_findings,
                    finding_responses=(),
                    evidence=(),
                    header=(
                        f"Group revision job: group {group_id} (revision "
                        f"{revision}). Rewrite only the editable member tasks "
                        "listed below and this group's requirement document "
                        "(§8 must close over the member acceptance signals). "
                        "Every other member and every other group is frozen: "
                        "return them byte-identical or omit them. Each patched "
                        "member must keep its item_id unchanged; copy only the fields the "
                        "member already has and do not add a group_id it lacks. "
                        "Reply with "
                        "action READY_FOR_CRITIC plus the patched member items "
                        "and this group's document."
                    ),
                ) + retry_feedback(
                    context,
                    "ZHONGSHU_SOLVER",
                    worker_id=f"zhongshu_solver-worker-{index:02d}",
                ),
                "dispatch_context": {
                    "zhongshu_dispatch_mode": "group_revise",
                    "revision_id": revision,
                    "plan_hash": effective_plan_hash,
                    "group_id": group_id,
                    "group_hash": group_surface_hash(
                        group_id,
                        member_hashes.values(),
                        doc_hash=doc_markdown.get(group_id, ""),
                    )
                    if member_hashes
                    else "",
                    "editable_item_ids": editable,
                    "batch_findings": batch_findings,
                    "member_hashes": member_hashes,
                    # Same authoritative array the Critic's bundle carries:
                    # the revising Solver quotes requirement text from here
                    # instead of guessing (task-20260929-c261a8 problem B).
                    "requirement_contract": [
                        dict(entry)
                        for entry in (getattr(review, "requirements", ()) or ())
                    ],
                    "envelope": build_envelope(
                        ingredients=[
                            {"key": "group_capsule", "source": "prompt.txt", "lifetime": "persisted",
                             "slice": f"group:{group_id}"},
                            {"key": "review.group_doc", "source": "context.json",
                             "lifetime": "persisted", "slice": f"group:{group_id}"},
                            {"key": "retry_feedback", "source": "prompt.txt",
                             "lifetime": "transient"},
                        ],
                        tools={"editable": "items:" + (",".join(editable) or "none"),
                               "contract": "group_revise"},
                        product={"type": "group_revision_patch", "contract": "group_revise"},
                    ),
                },
            }
        )
    return bindings


def _select_review_jobs(
    jobs: object,
    review: object,
) -> list[object]:
    """Re-review a job only when its content, dependencies, or verdict changed.

    Two carry rules keep a retry from re-paying for verdicts it already has:
    an ``APPROVED`` ledger record with matching hashes (the approval ratchet)
    and a salvaged row from a failed wave with matching hashes (the row is
    replayed verbatim by the fan-in).  A hash mismatch — the Solver patched the
    task or its dependencies — invalidates both and forces a fresh review.
    """

    ledger = {
        record.item_id: record for record in getattr(review, "task_review_ledger", ())
    }
    salvaged = {
        str(row.get("item_id") or ""): row
        for row in getattr(review, "salvaged_task_reviews", ()) or ()
        if isinstance(row, Mapping)
    }
    selected: list[object] = []
    for job in jobs:
        salvage = salvaged.get(job.item_id)
        if (
            salvage is not None
            and str(salvage.get("reviewed_task_hash") or "") == job.task_hash
            and str(salvage.get("reviewed_dependency_hash") or "") == job.dependency_hash
        ):
            continue
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
    remint_findings: bool = False,
    task_id: str = "",
) -> tuple[object, ...]:
    """Merge findings and close those owned by a task the Critic approved.

    ``remint_findings`` (critic rounds only) re-addresses incoming finding
    ids *before* anything folds, so an id can never name two opinions and a
    re-raised opinion rebinds onto the id its earlier round already owns
    (2026-09-28 efficiency plan Task 2).  Solver rounds must not remint:
    their dispositions reference the ids they echo.
    """

    if remint_findings:
        incoming, remints = remint_incoming_finding_ids(existing, incoming)
        for entry in remints:
            logger.warning(
                "FINDING_ID_REMINTED task_id=%s item_id=%s from=%s to=%s rule=%s",
                task_id or "-",
                entry["item_id"] or "-",
                entry["from"] or "<none>",
                entry["to"],
                entry["rule"],
            )
    incoming, rebinds = rebind_restatement_findings(existing, incoming)
    for rebind in rebinds:
        logger.warning(
            "TASK_REVIEW_FINDING_REBOUND item_id=%s from=%s to=%s shared_anchor=%s",
            rebind["item_id"],
            rebind["from"] or "<none>",
            rebind["to"],
            rebind["shared_anchor"],
        )
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


def _zhongshu_graph_issues(review: ReviewState) -> tuple[str, ...]:
    """Graph-level checks the freeze must satisfy before the first FROZEN row.

    Mirrors the structural gate's dependency-cycle detection over the task
    projection; runs once per run (skipped once any group is FROZEN) so a
    late freeze approval cannot loop on the same global finding.
    """

    by_item = {
        item.item_id: {"dependencies": tuple(item.dependencies or ())}
        for item in review.task_items or ()
    }
    return tuple(
        "DEPENDENCY_CYCLE:" + ",".join(cycle)
        for cycle in _dependency_cycles(by_item)
    )


def mechanical_freeze_ready(context: WorkflowContext) -> tuple[bool, list[str]]:
    """Objective freeze preconditions a group round must already satisfy.

    Group rounds only — a legacy (non-group) round never qualifies: it has
    neither the surface ratchet nor the ``_group_freeze_decision``
    re-verification as a backstop, so the freeze-check agent's holistic
    judgment is the only safety net and the mechanical path must not fire.

    Conditions, all re-derived from committed state at dispatch time:
    1. every group row is terminal — ``FROZEN``, or ``CONVERGED`` with the
       approval ratchet intact (``group_approval_held`` re-verifies the
       member/doc surface hash, so a Solver patch after approval breaks it);
    2. the item ledger closes with fresh review-surface hashes — the exact
       material the group fold wrote (``current_member_hashes``), so a stale
       APPROVED row forces the agent hop instead of a mechanical release;
    3. the dependency graph is sane — no cycles, and every item dependency
       resolves to a known item (group_dependencies drops unknown endpoints,
       so the resolution check must run at item granularity).

    Which groups actually freeze NOW stays with ``_group_freeze_decision``
    (``freeze_ready_groups`` requires dependency rows FROZEN, plus document
    verification): the mechanical precheck only decides whether the agent hop
    could add any information, so it is deliberately looser and relies on
    that decision as the defense in depth (worst case: partial release).
    """

    review = context.review
    if review is None or not review.zhongshu_groups:
        return False, ["NON_GROUP_ROUND"]
    failures: list[str] = []
    rows = {row.group_id: row for row in review.zhongshu_groups}
    member_hashes: dict[str, str] = {}
    for group_id, row in rows.items():
        stage = str(getattr(row, "stage", "") or "")
        if stage == "FROZEN" or (
            stage == "CONVERGED" and group_approval_held(review, group_id)
        ):
            member_hashes[group_id] = current_member_hashes(review, group_id)
            continue
        failures.append(f"GROUP_NOT_TERMINAL:{group_id}:{stage or 'EMPTY'}")
    if failures:
        return False, failures
    ledger = {
        record.item_id: record
        for record in getattr(review, "task_review_ledger", ()) or ()
    }
    seen_items: set[str] = set()
    for group_id, group_hashes in member_hashes.items():
        for item_id, task_hash in group_hashes.items():
            seen_items.add(item_id)
            record = ledger.get(item_id)
            if (
                record is None
                or str(record.status or "") != "APPROVED"
                or str(record.task_hash or "") != task_hash
            ):
                failures.append(f"LEDGER_STALE:{item_id}")
    for item in review.task_items or ():
        item_id = str(item.item_id)
        if item_id and item_id not in seen_items:
            failures.append(f"ITEM_UNGROUPED:{item_id}")
    if failures:
        return False, failures
    issues = _zhongshu_graph_issues(review)
    for issue in issues:
        failures.append(issue)
    item_ids = {str(item.item_id) for item in review.task_items or ()}
    for item in review.task_items or ():
        for dependency in item.dependencies or ():
            if str(dependency) not in item_ids:
                failures.append(
                    f"DEPENDENCY_UNRESOLVED:{item.item_id}:{dependency}"
                )
    return not failures, failures


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
    group_reviews = payload.get("group_reviews")
    if isinstance(group_reviews, list) and group_reviews:
        # Group-verdict wave: a REVISE_GROUP row attempts every member of
        # that group, so the group revision budget charges exactly the
        # groups the wave sent back to the Solver.
        attempted: list[str] = []
        for row in group_reviews:
            if isinstance(row, Mapping) and str(row.get("action") or "") == "REVISE_GROUP":
                attempted.extend(
                    str(value) for value in (row.get("member_ids") or ()) if str(value)
                )
        return tuple(dict.fromkeys(attempted))
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
        item_id = resolve_finding_item_id(finding)
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
    re-dispatching approved tasks, non-approved tasks with a valid salvage row
    are replayed verbatim by the fan-in (see ``salvaged_task_reviews``), and
    the retry charges a task's stall budget only when its fresh re-review
    joins cleanly -- so one worker's protocol slip must not spend other
    items' stall budget twice.
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


def _salvaged_task_review_rows(
    context: WorkflowContext,
    payload: Mapping[str, object],
    task_items: object = None,
) -> tuple[dict[str, object], ...] | None:
    """Project the salvage-carry set after folding one review payload.

    A failed Critic wave stores the full verdict rows its successful workers
    produced (``salvaged_task_reviews`` on the aggregate); the retry then
    re-dispatches only the tasks still missing and the fan-in replays the
    carried rows.  The set evolves by three rules:

    - a failed round's payload replaces the carried rows for the items it
      salvaged (fresh salvage wins); rows for already-approved tasks are
      dropped because the approval ratchet already holds them;
    - any round that produced a fresh verdict for an item drops that item's
      carried row (a real ledger row exists now);
    - rows for discarded items disappear with the item.
    """

    review = context.review
    previous_rows = {
        str(row.get("item_id") or ""): dict(row)
        for row in (review.salvaged_task_reviews if review else ())
        if isinstance(row, Mapping)
    }
    failed_round = str(payload.get("action") or "") == "FAIL"
    raw_salvaged = payload.get("salvaged_task_reviews")
    reviewed_items = {
        str(entry.get("item_id") or "")
        for entry in (
            payload.get("task_reviews") if isinstance(payload.get("task_reviews"), list) else []
        )
        if isinstance(entry, Mapping)
    }
    if not failed_round and not reviewed_items and not previous_rows:
        return None
    merged: dict[str, dict[str, object]] = {}
    if failed_round and isinstance(raw_salvaged, list):
        for row in raw_salvaged:
            if not isinstance(row, Mapping):
                continue
            item_id = str(row.get("item_id") or "")
            action = str(row.get("action") or "")
            if not item_id or not str(row.get("reviewed_task_hash") or ""):
                continue
            if action not in {"TASK_APPROVED", "TASK_CHANGES_REQUIRED"}:
                continue
            if action == "TASK_APPROVED":
                # The ratchet already holds approved tasks in the ledger; a
                # carried row would only duplicate that decision.
                continue
            merged[item_id] = dict(row)
    for item_id, row in previous_rows.items():
        if item_id in merged or item_id in reviewed_items:
            continue
        merged[item_id] = row
    discarded = _discarded_item_ids(context, task_items)
    for item_id in discarded:
        merged.pop(item_id, None)
    return tuple(merged[item_id] for item_id in sorted(merged))


def _salvaged_analyst_worker_payloads(
    context: WorkflowContext,
    payload: Mapping[str, object],
) -> tuple[dict[str, object], ...] | None:
    """Project the analyst-wave salvage-carry set after folding one payload.

    A failed Analyst evidence wave stores the full evidence payloads its
    successful workers produced (``salvaged_worker_payloads`` on the
    aggregate); the retry then re-dispatches only the missing workers and the
    fan-in replays the carried payloads.  The key follows the
    ``ReviewUpdate`` convention: present in the payload replaces the carried
    set (the failed round carries rows, the healthy round carries ``[]`` to
    reset), absent preserves whatever is already stored.
    """

    raw = payload.get("salvaged_worker_payloads")
    if not isinstance(raw, list):
        return None
    return tuple(
        dict(item)
        for item in raw
        if isinstance(item, Mapping) and str(item.get("worker_id") or "")
    )


def _salvaged_analyst_workers(
    context: WorkflowContext,
    revision_id: str,
) -> dict[str, dict[str, object]]:
    """Carried analyst evidence payloads that are still replayable.

    Rows only stand in for a finished worker while the revision they were
    salvaged under is still the live one; anything else would inject stale
    evidence into a fan-in that already stamps a newer revision.
    """

    review = context.review
    workers: dict[str, dict[str, object]] = {}
    for row in (review.salvaged_worker_payloads if review else ()):
        if not isinstance(row, Mapping):
            continue
        if str(row.get("salvage_revision_id") or "") != revision_id:
            continue
        worker_id = str(row.get("worker_id") or "")
        if worker_id:
            workers[worker_id] = dict(row)
    return workers


def _task_capsule_text(
    job: object,
    item: ReviewTaskItem | None,
    group: ReviewTaskGroup | None,
    evidence_records: object = (),
    active_findings: object = (),
    finding_responses: object = (),
) -> str:
    active_p0_ids = [
        str(finding.get("finding_id") or "")
        for finding in (active_findings or ())
        if isinstance(finding, Mapping)
        and str(finding.get("severity") or "").strip().upper() == "P0"
    ]
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
    responses = [
        response
        for response in (finding_responses or ())
        if isinstance(response, Mapping) and str(response.get("finding_id") or "")
    ]
    if responses:
        answered_ids = ", ".join(
            str(response.get("finding_id")) for response in responses
        )
        lines.append(
            "Analyst answers (dispatch_context.finding_responses) address "
            f"finding(s) {answered_ids}. Each answer carries file:line "
            "evidence and a suggested_disposition: accept an answer whose "
            "evidence covers the demand (close the finding with "
            "decision=RESOLVED naming it) or rebut it by naming the concrete "
            "defect with file:line; re-requesting the supplied evidence is a "
            "protocol violation."
        )
    lines.append(acceptance_standard_hint())
    lines.append(
        "dispatch_context.active_findings lists the canonical findings already "
        "recorded for this task. This is a hard output gate, not a suggestion: "
        "a finding that continues, re-words, narrows, or translates an existing "
        "active finding MUST reuse its finding_id verbatim and MUST write the "
        "claim in the same language as that canonical claim. Switching between "
        "Chinese and English (or re-wording) the same issue across rounds does "
        "not create a new finding; live runs show such alternation minting new "
        "ids for already-recorded issues, which resets the convergence counters "
        "and burns revision rounds. Mint a new finding_id only for a genuinely "
        "new issue that no active finding covers, and keep its claim language "
        "stable in later rounds."
    )
    if active_p0_ids:
        lines.append(
            "Delta disposition gate: every active P0 finding listed in "
            "dispatch_context.active_findings (finding_id: "
            f"{', '.join(active_p0_ids)}) MUST be explicitly answered in this "
            "reply. Re-raise it in findings with its canonical finding_id "
            "(STILL_OPEN), or add a finding_responses entry with "
            "finding_id + response=RESOLVED (name what changed, with "
            "evidence) or response=ACCEPT (accept the risk; put the "
            "follow-up plan in note). A silent P0 is a contract violation: "
            "the reply is rejected and re-dispatched."
        )
    lines.append(
        "Return TASK_APPROVED only when no active P0/P1 finding applies to this "
        "task. If the task itself is fine but the run lacks the investigation "
        "needed to judge it, return REQUEST_ANALYST_EVIDENCE instead: the "
        "Analyst collects evidence, while the Solver can only edit plan text "
        "and cannot manufacture evidence. Otherwise return "
        "TASK_CHANGES_REQUIRED with review_checks and task-scoped findings. "
        "Do not echo revision ids or hashes; the orchestrator stamps them. "
        "Copy the envelope's structured_output_schema_hash verbatim from the "
        "injected contract."
    )
    lines.append(
        "When you return TASK_CHANGES_REQUIRED, every finding MUST carry "
        "required_action: one concrete, minimal modification suggestion naming "
        "the item field to change (objective, acceptance_signals, dependencies, "
        "or source_requirement_ids) and the direction to move it. The Solver "
        "reads required_action verbatim as its revision brief, so state the "
        "correction you would accept as RESOLVED instead of restating the "
        "complaint."
    )
    lines.append(
        "REQUEST_ANALYST_EVIDENCE must ask only for evidence that exists NOW "
        "(repository state, plan text, dispatch records). No task in this run "
        "has executed yet, so another task's future output (results, rankings, "
        "computed sets) cannot be evidence; if your concern depends on what a "
        "task will produce, judge the task's stated contract instead and "
        "return TASK_CHANGES_REQUIRED."
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
            MenxiaGroupSolverState(),
            MenxiaGroupAnalystState(),
            MenxiaGroupCriticState(),
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
    "MenxiaGroupAnalystState",
    "MenxiaGroupCriticState",
    "MenxiaGroupSolverState",
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


