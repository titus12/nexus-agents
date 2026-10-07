"""Requirements side effects, run only after transition commit."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import uuid
import subprocess
import time
import urllib.error
import logging
from .effects import EffectOutcome
from ..domain.requirements.model import RequirementsState, canonical, text_digest
from ..domain.requirements.document import render_markdown, validate_draft
from ..domain.requirements.review import review_input_digest
from ..domain.errors import DomainError

logger = logging.getLogger('review_orchestrator_fsm')


def immutable_write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != content:
            raise ValueError('IMMUTABLE_ARTIFACT_COLLISION')
        return
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    with temporary.open('xb') as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def materialize(root, state, approved=False):
    draft = state.draft
    stamp = state.review_stamp
    if not draft or not stamp or not stamp.passed or stamp.input_digest != review_input_digest(draft, state.consensus):
        raise ValueError('VALID_REVIEW_REQUIRED')
    if state.consensus.pending_source_ids:
        raise ValueError('UNINTERPRETED_HUMAN_INPUT')
    if validate_draft(draft, state.consensus):
        raise ValueError('MECHANICAL_DOCUMENT_INVALID')
    if approved and (not state.approval or state.approval.review_input_digest != stamp.input_digest):
        raise ValueError('VERIFIED_APPROVAL_REQUIRED')
    suffix = state.approval.approval_id if approved else stamp.input_digest
    directory = Path(root) / 'requirements' / ('approved' if approved else 'drafts') / f'v{draft.version:03d}-{draft.document_digest}-{suffix}'
    files = {'requirements.md':render_markdown(draft).encode('utf-8'),
             'consensus.json':canonical(state.consensus).encode('utf-8'),
             'traceability.json':canonical({'sections':[{'section_id':s.section_id,'requirement_ids':s.requirement_ids,
                'decision_ids':s.decision_ids,'fact_ids':s.fact_ids} for s in draft.sections],
                'sources':state.sources,'decisions':state.decisions}).encode('utf-8'),
             'review.json':canonical(stamp).encode('utf-8')}
    if approved:
        files['approval.json'] = canonical(state.approval).encode('utf-8')
    for name, content in files.items():
        immutable_write(directory / name, content)
    import hashlib
    manifest = {'workflow':'requirements-v1','version':draft.version,'document_digest':draft.document_digest,
                'review_input_digest':stamp.input_digest,'policy_version':state.budget.policy_version,
                'files':{name:hashlib.sha256(content).hexdigest() for name,content in files.items()}}
    immutable_write(directory / 'manifest.json', canonical(manifest).encode('utf-8'))
    return directory


class RequirementsEffects:
    def __init__(self, root, port, sleep=None):
        self.root, self.port = Path(root), port
        self.sleep = sleep or (lambda seconds: None)

    def external_retry_dwell(self, state):
        recovery = state.recovery
        if not recovery or recovery.repair_fingerprint not in ('external', 'publication'):
            return 0
        count = next((c.count for c in state.budget.publication_retries
                      if c.effect_id == recovery.logical_dispatch_id), 0)
        return min(45 * 2 ** max(0, count - 1), 120)

    def run_once(self, request):
        from ..requirements_observability import record, error_code
        record('REQUIREMENTS_EFFECT_STARTED', task_id=request.task_id,
               effect=request.effect_type, key=request.idempotency_key)
        try:
            outcome = self._run_once(request)
            record('REQUIREMENTS_EFFECT_FINISHED', task_id=request.task_id,
                   effect=request.effect_type, key=request.idempotency_key,
                   status=outcome.status, event=outcome.event_name)
            return outcome
        except (TimeoutError,ConnectionError,urllib.error.URLError,subprocess.TimeoutExpired) as error:
            record('REQUIREMENTS_EFFECT_FAILED', task_id=request.task_id,
                   effect=request.effect_type, reason=type(error).__name__)
            return EffectOutcome('FAILED','FAIL',{'error_code':'COMMUNICATION_TRANSIENT','retryable':True,
                'reason':'通信暂时失败：%s: %s' % (type(error).__name__, str(error)[:300]),
                'publication_logical_key':request.idempotency_key})
        except DomainError as error:
            record('REQUIREMENTS_EFFECT_FAILED', task_id=request.task_id,
                   effect=request.effect_type, reason=error.error_code)
            return EffectOutcome('FAILED','FAIL',{'error_code':error.error_code,'retryable':False,'reason':str(error),
                'prerequisite_fingerprint':getattr(error,'prerequisite_fingerprint',''),
                'recovery_source_ref':getattr(error,'recovery_source_ref','')})
        except Exception as error:
            record('REQUIREMENTS_EFFECT_FAILED', task_id=request.task_id,
                   effect=request.effect_type, reason=error_code(error))
            raise

    def _run_once(self, request):
        if request.effect_type == 'requirements_progress':
            try:
                receipt = self.port.publish_progress(request.task_id,request.idempotency_key,
                                                     request.payload['body'])
            except Exception as error:
                logger.exception('REQUIREMENTS_PROGRESS_FAILED task_id=%s key=%s',
                                 request.task_id,request.idempotency_key)
                return EffectOutcome('FAILED',event_payload={'reason':str(error),
                    'error_code':'REQUIREMENTS_PROGRESS_NOT_DELIVERED','delivered':False,
                    'notification_key':request.idempotency_key})
            return EffectOutcome('SUCCEEDED',event_payload={**receipt,'notification_key':request.idempotency_key})
        state = RequirementsState.from_dict(dict(request.payload['state']))
        kind = request.effect_type
        if kind == 'requirements_notice':
            # Notification failure must not become another workflow failure event.
            try:
                receipt = self.port.publish_notice(request.task_id, state, request.idempotency_key,
                                                  request.payload['target_state'])
            except Exception as error:
                logger.exception('REQUIREMENTS_NOTIFY_FAILED task_id=%s key=%s',
                                 request.task_id, request.idempotency_key)
                return EffectOutcome('FAILED', event_payload={'reason':str(error),
                    'error_code':'REQUIREMENTS_NOTICE_NOT_DELIVERED'})
            return EffectOutcome('SUCCEEDED', event_payload=receipt)
        if kind in ('requirements_step','requirements_review_document','requirements_approval_verify'):
            from .requirements_fact_sources import changed_sources
            changes = changed_sources(state)
            if changes:
                return EffectOutcome('SUCCEEDED','FACTS_CHANGED',{'changes':changes})
        if kind == 'requirements_step':
            issues = validate_draft(state.draft, state.consensus)
            return EffectOutcome('SUCCEEDED', 'REPAIR_CONTENT' if issues else 'VALID', {})
        if kind == 'requirements_retry':
            dwell = self.external_retry_dwell(state)
            if dwell:
                self.sleep(dwell)
            return EffectOutcome('SUCCEEDED','RETRY_READY',{})
        if kind == 'requirements_cleanup':
            self.port.cancel_task(request.task_id)
            return EffectOutcome('SUCCEEDED')
        if kind == 'requirements_clarification_refresh':
            try:
                self.port.refresh_clarification(request.task_id,state)
            except Exception as error:
                from ..requirements_observability import record, error_code
                record('CARD_REFRESH_FAILED', task_id=request.task_id,
                       key=request.idempotency_key, reason=error_code(error))
                return EffectOutcome('SUCCEEDED','CARD_REFRESH_FAILED',{'reason':'回答已保存，卡片刷新失败：'+str(error)})
            return EffectOutcome('SUCCEEDED')
        if kind == 'requirements_clarification':
            self.port.publish_clarification(request.task_id, state, request.idempotency_key)
            return EffectOutcome('SUCCEEDED','CARD_DELIVERED',{})
        if kind == 'requirements_review_document':
            directory = materialize(self.root, state)
            result = self.port.publish_review(request.task_id, state, request.idempotency_key)
            return EffectOutcome('SUCCEEDED','REVIEW_DOCUMENT_DELIVERED',
                                 {**result,'document_ref':str(directory / 'requirements.md')})
        if kind == 'requirements_approval_verify':
            result = self.port.verify_approval(request.task_id, state)
            return EffectOutcome('SUCCEEDED','APPROVAL_VERIFIED',
                {**result,'review_input_digest':review_input_digest(state.draft, state.consensus)})
        if kind == 'requirements_publish':
            directory = materialize(self.root, state, approved=True)
            return EffectOutcome('SUCCEEDED','REQUIREMENTS_PUBLISHED',{'published_ref':str(directory)})
        if kind == 'requirements_handoff':
            from ..domain.requirements.handoff import make_approved_input
            input_ = make_approved_input(request.task_id, state)
            path = self.root / 'requirements' / 'handoff' / (state.draft.document_digest+'-'+state.approval.approval_id+'.json')
            immutable_write(path, canonical(input_).encode('utf-8'))
            return EffectOutcome('SUCCEEDED','HANDOFF_PERSISTED',{'handoff_ref':str(path)})
        raise ValueError('unknown requirements effect')
