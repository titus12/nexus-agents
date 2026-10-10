"""Pure concrete state objects for the linear workflow.

The objects in this module are domain policies only.  They create typed
``StateDecision`` values and never perform I/O, persistence, transport, or
concurrency work.  The runtime executes the returned effect intents through
typed ports after the decision has been durably committed.
"""
from __future__ import annotations
from collections.abc import Mapping
from dataclasses import asdict, replace
import logging
from types import MappingProxyType
from typing import ClassVar, Protocol, runtime_checkable
from ..plan_utils import canonical_plan_hash
from ..dispatch_envelope import build_envelope
from .context import DeliveryUpdate, HumanGateUpdate, MenxiaGroupState, MenxiaItemState, ParallelUpdate, ProgressUpdate, RecoveryUpdate, ReviewUpdate, ReviewState, ReviewTaskItem, WorkflowContext, apply_review_update
from .decisions import ContextUpdate, EffectRequest, StateDecision
from .errors import FailureRecord, InvariantViolation, is_infrastructure_failure, is_reply_failure
from .events import DomainEvent, TransitionRequest
from .policies.review_findings import MAX_EVIDENCE_RECORDS, merge_findings, project_evidence_record
from .policies.analyst_partition import expected_analyst_lenses
from .policies.prompts import build_prompt
from .transitions import ALL_STATES, TransitionRegistry
from .policies.item_findings import item_findings
from .policies.menxia import MENXIA_ACTIVE_STAGES, MENXIA_ITEM_TARGETS, MENXIA_STAGE_BY_TARGET, MENXIA_TERMINAL_STAGES, apply_menxia_item_results, findings_for_scope, menxia_group_readiness, menxia_has_blockers, menxia_stage_census, merge_menxia_evidence, ready_stage_items, remove_plan_item
from .policies.menxia_group import MENXIA_GROUP_ACTIVE_STAGES, MENXIA_GROUP_STAGE_BY_TARGET, MENXIA_GROUP_TARGETS, apply_menxia_group_results, menxia_group_pipeline_readiness, menxia_group_stage_census, menxia_groups_have_blockers, ready_stage_groups
from .menxia_doc import BODY_PART_TITLES, body_protocol_prompt, render_initial
_MENXIA_CONTRACT_BY_TARGET = {'MENXIA_ITEM_SOLVER': 'nexus.menxia.item_solver.v1', 'MENXIA_ITEM_ANALYST': 'nexus.menxia.item_analyst.v1', 'MENXIA_ITEM_CRITIC': 'nexus.menxia.item_critic.v1', 'MENXIA_GROUP_SOLVER': 'nexus.menxia.group_solver.v1', 'MENXIA_GROUP_ANALYST': 'nexus.menxia.group_analyst.v1', 'MENXIA_GROUP_CRITIC': 'nexus.menxia.group_critic.v1', 'MENXIA_GROUP_GATE': 'nexus.menxia.group_gate.v1'}
_MENXIA_PRODUCT_BY_TARGET = {'MENXIA_ITEM_SOLVER': 'menxia_item_proposal', 'MENXIA_ITEM_ANALYST': 'menxia_item_evidence', 'MENXIA_ITEM_CRITIC': 'menxia_item_review', 'MENXIA_GROUP_SOLVER': 'menxia_group_doc', 'MENXIA_GROUP_ANALYST': 'menxia_group_doc', 'MENXIA_GROUP_CRITIC': 'menxia_group_doc', 'MENXIA_GROUP_GATE': 'menxia_group_gate'}

def _finding_payload(finding: object) -> dict[str, object]:
    """Serialize one finding for the Solver prompt context."""
    to_dict = getattr(finding, 'to_dict', None)
    if callable(to_dict):
        return dict(to_dict())
    if isinstance(finding, Mapping):
        return dict(finding)
    return {}

def _resolve_plan_hash(payload: Mapping[str, object]) -> str | None:
    raw = payload.get('plan_hash') or payload.get('reviewed_plan_hash') or ''
    if raw:
        return str(raw)
    plan = payload.get('plan')
    if isinstance(plan, dict) and plan.get('items'):
        return canonical_plan_hash(plan)
    return None
_TASK_REVIEW_RESULT_INVALID = 'NODE_TASK_REVIEW_RESULT_INVALID'

def _failure_error_code(payload: Mapping[str, object]) -> str:
    """Best-effort error code from a FAIL event payload."""
    raw = payload.get('failure')
    if isinstance(raw, Mapping):
        return str(raw.get('error_code') or '')
    return str(payload.get('error_code') or '')

def _failure_budget(error_code: object) -> str:
    """Return the retry budget that pays for one retryable failure.

    Routing and accounting both read this so they cannot disagree about which
    counter a retry charged; keeping them separate let a reply failure be routed
    to one budget while incrementing another.
    """
    code = str(error_code or '').strip()
    if code == _TASK_REVIEW_RESULT_INVALID or is_reply_failure(code):
        return 'reply'
    if is_infrastructure_failure(code):
        return 'external'
    return 'convergence'
_ROLE_BY_STATE: dict[str, tuple[str, str]] = {'MENXIA_ITEM_SOLVER': ('review-solver', 'MENXIA'), 'MENXIA_ITEM_ANALYST': ('review-analyst', 'MENXIA'), 'MENXIA_ITEM_CRITIC': ('review-critic', 'MENXIA'), 'MENXIA_GROUP_SOLVER': ('review-solver', 'MENXIA'), 'MENXIA_GROUP_ANALYST': ('review-analyst', 'MENXIA'), 'MENXIA_GROUP_CRITIC': ('review-critic', 'MENXIA'), 'MENXIA_GROUP_GATE': ('review-critic', 'MENXIA')}
logger = logging.getLogger('review_orchestrator_fsm')

