"""Journal-first persistence for the new immutable workflow context.

The legacy :class:`JsonStateStore` remains available for old callers.  This
module deliberately stores the new domain context through an explicit DTO
boundary instead of exposing the mutable ``StateContext`` to the FSM.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import NoReturn, Protocol

from ..domain.context import (
    WorkflowContext,
    context_from_dto,
    context_to_dto,
)
from ..domain.decisions import EffectRequest, StateDecision
from ..domain.errors import FailureRecord, PersistenceError


@dataclass(frozen=True)
class WorkflowSnapshot:
    task_id: str
    context: WorkflowContext
    state_version: int


@dataclass(frozen=True)
class EffectRecord:
    effect_id: str
    task_id: str
    request: EffectRequest
    status: str
    attempt: int
    state: str = ""
    sequence: int = 0


@dataclass(frozen=True)
class EffectResult:
    effect_id: str
    task_id: str
    status: str
    failure: FailureRecord | None = None


@dataclass(frozen=True)
class CommitResult:
    transition_id: str
    snapshot: WorkflowSnapshot
    effects: tuple[EffectRecord, ...]


class WorkflowRepository(Protocol):
    def load(self, task_id: str) -> WorkflowSnapshot: ...

    def commit_transition(
        self,
        before: WorkflowSnapshot,
        after: WorkflowSnapshot,
        decision: StateDecision,
    ) -> CommitResult: ...

    def pending_effects(self, task_id: str) -> tuple[EffectRecord, ...]: ...

    def mark_effect_running(self, effect_id: str) -> None: ...

    def commit_effect_result(self, result: EffectResult) -> None: ...


class JsonWorkflowRepository:
    """Small JSON implementation of ``WorkflowRepository``.

    A transition journal record contains the complete post-transition DTO and
    effect intents.  The journal is fsynced before the workflow snapshot is replaced;
    therefore a failed snapshot write is recoverable while a failed journal
    append cannot advance the visible snapshot.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        # Keep the new DTO format separate from legacy state.json/events.jsonl
        # until the compatibility wrapper is deliberately migrated.
        self.state_path = self.root / "workflow-state.json"
        self.event_path = self.root / "workflow-events.jsonl"

    def initialize(self, snapshot: WorkflowSnapshot) -> None:
        """Create the initial snapshot without inventing a transition."""

        self._validate_snapshot(snapshot)
        if self.state_path.exists():
            raise PersistenceError(f"snapshot already exists: {self.state_path}")
        self._atomic_write(self.state_path, _snapshot_to_dto(snapshot))

    def load(self, task_id: str) -> WorkflowSnapshot:
        value = self._read_snapshot() if self.state_path.exists() else None
        records = self._read_journal()
        transition_records = [
            record
            for record in records
            if record.get("record_type") == "transition"
            and record.get("task_id") == task_id
        ]

        if value is None:
            if not transition_records:
                raise PersistenceError(f"missing workflow snapshot: {task_id}")
            value = transition_records[-1]["snapshot"]

        snapshot = _snapshot_from_dto(value)
        if snapshot.task_id != task_id:
            raise PersistenceError(
                f"snapshot task mismatch: expected {task_id}, got {snapshot.task_id}"
            )

        if transition_records:
            latest = transition_records[-1]
            journal_snapshot = _snapshot_from_dto(latest["snapshot"])
            if journal_snapshot.state_version > snapshot.state_version:
                self._atomic_write(self.state_path, latest["snapshot"])
                snapshot = journal_snapshot
        return snapshot

    def commit_transition(
        self,
        before: WorkflowSnapshot,
        after: WorkflowSnapshot,
        decision: StateDecision,
    ) -> CommitResult:
        self._validate_transition(before, after)
        effects = tuple(
            EffectRecord(
                effect_id=request.effect_id,
                task_id=request.task_id,
                request=request,
                status="PENDING",
                attempt=0,
                state=before.context.progression.state,
                sequence=before.context.progression.sequence,
            )
            for request in decision.effects
        )
        transition_id = f"{after.task_id}:{after.context.progression.sequence}"
        existing = self._find_transition(transition_id)
        if existing is not None:
            existing_snapshot = _snapshot_from_dto(existing["snapshot"])
            existing_effects = tuple(
                _effect_from_dto(item) for item in existing.get("effects", [])
            )
            if existing_snapshot != after or existing_effects != effects:
                raise PersistenceError(
                    f"transition id already exists with different content: {transition_id}"
                )
            return CommitResult(transition_id, existing_snapshot, existing_effects)

        if self.state_path.exists():
            current = _snapshot_from_dto(self._read_snapshot())
            if current != before:
                raise PersistenceError(
                    "transition starts from a stale workflow snapshot"
                )

        record = {
            "record_type": "transition",
            "transition_id": transition_id,
            "task_id": after.task_id,
            "before_sequence": before.context.progression.sequence,
            "sequence": after.context.progression.sequence,
            "snapshot": _snapshot_to_dto(after),
            "effects": [_effect_to_dto(effect) for effect in effects],
            "transition": (
                asdict(decision.transition) if decision.transition is not None else None
            ),
        }
        try:
            self._append_record(record)
        except PersistenceError as error:
            _raise_persistence_failure(
                "journal append failed",
                after,
                error,
            )
        try:
            self._atomic_write(self.state_path, record["snapshot"])
        except PersistenceError as error:
            _raise_persistence_failure(
                "snapshot replacement failed after journal commit",
                after,
                error,
            )
        return CommitResult(transition_id, after, effects)

    def pending_effects(self, task_id: str) -> tuple[EffectRecord, ...]:
        intents: dict[str, EffectRecord] = {}
        statuses: dict[str, str] = {}
        for record in self._read_journal():
            if record.get("task_id") != task_id:
                continue
            if record.get("record_type") == "transition":
                for item in record.get("effects", []):
                    effect = _effect_from_dto(item)
                    intents[effect.effect_id] = effect
            elif record.get("record_type") == "effect_running":
                statuses[str(record.get("effect_id"))] = "RUNNING"
            elif record.get("record_type") == "effect_result":
                statuses[str(record.get("effect_id"))] = str(record.get("status"))
        return tuple(
            EffectRecord(
                effect_id=effect.effect_id,
                task_id=effect.task_id,
                request=effect.request,
                status=statuses.get(effect.effect_id, effect.status),
                attempt=effect.attempt + (1 if statuses.get(effect.effect_id) == "RUNNING" else 0),
                state=effect.state,
                sequence=effect.sequence,
            )
            for effect in intents.values()
            if statuses.get(effect.effect_id, effect.status) not in {"SUCCEEDED", "FAILED"}
        )

    def mark_effect_running(self, effect_id: str) -> None:
        effect = self._find_effect(effect_id)
        if effect is None:
            raise PersistenceError(f"unknown effect: {effect_id}")
        current_status = self._effect_status(effect_id)
        if current_status in {"RUNNING", "SUCCEEDED", "FAILED"}:
            # Effect lifecycle updates are idempotent and never regress a
            # terminal result back to RUNNING.
            return
        self._append_record(
            {
                "record_type": "effect_running",
                "effect_id": effect_id,
                "task_id": effect.task_id,
            }
        )

    def _effect_status(self, effect_id: str) -> str | None:
        status: str | None = None
        for record in self._read_journal():
            if record.get("effect_id") != effect_id:
                continue
            if record.get("record_type") == "effect_running":
                status = "RUNNING"
            elif record.get("record_type") == "effect_result":
                status = str(record["status"])
        return status

    def commit_effect_result(self, result: EffectResult) -> None:
        effect = self._find_effect(result.effect_id)
        if effect is None or effect.task_id != result.task_id:
            raise PersistenceError(f"unknown effect: {result.effect_id}")
        if result.status not in {"SUCCEEDED", "FAILED"}:
            raise PersistenceError(f"invalid effect result status: {result.status}")
        existing = [
            record
            for record in self._read_journal()
            if record.get("record_type") == "effect_result"
            and record.get("effect_id") == result.effect_id
        ]
        if existing:
            if existing[-1].get("status") != result.status:
                raise PersistenceError(f"effect result conflict: {result.effect_id}")
            return
        self._append_record(
            {
                "record_type": "effect_result",
                "effect_id": result.effect_id,
                "task_id": result.task_id,
                "status": result.status,
                "failure": _failure_to_dto(result.failure),
            }
        )

    def _find_transition(self, transition_id: str) -> dict[str, object] | None:
        for record in self._read_journal():
            if (
                record.get("record_type") == "transition"
                and record.get("transition_id") == transition_id
            ):
                return record
        return None

    def _find_effect(self, effect_id: str) -> EffectRecord | None:
        result: EffectRecord | None = None
        for record in self._read_journal():
            if record.get("record_type") != "transition":
                continue
            for item in record.get("effects", []):
                effect = _effect_from_dto(item)
                if effect.effect_id == effect_id:
                    result = effect
        return result

    def _read_snapshot(self) -> dict[str, object]:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise TypeError("snapshot must be an object")
            return value
        except Exception as error:
            raise PersistenceError(f"invalid workflow snapshot: {self.state_path}") from error

    def _read_journal(self) -> list[dict[str, object]]:
        if not self.event_path.exists():
            return []
        lines = self.event_path.read_text(encoding="utf-8").splitlines()
        records: list[dict[str, object]] = []
        for index, line in enumerate(lines):
            try:
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise TypeError("journal record must be an object")
                if not value.get("record_type"):
                    raise ValueError("journal record_type is required")
                self._validate_journal_record(value)
                records.append(value)
            except Exception as error:
                # A malformed record in the middle is never safe to ignore.
                raise PersistenceError(
                    f"invalid journal record at line {index + 1}"
                ) from error
        last_sequence: dict[str, int] = {}
        for record in records:
            if record.get("record_type") != "transition":
                continue
            task_id = str(record["task_id"])
            sequence = int(record["sequence"])
            previous = last_sequence.get(task_id)
            if previous is not None and sequence != previous + 1:
                raise PersistenceError(
                    f"journal transition sequence contains a gap for task {task_id}"
                )
            last_sequence[task_id] = sequence
        return records

    @staticmethod
    def _validate_journal_record(record: Mapping[str, object]) -> None:
        record_type = record.get("record_type")
        if record_type == "transition":
            required = ("transition_id", "task_id", "sequence", "before_sequence", "snapshot")
            if any(name not in record for name in required):
                raise ValueError("transition record is incomplete")
            snapshot = record["snapshot"]
            if not isinstance(snapshot, Mapping):
                raise TypeError("transition snapshot must be an object")
            parsed = _snapshot_from_dto(snapshot)
            if int(record["sequence"]) != parsed.context.progression.sequence:
                raise ValueError("transition sequence does not match snapshot")
            if int(record["sequence"]) != int(record["before_sequence"]) + 1:
                raise ValueError("transition sequence is not linear")
            effects = record.get("effects", [])
            if not isinstance(effects, list):
                raise TypeError("transition effects must be an array")
            for effect in effects:
                if not isinstance(effect, Mapping):
                    raise TypeError("effect record must be an object")
                _effect_from_dto(effect)
            return
        if record_type == "effect_running":
            if not record.get("effect_id") or not record.get("task_id"):
                raise ValueError("effect_running record is incomplete")
            return
        if record_type == "effect_result":
            if not record.get("effect_id") or not record.get("task_id"):
                raise ValueError("effect_result record is incomplete")
            if record.get("status") not in {"SUCCEEDED", "FAILED"}:
                raise ValueError("effect_result status is invalid")
            return
        raise ValueError(f"unknown journal record type: {record_type}")

    def _append_record(self, record: dict[str, object]) -> None:
        try:
            self.event_path.parent.mkdir(parents=True, exist_ok=True)
            with self.event_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        except Exception as error:
            raise PersistenceError("journal append failed") from error

    def _validate_snapshot(self, snapshot: WorkflowSnapshot) -> None:
        if not snapshot.task_id or snapshot.task_id != snapshot.context.identity.task_id:
            raise PersistenceError("snapshot task identity is invalid")
        if snapshot.state_version < 0:
            raise PersistenceError("snapshot state_version must be non-negative")

    def _validate_transition(
        self,
        before: WorkflowSnapshot,
        after: WorkflowSnapshot,
    ) -> None:
        self._validate_snapshot(before)
        self._validate_snapshot(after)
        if before.task_id != after.task_id:
            raise PersistenceError("transition task identity mismatch")
        if after.context.progression.sequence != before.context.progression.sequence + 1:
            raise PersistenceError("transition sequence must advance by one")
        if after.state_version != before.state_version + 1:
            raise PersistenceError("snapshot state_version must advance by one")

    @staticmethod
    def _atomic_write(path: Path, value: Mapping[str, object]) -> None:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        ).encode("utf-8")
        fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, path)
        except Exception as error:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise PersistenceError("snapshot replacement failed") from error


