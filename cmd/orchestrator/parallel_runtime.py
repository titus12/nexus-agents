from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
import copy
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

from .concurrency import ConcurrencyAdmission
from .lifecycle import LifecyclePaths, write_stage_result, write_stage_verdict
from .models import AgentRequest, DispatchReceipt, ExternalMessage
from .zhongshu_review_queue import ReviewJob, TaskReviewQueue

logger = logging.getLogger("review_orchestrator_fsm")


@dataclass(frozen=True)
class ParallelWorker:
    worker_id: str
    phase: str
    role: str
    request: AgentRequest


@dataclass(frozen=True)
class ParallelFanIn:
    phase: str
    revision_id: str
    completed: tuple[dict[str, Any], ...]
    failed: tuple[dict[str, Any], ...]
    rejected: tuple[dict[str, Any], ...]
    fanin_error: str = ""


def external_target_parallelism(workers: list[ParallelWorker]) -> int:
    """Return a safe fan-out width for the external execution targets.

    Multica may coalesce comments sent to the same issue and Agent while a
    run is active. Keep those requests serialized; distinct targets retain
    the normal worker width.
    """
    keys = [
        (
            str(worker.request.issue_id or worker.request.task_id),
            str(worker.request.agent_id or ""),
        )
        for worker in workers
    ]
    if len(keys) != len(set(keys)):
        return 1
    return max(1, len(workers))