@runtime_checkable
class WorkflowState(Protocol):
    """Interface implemented by every concrete workflow state."""
    name: str

    def enter(self, context: WorkflowContext) -> StateDecision:
        ...

    def handle(self, context: WorkflowContext, event: DomainEvent) -> StateDecision:
        ...

    def on_timeout(self, context: WorkflowContext) -> StateDecision:
        ...

    def on_resume(self, context: WorkflowContext) -> StateDecision:
        ...

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

    def _next_node_fail_streak(self, context: WorkflowContext, error_code: str='') -> int:
        """Consecutive-FAIL count this state would reach with one more FAIL.

        Zhongshu counts consecutive failures in the same retry-budget class.
        A rejected citation followed by an unrelated runtime crash does not
        prove the citation repair repeated. Cumulative budgets remain bounded.
        Menxia and legacy snapshots without last_failure retain their behavior.
        """
        if context.recovery.node_fail_state == self.name:
            previous = context.recovery.last_failure
            return context.recovery.node_fail_streak + 1
        return 1

    def handle(self, context: WorkflowContext, event: DomainEvent) -> StateDecision:
        self._require_current_state(context)
        self._require_event_task(context, event)
        action = self._event_action(event)
        if event.name == 'TIMEOUT':
            timeout = self.on_timeout(context)
            return replace(timeout, update=replace(timeout.update, progression=replace(timeout.update.progression or ProgressUpdate(), entered_at=event.occurred_at)))
        if event.name == 'FAIL' and isinstance(event.payload, Mapping) and bool(event.payload.get('retryable')):
            error_code = _failure_error_code(event.payload)
            streak = self._next_node_fail_streak(context, error_code)
            if context.recovery.max_node_fail_streak > 0 and streak >= context.recovery.max_node_fail_streak:
                decision = self._human_gate_decision(context, event, 'NODE_FAIL_STREAK_EXHAUSTED')
                recovery = decision.update.recovery or RecoveryUpdate()
                return replace(decision, update=replace(decision.update, recovery=replace(recovery, node_fail_streak=streak, node_fail_state=self.name)))
            budget = _failure_budget(error_code)
            if budget == 'reply':
                action = 'RETRY' if context.recovery.reply_retry_count < context.recovery.max_reply_retries else 'HUMAN_GATE'
            elif budget == 'external':
                if context.recovery.external_retry_count < context.recovery.max_external_retries:
                    action = 'RETRY'
            elif context.recovery.retry_count < context.recovery.max_retries:
                action = 'RETRY'
        if action not in self.supported_actions and action not in {'FAIL', 'CANCEL'}:
            raise InvariantViolation(f'event {event.name!r} is not supported by state {self.name!r}')
        return self._transition_decision(context, event, action=action)

    def on_timeout(self, context: WorkflowContext) -> StateDecision:
        self._require_current_state(context)
        failure = FailureRecord(failure_id=f'timeout:{context.identity.task_id}:{context.progression.sequence}', stage='remote_run', owner_component=self.name, task_id=context.identity.task_id, state=self.name, sequence=context.progression.sequence, node_run_id=context.parallel.active_node_run_id, worker_id=None, effect_id=None, error_code='AGENT_TIMEOUT', retryable=context.recovery.timeout_retry_count < context.recovery.max_timeout_retries, message=f'agent timeout in {self.name}', cause_type='Timeout')
        action = 'RETRY' if failure.retryable else 'FAIL'
        return StateDecision(transition=TransitionRequest(action=action, reason_code=failure.error_code), update=ContextUpdate(progression=ProgressUpdate(resume_state=self.name), recovery=RecoveryUpdate(timeout_retry_count=context.recovery.timeout_retry_count + 1, last_failure=failure)))

    def on_resume(self, context: WorkflowContext) -> StateDecision:
        self._require_current_state(context)
        raise InvariantViolation(f'resume handling is not supported for state {self.name!r}')

    def _transition_decision(self, context: WorkflowContext, event: DomainEvent, *, action: str | None=None) -> StateDecision:
        payload = event.payload if isinstance(event.payload, Mapping) else {}
        action = action or event.name
        target = self._transitions.target_for(self.name, action, context.progression.resume_state if action == 'RESUME' else None)
        update = self._payload_update(context, event)
        if action in {'RETRY', 'BLOCK', 'BLOCKED', 'OPEN_HUMAN_GATE', 'HUMAN_GATE'}:
            update = ContextUpdate(progression=ProgressUpdate(resume_state=self.name), recovery=update.recovery, human_gate=update.human_gate, parallel=update.parallel, review=update.review, delivery=update.delivery, audit=update.audit)
        if action in {'OPEN_HUMAN_GATE', 'HUMAN_GATE'} and isinstance(payload, Mapping):
            decision_id = str(payload.get('decision_id') or f'{context.identity.task_id}:human-gate:{context.progression.sequence + 1}')
            update = replace(update, progression=ProgressUpdate(resume_state=self.name), human_gate=HumanGateUpdate(decision_id=decision_id, resume_state=self.name, reason_code=self._reason_code(event), message_id=str(payload['message_id']) if payload.get('message_id') else None))
        if action == 'RETRY' and event.name == 'FAIL':
            failure = update.recovery.last_failure if update.recovery is not None else self._failure_from_payload(context, payload)
            budget = _failure_budget(failure.error_code if failure is not None else '')
            streak = self._next_node_fail_streak(context, failure.error_code if failure is not None else '')
            if budget == 'reply':
                recovery = RecoveryUpdate(reply_retry_count=context.recovery.reply_retry_count + 1, last_failure=failure, node_fail_streak=streak, node_fail_state=self.name)
            elif budget == 'external':
                recovery = RecoveryUpdate(external_retry_count=context.recovery.external_retry_count + 1, last_failure=failure, node_fail_streak=streak, node_fail_state=self.name)
            else:
                recovery = RecoveryUpdate(retry_count=context.recovery.retry_count + 1, last_failure=failure, node_fail_streak=streak, node_fail_state=self.name)
            update = replace(update, recovery=recovery)
        if (event.name == 'NODE_COMPLETED' or str(getattr(event, 'event_id', '') or '').endswith(':SUCCEEDED')) and action not in {'RETRY', 'BLOCK', 'BLOCKED', 'OPEN_HUMAN_GATE', 'HUMAN_GATE'}:
            recovery = update.recovery or RecoveryUpdate()
            update = replace(update, recovery=replace(recovery, reply_retry_count=0, node_fail_streak=0, node_fail_state='', clear_last_failure=True))
            if context.review is not None:
                review = update.review or ReviewUpdate(revision_id=context.review.revision_id)
                update = replace(update, review=replace(review, clear_evidence_requester=True))
        if action == 'RESUME' and self.name == 'HUMAN_GATE':
            update = replace(update, human_gate=HumanGateUpdate(clear_gate=True))
        progression = update.progression or ProgressUpdate()
        update = replace(update, progression=replace(progression, entered_at=event.occurred_at or progression.entered_at))
        if target not in {'HUMAN_GATE', 'RETRY_WAIT', 'BLOCKED', 'PERSISTENCE_DEGRADED', 'FAILED', 'CANCELLED', 'DONE'}:
            pending_requirements: tuple[dict[str, object], ...] = ()
            if update.review is not None and update.review.requirements is not None:
                pending_requirements = update.review.requirements
            elif context.review is not None:
                pending_requirements = context.review.requirements
            effective_review = apply_review_update(context.review, update.review)
            pending_review = None
            if effective_review is not None and update.review is not None and (update.review.task_items is not None):
                pending_review = effective_review
            effect = self._dispatch_effect(context, target, revision_id=str(payload.get('revision_id') or '') or (effective_review.revision_id if effective_review else '') or (context.review.revision_id if context.review else ''), plan_hash=_resolve_plan_hash(payload) or (context.review.plan_hash if context.review else '') or '', canonical_requirements=pending_requirements, pending_review=pending_review, effective_review=effective_review)
            review_update = update.review
            if target in MENXIA_ITEM_TARGETS and context.review is not None:
                limits = context.parallel.menxia if context.parallel is not None else None
                rows = ready_stage_items(context.review, target=target, limits=limits)
                if rows:
                    base = review_update if review_update is not None else ReviewUpdate(revision_id=context.review.revision_id)
                    review_update = replace(base, active_group_id=rows[0].group_id, active_item_id=rows[0].item_id)
            elif target in MENXIA_GROUP_TARGETS and context.review is not None:
                rows = ready_stage_groups(context.review, target=target)
                if rows:
                    base = review_update if review_update is not None else ReviewUpdate(revision_id=context.review.revision_id)
                    review_update = replace(base, active_group_id=rows[0].group_id, active_item_id=None)
            update = ContextUpdate(progression=update.progression, review=review_update, recovery=update.recovery, human_gate=update.human_gate, parallel=ParallelUpdate(active_node_run_id=str(effect.payload.get('node_run_id'))) if effect.effect_type == 'node_dispatch' else ParallelUpdate(clear_active_node_run=True), delivery=DeliveryUpdate(active_request_id=str(effect.payload.get('request_id') or ''), dispatch_operation_id=None, idempotency_key=effect.idempotency_key, status='PENDING', phase=str(effect.payload.get('phase') or ''), role=str(effect.payload.get('role') or ''), expected_agent_id=str(effect.payload.get('agent_id') or ''), attempt=context.delivery.attempt + 1), audit=update.audit)
        elif target in {'HUMAN_GATE', 'DONE'}:
            if target == 'HUMAN_GATE':
                notification_key = f'{context.identity.task_id}:human-gate:{context.progression.sequence + 1}'
                decision_id = str(payload.get('decision_id') or notification_key)
                update = replace(update, delivery=DeliveryUpdate(active_request_id=decision_id, idempotency_key=notification_key, status='PENDING', phase='HUMAN_GATE', role='human-gate'))
            effect = None
        else:
            effect = None
        return StateDecision(transition=TransitionRequest(action=action, reason_code=self._reason_code(event)), update=update, effects=(effect,) if effect is not None else ())

    def _payload_update(self, context: WorkflowContext, event: DomainEvent) -> ContextUpdate:
        payload = dict(event.payload) if isinstance(event.payload, Mapping) else {}
        if event.name == 'APPROVE_ITEM' and context.review is not None:
            payload.setdefault('group_id', context.review.active_group_id)
            payload.setdefault('item_id', context.review.active_item_id)
            payload.setdefault('completed_item_id', context.review.active_item_id)
        review_update = self._review_update(context, payload)
        recovery_update = None
        delivery_update = None
        if event.name not in {'RETRY', 'BLOCK', 'BLOCKED', 'OPEN_HUMAN_GATE', 'HUMAN_GATE', 'FAIL', 'CANCEL', 'AGENT_FAILED', 'NODE_FAILED'}:
            delivery_update = DeliveryUpdate(status='COMPLETED', active_request_id=str(payload['request_id']) if payload.get('request_id') else None, dispatch_operation_id=str(payload['operation_id']) if payload.get('operation_id') else None, external_message_id=str(payload['external_message_id']) if payload.get('external_message_id') else None, last_result_ref=str(payload['result_artifact_id']) if payload.get('result_artifact_id') else None)
        if event.name in {'FAIL', 'AGENT_FAILED', 'NODE_FAILED'}:
            recovery_update = RecoveryUpdate(last_failure=self._failure_from_payload(context, payload))
            delivery_update = DeliveryUpdate(status='FAILED', active_request_id=str(payload['request_id']) if payload.get('request_id') else None, dispatch_operation_id=str(payload['operation_id']) if payload.get('operation_id') else None, external_message_id=str(payload['external_message_id']) if payload.get('external_message_id') else None, last_result_ref=str(payload['result_artifact_id']) if payload.get('result_artifact_id') else None)
        return ContextUpdate(review=review_update, recovery=recovery_update, delivery=delivery_update, parallel=ParallelUpdate(clear_active_node_run=True) if event.name == 'NODE_COMPLETED' else None)

    @staticmethod
    def _review_update(context, payload):
        review = context.review
        if review is None:
            return None
        from .findings import Finding
        raw = payload.get('findings')
        if raw is not None and (not isinstance(raw, list)):
            raise InvariantViolation('findings payload must be an array')
        findings = merge_findings(review.findings, tuple((Finding.from_dict(dict(row)) for row in raw or [] if isinstance(row, Mapping))))
        item_results = [dict(row) for row in payload.get('menxia_item_results', []) if isinstance(row, Mapping)]
        group_results = [dict(row) for row in payload.get('menxia_group_results', []) if isinstance(row, Mapping)]
        item_rows = apply_menxia_item_results(review, item_results, max_rounds=review.max_item_revision_rounds) if item_results else None
        group_rows = apply_menxia_group_results(review, group_results, max_rounds=review.max_menxia_group_rounds) if group_results else None
        completed = set(review.completed_item_ids)
        if payload.get('completed_item_id'):
            completed.add(str(payload['completed_item_id']))
        if payload.get('action') == 'APPROVE_ITEM' and payload.get('item_id'):
            completed.add(str(payload['item_id']))
        completed.update((row.item_id for row in item_rows or () if row.stage == 'APPROVED'))
        removed = [str(row.get('item_id') or '') for row in item_results if row.get('action') == 'REMOVE_ITEM']
        patched_plan = None
        if removed:
            patched = review
            for identity in removed:
                patched = replace(patched, plan=remove_plan_item(patched.plan, identity))
            patched_plan = patched.plan
        has_data = raw is not None or item_results or group_results or (completed != set(review.completed_item_ids)) or any((k in payload for k in ('group_id', 'item_id', 'evidence_packet', 'plan_ref')))
        if not has_data:
            return None
        return ReviewUpdate(revision_id=review.revision_id, plan_schema=review.plan_schema, plan_base_ref=review.plan_revision.reference if review.plan_revision else '', plan=patched_plan, task_items=tuple((item for item in review.task_items if item.item_id not in removed)) if removed and review.plan_schema != 'canonical-v1' else None, task_groups=tuple((replace(group, item_ids=tuple((identity for identity in group.item_ids if identity not in removed))) for group in review.task_groups)) if removed and review.plan_schema != 'canonical-v1' else None, findings=findings, active_group_id=str(payload['group_id']) if payload.get('group_id') else None, active_item_id=str(payload['item_id']) if payload.get('item_id') else None, completed_item_ids=tuple(sorted(completed)), menxia_items=item_rows, menxia_groups=group_rows, evidence_packet=_menxia_evidence_packet_update(context, payload))

    @staticmethod
    def _failure_from_payload(context: WorkflowContext, payload: Mapping[str, object]) -> FailureRecord:
        raw = payload.get('failure')
        if isinstance(raw, Mapping):
            data = dict(raw)
            data.setdefault('failure_id', f'failure:{context.identity.task_id}:{context.progression.sequence}')
            data.setdefault('stage', 'workflow')
            data.setdefault('owner_component', context.progression.state)
            data.setdefault('task_id', context.identity.task_id)
            data.setdefault('state', context.progression.state)
            data.setdefault('sequence', context.progression.sequence)
            data.setdefault('node_run_id', context.parallel.active_node_run_id)
            data.setdefault('worker_id', None)
            data.setdefault('effect_id', None)
            data.setdefault('error_code', 'WORKFLOW_FAILURE')
            data.setdefault('retryable', False)
            data.setdefault('message', str(payload.get('reason') or 'workflow failure'))
            data.setdefault('cause_type', 'DomainEvent')
            return FailureRecord(**data)
        return FailureRecord(failure_id=f'failure:{context.identity.task_id}:{context.progression.sequence}', stage='workflow', owner_component=context.progression.state, task_id=context.identity.task_id, state=context.progression.state, sequence=context.progression.sequence, node_run_id=context.parallel.active_node_run_id, worker_id=None, effect_id=None, error_code=str(payload.get('error_code') or 'WORKFLOW_FAILURE'), retryable=bool(payload.get('retryable', False)), message=str(payload.get('reason') or 'workflow failure'), cause_type='DomainEvent')

    @staticmethod
    def _dispatch_effect(context: WorkflowContext, target: str, *, revision_id: str='', plan_hash: str='', canonical_requirements: tuple[dict[str, object], ...]=(), pending_review: object | None=None, effective_review: ReviewState | None=None) -> EffectRequest:
        if effective_review is not None:
            context = replace(context, review=effective_review)
        role, phase = _ROLE_BY_STATE[target]
        request_id = f'{context.identity.task_id}:{target}:{context.progression.sequence + 1}'
        prompt = build_prompt(context, target_state=target)
        next_item = context.review.next_menxia_item() if target == 'MENXIA_ITEM_SOLVER' and context.review is not None else None
        if target in MENXIA_ITEM_TARGETS and context.review is not None:
            limits = context.parallel.menxia if context.parallel is not None else None
            if limits is not None and limits.enabled:
                bindings = _menxia_item_bindings(context, target, request_id=request_id, revision_id=revision_id, plan_hash=plan_hash or '')
                if bindings:
                    rows = context.review.menxia_items or context.review.seed_menxia_items()
                    return EffectRequest(effect_id=f'node:{request_id}', effect_type='node_dispatch', task_id=context.identity.task_id, idempotency_key=request_id, payload_ref=context.request.payload_ref, payload={'issue_id': context.identity.issue_id, 'request_id': request_id, 'phase': phase, 'role': role, 'target_state': target, 'state': target, 'node_run_id': f'node:{request_id}', 'prompt_ref': prompt.content, 'request_payload_ref': context.request.payload_ref or '', 'revision_id': revision_id, 'plan_hash': plan_hash, 'sequence': context.progression.sequence, 'dispatch_mode': 'menxia_item_pipeline', 'bindings': bindings, 'menxia_stage_census': {row.item_id: row.stage for row in rows}, 'group_id': None, 'item_id': None})
        if target in MENXIA_GROUP_TARGETS and context.review is not None:
            limits = context.parallel.menxia if context.parallel is not None else None
            if limits is not None and limits.enabled:
                bindings = _menxia_group_bindings(context, target, request_id=request_id, revision_id=revision_id, plan_hash=plan_hash or '')
                if bindings:
                    rows = context.review.menxia_groups or context.review.seed_menxia_groups()
                    return EffectRequest(effect_id=f'node:{request_id}', effect_type='node_dispatch', task_id=context.identity.task_id, idempotency_key=request_id, payload_ref=context.request.payload_ref, payload={'issue_id': context.identity.issue_id, 'request_id': request_id, 'phase': phase, 'role': role, 'target_state': target, 'state': target, 'node_run_id': f'node:{request_id}', 'prompt_ref': prompt.content, 'request_payload_ref': context.request.payload_ref or '', 'revision_id': revision_id, 'plan_hash': plan_hash, 'sequence': context.progression.sequence, 'dispatch_mode': 'menxia_group_pipeline', 'bindings': bindings, 'menxia_stage_census': {row.group_id: row.stage for row in rows}, 'group_id': None, 'item_id': None})
        if target == 'MENXIA_GROUP_GATE' and context.review is not None:
            limits = context.parallel.menxia if context.parallel is not None else None
            if limits is not None and limits.enabled:
                gate_dispatch = _menxia_group_gate_dispatch(context, request_id=request_id, prompt=prompt, revision_id=revision_id, plan_hash=plan_hash or '')
                if gate_dispatch is not None:
                    return gate_dispatch
        agent_id = f"{phase.lower()}-{role.split('-')[-1]}"
        payload: dict[str, object] = {'issue_id': context.identity.issue_id, 'request_id': request_id, 'agent_id': agent_id, 'role': role, 'phase': phase, 'target_state': target, 'prompt_ref': prompt.content, 'request_payload_ref': context.request.payload_ref or '', 'references': dict(prompt.references), 'revision_id': revision_id, 'plan_hash': plan_hash, 'sequence': context.progression.sequence, 'group_id': next_item.group_id if next_item else None, 'item_id': next_item.item_id if next_item else None}
        if target in MENXIA_ITEM_TARGETS and context.review is not None:
            item_id = str(payload.get('item_id') or '') or str(context.review.active_item_id or '')
            dispatch_context = _menxia_item_dispatch_context(context, target, item_id=item_id, group_id='', revision_id=revision_id, plan_hash=plan_hash or '')
            if dispatch_context is not None:
                payload['dispatch_context'] = dispatch_context
                payload['item_id'] = item_id or None
                payload['group_id'] = dispatch_context.get('group_id') or None
        if target in MENXIA_GROUP_TARGETS and context.review is not None:
            rows = context.review.menxia_groups or context.review.seed_menxia_groups()
            group_id = str(context.review.active_group_id or '') or (rows[0].group_id if rows else '')
            dispatch_context = _menxia_group_dispatch_context(context, target, group_id=group_id, revision_id=revision_id, plan_hash=plan_hash or '')
            if dispatch_context is not None:
                payload['dispatch_context'] = dispatch_context
                payload['group_id'] = group_id or None
        return EffectRequest(effect_id=f'dispatch:{request_id}', effect_type='agent_dispatch', task_id=context.identity.task_id, idempotency_key=request_id, payload_ref=context.request.payload_ref, payload=payload)

    @staticmethod
    def _event_action(event: DomainEvent) -> str:
        if event.name != 'NODE_COMPLETED':
            return event.name
        payload = event.payload if isinstance(event.payload, Mapping) else {}
        aggregate = payload.get('aggregate')
        action = payload.get('action')
        if not action and isinstance(aggregate, Mapping):
            action = aggregate.get('action')
        if not isinstance(action, str) or not action:
            raise InvariantViolation('node completion must contain one domain action')
        return action

    @staticmethod
    def _reason_code(event: DomainEvent) -> str:
        if isinstance(event.payload, Mapping):
            reason_code = event.payload.get('reason_code')
            if isinstance(reason_code, str) and reason_code:
                return reason_code
        return event.name

    def _blocked_decision(self, context: WorkflowContext, event: DomainEvent, reason: str) -> StateDecision:
        """Stop an unbounded convergence loop and record why for the operator."""
        decision = self._transition_decision(context, event, action='BLOCKED')
        return replace(decision, transition=TransitionRequest(action='BLOCKED', reason_code=reason), update=replace(decision.update, recovery=RecoveryUpdate(blocked_reason=reason)))

    def _human_gate_decision(self, context: WorkflowContext, event: DomainEvent, reason: str, *, no_progress_count: int | None=None) -> StateDecision:
        """Hand the workflow to an operator with an explicit reason code."""
        decision = self._transition_decision(context, event, action='HUMAN_GATE')
        human_gate = decision.update.human_gate
        if human_gate is not None:
            human_gate = replace(human_gate, reason_code=reason)
        recovery = replace(decision.update.recovery or RecoveryUpdate(), blocked_reason=reason)
        if no_progress_count is not None:
            recovery = replace(recovery, no_progress_count=no_progress_count)
        return replace(decision, transition=TransitionRequest(action='HUMAN_GATE', reason_code=reason), update=replace(decision.update, human_gate=human_gate, recovery=recovery))

    def _menxia_wave_decision(self, context: WorkflowContext, event: DomainEvent) -> StateDecision | None:
        """Stage-barrier override for a menxia item wave completion.

        Returns a decision only for the drain-out tail: when no item is left
        in any active stage and at least one is parked (ESCALATED/BLOCKED),
        the run goes to a human with MENXIA_GROUP_GATE as the resume point so
        the group gate can run after the operator decides.  Everything else
        (including per-item budget escalation, which just parks the item) is
        routed by the joiner's aggregate action.
        """
        if event.name != 'NODE_COMPLETED' or context.review is None:
            return None
        if context.parallel is None or not context.parallel.menxia.enabled:
            return None
        payload = event.payload if isinstance(event.payload, Mapping) else {}
        raw_results = payload.get('menxia_item_results')
        if not isinstance(raw_results, list) or not raw_results:
            return None
        results = [dict(row) for row in raw_results if isinstance(row, Mapping)]
        post = apply_menxia_item_results(context.review, results, max_rounds=context.review.max_item_revision_rounds)
        census = menxia_stage_census(post)
        if any((census.get(stage) for stage in MENXIA_ACTIVE_STAGES)):
            return None
        if not menxia_has_blockers(post):
            return None
        decision = self._transition_decision(context, event, action='OPEN_HUMAN_GATE')
        human_gate = decision.update.human_gate
        progression = decision.update.progression
        if human_gate is not None:
            human_gate = replace(human_gate, resume_state='MENXIA_GROUP_GATE', reason_code='MENXIA_GROUP_BLOCKED')
        if progression is not None:
            progression = replace(progression, resume_state='MENXIA_GROUP_GATE')
        return replace(decision, transition=TransitionRequest(action='OPEN_HUMAN_GATE', reason_code='MENXIA_GROUP_BLOCKED'), update=replace(decision.update, human_gate=human_gate, progression=progression, recovery=RecoveryUpdate(blocked_reason='MENXIA_GROUP_BLOCKED')))

    def _menxia_group_wave_decision(self, context: WorkflowContext, event: DomainEvent) -> StateDecision | None:
        """Stage-barrier override for a menxia group wave completion.

        Mirrors the item wave decision: only the drain-out tail — no group
        left in any active stage and at least one parked (ESCALATED/BLOCKED)
        — routes to a human with MENXIA_GROUP_GATE as the resume point.
        Everything else is routed by the joiner's aggregate action.
        """
        if event.name != 'NODE_COMPLETED' or context.review is None:
            return None
        if context.parallel is None or not context.parallel.menxia.enabled:
            return None
        payload = event.payload if isinstance(event.payload, Mapping) else {}
        raw_results = payload.get('menxia_group_results')
        if not isinstance(raw_results, list) or not raw_results:
            return None
        results = [dict(row) for row in raw_results if isinstance(row, Mapping)]
        post = apply_menxia_group_results(context.review, results, max_rounds=context.review.max_menxia_group_rounds)
        census = menxia_group_stage_census(post)
        if any((census.get(stage) for stage in MENXIA_GROUP_ACTIVE_STAGES)):
            return None
        if not menxia_groups_have_blockers(post):
            return None
        decision = self._transition_decision(context, event, action='OPEN_HUMAN_GATE')
        human_gate = decision.update.human_gate
        progression = decision.update.progression
        if human_gate is not None:
            human_gate = replace(human_gate, resume_state='MENXIA_GROUP_GATE', reason_code='MENXIA_GROUP_BLOCKED')
        if progression is not None:
            progression = replace(progression, resume_state='MENXIA_GROUP_GATE')
        return replace(decision, transition=TransitionRequest(action='OPEN_HUMAN_GATE', reason_code='MENXIA_GROUP_BLOCKED'), update=replace(decision.update, human_gate=human_gate, progression=progression, recovery=RecoveryUpdate(blocked_reason='MENXIA_GROUP_BLOCKED')))

    def _require_current_state(self, context: WorkflowContext) -> None:
        current_state = context.progression.state
        if current_state != self.name:
            raise InvariantViolation(f'state object {self.name!r} cannot operate on context in {current_state!r}')

    @staticmethod
    def _require_event_task(context: WorkflowContext, event: DomainEvent) -> None:
        if event.task_id != context.identity.task_id:
            raise InvariantViolation(f'event task {event.task_id!r} does not match context task {context.identity.task_id!r}')

    def _require_recorded_resume_state(self, context: WorkflowContext) -> None:
        resume_state = context.progression.resume_state
        if not isinstance(resume_state, str) or not resume_state:
            raise InvariantViolation(f'state {self.name!r} requires a recorded resume_state')

