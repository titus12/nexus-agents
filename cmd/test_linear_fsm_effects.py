from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError

from orchestrator.domain.decisions import EffectRequest
from orchestrator.domain.errors import InvariantViolation, TransportError
from orchestrator.runtime.effects import EffectManager, EffectRunnerNotFound
from orchestrator.runtime.ports import (
    AgentRequest,
    ArtifactInput,
    DispatchReceipt,
    NotificationRequest,
    PollRequest,
)
from orchestrator.runtime.repository import EffectRecord, EffectResult


class _Repository:
    def __init__(self, effects: tuple[EffectRecord, ...]) -> None:
        self.effects = effects
        self.running: list[str] = []
        self.results: list[EffectResult] = []

    def pending_effects(self, task_id: str) -> tuple[EffectRecord, ...]:
        return tuple(effect for effect in self.effects if effect.task_id == task_id)

    def mark_effect_running(self, effect_id: str) -> None:
        self.running.append(effect_id)

    def commit_effect_result(self, result: EffectResult) -> None:
        self.results.append(result)


class _Runner:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.requests: list[EffectRequest] = []

    def run_once(self, request: EffectRequest) -> object:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return object()


def _effect(effect_id: str = "effect-1", effect_type: str = "dispatch") -> EffectRecord:
    request = EffectRequest(
        effect_id=effect_id,
        effect_type=effect_type,
        task_id="task-1",
        idempotency_key=f"idem-{effect_id}",
        payload_ref="payload-1",
    )
    return EffectRecord(
        effect_id,
        "task-1",
        request,
        "PENDING",
        0,
        "ZHONGSHU_SOLVER",
        5,
    )


class EffectManagerTests(unittest.TestCase):
    def test_success_marks_running_and_commits_terminal_result(self) -> None:
        repository = _Repository((_effect(),))
        runner = _Runner()

        results = EffectManager(repository, {"dispatch": runner}).execute_pending("task-1")

        self.assertEqual(["effect-1"], repository.running)
        self.assertEqual(results, (EffectResult("effect-1", "task-1", "SUCCEEDED"),))
        self.assertEqual(repository.results, list(results))
        self.assertEqual(runner.requests, [_effect().request])

    def test_typed_runner_failure_is_committed_with_attribution(self) -> None:
        repository = _Repository((_effect(),))
        runner = _Runner(TransportError("gateway unavailable"))

        results = EffectManager(repository, {"dispatch": runner}).execute_pending("task-1")

        failure = results[0].failure
        self.assertIsNotNone(failure)
        assert failure is not None
        self.assertEqual(results[0].status, "FAILED")
        self.assertEqual(failure.stage, "effect")
        self.assertEqual(failure.owner_component, "dispatch")
        self.assertEqual(failure.effect_id, "effect-1")
        self.assertEqual(failure.state, "ZHONGSHU_SOLVER")
        self.assertEqual(failure.sequence, 5)
        self.assertEqual(failure.error_code, "TRANSPORT_ERROR")
        self.assertEqual(failure.message, "gateway unavailable")
        self.assertEqual(repository.results, list(results))

    def test_unknown_runner_is_explicit_and_effect_stays_unstarted(self) -> None:
        repository = _Repository((_effect(effect_type="missing"),))

        with self.assertRaises(EffectRunnerNotFound) as raised:
            EffectManager(repository, {}).execute_pending("task-1")

        self.assertEqual(raised.exception.error_code, "EFFECT_RUNNER_NOT_FOUND")
        self.assertEqual(repository.running, [])
        self.assertEqual(repository.results, [])

    def test_unknown_runner_error_is_failed_and_preserves_original_cause_type(self) -> None:
        repository = _Repository((_effect(),))
        runner = _Runner(RuntimeError("bug in runner"))

        result = EffectManager(repository, {"dispatch": runner}).execute_pending("task-1")[0]

        self.assertEqual(result.status, "FAILED")
        assert result.failure is not None
        self.assertEqual(result.failure.error_code, "INVARIANT_VIOLATION")
        self.assertEqual(result.failure.cause_type, "RuntimeError")
        self.assertIn("bug in runner", result.failure.message)

    def test_empty_and_duplicate_pending_effects_are_safe(self) -> None:
        repository = _Repository((_effect(), _effect()))
        runner = _Runner()

        results = EffectManager(repository, {"dispatch": runner}).execute_pending("task-1")
        empty = EffectManager(_Repository(()), {"dispatch": runner}).execute_pending("task-1")

        self.assertEqual(len(results), 1)
        self.assertEqual(runner.requests, [_effect().request])
        self.assertEqual(empty, ())


class PortContractTests(unittest.TestCase):
    def test_port_dtos_are_frozen(self) -> None:
        records = (
            AgentRequest("task", "request", "agent", "payload"),
            PollRequest("task", "request", "operation"),
            DispatchReceipt("operation", "message", True),
            NotificationRequest("task", "notification", "body"),
            ArtifactInput("task", "name", b"content"),
        )
        for record in records:
            with self.assertRaises(FrozenInstanceError):
                record.task_id = "changed"  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
