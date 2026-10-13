"""Per-task composition and guarded continuation into the existing Menxia pipeline."""
from dataclasses import asdict, replace
from pathlib import Path
import time
from ..domain.decisions import ContextUpdate, StateDecision
from ..domain.context import ReviewUpdate, MenxiaGroupState, apply_review_update, ParallelUpdate, DeliveryUpdate
from ..domain.events import TransitionRequest
from ..domain.requirements.states import RequirementsWorkflowState, requirements_states
from ..domain.requirements.transitions import RequirementsTransitionRegistry, TERMINAL, WAIT, REQ_BUSINESS, MENXIA, SYSTEM
from ..domain.requirements.handoff import make_approved_input
from ..domain.requirements.questions import merge_questions, open_round
from ..domain.requirements.model import Outcome
from ..domain.states import StateRegistry, MenxiaGroupSolverState
from ..requirements_gateway import GatewayStore, GatewayBridge
from ..requirements_port import FeishuRequirementsPort, LocalRequirementsPort
from .requirements_event_coordinator import RequirementsEventCoordinator
from .requirements_effects import RequirementsEffects


def bind_continuation_effect(effect, context):
    if effect.effect_type not in ('agent_dispatch','node_dispatch'):
        return effect
    payload = dict(effect.payload)
    source = context.review.approved_requirements_input
    extra = {'workflow':'requirements-v1','approved_input_digest':source.input_digest,
             'approved_requirements_ref':source.document_ref,'approved_document_digest':source.document_digest,
             'approved_acceptance_markdown':source.acceptance_markdown}
    if effect.effect_type == 'node_dispatch':
        key = 'worker_bindings' if 'worker_bindings' in payload else 'bindings'
        payload[key] = [{**b,'dispatch_context':{**dict(b.get('dispatch_context') or {}),**extra}} for b in payload.get(key,[])]
    else:
        payload['dispatch_context'] = {**dict(payload.get('dispatch_context') or {}),**extra}
    payload.update(workflow='requirements-v1',approved_input_digest=source.input_digest)
    return replace(effect,payload=payload)


class RequirementsIntake(RequirementsWorkflowState):
    def _handle(self, context, state, event, payload):
        if event.name == 'IMPLEMENTATION_SCOPE_READY':
            approved = make_approved_input(context.identity.task_id,state)
            baseline = tuple({'requirement_id':c.requirement_id,'description':c.statement} for c in approved.claims)
            update = ReviewUpdate(revision_id='implementation:'+approved.document_digest,plan_schema='canonical-v1',
                plan=payload['plan'],plan_base_ref='',requirements=baseline,human_revision_validated=True,
                approved_requirements_input=approved)
            review = apply_review_update(None,update)
            groups = tuple(MenxiaGroupState(group_id=g.group_id,requirement_markdown=approved.markdown) for g in review.task_groups)
            update = replace(update,menxia_groups=groups)
            review = apply_review_update(None,update)
            projected = replace(context,review=review)
            effect = MenxiaGroupSolverState._dispatch_effect(projected,'MENXIA_GROUP_SOLVER',
                revision_id=review.revision_id,plan_hash=review.plan_hash,canonical_requirements=review.requirements,effective_review=review)
            effect = bind_continuation_effect(effect,projected)
            runtime_update = ContextUpdate(review=update,requirements=state,
                parallel=ParallelUpdate(active_node_run_id=effect.payload['node_run_id']) if effect.effect_type=='node_dispatch' else None,
                delivery=DeliveryUpdate(active_request_id=effect.payload['request_id'],idempotency_key=effect.idempotency_key,
                    status='PENDING',phase=effect.payload['phase'],role=effect.payload['role'],attempt=context.delivery.attempt+1))
            return StateDecision(TransitionRequest(event.name,event.name),runtime_update,(effect,))
        if event.name == 'REQUIREMENTS_CHANGE_REQUESTED':
            return requirements_change(self,context,state,payload)
        return super()._handle(context,state,event,payload)


def requirements_change(handler, context, state, payload):
    questions = payload.get('questions') or payload.get('questions_for_user')
    if not questions:
        raise ValueError('BUSINESS_CHANGE_REQUIRES_USER_QUESTION')
    state = merge_questions(state,questions)
    state = open_round(state,'REQ_DRAFT',context.request.decision_actor_ids)
    state = replace(state,pending_approval=None,approval=None,outcome=Outcome.WAITING_CLARIFICATION)
    return handler.goto(context,state,'REQUIREMENTS_CHANGE_REQUESTED')


