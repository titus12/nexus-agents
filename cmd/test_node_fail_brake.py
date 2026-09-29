"""Fix 5: deterministic-failure brake on same-state node-FAIL streaks.

Two back-to-back wave failures inside one workflow state mean the same
payload keeps producing the same outcome (incident task-20260928-835a07:
waves 6/8/10/12 re-ran a solver revision that could never finish inside
its dispatch deadline).  The second consecutive FAIL escalates to
HUMAN_GATE instead of spending another budgeted wave.
"""

from __future__ import annotations

import os
import tempfile
import unittest

from orchestrator.domain.context import (
    HumanGateState,
    ProgressState,
    RecoveryState,
    RecoveryUpdate,
    TaskIdentity,
    WorkflowContext,
    context_from_dto,
    context_to_dto,
)
from orchestrator.domain.decisions import ContextUpdate, StateDecision
from orchestrator.domain.errors import FailureRecord
from orchestrator.domain.events import DomainEvent
from orchestrator.domain.policies.prompts import retry_feedback
from orchestrator.domain.states import HumanGateWorkflowState, ZhongshuSolverState
from orchestrator.domain.transitions import TransitionRegistry
from orchestrator.runtime.compat_effects import FileArtifactStore
from orchestrator.runtime.migration import legacy_dto_to_snapshot
from orchestrator.runtime.reducer import LinearContextReducer
from orchestrator.runtime.repository import WorkflowSnapshot

os.environ.setdefault("NEXUS_TEST_NO_EXTERNAL_NOTIFICATIONS", "1")


def _context(**recovery) -> WorkflowContext:
    return WorkflowContext(
        identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
        progression=ProgressState("ZHONGSHU_SOLVER", 3, "2026-09-28T00:00:00Z"),
        recovery=RecoveryState(**recovery),
    )


def _fail_event(
    sequence: int = 4,
    error_code: str = "AGENT_TIMEOUT",
) -> DomainEvent:
    return DomainEvent(
        name="FAIL",
        task_id="task-1",
        sequence=sequence,
        payload={
            "retryable": True,
            "error_code": error_code,
            "reason": f"wave failed: {error_code}",
        },
        occurred_at="2026-09-28T00:01:00Z",
        event_id=f"evt-fail-{sequence}",
    )


def _completed_event() -> DomainEvent:
    return DomainEvent(
        name="NODE_COMPLETED",
        task_id="task-1",
        sequence=6,
        payload={"action": "READY_FOR_CRITIC"},
        occurred_at="2026-09-28T00:02:00Z",
        event_id="evt-done-6",
    )


class NodeFailStreakTests(unittest.TestCase):
    """The brake escalates the second consecutive FAIL in a state."""

    def test_first_fail_retries_and_persists_the_streak(self) -> None:
        decision = ZhongshuSolverState().handle(_context(), _fail_event())

        self.assertEqual(decision.transition.action, "RETRY")
        self.assertIsNotNone(decision.update.recovery)
        self.assertEqual(decision.update.recovery.node_fail_streak, 1)
        self.assertEqual(
            decision.update.recovery.node_fail_state, "ZHONGSHU_SOLVER"
        )
        # AGENT_TIMEOUT is an infrastructure failure: charged to the
        # external budget exactly as before.
        self.assertEqual(decision.update.recovery.external_retry_count, 1)

    def test_second_consecutive_fail_escalates_to_human_gate(self) -> None:
        context = _context(
            node_fail_streak=1, node_fail_state="ZHONGSHU_SOLVER"
        )

        decision = ZhongshuSolverState().handle(context, _fail_event())

        self.assertEqual(decision.transition.action, "HUMAN_GATE")
        self.assertEqual(
            decision.transition.reason_code, "NODE_FAIL_STREAK_EXHAUSTED"
        )
        self.assertIsNotNone(decision.update.recovery)
        self.assertEqual(decision.update.recovery.node_fail_streak, 2)
        self.assertEqual(
            decision.update.recovery.node_fail_state, "ZHONGSHU_SOLVER"
        )
        self.assertEqual(
            decision.update.recovery.blocked_reason, "NODE_FAIL_STREAK_EXHAUSTED"
        )
        # The operator gate must record where the run resumes from.
        self.assertEqual(
            decision.update.progression.resume_state, "ZHONGSHU_SOLVER"
        )
        self.assertIsNotNone(decision.update.human_gate)
        self.assertEqual(
            decision.update.human_gate.resume_state, "ZHONGSHU_SOLVER"
        )
        self.assertEqual(
            decision.update.human_gate.reason_code, "NODE_FAIL_STREAK_EXHAUSTED"
        )
        self.assertIsNotNone(decision.update.recovery.last_failure)
        self.assertEqual(
            decision.update.recovery.last_failure.error_code, "AGENT_TIMEOUT"
        )

    def test_streak_ignores_the_error_code(self) -> None:
        # Wave 6 failed the fold (reply class), wave 8 timed out
        # (infrastructure class): still one state failing deterministically.
        context = _context(
            node_fail_streak=1, node_fail_state="ZHONGSHU_SOLVER"
        )

        decision = ZhongshuSolverState().handle(
            context, _fail_event(error_code="NODE_ITEM_REVISION_INVALID")
        )

        self.assertEqual(decision.transition.action, "HUMAN_GATE")

    def test_success_resets_the_streak(self) -> None:
        context = _context(
            node_fail_streak=1, node_fail_state="ZHONGSHU_SOLVER"
        )

        decision = ZhongshuSolverState().handle(context, _completed_event())

        self.assertEqual(decision.transition.action, "READY_FOR_CRITIC")
        self.assertEqual(decision.update.recovery.node_fail_streak, 0)
        self.assertEqual(decision.update.recovery.node_fail_state, "")

    def test_streak_is_state_keyed(self) -> None:
        context = _context(
            node_fail_streak=1, node_fail_state="ZHONGSHU_ANALYST"
        )

        decision = ZhongshuSolverState().handle(context, _fail_event())

        self.assertEqual(decision.transition.action, "RETRY")
        self.assertEqual(decision.update.recovery.node_fail_streak, 1)
        self.assertEqual(
            decision.update.recovery.node_fail_state, "ZHONGSHU_SOLVER"
        )

    def test_zero_limit_disables_the_brake(self) -> None:
        context = _context(
            node_fail_streak=5,
            node_fail_state="ZHONGSHU_SOLVER",
            max_node_fail_streak=0,
        )

        decision = ZhongshuSolverState().handle(context, _fail_event())

        self.assertEqual(decision.transition.action, "RETRY")

    def test_brake_precedes_the_budget_routing(self) -> None:
        # With the brake tripped the external budget is not charged: the
        # operator decides, the retry machinery stays untouched.
        context = _context(
            node_fail_streak=1,
            node_fail_state="ZHONGSHU_SOLVER",
            external_retry_count=0,
        )

        decision = ZhongshuSolverState().handle(context, _fail_event())

        self.assertEqual(decision.transition.action, "HUMAN_GATE")
        # None = "not provided": the reducer keeps the existing count, so
        # the tripped brake never charges the external budget.
        self.assertIsNone(decision.update.recovery.external_retry_count)