class MenxiaItemSolverState(_ConcreteWorkflowState):
    name = 'MENXIA_ITEM_SOLVER'
    supported_actions = ('FEASIBLE', 'READY_FOR_ANALYST', 'READY_FOR_CRITIC', 'HUMAN_GATE', 'BLOCKED', 'RETRY', 'BLOCK', 'OPEN_HUMAN_GATE')

    def handle(self, context: WorkflowContext, event: DomainEvent) -> StateDecision:
        self._require_current_state(context)
        self._require_event_task(context, event)
        decision = self._menxia_wave_decision(context, event)
        if decision is not None:
            return decision
        return super().handle(context, event)

class MenxiaItemAnalystState(_ConcreteWorkflowState):
    name = 'MENXIA_ITEM_ANALYST'
    supported_actions = ('EVIDENCE_SUFFICIENT', 'READY_FOR_CRITIC', 'NEEDS_MORE_EVIDENCE', 'REQUEST_SOLVER_REVISION', 'HUMAN_GATE', 'BLOCKED', 'RETRY', 'BLOCK', 'OPEN_HUMAN_GATE')

    def handle(self, context: WorkflowContext, event: DomainEvent) -> StateDecision:
        self._require_current_state(context)
        self._require_event_task(context, event)
        decision = self._menxia_wave_decision(context, event)
        if decision is not None:
            return decision
        return super().handle(context, event)

