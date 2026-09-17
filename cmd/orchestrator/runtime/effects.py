"""Durable execution lifecycle for externally performed effects."""

from __future__ import annotations

from dataclasses import dataclass, replace
from collections.abc import Mapping
from typing import Protocol
import uuid

from ..domain.decisions import EffectRequest
from ..domain.errors import (
    CONTRACT_REJECTED_EVENT,
    DomainError,
    FailureRecord,
    InvariantViolation,
    UNSTRUCTURED_REPLY_EVENT,
)
from .event_inbox import effect_result_event
from .repository import EffectRecord, EffectResult, WorkflowRepository


# Synthetic actions a transport may use for a delivered-but-unusable reply: a body
# that is not the required JSON contract at all, or JSON that the role validator
# refused.  Neither is a domain action, so a dispatch must never surface either as
# a completed event; both become a retryable reply-shape failure so the workflow
# re-asks the agent instead of reporting a missing result.
REPLY_EVENT_FAILURES = {
    UNSTRUCTURED_REPLY_EVENT: (
        "AGENT_REPLY_UNSTRUCTURED",
        "agent reply was not the required JSON result contract",
    ),
    CONTRACT_REJECTED_EVENT: (
        "AGENT_REPLY_CONTRACT_REJECTED",
        "agent reply violated the role result contract",
    ),
}


class EffectRunner(Protocol):
    """Executes exactly one external operation for an effect request."""

    def run_once(self, request: EffectRequest) -> object: ...


class EffectRunnerNotFound(InvariantViolation):
    """Raised when an effect type has no registered external runner."""

    error_code = "EFFECT_RUNNER_NOT_FOUND"


@dataclass(frozen=True)
class EffectOutcome:
    """Optional richer result returned by an external effect runner."""

    status: str = "SUCCEEDED"
    event_name: str | None = None
    event_payload: object | None = None
    request_id: str | None = None
    operation_id: str | None = None
    deadline_at: str | None = None
    failure: FailureRecord | None = None


