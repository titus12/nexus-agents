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

    def test_partial_quote_of_existing_conclusion_reuses_the_decision(self):
        state = make_state()
        source_text = state.sources[0].text
        full_quote = source_text + '\n（补充说明）接受默认约束'
        state = add_feedback(state, full_quote, 'feedback-multi')
        payload = {'claims': [
            {'local_key': 'claim_full', 'topic_key': 'gc', 'kind': 'goal', 'origin': 'explicit',
             'statement': full_quote, 'source_ids': ['src-000002'], 'quote': full_quote},
            {'local_key': 'claim_part', 'topic_key': 'gc', 'kind': 'goal', 'origin': 'explicit',
             'statement': '重述同一结论的目标', 'source_ids': ['src-000002'], 'quote': source_text},
        ]}
        state = apply_analysis(state, payload)
        gc_decisions = [d for d in state.decisions if d.topic_key == 'gc']
        self.assertEqual(len(gc_decisions), 1)
        self.assertEqual(gc_decisions[0].conclusion, full_quote)
        bound = {c.local_id if hasattr(c, 'local_id') else c.requirement_id: c
                 for c in state.consensus.claims}
        part = [c for c in state.consensus.claims if c.statement == '重述同一结论的目标'][0]
        self.assertEqual(part.decision_ids, (gc_decisions[0].decision_id,))

    def test_topic_remap_orphaning_a_decision_is_rejected(self):
        state = make_state()
        state = add_feedback(state, 'GC 门禁：每帧分配低于 128 字节', 'feedback-gc')
        state = apply_analysis(state, {'claims': [{'requirement_id': 'req-000002', 'topic_key': 'gc-gate',
            'statement': 'GC 门禁以每帧分配为验收口径', 'source_ids': ['src-000002'], 'quote': 'GC 门禁：每帧分配低于 128 字节'}]})
        with self.assertRaisesRegex(ValueError, r"claims\[req-000002\]: REQUIREMENT_REMAP_ORPHANS_DECISION.*decision-"):
            apply_analysis(state, {'claims': [{'requirement_id': 'req-000002', 'topic_key': 'engine-version',
                'statement': '引擎版本约束', 'source_ids': ['src-000002'], 'quote': 'GC 门禁：每帧分配低于 128 字节'}]})

    def test_topic_remap_without_decisions_is_allowed(self):
        state = make_state()
        state = apply_analysis(state, {'claims': [{'requirement_id': 'req-000002', 'topic_key': 'old-topic',
            'statement': '无决定的主题', 'source_ids': ['src-000001'], 'origin': 'derived'}]})
        state = apply_analysis(state, {'claims': [{'requirement_id': 'req-000002', 'topic_key': 'new-topic',
            'statement': '换个主题重新归纳', 'source_ids': ['src-000001'], 'origin': 'derived'}]})
        moved = next(c for c in state.consensus.claims if c.requirement_id == 'req-000002')
        self.assertEqual(moved.topic_key, 'new-topic')

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