class MenxiaItemCriticState(_ConcreteWorkflowState):
    name = 'MENXIA_ITEM_CRITIC'
    supported_actions = ('APPROVE_ITEM', 'REVISE_ITEM', 'SPLIT_ITEM', 'MERGE_ITEM', 'REMOVE_ITEM', 'REQUEST_SOLVER_REVISION', 'HUMAN_GATE', 'BLOCKED', 'RETRY', 'BLOCK', 'OPEN_HUMAN_GATE')

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
    name = 'MENXIA_GROUP_SOLVER'
    supported_actions = ('FEASIBLE', 'READY_FOR_ANALYST', 'READY_FOR_CRITIC', 'HUMAN_GATE', 'BLOCKED', 'RETRY', 'BLOCK', 'OPEN_HUMAN_GATE')

    def handle(self, context: WorkflowContext, event: DomainEvent) -> StateDecision:
        self._require_current_state(context)
        self._require_event_task(context, event)
        decision = self._menxia_group_wave_decision(context, event)
        if decision is not None:
            return decision
        return super().handle(context, event)

class MenxiaGroupAnalystState(_ConcreteWorkflowState):
    name = 'MENXIA_GROUP_ANALYST'
    supported_actions = ('EVIDENCE_SUFFICIENT', 'READY_FOR_CRITIC', 'NEEDS_MORE_EVIDENCE', 'REQUEST_SOLVER_REVISION', 'HUMAN_GATE', 'BLOCKED', 'RETRY', 'BLOCK', 'OPEN_HUMAN_GATE')

    def handle(self, context: WorkflowContext, event: DomainEvent) -> StateDecision:
        self._require_current_state(context)
        self._require_event_task(context, event)
        decision = self._menxia_group_wave_decision(context, event)
        if decision is not None:
            return decision
        return super().handle(context, event)