class StaleFailureCleanupTests(unittest.TestCase):
    """Clean joins drop the stale [Retry feedback] a later re-ask inherits."""

    def _failure(self, state: str = "ZHONGSHU_SOLVER") -> FailureRecord:
        return FailureRecord(
            failure_id="failure-1",
            stage="fold",
            owner_component="node_effects",
            task_id="task-1",
            state=state,
            sequence=3,
            node_run_id="run-1",
            worker_id="worker-1",
            effect_id="effect-1",
            error_code="SOLVER_PLAN_STRUCTURE_INVALID:PLAN_HAS_NO_ITEMS",
            retryable=True,
            message="the folded plan has no items",
            cause_type="validation",
        )

    def test_success_clears_stale_failure_feedback(self) -> None:
        context = WorkflowContext(
            identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
            progression=ProgressState(
                "ZHONGSHU_SOLVER", 3, "2026-09-28T00:00:00Z"
            ),
            recovery=RecoveryState(last_failure=self._failure()),
        )
        # The stale failure would leak into the next re-ask (the feedback
        # restates the rejection message, not the raw error code)...
        self.assertIn(
            "folded plan has no items",
            retry_feedback(context, "ZHONGSHU_SOLVER"),
        )

        decision = ZhongshuSolverState().handle(context, _completed_event())

        self.assertTrue(decision.update.recovery.clear_last_failure)
        snapshot = LinearContextReducer().apply(
            WorkflowSnapshot("task-1", context, 3), decision
        )
        # ...but after the clean join it is gone.
        self.assertIsNone(snapshot.context.recovery.last_failure)
        self.assertEqual(
            retry_feedback(snapshot.context, "ZHONGSHU_SOLVER"), ""
        )

    def test_explicit_new_failure_beats_the_clear_sentinel(self) -> None:
        context = _context()
        update = RecoveryUpdate(
            last_failure=self._failure("ZHONGSHU_ANALYST"),
            clear_last_failure=True,
        )

        snapshot = LinearContextReducer().apply(
            WorkflowSnapshot("task-1", context, 3),
            StateDecision(
                update=ContextUpdate(
                    recovery=RecoveryUpdate(
                        last_failure=self._failure("ZHONGSHU_ANALYST"),
                        clear_last_failure=True,
                    )
                )
            ),
        )

        self.assertIsNotNone(snapshot.context.recovery.last_failure)
        self.assertEqual(
            snapshot.context.recovery.last_failure.state, "ZHONGSHU_ANALYST"
        )

    def test_failure_update_without_sentinel_keeps_prior_failure(self) -> None:
        context = WorkflowContext(
            identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
            progression=ProgressState(
                "ZHONGSHU_SOLVER", 3, "2026-09-28T00:00:00Z"
            ),
            recovery=RecoveryState(last_failure=self._failure()),
        )

        snapshot = LinearContextReducer().apply(
            WorkflowSnapshot("task-1", context, 3),
            StateDecision(
                update=ContextUpdate(
                    recovery=RecoveryUpdate(reply_retry_count=1)
                )
            ),
        )

        self.assertIsNotNone(snapshot.context.recovery.last_failure)


