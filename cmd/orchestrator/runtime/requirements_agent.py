"""Strict new-role boundary and runtime verification of claimed repository facts."""
from dataclasses import replace
import hashlib
import os
import re
import copy
import json
import time
from pathlib import Path
from ..contracts import contract_for_state
from ..domain.requirements.result_validation import result_errors
from ..domain.requirements.model import RequirementsState
from ..domain.requirements.consensus import apply_analysis
from ..domain.requirements.questions import merge_questions
from ..requirements_observability import record
from ..domain.errors import CONTRACT_REJECTED_EVENT, UNSTRUCTURED_REPLY_EVENT
from .effects import EffectOutcome
from .compat_effects import _skill_lock
from .requirements_fact_sources import source_path

# Fields that bind one agent reply to the dispatch that produced it.  A cached
# reply replayed for a different task must not carry another task's values.
_BINDING_KEYS = ('task_id', 'request_id', 'role', 'state')
_RUNTIME_KEYS = {'result_artifact_id','operation_id','external_message_id','idempotency_key',
    'result_source','result_path','result_sha256','result_utf8_bytes','result_has_bom','result_transport_backfilled_fields',
    'supplemental_reports'}


def _canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


class RequirementsAgentRunner:
    def __init__(self, inner, *, allowed_root=None):
        self.inner = inner
        self.allowed_root = Path(allowed_root).resolve() if allowed_root else None
        self.cache_dir = (self.allowed_root / 'analyze-cache') if self.allowed_root else None
        self.cache_enabled = self.cache_dir is not None and os.environ.get(
            'REQUIREMENTS_ANALYZE_CACHE', '1') != '0'

    def rejected_payload(self, outcome, request):
        """Inspect correlated rejected JSON for feedback only; never accept it."""
        raw = dict(outcome.event_payload or {})
        try:
            if raw.get('raw_reply_path') and self.allowed_root:
                path = Path(raw['raw_reply_path']).resolve()
                path.relative_to(self.allowed_root)
                if path.stat().st_size > 1024 * 1024:
                    return None
                body = path.read_text(encoding='utf-8-sig')
            elif not raw.get('raw_reply_truncated') and isinstance(raw.get('raw_reply'), str):
                body = raw['raw_reply']
            else:
                return None
            payload = json.loads(body)
            if not isinstance(payload, dict):
                return None
            if any(payload.get(key) != expected for key,expected in (
                ('task_id',request.task_id), ('request_id',request.payload.get('request_id',request.idempotency_key)),
                ('state',request.payload['target_state']), ('role',request.payload.get('role')))):
                return None
            return payload
        except (OSError, UnicodeError, ValueError, TypeError):
            return None

    def _analyze_cache_path(self, request):
        """Content-addressed cache key for one REQ_ANALYZE dispatch, or None.

        The key covers everything the analysis can depend on: the validated
        domain state (whose skeleton embeds the raw request text), the locked
        analyst skill version and the result contract schema.  ``active_dispatch``
        and ``budget`` are dropped before hashing: they only carry per-task
        bookkeeping (logical_dispatch_id, request_id, charges) that must not
        split identical analysis inputs across rounds.
        """
        if not self.cache_enabled or request.payload.get('target_state') != 'REQ_ANALYZE':
            return None
        state = request.payload.get('validation_state')
        if not isinstance(state, dict) or not state:
            return None
        key_state = {k: v for k, v in state.items() if k not in ('active_dispatch', 'budget')}
        try:
            contract = contract_for_state('REQ_ANALYZE', workflow='requirements-v1')
            schema_hash = hashlib.sha256(_canonical_json(contract.schema).encode('utf-8')).hexdigest()
        except Exception:
            schema_hash = ''
        material = {'v': 2,
                    'skill_sha': str(_skill_lock('REQ_ANALYZE').get('sha256', '') or ''),
                    'schema_hash': schema_hash,
                    'validation_state': key_state}
        return self.cache_dir / (hashlib.sha256(_canonical_json(material).encode('utf-8')).hexdigest() + '.json')

    def _analyze_cache_fresh(self, payload) -> bool:
        """True when every source a cached fact quotes still hashes the same."""
        analysis = payload.get('analysis') if isinstance(payload.get('analysis'), dict) else {}
        facts = analysis.get('facts') or []
        for fact in facts:
            if not isinstance(fact, dict) or fact.get('status', 'observed') != 'observed':
                continue
            fingerprint = str(fact.get('source_fingerprint', '') or '')
            if not fingerprint:
                continue
            try:
                content = source_path(str(fact.get('source', ''))).read_bytes()
            except (OSError, ValueError):
                return False
            if hashlib.sha256(content).hexdigest() != fingerprint:
                return False
        return True

    def _analyze_cache_hit(self, request):
        """Return ``(rebound payload, cache path or None)`` for a usable hit."""
        path = self._analyze_cache_path(request)
        if path is None:
            return None, None
        try:
            entry = json.loads(path.read_text(encoding='utf-8-sig'))
            payload = entry['payload']
            if not isinstance(payload, dict) or str(payload.get('action', '')) != 'ANALYSIS_READY':
                return None, path
        except (OSError, ValueError, KeyError, TypeError):
            return None, path
        if not self._analyze_cache_fresh(payload):
            return None, path
        payload = copy.deepcopy(payload)
        # The runtime adds source_fingerprint AFTER contract validation, so a
        # replayed payload must carry none: strip them and let the validation
        # tail below re-derive them from the current files.
        analysis = payload.get('analysis')
        if isinstance(analysis, dict) and isinstance(analysis.get('facts'), list):
            for fact in analysis['facts']:
                if isinstance(fact, dict):
                    fact.pop('source_fingerprint', None)
        payload['task_id'] = request.task_id
        payload['request_id'] = request.payload.get('request_id', request.idempotency_key)
        payload['role'] = request.payload.get('role', '')
        payload['state'] = request.payload['target_state']
        payload['reason'] = '（分析缓存）分析输入与既有已验证结论完全一致，直接复用该结论。'
        record('ANALYZE_CACHE_HIT', task_id=request.task_id, key=path.stem)
        return payload, path

    def _analyze_cache_evict(self, path) -> None:
        try:
            path.unlink()
        except OSError:
            pass

    def _analyze_cache_store(self, request, payload) -> None:
        path = self._analyze_cache_path(request)
        if path is None or not isinstance(payload, dict):
            return
        clean = {key: copy.deepcopy(value) for key, value in payload.items()
                 if key not in _RUNTIME_KEYS and key not in _BINDING_KEYS}
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + '.tmp')
            stamp = time.strftime('%Y-%m-%dT%H:%M:%S%z')
            temporary.write_text(json.dumps({'cached_at': stamp,
                                             'payload': clean}, ensure_ascii=False), encoding='utf-8')
            os.replace(temporary, path)
            record('ANALYZE_CACHE_STORED', task_id=request.task_id, key=path.stem)
        except OSError:
            pass

    def run_once(self, request):
        cached_payload, cached_path = self._analyze_cache_hit(request)
        if cached_payload is not None:
            outcome = EffectOutcome(status='SUCCEEDED',
                                    event_name=str(cached_payload.get('action') or ''),
                                    event_payload=cached_payload)
        else:
            outcome = self.inner.run_once(request)
            if outcome.status != 'SUCCEEDED' or outcome.event_name == UNSTRUCTURED_REPLY_EVENT:
                return outcome
        target = request.payload['target_state']
        if not target.startswith('REQ_') and target != 'MENXIA_REQUIREMENTS_INTAKE':
            return outcome
        contract = contract_for_state(target,workflow='requirements-v1')
        rejected = outcome.event_name == CONTRACT_REJECTED_EVENT
        candidate = self.rejected_payload(outcome,request) if rejected else outcome.event_payload
        if candidate is None:
            return outcome
        payload = copy.deepcopy(dict(candidate or {}))
        runtime_keys = {'result_artifact_id','operation_id','external_message_id','idempotency_key',
            'result_source','result_path','result_sha256','result_utf8_bytes','result_has_bom','result_transport_backfilled_fields',
            'supplemental_reports'}
        errors = contract.validate({k:v for k,v in payload.items() if k not in runtime_keys})
        dispatch = request.payload.get('dispatch_context',{})
        semantic_errors = result_errors(payload,dispatch)
        analysis = payload.get('analysis',{})
        facts = analysis.get('facts',[]) if target == 'REQ_ANALYZE' and isinstance(analysis,dict) else payload.get('fact_updates',[])
        if not isinstance(facts,list):
            facts = []
        evidence_errors = []
        project_root = request.payload.get('dispatch_context',{}).get('project_root','')
        for index, fact in enumerate(facts):
            if not isinstance(fact,dict) or not isinstance(fact.get('source',''),str) or not isinstance(fact.get('quote',''),str):
                continue
            if fact.get('status','observed') != 'observed':
                continue
            try:
                path = source_path(fact['source'],project_root)
                content = path.read_bytes()
                text = content.decode('utf-8-sig')
                # File-reading tools commonly expose CRLF as LF. Only line-ending
                # equivalence is allowed; indentation, text and contiguity stay exact.
                if not fact['quote'] or fact['quote'].replace('\r\n','\n') not in text.replace('\r\n','\n'):
                    raise ValueError('FACT_QUOTE_NOT_IN_SOURCE')
                fingerprint = hashlib.sha256(content).hexdigest()
                fact['source_fingerprint'] = fingerprint
                suffix = re.search(r':\d+(?:-\d+)?$',fact['source'])
                fact['source'] = str(path) + (suffix.group() if suffix else '')
            except (OSError,UnicodeError,KeyError,ValueError) as error:
                evidence_errors.append({'index':index,'fact_id':fact.get('fact_id') or fact.get('local_key',''),
                                        'source':fact.get('source',''),'reason':str(error)})
        if not errors and not semantic_errors and not evidence_errors and target == 'REQ_ANALYZE' and request.payload.get('validation_state'):
            try:
                aliases = {}
                state = apply_analysis(RequirementsState.from_dict(request.payload['validation_state']),payload,aliases=aliases)
                merge_questions(state,payload.get('questions',[]),aliases)
            except (ValueError,TypeError,KeyError) as error:
                semantic_errors.append('analysis/questions: '+str(error))
        all_errors = list(dict.fromkeys([*errors,*semantic_errors]))
        if all_errors or evidence_errors or rejected:
            if cached_path is not None and cached_payload is not None:
                # A stale or otherwise unusable cache entry must not poison
                # the retry: evict it so the next attempt dispatches fresh.
                self._analyze_cache_evict(cached_path)
            code = 'AGENT_REPLY_VALIDATION_FAILED' if semantic_errors else 'AGENT_REPLY_CONTRACT_REJECTED' if errors or rejected else 'FACT_EVIDENCE_INVALID'
            evidence_feedback = [f"{'analysis.facts' if target == 'REQ_ANALYZE' else 'fact_updates'}[{e['index']}].quote/source: {e['reason']}" for e in evidence_errors]
            feedback = all_errors + evidence_feedback
            if not feedback:
                feedback = [str(dict(outcome.event_payload or {}).get('contract_rejection') or 'Transport rejected this result; preserve result binding.')]
            record('REQUIREMENTS_RESULT_VALIDATION_FAILED',task_id=getattr(request,'task_id',''),
                   request_id=request.payload.get('request_id',''),schema_error_count=len(errors),
                   semantic_error_count=len(semantic_errors),evidence_error_count=len(evidence_errors),reason=code)
            return replace(outcome,event_name='FAIL',event_payload={**payload,
                'error_code':code,'reason':'Repair ALL reported paths in this same response:\n'+'\n'.join(feedback)+
                    '\nUse exact contiguous quotes; '
                    'omit source_fingerprint. If a source cannot be read, mark that fact unavailable. '
                    'New question revision must be omitted or 1; all local_key aliases must be unique. '
                    'For MODEL_CANNOT_ACCEPT_ASSUMPTION set the indexed disposition to proposed; changing an ID does not fix it. '
                    'Return existing human-accepted assumptions unchanged or omit them. '
                    'Preserve human decisions and valid research. Do not repeat the full repository investigation.',
                'validation_errors':feedback,'evidence_errors':evidence_errors,'retryable':True,
                **({'raw_reply_path':dict(outcome.event_payload or {}).get('raw_reply_path','')} if rejected else {})})
        if cached_payload is None:
            self._analyze_cache_store(request, payload)
        return replace(outcome,event_payload=payload)