class MenxiaGroupCriticState(_ConcreteWorkflowState):
    name = 'MENXIA_GROUP_CRITIC'
    supported_actions = ('APPROVE_GROUP', 'REQUEST_SOLVER_REVISION', 'REQUEST_EVIDENCE', 'HUMAN_GATE', 'BLOCKED', 'RETRY', 'BLOCK', 'OPEN_HUMAN_GATE')

    def handle(self, context: WorkflowContext, event: DomainEvent) -> StateDecision:
        self._require_current_state(context)
        self._require_event_task(context, event)
        decision = self._menxia_group_wave_decision(context, event)
        if decision is not None:
            return decision
        return super().handle(context, event)

class MenxiaGroupGateState(_ConcreteWorkflowState):
    name = 'MENXIA_GROUP_GATE'
    supported_actions = ('COMPLETE', 'APPROVE_GROUP', 'APPROVE_FREEZE', 'NEXT_ITEM', 'REQUEST_GROUP_REVISION', 'REQUEST_NEXT_GROUP', 'HUMAN_GATE', 'BLOCKED', 'RETRY', 'BLOCK', 'OPEN_HUMAN_GATE')
    group_blocked_reason = 'MENXIA_GROUP_BLOCKED'

    def handle(self, context: WorkflowContext, event: DomainEvent) -> StateDecision:
        self._require_current_state(context)
        self._require_event_task(context, event)
        action = self._event_action(event)
        review = context.review
        menxia_enabled = review is not None and context.parallel is not None and context.parallel.menxia.enabled
        group_rows = tuple(review.menxia_groups or review.seed_menxia_groups()) if review is not None and menxia_enabled and (not review.menxia_items) else ()
        if action in {'COMPLETE', 'APPROVE_GROUP'} and review is not None:
            if group_rows:
                approved_groups = {row.group_id for row in group_rows if row.stage == 'APPROVED'}
                parked_groups = {row.group_id for row in group_rows if row.stage in ('BLOCKED', 'ESCALATED')}
                completed = set(review.completed_item_ids) | {item.item_id for item in review.task_items if item.group_id in approved_groups}
                pending = [item for item in review.task_items if item.item_id not in completed]
                if pending:
                    if all((item.group_id in parked_groups for item in pending)):
                        return self._human_gate_decision(context, event, self.group_blocked_reason)
                    action = 'NEXT_ITEM'
                decision = self._transition_decision(context, event, action=action)
                if completed != set(review.completed_item_ids):
                    base = decision.update.review if decision.update.review is not None else ReviewUpdate(revision_id=review.revision_id)
                    decision = replace(decision, update=replace(decision.update, review=replace(base, completed_item_ids=tuple(sorted(completed)))))
                return decision
            parked = {row.item_id for row in review.menxia_items if row.stage in ('BLOCKED', 'ESCALATED')} if menxia_enabled else set()
            completed = set(review.completed_item_ids)
            pending = [item for item in review.task_items if item.item_id not in completed and item.item_id not in parked]
            if pending:
                if review.next_menxia_item() is None:
                    raise InvariantViolation('Menxia task graph has no dependency-ready next item')
                action = 'NEXT_ITEM'
        if action == 'REQUEST_GROUP_REVISION' and menxia_enabled:
            payload = event.payload if isinstance(event.payload, Mapping) else {}
            if group_rows:
                named = _menxia_gate_revision_group_ids(payload, review)
                if not named:
                    return self._human_gate_decision(context, event, self.group_blocked_reason)
                event = replace(event, payload={**payload, 'menxia_group_results': [{'group_id': group_id, 'action': 'REQUEST_SOLVER_REVISION', 'budget_reset': True} for group_id in named]})
                action = 'REQUEST_GROUP_REVISION'
            else:
                named = _menxia_gate_revision_item_ids(payload)
                if not named:
                    return self._human_gate_decision(context, event, self.group_blocked_reason)
                event = replace(event, payload={**payload, 'menxia_item_results': [{'item_id': item_id, 'action': 'REQUEST_SOLVER_REVISION', 'budget_reset': True} for item_id in named]})
                action = 'NEXT_ITEM'
        if action not in self.supported_actions and action not in {'FAIL', 'CANCEL'}:
            raise InvariantViolation(f'event {event.name!r} is not supported by state {self.name!r}')
        return self._transition_decision(context, event, action=action)

class DoneState(_ConcreteWorkflowState):
    name = 'DONE'

class HumanGateWorkflowState(_ConcreteWorkflowState):
    name = 'HUMAN_GATE'
    supported_actions = ('RESUME', 'CANCEL', 'PLAN_REVIEW_REJECTED')
    requires_resume_state = True

    def handle(self, context: WorkflowContext, event: DomainEvent) -> StateDecision:
        gate = context.human_gate
        return super().handle(context, event)

    def on_resume(self, context: WorkflowContext) -> StateDecision:
        self._require_current_state(context)
        self._require_recorded_resume_state(context)
        return StateDecision(transition=TransitionRequest(action='RESUME', reason_code='HUMAN_DECISION_RECEIVED'), update=ContextUpdate(human_gate=HumanGateUpdate(clear_gate=True)))

class RetryWaitState(_ConcreteWorkflowState):
    name = 'RETRY_WAIT'
    supported_actions = ('RESUME', 'CANCEL')
    requires_resume_state = True

    def on_resume(self, context: WorkflowContext) -> StateDecision:
        self._require_current_state(context)
        self._require_recorded_resume_state(context)
        return StateDecision(transition=TransitionRequest(action='RESUME', reason_code='RETRY_READY'))

class BlockedState(_ConcreteWorkflowState):
    name = 'BLOCKED'
    supported_actions = ('RESUME', 'CANCEL')
    requires_resume_state = True

    def on_resume(self, context: WorkflowContext) -> StateDecision:
        self._require_current_state(context)
        self._require_recorded_resume_state(context)
        return StateDecision(transition=TransitionRequest(action='RESUME', reason_code='UNBLOCKED'))

class CancelledState(_ConcreteWorkflowState):
    name = 'CANCELLED'

class FailedState(_ConcreteWorkflowState):
    name = 'FAILED'

class PersistenceDegradedState(_ConcreteWorkflowState):
    name = 'PERSISTENCE_DEGRADED'
    supported_actions = ('RESUME', 'CANCEL')
    requires_resume_state = True

    def on_resume(self, context: WorkflowContext) -> StateDecision:
        self._require_current_state(context)
        self._require_recorded_resume_state(context)
        return StateDecision(transition=TransitionRequest(action='RESUME', reason_code='PERSISTENCE_RECOVERED'))
