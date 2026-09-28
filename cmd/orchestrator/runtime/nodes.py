"""Sequential and bounded-concurrent execution of one review node.

The node layer owns worker scheduling and fan-in only.  It never receives the
mutable workflow context, persists snapshots, or advances the main FSM.
"""

from __future__ import annotations

from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
import itertools
import logging
import threading
import time
from typing import Callable, Protocol, Sequence
import uuid

from ..domain.errors import (
    CONTRACT_REJECTED_EVENT,
    UNSTRUCTURED_REPLY_EVENT,
    FailureRecord,
    InvariantViolation,
    is_infrastructure_failure,
    is_reply_failure,
)


logger = logging.getLogger("review_orchestrator_fsm")


# One node run used to fail outright when a single binding hit an agent/transport
# error, so the whole batch (including already-reviewed items) was re-dispatched
# and paid the flaky-backend tax again.  A bounded per-binding retry keeps the
# successful siblings and re-asks only the binding that failed.
INFRASTRUCTURE_WORKER_ATTEMPTS = 2
INFRASTRUCTURE_WORKER_BACKOFF_SECONDS = 3.0


def _worker_retry_sleep(seconds: float) -> None:
    time.sleep(seconds)


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
    attempt: int = 1


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
    # A2/P3 item-revise nodes: the dispatch-time plan the joiner merges
    # worker patches onto.
    base_plan: object | None = None
    # Task-review nodes: the dispatch-time snapshot of the prior round's
    # findings and task-review ledger, consumed by the fan-in aggregate.
    previous_review: Mapping[str, object] | None = None
    # Menxia stage-barrier nodes: dispatch-time snapshot of every pipeline
    # row's stage; rows not dispatched in this wave cannot change during it,
    # so the joiner computes the post-wave census as snapshot ∪ mapped results.
    menxia_stage_census: Mapping[str, str] | None = None
    # Analyst evidence nodes: evidence payloads salvaged from the failed
    # round's wave; the joiner replays them at fan-in alongside the fresh
    # workers' results.
    salvaged_worker_payloads: tuple[dict[str, object], ...] = ()


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


def _infrastructure_failure(result: WorkerResult) -> bool:
    """True when a worker failed for an agent/transport reason (not content)."""

    failure = result.failure
    return (
        result.status == "FAILED"
        and failure is not None
        and bool(getattr(failure, "retryable", False))
        and is_infrastructure_failure(getattr(failure, "error_code", ""))
    )


def _rejected_reply(result: WorkerResult) -> str | None:
    """Rejection text when a worker delivered an unusable reply body.

    Two shapes exist: the transport reports an unstructured/contract-rejected
    reply as a synthetic action on a "successful" result (see ``adapters``),
    and some paths surface it as a failed result with a reply-shape error
    code.  Both deserve a targeted re-ask with the rejection restated -- one
    prose reply used to fail the whole wave and re-dispatch its healthy
    siblings (task-20260928-eaea40: 4-group wave died on 1 prose reply).
    """

    payload = result.result_payload
    if result.status == "SUCCEEDED" and isinstance(payload, Mapping):
        action = str(payload.get("action") or "")
        if action == UNSTRUCTURED_REPLY_EVENT:
            return (
                "the reply body must be exactly one complete JSON object "
                "(no prose, no Markdown, no code fences)"
            )
        if action == CONTRACT_REJECTED_EVENT:
            return str(
                payload.get("contract_rejection")
                or "the reply violated the role result contract"
            )
        return None
    failure = result.failure
    if (
        result.status == "FAILED"
        and failure is not None
        and is_reply_failure(getattr(failure, "error_code", ""))
    ):
        return str(getattr(failure, "message", "") or "unusable reply")
    return None


def _with_reply_feedback(
    binding: WorkerBinding,
    context: NodeContext,
    rejection: str,
) -> WorkerBinding:
    """Re-ask prompt: the worker's own capsule plus its rejection restated."""

    base = binding.prompt_ref or context.prompt_ref
    feedback = (
        "\n[Retry feedback] The orchestrator rejected your previous reply: "
        + rejection[:400]
        + "\nCorrect exactly that defect and return one complete structured "
        "result again.\n"
    )
    return replace(binding, prompt_ref=base + feedback)


def _retry_binding(binding: WorkerBinding, attempt: int) -> WorkerBinding:
    """Give a per-binding retry its own dispatch identity.

    Reusing the original request id kept the effect idempotent: the transport's
    ``find_existing`` re-attached the retry to the run that had already ended
    without a correlated result, so the retry could only re-poll a drained run
    for the whole result window and then fail with the same error.  Scoping the
    identity to the attempt forces a real re-ask (fresh prompt bundle, fresh
    child run) while ``worker_id`` stays stable so fan-in still matches.

    Fan-out children need one more nudge: the child issue is found by
    deterministic title, so reusing the title re-attached the retry to the
    drained issue whose assignment run had already died.  Retitling the retry
    makes ``ensure_child_issue`` create a fresh child issue, whose creation
    fires the fresh assignment run.
    """

    title = binding.fanout_title
    if title and attempt > 1:
        title = f"{title} (retry-{attempt})"
    return replace(binding, attempt=attempt, fanout_title=title)


def _run_worker(
    runner: WorkerRunner,
    binding: WorkerBinding,
    context: NodeContext,
) -> WorkerResult:
    result = _run_worker_once(runner, binding, context)
    for attempt in range(2, INFRASTRUCTURE_WORKER_ATTEMPTS + 1):
        rejection = _rejected_reply(result)
        if rejection is None and not _infrastructure_failure(result):
            break
        label = str(getattr(result.failure, "error_code", "") or "")
        if not label and isinstance(result.result_payload, Mapping):
            label = str(result.result_payload.get("action") or "")
        logger.warning(
            "NODE_WORKER_RETRY task_id=%s node_run_id=%s worker_id=%s "
            "attempt=%s/%s error_code=%s",
            context.task_id,
            context.node_run_id,
            binding.worker_id,
            attempt,
            INFRASTRUCTURE_WORKER_ATTEMPTS,
            label,
        )
        _worker_retry_sleep(INFRASTRUCTURE_WORKER_BACKOFF_SECONDS)
        retried = _retry_binding(binding, attempt)
        if rejection is not None:
            # A delivered-but-unusable reply is re-asked with its rejection
            # restated, mirroring the wave-level retry feedback rule: a blind
            # re-sample emits the same document and burns the attempt.
            retried = _with_reply_feedback(retried, context, rejection)
        result = _run_worker_once(runner, retried, context)
    return result


def _run_worker_once(
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
    "INFRASTRUCTURE_WORKER_ATTEMPTS",
    "INFRASTRUCTURE_WORKER_BACKOFF_SECONDS",
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
