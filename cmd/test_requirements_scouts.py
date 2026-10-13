"""Scout fan-out: parallel read-only probes merged into one analysis result."""
import json
import os
import tempfile
import threading
import time
import unittest
from copy import deepcopy
from pathlib import Path

from orchestrator.domain.decisions import EffectRequest
from orchestrator.domain.requirements.model import (ConsensusRevision, RequirementsState,
                                                    SourceRecord)
from orchestrator.runtime.effects import EffectOutcome
from orchestrator.runtime.requirements_agent import RequirementsAgentRunner
from orchestrator.runtime.requirements_scouts import ScoutConfig, ScoutFanoutRunner

SCHEMA_HASH = '6ae097008fef6b3292f412bf2f54fb94bef0307983c5df00c11562a9248241f9'
SOURCE_TEXT = '我们要把 HEAD 常量固定为 8。'


def scout_state():
    source = SourceRecord('src-000001', 'request', SOURCE_TEXT)
    return RequirementsState(sources=(source,), decisions=(),
                             consensus=ConsensusRevision(claims=(), active_decision_ids=()))


def make_request(tmp_root, state=None, target='REQ_ANALYZE'):
    payload = {'target_state': target, 'request_id': 'task-t:e1:1:REQ_ANALYZE:attempt-1',
               'role': 'review-analyst', 'task_id': 'task-t',
               'issue_id': 'issue-42',
               'dispatch_context': {'project_root': str(tmp_root),
                                    'sources': [{'source_id': 'src-000001', 'kind': 'request',
                                                 'author_id': 'user',
                                                 'spans': [f'[src-000001#1] {SOURCE_TEXT}']}]},
               'validation_state': (state or scout_state()).to_dict()}
    return EffectRequest('agent:x', 'agent_dispatch', 'task-t', 'req-1', payload=payload)


def probe_payload(tmp_root, facet, *, facts=None, claims=None, assumptions=None, gaps=(),
                  questions=(), event='ANALYSIS_READY'):
    source = tmp_root / 'src.txt'
    if facts is None:
        facts = [{'observation': 'HEAD 常量固定为 8', 'source': str(source),
                  'quote': 'HEAD 常量固定为 8', 'status': 'observed', 'local_key': 'fact-1'}]
    if claims is None:
        claims = ([{'topic_key': 'goal.head', 'statement': 'HEAD 常量固定为 8', 'kind': 'goal',
                    'origin': 'explicit', 'source_ids': ['src-000001'],
                    'span_ids': ['src-000001#1']}] if facet == 'intent' else
                  [{'requirement_id': 'req-800001', 'topic_key': 'goal.code', 'statement': '代码里也有',
                    'kind': 'goal', 'origin': 'explicit', 'source_ids': ['src-000001'],
                    'span_ids': ['src-000001#1']}])
    if assumptions is None:
        assumptions = [{'statement': f'{facet} 可能需要回归', 'local_key': 'asm-1'}]
    return {'action': event, 'mode': 'REQUIREMENTS_DOCUMENT', 'phase': 'ZHONGSHU',
            'role': 'review-analyst', 'state': 'REQ_ANALYZE',
            'contract_id': 'nexus.requirements.req_analyze.v1',
            'structured_output_protocol': 'nexus-agent-result-file-v2',
            'structured_output_schema_hash': SCHEMA_HASH,
            'task_id': 'task-old', 'request_id': f'old-req-scout-{facet}',
            'reason': f'{facet} 探针完成', 'questions': list(questions),
            'analysis': {'facts': list(facts), 'claims': list(claims),
                         'assumptions': list(assumptions), 'gaps': list(gaps)}}


class ScriptedInner:
    """Returns a canned probe payload per facet; can fail before succeeding."""

    def __init__(self, tmp_root, *, payloads=None, failures=None, barrier=None, delay=0.0):
        self.tmp_root = tmp_root
        self.payloads = payloads or {}
        self.failures = {facet: list(queue) for facet, queue in (failures or {}).items()}
        self.barrier = barrier
        self.delay = delay
        self.requests = []
        self.calls = 0

    def run_once(self, request):
        self.calls += 1
        scout = (request.payload.get('dispatch_context') or {}).get('scout') or {}
        facet = scout.get('facet') or ''
        self.requests.append(request)
        if not facet:  # passthrough: no probe overrides in this request
            return EffectOutcome(status='SUCCEEDED', event_name='ANALYSIS_READY',
                                 event_payload={'action': 'ANALYSIS_READY'})
        if self.barrier is not None:
            self.barrier.wait(timeout=10)
        if self.delay:
            time.sleep(self.delay)
        queue = self.failures.get(facet) or []
        if queue:
            failure = queue.pop(0)
            self.failures[facet] = queue
            if isinstance(failure, Exception):
                raise failure
            return failure
        payload = deepcopy(self.payloads.get(facet) or probe_payload(self.tmp_root, facet))
        payload['operation_id'] = f'child-{facet}'
        return EffectOutcome(status='SUCCEEDED', event_name=str(payload.get('action')),
                             event_payload=payload, operation_id=f'child-{facet}')


