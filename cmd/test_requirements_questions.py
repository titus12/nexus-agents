import unittest
from dataclasses import asdict, replace
from orchestrator.domain.requirements.questions import *
from requirements_test_support import make_state, question


class QuestionsTests(unittest.TestCase):
    def test_dependencies_and_partial_answers(self):
        state = replace(make_state(), questions=(question(), question('q-000002', ('q-000001',)), question('q-000003')))
        self.assertEqual([q.question_id for q in eligible_questions(state)], ['q-000001', 'q-000003'])
        state = open_round(state, 'REQ_ANALYZE', ('ou-owner',))
        submission = {'round_id': 'round-000001', 'actor_id': 'ou-owner', 'event_id': 'event-1',
                      'answers': [{'question_id': 'q-000001', 'question_revision': 1, 'option_ids': ['manual']}]}
        answered = accept_answers(state, submission)
        self.assertEqual(answered.rounds[-1].status, 'partial')
        self.assertEqual(accept_answers(answered, submission), answered)
        self.assertEqual(answered.answers[0].option_ids, ('manual',))
        self.assertIn('q-000002', [q.question_id for q in eligible_questions(answered)])
        submission['event_id'] = 'event-2'
        submission['answers'][0]['option_ids'] = ['automatic']
        with self.assertRaisesRegex(ValueError, 'DECISION_CHANGE_REQUIRED'):
            accept_answers(answered, submission)

    def test_no_preselection_authority_and_wrong_actor_rejected(self):
        state = open_round(replace(make_state(), questions=(question(),)), 'REQ_DRAFT', ('ou-owner',))
        self.assertEqual(state.answers, ())
        with self.assertRaisesRegex(ValueError, 'UNAUTHORIZED'):
            accept_answers(state, {'round_id': 'round-000001', 'event_id': 'event-1', 'actor_id': 'ou-other', 'answers': [{}]})

    def test_question_cycle_rejected(self):
        state = make_state()
        with self.assertRaisesRegex(ValueError, 'CYCLE'):
            merge_questions(state, [asdict(question('q-000001', ('q-000002',))), asdict(question('q-000002', ('q-000001',)))])

    def test_local_keys_resolve_dependencies_and_claim_references(self):
        first=asdict(question())
        first.pop('question_id')
        first.update(local_key='goal',related_requirement_ids=['local-claim'])
        second=asdict(question('q-000002',('goal',)))
        second.pop('question_id')
        second.update(local_key='behavior',topic_key='different-topic')
        state=merge_questions(make_state(),[first,second],{'local-claim':'req-000001'})
        self.assertEqual(state.questions[1].depends_on_question_ids,('q-000001',))
        self.assertEqual(state.questions[0].related_requirement_ids,('req-000001',))

    def test_late_answer_proposes_change_without_overwriting_decision(self):
        state=open_round(replace(make_state(),questions=(question(),)),'REQ_DRAFT',('ou-owner',))
        submission={'round_id':'round-000001','actor_id':'ou-owner','event_id':'a',
            'answers':[{'question_id':'q-000001','question_revision':1,'option_ids':['manual']}]}
        state=accept_answers(state,submission)
        decisions=state.decisions
        submission['answers'][0]['option_ids']=['automatic']
        proposed=propose_answer_changes(state,submission,'b','')
        self.assertEqual(proposed.decisions,decisions)
        self.assertEqual(proposed.answers,state.answers)
        self.assertEqual(proposed.questions[-1].options[1].label,'自动重试')
        self.assertIn('原决定',proposed.sources[-1].text)