def _raise_persistence_failure(
    message: str,
    snapshot: WorkflowSnapshot,
    cause: Exception,
) -> NoReturn:
    failure = FailureRecord(
        failure_id=uuid.uuid4().hex,
        stage="persistence",
        owner_component="workflow_repository",
        task_id=snapshot.task_id,
        state=snapshot.context.progression.state,
        sequence=snapshot.context.progression.sequence,
        node_run_id=None,
        worker_id=None,
        effect_id=None,
        error_code="PERSISTENCE_ERROR",
        retryable=True,
        message=str(cause),
        cause_type=type(cause).__name__,
    )
    raise PersistenceError(message, failure=failure) from cause


def _snapshot_to_dto(snapshot: WorkflowSnapshot) -> dict[str, object]:
    return {
        "task_id": snapshot.task_id,
        "state_version": snapshot.state_version,
        "context": context_to_dto(snapshot.context),
    }


def _snapshot_from_dto(value: Mapping[str, object]) -> WorkflowSnapshot:
    context_value = value.get("context")
    if not isinstance(context_value, Mapping):
        raise PersistenceError("snapshot context must be an object")
    try:
        context = context_from_dto(context_value)
    except (TypeError, ValueError) as error:
        raise PersistenceError("invalid workflow context DTO") from error
    task_id = str(value.get("task_id") or "")
    snapshot = WorkflowSnapshot(task_id, context, int(value.get("state_version", 0)))
    if snapshot.task_id != context.identity.task_id:
        raise PersistenceError("snapshot task identity mismatch")
    return snapshot


def _effect_to_dto(effect: EffectRecord) -> dict[str, object]:
    return {
        "effect_id": effect.effect_id,
        "task_id": effect.task_id,
        "status": effect.status,
        "attempt": effect.attempt,
        "state": effect.state,
        "sequence": effect.sequence,
        "request": asdict(effect.request),
    }


def _effect_from_dto(value: Mapping[str, object]) -> EffectRecord:
    request_value = value.get("request")
    if not isinstance(request_value, Mapping):
        raise PersistenceError("effect request must be an object")
    try:
        request = EffectRequest(**dict(request_value))
        return EffectRecord(
            effect_id=str(value["effect_id"]),
            task_id=str(value["task_id"]),
            request=request,
            status=str(value["status"]),
            attempt=int(value.get("attempt", 0)),
            state=str(value.get("state") or ""),
            sequence=int(value.get("sequence", 0)),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise PersistenceError("invalid effect record") from error


def _failure_to_dto(failure: FailureRecord | None) -> dict[str, object] | None:
    return asdict(failure) if failure is not None else None


__all__ = [
    "CommitResult",
    "EffectRecord",
    "EffectResult",
    "JsonWorkflowRepository",
    "WorkflowRepository",
    "WorkflowSnapshot",
]
