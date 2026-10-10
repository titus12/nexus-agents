"""Fix 2: role-differentiated dispatch timeouts (2026-09-28 reliability plan).

Group revision / group review workers re-read the full prompt bundle and
routinely exceed a single global cap (incident task-20260928-835a07: three
deterministic 900.0s AGENT_TIMEOUT hits on one solver revision), while
analyst hops stay well under it.
"""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from unittest import mock

from orchestrator.adapters import FakeMulticaAdapter
from orchestrator.app import OrchestratorApp
from orchestrator.domain.context import (
    ProgressState,
    TaskIdentity,
    WorkflowContext,
)
from orchestrator.runtime.agent_effects import (
    ROLE_DISPATCH_TIMEOUTS,
    AgentWorkerRunner,
    resolve_dispatch_role_timeouts,
)
from orchestrator.runtime.ports import (
    AgentDispatchRequest,
    DispatchReceipt,
    RemoteRunStatus,
)

os.environ.setdefault("NEXUS_TEST_NO_EXTERNAL_NOTIFICATIONS", "1")

def _context(state: str = "ZHONGSHU_SOLVER") -> WorkflowContext:
    return WorkflowContext(
        identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
        progression=ProgressState(state, 3, "2026-09-28T00:00:00Z"),
    )


def _request(payload: dict | None = None):
    from orchestrator.domain.decisions import EffectRequest

    return EffectRequest(
        effect_id="effect:task-1:1",
        effect_type="agent_dispatch",
        task_id="task-1",
        idempotency_key="task-1:ZHONGSHU_SOLVER:1",
        payload=payload or {},
    )


class _HangingTransport:
    """A transport whose remote runs never reach a terminal state."""

    def dispatch(self, request: AgentDispatchRequest) -> DispatchReceipt:
        return DispatchReceipt(
            operation_id=f"op-{request.request_id}",
            external_message_id=f"msg-{request.request_id}",
            confirmed=True,
            request_id=request.request_id,
        )

    def status(self, poll) -> RemoteRunStatus:
        return RemoteRunStatus(
            request_id=poll.request_id,
            operation_id=poll.operation_id,
            status="RUNNING",
        )


class EffectiveDispatchLimitsTests(unittest.TestCase):
    """The (timeout, max_polls) pair honours payload > role table > global."""

    def _runner(self, **kwargs) -> AgentWorkerRunner:
        defaults = dict(poll_interval=0.0, max_polls=30, timeout_seconds=900.0)
        defaults.update(kwargs)
        return AgentWorkerRunner(_HangingTransport(), **defaults)

    def test_role_table_overrides_global(self) -> None:
        runner = self._runner(
            role_timeout_seconds={"review-solver": 1800.0}
        )
        timeout, max_polls = runner._effective_dispatch_limits(
            "review-solver", {}
        )
        self.assertEqual(timeout, 1800.0)
        self.assertEqual(max_polls, 30)

    def test_unknown_role_falls_back_to_global(self) -> None:
        runner = self._runner(
            timeout_seconds=777.0,
            role_timeout_seconds={"review-solver": 1800.0},
        )
        timeout, _ = runner._effective_dispatch_limits("review-other", {})
        self.assertEqual(timeout, 777.0)

    def test_payload_override_beats_role_table(self) -> None:
        runner = self._runner(
            role_timeout_seconds={"review-solver": 1800.0}
        )
        timeout, _ = runner._effective_dispatch_limits(
            "review-solver", {"timeout_seconds": 600}
        )
        self.assertEqual(timeout, 600.0)

    def test_invalid_payload_override_falls_back(self) -> None:
        runner = self._runner(
            timeout_seconds=777.0,
            role_timeout_seconds={"review-solver": 1800.0},
        )
        timeout, _ = runner._effective_dispatch_limits(
            "review-solver", {"timeout_seconds": "not-a-number"}
        )
        self.assertEqual(timeout, 1800.0)

    def test_max_polls_grows_with_the_effective_timeout(self) -> None:
        runner = self._runner(
            poll_interval=1.0,
            max_polls=30,
            timeout_seconds=900.0,
            role_timeout_seconds={"review-solver": 1800.0},
        )
        timeout, max_polls = runner._effective_dispatch_limits(
            "review-solver", {}
        )
        self.assertEqual(timeout, 1800.0)
        # 1800 / 1 + 2 = 1802 polls, above the 30-poll floor.
        self.assertEqual(max_polls, 1802)

    def test_max_polls_floor_kept_for_short_timeouts(self) -> None:
        runner = self._runner(
            poll_interval=1.0, max_polls=30, timeout_seconds=10.0
        )
        _, max_polls = runner._effective_dispatch_limits("review-analyst", {})
        # 10 / 1 + 2 = 12 derived polls sit under the 30-poll floor.
        self.assertEqual(max_polls, 30)

    def test_unsleept_polling_keeps_configured_max_polls(self) -> None:
        runner = self._runner(
            poll_interval=0.0, max_polls=17, timeout_seconds=900.0
        )
        _, max_polls = runner._effective_dispatch_limits("review-solver", {})
        self.assertEqual(max_polls, 17)


