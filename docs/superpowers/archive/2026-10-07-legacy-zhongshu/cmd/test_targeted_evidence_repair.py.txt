"""Regression coverage for task-20261006-3a0200's supplement loop."""
import copy
from dataclasses import replace
from pathlib import Path
import unittest
import tempfile
from unittest.mock import patch

from orchestrator.adapters import _apply_quote_gate, _evidence_demand_rejection
from orchestrator.agent_result_file import AgentResultFileError
from orchestrator.domain.context import ReviewUpdate, ReviewTaskItem, apply_review_update, context_to_dto, context_from_dto
from orchestrator.domain.findings import Finding
from orchestrator.domain.policies.analyst_partition import partition_active_findings
from orchestrator.domain.policies.evidence_repair import evidence_scope_rejection
from orchestrator.domain.policies.parallel import aggregate_zhongshu_workers
from orchestrator.domain.states import ZhongshuCriticState, ZhongshuAnalystState, _analyst_evidence_demand
from orchestrator.runtime.agent_effects import AgentNodeJoiner
from orchestrator.runtime.nodes import WorkerBinding, WorkerResult, NodeContext, _run_worker
from orchestrator.domain.errors import CONTRACT_REJECTED_EVENT
from test_evidence_direct_routing import _context, _worker, _completed_event
from test_evidence_quote_gate import _request
from test_evidence_direct_routing import _EvidenceDetourMultica, _e2e_context, OrchestratorApp


DEMAND = {'strict_item_scope': True, 'repair_quotes_locally': True, 'active_findings': [
    {'finding_id': 'f1', 'item_id': 'i3', 'source_requirement_ids': ['r3'], 'evidence_targets': []}],
    'evidence_targets': []}


def supplement():
    return {'action': 'EVIDENCE_PACKET_READY', 'evidence_updates': [
        {'evidence_id': 'e1', 'item_id': 'i3', 'requirement_id': 'r3', 'source': 'runtime probe',
         'conclusion': 'measured', 'decision_relevance': 'coverage'}],
        'finding_responses': [{'finding_id': 'f1', 'answer': 'measured', 'evidence_ids': ['e1']}]}


