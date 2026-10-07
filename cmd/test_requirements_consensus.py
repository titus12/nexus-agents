import unittest
from orchestrator.domain.requirements.consensus import *
from requirements_test_support import make_state


class ConsensusTests(unittest.TestCase):
    def test_analysis_cannot_override_choice_or_forge_decision(self):
        state = make_state()
        with self.assertRaisesRegex(ValueError, 'PROTECTED'):
            apply_analysis(state, {'claims': [{'requirement_id': 'req-000001', 'topic_key': 'duplicate-login',
                           'statement': '每次登录都创建连接', 'source_ids': ['src-000001']}]})
        with self.assertRaisesRegex(ValueError, 'AUTHORITY'):
            apply_analysis(state, {'decisions': []})

    def test_feedback_adds_source_and_duplicate_does_not_reset(self):
        state = add_feedback(make_state(), '允许最多重试两次', 'feedback-1')
        self.assertEqual(len(state.sources), 2)
        self.assertEqual(add_feedback(state, '允许最多重试两次', 'feedback-1'), state)
        self.assertTrue(state.consensus.pending_source_ids)
        self.assertEqual(len(state.decisions), 1)

    def test_new_explicit_choice_preserves_history(self):
        state = add_feedback(make_state(), '每次登录都创建连接', 'feedback-2')
        state = apply_analysis(state, {'claims': [{'requirement_id': 'req-000001', 'topic_key': 'duplicate-login',
                           'statement': '每次登录都创建连接', 'source_ids': ['src-000002'], 'quote': '每次登录都创建连接'}]})
        self.assertEqual(len(state.decisions), 2)
        self.assertEqual(active_decisions(state)[0].supersedes_ids, ('decision-000001',))
        self.assertEqual(active_decisions(state)[0].conclusion, '每次登录都创建连接')

    def test_conflicting_quote_same_topic_names_claim_and_conclusion(self):
        state = make_state()
        payload = {'claims': [
            {'local_key': 'claim_a', 'topic_key': 'gc', 'kind': 'goal', 'origin': 'explicit',
             'statement': '重复登录相关目标', 'source_ids': ['src-000001'], 'quote': '重复登录'},
            {'local_key': 'claim_b', 'topic_key': 'gc', 'kind': 'goal', 'origin': 'explicit',
             'statement': '只触发一次连接相关目标', 'source_ids': ['src-000001'], 'quote': '只触发一次连接'},
        ]}
        with self.assertRaisesRegex(ValueError, r"claims\[claim_b\]: USER_DECISION_PROTECTED: topic 'gc' "
                                                r"already concluded '重复登录'") as caught:
            apply_analysis(state, payload)
        self.assertIn('req-000002', str(caught.exception))

    def test_statement_change_error_names_claim_and_existing_statement(self):
        with self.assertRaisesRegex(ValueError, r"claims\[req-000001\]: USER_DECISION_PROTECTED: "
                                                r"statement changed.*keep existing statement: "
                                                r"'同一会话的重复登录只触发一次连接'"):
            apply_analysis(make_state(), {'claims': [{'requirement_id': 'req-000001',
                                                      'topic_key': 'duplicate-login',
                                                      'statement': '每次登录只建立一条连接',
                                                      'source_ids': ['src-000001']}]})

    def test_unknown_claim_source_names_claim(self):
        with self.assertRaisesRegex(ValueError, r'claims\[claim_x\]: CLAIM_SOURCE_UNKNOWN'):
            apply_analysis(make_state(), {'claims': [{'local_key': 'claim_x', 'topic_key': 't',
                                                      'statement': 's', 'source_ids': ['src-999999'],
                                                      'origin': 'derived'}]})
