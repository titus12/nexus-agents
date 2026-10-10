from __future__ import annotations
import tempfile
import threading
import time
import unittest
from orchestrator.adapters import FakeMulticaAdapter
from orchestrator.app import OrchestratorApp
from orchestrator.domain.context import HumanGateState, ProgressState, RecoveryState, TaskIdentity, WorkflowContext
from orchestrator.domain.states import MenxiaGroupAnalystState

def _context(state: str, *, entered_at: str='2026-09-15T00:00:00Z', **review):
    return WorkflowContext(identity=TaskIdentity('task-1', 'issue-1', 'project-1', 'request-1'), progression=ProgressState(state, 3, entered_at), **review)

class StateTimeoutBudgetTests(unittest.TestCase):
    """A stalled agent is retried on the timeout budget, then failed."""

    def test_timeout_retries_while_the_budget_lasts(self) -> None:
        context = _context('MENXIA_GROUP_ANALYST', recovery=RecoveryState(timeout_retry_count=1, max_timeout_retries=3))
        decision = MenxiaGroupAnalystState().on_timeout(context)
        self.assertEqual(decision.transition.action, 'RETRY')
        self.assertEqual(decision.transition.reason_code, 'AGENT_TIMEOUT')
        self.assertEqual(decision.update.recovery.timeout_retry_count, 2)
        self.assertEqual(decision.update.recovery.last_failure.error_code, 'AGENT_TIMEOUT')
        self.assertEqual(decision.update.progression.resume_state, 'MENXIA_GROUP_ANALYST')

    def test_timeout_fails_once_the_budget_is_spent(self) -> None:
        context = _context('MENXIA_GROUP_ANALYST', recovery=RecoveryState(timeout_retry_count=3, max_timeout_retries=3))
        decision = MenxiaGroupAnalystState().on_timeout(context)
        self.assertEqual(decision.transition.action, 'FAIL')
        self.assertFalse(decision.update.recovery.last_failure.retryable)
if __name__ == '__main__':
    unittest.main()