class HangingRunTimeoutTests(unittest.TestCase):
    """A hanging solver run times out on the role timeout, not the global."""

    def _run_until_timeout(self, runner: AgentWorkerRunner, payload: dict):
        started = time.monotonic()
        outcome = runner.run_once(_request(payload))
        elapsed = time.monotonic() - started
        return outcome, elapsed

    def test_solver_hang_exceeds_short_global_cap(self) -> None:
        runner = AgentWorkerRunner(
            _HangingTransport(),
            poll_interval=0.02,
            max_polls=1000,
            timeout_seconds=0.3,
            role_timeout_seconds={"review-solver": 0.8},
        )
        outcome, elapsed = self._run_until_timeout(runner, {"role": "review-solver"})
        self.assertEqual(outcome.status, "FAILED")
        self.assertEqual(outcome.failure.error_code, "AGENT_TIMEOUT")
        self.assertGreaterEqual(elapsed, 0.75)

    def test_payload_timeout_beats_role_table_on_a_hang(self) -> None:
        runner = AgentWorkerRunner(
            _HangingTransport(),
            poll_interval=0.02,
            max_polls=1000,
            timeout_seconds=5.0,
            role_timeout_seconds={"review-solver": 5.0},
        )
        outcome, elapsed = self._run_until_timeout(
            runner, {"role": "review-solver", "timeout_seconds": 0.2}
        )
        self.assertEqual(outcome.failure.error_code, "AGENT_TIMEOUT")
        self.assertLess(elapsed, 2.0)


