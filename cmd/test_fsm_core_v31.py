from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from orchestrator.context import StateContext
from orchestrator.events import Event
from orchestrator.locks import TaskLock, TaskLockError
from orchestrator.adapters import FakeMulticaAdapter
from orchestrator.models import Finding, ExternalMessage
from orchestrator.persistence import JsonStateStore
from orchestrator.states import build_state_registry
from orchestrator.state_machine import StateMachine
from orchestrator.transitions import TransitionError, TransitionPolicy
from test_analyst_contract_v31 import VALID_PLAN


class DummyState:
    def __init__(self, name: str, calls: list[tuple[str, str]]):
        self.name = name
        self.calls = calls

    def enter(self, ctx: StateContext) -> None:
        self.calls.append((self.name, "enter"))

    def update(self, ctx: StateContext) -> Event:
        self.calls.append((self.name, "update"))
        return Event("NOOP")

    def exit(self, ctx: StateContext, event: Event) -> None:
        self.calls.append((self.name, "exit"))


class FsmCoreTests(unittest.TestCase):
    def test_solver_reply_transitions_to_critic(self):
        ctx = StateContext(task_id="task-1", workflow_state="ZHONGSHU_SOLVER")
        transition = TransitionPolicy.resolve(
            ctx,
            Event("AGENT_REPLY_ACCEPTED", {"action": "READY_FOR_CRITIC"}),
        )
        self.assertEqual(transition.to_state, "ZHONGSHU_CRITIC")

    def test_solver_evidence_gap_returns_to_analyst(self):
        ctx = StateContext(task_id="task-1", workflow_state="ZHONGSHU_SOLVER")
        for action in ("REQUEST_ANALYST_EVIDENCE", "NEEDS_MORE_EVIDENCE"):
            transition = TransitionPolicy.resolve(
                ctx,
                Event("AGENT_REPLY_ACCEPTED", {"action": action}),
            )
            self.assertEqual(transition.to_state, "ZHONGSHU_ANALYST")

    def test_solver_unresolved_revision_routes_to_analyst(self):
        ctx = StateContext(task_id="task-1", workflow_state="ZHONGSHU_SOLVER")
        transition = TransitionPolicy.resolve(
            ctx,
            Event("AGENT_REPLY_ACCEPTED", {
                "action": "REQUEST_ANALYST_EVIDENCE",
                "finding_ids": ["finding-1"],
            }),
        )
        self.assertEqual(transition.to_state, "ZHONGSHU_ANALYST")

    def test_agent_binding_changes_with_fsm_role(self):
        from orchestrator.adapters import FakeMulticaAdapter
        from orchestrator.states import build_state_registry

        ctx = StateContext(task_id="task-1", workflow_state="REQUEST_INTAKE")
        states = build_state_registry(
            multica=FakeMulticaAdapter(),
            agent_ids={
                "review-analyst": "analyst-1",
                "review-solver": "solver-1",
                "review-critic": "critic-1",
            },
        )
        machine = StateMachine(ctx, states)
        machine.start()
        machine.dispatch(Event("AGENT_REPLY_ACCEPTED", {"action": "START"}))
        self.assertEqual(ctx.expected_agent_id, "analyst-1")
        ctx.request_payload["analyst_plan"] = VALID_PLAN
        machine.dispatch(Event("AGENT_REPLY_ACCEPTED", {"action": "READY_FOR_SOLVER", "plan": VALID_PLAN}))
        self.assertEqual(ctx.expected_agent_id, "solver-1")
        machine.dispatch(Event("AGENT_REPLY_ACCEPTED", {"action": "READY_FOR_CRITIC"}))
        self.assertEqual(ctx.expected_agent_id, "critic-1")

    def test_critic_blocked_with_actionable_findings_routes_to_solver(self):
        ctx = StateContext(
            task_id="task-1",
            workflow_state="ZHONGSHU_CRITIC",
            active_finding_ids=["finding-1"],
        )
        transition = TransitionPolicy.resolve(
            ctx,
            Event("AGENT_REPLY_ACCEPTED", {
                "action": "BLOCKED",
                "action_normalized": True,
            }),
        )
        self.assertEqual(transition.to_state, "ZHONGSHU_SOLVER")

    def test_critic_approve_freeze_with_deferred_findings_reaches_freeze_check(self):
        ctx = StateContext(
            task_id="task-1",
            workflow_state="ZHONGSHU_CRITIC",
        )
        deferred = Finding.from_dict({
            "finding_id": "finding-deferred",
            "severity": "P2",
            "decision": "DEFERRED",
        })
        self.assertEqual(deferred.status, "DEFERRED")
        self.assertFalse(deferred.active)
        ctx.replace_findings([deferred])
        transition = TransitionPolicy.resolve(
            ctx,
            Event("AGENT_REPLY_ACCEPTED", {
                "action": "APPROVE_FREEZE",
                "action_normalized": True,
            }),
        )
        self.assertEqual(transition.to_state, "ZHONGSHU_FREEZE_CHECK")

    def test_human_decision_restores_resume_state(self):
        ctx = StateContext(task_id="task-1", workflow_state="HUMAN_GATE")
        transition = TransitionPolicy.resolve(
            ctx,
            Event("HUMAN_DECISION_RECEIVED", {"resume_state": "ZHONGSHU_CRITIC"}),
        )
        self.assertEqual(transition.to_state, "ZHONGSHU_CRITIC")

    def test_freeze_retry_has_circuit_breaker(self):
        ctx = StateContext(
            task_id="task-1",
            workflow_state="ZHONGSHU_FREEZE_CHECK",
            freeze_check_attempt=2,
            max_freeze_check_attempts=2,
        )
        transition = TransitionPolicy.resolve(
            ctx,
            Event("FREEZE_REJECTED"),
        )
        self.assertEqual(transition.to_state, "BLOCKED")

    def test_item_revision_has_circuit_breaker(self):
        ctx = StateContext(
            task_id="task-1",
            workflow_state="MENXIA_ITEM_CRITIC",
            item_revision_round=3,
            max_item_revision_rounds=3,
        )
        transition = TransitionPolicy.resolve(
            ctx,
            Event("AGENT_REPLY_ACCEPTED", {"action": "REVISE_ITEM"}),
        )
        self.assertEqual(transition.to_state, "BLOCKED")

    def test_blocked_transition_populates_top_level_reason(self):
        ctx = StateContext(
            task_id="task-1",
            workflow_state="ZHONGSHU_CRITIC",
        )
        calls: list[tuple[str, str]] = []
        states = {
            "ZHONGSHU_CRITIC": DummyState("ZHONGSHU_CRITIC", calls),
            "BLOCKED": DummyState("BLOCKED", calls),
        }
        machine = StateMachine(ctx, states)
        machine.dispatch(
            Event(
                "AGENT_REPLY_ACCEPTED",
                {
                    "action": "BLOCKED",
                    "notification": "result transport failed",
                },
            )
        )
        self.assertEqual(ctx.workflow_state, "BLOCKED")
        self.assertEqual(ctx.blocked_reason, "result transport failed")

    def test_item_critic_cannot_approve_with_unresolved_p1(self):
        ctx = StateContext(
            task_id="task-1",
            workflow_state="MENXIA_ITEM_CRITIC",
            findings=[{
                "finding_id": "f-1",
                "severity": "P1",
                "status": "OPEN",
            }],
        )
        transition = TransitionPolicy.resolve(
            ctx,
            Event("AGENT_REPLY_ACCEPTED", {"action": "APPROVE_ITEM"}),
        )
        self.assertEqual(transition.to_state, "MENXIA_ITEM_SOLVER")

    def test_item_critic_requires_human_decision_for_unresolved_p2(self):
        ctx = StateContext(
            task_id="task-1",
            workflow_state="MENXIA_ITEM_CRITIC",
            findings=[{
                "finding_id": "f-2",
                "severity": "P2",
                "status": "OPEN",
            }],
        )
        event = Event("AGENT_REPLY_ACCEPTED", {"action": "APPROVE_ITEM"})
        transition = TransitionPolicy.resolve(ctx, event)
        self.assertEqual(transition.to_state, "HUMAN_GATE")
        self.assertIn("options", event.payload["human_gate"])

    def test_state_machine_calls_exit_before_enter(self):
        calls: list[tuple[str, str]] = []
        states = {
            "ZHONGSHU_SOLVER": DummyState("solver", calls),
            "ZHONGSHU_CRITIC": DummyState("critic", calls),
        }
        ctx = StateContext(task_id="task-1", workflow_state="ZHONGSHU_SOLVER")
        machine = StateMachine(ctx, states)
        machine.start()
        machine.dispatch(Event("AGENT_REPLY_ACCEPTED", {"action": "READY_FOR_CRITIC"}))
        self.assertEqual(
            calls,
            [("solver", "enter"), ("solver", "exit"), ("critic", "enter")],
        )

    def test_state_machine_rejects_unregistered_dynamic_target(self):
        calls: list[tuple[str, str]] = []
        states = {
            "MENXIA_GROUP_GATE": DummyState("gate", calls),
            "DONE": DummyState("done", calls),
        }
        ctx = StateContext(task_id="task-1", workflow_state="MENXIA_GROUP_GATE")
        machine = StateMachine(ctx, states)
        with self.assertRaises(TransitionError):
            machine.dispatch(Event(
                "AGENT_REPLY_ACCEPTED",
                {"action": "APPROVE_GROUP", "next_state": "NOT_A_STATE"},
            ))
        self.assertEqual(ctx.workflow_state, "MENXIA_GROUP_GATE")
        self.assertEqual(calls, [])

    def test_state_store_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            store = JsonStateStore(directory)
            ctx = StateContext(task_id="task-1", workflow_state="ZHONGSHU_SOLVER", sequence=4)
            store.save_state(ctx)
            loaded = store.load()
            self.assertEqual(loaded.task_id, "task-1")
            self.assertEqual(loaded.sequence, 4)

    def test_task_lock_is_exclusive(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "task.lock"
            first = TaskLock(path)
            second = TaskLock(path)
            first.acquire()
            try:
                with self.assertRaises(TaskLockError):
                    second.acquire()
            finally:
                first.release()

    def test_unacquired_lock_release_cannot_delete_owner_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "task.lock"
            first = TaskLock(path)
            second = TaskLock(path)
            first.acquire()
            try:
                second.release()
                self.assertTrue(path.exists())
                self.assertTrue(first.meta_path.exists())
            finally:
                first.release()

    def test_corrupt_lock_metadata_is_not_reclaimed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "task.lock"
            path.write_text("", encoding="utf-8")
            path.with_suffix(path.suffix + ".json").write_text(
                '{"lease_until":"not-a-number","pid":"not-a-pid"}',
                encoding="utf-8",
            )
            with self.assertRaises(TaskLockError):
                TaskLock(path).acquire()

    def test_finding_status_is_case_normalized(self):
        self.assertTrue(Finding.from_dict({
            "finding_id": "f-open",
            "severity": "P1",
            "status": "open",
        }).active)
        self.assertTrue(Finding.from_dict({
            "finding_id": "f-resolved",
            "severity": "P1",
            "status": "resolved",
        }).resolved)

    def test_illegal_transition_is_rejected(self):
        ctx = StateContext(task_id="task-1", workflow_state="DONE")
        with self.assertRaises(TransitionError):
            TransitionPolicy.resolve(ctx, Event("START"))

    def test_dispatch_intent_is_persisted_before_external_dispatch(self):
        calls: list[tuple[str, str]] = []
        adapter = FakeMulticaAdapter()

        def persist(ctx, _event, _transition):
            calls.append((ctx.dispatch_status, ctx.active_request_id))

        ctx = StateContext(
            task_id="task-1",
            workflow_state="ZHONGSHU_SOLVER",
            expected_agent_id="solver-1",
            raw_request="draft",
            request_payload={"analyst_plan": VALID_PLAN},
        )
        machine = StateMachine(
            ctx,
            build_state_registry(multica=adapter),
            persist=persist,
        )
        machine.start()
        self.assertEqual(len(adapter.dispatched), 1)
        self.assertIn(("pending", ctx.active_request_id), calls)
        self.assertEqual(ctx.dispatch_status, "confirmed")

    def test_findings_merge_by_id_without_losing_unmodified_findings(self):
        ctx = StateContext(task_id="task-1")
        ctx.merge_findings([
            Finding("f-1", "P1", owner_role="review-solver"),
            Finding("f-2", "P2"),
        ])
        ctx.merge_findings([
            Finding("f-1", "P1", status="RESOLVED", resolution="accepted"),
        ])
        self.assertEqual(ctx.active_finding_ids, ["f-2"])
        self.assertEqual(ctx.resolved_finding_ids, ["f-1"])
        self.assertEqual(len(ctx.findings), 2)

    def test_pending_dispatch_recovery_does_not_duplicate_request(self):
        with tempfile.TemporaryDirectory() as directory:
            store = JsonStateStore(directory)
            adapter = FakeMulticaAdapter()
            ctx = StateContext(
                task_id="task-1",
                workflow_state="ZHONGSHU_SOLVER",
                expected_agent_id="solver-1",
                active_request_id="task-1:ZHONGSHU_SOLVER:0:req",
                dispatch_idempotency_key="task-1:ZHONGSHU_SOLVER:0",
                dispatch_status="pending",
            )
            store.save_state(ctx)
            adapter.dispatch(
                __import__("orchestrator.models", fromlist=["AgentRequest"]).AgentRequest(
                    task_id=ctx.task_id,
                    request_id=ctx.active_request_id,
                    agent_id=ctx.expected_agent_id,
                    role="review-solver",
                    phase="ZHONGSHU",
                    prompt="draft",
                    idempotency_key=ctx.dispatch_idempotency_key,
                )
            )
            from orchestrator.recovery import RecoveryManager
            from orchestrator.models import AgentRequest

            result = RecoveryManager(store).reconcile_dispatch(
                ctx,
                adapter,
                AgentRequest(
                    task_id=ctx.task_id,
                    request_id=ctx.active_request_id,
                    agent_id=ctx.expected_agent_id,
                    role="review-solver",
                    phase="ZHONGSHU",
                    prompt="draft",
                    idempotency_key=ctx.dispatch_idempotency_key,
                ),
            )
            self.assertEqual(result.action, "reconcile")
            self.assertEqual(len(adapter.dispatched), 1)


if __name__ == "__main__":
    unittest.main()