class TargetedEvidenceRepairTests(unittest.TestCase):
    def test_mixed_repair_executes_solver_with_changes_and_evidence_before_critic(self):
        class MixedAdapter(_EvidenceDetourMultica):
            def _reply(self, request, payload):
                if payload.get('action') == 'REQUEST_ANALYST_EVIDENCE':
                    payload = {**payload, 'required_change': ['Align database acceptance wording in section 8']}
                return super()._reply(request, payload)
        with tempfile.TemporaryDirectory() as directory:
            site = Path(directory) / 'evidence_site.py'
            site.write_text('ENGINE = "postgres"\n', encoding='utf-8')
            adapter = MixedAdapter(site.as_posix())
            app = OrchestratorApp(_e2e_context(), root=directory, multica=adapter, poll_interval=0, timeout_seconds=15)
            self.assertTrue(app.run())
            dispatched = adapter.dispatched
            evidence_index = next(i for i, request in enumerate(dispatched) if request.context.get('evidence_demand'))
            revision_index = next(i for i, request in enumerate(dispatched) if i > evidence_index
                                  and request.context.get('zhongshu_dispatch_mode') == 'group_revise')
            critic_index = next(i for i, request in enumerate(dispatched) if i > evidence_index
                                and request.target_state == 'ZHONGSHU_CRITIC')
            self.assertLess(revision_index, critic_index)
            dispatch = dispatched[revision_index].context
            self.assertEqual(dispatch['evidence_required_changes'][0]['required_change'],
                             ['Align database acceptance wording in section 8'])
            self.assertTrue(dispatch['item_evidence_by_item']['item-000001'])
            self.assertIn('finding-evidence-db', [row['finding_id'] for row in dispatch['finding_responses']])
            self.assertEqual(app.repository.load('task-e2e').context.progression.state, 'DONE')

    def test_missing_item_and_wrong_requirement_rejected_at_transport(self):
        request = replace(_request(), context={'evidence_demand': DEMAND})
        for field, value in [('item_id', None), ('item_id', 'i1'), ('requirement_id', 'r1')]:
            body = supplement()
            body['evidence_updates'][0][field] = value
            self.assertIn('evidence scope:', _evidence_demand_rejection(request, body))
        self.assertEqual(_evidence_demand_rejection(request, supplement()), '')

    def test_dangling_response_and_extra_wrong_scope_rejected(self):
        body = supplement()
        body['finding_responses'][0]['evidence_ids'] = ['absent']
        self.assertIn('absent', evidence_scope_rejection(body, DEMAND))
        body = supplement()
        body['evidence_updates'].append({'evidence_id': 'extra', 'item_id': 'i2', 'requirement_id': 'r2'})
        self.assertIn('unsolicited scope', evidence_scope_rejection(body, DEMAND))

    def test_runtime_unknown_is_explicit_and_not_a_false_close(self):
        body = supplement()
        body['finding_responses'][0].update(evidence_ids=[], suggested_disposition='NEEDS_RUNTIME_DATA')
        self.assertTrue(evidence_scope_rejection(body, DEMAND))
        body['unknowns'] = ['runtime cache performance needs measurement']
        self.assertEqual(evidence_scope_rejection(body, DEMAND), '')

    def test_unverified_marker_cannot_bypass_targeted_quote_repair(self):
        request = replace(_request(), context={'evidence_demand': DEMAND})
        body = supplement()
        body['evidence_updates'][0]['conclusion'] = '[UNVERIFIED-QUOTE] old rejected quote'
        with self.assertRaisesRegex(AgentResultFileError, 'UNVERIFIED-QUOTE'):
            _apply_quote_gate(request, body)

    def test_dispatch_includes_item_canonical_requirement_scope(self):
        context = _context()
        finding = Finding(finding_id='f1', item_id='i3', severity='P1', claim='missing proof')
        context = replace(context, review=replace(context.review, findings=(finding,),
            task_items=(ReviewTaskItem('i3', 'g2', source_requirement_ids=('r3',)),)))
        demand = _analyst_evidence_demand(context)
        self.assertTrue(demand['strict_item_scope'])
        self.assertEqual(demand['active_findings'][0]['source_requirement_ids'], ['r3'])

    def test_document_only_finding_does_not_create_extra_analyst_worker(self):
        context = _context()
        findings = (Finding(finding_id='f1', item_id='i3', severity='P1', category='evidence_gap', claim='proof'),
                    Finding(finding_id='f2', item_id='i3', severity='P2', category='acceptance_gap', claim='wording'))
        review = replace(context.review, findings=findings)
        self.assertEqual(partition_active_findings(review, 3), {1: (findings[0],)})

    def test_mixed_request_survives_restart_and_consumption_clears_flag(self):
        context = _context('ZHONGSHU_CRITIC')
        event = _completed_event({'action': 'REQUEST_ANALYST_EVIDENCE', 'group_reviews': [
            {'action': 'REQUEST_ANALYST_EVIDENCE', 'required_change': ['align sections 3,4,8']}]})
        update = ZhongshuCriticState()._payload_update(context, event).review
        review = apply_review_update(context.review, update)
        self.assertTrue(review.evidence_requires_solver_revision)
        restored = context_from_dto(context_to_dto(replace(context, review=review)))
        self.assertTrue(restored.review.evidence_requires_solver_revision)
        self.assertEqual(restored.review.evidence_required_changes[0]['required_change'], ['align sections 3,4,8'])
        self.assertFalse(apply_review_update(review, ReviewUpdate(clear_evidence_requester=True)).evidence_requires_solver_revision)
        self.assertEqual(apply_review_update(review, ReviewUpdate(clear_evidence_requester=True)).evidence_required_changes,
                         review.evidence_required_changes)

    def test_mixed_evidence_routes_solver_without_overwriting_document(self):
        aggregate = aggregate_zhongshu_workers('task-1', 'ZHONGSHU_ANALYST', 'R1', 'hash', [_worker()],
            evidence_requester='ZHONGSHU_CRITIC', evidence_requires_solver_revision=True)
        self.assertEqual(aggregate['action'], 'READY_FOR_SOLVER')
        self.assertNotIn('plan', aggregate)
        context = _context(evidence_requester='ZHONGSHU_CRITIC')
        context = replace(context, review=replace(context.review, evidence_requires_solver_revision=True))
        decision = ZhongshuAnalystState().handle(context, _completed_event(aggregate))
        self.assertEqual(decision.transition.action, 'READY_FOR_SOLVER')
        self.assertEqual(apply_review_update(context.review, decision.update.review).plan, context.review.plan)

    def test_pure_evidence_keeps_direct_critic_path(self):
        aggregate = aggregate_zhongshu_workers('task-1', 'ZHONGSHU_ANALYST', 'R1', 'hash', [_worker()],
            evidence_requester='ZHONGSHU_CRITIC')
        self.assertEqual(aggregate['action'], 'EVIDENCE_PACKET_READY_FOR_CRITIC')

    def test_group_join_preserves_required_document_changes(self):
        joiner = AgentNodeJoiner('node', task_id='task', state='ZHONGSHU_CRITIC', sequence=1,
            revision_id='r', plan_hash='h', dispatch_mode='group_review',
            binding_contexts={'w1': {'group_id': 'g2', 'member_ids': ['i3'], 'revision_id': 'r', 'plan_hash': 'h'}})
        result = joiner.join((WorkerResult('w1', 'SUCCEEDED', None, result_payload={
            'action': 'REQUEST_ANALYST_EVIDENCE', 'group_id': 'g2', 'findings': [],
            'required_change': ['align sections 3,4,8'], 'missing_evidence': ['quote cache implementation']}),))
        self.assertEqual(result.status, 'SUCCEEDED')
        self.assertEqual(result.aggregate['group_reviews'][0]['required_change'], ['align sections 3,4,8'])

    def test_invalid_quote_is_repaired_in_same_worker_retry(self):
        request = replace(_request(), context={'evidence_demand': DEMAND})
        body = supplement()
        site = Path('cmd/orchestrator/quote_gate.py')
        body['evidence_updates'][0].update(source=f'{site.as_posix()}:1',
            quote={'path': site.as_posix(), 'line_start': 1, 'line_end': 1, 'text': 'invented behavior'})
        repaired = copy.deepcopy(body)
        repaired['evidence_updates'][0]['quote']['text'] = site.read_text(encoding='utf-8').splitlines()[0]
        class Runner:
            bindings = []
            def run(self, binding, context):
                self.bindings.append(binding)
                candidate = body if len(self.bindings) == 1 else repaired
                try:
                    payload = _apply_quote_gate(request, candidate)
                except AgentResultFileError as error:
                    payload = {'action': CONTRACT_REJECTED_EVENT, 'contract_rejection': str(error), 'raw_reply': candidate}
                return WorkerResult(binding.worker_id, 'SUCCEEDED', None, result_payload=payload)
        runner = Runner()
        binding = WorkerBinding('w1', 'a1', 't1', 'req1', 'review-analyst', 'ZHONGSHU', 'Supplement only')
        context = NodeContext('t1', 'n1', 'r1', state='ZHONGSHU_ANALYST')
        with patch('orchestrator.runtime.nodes._worker_retry_sleep'):
            result = _run_worker(runner, binding, context)
        self.assertEqual(result.result_payload, repaired)
        self.assertEqual(len(runner.bindings), 2)
        self.assertIn('QUOTE_MISMATCH', runner.bindings[1].prompt_ref)


if __name__ == '__main__':
    unittest.main()