_MAX_ITEM_EVIDENCE_RECORDS = MAX_EVIDENCE_RECORDS

def _menxia_evidence_packet_update(context: WorkflowContext, payload: Mapping[str, object]) -> dict[str, object] | None:
    """Evidence packet for this fold (``None`` keeps the current packet).

    Zhongshu folds carry the packet directly; menxia item waves carry
    ``menxia_evidence`` contributions that must be merged into the existing
    packet so the Analyst's fresh evidence reaches the item Critic without
    discarding the zhongshu records.
    """
    direct = payload.get('evidence_packet')
    if isinstance(direct, Mapping):
        from ..plan_utils import merge_evidence_packet
        return merge_evidence_packet(context.review.evidence_packet if context.review else None, dict(direct))
    raw = payload.get('menxia_evidence')
    if not isinstance(raw, list) or not raw:
        return None
    return merge_menxia_evidence(context.review.evidence_packet if context.review else None, [row for row in raw if isinstance(row, Mapping)])

def _item_evidence_context(review: object, item: ReviewTaskItem | None, expected_lenses: int) -> dict[str, object]:
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
    packet = getattr(review, 'evidence_packet', None)
    if not isinstance(packet, Mapping) or item is None:
        return {'records': [], 'lens_workers': [], 'lens_gaps': 0, 'finding_responses': []}
    records: list[dict[str, object]] = []
    raw_updates = packet.get('evidence_updates')
    if not isinstance(raw_updates, list):
        raw_updates = []
    demanded_blob = ' '.join((' '.join(part.split()) for finding in getattr(review, 'findings', ()) if str(getattr(finding, 'item_id', '')) == item.item_id and bool(getattr(finding, 'active', True)) for part in (str(getattr(finding, 'claim', '') or ''), str(getattr(finding, 'required_action', '') or ''), *(str(eid) for eid in getattr(finding, 'supporting_evidence', ()) or ()))))
    active_ids = {finding.finding_id for finding in getattr(review, 'findings', ()) if finding.active and finding.item_id == item.item_id}
    answered_evidence_ids = {str(eid) for response in packet.get('finding_responses') or [] if isinstance(response, Mapping) and response.get('finding_id') in active_ids for eid in response.get('evidence_ids') or []}
    prioritized: list[tuple[int, dict[str, object]]] = []
    for value in raw_updates:
        if not isinstance(value, Mapping):
            continue
        record_item = str(value.get('item_id') or '').strip()
        requirement = str(value.get('requirement_id') or '').strip()
        if record_item and record_item != item.item_id:
            continue
        if not record_item and requirement not in item.source_requirement_ids:
            continue
        record = project_evidence_record(value)
        if not record:
            continue
        evidence_id = str(record.get('evidence_id') or '')
        if evidence_id in answered_evidence_ids:
            tier = -1
        elif evidence_id and evidence_id in demanded_blob:
            tier = 0
        elif record_item:
            tier = 1
        else:
            tier = 2
        prioritized.append((tier, record))
    prioritized.sort(key=lambda pair: pair[0])
    records = [record for _, record in prioritized[:_MAX_ITEM_EVIDENCE_RECORDS]]
    worker_evidence = packet.get('worker_evidence')
    lens_workers = sorted((str(key).strip() for key in worker_evidence if str(key).strip())) if isinstance(worker_evidence, Mapping) else []
    lens_gaps = max(0, expected_lenses - len(lens_workers)) if expected_lenses else 0
    item_finding_ids = {str(finding.finding_id) for finding in getattr(review, 'findings', ()) if str(getattr(finding, 'item_id', '')) == item.item_id}
    responses: list[dict[str, object]] = []
    raw_responses = packet.get('finding_responses')
    if isinstance(raw_responses, list) and item_finding_ids:
        for value in raw_responses:
            if not isinstance(value, Mapping):
                continue
            if str(value.get('finding_id') or '') not in item_finding_ids:
                continue
            response: dict[str, object] = {}
            for key in ('finding_id', 'answer', 'evidence_ids', 'suggested_disposition', 'note'):
                raw = value.get(key)
                if raw in (None, '', [], {}):
                    continue
                response[key] = raw
            if 'finding_id' in response:
                responses.append(response)
    return {'records': records, 'lens_workers': lens_workers, 'lens_gaps': lens_gaps, 'finding_responses': responses}

def _repair_menxia_stage_gap(review: ReviewState, target: str, rows: tuple[MenxiaItemState, ...]) -> tuple[MenxiaItemState, ...]:
    """Resume repair for snapshots seeded before the pipeline records existed.

    A legacy snapshot restored mid-item has no menxia rows, so the stage wave
    for the resumed state would dispatch nothing.  Seed the active item into
    the resumed state's stage so the run continues exactly where it paused.
    """
    if rows or not review.active_item_id:
        return rows
    item = next((entry for entry in review.task_items if entry.item_id == review.active_item_id), None)
    if item is None:
        return rows
    return (MenxiaItemState(item_id=item.item_id, group_id=item.group_id, stage=MENXIA_STAGE_BY_TARGET[target]),)

def _menxia_item_capsule(target: str, item: ReviewTaskItem, row: MenxiaItemState) -> str:
    """Instruction capsule for one item-scoped menxia worker."""
    lines = [f'Menxia item pipeline job: {item.item_id} in group {item.group_id} (stage {row.stage}, revision round {row.revision_round}).', f'Title: {item.title}', f'Objective: {item.objective}']
    if item.dependencies:
        lines.append(f"Dependencies: {', '.join(item.dependencies)}")
    if target == 'MENXIA_ITEM_SOLVER':
        lines.append('Produce the implementation proposal for this item only; answer every item finding in responses_to_critic.')
    elif target == 'MENXIA_ITEM_ANALYST':
        lines.append('Run the evidence review for this item only; do not modify the plan.')
    else:
        lines.append('Review this item only; every required change must be observable through the verification plan.')
        lines.append("The item objective is frozen at plan-freeze time; runtime counters quoted inside it (state versions, hashes, component counts) are historical anchors, not current-state claims. Judge evidence against the latest verified snapshot and the current proposal, not the frozen text's volatile counters.")
    return '\n'.join(lines)

def _menxia_item_dispatch_context(context: WorkflowContext, target: str, *, item_id: str, group_id: str, revision_id: str, plan_hash: str, row: MenxiaItemState | None=None, item_override: ReviewTaskItem | None=None, expected_lenses: int=0) -> dict[str, object] | None:
    """Per-item dispatch context shared by wave bindings and serial dispatch."""
    review = context.review
    if review is None or not item_id:
        return None
    item = item_override
    if item is None:
        item = next((entry for entry in review.task_items if entry.item_id == item_id), None)
    if item is None:
        return None
    group_id = group_id or item.group_id
    if row is None:
        row = next((entry for entry in review.menxia_items if entry.item_id == item_id), None)
    if row is None:
        row = {state.item_id: state for state in review.seed_menxia_items()}.get(item_id)
    item_findings = findings_for_scope(review.findings, group_id=group_id, item_id=item_id)
    evidence = _item_evidence_context(review, item, expected_lenses)
    contract_id = _MENXIA_CONTRACT_BY_TARGET[target]
    editable = 'none' if target == 'MENXIA_ITEM_ANALYST' else f'items:{item_id}'
    solver_proposal = row.last_solver_proposal if row is not None else None
    envelope_ingredients = [{'key': 'review.plan', 'source': 'context.json', 'lifetime': 'persisted', 'slice': f'item:{item_id}'}, {'key': 'review.findings', 'source': 'context.json', 'lifetime': 'persisted', 'slice': f'item:{item_id}+active'}, {'key': 'review.item_evidence', 'source': 'context.json', 'lifetime': 'persisted', 'slice': f'item:{item_id}'}]
    context_payload: dict[str, object] = {'menxia_dispatch_mode': 'item_pipeline', 'stage': MENXIA_STAGE_BY_TARGET[target], 'revision_id': revision_id, 'plan_hash': plan_hash, 'group_id': group_id, 'item_id': item_id, 'revision_round': row.revision_round if row is not None else 0, 'item': asdict(item), 'item_findings': [_finding_payload(finding) for finding in item_findings], 'item_evidence': evidence['records'], 'finding_responses': evidence['finding_responses']}
    if target == 'MENXIA_ITEM_CRITIC':
        if isinstance(solver_proposal, Mapping):
            context_payload['item_solver_proposal'] = dict(solver_proposal)
            envelope_ingredients.append({'key': 'review.item_solver_proposal', 'source': 'context.json', 'lifetime': 'persisted', 'slice': f'item:{item_id}'})
    context_payload['envelope'] = build_envelope(ingredients=envelope_ingredients, tools={'editable': editable, 'contract': contract_id}, product={'type': _MENXIA_PRODUCT_BY_TARGET[target], 'contract': contract_id})
    return context_payload

