"""Fan out the first REQ_ANALYZE dispatch into parallel read-only scouts.

The fan-out happens inside a single agent effect: ``ScoutFanoutRunner`` runs
one probe per facet through the existing ``AgentWorkerRunner``, merges the
verified results into one deterministic analysis payload, and hands it back as
if a single analyst had replied.  Budget, guard, cache and repair semantics
are owned by the outer ``RequirementsAgentRunner`` and stay unchanged.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import os
import re
import time
from typing import Callable
from ..contracts import contract_for_state
from ..domain.requirements.model import normalized_quote
from ..domain.requirements.result_validation import result_errors
from ..requirements_observability import record
from .effects import EffectOutcome
from .requirements_agent import _RUNTIME_KEYS, verify_facts
from .requirements_quotes import normalize_claim_quotes

DEFAULT_FACETS = ('intent', 'code', 'docs', 'constraints')
KNOWN_FACETS = frozenset(set(DEFAULT_FACETS) | {'history'})
MAX_FACETS = 5
FACT_CAP_TOTAL = 40
FACET_PROMPT_VERSION = 1

FACET_FOCUS = {
    'intent': '用户原文中的目标、行为、约束、非目标与范围取舍',
    'code': '项目内与需求直接相关的代码、模块、接口、配置现状',
    'docs': '既有文档、规格、计划中的相关规定与冲突',
    'constraints': '外部强加、无法靠本次决定改变的限制',
    'history': '相关历史任务、已批准需求与评审记录',
}

_FACET_INSTRUCTIONS = {
    'intent': '从用户原始来源中提取明确的目标、行为、约束、非目标与范围取舍，作为 claims'
              '（kind 可为 goal/behavior/constraint/non_goal/boundary，必须逐字引用用户原文）。'
              '区分"用户明确说的"与"你推断的"，推断只能进 assumptions（proposed，必要时 blocking=true）。'
              '同时可返回与意图直接相关的 facts。',
    'code': '在项目内定位与需求直接相关的代码、模块、接口、配置的现状，返回 facts（来源=文件路径[:行号]）。'
            '只读，不改，不通读整个仓库；覆盖面以"足以说明现状与影响"为准。',
    'docs': '查找与需求相关的既有文档、规格、计划、README、说明，返回 facts；特别标出已有的相反/重叠规定。',
    'constraints': '查找外部强加、无法靠我们的决定改变的限制（既有接口能力、平台/版本兼容、合规、'
                   '已定技术栈、禁止引入的依赖），返回 facts。不要把"本次范围取舍"当约束。',
    'history': '查找与该需求相关的历史任务、已批准需求、评审记录，返回 facts。',
}


def _facet_prompt(facet: str, fact_cap: int) -> str:
    tail = (f'你是只读探针，限时完成；只找线索，不提问、不写需求正文、不设计实现。'
            f'每条 fact 必须含来源文件与逐字连续引用；找不到的内容写入 analysis.gaps，不要猜。'
            f'最多返回 {fact_cap} 条 fact。questions 必须为空数组。')
    return f'[探针方向：{FACET_FOCUS[facet]}] {_FACET_INSTRUCTIONS[facet]}\n{tail}'


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def _parse_pairs(raw: str, cast, default: dict, label: str) -> dict:
    """Parse ``name=value,name=value`` pairs; invalid entries fall back recorded."""
    parsed = dict(default)
    for chunk in raw.split(','):
        chunk = chunk.strip()
        if not chunk:
            continue
        name, _, value = chunk.partition('=')
        name, value = name.strip(), value.strip()
        if not name or not value:
            record('SCOUT_CONFIG_INVALID', label=label, entry=chunk, reason='missing name or value')
            continue
        try:
            parsed[name] = cast(value)
        except (TypeError, ValueError):
            record('SCOUT_CONFIG_INVALID', label=label, entry=chunk, reason='invalid value')
    return parsed


class ScoutConfig:
    """Immutable fan-out configuration resolved from the environment."""

    def __init__(self, *, enabled: bool=True, facets: tuple[str, ...]=DEFAULT_FACETS,
                 timeouts: dict[str, float] | None=None, attempts: dict[str, int] | None=None,
                 fact_cap: int=12, max_parallel: int=3) -> None:
        self.enabled = bool(enabled)
        self.facets = tuple(facets)
        self._timeouts = {'*': 300.0, **(timeouts or {})}
        self._attempts = {'*': 1, **(attempts or {})}
        self.fact_cap = max(1, int(fact_cap))
        self.max_parallel = max(1, int(max_parallel))

    @classmethod
    def from_env(cls, environ=None) -> 'ScoutConfig':
        env = os.environ if environ is None else environ
        enabled = str(env.get('REQUIREMENTS_SCOUT', '1')).strip() != '0'
        facets: list[str] = []
        for name in str(env.get('REQUIREMENTS_SCOUT_FACETS', ','.join(DEFAULT_FACETS))).split(','):
            facet = name.strip()
            if not facet:
                continue
            if facet not in KNOWN_FACETS:
                record('SCOUT_CONFIG_INVALID', label='facets', entry=facet, reason='unknown facet ignored')
                continue
            if facet not in facets:
                facets.append(facet)
        if 'intent' in facets:
            facets.remove('intent')
        facets.insert(0, 'intent')
        if len(facets) > MAX_FACETS:
            record('SCOUT_CONFIG_INVALID', label='facets', entry=','.join(facets),
                   reason=f'truncated to {MAX_FACETS}')
            facets = facets[:MAX_FACETS]
        timeouts = _parse_pairs(str(env.get('REQUIREMENTS_SCOUT_TIMEOUTS', 'intent=2400,*=2100')),
                                lambda value: max(1.0, float(value)), {'intent': 2400.0, '*': 2100.0}, 'timeouts')
        attempts = _parse_pairs(str(env.get('REQUIREMENTS_SCOUT_ATTEMPTS', 'intent=2,*=1')),
                                lambda value: max(1, int(value)), {'intent': 2, '*': 1}, 'attempts')
        try:
            fact_cap = max(1, int(str(env.get('REQUIREMENTS_SCOUT_FACT_CAP', '12')).strip()))
        except (TypeError, ValueError):
            record('SCOUT_CONFIG_INVALID', label='fact_cap', reason='invalid value')
            fact_cap = 12
        try:
            max_parallel = max(1, int(str(env.get('ANALYST_MAX_WORKERS', '3')).strip()))
        except (TypeError, ValueError):
            record('SCOUT_CONFIG_INVALID', label='max_parallel', reason='invalid value')
            max_parallel = 3
        return cls(enabled=enabled, facets=tuple(facets), timeouts=timeouts,
                   attempts=attempts, fact_cap=fact_cap, max_parallel=max_parallel)

    def timeout(self, facet: str) -> float:
        return float(self._timeouts.get(facet, self._timeouts.get('*', 2100.0)))

    def attempts(self, facet: str) -> int:
        return int(self._attempts.get(facet, self._attempts.get('*', 1)))


class _ProbeResult:
    __slots__ = ('facet', 'clean', 'outcome', 'report')

    def __init__(self, facet, clean, outcome, report) -> None:
        self.facet = facet
        self.clean = clean          # {'facts': [...], 'assumptions': [...], 'gaps': [...]} or None
        self.outcome = outcome      # last EffectOutcome of the last attempt
        self.report = report


class ScoutFanoutRunner:
    """Run the first REQ_ANALYZE analysis as parallel read-only probes."""

    def __init__(self, inner, *, config: ScoutConfig | None=None, report_dir=None,
                 max_parallel: int | None=None, close_child: Callable[[str, str], None] | None=None) -> None:
        self.inner = inner
        self._config = config or ScoutConfig.from_env()
        if max_parallel is not None:
            self._config.max_parallel = max(1, int(max_parallel))
        self._report_dir = report_dir
        self._close_child = close_child

    def cache_salt(self) -> str:
        material = {'enabled': self._config.enabled, 'facets': list(self._config.facets),
                    'fact_cap': self._config.fact_cap, 'facet_prompt_version': FACET_PROMPT_VERSION}
        return hashlib.sha256(_canonical(material).encode('utf-8')).hexdigest()

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def _should_fanout(self, request) -> bool:
        if not self._config.enabled:
            return False
        payload = request.payload
        if str(payload.get('target_state') or '') != 'REQ_ANALYZE':
            return False
        state = payload.get('validation_state')
        if not isinstance(state, dict):
            return False
        consensus = state.get('consensus')
        claims = consensus.get('claims') if isinstance(consensus, dict) else None
        return not claims

    def _probe_request(self, request, facet: str, index: int, extra_prompt: str=''):
        payload = dict(request.payload)
        original_request_id = str(payload.get('request_id') or request.idempotency_key)
        dispatch_context = deepcopy(payload.get('dispatch_context')) if isinstance(
            payload.get('dispatch_context'), dict) else {}
        dispatch_context['scout'] = {'facet': facet, 'focus': FACET_FOCUS[facet],
                                     'allow_claims': facet == 'intent', 'fact_cap': self._config.fact_cap}
        prompt_ref = str(payload.get('prompt_ref') or '')
        payload.update({
            'worker_id': f'scout-{facet}-{index}',
            'fanout_parent_id': str(payload.get('issue_id') or ''),
            'fanout_title': f'[scout:{facet}] {request.task_id}',
            'timeout_seconds': self._config.timeout(facet),
            'request_id': f'{original_request_id}-scout-{facet}',
            'dispatch_context': dispatch_context,
            'prompt_ref': (prompt_ref + ('\n\n' if prompt_ref else '')
                           + _facet_prompt(facet, self._config.fact_cap) + extra_prompt),
        })
        return replace(request, effect_id=f'{request.effect_id}-scout-{facet}',
                       idempotency_key=f'{request.idempotency_key}-scout-{facet}', payload=payload)

    def _accept_probe(self, outcome, request, facet: str):
        """Validate and clean one probe reply. Returns ``(clean, errors)``.

        ``clean`` is None when the attempt is rejected; ``errors`` carries the
        feedback text for a retry prompt.
        """
        if outcome.status != 'SUCCEEDED' or outcome.event_name != 'ANALYSIS_READY':
            reason = str(getattr(outcome, 'failure', None) and outcome.failure.error_code
                         or outcome.event_name or 'no result')
            return None, [f'探针未返回可用结果：{reason}']
        payload = deepcopy(dict(outcome.event_payload or {}))
        try:
            contract = contract_for_state('REQ_ANALYZE', workflow='requirements-v1')
        except Exception as error:  # pragma: no cover - configuration error
            return None, [f'contract unavailable: {error}']
        schema_errors = list(contract.validate({k: v for k, v in payload.items() if k not in _RUNTIME_KEYS}))
        if schema_errors:
            return None, schema_errors
        analysis = payload.get('analysis') if isinstance(payload.get('analysis'), dict) else {}
        claims = analysis.get('claims') if isinstance(analysis.get('claims'), list) else []
        facts = [fact for fact in analysis.get('facts', []) if isinstance(fact, dict)]
        assumptions = [row for row in analysis.get('assumptions', []) if isinstance(row, dict)]
        gaps = [str(gap) for gap in analysis.get('gaps', []) if str(gap).strip()]
        questions_dropped = len(payload.get('questions', []) or [])
        counts = {'claims_dropped': 0, 'questions_dropped': questions_dropped}
        errors: list[str] = []
        verified, evidence_errors = verify_facts(facts, request.payload.get('dispatch_context', {}).get('project_root', ''),
                                                 getattr(request, 'task_id', ''))
        # A fact whose quote does not match its source is dropped and counted,
        # but never fails the probe (spec 5.1.5.3).
        counts['facts_rejected'] = len(facts) - len(verified)
        if facet != 'intent':
            counts['claims_dropped'] = len(claims)
            claims = []
            cleaned_assumptions = []
            for row in assumptions:
                row = dict(row)
                row.pop('fact_id', None)
                row.pop('assumption_id', None)
                row['blocking'] = False
                cleaned_assumptions.append(row)
            assumptions = cleaned_assumptions
        cleaned_facts = []
        for fact in verified:
            row = dict(fact)
            # Runtime-owned fields are re-derived by the outer validation tail;
            # a stored fingerprint would fail the FACT schema (const '').
            row.pop('source_fingerprint', None)
            if facet != 'intent':
                row.pop('fact_id', None)
                row['requirement_ids'] = []
            cleaned_facts.append(row)
        if len(cleaned_facts) > self._config.fact_cap:
            counts['facts_rejected'] = counts.get('facts_rejected', 0) + len(cleaned_facts) - self._config.fact_cap
            cleaned_facts = cleaned_facts[:self._config.fact_cap]
        if facet == 'intent':
            raw_sources = (request.payload.get('validation_state') or {}).get('sources', [])
            span_errors = normalize_claim_quotes(payload, raw_sources)
            gate = result_errors(payload, request.payload.get('dispatch_context', {}))
            errors.extend(span_errors)
            errors.extend(gate)
        if errors:
            return None, errors
        clean = {'facts': cleaned_facts, 'assumptions': assumptions, 'gaps': gaps, 'counts': counts}
        return clean, []

    def _close_probe_issue(self, probe: _ProbeResult) -> None:
        child_id = str(getattr(probe.outcome, 'operation_id', '') or '') if probe.outcome is not None else ''
        if probe.clean is not None and not child_id:
            record('SCOUT_CHILD_ISSUE_FALLBACK', facet=probe.facet)
            return
        if child_id and self._close_child is not None:
            status = 'done' if probe.clean is not None else 'cancelled'
            try:
                self._close_child(child_id, status)
            except Exception as error:  # closing must never fail a probe
                record('SCOUT_CHILD_ISSUE_CLOSE_FAILED', facet=probe.facet, reason=str(error)[:200])

    def _run_probe(self, request, facet: str, index: int) -> _ProbeResult:
        attempts = self._config.attempts(facet)
        started = time.monotonic()
        last_outcome, errors = None, []
        clean = None
        for attempt in range(1, attempts + 1):
            extra = ''
            if attempt > 1 and errors:
                extra = '\n\n上次回复被拒：' + '；'.join(str(item) for item in errors) + '，请只修复这些路径'
            probe = self._probe_request(request, facet, index, extra)
            try:
                outcome = self.inner.run_once(probe)
            except Exception as error:  # a probe failure must never escape
                last_outcome, errors = None, [str(error) or error.__class__.__name__]
                continue
            last_outcome = outcome
            clean, errors = self._accept_probe(outcome, request, facet)
            if clean is not None:
                break
        result = _ProbeResult(facet, clean, last_outcome, {
            'facet': facet,
            'status': 'ok' if clean is not None else 'failed',
            'attempts': attempt,
            'seconds': round(time.monotonic() - started, 3),
            'facts_accepted': len(clean['facts']) if clean else 0,
            'facts_rejected': clean['counts']['facts_rejected'] if clean else 0,
            'claims_dropped': clean['counts']['claims_dropped'] if clean else 0,
            'questions_dropped': clean['counts']['questions_dropped'] if clean else 0,
            'failure': '' if clean else '；'.join(str(item) for item in errors)[:500],
        })
        if clean is not None:
            result.clean['counts']['attempts'] = result.report['attempts']
            result.clean['counts']['seconds'] = result.report['seconds']
        self._close_probe_issue(result)
        record('SCOUT_PROBE_DONE', task_id=getattr(request, 'task_id', ''), facet=facet,
               status=result.report['status'], attempts=result.report['attempts'],
                facts=result.report['facts_accepted'], failure=result.report['failure'][:120],
               seconds=result.report['seconds'])
        return result

    def _write_report(self, request, request_id: str, probes, merged: dict) -> None:
        if self._report_dir is None:
            return
        try:
            safe = re.sub(r'[^\w.-]', '_', str(request_id)) or 'request'
            directory = os.path.join(str(self._report_dir), str(request.task_id))
            os.makedirs(directory, exist_ok=True)
            body = {'facets': [probe.report for probe in probes], 'merged': merged}
            with open(os.path.join(directory, safe + '.json'), 'w', encoding='utf-8') as handle:
                json.dump(body, handle, ensure_ascii=False, indent=1, sort_keys=True)
        except OSError:
            pass

    def _unavailable_fact(self, facet: str, observation: str, counters: dict) -> dict:
        number = counters.get(facet, 0) + 1
        counters[facet] = number
        return {'status': 'unavailable', 'observation': observation,
                'source': f'scout:{facet}', 'quote': '', 'local_key': f'{facet}.gap-{number}'}

    def _merge(self, request, probes: list[_ProbeResult]):
        facets = self._config.facets
        by_facet = {probe.facet: probe for probe in probes}
        intent = by_facet['intent']
        if intent.clean is None:
            original_request_id = str(request.payload.get('request_id') or request.idempotency_key)
            if intent.outcome is not None:
                payload = dict(intent.outcome.event_payload or {})
                payload['request_id'] = original_request_id
                return replace(intent.outcome, event_payload=payload,
                               request_id=original_request_id)  # let the outer runner turn it into repair feedback
            return EffectOutcome(status='FAILED', event_name='FAIL',
                                 event_payload={'error_code': 'SCOUT_INTENT_PROBE_FAILED',
                                                'reason': intent.report['failure']},
                                 request_id=original_request_id)
        original_request_id = str(request.payload.get('request_id') or request.idempotency_key)
        merged = deepcopy(dict(intent.outcome.event_payload or {}))
        analysis = dict(merged.get('analysis') if isinstance(merged.get('analysis'), dict) else {})
        analysis['claims'] = analysis.get('claims') if isinstance(analysis.get('claims'), list) else []
        facts: list[dict] = []
        assumptions: list[dict] = []
        counters: dict[str, int] = {}
        seen_facts: set[tuple[str, str]] = set()
        seen_assumptions: set[str] = set()
        deduped = renamed = 0
        for facet in facets:
            probe = by_facet[facet]
            if probe.clean is None:
                facts.append(self._unavailable_fact(facet, f'探针 {facet} 未完成：{probe.report["failure"]}', counters))
                continue
            prefix = '' if facet == 'intent' else facet + '.'
            rename: dict[str, str] = {}
            facet_facts = []
            for fact in probe.clean['facts']:
                row = dict(fact)
                key = row.get('local_key')
                if prefix and isinstance(key, str) and key:
                    rename[key] = prefix + key
                    row['local_key'] = rename[key]
                    renamed += 1
                facet_facts.append(row)
            for assumption in probe.clean['assumptions']:
                row = dict(assumption)
                key = row.get('local_key')
                if prefix and isinstance(key, str) and key:
                    row['local_key'] = prefix + key
                if prefix and isinstance(row.get('source_ids'), list):
                    row['source_ids'] = [rename.get(ref, ref) for ref in row['source_ids']
                                         if isinstance(ref, str)]
                statement = ''.join(str(row.get('statement', '')).split()).casefold()
                if statement in seen_assumptions:
                    deduped += 1
                    continue
                seen_assumptions.add(statement)
                assumptions.append(row)
            for row in facet_facts:
                dedupe_key = (str(row.get('source', '')).strip(),
                              normalized_quote(str(row.get('quote', ''))) if row.get('status', 'observed') == 'observed' else str(row.get('local_key', '')))
                if dedupe_key in seen_facts:
                    deduped += 1
                    continue
                seen_facts.add(dedupe_key)
                facts.append(row)
            for gap in probe.clean['gaps']:
                facts.append(self._unavailable_fact(facet, gap, counters))
        if len(facts) > FACT_CAP_TOTAL:
            facts = facts[:FACT_CAP_TOTAL]
        analysis['facts'] = facts
        analysis['assumptions'] = assumptions
        analysis['gaps'] = []
        merged['analysis'] = analysis
        merged['questions'] = []
        merged['action'] = 'ANALYSIS_READY'
        succeeded = sum(1 for probe in probes if probe.clean is not None)
        merged['reason'] = f'探索汇总：{len(facets)}个探针，成功{succeeded}，失败{len(facets) - succeeded}'
        merged['task_id'] = request.task_id
        merged['request_id'] = original_request_id
        merged['role'] = str(request.payload.get('role') or '')
        merged['state'] = str(request.payload.get('target_state') or '')
        merged_counts = {'facets': len(facets), 'succeeded': succeeded, 'failed': len(facets) - succeeded,
                         'facts': len(facts), 'assumptions': len(assumptions),
                         'unavailable_facts': sum(1 for row in facts if row.get('status') == 'unavailable'),
                         'deduped': deduped, 'renamed_local_keys': renamed}
        self._write_report(request, original_request_id, probes, merged_counts)
        record('SCOUT_FANOUT_DONE', task_id=request.task_id, **merged_counts)
        return replace(intent.outcome, event_payload=merged, request_id=original_request_id)

    def run_once(self, request) -> EffectOutcome:
        if not self._should_fanout(request):
            return self.inner.run_once(request)
        facets = self._config.facets
        record('SCOUT_FANOUT_START', task_id=request.task_id, facets=','.join(facets))
        workers = max(1, min(len(facets), self._config.max_parallel))
        fanout_started = time.monotonic()

        def run_probe(pair):
            index, facet = pair[0], pair[1]
            # waited_s is the time this probe sat behind the worker cap (ANALYST_MAX_WORKERS).
            record('SCOUT_PROBE_START', task_id=getattr(request, 'task_id', ''), facet=facet, index=index,
                   workers=workers, waited_s=round(time.monotonic() - fanout_started, 1))
            return self._run_probe(request, facet, index)

        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix='scout') as pool:
            probes = list(pool.map(run_probe, list(enumerate(facets, 1))))
        outcome = self._merge(request, probes)
        if not isinstance(outcome, EffectOutcome):  # defensive; _merge returns outcomes
            return self.inner.run_once(request)
        return outcome