class NodeFailStreakReducerTests(unittest.TestCase):
    """The streak survives the reducer merge and the snapshot roundtrip."""

    def test_reducer_persists_the_streak_fields(self) -> None:
        context = _context()
        snapshot = WorkflowSnapshot("task-1", context, 3)
        reducer = LinearContextReducer()
        decision = ZhongshuSolverState().handle(context, _fail_event())

        next_snapshot = reducer.apply(snapshot, decision)

        recovery = next_snapshot.context.recovery
        self.assertEqual(recovery.node_fail_streak, 1)
        self.assertEqual(recovery.node_fail_state, "ZHONGSHU_SOLVER")

    def test_dto_roundtrip_preserves_the_streak(self) -> None:
        context = _context(
            node_fail_streak=2, node_fail_state="ZHONGSHU_SOLVER"
        )

        restored = context_from_dto(context_to_dto(context))

        self.assertEqual(restored.recovery.node_fail_streak, 2)
        self.assertEqual(restored.recovery.node_fail_state, "ZHONGSHU_SOLVER")

    def test_dto_without_the_fields_keeps_the_defaults(self) -> None:
        dto = context_to_dto(_context())
        dto["recovery"].pop("node_fail_streak")
        dto["recovery"].pop("node_fail_state")
        dto["recovery"].pop("max_node_fail_streak")

        restored = context_from_dto(dto)

        self.assertEqual(restored.recovery.node_fail_streak, 0)
        self.assertEqual(restored.recovery.node_fail_state, "")
        self.assertEqual(restored.recovery.max_node_fail_streak, 2)


class NodeFailStreakMigrationTests(unittest.TestCase):
    """Legacy snapshots keep the brake counters across restore."""

    @staticmethod
    def _legacy(**extra) -> dict:
        value: dict = {
            "task_id": "task-1",
            "issue_id": "issue-1",
            "workflow_state": "ZHONGSHU_SOLVER",
            "sequence": 4,
            "raw_request": "review",
            "request_payload": {"requirements": ["r1"]},
            "findings": [],
        }
        value.update(extra)
        return value

    def test_legacy_restore_reads_the_streak_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            snapshot = legacy_dto_to_snapshot(
                self._legacy(
                    node_fail_streak=2,
                    node_fail_state="ZHONGSHU_SOLVER",
                    max_node_fail_streak=5,
                ),
                artifact_port=FileArtifactStore(directory),
            )

        recovery = snapshot.context.recovery
        self.assertEqual(recovery.node_fail_streak, 2)
        self.assertEqual(recovery.node_fail_state, "ZHONGSHU_SOLVER")
        self.assertEqual(recovery.max_node_fail_streak, 5)

    def test_legacy_restore_defaults_when_fields_absent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            snapshot = legacy_dto_to_snapshot(
                self._legacy(), artifact_port=FileArtifactStore(directory)
            )

        recovery = snapshot.context.recovery
        self.assertEqual(recovery.node_fail_streak, 0)
        self.assertEqual(recovery.node_fail_state, "")
        self.assertEqual(recovery.max_node_fail_streak, 2)


class HumanGateResumeTests(unittest.TestCase):
    """The brake's gate resumes through the standard operator path."""

    def test_operator_resume_returns_to_the_failed_state(self) -> None:
        gated = WorkflowContext(
            identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
            progression=ProgressState(
                "HUMAN_GATE",
                5,
                "2026-09-28T00:03:00Z",
                resume_state="ZHONGSHU_SOLVER",
            ),
            recovery=RecoveryState(
                node_fail_streak=2,
                node_fail_state="ZHONGSHU_SOLVER",
                max_node_fail_streak=2,
            ),
            human_gate=HumanGateState(
                decision_id="task-1:human-gate:5",
                reason_code="NODE_FAIL_STREAK_EXHAUSTED",
                resume_state="ZHONGSHU_SOLVER",
            ),
        )

        decision = HumanGateWorkflowState().handle(
            gated,
            DomainEvent(
                name="RESUME",
                task_id="task-1",
                sequence=6,
                payload={"answer": "继续"},
                occurred_at="2026-09-28T00:04:00Z",
                event_id="evt-resume-6",
            ),
        )

        self.assertEqual(decision.transition.action, "RESUME")
        self.assertIsNotNone(decision.update.human_gate)
        self.assertTrue(decision.update.human_gate.clear_gate)
        # The existing recovery path routes RESUME back to the recorded state.
        self.assertEqual(
            TransitionRegistry.default().target_for(
                "HUMAN_GATE", "RESUME", "ZHONGSHU_SOLVER"
            ),
            "ZHONGSHU_SOLVER",
        )


if __name__ == "__main__":
    unittest.main()
