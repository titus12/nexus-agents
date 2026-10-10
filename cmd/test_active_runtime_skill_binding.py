from __future__ import annotations
import unittest
from pathlib import Path
from orchestrator.runtime.compat_effects import _SKILLS, _skill_lock

class ActiveRuntimeSkillBindingTests(unittest.TestCase):

    def test_every_menxia_group_state_has_a_locked_skill_binding(self) -> None:
        group_states = ('MENXIA_GROUP_SOLVER', 'MENXIA_GROUP_ANALYST', 'MENXIA_GROUP_CRITIC', 'MENXIA_GROUP_GATE')
        for state in group_states:
            with self.subTest(state=state):
                self.assertIn(state, _SKILLS)
                lock = _skill_lock(state)
                self.assertEqual(lock.get('status'), 'locked', state)
                self.assertTrue(lock.get('sha256'), state)
                self.assertGreater(lock.get('bytes', 0), 0, state)

    def test_group_states_reuse_the_item_pipeline_skill_files(self) -> None:
        self.assertEqual(_SKILLS['MENXIA_GROUP_SOLVER'], _SKILLS['MENXIA_ITEM_SOLVER'])
        self.assertEqual(_SKILLS['MENXIA_GROUP_ANALYST'], _SKILLS['MENXIA_ITEM_ANALYST'])
        self.assertEqual(_SKILLS['MENXIA_GROUP_CRITIC'], _SKILLS['MENXIA_ITEM_CRITIC'])

    def test_skill_table_covers_every_dispatch_state(self) -> None:
        from orchestrator.domain.states import _ROLE_BY_STATE
        for state in _ROLE_BY_STATE:
            with self.subTest(state=state):
                self.assertIn(state, _SKILLS)

    def test_every_skill_allows_the_overflow_result_file_fallback(self) -> None:
        for state in sorted(_SKILLS):
            lock = _skill_lock(state)
            content = Path(str(lock.get('source') or '')).read_text(encoding='utf-8')
            with self.subTest(skill=_SKILLS[state]):
                self.assertIn('too large to post safely', content)
                self.assertIn('结果文件', content)

    def test_agent_pool_map_covers_every_dispatch_state(self) -> None:
        from orchestrator.app import _agent_pool_by_state
        from orchestrator.domain.states import _ROLE_BY_STATE
        pools = _agent_pool_by_state()
        for state in _ROLE_BY_STATE:
            with self.subTest(state=state):
                self.assertIn(state, pools)

    def test_group_wave_dispatches_carry_a_locked_skill_binding(self) -> None:
        from orchestrator.runtime.compat_effects import MulticaTransportAdapter
        from orchestrator.runtime.ports import AgentDispatchRequest
        adapter = MulticaTransportAdapter(adapter=object())
        for state, role in (('MENXIA_GROUP_SOLVER', 'review-solver'), ('MENXIA_GROUP_ANALYST', 'review-analyst'), ('MENXIA_GROUP_CRITIC', 'review-critic')):
            with self.subTest(state=state):
                external = adapter._external_request(AgentDispatchRequest(task_id='task-1', issue_id='issue-1', request_id=f'task-1:{state}:1', agent_id='menxia-agent', role=role, phase='MENXIA', prompt_ref='prompt', idempotency_key=f'task-1:{state}:1', target_state=state))
                lock = external.context.get('active_runtime_skill_lock')
                self.assertIsInstance(lock, dict)
                self.assertEqual(lock.get('status'), 'locked', state)
if __name__ == '__main__':
    unittest.main()
