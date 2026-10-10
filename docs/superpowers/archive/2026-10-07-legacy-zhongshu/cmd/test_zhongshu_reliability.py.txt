import copy
from dataclasses import replace
from pathlib import Path
import unittest
import tempfile

from orchestrator.domain.findings import Finding
from orchestrator.domain.context import ReviewTaskItem, apply_review_update, context_from_dto, context_to_dto
from orchestrator.domain.states import ZhongshuCriticState, ZhongshuAnalystState, _analyst_evidence_demand
from orchestrator.domain.policies.analyst_partition import partition_active_findings
from orchestrator.runtime.agent_effects import AgentNodeJoiner
from orchestrator.runtime.nodes import WorkerResult, _with_reply_feedback
from test_evidence_direct_routing import _context, _completed_event
from test_worker_reply_reask import _binding, _context as _node_context
from test_evidence_direct_routing import _EvidenceDetourMultica, _e2e_context
from orchestrator.app import OrchestratorApp


class _ResolvedGapMultica(_EvidenceDetourMultica):
    def _reply(self, request, payload):
        if (request.target_state == 'ZHONGSHU_CRITIC'
                and request.context.get('zhongshu_dispatch_mode') == 'group_review'
                and self.critic_review_waves == 2):
            old = request.context['active_findings'][0]
            payload = {**payload, 'action': 'REQUEST_ANALYST_EVIDENCE',
                'findings': [{**old, 'finding_id': 'new-gap', 'claim': 'verify new scoped source coverage'}],
                'finding_responses': [{'finding_id': old['finding_id'], 'response': 'RESOLVED',
                                      'note': 'verified delivered source evidence'}]}
        super()._reply(request, payload)


def finding(fid, item='i1', group='g1', severity='P1'):
    return Finding(finding_id=fid, item_id=item, group_id=group, severity=severity,
                   claim=f'missing proof {fid}', category='evidence_gap')


