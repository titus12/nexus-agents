"""Sequential and bounded-concurrent execution of one review node.

The node layer owns worker scheduling and fan-in only.  It never receives the
mutable workflow context, persists snapshots, or advances the main FSM.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Protocol, Sequence
import uuid

from ..domain.errors import FailureRecord, InvariantViolation


@dataclass(frozen=True)
class WorkerBinding:
    worker_id: str
    agent_id: str
    task_id: str
    request_id: str
    role: str
    phase: str


@dataclass(frozen=True)
class NodeContext:
    task_id: str
    node_run_id: str
    revision_id: str
    group_id: str | None = None
    item_id: str | None = None
    state: str = ""
    sequence: int = 0


@dataclass(frozen=True)
class ReviewNode:
    node_run_id: str
    phase: str
    bindings: tuple[WorkerBinding, ...]


@dataclass(frozen=True)
class WorkerResult:
    worker_id: str
    status: str
    payload_ref: str | None
    failure: FailureRecord | None = None


@dataclass(frozen=True)
class NodeResult:
    node_run_id: str
    status: str
    worker_results: tuple[WorkerResult, ...]
    aggregate: object | None = None
    failure: FailureRecord | None = None


class WorkerRunner(Protocol):
    def run(self, binding: WorkerBinding, context: NodeContext) -> WorkerResult: ...


class NodeJoiner(Protocol):
    def join(self, results: Sequence[WorkerResult]) -> NodeResult: ...


class NodeExecutor(Protocol):
    def execute(self, node: ReviewNode, context: NodeContext) -> NodeResult: ...


class SequentialNodeExecutor:
    """Run one node's workers in deterministic binding order."""

    def __init__(self, runner: WorkerRunner, joiner: NodeJoiner) -> None:
        self._runner = runner
        self._joiner = joiner

    def execute(self, node: ReviewNode, context: NodeContext) -> NodeResult:
        _validate_node(node, context)
        results = tuple(
            _run_worker(self._runner, binding, context)
            for binding in _ordered_bindings(node.bindings)
        )
        return _join_results(self._joiner, node, context, results)


class ConcurrentNodeExecutor:
    """Run one node's workers with a bounded executor and deterministic fan-in."""

    def __init__(
        self,
        runner: WorkerRunner,
        joiner: NodeJoiner,
        *,
        max_workers: int,
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be at least one")
        self._runner = runner
        self._joiner = joiner
        self._max_workers = max_workers

    def execute(self, node: ReviewNode, context: NodeContext) -> NodeResult:
        _validate_node(node, context)
        bindings = _ordered_bindings(node.bindings)
        with ThreadPoolExecutor(max_workers=min(self._max_workers, len(bindings))) as pool:
            futures = [
                pool.submit(_run_worker, self._runner, binding, context)
                for binding in bindings
            ]
            results = tuple(future.result() for future in futures)
        return _join_results(self._joiner, node, context, results)


def _ordered_bindings(bindings: tuple[WorkerBinding, ...]) -> tuple[WorkerBinding, ...]:
    if not bindings:
        raise ValueError("review node must contain at least one worker")
    worker_ids = [binding.worker_id for binding in bindings]
    if any(not worker_id for worker_id in worker_ids):
        raise ValueError("worker_id must not be empty")
    if len(worker_ids) != len(set(worker_ids)):
        raise ValueError("worker_id must be unique within a node")
    return tuple(sorted(bindings, key=lambda binding: (binding.worker_id, binding.request_id)))


def _validate_node(node: ReviewNode, context: NodeContext) -> None:
    if not isinstance(node, ReviewNode) or not isinstance(context, NodeContext):
        raise TypeError("node execution requires ReviewNode and NodeContext")
    if node.node_run_id != context.node_run_id:
        raise InvariantViolation("node and context node_run_id do not match")
    for binding in node.bindings:
        if binding.task_id != context.task_id:
            raise InvariantViolation(
                f"worker {binding.worker_id} does not belong to task {context.task_id}"
            )
        if binding.phase != node.phase:
            raise InvariantViolation(
                f"worker {binding.worker_id} phase does not match node phase"
            )


def _run_worker(
    runner: WorkerRunner,
    binding: WorkerBinding,
    context: NodeContext,
) -> WorkerResult:
    try:
        result = runner.run(binding, context)
        if not isinstance(result, WorkerResult):
            raise TypeError("worker runner must return WorkerResult")
        if result.worker_id != binding.worker_id:
            raise InvariantViolation(
                f"worker result identity mismatch: expected {binding.worker_id}"
            )
        return result
    except Exception as error:
        return WorkerResult(
            worker_id=binding.worker_id,
            status="FAILED",
            payload_ref=None,
            failure=FailureRecord(
                failure_id=uuid.uuid4().hex,
                stage="node_worker",
                owner_component="worker_runner",
                task_id=context.task_id,
                state=context.state,
                sequence=context.sequence,
                node_run_id=context.node_run_id,
                worker_id=binding.worker_id,
                effect_id=None,
                error_code=str(
                    getattr(error, "error_code", None)
                    or type(error).__name__.upper()
                ),
                retryable=bool(getattr(error, "retryable", False)),
                message=str(error),
                cause_type=type(error).__name__,
            ),
        )


def _join_results(
    joiner: NodeJoiner,
    node: ReviewNode,
    context: NodeContext,
    results: tuple[WorkerResult, ...],
) -> NodeResult:
    try:
        result = joiner.join(results)
        if not isinstance(result, NodeResult):
            raise TypeError("node joiner must return NodeResult")
        if result.node_run_id != node.node_run_id:
            raise InvariantViolation("node join result identity mismatch")
        if result.worker_results != results:
            raise InvariantViolation("node join result workers do not match execution")
        return result
    except Exception as error:
        return NodeResult(
            node_run_id=node.node_run_id,
            status="FAILED",
            worker_results=results,
            failure=FailureRecord(
                failure_id=uuid.uuid4().hex,
                stage="node_join",
                owner_component="node_joiner",
                task_id=context.task_id,
                state=context.state,
                sequence=context.sequence,
                node_run_id=context.node_run_id,
                worker_id=None,
                effect_id=None,
                error_code=str(
                    getattr(error, "error_code", None)
                    or type(error).__name__.upper()
                ),
                retryable=False,
                message=str(error),
                cause_type=type(error).__name__,
            ),
        )


__all__ = [
    "ConcurrentNodeExecutor",
    "NodeContext",
    "NodeExecutor",
    "NodeJoiner",
    "NodeResult",
    "ReviewNode",
    "SequentialNodeExecutor",
    "WorkerBinding",
    "WorkerResult",
    "WorkerRunner",
]
