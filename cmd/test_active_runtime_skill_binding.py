from __future__ import annotations

import unittest

from orchestrator.runtime.compat_effects import _SKILLS, _skill_lock


class ActiveRuntimeSkillBindingTests(unittest.TestCase):
    def test_zhongshu_analyst_skill_lock_resolves_from_repository_root(self) -> None:
        lock = _skill_lock("ZHONGSHU_ANALYST")

        self.assertEqual(lock.get("name"), "zhongshu-analyst")
        self.assertEqual(lock.get("status"), "locked")
        self.assertTrue(lock.get("source", "").endswith(
            "docs\\multi\\runtime\\zhongshu-analyst-skill.md"
        ))
        self.assertTrue(lock.get("sha256"))
        self.assertGreater(lock.get("bytes", 0), 0)

    def test_every_menxia_group_state_has_a_locked_skill_binding(self) -> None:
        # Regression: the group-pipeline states were missing from _SKILLS,
        # so live dispatches raised ACTIVE_RUNTIME_SKILL_BINDING_MISSING and
        # the task-menxia-t1 acceptance run failed (2026-09-23).
        group_states = (
            "MENXIA_GROUP_SOLVER",
            "MENXIA_GROUP_ANALYST",
            "MENXIA_GROUP_CRITIC",
            "MENXIA_GROUP_GATE",
        )
        for state in group_states:
            with self.subTest(state=state):
                self.assertIn(state, _SKILLS)
                lock = _skill_lock(state)
                self.assertEqual(lock.get("status"), "locked", state)
                self.assertTrue(lock.get("sha256"), state)
                self.assertGreater(lock.get("bytes", 0), 0, state)

    def test_group_states_reuse_the_item_pipeline_skill_files(self) -> None:
        self.assertEqual(_SKILLS["MENXIA_GROUP_SOLVER"], _SKILLS["MENXIA_ITEM_SOLVER"])
        self.assertEqual(_SKILLS["MENXIA_GROUP_ANALYST"], _SKILLS["MENXIA_ITEM_ANALYST"])
        self.assertEqual(_SKILLS["MENXIA_GROUP_CRITIC"], _SKILLS["MENXIA_ITEM_CRITIC"])

    def test_skill_table_covers_every_dispatch_state(self) -> None:
        from orchestrator.domain.states import _ROLE_BY_STATE

        for state in _ROLE_BY_STATE:
            with self.subTest(state=state):
                self.assertIn(state, _SKILLS)

    def test_agent_pool_map_covers_every_dispatch_state(self) -> None:
        # Regression: the agent_ids map in app.py was missing the three
        # MENXIA_GROUP_* states, so worker binding fell back to the payload's
        # worker name ("menxia-solver-01") and the multica CLI rejected it:
        # "resolve assignee: expected a canonical UUID" (2026-09-23).
        from orchestrator.app import _agent_pool_by_state
        from orchestrator.domain.states import _ROLE_BY_STATE

        pools = _agent_pool_by_state()
        for state in _ROLE_BY_STATE:
            with self.subTest(state=state):
                self.assertIn(state, pools)

    def test_group_wave_dispatches_carry_a_locked_skill_binding(self) -> None:
        # Integration point that failed live: MulticaTransportAdapter stamps
        # the skill lock onto every outbound request; the Multica adapter
        # refuses MENXIA reviewer dispatches without one.
        from orchestrator.runtime.compat_effects import MulticaTransportAdapter
        from orchestrator.runtime.ports import AgentDispatchRequest

        adapter = MulticaTransportAdapter(adapter=object())
        for state, role in (
            ("MENXIA_GROUP_SOLVER", "review-solver"),
            ("MENXIA_GROUP_ANALYST", "review-analyst"),
            ("MENXIA_GROUP_CRITIC", "review-critic"),
        ):
            with self.subTest(state=state):
                external = adapter._external_request(
                    AgentDispatchRequest(
                        task_id="task-1",
                        issue_id="issue-1",
                        request_id=f"task-1:{state}:1",
                        agent_id="menxia-agent",
                        role=role,
                        phase="MENXIA",
                        prompt_ref="prompt",
                        idempotency_key=f"task-1:{state}:1",
                        target_state=state,
                    )
                )
                lock = external.context.get("active_runtime_skill_lock")
                self.assertIsInstance(lock, dict)
                self.assertEqual(lock.get("status"), "locked", state)


if __name__ == "__main__":
    unittest.main()