class RoleTimeoutResolutionTests(unittest.TestCase):
    """resolve_dispatch_role_timeouts merges the table with the env JSON."""

    def test_defaults_match_the_role_table(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ZHONGSHU_DISPATCH_TIMEOUT_OVERRIDES", None)
            resolved = resolve_dispatch_role_timeouts()
        self.assertEqual(resolved, ROLE_DISPATCH_TIMEOUTS)
        self.assertEqual(resolved["review-solver"], 1800.0)
        self.assertEqual(resolved["review-critic"], 1200.0)
        self.assertEqual(resolved["review-analyst"], 900.0)

    def test_env_override_wins_per_role(self) -> None:
        env = {"ZHONGSHU_DISPATCH_TIMEOUT_OVERRIDES": '{"review-solver": 2400}'}
        with mock.patch.dict(os.environ, env):
            resolved = resolve_dispatch_role_timeouts()
        self.assertEqual(resolved["review-solver"], 2400.0)
        self.assertEqual(resolved["review-analyst"], 900.0)

    def test_invalid_env_json_keeps_the_table(self) -> None:
        env = {"ZHONGSHU_DISPATCH_TIMEOUT_OVERRIDES": "{not json"}
        with mock.patch.dict(os.environ, env):
            resolved = resolve_dispatch_role_timeouts()
        self.assertEqual(resolved, ROLE_DISPATCH_TIMEOUTS)

    def test_include_defaults_false_keeps_only_env_overrides(self) -> None:
        env = {"ZHONGSHU_DISPATCH_TIMEOUT_OVERRIDES": '{"review-solver": 2400}'}
        with mock.patch.dict(os.environ, env):
            resolved = resolve_dispatch_role_timeouts(include_defaults=False)
        self.assertEqual(resolved, {"review-solver": 2400.0})


class MenxiaSharedRoleTimeoutTests(unittest.TestCase):
    """Role keys are phase-agnostic: Menxia workers share the same table.

    The plan's open choice ("(phase, role) keys or announce Menxia sharing")
    resolved to sharing: `_ROLE_BY_STATE` maps both phases onto the same
    review-* role names, so one lookup table serves them alike.  Locked here
    so a future phase-scoped split cannot silently change one side only.
    """

    def test_menxia_states_resolve_the_same_role_timeouts(self) -> None:
        from orchestrator.domain.states import _ROLE_BY_STATE

        expected = {
            "review-analyst": ROLE_DISPATCH_TIMEOUTS["review-analyst"],
            "review-critic": ROLE_DISPATCH_TIMEOUTS["review-critic"],
            "review-solver": ROLE_DISPATCH_TIMEOUTS["review-solver"],
        }
        runner = AgentWorkerRunner(
            _HangingTransport(),
            poll_interval=0.0,
            max_polls=30,
            timeout_seconds=900.0,
            role_timeout_seconds=ROLE_DISPATCH_TIMEOUTS,
        )
        for state, (role_name, _phase) in _ROLE_BY_STATE.items():
            if not state.startswith("MENXIA_"):
                continue
            with self.subTest(state=state):
                self.assertIn(role_name, expected)
                timeout, _ = runner._effective_dispatch_limits(role_name, {})
                self.assertEqual(timeout, expected[role_name])
        # The same solver role carries the same deadline in both phases.
        for state in ("ZHONGSHU_SOLVER", "MENXIA_GROUP_SOLVER", "MENXIA_ITEM_SOLVER"):
            role, _phase = _ROLE_BY_STATE[state]
            self.assertEqual(role, "review-solver")
            timeout, _ = runner._effective_dispatch_limits(role, {})
            self.assertEqual(timeout, 1800.0)


class AppWiringTests(unittest.TestCase):
    """The differentiation applies only to the default global timeout."""

    def _app(self, **kwargs) -> OrchestratorApp:
        with tempfile.TemporaryDirectory() as directory:
            app = OrchestratorApp(
                _context(),
                root=directory,
                multica=FakeMulticaAdapter(),
                **kwargs,
            )
            return app

    def test_default_configuration_injects_the_role_table(self) -> None:
        app = self._app()
        self.assertEqual(
            app.runner._role_timeout_seconds,
            ROLE_DISPATCH_TIMEOUTS,
        )

    def test_explicit_non_default_configuration_skips_the_table(self) -> None:
        app = self._app(timeout_seconds=1200)
        self.assertEqual(app.runner._role_timeout_seconds, {})

    def test_env_override_survives_a_non_default_global_timeout(self) -> None:
        env = {"ZHONGSHU_DISPATCH_TIMEOUT_OVERRIDES": '{"review-solver": 2400}'}
        with mock.patch.dict(os.environ, env):
            app = self._app(timeout_seconds=1200)
        # The baked-in table stays out of the way, but an explicit env
        # override is never silently dropped with it.
        self.assertEqual(app.runner._role_timeout_seconds, {"review-solver": 2400.0})


if __name__ == "__main__":
    unittest.main()