class EffectManager:
    """Own effect lifecycle persistence without making workflow decisions."""

    def __init__(
        self,
        repository: WorkflowRepository,
        runners: Mapping[str, EffectRunner],
    ) -> None:
        self._repository = repository
        self._runners = dict(runners)

    def execute_pending(self, task_id: str) -> tuple[EffectResult, ...]:
        """Execute each distinct pending effect and persist its terminal result.

        A runner failure is converted into a durable failed result.  Repository
        failures are deliberately outside the runner boundary and propagate to
        the caller, because losing a lifecycle record must never be hidden.
        """

        results: list[EffectResult] = []
        for effect in self._deduplicate(self._repository.pending_effects(task_id)):
            runner = self._runner_for(effect)
            self._repository.mark_effect_running(effect.effect_id)
            result = self._run(effect, runner)
            self._repository.commit_effect_result(result)
            event = effect_result_event(result, effect)
            if event is not None and hasattr(self._repository, "append_domain_event"):
                self._repository.append_domain_event(event)
            results.append(result)
        return tuple(results)

    def _runner_for(self, effect: EffectRecord) -> EffectRunner:
        effect_type = effect.request.effect_type
        try:
            return self._runners[effect_type]
        except KeyError as error:
            raise EffectRunnerNotFound(
                f"no effect runner registered for {effect_type!r}"
            ) from error

    @staticmethod
    def _deduplicate(effects: tuple[EffectRecord, ...]) -> tuple[EffectRecord, ...]:
        unique: dict[str, EffectRecord] = {}
        for effect in effects:
            existing = unique.get(effect.effect_id)
            if existing is None:
                unique[effect.effect_id] = effect
                continue
            if existing != effect:
                raise InvariantViolation(
                    f"conflicting pending effect records: {effect.effect_id}"
                )
        return tuple(unique.values())

    @classmethod
    def _run(cls, effect: EffectRecord, runner: EffectRunner) -> EffectResult:
        try:
            outcome = runner.run_once(effect.request)
        except DomainError as error:
            failure = error.failure or FailureRecord.from_effect_exception(effect, error)
            return EffectResult(
                effect.effect_id,
                effect.task_id,
                "FAILED",
                failure,
                "FAIL",
                {"error_code": failure.error_code, "reason": failure.message},
                None,
                None,
                effect.request.deadline_at,
            )
        except Exception as error:
            failure = cls._unknown_failure(effect, error)
            return EffectResult(
                effect.effect_id,
                effect.task_id,
                "FAILED",
                failure,
                "FAIL",
                {"error_code": failure.error_code, "reason": failure.message},
                None,
                None,
                effect.request.deadline_at,
            )
        else:
            if not isinstance(outcome, EffectOutcome):
                return EffectResult(effect.effect_id, effect.task_id, "SUCCEEDED")
            if outcome.status not in {"SUCCEEDED", "FAILED"}:
                failure = FailureRecord(
                    failure_id=uuid.uuid4().hex,
                    stage="effect",
                    owner_component=effect.request.effect_type,
                    task_id=effect.task_id,
                    state=effect.state,
                    sequence=effect.sequence,
                    node_run_id=None,
                    worker_id=None,
                    effect_id=effect.effect_id,
                    error_code="INVALID_EFFECT_OUTCOME",
                    retryable=False,
                    message=f"invalid effect outcome status: {outcome.status}",
                    cause_type="EffectOutcome",
                )
                return EffectResult(effect.effect_id, effect.task_id, "FAILED", failure)
            if (
                outcome.status == "SUCCEEDED"
                and outcome.event_name in REPLY_EVENT_FAILURES
            ):
                error_code, default_message = REPLY_EVENT_FAILURES[outcome.event_name]
                reason = ""
                if isinstance(outcome.event_payload, Mapping):
                    reason = str(outcome.event_payload.get("contract_rejection") or "")
                message = f"{default_message}: {reason}" if reason else default_message
                failure = FailureRecord(
                    failure_id=uuid.uuid4().hex,
                    stage="agent_result",
                    owner_component=effect.request.effect_type,
                    task_id=effect.task_id,
                    state=effect.state,
                    sequence=effect.sequence,
                    node_run_id=None,
                    worker_id=None,
                    effect_id=effect.effect_id,
                    error_code=error_code,
                    retryable=True,
                    message=message,
                    cause_type="AgentReply",
                )
                return EffectResult(
                    effect.effect_id,
                    effect.task_id,
                    "FAILED",
                    failure,
                    "FAIL",
                    {
                        "error_code": failure.error_code,
                        "reason": failure.message,
                        "retryable": True,
                    },
                    outcome.request_id,
                    outcome.operation_id,
                    outcome.deadline_at,
                )
            return EffectResult(
                effect.effect_id,
                effect.task_id,
                outcome.status,
                outcome.failure,
                outcome.event_name,
                outcome.event_payload,
                outcome.request_id,
                outcome.operation_id,
                outcome.deadline_at,
            )
        return EffectResult(
            effect.effect_id,
            effect.task_id,
            "FAILED",
            failure,
            "FAIL",
            {"error_code": failure.error_code, "reason": failure.message},
            None,
            None,
            effect.request.deadline_at,
        )

    @staticmethod
    def _unknown_failure(effect: EffectRecord, error: Exception) -> FailureRecord:
        wrapped = InvariantViolation(
            f"unknown effect runner failure: {type(error).__name__}: {error}"
        )
        failure = FailureRecord.from_effect_exception(effect, wrapped)
        return replace(failure, cause_type=type(error).__name__)


__all__ = [
    "EffectManager",
    "EffectOutcome",
    "EffectRunner",
    "EffectRunnerNotFound",
    "CONTRACT_REJECTED_EVENT",
    "REPLY_EVENT_FAILURES",
    "UNSTRUCTURED_REPLY_EVENT",
]
