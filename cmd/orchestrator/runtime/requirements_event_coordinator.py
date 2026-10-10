"""One sequenced domain event at a time; durable results survive lost delivery."""
from datetime import datetime, timezone
from ..domain.events import DomainEvent


class RequirementsEventCoordinator:
    def __init__(self, repository, bridge=None, port=None):
        self.repository, self.bridge, self.port = repository, bridge, port

    def accept_result(self, result, effect):
        # commit_effect_result already fsynced the authoritative record.
        # flush_one rediscovers it after a crash, rather than trusting RAM.
        return None

    def flush_one(self, task_id):
        if self.repository.pending_domain_events(task_id):
            return
        if self.bridge and self.bridge.poll(task_id):
            return
        snapshot = self.repository.load(task_id)
        context = snapshot.context
        for result, effect in self.repository.list_effect_results(task_id):
            identity = 'requirements-effect:' + effect.effect_id
            if not result.event_name or self.repository.is_event_processed(identity):
                continue
            payload = dict(result.event_payload or {})
            if result.event_name == 'FAIL' and not payload.get('prerequisite_fingerprint'):
                from .requirements_recovery import runtime_fingerprint
                payload['prerequisite_fingerprint'] = runtime_fingerprint()
                payload['recovery_source_ref'] = 'runtime-code'
                if (payload.get('error_code') == 'PREREQUISITE_MISSING'
                    and hasattr(self.port,'prerequisite_fingerprint')):
                    payload['prerequisite_fingerprint'] = self.port.prerequisite_fingerprint()
                    payload['recovery_source_ref'] = 'communication'
            if effect.request.effect_type.startswith('requirements_'):
                payload['publication_logical_key'] = effect.request.idempotency_key
            if result.failure:
                from dataclasses import asdict
                payload['failure'] = asdict(result.failure)
            stale = False
            target = effect.request.payload.get('target_state')
            req = context.requirements
            if target and target != context.progression.state:
                stale = True
            if effect.request.effect_type == 'agent_dispatch' and (str(target).startswith('REQ_') or target == 'MENXIA_REQUIREMENTS_INTAKE'):
                dispatched = effect.request.payload.get('dispatch_context', {})
                active = req.active_dispatch
                stale = stale or not active or active.request_id != (result.request_id or effect.request.payload.get('request_id'))
                stale = stale or dispatched.get('base_consensus_digest') != req.consensus.digest
                stale = stale or dispatched.get('base_document_digest') != (req.draft.document_digest if req.draft else '')
            if effect.request.effect_type.startswith('requirements_') and effect.request.payload.get('state'):
                old = effect.request.payload['state']
                old_draft = old.get('draft') or {}
                stale = stale or old_draft.get('document_digest', '') != (req.draft.document_digest if req.draft else '')
                from ..domain.requirements.model import RequirementsState
                stale = stale or RequirementsState.from_dict(old).consensus.digest != req.consensus.digest
            if effect.request.payload.get('approved_input_digest'):
                source = context.review.approved_requirements_input if context.review else None
                stale = stale or not source or source.input_digest != effect.request.payload['approved_input_digest']
            if effect.request.effect_type == 'node_dispatch':
                offered = effect.request.payload
                stale = stale or offered.get('node_run_id') != context.parallel.active_node_run_id
                stale = stale or not context.review or offered.get('plan_hash') != context.review.plan_hash
            if effect.request.effect_type=='agent_dispatch' and str(target).startswith('MENXIA_GROUP'):
                stale = stale or effect.request.payload.get('request_id') != context.delivery.active_request_id
            if context.progression.state in ('REQ_CANCELLED','DONE','FAILED'):
                stale = True
            event = DomainEvent('STALE_RESULT' if stale else result.event_name, task_id,
                                context.progression.sequence, payload, datetime.now(timezone.utc).isoformat(), identity)
            self.repository.append_domain_event(event)
            return
