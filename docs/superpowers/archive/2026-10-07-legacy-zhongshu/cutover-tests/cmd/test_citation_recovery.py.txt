"""Live regression: path aliases and mixed Zhongshu failure classes."""
import copy
from dataclasses import replace
from pathlib import Path
import unittest

from orchestrator.adapters import _apply_quote_gate
from orchestrator.domain.policies.evidence_repair import evidence_scope_rejection
from orchestrator.domain.policies.evidence_repair import _workspace_file
from orchestrator.domain.context import context_from_dto, context_to_dto
from orchestrator.domain.states import ZhongshuSolverState, MenxiaGroupSolverState
from test_node_fail_brake import _context, _fail_event
from test_evidence_quote_gate import _request
from test_infrastructure_retry import _failure


def reused_fact():
    site = Path('cmd/orchestrator/domain/context.py').resolve()
    source = {'evidence_id': 'original', 'item_id': 'i2', 'requirement_id': 'r2',
        'source': 'cmd/orchestrator/domain/context.py:ParallelState.prompt_slicing',
        'quote': {'path': site.as_posix(), 'line_start': 1, 'line_end': 1,
                  'text': site.read_text(encoding='utf-8-sig').splitlines()[0]}, 'conclusion': 'source fact'}
    row = {**copy.deepcopy(source), 'evidence_id': 'local', 'source_evidence_id': 'original',
           'item_id': 'i1', 'requirement_id': 'r1'}
    demand = {'strict_item_scope': True, 'repair_quotes_locally': True,
        'active_findings': [{'finding_id': 'f1', 'item_id': 'i1', 'source_requirement_ids': ['r1']}],
        'available_evidence': [source]}
    payload = {'evidence_updates': [row], 'finding_responses': [{'finding_id': 'f1', 'evidence_ids': ['local']}]}
    return source, demand, payload


class CitationRecoveryTests(unittest.TestCase):
    def test_windows_identity_handles_slashes_case_and_contained_dot_segments(self):
        root = 'D:/workspace/src/nexus-agents'
        expected = _workspace_file('cmd/orchestrator/domain/context.py', root)
        self.assertEqual(_workspace_file('d:\\WORKSPACE\\src\\nexus-agents\\cmd\\orchestrator\\domain\\context.py', root), expected)
        self.assertEqual(_workspace_file('cmd/orchestrator/../orchestrator/domain/context.py', root), expected)
        for path in ('../other/context.py', 'E:/workspace/src/nexus-agents/cmd/context.py',
                     'D:cmd/context.py', '//server/share/context.py'):
            self.assertIsNone(_workspace_file(path, root))

    def test_same_file_relative_quote_path_is_accepted_and_canonicalized(self):
        source, demand, body = reused_fact()
        body['evidence_updates'][0]['quote']['path'] = './cmd/orchestrator/domain/context.py'
        before = copy.deepcopy(body)
        self.assertEqual(evidence_scope_rejection(body, demand), '')
        result = _apply_quote_gate(replace(_request(), context={'evidence_demand': demand}), body)
        self.assertEqual(result['evidence_updates'][0]['quote'], source['quote'])
        self.assertEqual(body, before)

    def test_source_file_locator_alias_keeps_symbol_suffix(self):
        source, demand, body = reused_fact()
        body['evidence_updates'][0]['source'] = source['quote']['path'] + ':ParallelState.prompt_slicing'
        self.assertEqual(evidence_scope_rejection(body, demand), '')

    def test_new_source_claim_requires_independent_evidence(self):
        source, demand, body = reused_fact()
        body['evidence_updates'][0]['source'] += ' + full repo search'
        body['evidence_updates'][0]['quote']['path'] = 'cmd/orchestrator/domain/context.py'
        error = evidence_scope_rejection(body, demand)
        self.assertIn('changed the original', error)
        self.assertIn(source['source'], error)
        self.assertIn("changed fields=['source']", error)

    def test_different_file_text_lines_and_workspace_escape_are_rejected(self):
        source, demand, body = reused_fact()
        for field, value in [('path', 'cmd/orchestrator/app.py'), ('path', '../domain/context.py'),
                             ('text', 'invented'), ('line_start', 2), ('line_end', 2)]:
            invalid = copy.deepcopy(body)
            invalid['evidence_updates'][0]['quote'][field] = value
            self.assertIn('changed the original', evidence_scope_rejection(invalid, demand))

    def test_mixed_reply_then_remote_failure_gets_external_retry(self):
        prior = _failure('AGENT_REPLY_CONTRACT_REJECTED')
        context = _context(node_fail_streak=1, node_fail_state='ZHONGSHU_SOLVER',
            last_failure=prior, reply_retry_count=1, external_retry_count=0)
        decision = ZhongshuSolverState().handle(context, _fail_event(error_code='REMOTE_RUN_FAILED'))
        self.assertEqual(decision.transition.action, 'RETRY')
        self.assertEqual(decision.update.recovery.node_fail_streak, 1)
        self.assertEqual(decision.update.recovery.external_retry_count, 1)
        self.assertIsNone(decision.update.recovery.reply_retry_count)

    def test_same_reply_class_still_trips_brake_even_when_codes_differ(self):
        context = _context(node_fail_streak=1, node_fail_state='ZHONGSHU_SOLVER',
            last_failure=_failure('AGENT_REPLY_CONTRACT_REJECTED'))
        decision = ZhongshuSolverState().handle(context, _fail_event(error_code='NODE_ITEM_REVISION_INVALID'))
        self.assertEqual(decision.transition.reason_code, 'NODE_FAIL_STREAK_EXHAUSTED')

    def test_restart_preserves_failure_class_and_budget(self):
        context = _context(node_fail_streak=1, node_fail_state='ZHONGSHU_SOLVER',
            last_failure=_failure('REMOTE_RUN_FAILED'), external_retry_count=1, reply_retry_count=2)
        restored = context_from_dto(context_to_dto(context))
        decision = ZhongshuSolverState().handle(restored, _fail_event(error_code='AGENT_REPLY_CONTRACT_REJECTED'))
        self.assertEqual(decision.transition.action, 'RETRY')
        self.assertEqual(decision.update.recovery.reply_retry_count, 3)
        self.assertEqual(decision.update.recovery.node_fail_streak, 1)
        self.assertIsNone(decision.update.recovery.external_retry_count)

    def test_class_switch_cannot_bypass_exhausted_reply_budget(self):
        context = _context(node_fail_streak=1, node_fail_state='ZHONGSHU_SOLVER',
            last_failure=_failure('REMOTE_RUN_FAILED'), reply_retry_count=3, max_reply_retries=3)
        decision = ZhongshuSolverState().handle(context, _fail_event(error_code='AGENT_REPLY_CONTRACT_REJECTED'))
        self.assertEqual(decision.transition.action, 'HUMAN_GATE')

    def test_menxia_keeps_existing_brake(self):
        context = _context(node_fail_streak=1, node_fail_state='MENXIA_GROUP_SOLVER',
            last_failure=_failure('AGENT_REPLY_CONTRACT_REJECTED'))
        context = replace(context, progression=replace(context.progression, state='MENXIA_GROUP_SOLVER'))
        decision = MenxiaGroupSolverState().handle(context, _fail_event(error_code='REMOTE_RUN_FAILED'))
        self.assertEqual(decision.transition.reason_code, 'NODE_FAIL_STREAK_EXHAUSTED')


if __name__ == '__main__':
    unittest.main()