def _menxia_item_bindings(context: WorkflowContext, target: str, *, request_id: str, revision_id: str, plan_hash: str) -> list[dict[str, object]]:
    """One wave binding per item currently sitting in the state's stage."""
    review = context.review
    if review is None:
        return []
    limits = context.parallel.menxia if context.parallel is not None else None
    rows = _repair_menxia_stage_gap(review, target, ready_stage_items(review, target=target, limits=limits))
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
        bindings.append({'worker_id': f'menxia_{str(row.stage).lower()}-worker-{index:02d}', 'agent_id': f"{phase.lower()}-{role.split('-')[-1]}-{index:02d}", 'task_id': context.identity.task_id, 'request_id': f'{request_id}:worker-{index:02d}', 'role': role, 'phase': phase, 'group_id': row.group_id, 'item_id': row.item_id, 'prompt_ref': _menxia_item_capsule(target, item, row), 'dispatch_context': _menxia_item_dispatch_context(context, target, item_id=row.item_id, group_id=row.group_id, revision_id=revision_id, plan_hash=plan_hash, row=row, item_override=item, expected_lenses=expected_lenses)})
    return bindings

def _menxia_group_baseline(review: ReviewState, group_id: str) -> str:
    """Requirement baseline a group's v1 document starts from."""
    lines: list[str] = []
    for item in sorted((entry for entry in review.task_items if entry.group_id == group_id), key=lambda entry: (entry.order, entry.item_id)):
        lines.append(f'### {item.item_id} {item.title}'.strip())
        if item.objective:
            lines.append(item.objective)
        lines.append('')
    return '\n'.join(lines).strip()

def _menxia_group_capsule(target: str, group_id: str, row: MenxiaGroupState, member_count: int) -> str:
    """Instruction capsule for one group-scoped menxia worker."""
    lines = [f'Menxia group pipeline job: group {group_id} (stage {row.stage}, revision round {row.revision_round}, {member_count} items share one plan document).']
    if target == 'MENXIA_GROUP_SOLVER':
        lines.append(body_protocol_prompt())
        if row.reply_rejection:
            lines.append('[Group reply repair] ' + row.reply_rejection + ' Reuse rejected_doc_markdown as repair material; the accepted doc_markdown is still authoritative. Fix this defect without repeating the investigation.')
        if row.doc_version == 0 and (not row.doc_markdown.strip()):
            lines.append('First round: the document you receive is the v1 skeleton. Fill in the plan body and keep the title version at [v1]; only later revision rounds advance the version and declare changed sections in touched_scope.')
        else:
            lines.append('Produce the next version of the group document: update the plan body, absorb or decline suggestions by id, and declare every body section you changed in touched_scope.')
    elif target == 'MENXIA_GROUP_ANALYST':
        lines.append('Review the group document against the requirement and append suggestions to the suggestion section only.')
    else:
        lines.append("Attack the group document's body and append suggestions to the suggestion section only; route evidence gaps to REQUEST_EVIDENCE.")
    return '\n'.join(lines)

def _menxia_group_dispatch_context(context: WorkflowContext, target: str, *, group_id: str, revision_id: str, plan_hash: str, row: MenxiaGroupState | None=None) -> dict[str, object] | None:
    """Per-group dispatch context (three-segment bundle, shared doc model)."""
    review = context.review
    if review is None or not group_id:
        return None
    if row is None:
        row = next((entry for entry in review.menxia_groups if entry.group_id == group_id), None)
    if row is None:
        row = {state.group_id: state for state in review.seed_menxia_groups()}.get(group_id)
    if row is None:
        return None
    members = [item for item in review.task_items if item.group_id == group_id]
    group_findings = findings_for_scope(review.findings, group_id=group_id)
    doc_markdown = row.doc_markdown
    if not doc_markdown.strip() and target == 'MENXIA_GROUP_SOLVER':
        doc_markdown = render_initial(group_id=group_id, baseline_markdown=_menxia_group_baseline(review, group_id))
    contract_id = _MENXIA_CONTRACT_BY_TARGET[target]
    editable = 'none' if target != 'MENXIA_GROUP_SOLVER' else f'groups:{group_id}'
    return {'menxia_dispatch_mode': 'group_pipeline', 'stage': MENXIA_GROUP_STAGE_BY_TARGET[target], 'revision_id': revision_id, 'plan_hash': plan_hash, 'group_id': group_id, 'revision_round': row.revision_round, 'doc_version': row.doc_version, 'doc_hash': row.doc_hash, 'doc_markdown': doc_markdown, 'body_part_titles': list(BODY_PART_TITLES), 'initial_document_fill': row.doc_version == 0 and (not row.doc_markdown.strip()), 'expected_doc_version': (row.doc_version + 1 if row.doc_version else 1) if target == 'MENXIA_GROUP_SOLVER' else row.doc_version, 'reply_retry_count': row.reply_retry_count, 'max_reply_retries': context.recovery.max_reply_retries, 'reply_rejection': row.reply_rejection, 'rejected_doc_markdown': row.rejected_doc_markdown, 'requirement_markdown': row.requirement_markdown, 'requirements_version': 0, 'requirements_hash': '', 'items': [asdict(item) for item in members], 'group_findings': [_finding_payload(finding) for finding in group_findings], 'envelope': build_envelope(ingredients=[{'key': 'review.plan', 'source': 'context.json', 'lifetime': 'persisted', 'slice': f'group:{group_id}'}, {'key': 'review.findings', 'source': 'context.json', 'lifetime': 'persisted', 'slice': f'group:{group_id}+active'}, {'key': 'review.group_document', 'source': 'context.json', 'lifetime': 'persisted'}], tools={'editable': editable, 'contract': contract_id}, product={'type': _MENXIA_PRODUCT_BY_TARGET[target], 'contract': contract_id})}

def _menxia_group_bindings(context: WorkflowContext, target: str, *, request_id: str, revision_id: str, plan_hash: str) -> list[dict[str, object]]:
    """One wave binding per group currently sitting in the state's stage."""
    review = context.review
    if review is None:
        return []
    limits = context.parallel.menxia if context.parallel is not None else None
    rows = ready_stage_groups(review, target=target, limits=limits)
    if not rows:
        return []
    role, phase = _ROLE_BY_STATE[target]
    bindings: list[dict[str, object]] = []
    for index, row in enumerate(rows, start=1):
        member_count = sum((1 for item in review.task_items if item.group_id == row.group_id))
        dispatch_context = _menxia_group_dispatch_context(context, target, group_id=row.group_id, revision_id=revision_id, plan_hash=plan_hash, row=row)
        if dispatch_context is None:
            continue
        bindings.append({'worker_id': f'menxia_group_{str(row.stage).lower()}-worker-{index:02d}', 'agent_id': f"{phase.lower()}-{role.split('-')[-1]}-{index:02d}", 'task_id': context.identity.task_id, 'request_id': f'{request_id}:worker-{index:02d}', 'role': role, 'phase': phase, 'group_id': row.group_id, 'item_id': None, 'prompt_ref': _menxia_group_capsule(target, row.group_id, row, member_count), 'dispatch_context': dispatch_context})
    return bindings