def make_config(facets=('intent', 'code'), **overrides):
    values = dict(enabled=True, facets=facets, timeouts={'intent': 600.0, '*': 300.0},
                  attempts={'intent': 2, '*': 1}, fact_cap=12, max_parallel=4)
    values.update(overrides)
    return ScoutConfig(**values)


def read_report(tmp_root):
    reports = list((tmp_root / 'scout-reports' / 'task-t').glob('*.json'))
    assert len(reports) == 1, reports
    return json.loads(reports[0].read_text(encoding='utf-8'))


class ScoutMergeTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='scouts-'))
        (self.root / 'src.txt').write_text(SOURCE_TEXT + '\n其他行\n', encoding='utf-8')
        self.state = scout_state()

    def merged(self, inner, config=None, closer=None):
        scout = ScoutFanoutRunner(inner, config=config or make_config(),
                                  report_dir=self.root / 'scout-reports', close_child=closer)
        return scout.run_once(make_request(self.root, self.state))

    def test_probes_run_in_parallel(self):
        barrier = threading.Barrier(2)
        outcome = self.merged(ScriptedInner(self.root, barrier=barrier))
        self.assertEqual(outcome.event_name, 'ANALYSIS_READY')

    def test_merge_is_deterministic_regardless_of_completion_order(self):
        first = self.merged(ScriptedInner(self.root))
        second = self.merged(ScriptedInner(self.root, delay=0.15))
        self.assertEqual(json.dumps(first.event_payload, sort_keys=True, ensure_ascii=False),
                         json.dumps(second.event_payload, sort_keys=True, ensure_ascii=False))

    def test_non_intent_claims_blocking_and_requirement_ids_are_sanitized(self):
        source = self.root / 'src.txt'
        payloads = {'code': probe_payload(self.root, 'code', facts=[
            {'observation': '还有其他行', 'source': str(source), 'quote': '其他行',
             'status': 'observed', 'local_key': 'fact-1'}])}
        outcome = self.merged(ScriptedInner(self.root, payloads=payloads))
        analysis = outcome.event_payload['analysis']
        self.assertEqual([claim.get('topic_key') for claim in analysis['claims']], ['goal.head'])
        code_asm = next(row for row in analysis['assumptions'] if row['local_key'] == 'code.asm-1')
        self.assertEqual(code_asm['blocking'], False)
        code_fact = next(row for row in analysis['facts'] if row['local_key'] == 'code.fact-1')
        self.assertEqual(code_fact['requirement_ids'], [])
        self.assertEqual(analysis['gaps'], [])
        self.assertEqual(outcome.event_payload['questions'], [])

    def test_evidence_failed_facts_are_dropped_not_fatal(self):
        source = self.root / 'src.txt'
        payloads = {'code': probe_payload(self.root, 'code', facts=[
            {'observation': '还有其他行', 'source': str(source), 'quote': '其他行',
             'status': 'observed', 'local_key': 'fact-1'},
            {'observation': '编造的引用', 'source': str(source), 'quote': '这句话不存在',
             'status': 'observed', 'local_key': 'fact-2'}])}
        outcome = self.merged(ScriptedInner(self.root, payloads=payloads))
        local_keys = [row['local_key'] for row in outcome.event_payload['analysis']['facts']]
        self.assertIn('code.fact-1', local_keys)
        self.assertNotIn('code.fact-2', local_keys)
        code_row = next(row for row in read_report(self.root)['facets'] if row['facet'] == 'code')
        self.assertEqual(code_row['status'], 'ok')
        self.assertEqual(code_row['facts_rejected'], 1)

    def test_gaps_and_failed_probes_become_unavailable_facts(self):
        payloads = {'code': probe_payload(self.root, 'code', gaps=['没有找到相关文档'])}
        failures = {'docs': [EffectOutcome(status='FAILED', event_name='FAIL',
                                           event_payload={'error_code': 'AGENT_TIMEOUT'},
                                           operation_id='child-docs')]}
        outcome = self.merged(ScriptedInner(self.root, payloads=payloads, failures=failures),
                              make_config(facets=('intent', 'code', 'docs')))
        unavailable = [row for row in outcome.event_payload['analysis']['facts']
                       if row.get('status') == 'unavailable']
        self.assertEqual(len(unavailable), 2)
        self.assertEqual(unavailable[0]['source'], 'scout:code')
        self.assertEqual(unavailable[0]['observation'], '没有找到相关文档')
        self.assertEqual(unavailable[0]['local_key'], 'code.gap-1')
        self.assertIn('探针 docs 未完成', unavailable[1]['observation'])
        self.assertEqual(unavailable[1]['local_key'], 'docs.gap-1')

    def test_intent_failure_rebinds_the_parent_request_id(self):
        failures = {'intent': [EffectOutcome(status='FAILED', event_name='FAIL',
                                             event_payload={'error_code': 'AGENT_TIMEOUT',
                                                            'request_id': 'req-1-scout-intent'},
                                             request_id='req-1-scout-intent',
                                             operation_id='child-intent')] * 2}
        inner = ScriptedInner(self.root, failures=failures)
        outcome = self.merged(inner)
        self.assertEqual(outcome.event_name, 'FAIL')
        self.assertEqual(outcome.event_payload['error_code'], 'AGENT_TIMEOUT')
        parent = 'task-t:e1:1:REQ_ANALYZE:attempt-1'
        self.assertEqual(outcome.request_id, parent)
        self.assertEqual(outcome.event_payload['request_id'], parent)
        self.assertEqual(inner.calls, 3)  # two intent attempts plus the code probe

    def test_intent_exception_still_fails_cleanly(self):
        inner = ScriptedInner(self.root,
                              failures={'intent': [RuntimeError('boom'), RuntimeError('boom')]})
        outcome = self.merged(inner)
        self.assertEqual(outcome.status, 'FAILED')

    def test_prerequisite_missing_is_passed_through(self):
        payloads = {'intent': probe_payload(self.root, 'intent', event='PREREQUISITE_MISSING')}
        outcome = self.merged(ScriptedInner(self.root, payloads=payloads))
        self.assertEqual(outcome.event_name, 'PREREQUISITE_MISSING')

    def test_local_keys_are_prefixed_and_duplicates_removed(self):
        source = self.root / 'src.txt'
        shared = {'observation': 'HEAD 常量固定为 8', 'source': str(source),
                  'quote': 'HEAD 常量固定为 8', 'status': 'observed', 'local_key': 'f-1'}
        distinct = dict(shared, quote='其他行', observation='还有其他行')
        payloads = {'intent': probe_payload(self.root, 'intent', facts=[shared],
                                            assumptions=[{'statement': '同一条假设', 'local_key': 'asm-x'}]),
                    'code': probe_payload(self.root, 'code', facts=[dict(shared), distinct],
                                          assumptions=[{'statement': '同一条假设', 'local_key': 'asm-x'}])}
        inner = ScriptedInner(self.root, payloads=payloads)
        outcome = self.merged(inner)
        analysis = outcome.event_payload['analysis']
        local_keys = [row['local_key'] for row in analysis['facts']]
        self.assertEqual(local_keys.count('f-1'), 1)       # code copy of the same quote is deduped
        self.assertEqual(local_keys.count('code.f-1'), 1)  # distinct quote survives under the prefixed key
        self.assertEqual([row['local_key'] for row in analysis['assumptions']], ['asm-x'])

    def test_merged_fact_total_is_capped(self):
        lines = '\n'.join(f'line-{number:03d}' for number in range(1, 61))
        (self.root / 'src.txt').write_text(lines + '\n', encoding='utf-8')
        source = self.root / 'src.txt'

        def many(facet, start):
            return [{'observation': f'line-{number:03d}', 'source': str(source),
                     'quote': f'line-{number:03d}', 'status': 'observed',
                     'local_key': f'{facet}-fact-{number:03d}'}
                    for number in range(start, start + 15)]

        payloads = {'intent': probe_payload(self.root, 'intent', facts=many('intent', 1)),
                    'code': probe_payload(self.root, 'code', facts=many('code', 16)),
                    'docs': probe_payload(self.root, 'docs', facts=many('docs', 31))}
        inner = ScriptedInner(self.root, payloads=payloads)
        outcome = self.merged(inner, make_config(facets=('intent', 'code', 'docs'), fact_cap=15))
        self.assertEqual(len(outcome.event_payload['analysis']['facts']), 40)

    def test_merged_result_passes_the_outer_validation_chain(self):
        os.environ.pop('REQUIREMENTS_ANALYZE_CACHE', None)
        scout = ScoutFanoutRunner(ScriptedInner(self.root), config=make_config(),
                                  report_dir=self.root / 'scout-reports')
        runner = RequirementsAgentRunner(scout, allowed_root=self.root)
        outcome = runner.run_once(make_request(self.root, self.state))
        self.assertEqual(outcome.event_name, 'ANALYSIS_READY')
        self.assertEqual(outcome.event_payload['task_id'], 'task-t')
        self.assertEqual(outcome.event_payload['request_id'], 'task-t:e1:1:REQ_ANALYZE:attempt-1')
        self.assertTrue(outcome.event_payload['reason'].startswith('探索汇总'))
        self.assertEqual(len(list((self.root / 'analyze-cache').glob('*.json'))), 1)
        self.assertEqual(outcome.event_payload['analysis']['claims'][0]['quote'], SOURCE_TEXT)

    def test_close_child_on_success_and_failure_paths(self):
        closes = []
        payloads = {'code': probe_payload(self.root, 'code', gaps=['x'])}
        failures = {'docs': [EffectOutcome(status='FAILED', event_name='FAIL',
                                           event_payload={}, operation_id='child-docs')]}
        inner = ScriptedInner(self.root, payloads=payloads, failures=failures)
        self.merged(inner, make_config(facets=('intent', 'code', 'docs')),
                    closer=lambda child, status: closes.append((child, status)))
        self.assertEqual(sorted(closes), [('child-code', 'done'), ('child-docs', 'cancelled'),
                                          ('child-intent', 'done')])

    def test_probe_requests_carry_facet_overrides(self):
        inner = ScriptedInner(self.root)
        self.merged(inner)
        by_facet = {request.payload['dispatch_context']['scout']['facet']: request
                    for request in inner.requests}
        for index, (facet, request) in enumerate(by_facet.items(), 1):
            self.assertEqual(request.payload['worker_id'], f'scout-{facet}-{index}')
            self.assertEqual(request.payload['fanout_parent_id'], 'issue-42')
            self.assertEqual(request.payload['timeout_seconds'], 600.0 if facet == 'intent' else 300.0)
            self.assertTrue(request.payload['request_id'].endswith(f'-scout-{facet}'))
            self.assertEqual(request.idempotency_key, f'req-1-scout-{facet}')
            self.assertIn('questions 必须为空数组', request.payload['prompt_ref'])
            self.assertEqual(request.payload['dispatch_context']['scout']['fact_cap'], 12)
            self.assertEqual(request.payload['dispatch_context']['scout']['allow_claims'], facet == 'intent')


class ScoutPassthroughTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='scouts-off-'))
        (self.root / 'src.txt').write_text(SOURCE_TEXT + '\n', encoding='utf-8')

    def test_env_kill_switch_passes_through(self):
        inner = ScriptedInner(self.root)
        scout = ScoutFanoutRunner(inner, config=ScoutConfig(enabled=False, facets=make_config().facets))
        request = make_request(self.root)
        outcome = scout.run_once(request)
        self.assertEqual(inner.calls, 1)
        self.assertNotIn('scout', request.payload.get('dispatch_context', {}))

    def test_existing_claims_pass_through(self):
        import requirements_test_support as support
        inner = ScriptedInner(self.root)
        scout = ScoutFanoutRunner(inner, config=make_config())
        scout.run_once(make_request(self.root, support.make_state()))
        self.assertEqual(inner.calls, 1)

    def test_non_analyze_states_pass_through(self):
        inner = ScriptedInner(self.root)
        scout = ScoutFanoutRunner(inner, config=make_config())
        scout.run_once(make_request(self.root, target='REQ_DRAFT'))
        self.assertEqual(inner.calls, 1)

    def test_config_parsing(self):
        env = {'REQUIREMENTS_SCOUT': '1',
               'REQUIREMENTS_SCOUT_FACETS': 'bogus, code, docs, intent, code',
               'REQUIREMENTS_SCOUT_TIMEOUTS': 'docs=abc',
               'REQUIREMENTS_SCOUT_ATTEMPTS': 'intent=zz',
               'REQUIREMENTS_SCOUT_FACT_CAP': 'x'}
        config = ScoutConfig.from_env(env)
        self.assertTrue(config.enabled)
        self.assertEqual(config.facets, ('intent', 'code', 'docs'))
        self.assertEqual(config.timeout('docs'), 2100.0)
        self.assertEqual(config.attempts('intent'), 2)
        self.assertEqual(config.fact_cap, 12)
        self.assertEqual(ScoutConfig.from_env({'REQUIREMENTS_SCOUT': '0'}).enabled, False)


class CacheSaltTests(unittest.TestCase):
    def test_salt_tracks_facets_and_fact_cap(self):
        def salt(config):
            return ScoutFanoutRunner(None, config=config).cache_salt()
        base = make_config()
        self.assertEqual(salt(base), salt(make_config()))
        self.assertNotEqual(salt(base), salt(make_config(facets=('intent', 'docs'))))
        self.assertNotEqual(salt(base), salt(make_config(fact_cap=7)))


if __name__ == '__main__':
    unittest.main()
