"""Group protocol alignment, bounded repair and real scripted application."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from orchestrator.app import OrchestratorApp
from orchestrator.domain.context import MenxiaGroupState, ReviewState, ReviewTaskGroup, ReviewTaskItem
from orchestrator.domain.menxia_doc import BODY_PART_TITLES, MenxiaGroupDoc, Signoff, render_initial, verify_group_reply
from orchestrator.domain.policies.menxia_group import apply_menxia_group_results
from orchestrator.runtime.agent_effects import AgentNodeJoiner
from orchestrator.runtime.nodes import WorkerResult
from test_menxia_group_join import _six_part_body
from test_group_doc_loop import _GroupLoopMultica, _context
from test_human_document_loop import _drive


def _join(previous, payload, count=0):
    joiner = AgentNodeJoiner('node', task_id='task', state='MENXIA_GROUP_SOLVER',
        dispatch_mode='menxia_group_pipeline', binding_contexts={'w': {
            'group_id': 'g1', 'doc_markdown': previous, 'reply_retry_count': count,
            'max_reply_retries': 3}}, menxia_stage_census={'g1': 'SOLVING'})
    return joiner.join((WorkerResult('w', 'SUCCEEDED', None, result_payload=payload),)).aggregate


class MenxiaAlignmentTests(unittest.TestCase):
    def test_human_question_survives_fanin_without_accepting_a_draft(self):
        previous = render_initial(group_id='g1', baseline_markdown='approved')
        result = _join(previous, {'action': 'HUMAN_GATE', 'summary': 'Only the user can choose the target environment',
                                 'questions_for_user': [{'question': 'Use test or production?'}],
                                 'doc_markdown': 'unaccepted draft'})
        self.assertEqual(result['action'], 'HUMAN_GATE')
        self.assertEqual(result['questions_for_user'], ['g1: Use test or production?'])
        self.assertNotIn('doc_markdown', result['menxia_group_results'][0])
        empty = _join(previous, {'action': 'HUMAN_GATE', 'summary': 'Completed', 'questions_for_user': []})
        self.assertEqual(empty['action'], 'RETRY')
        self.assertEqual(empty['menxia_group_results'][0]['action'], 'REPAIR_SOLVER_REPLY')

    def test_template_has_all_headers_and_first_fill_remains_v1(self):
        previous = MenxiaGroupDoc.parse(render_initial(group_id='g1', baseline_markdown='approved'))
        for title in BODY_PART_TITLES:
            self.assertIn('### ' + title, previous.body_markdown)
        current = replace(previous, body_markdown=_six_part_body())
        self.assertEqual(verify_group_reply(previous, role='solver', reply={
            'doc_markdown': current.render(), 'doc_version': 1}), ())
        self.assertTrue(verify_group_reply(previous, role='solver', reply={
            'doc_markdown': previous.render(), 'doc_version': 1}))

    def test_wrong_structure_repairs_without_folding_doc(self):
        previous = render_initial(group_id='g1', baseline_markdown='approved')
        bad = replace(MenxiaGroupDoc.parse(previous), body_markdown='### ITEM-001\nReview this.').render()
        aggregate = _join(previous, {'action': 'READY_FOR_CRITIC', 'doc_markdown': bad})
        row = aggregate['menxia_group_results'][0]
        self.assertEqual(row['action'], 'REPAIR_SOLVER_REPLY')
        self.assertEqual(aggregate['action'], 'RETRY')
        self.assertNotIn('doc_markdown', row)
        review = ReviewState(task_items=(ReviewTaskItem('i1', 'g1'),),
                             task_groups=(ReviewTaskGroup('g1', ('i1',)),),
                             menxia_groups=(MenxiaGroupState('g1', requirement_markdown='approved'),))
        folded = apply_menxia_group_results(review, [row], max_rounds=3)[0]
        self.assertEqual(folded.reply_retry_count, 1)
        self.assertEqual(folded.revision_round, 0)
        self.assertEqual(folded.doc_markdown, '')
        self.assertEqual(folded.requirement_markdown, 'approved')
        self.assertEqual(MenxiaGroupState.from_dict(folded.to_dict()), folded)
        exhausted = _join(previous, {'action': 'READY_FOR_ANALYST', 'doc_markdown': bad}, count=3)
        self.assertIn('RETRIES_EXHAUSTED', exhausted['menxia_group_results'][0]['blocked_reason'])

    def test_solver_cannot_skip_analyst_or_change_baseline(self):
        previous = render_initial(group_id='g1', baseline_markdown='approved')
        doc = replace(MenxiaGroupDoc.parse(previous), body_markdown=_six_part_body())
        valid = _join(previous, {'action': 'READY_FOR_CRITIC', 'doc_markdown': doc.render()})
        self.assertEqual(valid['menxia_group_results'][0]['action'], 'READY_FOR_ANALYST')
        bad = _join(previous, {'action': 'READY_FOR_ANALYST', 'doc_markdown':
                              replace(doc, baseline_markdown='changed').render()})
        self.assertEqual(bad['menxia_group_results'][0]['action'], 'BLOCKED')

    def test_application_repairs_only_bad_group_and_reaches_done(self):
        class WrongOnce(_GroupLoopMultica):
            critic_demands_revision = False
            wrong = False

            def _solver_reply(self, request, group_id, doc):
                if group_id == 'group-000001' and not self.wrong:
                    self.wrong = True
                    return {'action': 'READY_FOR_CRITIC', 'doc_version': 1,
                            'doc_markdown': replace(doc, body_markdown='### ITEM-001\nBad headings').render()}
                return super()._solver_reply(request, group_id, doc)

        with tempfile.TemporaryDirectory() as directory:
            adapter = WrongOnce()
            app = OrchestratorApp(_context(), root=directory, multica=adapter, poll_interval=0)
            final = _drive(app, 180)
            self.assertEqual(final.progression.state, 'DONE',
                             [(g.group_id, g.stage, g.blocked_reason) for g in final.review.menxia_groups])
            calls = [r for r in adapter.dispatched if r.target_state == 'MENXIA_GROUP_SOLVER']
            self.assertEqual([r.context['group_id'] for r in calls].count('group-000001'), 2)
            self.assertEqual([r.context['group_id'] for r in calls].count('group-000002'), 1)
            repair = [r for r in calls if r.context.get('reply_rejection')][0]
            self.assertIn('body_missing_part', repair.context['reply_rejection'])
            self.assertIn('Bad headings', repair.context['rejected_doc_markdown'])
            self.assertTrue(all(g.requirement_markdown for g in final.review.menxia_groups))
            self.assertTrue(all(MenxiaGroupDoc.parse(g.doc_markdown).converged()
                                for g in final.review.menxia_groups))

    def test_application_exhausts_shape_repair_budget(self):
        class AlwaysWrong(_GroupLoopMultica):
            critic_demands_revision = False

            def _solver_reply(self, request, group_id, doc):
                if group_id == 'group-000001':
                    return {'action': 'READY_FOR_ANALYST', 'doc_version': 1,
                            'doc_markdown': replace(doc, body_markdown='### ITEM-001\nWrong').render()}
                return super()._solver_reply(request, group_id, doc)

        with tempfile.TemporaryDirectory() as directory:
            adapter = AlwaysWrong()
            app = OrchestratorApp(_context(), root=directory, multica=adapter, poll_interval=0)
            final = _drive(app, 220)
            rows = {g.group_id: g for g in final.review.menxia_groups}
            bad = rows['group-000001']
            self.assertEqual(bad.stage, 'BLOCKED')
            self.assertEqual(bad.reply_retry_count, 3)
            self.assertEqual(bad.revision_round, 0)
            self.assertEqual(bad.doc_markdown, '')
            self.assertIn('RETRIES_EXHAUSTED', bad.blocked_reason)
            calls = [r for r in adapter.dispatched if r.target_state == 'MENXIA_GROUP_SOLVER']
            self.assertEqual([r.context['group_id'] for r in calls].count('group-000001'), 4)
            self.assertEqual([r.context['group_id'] for r in calls].count('group-000002'), 1)

    def test_reviewer_signoff_requires_current_analyst_and_bound_hash(self):
        doc = replace(MenxiaGroupDoc.parse(render_initial(group_id='g1', baseline_markdown='approved')),
                      body_markdown=_six_part_body())
        signed_once = doc.sign_doc(author='analyst', approved=True, note='evidence checked')
        self.assertEqual(MenxiaGroupDoc.parse(signed_once.render()), signed_once)
        unsigned = doc.sign_doc(author='analyst', approved=False, note='请勿视为通过')
        self.assertFalse(MenxiaGroupDoc.parse(unsigned.render()).approved_by('analyst'))

        def review(role, previous, **fields):
            joiner = AgentNodeJoiner('node', task_id='task', state='MENXIA_GROUP_' + role.upper(),
                dispatch_mode='menxia_group_pipeline', binding_contexts={'w': {
                    'group_id': 'g1', 'doc_markdown': previous}}, menxia_stage_census={'g1': 'REVIEWING'})
            payload = {'action': 'APPROVE_GROUP' if role == 'critic' else 'EVIDENCE_SUFFICIENT',
                       'doc_markdown': previous, 'added_suggestion_ids': []}
            payload.update(fields)
            return joiner.join((WorkerResult('w', 'SUCCEEDED', None, result_payload=payload),)).aggregate['menxia_group_results'][0]

        missing = review('critic', doc.render())
        self.assertEqual(missing['action'], 'REQUEST_EVIDENCE')
        stale = review('critic', replace(doc, signoffs=(Signoff('analyst', 0, True),)).render())
        self.assertEqual(stale['action'], 'REQUEST_EVIDENCE')
        rejected = review('analyst', doc.render(), reviewed_hash='wrong')
        self.assertEqual(rejected['action'], 'BLOCKED')
        row = review('analyst', doc.render(), reviewed_version=1,
                     reviewed_hash=hashlib.sha256(doc.render().encode()).hexdigest())
        signed = MenxiaGroupDoc.parse(row['doc_markdown'])
        self.assertTrue(signed.approved_by('analyst'))
        approved = review('critic', signed.render())
        self.assertTrue(MenxiaGroupDoc.parse(approved['doc_markdown']).converged())

        pending = doc.add_suggestion(author='analyst', severity='P1', body='Verify behavior')
        pending = pending.reject(pending.suggestions[0].suggestion_id, reason='Evidence disproves issue')
        confirmed = review('analyst', pending.render(), confirmed_rejection_ids=['S-001'])
        closed = MenxiaGroupDoc.parse(confirmed['doc_markdown'])
        self.assertFalse(closed.pending_rejections())
        self.assertTrue(closed.approved_by('analyst'))
        self.assertIn('S-001', closed.ledger[-1].suggestion_ids)
        wrong_author = review('critic', pending.render(), confirmed_rejection_ids=['S-001'])
        self.assertEqual(wrong_author['action'], 'BLOCKED')
        forged = replace(doc, version=2, signoffs=(Signoff('critic', 2, True),))
        self.assertIn('solver forged reviewer signoff', verify_group_reply(doc, role='solver', reply={
            'doc_markdown': forged.render(), 'touched_scope': []}))

    def test_old_reply_replays_into_repair_without_live_state_mutation(self):
        bundle = Path(__file__).resolve().parents[1] / ('runs/transport/prompt-bundles/task-20261005-498c2f/'
                 'task-20261005-498c2f_MENXIA_GROUP_SOLVER_9_worker-01')
        if not bundle.exists():
            self.skipTest('optional local incident bundle is not available')
        context = json.loads((bundle / 'context.json').read_text(encoding='utf-8'))
        payload = json.loads((bundle / 'result.json').read_text(encoding='utf-8'))
        group_id = context['group_id']
        joiner = AgentNodeJoiner('offline-replay', task_id='offline-replay', state='MENXIA_GROUP_SOLVER',
            dispatch_mode='menxia_group_pipeline', binding_contexts={'w': context},
            menxia_stage_census={group_id: 'SOLVING'})
        result = joiner.join((WorkerResult('w', 'SUCCEEDED', None, result_payload=payload),)).aggregate
        self.assertEqual(result['action'], 'RETRY')
        row = result['menxia_group_results'][0]
        self.assertEqual(row['action'], 'REPAIR_SOLVER_REPLY')
        self.assertNotIn('doc_markdown', row)
        self.assertEqual(row['rejected_doc_markdown'], payload['doc_markdown'])
