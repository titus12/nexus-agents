from dataclasses import replace
import json
import unittest
from orchestrator.requirements_cards import clarification_card,review_card
from orchestrator.domain.requirements.questions import open_round,accept_answers
from requirements_test_support import make_state,question


class CardTests(unittest.TestCase):
    def test_recommendation_is_visible_without_answer_mode_dropdown_or_preselection(self):
        q=replace(question(),blocking=False,recommendation='推荐由业务触发，便于控制时机。',default_assumption_id='a1')
        card=clarification_card('gate','token','page',[q],1,1)
        encoded=json.dumps(card,ensure_ascii=False)
        self.assertIn(q.recommendation,encoded)
        self.assertNotIn('_disposition',encoded)
        self.assertNotIn('暂缓',encoded)
        self.assertNotIn('initial_option',encoded)
        form=card['body']['elements'][1]['elements']
        self.assertTrue(any(e.get('name')=='q0' for e in form))
        self.assertTrue(any(e.get('name')=='q0_text' for e in form))

    def test_question_reason_impact_and_partial_answer_are_visible_without_preselection(self):
        q=question()
        card=clarification_card('gate','token','page',[q],1,1)
        encoded=json.dumps(card,ensure_ascii=False)
        self.assertIn(q.reason,encoded)
        self.assertIn(q.impact,encoded)
        self.assertNotIn('initial_option',encoded)
        state=open_round(replace(make_state(),questions=(q,)),'REQ_DRAFT')
        state=accept_answers(state,{'round_id':state.rounds[-1].round_id,'actor_id':'local-operator','event_id':'answer',
            'answers':[{'question_id':q.question_id,'question_revision':1,'option_ids':['manual']}]})
        refreshed=json.dumps(clarification_card('gate','token','page',[q],1,1,state.answers),ensure_ascii=False)
        self.assertIn('已',refreshed)
        self.assertIn('由业务触发',refreshed)

    def test_approval_carries_exact_document_version_and_separate_feedback(self):
        encoded=json.dumps(review_card('gate','token','https://example.test/doc',3,'a'*64),ensure_ascii=False)
        self.assertIn('a'*64,encoded)
        self.assertIn('approve',encoded)
        self.assertIn('feedback',encoded)
        self.assertIn('cancel',encoded)
