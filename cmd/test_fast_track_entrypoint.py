from __future__ import annotations

import tempfile
import unittest

from orchestrator.app import OrchestratorApp
from orchestrator.domain.context import (
    MenxiaParallelLimits,
    ParallelState,
    ProgressState,
    RequestState,
    TaskIdentity,
    WorkflowContext,
    ZhongshuParallelLimits,
)
from test_linear_fsm_entrypoint import _ScriptedMultica


def _context() -> WorkflowContext:
    return WorkflowContext(
        identity=TaskIdentity("task-fast-track", "issue-fast-track", "", "request-fast-track"),
        progression=ProgressState("REQUEST_INTAKE", 0, "2026-09-10T00:00:00Z"),
        request=RequestState(raw_request="run a review", project_type="go", task_type="review"),
        parallel=ParallelState(
            zhongshu=ZhongshuParallelLimits(enabled=True, fast_track=True),
            menxia=MenxiaParallelLimits(
                enabled=True,
                max_concurrent_groups=2,
                max_concurrent_items=3,
            ),
        ),
    )


class FastTrackEntrypointTests(unittest.TestCase):
    def test_fast_track_skips_critic_wave_and_freeze_check(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter = _ScriptedMultica()
            app = OrchestratorApp(
                _context(),
                root=directory,
                multica=adapter,
                poll_interval=0,
                timeout_seconds=2,
            )

            self.assertTrue(app.run())
            snapshot = app.repository.load("task-fast-track")

        self.assertEqual(snapshot.context.progression.state, "DONE")
        targets = [request.target_state for request in adapter.dispatched]
        # The Critic review wave and the freeze-check agent must never run:
        # the fast track auto-approves both stages without any agent round.
        self.assertNotIn("ZHONGSHU_CRITIC", targets)
        self.assertNotIn("ZHONGSHU_FREEZE_CHECK", targets)
        # Analyst (contract + lens wave), solver, the menxia group waves
        # (solver/analyst/critic for the single group) and the group gate
        # still run exactly as in the baseline.
        self.assertIn("ZHONGSHU_ANALYST", targets)
        self.assertIn("ZHONGSHU_SOLVER", targets)
        self.assertIn("MENXIA_GROUP_SOLVER", targets)
        self.assertIn("MENXIA_GROUP_ANALYST", targets)
        self.assertIn("MENXIA_GROUP_CRITIC", targets)
        self.assertIn("MENXIA_GROUP_GATE", targets)

    def test_fast_track_flag_defaults_off(self):
        limits = ZhongshuParallelLimits()
        self.assertFalse(limits.fast_track)


if __name__ == "__main__":
    unittest.main()