class ParallelCoordinatorDriver:
    """Production adapter for bounded fan-out/fan-in.

    Workers never write shared files. Each worker returns an in-memory payload;
    this driver is the sole writer and writes each worker result exactly once,
    then writes one deterministic fan-in verdict.
    """

    def __init__(
        self,
        *,
        task_id: str,
        lifecycle: LifecyclePaths,
        admission: ConcurrencyAdmission,
        dispatch: Callable[[AgentRequest], Any],
        poll: Callable[[AgentRequest], list[ExternalMessage]],
        find_existing_request: Callable[[str, str], DispatchReceipt | None] | None = None,
        max_attempts: int = 3,
        poll_interval: float = 2.0,
        timeout_seconds: float = 900.0,
    ) -> None:
        self.task_id = task_id
        self.lifecycle = lifecycle
        self.admission = admission
        self.dispatch = dispatch
        self.poll = poll
        self.find_existing_request = find_existing_request
        self.max_attempts = max(1, max_attempts)
        self.poll_interval = max(0.0, poll_interval)
        self.timeout_seconds = max(1.0, timeout_seconds)

    def run(
        self,
        *,
        phase: str,
        revision_id: str,
        workers: list[ParallelWorker],
        fan_in: Callable[[list[dict[str, Any]]], dict[str, Any]],
        validate: Callable[[dict[str, Any], ParallelWorker], None] | None = None,
        timeout_seconds: float | None = None,
        max_attempts: int | None = None,
        max_workers: int | None = None,
        recovered_results: list[dict[str, Any]] | None = None,
        heartbeat: Callable[[], None] | None = None,
        write_verdict: bool = True,
    ) -> ParallelFanIn:
        if not workers:
            raise ValueError("workers must not be empty")
        recovered_by_worker: dict[str, dict[str, Any]] = {}
        for result in recovered_results or []:
            if not isinstance(result, dict):
                continue
            worker_id = str(result.get("worker_id") or "").strip()
            if worker_id and worker_id in {worker.worker_id for worker in workers}:
                recovered_by_worker[worker_id] = result
        pending_workers = [
            worker for worker in workers if worker.worker_id not in recovered_by_worker
        ]
        worker_timeout = (
            self.timeout_seconds
            if timeout_seconds is None
            else max(1.0, float(timeout_seconds))
        )
        worker_attempts = (
            self.max_attempts
            if max_attempts is None
            else max(1, int(max_attempts))
        )
        logger.info(
            "PARALLEL_FANOUT_STARTED task_id=%s phase=%s revision_id=%s requested=%s recovered=%s queued=%s",
            self.task_id,
            phase,
            revision_id,
            len(workers),
            len(recovered_by_worker),
            len(pending_workers),
        )
        if recovered_by_worker:
            logger.info(
                "PARALLEL_STAGE_RESULTS_REUSED task_id=%s phase=%s revision_id=%s "
                "worker_ids=%s",
                self.task_id,
                phase,
                revision_id,
                sorted(recovered_by_worker),
            )

        stop_event = threading.Event()

        def refresh_runtime() -> None:
            if heartbeat is not None:
                heartbeat()

        def execute(worker: ParallelWorker) -> dict[str, Any]:
            lease: Any | None = None
            last_error = ""
            rejected_reply_paths: list[str] = []
            worker_deadline = time.monotonic() + worker_timeout
            try:
                lease = self.admission.wait_acquire(
                    self.task_id,
                    worker.phase,
                    worker.worker_id,
                    revision_id,
                    timeout_seconds=worker_timeout,
                    poll_interval=max(0.5, min(2.0, self.poll_interval or 0.5)),
                    stop_event=stop_event,
                    heartbeat=refresh_runtime,
                )
                if lease is None:
                    if stop_event.is_set():
                        return {
                            "status": "failed",
                            "failure": {
                                "worker_id": worker.worker_id,
                                "phase": phase,
                                "revision_id": revision_id,
                                "attempts": 0,
                                "error": "PARALLEL_EXECUTION_INTERRUPTED",
                                "rejected_reply_paths": rejected_reply_paths,
                            },
                        }
                    return {
                        "status": "rejected",
                        "failure": {
                            "worker_id": worker.worker_id,
                            "phase": phase,
                            "revision_id": revision_id,
                            "attempts": 0,
                            "reason": "CONCURRENCY_LIMIT_REACHED",
                            "error": "CONCURRENCY_LIMIT_REACHED",
                        },
                    }
                logger.info(
                    "PARALLEL_WORKER_ADMITTED task_id=%s phase=%s worker_id=%s revision_id=%s",
                    self.task_id,
                    phase,
                    worker.worker_id,
                    revision_id,
                )
                for attempt in range(1, worker_attempts + 1):
                    if stop_event.is_set():
                        return {
                            "status": "failed",
                            "failure": {
                                "worker_id": worker.worker_id,
                                "phase": phase,
                                "revision_id": revision_id,
                                "attempts": attempt - 1,
                                "error": "PARALLEL_EXECUTION_INTERRUPTED",
                                "rejected_reply_paths": rejected_reply_paths,
                            },
                        }
                    last_payload: dict[str, Any] | None = None
                    refresh_runtime()
                    if not self.admission.refresh(lease.lease_id):
                        raise RuntimeError("CONCURRENCY_LEASE_LOST")
                    if time.monotonic() >= worker_deadline:
                        last_error = "TimeoutError: parallel worker total timeout exceeded"
                        break
                    logical_request = worker.request
                    attempt_request = replace(
                        logical_request,
                        request_id=f"{logical_request.request_id}:attempt-{attempt}",
                        idempotency_key=f"{logical_request.idempotency_key}:attempt-{attempt}",
                        sent_after=datetime.now(timezone.utc).isoformat(),
                        dispatch_external_message_id="",
                        context={
                            **logical_request.context,
                            "logical_request_id": logical_request.request_id,
                            "attempt": attempt,
                            "revision_id": revision_id,
                        },
                    )
                    attempt_worker = ParallelWorker(
                        worker.worker_id,
                        worker.phase,
                        worker.role,
                        attempt_request,
                    )
                    logger.info(
                        "PARALLEL_WORKER_ATTEMPT task_id=%s phase=%s worker_id=%s request_id=%s "
                        "logical_request_id=%s attempt=%s max_attempts=%s",
                            self.task_id,
                            phase,
                            worker.worker_id,
                            attempt_request.request_id,
                            logical_request.request_id,
                            attempt,
                            worker_attempts,
                    )
                    try:
                        receipt: DispatchReceipt | Any | None = None
                        receipt_source = "new_dispatch"
                        if self.find_existing_request is not None:
                            try:
                                receipt = self.find_existing_request(
                                    attempt_request.idempotency_key,
                                    attempt_request.issue_id,
                                )
                            except Exception as error:
                                logger.warning(
                                    "PARALLEL_REQUEST_LOOKUP_FAILED task_id=%s phase=%s "
                                    "worker_id=%s request_id=%s attempt=%s error_type=%s error=%s",
                                    self.task_id,
                                    phase,
                                    worker.worker_id,
                                    attempt_request.request_id,
                                    attempt,
                                    type(error).__name__,
                                    str(error)[:300],
                                )
                                raise RuntimeError("REQUEST_LOOKUP_FAILED") from error
                            if receipt is not None:
                                receipt_source = "existing_request"
                                logger.info(
                                    "PARALLEL_REQUEST_REUSED_EXISTING task_id=%s phase=%s "
                                    "worker_id=%s request_id=%s attempt=%s external_message_id=%s",
                                    self.task_id,
                                    phase,
                                    worker.worker_id,
                                    attempt_request.request_id,
                                    attempt,
                                    getattr(receipt, "external_message_id", "") or "",
                                )
                        if receipt is None:
                            receipt = self.dispatch(attempt_request)
                            logger.info(
                                "PARALLEL_REQUEST_DISPATCHED_NEW task_id=%s phase=%s "
                                "worker_id=%s request_id=%s attempt=%s",
                                self.task_id,
                                phase,
                                worker.worker_id,
                                attempt_request.request_id,
                                attempt,
                            )
                        external_message_id = str(
                            getattr(receipt, "external_message_id", "") or ""
                        )
                        if not external_message_id:
                            raise RuntimeError("DISPATCH_CORRELATION_UNAVAILABLE")
                        poll_request = replace(
                            attempt_request,
                            request_id=str(
                                getattr(receipt, "request_id", "")
                                or attempt_request.request_id
                            ),
                            dispatch_external_message_id=external_message_id,
                        )
                        poll_worker = replace(attempt_worker, request=poll_request)
                        logger.info(
                            "PARALLEL_DISPATCH_RECEIPT task_id=%s phase=%s worker_id=%s "
                            "request_id=%s attempt=%s external_message_id=%s source=%s",
                            self.task_id,
                            phase,
                            worker.worker_id,
                            attempt_request.request_id,
                            attempt,
                            external_message_id,
                            receipt_source,
                        )
                        while time.monotonic() < worker_deadline:
                            if stop_event.is_set():
                                return {
                                    "status": "failed",
                                    "failure": {
                                        "worker_id": worker.worker_id,
                                        "phase": phase,
                                        "revision_id": revision_id,
                                        "attempts": attempt,
                                        "error": "PARALLEL_EXECUTION_INTERRUPTED",
                                        "rejected_reply_paths": rejected_reply_paths,
                                    },
                                }
                            refresh_runtime()
                            if not self.admission.refresh(lease.lease_id):
                                raise RuntimeError("CONCURRENCY_LEASE_LOST")
                            replies = self.poll(poll_request)
                            if replies:
                                payload = dict(replies[-1].payload)
                                last_payload = dict(payload)
                                reported_worker_id = str(payload.get("worker_id") or "")
                                if reported_worker_id and reported_worker_id != worker.worker_id:
                                    raise ValueError(
                                        "PARALLEL_REPLY_WORKER_ID_MISMATCH"
                                    )
                                payload.setdefault("worker_id", worker.worker_id)
                                payload.setdefault("phase", phase)
                                payload.setdefault("revision_id", revision_id)
                                if isinstance(poll_request.context, dict):
                                    for key in (
                                        "group_id",
                                        "item_id",
                                        "active_item_id",
                                        "worker_lens",
                                        "zhongshu_dispatch_mode",
                                        "review_job_id",
                                        "task_hash",
                                        "dependency_hash",
                                    ):
                                        if poll_request.context.get(key) and not payload.get(key):
                                            payload[key] = poll_request.context[key]
                                payload["attempt"] = attempt
                                payload["logical_request_id"] = logical_request.request_id
                                if validate is not None:
                                    validate(payload, poll_worker)
                                write_stage_result(
                                    self.lifecycle,
                                    phase=phase,
                                    revision=revision_id,
                                    state=(
                                        worker.phase
                                        if phase.upper() == "MENXIA"
                                        else worker.role
                                    ),
                                    payload=payload,
                                    group_id=str(
                                        payload.get("group_id")
                                        or attempt_request.context.get("group_id")
                                        or ""
                                    ),
                                    item_id=str(
                                        payload.get("item_id")
                                        or payload.get("active_item_id")
                                        or attempt_request.context.get("item_id")
                                        or attempt_request.context.get("active_item_id")
                                        or ""
                                    ),
                                    worker_id=worker.worker_id,
                                )
                                return {"status": "completed", "worker_id": worker.worker_id, "result": payload}
                            time.sleep(self.poll_interval)
                        raise TimeoutError("parallel worker timed out")
                    except Exception as error:
                        last_error = f"{type(error).__name__}: {error}"
                        if phase == "ZHONGSHU" and last_payload is not None:
                            try:
                                rejected_path = write_stage_result(self.lifecycle, phase=phase, revision=revision_id, state=worker.role, worker_id=f"{worker.worker_id}-rejected-{logical_request.request_id.rsplit(':', 1)[-1]}-{attempt}", payload={"error": last_error, "reply": last_payload, "logical_request_id": logical_request.request_id, "request_id": attempt_request.request_id})
                                rejected_reply_paths.append(str(rejected_path))
                            except OSError:
                                logger.exception("PARALLEL_REJECTED_REPLY_PERSIST_FAILED worker_id=%s", worker.worker_id)
                        logger.warning(
                            "PARALLEL_WORKER_FAILED task_id=%s phase=%s worker_id=%s attempt=%s error=%s",
                            self.task_id, phase, worker.worker_id, attempt, last_error[:300]
                        )
                return {
                    "status": "failed",
                    "failure": {
                        "worker_id": worker.worker_id,
                        "phase": phase,
                        "revision_id": revision_id,
                        "attempts": worker_attempts,
                        "error": last_error or "worker failed",
                        "rejected_reply_paths": rejected_reply_paths,
                    },
                }
            finally:
                if lease is not None:
                    self.admission.release(lease.lease_id)

        completed: list[dict[str, Any]] = list(recovered_by_worker.values())
        failed: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        pool_size = len(pending_workers) or 1
        if max_workers is not None:
            pool_size = max(1, min(pool_size, int(max_workers)))
        pool = ThreadPoolExecutor(max_workers=pool_size)
        futures = {
            pool.submit(execute, worker): worker
            for worker in pending_workers
        }
        try:
            for future in as_completed(futures):
                worker = futures[future]
                try:
                    result = future.result()
                except Exception as error:
                    # A worker exception must be represented as one failed
                    # slot, not escape fan-in and crash the FSM process.
                    logger.exception(
                        "PARALLEL_WORKER_FUTURE_FAILED task_id=%s phase=%s worker_id=%s error_type=%s",
                        self.task_id,
                        phase,
                        worker.worker_id,
                        type(error).__name__,
                    )
                    failed.append({
                        "worker_id": worker.worker_id,
                        "phase": phase,
                        "revision_id": revision_id,
                        "attempts": worker_attempts,
                        "error": f"{type(error).__name__}: {error}",
                    })
                    continue
                if result.get("status") == "completed":
                    completed.append(result["result"])
                elif result.get("status") == "rejected":
                    rejected.append(result["failure"])
                else:
                    failed.append(result["failure"])
        except BaseException:
            # KeyboardInterrupt and process-level cancellation must not wait
            # for every remote poll to reach its 15-minute deadline.  Running
            # workers observe the event and stop at their next poll boundary;
            # already durable worker results remain available for recovery.
            stop_event.set()
            for future in futures:
                future.cancel()
            pool.shutdown(wait=False, cancel_futures=True)
            raise
        else:
            pool.shutdown(wait=True)

        completed.sort(key=lambda item: str(item.get("worker_id") or ""))
        failed.sort(key=lambda item: str(item.get("worker_id") or ""))
        fanin_error = ""
        try:
            verdict = fan_in(completed)
        except Exception as error:
            fanin_error = f"{type(error).__name__}: {error}"
            logger.exception(
                "PARALLEL_FANIN_FAILED task_id=%s phase=%s revision_id=%s error=%s",
                self.task_id,
                phase,
                revision_id,
                fanin_error[:500],
            )
            verdict = {
                "action": "FANIN_ERROR",
                "fanin_error": fanin_error[:1000],
            }
        verdict.update({
            "phase": phase,
            "revision_id": revision_id,
            "completed_workers": [item.get("worker_id") for item in completed],
            "failed_workers": [item.get("worker_id") for item in failed],
            "rejected_workers": [item.get("worker_id") for item in rejected],
        })
        verdict_path = None
        if write_verdict:
            verdict_path = write_stage_verdict(
                self.lifecycle, phase=phase, revision=revision_id, verdict=verdict
            )
        logger.info(
            "PARALLEL_FANIN_COMPLETED task_id=%s phase=%s revision_id=%s completed=%s failed=%s rejected=%s verdict=%s",
            self.task_id, phase, revision_id, len(completed), len(failed), len(rejected), verdict_path or "disabled"
        )
        return ParallelFanIn(
            phase,
            revision_id,
            tuple(completed),
            tuple(failed),
            tuple(rejected),
            fanin_error,
        )

    def run_task_review_queue(
        self,
        *,
        phase: str,
        revision_id: str,
        queue: TaskReviewQueue,
        worker_count: int,
        build_request: Callable[[ReviewJob, str], ParallelWorker],
        validate: Callable[[dict[str, Any], ParallelWorker, ReviewJob], None] | None = None,
        on_completed: Callable[[ReviewJob, dict[str, Any]], None] | None = None,
        on_failed: Callable[[ReviewJob, dict[str, Any]], None] | None = None,
        persist_queue: Callable[[TaskReviewQueue], None] | None = None,
        timeout_seconds: float | None = None,
        max_attempts: int | None = None,
        heartbeat: Callable[[], None] | None = None,
    ) -> TaskReviewQueue:
        """Drain a durable task queue with reusable bounded worker slots.

        ``run`` remains the single-request transport implementation. Each
        slot invokes it for one claimed task and then claims the next task;
        this preserves the existing correlation/retry behavior without ever
        giving two slots the same task identity.
        """
        if phase.upper() != "ZHONGSHU":
            raise ValueError("task review queue is only valid for Zhongshu")
        if revision_id != queue.revision_id:
            raise ValueError("task review queue revision mismatch")
        if not queue.jobs:
            raise ValueError("task review queue is empty")
        recovered_leases = queue.recover_running()
        if recovered_leases:
            if persist_queue is not None:
                persist_queue(queue)
            logger.warning(
                "TASK_REVIEW_RUNNING_LEASES_RECOVERED task_id=%s revision_id=%s count=%s",
                self.task_id,
                revision_id,
                recovered_leases,
            )
        slot_count = max(1, min(int(worker_count), len(queue.jobs)))
        total_attempts = max(1, int(max_attempts or self.max_attempts))
        worker_timeout = self.timeout_seconds if timeout_seconds is None else max(1.0, float(timeout_seconds))
        logger.info(
            "TASK_REVIEW_QUEUE_STARTED task_id=%s revision_id=%s jobs=%s slots=%s",
            self.task_id,
            revision_id,
            len(queue.jobs),
            slot_count,
        )

        def persist() -> None:
            if persist_queue is not None:
                persist_queue(queue)

        def slot_loop(slot_index: int) -> None:
            worker_id = f"zhongshu-critic-slot-{slot_index}"
            while True:
                job = queue.claim_next(worker_id)
                if job is None:
                    return
                persist()
                logger.info(
                    "TASK_REVIEW_JOB_CLAIMED task_id=%s revision_id=%s job_id=%s "
                    "group_id=%s item_id=%s worker_id=%s attempt=%s",
                    self.task_id,
                    revision_id,
                    job.review_job_id,
                    job.group_id,
                    job.item_id,
                    worker_id,
                    job.attempt,
                )
                try:
                    worker = build_request(job, worker_id)
                    result = self.run(
                        phase=phase,
                        revision_id=revision_id,
                        workers=[worker],
                        fan_in=lambda values: {"worker_count": len(values)},
                        validate=(
                            None
                            if validate is None
                            else lambda payload, request_worker: validate(payload, request_worker, job)
                        ),
                        timeout_seconds=worker_timeout,
                        max_attempts=1,
                        heartbeat=heartbeat,
                        write_verdict=False,
                    )
                    if result.completed:
                        payload = copy.deepcopy(result.completed[0])
                        result_path = self.lifecycle.stage_result(
                            phase=phase,
                            revision=revision_id,
                            state=worker.role,
                            group_id=job.group_id,
                            item_id=job.item_id,
                            worker_id=worker_id,
                            payload=payload,
                        )
                        completed_job = queue.complete(
                            job.review_job_id,
                            job.lease_id,
                            job.attempt,
                            str(result_path),
                        )
                        persist()
                        logger.info(
                            "TASK_REVIEW_JOB_COMPLETED task_id=%s revision_id=%s "
                            "job_id=%s group_id=%s item_id=%s worker_id=%s",
                            self.task_id,
                            revision_id,
                            completed_job.review_job_id,
                            completed_job.group_id,
                            completed_job.item_id,
                            worker_id,
                        )
                        if on_completed is not None:
                            try:
                                on_completed(completed_job, payload)
                            except Exception:
                                # A status hook must never turn a durable
                                # completion into a stale-lease failure.
                                logger.exception(
                                    "TASK_REVIEW_COMPLETION_HOOK_FAILED task_id=%s "
                                    "revision_id=%s job_id=%s",
                                    self.task_id,
                                    revision_id,
                                    completed_job.review_job_id,
                                )
                        continue

                    failure_items = list(result.failed) + list(result.rejected)
                    failure = failure_items[0] if failure_items else {
                        "error": result.fanin_error or "task review produced no result",
                    }
                    error = str(failure.get("error") or failure.get("reason") or "task review failed")
                    retryable = job.attempt < total_attempts
                    failed_job = queue.fail(
                        job.review_job_id,
                        job.lease_id,
                        job.attempt,
                        error,
                        retryable=retryable,
                    )
                    persist()
                    if on_failed is not None:
                        try:
                            on_failed(
                                failed_job,
                                {**copy.deepcopy(failure), "retryable": retryable},
                            )
                        except Exception:
                            logger.exception(
                                "TASK_REVIEW_FAILURE_HOOK_FAILED task_id=%s "
                                "revision_id=%s job_id=%s",
                                self.task_id,
                                revision_id,
                                failed_job.review_job_id,
                            )
                except Exception as error:
                    retryable = job.attempt < total_attempts
                    failed_job = queue.fail(
                        job.review_job_id,
                        job.lease_id,
                        job.attempt,
                        f"{type(error).__name__}: {error}",
                        retryable=retryable,
                    )
                    persist()
                    logger.exception(
                        "TASK_REVIEW_JOB_FAILED task_id=%s revision_id=%s job_id=%s "
                        "group_id=%s item_id=%s worker_id=%s retryable=%s",
                        self.task_id,
                        revision_id,
                        job.review_job_id,
                        job.group_id,
                        job.item_id,
                        worker_id,
                        retryable,
                    )
                    if on_failed is not None:
                        try:
                            on_failed(
                                failed_job,
                                {
                                    "error": f"{type(error).__name__}: {error}",
                                    "retryable": retryable,
                                },
                            )
                        except Exception:
                            logger.exception(
                                "TASK_REVIEW_FAILURE_HOOK_FAILED task_id=%s "
                                "revision_id=%s job_id=%s",
                                self.task_id,
                                revision_id,
                                failed_job.review_job_id,
                            )

        with ThreadPoolExecutor(max_workers=slot_count) as pool:
            futures = [pool.submit(slot_loop, index) for index in range(1, slot_count + 1)]
            for future in futures:
                future.result()
        logger.info(
            "TASK_REVIEW_QUEUE_COMPLETED task_id=%s revision_id=%s counts=%s",
            self.task_id,
            revision_id,
            queue.counts(),
        )
        return queue