class ContinuationState:
    def __init__(self, inner, transitions):
        self.inner, self.name, self.transitions = inner,inner.name,transitions
        inner._transitions = transitions

    def handle(self, context, event):
        req_handler = RequirementsWorkflowState(self.name,self.transitions)
        if event.name in ('CANCEL','STALE_RESULT'):
            return req_handler.handle(context,event)
        if event.name in ('ANSWERS_SUBMITTED','REQUIREMENTS_APPROVED'):
            return StateDecision(update=ContextUpdate(requirements=replace(context.requirements,
                reason='需求已交接；旧回答或批准不能修改当前实施输入，请提出明确需求变更。')))
        if event.name == 'REQUIREMENTS_SECTION_CONFIRMED':
            return StateDecision(update=ContextUpdate(requirements=replace(context.requirements,
                reason='需求已交接；章节确认不再适用。')))
        if event.name == 'REQUIREMENTS_FEEDBACK':
            from ..domain.requirements.consensus import add_feedback
            from ..domain.requirements.model import SourceRecord, next_id
            state = context.requirements
            source = SourceRecord(next_id(state.sources,'source_id','src'),'feedback',event.payload_mapping['text'],
                                  event.payload_mapping.get('actor_id','local-operator'),event.event_id,event.occurred_at)
            return StateDecision(update=ContextUpdate(requirements=replace(state,sources=state.sources+(source,),
                reason='已批准需求的补充意见已记录，等待明确需求变更。')))
        action = event.payload_mapping.get('action',event.name)
        if action == 'REQUIREMENTS_CHANGE_REQUESTED':
            return requirements_change(req_handler,context,context.requirements,dict(event.payload_mapping))
        decision = self.inner.handle(context,event)
        if decision.effects:
            review = apply_review_update(context.review,decision.update.review)
            projected = replace(context,review=review)
            decision = replace(decision,effects=tuple(bind_continuation_effect(e,projected) for e in decision.effects))
        return decision


class RequirementsRegistry:
    def __init__(self, transitions):
        self.states = requirements_states(transitions)
        self.states['MENXIA_REQUIREMENTS_INTAKE'] = RequirementsIntake('MENXIA_REQUIREMENTS_INTAKE',transitions)
        from .requirements_recovery import runtime_fingerprint
        fingerprint = runtime_fingerprint()
        for state in self.states.values():
            state.failure_fingerprint = fingerprint
        menxia = StateRegistry.default()
        for name in REQ_BUSINESS + MENXIA + SYSTEM + TERMINAL:
            if name not in self.states:
                self.states[name] = ContinuationState(menxia.get(name),transitions)
        from .requirements_notifications import NotifyingRequirementsState
        self.states = {name:NotifyingRequirementsState(state,transitions) for name,state in self.states.items()}

    def get(self, name):
        return self.states[name]


class RequirementsRuntime:
    terminal_states, wait_states = TERMINAL, WAIT

    def __init__(self, repository, root, context, port=None):
        self.repository, self.task_id = repository,context.identity.task_id
        self.transitions = RequirementsTransitionRegistry()
        self.states = RequirementsRegistry(self.transitions)
        self.store = getattr(port,'store',None) or GatewayStore(Path(root).parent/'requirements-gateway.sqlite')
        self.port = port or (LocalRequirementsPort(root,self.store) if context.request.communication == 'local'
                            else FeishuRequirementsPort(root,self.store,actor_ids=context.request.decision_actor_ids,
                                                       owner_policy=context.request.decision_owner_policy))
        self.bridge = GatewayBridge(self.store,repository,getattr(self.port,'verify_message',None))
        self.coordinator = RequirementsEventCoordinator(repository,self.bridge,self.port)
        self.effects = RequirementsEffects(root,self.port,sleep=time.sleep)

    def guard(self, effect):
        context = self.repository.load(self.task_id).context
        if isinstance(self.port,FeishuRequirementsPort):
            self.port.actor_ids = context.request.decision_actor_ids
        payload = effect.request.payload
        if effect.request.effect_type in ('requirements_progress','requirements_notice'):
            return True  # Committed notices remain valid after state changes/termination.
        if effect.request.effect_type == 'requirements_cleanup':
            return True
        if context.progression.state in TERMINAL:
            return False
        if payload.get('target_state') and payload['target_state'] != context.progression.state:
            return False
        if isinstance(payload.get('state'),dict):
            from ..domain.requirements.model import RequirementsState
            offered = RequirementsState.from_dict(payload['state'])
            if offered.consensus.digest != context.requirements.consensus.digest:
                return False
        if effect.request.effect_type == 'agent_dispatch':
            active = context.requirements.active_dispatch
            if str(payload.get('target_state','')).startswith('REQ_') or payload.get('target_state') == 'MENXIA_REQUIREMENTS_INTAKE':
                return bool(active and active.request_id == payload['request_id'])
            return payload.get('request_id') == context.delivery.active_request_id
        if effect.request.effect_type == 'node_dispatch':
            return payload.get('node_run_id') == context.parallel.active_node_run_id
        return True

    def submit(self, name, payload, event_id):
        self.store.local(self.task_id,name,payload,event_id)
