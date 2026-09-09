from __future__ import annotations

import unittest

from orchestrator.domain.context import ProgressState, TaskIdentity, WorkflowContext
from orchestrator.domain.decisions import EffectRequest, StateDecision
from orchestrator.domain.errors import InvariantViolation, LeaseLostError
from orchestrator.domain.events import DomainEvent
from orchestrator.runtime.engine import WorkflowEngine
from orchestrator.runtime.repository import CommitResult, WorkflowSnapshot


def _snapshot(sequence: int = 0, version: int = 0) -> WorkflowSnapshot:
    identity = TaskIdentity("task-1", "issue-1", "project-1", "request-1")
    return WorkflowSnapshot(
        "task-1",
        WorkflowContext(
            identity,
            ProgressState("REQUEST_INTAKE", sequence, "2026-09-09T00:00:00Z"),
        ),
        version,
    )


class _Repository:
    def __init__(self, before: WorkflowSnapshot) -> None:
        self.before = before
        self.commits: list[tuple[WorkflowSnapshot, WorkflowSnapshot, StateDecision]] = []
        self.fail_commit: Exception | None = None

    def load(self, task_id: str) -> WorkflowSnapshot:
        return self.before

    def commit_transition(self, before, after, decision) -> CommitResult:
        if self.fail_commit:
            raise self.fail_commit
        self.commits.append((before, after, decision))
        return CommitResult(f"{after.task_id}:{after.context.progression.sequence}", after, ())


class _Lock:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail_acquire: Exception | None = None
        self.fail_release: Exception | None = None

    def acquire(self, task_id: str) -> None:
        self.calls.append(f"acquire:{task_id}")
        if self.fail_acquire:
            raise self.fail_acquire

    def refresh(self, task_id: str) -> None:
        self.calls.append(f"refresh:{task_id}")

    def release(self, task_id: str) -> None:
        self.calls.append(f"release:{task_id}")
        if self.fail_release:
            raise self.fail_release


class _State:
    def __init__(self, decision: StateDecision | None = None, error: Exception | None = None) -> None:
        self.decision = decision or StateDecision()
        self.error = error
        self.calls = 0

    def handle(self, context: WorkflowContext, event: DomainEvent) -> StateDecision:
        self.calls += 1
        if self.error:
            raise self.error
        return self.decision


class _States:
    def __init__(self, state: _State) -> None:
        self.state = state

    def get(self, state_name: str) -> _State:
        return self.state


class _Reducer:
    def __init__(self, after: WorkflowSnapshot | None = None) -> None:
        self.after = after or _snapshot(1, 1)
        self.calls = 0

    def apply(self, snapshot: WorkflowSnapshot, decision: StateDecision) -> WorkflowSnapshot:
        self.calls += 1
        return self.after


class WorkflowEngineTests(unittest.TestCase):
    def test_dispatch_is_linear_and_releases_lock_after_commit(self) -> None:
        repository = _Repository(_snapshot())
        lock = _Lock()
        state = _State()
        reducer = _Reducer()
        engine = WorkflowEngine(repository, _States(state), lock, reducer)
        event = DomainEvent("START", "task-1", 0, {}, "2026-09-09T00:00:00Z")

        decision = engine.dispatch(event)

        self.assertIsInstance(decision, StateDecision)
        self.assertEqual(state.calls, 1)
        self.assertEqual(reducer.calls, 1)
        self.assertEqual(len(repository.commits), 1)
        self.assertEqual(lock.calls, ["acquire:task-1", "release:task-1"])

    def test_state_failure_does_not_commit_but_releases_lock(self) -> None:
        repository = _Repository(_snapshot())
        lock = _Lock()
        engine = WorkflowEngine(
            repository,
            _States(_State(error=ValueError("state failed"))),
            lock,
            _Reducer(),
        )

        with self.assertRaisesRegex(ValueError, "state failed"):
            engine.dispatch(DomainEvent("START", "task-1", 0, {}, "now"))
        self.assertEqual(repository.commits, [])
        self.assertEqual(lock.calls, ["acquire:task-1", "release:task-1"])

    def test_invalid_reducer_output_is_rejected_before_commit(self) -> None:
        repository = _Repository(_snapshot())
        engine = WorkflowEngine(repository, _States(_State()), _Lock(), _Reducer(_snapshot()))

        with self.assertRaises(InvariantViolation):
            engine.dispatch(DomainEvent("START", "task-1", 0, {}, "now"))
        self.assertEqual(repository.commits, [])

    def test_stale_event_or_effect_intent_is_rejected_before_commit(self) -> None:
        repository = _Repository(_snapshot())
        engine = WorkflowEngine(repository, _States(_State()), _Lock(), _Reducer())

        with self.assertRaises(InvariantViolation):
            engine.dispatch(DomainEvent("START", "task-1", 1, {}, "now"))
        self.assertEqual(repository.commits, [])

        class _EffectState(_State):
            def handle(self, context, event):
                return StateDecision(
                    effects=(
                        EffectRequest("effect-1", "dispatch", "other-task", "idem-1"),
                    )
                )

        engine = WorkflowEngine(repository, _States(_EffectState()), _Lock(), _Reducer())
        with self.assertRaises(InvariantViolation):
            engine.dispatch(DomainEvent("START", "task-1", 0, {}, "now"))
        self.assertEqual(repository.commits, [])

    def test_commit_and_lock_failures_are_not_swallowed(self) -> None:
        repository = _Repository(_snapshot())
        repository.fail_commit = RuntimeError("commit failed")
        lock = _Lock()
        engine = WorkflowEngine(repository, _States(_State()), lock, _Reducer())
        with self.assertRaisesRegex(RuntimeError, "commit failed"):
            engine.dispatch(DomainEvent("START", "task-1", 0, {}, "now"))
        self.assertEqual(lock.calls, ["acquire:task-1", "release:task-1"])

        failing_lock = _Lock()
        failing_lock.fail_acquire = LeaseLostError("lease lost")
        engine = WorkflowEngine(repository, _States(_State()), failing_lock, _Reducer())
        with self.assertRaises(LeaseLostError):
            engine.dispatch(DomainEvent("START", "task-1", 0, {}, "now"))
        self.assertEqual(failing_lock.calls, ["acquire:task-1"])


if __name__ == "__main__":
    unittest.main()