def _menxia_group_gate_dispatch(context: WorkflowContext, *, request_id: str, prompt: object, revision_id: str, plan_hash: str) -> EffectRequest | None:
    """Single gate-agent dispatch, run only when every group is terminal."""
    review = context.review
    if review is None:
        return None
    group_rows = () if review.menxia_items else review.menxia_groups or review.seed_menxia_groups()
    if group_rows:
        reports = menxia_group_pipeline_readiness(review, group_rows)
        stage_by_group = {row.group_id: row.stage for row in group_rows}
        round_by_group = {row.group_id: row.revision_round for row in group_rows}
        verdict_by_group = {row.group_id: row.last_verdict for row in group_rows}
        reason_by_group = {row.group_id: row.blocked_reason for row in group_rows}
        terminal_by_group = {group_id: stage in ('APPROVED', 'BLOCKED', 'ESCALATED') for group_id, stage in stage_by_group.items()}
    else:
        rows = review.menxia_items or review.seed_menxia_items()
        reports = menxia_group_readiness(review, rows)
        stage_by_group = {}
        round_by_group = {}
        verdict_by_group = {}
        reason_by_group = {}
        terminal_by_group = {}
    if not reports or not all((bool(report['ready']) for report in reports)):
        return None
    role, phase = _ROLE_BY_STATE['MENXIA_GROUP_GATE']
    contract_id = _MENXIA_CONTRACT_BY_TARGET['MENXIA_GROUP_GATE']
    row_by_item = {row.item_id: row for row in review.menxia_items or ()}
    item_reviews = [{'item_id': item.item_id, 'group_id': item.group_id, 'stage': row_by_item[item.item_id].stage if item.item_id in row_by_item else stage_by_group.get(item.group_id, 'SOLVING'), 'revision_round': row_by_item[item.item_id].revision_round if item.item_id in row_by_item else round_by_group.get(item.group_id, 0), 'last_verdict': row_by_item[item.item_id].last_verdict if item.item_id in row_by_item else verdict_by_group.get(item.group_id, ''), 'blocked_reason': row_by_item[item.item_id].blocked_reason if item.item_id in row_by_item else reason_by_group.get(item.group_id, ''), 'terminal': row_by_item[item.item_id].stage in MENXIA_TERMINAL_STAGES if item.item_id in row_by_item else terminal_by_group.get(item.group_id, False), 'title': item.title, 'objective': item.objective} for item in sorted(review.task_items, key=lambda entry: (entry.order, entry.item_id))]
    group_context = [{'group_id': group.group_id, 'order': group.order, 'item_ids': list(group.item_ids)} for group in sorted(review.task_groups, key=lambda entry: (entry.order, entry.group_id))]
    return EffectRequest(effect_id=f'dispatch:{request_id}', effect_type='agent_dispatch', task_id=context.identity.task_id, idempotency_key=request_id, payload_ref=context.request.payload_ref, payload={'issue_id': context.identity.issue_id, 'request_id': request_id, 'agent_id': f"{phase.lower()}-{role.split('-')[-1]}", 'role': role, 'phase': phase, 'target_state': 'MENXIA_GROUP_GATE', 'prompt_ref': prompt.content, 'request_payload_ref': context.request.payload_ref or '', 'references': dict(prompt.references), 'revision_id': revision_id, 'plan_hash': plan_hash, 'sequence': context.progression.sequence, 'dispatch_context': {'menxia_dispatch_mode': 'group_gate', 'revision_id': revision_id, 'plan_hash': plan_hash, 'groups': [dict(report) for report in reports], 'group_context': group_context, 'item_reviews': item_reviews, 'envelope': build_envelope(ingredients=[{'key': 'review.findings', 'source': 'context.json', 'lifetime': 'persisted'}, {'key': 'review.task_items', 'source': 'context.json', 'lifetime': 'persisted'}, {'key': 'review.item_reviews', 'source': 'context.json', 'lifetime': 'persisted'}, {'key': 'review.group_context', 'source': 'context.json', 'lifetime': 'persisted'}], tools={'editable': 'none', 'contract': contract_id}, product={'type': _MENXIA_PRODUCT_BY_TARGET['MENXIA_GROUP_GATE'], 'contract': contract_id})}, 'group_id': None, 'item_id': None})

def _menxia_gate_revision_item_ids(payload: Mapping[str, object]) -> list[str]:
    """Extract the items a group gate verdict sends back to its Solver."""
    named: list[str] = []
    for source in (payload.get('revision_item_ids'), payload.get('group_consistency', {}).get('revision_item_ids') if isinstance(payload.get('group_consistency'), Mapping) else None):
        if isinstance(source, str):
            source = [source]
        if isinstance(source, list):
            named.extend((str(value).strip() for value in source if str(value).strip()))
    changes = payload.get('required_changes')
    if isinstance(changes, list):
        for change in changes:
            if isinstance(change, Mapping):
                for key in ('item_id', 'target'):
                    item_id = str(change.get(key) or '').strip()
                    if item_id.startswith('item-'):
                        named.append(item_id)
    consistency = payload.get('group_consistency')
    if isinstance(consistency, Mapping):
        decisions = consistency.get('item_decisions')
        if isinstance(decisions, list):
            for decision in decisions:
                if not isinstance(decision, Mapping):
                    continue
                if str(decision.get('action') or '') != 'REVISE_ITEM':
                    continue
                item_id = str(decision.get('item_id') or '').strip()
                if item_id.startswith('item-'):
                    named.append(item_id)
    ordered: list[str] = []
    seen: set[str] = set()
    for item_id in named:
        if item_id not in seen:
            seen.add(item_id)
            ordered.append(item_id)
    return ordered

def _menxia_gate_revision_group_ids(payload: Mapping[str, object], review: ReviewState) -> list[str]:
    """Extract the groups a group-gate verdict sends back to its Solver.

    The verdict may name groups directly (``group_ids`` / group-level
    ``required_changes`` targets) or items; item-level names map back to
    their owning group so a mixed verdict still restarts whole groups.
    """
    named: list[str] = []
    for source in (payload.get('group_ids'), payload.get('groups')):
        if isinstance(source, str):
            source = [source]
        if isinstance(source, list):
            named.extend((str(value).strip() for value in source if str(value).strip()))
    for change in payload.get('required_changes') or []:
        if not isinstance(change, (Mapping, str)):
            continue
        candidates = (change.get('target'), change.get('group_id')) if isinstance(change, Mapping) else (change,)
        for value in candidates:
            token = str(value or '').strip()
            if token.startswith('group-'):
                named.append(token)
    group_prefix_ids = [group_id for group_id in named if group_id.startswith('group-')]
    item_ids = _menxia_gate_revision_item_ids(payload)
    group_by_item = {item.item_id: item.group_id for item in review.task_items}
    ordered: list[str] = []
    seen: set[str] = set()
    for group_id in group_prefix_ids + [group_by_item.get(item_id, '') for item_id in item_ids]:
        if group_id and group_id not in seen:
            seen.add(group_id)
            ordered.append(group_id)
    return ordered

def _expected_analyst_lenses(context: WorkflowContext) -> int:
    """Analyst lens workers an evidence wave over the open findings dispatches."""
    if context.parallel is None:
        return 0
    review = context.review
    from .policies.analyst_partition import needs_analyst_evidence
    return expected_analyst_lenses(context.parallel.zhongshu.analyst_default_workers, (finding for finding in (review.findings if review is not None else ()) if needs_analyst_evidence(finding)))

class StateRegistry:
    """Read-only lookup of one concrete object per declared state."""

    def __init__(self, states: Mapping[str, WorkflowState]) -> None:
        state_map = dict(states)
        if set(state_map) != set(ALL_STATES):
            missing = sorted(set(ALL_STATES) - set(state_map))
            extra = sorted(set(state_map) - set(ALL_STATES))
            raise InvariantViolation(f'state registry must cover ALL_STATES; missing={missing}, extra={extra}')
        for name, state in state_map.items():
            if state.name != name:
                raise InvariantViolation(f'state registry key {name!r} does not match object name {state.name!r}')
        self._states = MappingProxyType(state_map)

    @classmethod
    def default(cls) -> 'StateRegistry':
        """Create the complete business/system state object registry."""
        state_objects: tuple[WorkflowState, ...] = (MenxiaItemSolverState(), MenxiaItemAnalystState(), MenxiaItemCriticState(), MenxiaGroupSolverState(), MenxiaGroupAnalystState(), MenxiaGroupCriticState(), MenxiaGroupGateState(), DoneState(), HumanGateWorkflowState(), RetryWaitState(), BlockedState(), CancelledState(), FailedState(), PersistenceDegradedState())
        return cls({state.name: state for state in state_objects})

    def get(self, state_name: str) -> WorkflowState:
        try:
            return self._states[state_name]
        except KeyError as error:
            raise InvariantViolation(f'unknown workflow state {state_name!r}') from error

    def state_for(self, state_name: str) -> WorkflowState:
        """Named alias for callers that prefer domain terminology."""
        return self.get(state_name)

    def names(self) -> tuple[str, ...]:
        return tuple((state.name for state in self._states.values()))

    def __contains__(self, state_name: object) -> bool:
        return state_name in self._states

    def __len__(self) -> int:
        return len(self._states)
__all__ = ['BlockedState', 'CancelledState', 'DoneState', 'FailedState', 'HumanGateWorkflowState', 'MenxiaGroupGateState', 'MenxiaGroupAnalystState', 'MenxiaGroupCriticState', 'MenxiaGroupSolverState', 'MenxiaItemAnalystState', 'MenxiaItemCriticState', 'MenxiaItemSolverState', 'PersistenceDegradedState', 'RetryWaitState', 'StateRegistry', 'WorkflowState']
