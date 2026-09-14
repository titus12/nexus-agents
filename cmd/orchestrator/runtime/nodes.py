"""Sequential and bounded-concurrent execution of one review node.

The node layer owns worker scheduling and fan-in only.  It never receives the
mutable workflow context, persists snapshots, or advances the main FSM.
"""

from __future__ import annotations

from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import itertools
import logging
import threading
import time
from typing import Callable, Protocol, Sequence
import uuid

from ..domain.errors import FailureRecord, InvariantViolation


logger = logging.getLogger("review_orchestrator_fsm")


@dataclass(frozen=True)
class WorkerBinding:
    worker_id: str
    agent_id: str
    task_id: str
    request_id: str
    role: str
    phase: str
    group_id: str | None = None
    item_id: str | None = None
    prompt_ref: str = ""
    issue_id: str = ""
    fanout_parent_id: str = ""
    fanout_title: str = ""
    dispatch_context: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class NodeContext:
    task_id: str
    node_run_id: str
    revision_id: str
    group_id: str | None = None
    item_id: str | None = None
    state: str = ""
    sequence: int = 0
    prompt_ref: str = ""
    issue_id: str = ""
    plan_hash: str = ""
    dispatch_mode: str = ""
    review_queue: object | None = None
    canonical_requirements: tuple[dict[str, object], ...] = ()


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
    result_payload: object | None = None
    external_issue_id: str = ""


@dataclass(frozen=True)
class NodeResult:
    node_run_id: str
    status: str
    worker_results: tuple[WorkerResult, ...]
    aggregate: object | None = None
    failure: FailureRecord | None = None


class WorkerRunner(Protocol):
    def run(self, binding: WorkerBinding, context: NodeContext) -> WorkerResult: ...


WorkerProgressCallback = Callable[
    [WorkerBinding, WorkerResult, int, int, NodeContext], None
]


class NodeJoiner(Protocol):
    def join(self, results: Sequence[WorkerResult]) -> NodeResult: ...


class NodeExecutor(Protocol):
    def execute(self, node: ReviewNode, context: NodeContext) -> NodeResult: ...


class SequentialNodeExecutor:
    """Run one node's workers in deterministic binding order."""

    def __init__(
        self,
        runner: WorkerRunner,
        joiner: NodeJoiner,
        *,
        on_worker_complete: WorkerProgressCallback | None = None,
    ) -> None:
        self._runner = runner
        self._joiner = joiner
        self._on_worker_complete = on_worker_complete

    def execute(self, node: ReviewNode, context: NodeContext) -> NodeResult:
        _validate_node(node, context)
        bindings = _ordered_bindings(node.bindings)
        total = len(bindings)
        results: list[WorkerResult] = []
        for completed, binding in enumerate(bindings, start=1):
            result = _run_worker(self._runner, binding, context)
            results.append(result)
            _report_worker_complete(
                self._on_worker_complete, binding, result, completed, total, context
            )
        return _join_results(self._joiner, node, context, tuple(results))


class ConcurrentNodeExecutor:
    """Run one node's workers with a bounded executor and deterministic fan-in."""

    def __init__(
        self,
        runner: WorkerRunner,
        joiner: NodeJoiner,
        *,
        max_workers: int,
        on_worker_complete: WorkerProgressCallback | None = None,
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be at least one")
        self._runner = runner
        self._joiner = joiner
        self._max_workers = max_workers
        self._on_worker_complete = on_worker_complete

    def execute(self, node: ReviewNode, context: NodeContext) -> NodeResult:
        _validate_node(node, context)
        bindings = _ordered_bindings(node.bindings)
        total = len(bindings)
        counter = itertools.count(1)
        counter_lock = threading.Lock()
        stats_lock = threading.Lock()
        active = 0
        peak = 0

        def run_and_report(binding: WorkerBinding) -> WorkerResult:
            nonlocal active, peak
            with stats_lock:
                active += 1
                peak = max(peak, active)
            try:
                result = _run_worker(self._runner, binding, context)
                with counter_lock:
                    completed = next(counter)
                _report_worker_complete(
                    self._on_worker_complete, binding, result, completed, total, context
                )
                return result
            finally:
                with stats_lock:
                    active -= 1

        pool_width = min(self._max_workers, len(bindings))
        started = time.monotonic()
        logger.info(
            "NODE_CONCURRENCY_START node_run_id=%s state=%s bindings=%s max_workers=%s",
            context.node_run_id,
            context.state,
            total,
            pool_width,
        )
        with ThreadPoolExecutor(max_workers=pool_width) as pool:
            futures = [pool.submit(run_and_report, binding) for binding in bindings]
            results = tuple(future.result() for future in futures)
        logger.info(
            "NODE_CONCURRENCY_END node_run_id=%s state=%s bindings=%s "
            "peak_concurrency=%s elapsed_ms=%s",
            context.node_run_id,
            context.state,
            total,
            peak,
            int((time.monotonic() - started) * 1000),
        )
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


def _report_worker_complete(
    callback: WorkerProgressCallback | None,
    binding: WorkerBinding,
    result: WorkerResult,
    completed: int,
    total: int,
    context: NodeContext,
) -> None:
    if callback is None:
        return
    try:
        callback(binding, result, completed, total, context)
    except Exception:
        logger.exception(
            "NODE_WORKER_PROGRESS_FAILED task_id=%s node_run_id=%s worker_id=%s",
            context.task_id,
            context.node_run_id,
            binding.worker_id,
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
    "WorkerProgressCallback",
    "WorkerResult",
    "WorkerRunner",
]