class ZhongshuReliabilityTests(unittest.TestCase):
    def test_real_app_only_dispatches_new_gap_after_critic_closes_prior_gap(self):
        with tempfile.TemporaryDirectory() as directory:
            site = Path(directory) / 'source.py'
            site.write_text('ENGINE = "postgres"\n', encoding='utf-8')
            adapter = _ResolvedGapMultica(site.as_posix())
            app = OrchestratorApp(_e2e_context(), root=directory, multica=adapter,
                                  poll_interval=0, timeout_seconds=15)
            self.assertTrue(app.run())
            context = app.repository.load('task-e2e').context
            self.assertEqual(context.progression.state, 'DONE')
            self.assertEqual(adapter.critic_review_waves, 3)
            self.assertEqual(adapter.evidence_waves, 2)
            demands = [request.context['evidence_demand'] for request in adapter.dispatched
                       if request.context.get('evidence_demand')]
            self.assertEqual([[row['finding_id'] for row in demand['active_findings']] for demand in demands],
                             [['finding-evidence-db'], ['new-gap']])

    def test_verified_oversized_quote_splits_without_losing_text_or_response_links(self):
        from orchestrator.quote_gate import repair_verified_quote_spans, find_quote_issues
        with tempfile.TemporaryDirectory() as directory:
            site = Path(directory) / 'source.py'
            source = '\n'.join(f'LINE_{i} = {i}' for i in range(1, 66))
            site.write_text(source, encoding='utf-8')
            body = {'evidence_updates': [{'evidence_id': 'ev1', 'source': f'{site.as_posix()}:1',
                'quote': {'path': site.as_posix(), 'line_start': 1, 'line_end': 65, 'text': source},
                'item_id': 'i1', 'requirement_id': 'r1', 'conclusion': 'collective source fact'}],
                'finding_responses': [{'finding_id': 'f1', 'evidence_ids': ['ev1']}]}
            before = copy.deepcopy(body)
            repaired = repair_verified_quote_spans(body)
            self.assertEqual(body, before)
            self.assertEqual(len(repaired['evidence_updates']), 3)
            self.assertEqual('\n'.join(row['quote']['text'] for row in repaired['evidence_updates']), source)
            self.assertEqual(repaired['finding_responses'][0]['evidence_ids'],
                             [row['evidence_id'] for row in repaired['evidence_updates']])
            self.assertEqual(find_quote_issues(repaired), [])
            self.assertEqual(repair_verified_quote_spans(repaired), repaired)

    def test_oversized_fabricated_quote_is_never_locally_repaired(self):
        from orchestrator.quote_gate import repair_verified_quote_spans
        body = {'evidence_updates': [{'evidence_id': 'fake', 'source': 'cmd/orchestrator/quote_gate.py:1',
            'quote': {'path': 'cmd/orchestrator/quote_gate.py', 'line_start': 1, 'text': '\n'.join(['invented'] * 60)}}]}
        self.assertEqual(repair_verified_quote_spans(body), body)

    def test_explicit_group_resolution_removes_old_findings_from_next_dispatch(self):
        context = _context('ZHONGSHU_CRITIC')
        old = (finding('old1'), finding('old2', 'i2'))
        context = replace(context, review=replace(context.review, findings=old,
            task_items=(ReviewTaskItem('i1', 'g1', source_requirement_ids=('r1',)),
                        ReviewTaskItem('i2', 'g1', source_requirement_ids=('r2',)))))
        payload = {'action': 'REQUEST_ANALYST_EVIDENCE',
                   'findings': [{'finding_id': 'new', 'item_id': 'i1', 'group_id': 'g1',
                                 'severity': 'P1', 'claim': 'new missing source', 'category': 'evidence_gap'}],
                   'group_reviews': [{'group_id': 'g1', 'member_ids': ['i1', 'i2'],
                                      'action': 'REQUEST_ANALYST_EVIDENCE', 'finding_responses': [
                                          {'finding_id': fid, 'response': 'RESOLVED', 'note': 'verified new scoped evidence'}
                                          for fid in ('old1', 'old2')]}]}
        update = ZhongshuCriticState()._payload_update(context, _completed_event(payload)).review
        review = apply_review_update(context.review, update)
        self.assertEqual([f.finding_id for f in review.findings if f.active], ['new'])
        restored = context_from_dto(context_to_dto(replace(context, review=review)))
        self.assertEqual(partition_active_findings(restored.review, 3), {1: (restored.review.findings[-1],)})
        self.assertEqual(_analyst_evidence_demand(restored)['active_findings'][0]['finding_id'], 'new')
        replayed = apply_review_update(review, update)
        self.assertEqual(replayed.findings, review.findings)

    def test_analyst_cannot_close_critic_finding_by_imitating_group_response(self):
        context = _context()
        context = replace(context, review=replace(context.review, findings=(finding('f1'),)))
        payload = {'action': 'EVIDENCE_PACKET_READY_FOR_CRITIC', 'group_reviews': [
            {'group_id': 'g1', 'member_ids': ['i1'], 'action': 'REQUEST_ANALYST_EVIDENCE',
             'finding_responses': [{'finding_id': 'f1', 'response': 'RESOLVED', 'note': 'self claimed'}]}]}
        update = ZhongshuAnalystState()._payload_update(context, _completed_event(payload)).review
        self.assertTrue(apply_review_update(context.review, update).findings[0].active)

    def test_retry_feedback_keeps_all_reported_quote_defects(self):
        reason = 'first defect ' + 'x' * 650 + ' second defect w02-ev-005 QUOTE_MISMATCH'
        retried = _with_reply_feedback(_binding(), _node_context(), reason, 'draft')
        self.assertIn('second defect w02-ev-005', retried.prompt_ref)

    def test_reusable_fact_preserves_source_and_still_requires_local_scope(self):
        from orchestrator.domain.policies.evidence_repair import available_evidence, evidence_scope_rejection
        context = _context()
        source = {'evidence_id': 'peer', 'item_id': 'i2', 'requirement_id': 'r2',
                  'source': 'some.py:1', 'quote': {'text': 'VALUE = 1'}, 'conclusion': 'source fact'}
        review = replace(context.review, requirements=({'requirement_id': 'r1'}, {'requirement_id': 'r2'}),
                         evidence_packet={'phase': 'ZHONGSHU', 'evidence_updates': [source]})
        pool = available_evidence(review)
        demand = {'strict_item_scope': True, 'active_findings': [{'finding_id': 'f1', 'item_id': 'i1',
                  'source_requirement_ids': ['r1']}], 'available_evidence': pool}
        row = {**copy.deepcopy(source), 'evidence_id': 'local', 'source_evidence_id': 'peer', 'item_id': 'i1', 'requirement_id': 'r1'}
        payload = {'evidence_updates': [row], 'finding_responses': [{'finding_id': 'f1', 'evidence_ids': ['local']}]}
        self.assertEqual(evidence_scope_rejection(payload, demand), '')
        row['quote']['text'] = 'invented new fact'
        self.assertIn('changed the original', evidence_scope_rejection(payload, demand))
        self.assertEqual(available_evidence(replace(review, evidence_packet={**review.evidence_packet, 'phase': 'MENXIA'})), [])
        self.assertEqual(available_evidence(replace(review, evidence_packet={'phase': 'ZHONGSHU',
            'evidence_updates': [{**source, 'conclusion': '[UNVERIFIED-QUOTE] bad'}]})), [])

    def test_reuse_pool_is_bounded_and_ambiguous_ids_are_excluded(self):
        from orchestrator.domain.policies.evidence_repair import available_evidence
        review = replace(_context().review, requirements=({'requirement_id': 'r1'},), evidence_packet={
            'phase': 'ZHONGSHU', 'evidence_updates': [
                {'evidence_id': 'same', 'requirement_id': 'r1', 'quote': {'text': 'one'}, 'source': 'one.py:1'},
                {'evidence_id': 'same', 'requirement_id': 'r1', 'quote': {'text': 'two'}, 'source': 'two.py:1'}]})
        self.assertEqual(available_evidence(review), [])
        self.assertEqual(available_evidence(review, max_chars=1), [])

    def test_group_critic_cannot_close_a_different_groups_finding(self):
        joiner = AgentNodeJoiner('n', task_id='t', state='ZHONGSHU_CRITIC', sequence=3,
            revision_id='r', plan_hash='h', dispatch_mode='group_review', binding_contexts={
                'w1': {'group_id': 'g1', 'member_ids': ['i1'], 'revision_id': 'r', 'plan_hash': 'h',
                       'active_findings': [{'finding_id': 'own', 'item_id': 'i1', 'severity': 'P1'}]}})
        result = joiner.join((WorkerResult('w1', 'SUCCEEDED', None, result_payload={
            'action': 'APPROVE_GROUP', 'group_id': 'g1', 'findings': [],
            'finding_responses': [{'finding_id': 'foreign', 'response': 'RESOLVED', 'note': 'unowned'}]}),))
        self.assertEqual(result.status, 'FAILED')
        self.assertEqual(result.failure.worker_rejections, (('w1', 'GROUP_RESPONSE_UNKNOWN_FINDING:foreign'),))

    def test_group_critic_cannot_silently_drop_p0(self):
        joiner = AgentNodeJoiner('n', task_id='t', state='ZHONGSHU_CRITIC', sequence=3,
            revision_id='r', plan_hash='h', dispatch_mode='group_review', binding_contexts={
                'w1': {'group_id': 'g1', 'member_ids': ['i1'], 'revision_id': 'r', 'plan_hash': 'h',
                       'active_findings': [{'finding_id': 'p0', 'item_id': 'i1', 'severity': 'P0'}]}})
        result = joiner.join((WorkerResult('w1', 'SUCCEEDED', None,
            result_payload={'action': 'APPROVE_GROUP', 'group_id': 'g1', 'findings': []}),))
        self.assertEqual(result.status, 'FAILED')

    def test_approval_with_new_p1_does_not_silently_resolve_it(self):
        from orchestrator.domain.policies.critic_dispositions import group_disposition_error, apply_group_dispositions
        blocker = finding('new')
        self.assertEqual(group_disposition_error([], [{'finding_id': 'new', 'severity': 'P1', 'decision': 'OPEN'}],
                                                 [], 'APPROVE_GROUP'), 'GROUP_APPROVAL_WITH_OPEN_BLOCKER')
        self.assertTrue(apply_group_dispositions((blocker,), [{'group_id': 'g1', 'member_ids': ['i1'],
            'action': 'APPROVE_GROUP'}])[0].active)

    def test_target_name_in_prose_is_not_evidence_coverage(self):
        from orchestrator.adapters import _evidence_demand_rejection
        from test_evidence_quote_gate import _request
        target = 'cmd/orchestrator/app.py'
        demand = {'strict_item_scope': True, 'active_findings': [{'finding_id': 'f1', 'item_id': 'i1',
                  'source_requirement_ids': ['r1']}], 'evidence_targets': [{'path': target}]}
        payload = {'evidence_updates': [{'evidence_id': 'ev', 'item_id': 'i1', 'requirement_id': 'r1',
                   'source': 'elsewhere.py:1', 'conclusion': f'please inspect {target}'}], 'finding_responses': [{'finding_id': 'f1', 'evidence_ids': ['ev'],
                   'answer': f'please inspect {target}'}]}
        self.assertIn('evidence targets unmet', _evidence_demand_rejection(replace(_request(), context={'evidence_demand': demand}), payload))

    def test_answered_quote_parts_outrank_old_history_in_bounded_critic_slice(self):
        from orchestrator.domain.states import _item_evidence_context
        from orchestrator.domain.policies.evidence_repair import available_evidence
        parts = [{'evidence_id': f'part-{i}', 'item_id': 'i1', 'requirement_id': 'r1',
                  'quote_group_id': 'base', 'quote_part_count': 3, 'quote_part_index': i,
                  'quote': {'text': f'LINE = {i}'}, 'source': 'some.py:1'} for i in range(3)]
        history = [{'evidence_id': f'old-{i}', 'item_id': 'i1', 'requirement_id': 'r1',
                    'quote': {'text': 'OLD = 1'}, 'source': 'some.py:1'} for i in range(16)]
        review = replace(_context().review, requirements=({'requirement_id': 'r1'},),
            findings=(finding('active'),), evidence_packet={'phase': 'ZHONGSHU',
                'evidence_updates': history + parts, 'finding_responses': [{'finding_id': 'active',
                    'evidence_ids': [row['evidence_id'] for row in parts]}]})
        slice_ = _item_evidence_context(review, ReviewTaskItem('i1', 'g1', source_requirement_ids=('r1',)), 1)
        self.assertEqual([row['evidence_id'] for row in slice_['records'][:3]], ['part-0', 'part-1', 'part-2'])
        pool = available_evidence(review, limit=3, evidence_ids=['part-0', 'part-1', 'part-2'])
        self.assertEqual([row['evidence_id'] for row in pool], ['part-0', 'part-1', 'part-2'])

    def test_conflicting_source_is_excluded_from_reuse_pool(self):
        from orchestrator.domain.policies.evidence_repair import available_evidence
        review = replace(_context().review, requirements=({'requirement_id': 'r1'},), evidence_packet={
            'phase': 'ZHONGSHU', 'evidence_updates': [{'evidence_id': 'conflict', 'requirement_id': 'r1',
                'quote': {'text': 'OLD = 1'}, 'source': 'some.py:1', 'observed_variants': [{'quote': {'text': 'NEW = 2'}}]}]})
        self.assertEqual(available_evidence(review), [])

    def test_duplicate_closures_and_changed_finding_owner_are_rejected(self):
        from orchestrator.domain.policies.critic_dispositions import group_disposition_error
        prior = [{'finding_id': 'f1', 'item_id': 'i1', 'severity': 'P0'}]
        self.assertIn('OWNER_CHANGED', group_disposition_error(prior, [{**prior[0], 'item_id': 'i2'}], []))
        response = {'finding_id': 'f1', 'response': 'RESOLVED', 'note': 'verified'}
        self.assertIn('DUPLICATE_ID', group_disposition_error(prior, [], [response, response]))


if __name__ == '__main__':
    unittest.main()
