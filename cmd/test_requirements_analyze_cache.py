import json
import os
import tempfile
import unittest
from pathlib import Path

from orchestrator.runtime.requirements_agent import RequirementsAgentRunner
from orchestrator.runtime.effects import EffectOutcome
from orchestrator.domain.decisions import EffectRequest
from requirements_test_support import make_state as build_state

SCHEMA_HASH = '6ae097008fef6b3292f412bf2f54fb94bef0307983c5df00c11562a9248241f9'


class FakeInner:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def run_once(self, request):
        self.calls += 1
        return EffectOutcome(status='SUCCEEDED', event_name=str(self.payload.get('action') or ''),
                             event_payload=dict(self.payload))


def make_request(tmp_root, state, **payload_overrides):
    payload = {'target_state': 'REQ_ANALYZE', 'request_id': 'task-t:e1:1:REQ_ANALYZE:attempt-1',
               'role': 'review-analyst', 'task_id': 'task-t',
               'dispatch_context': {'project_root': str(tmp_root)},
               'validation_state': state.to_dict()}
    payload.update(payload_overrides)
    return EffectRequest('agent:x', 'agent_dispatch', 'task-t', 'req-1', payload=payload)


def make_state():
    return build_state()


def make_payload(tmp_root, source_name='src.txt', quote=None, task_id='task-old'):
    source = tmp_root / source_name
    quote = quote if quote is not None else 'HEAD 常量固定为 8'
    return {'action': 'ANALYSIS_READY', 'mode': 'REQUIREMENTS_DOCUMENT', 'phase': 'ZHONGSHU',
            'role': 'review-analyst', 'state': 'REQ_ANALYZE',
            'contract_id': 'nexus.requirements.req_analyze.v1',
            'structured_output_protocol': 'nexus-agent-result-file-v2',
            'structured_output_schema_hash': SCHEMA_HASH,
            'task_id': task_id, 'request_id': 'task-old:e1:1:REQ_ANALYZE:attempt-1',
            'reason': '调查完成', 'questions': [],
            'analysis': {
                'facts': [{'fact_id': 'fact-000001', 'observation': quote, 'source': str(source),
                           'quote': quote, 'status': 'observed'}],
                'claims': [{'requirement_id': 'req-900001', 'topic_key': 'behavior.head-constant',
                            'statement': 'HEAD 常量固定为 8，该行为约束以源码观察为依据。',
                            'kind': 'constraint', 'origin': 'derived',
                            'source_ids': [], 'decision_ids': []}],
                'assumptions': [], 'gaps': []}}


class AnalyzeCacheTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='analyze-cache-'))
        (self.root / 'src.txt').write_text('HEAD 常量固定为 8\n其他行\n', encoding='utf-8')
        os.environ.pop('REQUIREMENTS_ANALYZE_CACHE', None)
        self.state = make_state()

    def tearDown(self):
        os.environ.pop('REQUIREMENTS_ANALYZE_CACHE', None)

    def runner(self, payload):
        inner = FakeInner(payload)
        return RequirementsAgentRunner(inner, allowed_root=self.root), inner

    def cache_files(self):
        return list((self.root / 'analyze-cache').glob('*.json'))

    def test_first_call_dispatches_and_stores_cache(self):
        runner, inner = self.runner(make_payload(self.root))
        outcome = runner.run_once(make_request(self.root, self.state))
        self.assertEqual(outcome.event_name, 'ANALYSIS_READY')
        self.assertEqual(inner.calls, 1)
        self.assertEqual(len(self.cache_files()), 1)

    def test_second_identical_request_hits_cache_without_dispatch(self):
        runner, inner = self.runner(make_payload(self.root))
        runner.run_once(make_request(self.root, self.state))
        outcome = runner.run_once(make_request(self.root, self.state))
        self.assertEqual(inner.calls, 1)
        self.assertEqual(outcome.event_name, 'ANALYSIS_READY')
        self.assertEqual(outcome.event_payload['task_id'], 'task-t')
        self.assertEqual(outcome.event_payload['request_id'], 'task-t:e1:1:REQ_ANALYZE:attempt-1')
        self.assertNotIn('result_artifact_id', outcome.event_payload)
        self.assertNotIn('task-old', json.dumps(outcome.event_payload))

    def test_source_drift_forces_cache_miss(self):
        runner, inner = self.runner(make_payload(self.root))
        runner.run_once(make_request(self.root, self.state))
        inner.calls = 0
        (self.root / 'src.txt').write_text('HEAD 常量固定为 9\n', encoding='utf-8')
        # A fresh investigation produces a payload matching the new content.
        inner.payload = make_payload(self.root, quote='HEAD 常量固定为 9')
        outcome = runner.run_once(make_request(self.root, self.state))
        self.assertEqual(inner.calls, 1)
        self.assertEqual(outcome.event_name, 'ANALYSIS_READY')

    def test_different_request_state_misses(self):
        from dataclasses import replace as dc_replace
        runner, inner = self.runner(make_payload(self.root))
        runner.run_once(make_request(self.root, self.state))
        inner.calls = 0
        state = self.state
        other = dc_replace(state, consensus=dc_replace(state.consensus, parent_digest='c' * 64))
        runner.run_once(make_request(self.root, other))
        self.assertEqual(inner.calls, 1)

    def test_invalid_cached_result_is_evicted_not_looped(self):
        runner, inner = self.runner(make_payload(self.root))
        runner.run_once(make_request(self.root, self.state))
        self.assertEqual(len(self.cache_files()), 1)
        # Poison the stored payload with a semantically invalid claim (no
        # topic_key statement): freshness passes (no fingerprinted facts are
        # checked here) but result validation must reject and evict it.
        path = self.cache_files()[0]
        entry = json.loads(path.read_text(encoding='utf-8-sig'))
        entry['payload']['analysis']['claims'] = [
            {'requirement_id': 'req-000001', 'kind': 'constraint', 'origin': 'derived',
             'source_ids': ['src-000001'], 'decision_ids': []}]
        path.write_text(json.dumps(entry, ensure_ascii=False), encoding='utf-8')
        outcome = runner.run_once(make_request(self.root, self.state))
        self.assertEqual(outcome.event_name, 'FAIL')
        self.assertEqual(self.cache_files(), [])
        # The retry dispatches fresh and repopulates a valid cache.
        outcome = runner.run_once(make_request(self.root, self.state))
        self.assertEqual(outcome.event_name, 'ANALYSIS_READY')
        self.assertEqual(inner.calls, 2)
        self.assertEqual(len(self.cache_files()), 1)

    def test_env_kill_switch_disables_cache(self):
        os.environ['REQUIREMENTS_ANALYZE_CACHE'] = '0'
        runner, inner = self.runner(make_payload(self.root))
        runner.run_once(make_request(self.root, self.state))
        runner.run_once(make_request(self.root, self.state))
        self.assertEqual(inner.calls, 2)
        self.assertFalse((self.root / 'analyze-cache').exists())


if __name__ == '__main__':
    unittest.main()
