"""Durable task-level review queue primitives for the Zhongshu Critic stage."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
import copy
import hashlib
import json
import threading
import uuid
from typing import Any


REVIEW_JOB_PENDING = "PENDING"
REVIEW_JOB_RUNNING = "RUNNING"
REVIEW_JOB_COMPLETED = "COMPLETED"
REVIEW_JOB_RETRYABLE = "RETRYABLE"
REVIEW_JOB_CANCELLED = "CANCELLED"
REVIEW_JOB_HUMAN_GATE = "HUMAN_GATE"

_TERMINAL_STATUSES = frozenset({
    REVIEW_JOB_COMPLETED,
    REVIEW_JOB_CANCELLED,
    REVIEW_JOB_HUMAN_GATE,
})


class ReviewQueueError(ValueError):
    """Base error for invalid or stale task-review queue operations."""


class DuplicateReviewJob(ReviewQueueError):
    """Raised when one revision contains the same task identity twice."""


class StaleReviewLease(ReviewQueueError):
    """Raised when a result does not belong to the current task lease."""


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _as_text(value: Any) -> str:
    return str(value or "").strip()


def _items_from_group(group: dict[str, Any]) -> list[dict[str, Any]]:
    value = group.get("items")
    if isinstance(value, list):
        return [copy.deepcopy(item) for item in value if isinstance(item, dict)]
    return []


def _group_id(group: dict[str, Any], index: int) -> str:
    return _as_text(
        group.get("group_id")
        or group.get("candidate_group_id")
        or f"group-{index + 1:06d}"
    )


def _group_context(group: dict[str, Any], group_id: str) -> dict[str, Any]:
    return {
        "group_id": group_id,
        **{
            key: copy.deepcopy(group[key])
            for key in (
                "title", "objective", "dependencies", "shared_acceptance",
                "suggested_order",
            )
            if key in group
        },
    }


def _item_id(item: dict[str, Any]) -> str:
    return _as_text(item.get("item_id") or item.get("task_id"))


def dependency_refs_for_item(
    item: dict[str, Any],
    items_by_id: dict[str, dict[str, Any]] | None = None,
) -> list[Any]:
    """Return stable dependency references, including endpoint content hashes."""
    dependencies = item.get("dependencies")
    if not isinstance(dependencies, list):
        dependencies = []
    refs: list[Any] = []
    for dependency in dependencies:
        if isinstance(dependency, dict):
            ref = {
                key: copy.deepcopy(dependency[key])
                for key in (
                    "from", "to", "target", "item_id", "requirement_id", "kind"
                )
                if key in dependency
            }
        else:
            ref = _as_text(dependency)
        dependency_id = ""
        if isinstance(ref, dict):
            dependency_id = _as_text(
                ref.get("item_id") or ref.get("to") or ref.get("target")
            )
        else:
            dependency_id = _as_text(ref)
        if isinstance(items_by_id, dict) and dependency_id:
            dependency_item = items_by_id.get(dependency_id)
            if isinstance(dependency_item, dict):
                if isinstance(ref, str):
                    ref = {"item_id": ref}
                ref["dependency_group_id"] = _as_text(
                    dependency_item.get("group_id")
                )
                ref["dependency_task_hash"] = stable_hash(dependency_item)
        refs.append(ref)
    return refs


@dataclass
class ReviewJob:
    review_job_id: str
    revision_id: str
    group_id: str
    item_id: str
    group_order: int = 0
    item_order: int = 0
    status: str = REVIEW_JOB_PENDING
    lease_id: str = ""
    worker_id: str = ""
    attempt: int = 0
    request_id: str = ""
    idempotency_key: str = ""
    reuse_request: bool = False
    task_hash: str = ""
    dependency_hash: str = ""
    result_path: str = ""
    last_error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ReviewJob":
        if not isinstance(value, dict):
            raise TypeError("review job must be an object")
        allowed = {item.name for item in fields(cls)}
        data = {name: value[name] for name in allowed if name in value}
        job = cls(**data)
        # Older design snapshots called the durable lease state CLAIMED.
        # Normalize it to the single runtime lease state before validation.
        if job.status == "CLAIMED":
            job.status = REVIEW_JOB_RUNNING
        if not job.review_job_id or not job.revision_id or not job.group_id or not job.item_id:
            raise ReviewQueueError("review job identity is incomplete")
        if job.status not in {
            REVIEW_JOB_PENDING,
            REVIEW_JOB_RUNNING,
            REVIEW_JOB_COMPLETED,
            REVIEW_JOB_RETRYABLE,
            REVIEW_JOB_CANCELLED,
            REVIEW_JOB_HUMAN_GATE,
        }:
            raise ReviewQueueError(f"unknown review job status: {job.status}")
        for name in (
            "review_job_id", "revision_id", "group_id", "item_id", "lease_id",
            "worker_id", "request_id", "idempotency_key", "task_hash",
            "dependency_hash", "result_path", "last_error",
        ):
            if not isinstance(getattr(job, name), str):
                raise TypeError(f"review job field {name} must be a string")
        for name in ("group_order", "item_order", "attempt"):
            value = getattr(job, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise TypeError(f"review job field {name} must be a non-negative integer")
        if not isinstance(job.reuse_request, bool):
            raise TypeError("review job field reuse_request must be a boolean")
        return job

    @property
    def identity(self) -> tuple[str, str, str]:
        return (self.revision_id, self.group_id, self.item_id)

    @property
    def terminal(self) -> bool:
        return self.status in _TERMINAL_STATUSES


@dataclass
class TaskReviewQueue:
    revision_id: str
    plan_hash: str
    jobs: list[ReviewJob] = field(default_factory=list)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not _as_text(self.revision_id):
            raise ReviewQueueError("review queue revision_id is required")
        if not _as_text(self.plan_hash):
            raise ReviewQueueError("review queue plan_hash is required")
        seen_ids: set[str] = set()
        seen_identity: set[tuple[str, str, str]] = set()
        for job in self.jobs:
            if job.review_job_id in seen_ids:
                raise DuplicateReviewJob(job.review_job_id)
            if job.identity in seen_identity:
                raise DuplicateReviewJob(
                    f"{job.revision_id}/{job.group_id}/{job.item_id}"
                )
            if job.revision_id != self.revision_id:
                raise ReviewQueueError("review job revision mismatch")
            seen_ids.add(job.review_job_id)
            seen_identity.add(job.identity)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "TaskReviewQueue":
        if not isinstance(value, dict):
            raise TypeError("task review queue must be an object")
        raw_jobs = value.get("jobs", [])
        if not isinstance(raw_jobs, list):
            raise TypeError("task review queue jobs must be an array")
        jobs = [ReviewJob.from_dict(item) for item in raw_jobs]
        if not jobs:
            raise ReviewQueueError("task review queue must contain jobs")
        if not isinstance(value.get("revision_id"), str):
            raise TypeError("task review queue revision_id must be a string")
        if not isinstance(value.get("plan_hash"), str):
            raise TypeError("task review queue plan_hash must be a string")
        revision_id = _as_text(value.get("revision_id"))
        plan_hash = _as_text(value.get("plan_hash"))
        if not revision_id:
            raise ReviewQueueError("task review queue revision_id is required")
        if not plan_hash:
            raise ReviewQueueError("task review queue plan_hash is required")
        return cls(
            revision_id=revision_id,
            plan_hash=plan_hash,
            jobs=jobs,
        )

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "revision_id": self.revision_id,
                "plan_hash": self.plan_hash,
                "jobs": [job.to_dict() for job in self.jobs],
            }

    def _find(self, review_job_id: str) -> ReviewJob:
        for job in self.jobs:
            if job.review_job_id == review_job_id:
                return job
        raise ReviewQueueError(f"review job not found: {review_job_id}")

    def _assert_lease(self, job: ReviewJob, lease_id: str, attempt: int) -> None:
        if (
            job.status != REVIEW_JOB_RUNNING
            or job.lease_id != lease_id
            or job.attempt != attempt
        ):
            raise StaleReviewLease(
                f"stale review lease: job={job.review_job_id} "
                f"status={job.status} attempt={job.attempt}"
            )

    def claim_next(self, worker_id: str) -> ReviewJob | None:
        worker = _as_text(worker_id)
        if not worker:
            raise ReviewQueueError("worker_id is required")
        with self._lock:
            candidates = sorted(
                (
                    job for job in self.jobs
                    if job.status in {REVIEW_JOB_PENDING, REVIEW_JOB_RETRYABLE}
                ),
                key=lambda job: (
                    job.group_order,
                    job.item_order,
                    job.group_id,
                    job.item_id,
                ),
            )
            if not candidates:
                return None
            job = candidates[0]
            if job.status == REVIEW_JOB_RETRYABLE:
                job.status = REVIEW_JOB_PENDING
            # A recovered RUNNING job keeps its original request identity so
            # the driver can query the existing external request after a
            # process restart. A normal retry receives a fresh attempt key.
            if job.reuse_request and job.request_id:
                job.reuse_request = False
            else:
                job.attempt += 1
                job.request_id = f"{job.review_job_id}:attempt-{job.attempt}"
                job.idempotency_key = (
                    f"{job.review_job_id}:attempt-{job.attempt}"
                )
            job.lease_id = uuid.uuid4().hex
            job.worker_id = worker
            job.status = REVIEW_JOB_RUNNING
            job.last_error = ""
            return copy.deepcopy(job)

    def recover_running(self) -> int:
        """Return abandoned leases to the queue without changing request IDs.

        The task lock guarantees that this is called by the replacement
        orchestrator after a process restart, not by a second live worker.
        Keeping ``request_id`` and ``idempotency_key`` lets the transport first
        locate the old external request instead of blindly dispatching a copy.
        """
        recovered = 0
        with self._lock:
            for job in self.jobs:
                if job.status != REVIEW_JOB_RUNNING:
                    continue
                job.status = REVIEW_JOB_PENDING
                job.lease_id = ""
                job.worker_id = ""
                job.reuse_request = bool(job.request_id)
                recovered += 1
        return recovered

    def complete(
        self,
        review_job_id: str,
        lease_id: str,
        attempt: int,
        result_path: str,
    ) -> ReviewJob:
        result = _as_text(result_path)
        if not result:
            raise ReviewQueueError("completed review job requires result_path")
        with self._lock:
            job = self._find(review_job_id)
            self._assert_lease(job, lease_id, attempt)
            job.status = REVIEW_JOB_COMPLETED
            job.result_path = result
            job.lease_id = ""
            job.reuse_request = False
            return copy.deepcopy(job)

    def fail(
        self,
        review_job_id: str,
        lease_id: str,
        attempt: int,
        error: str,
        retryable: bool,
    ) -> ReviewJob:
        with self._lock:
            job = self._find(review_job_id)
            self._assert_lease(job, lease_id, attempt)
            job.last_error = _as_text(error)[:1000]
            job.lease_id = ""
            if retryable:
                job.status = REVIEW_JOB_PENDING
                # A rejected/failed response is a new delivery attempt. Do
                # not accidentally bind it to the previous request forever.
                job.request_id = ""
                job.idempotency_key = ""
                job.reuse_request = False
            else:
                job.status = REVIEW_JOB_HUMAN_GATE
                job.reuse_request = False
            return copy.deepcopy(job)

    def cancel(self, review_job_id: str, reason: str) -> ReviewJob:
        with self._lock:
            job = self._find(review_job_id)
            if job.status == REVIEW_JOB_RUNNING:
                raise ReviewQueueError("cannot cancel a running review job")
            job.status = REVIEW_JOB_CANCELLED
            job.last_error = _as_text(reason)[:1000]
            job.lease_id = ""
            job.reuse_request = False
            return copy.deepcopy(job)

    def carry_forward(
        self,
        review_job_id: str,
        result_path: str,
        *,
        worker_id: str = "carry-forward",
    ) -> ReviewJob:
        """Mark an unchanged task as reviewed in a new revision snapshot."""
        with self._lock:
            job = self._find(review_job_id)
            if job.status != REVIEW_JOB_PENDING:
                raise ReviewQueueError(
                    f"cannot carry forward non-pending review job: {review_job_id}"
                )
            job.status = REVIEW_JOB_COMPLETED
            job.worker_id = _as_text(worker_id) or "carry-forward"
            job.result_path = _as_text(result_path)
            job.last_error = ""
            job.reuse_request = False
            return copy.deepcopy(job)

    def requeue(self, review_job_ids: list[str], revision_id: str) -> None:
        if _as_text(revision_id) != self.revision_id:
            raise ReviewQueueError("requeue revision mismatch")
        with self._lock:
            for review_job_id in review_job_ids:
                job = self._find(_as_text(review_job_id))
                if job.status == REVIEW_JOB_RUNNING:
                    raise ReviewQueueError(
                        f"cannot requeue running review job: {review_job_id}"
                    )
                job.status = REVIEW_JOB_PENDING
                job.lease_id = ""
                job.worker_id = ""
                job.request_id = ""
                job.idempotency_key = ""
                job.reuse_request = False
                job.result_path = ""
                job.last_error = ""

    def all_terminal(self) -> bool:
        with self._lock:
            return bool(self.jobs) and all(job.terminal for job in self.jobs)

    def all_completed(self) -> bool:
        with self._lock:
            return bool(self.jobs) and all(
                job.status == REVIEW_JOB_COMPLETED
                for job in self.jobs
            )

    def pending_ids(self) -> list[str]:
        with self._lock:
            return [
                job.review_job_id
                for job in self.jobs
                if job.status in {REVIEW_JOB_PENDING, REVIEW_JOB_RETRYABLE}
            ]

    def nonterminal_count(self) -> int:
        with self._lock:
            return sum(not job.terminal for job in self.jobs)

    def job_for_item(self, group_id: str, item_id: str) -> ReviewJob:
        group = _as_text(group_id)
        item = _as_text(item_id)
        with self._lock:
            for job in self.jobs:
                if job.group_id == group and job.item_id == item:
                    return copy.deepcopy(job)
        raise ReviewQueueError(f"review job not found: {group}/{item}")

    def counts(self) -> dict[str, int]:
        with self._lock:
            result: dict[str, int] = {}
            for job in self.jobs:
                result[job.status] = result.get(job.status, 0) + 1
            return result


def build_review_jobs(
    plan: dict[str, Any],
    revision_id: str,
    *,
    plan_hash: str = "",
) -> TaskReviewQueue:
    """Flatten one immutable formal plan into one job per task."""
    if not isinstance(plan, dict):
        raise ReviewQueueError("candidate plan must be an object")
    revision = _as_text(revision_id)
    if not revision:
        raise ReviewQueueError("revision_id is required")

    raw_groups = plan.get("groups")
    if not isinstance(raw_groups, list) or not raw_groups:
        raw_groups = plan.get("candidate_groups")
    raw_items = plan.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raw_items = plan.get("candidate_items")
    if not isinstance(raw_items, list):
        raw_items = []

    items_by_id = {
        _item_id(item): copy.deepcopy(item)
        for item in raw_items
        if isinstance(item, dict) and _item_id(item)
    }
    groups: list[tuple[int, str, list[dict[str, Any]], dict[str, Any]]] = []
    if isinstance(raw_groups, list) and raw_groups:
        for group_order, raw_group in enumerate(raw_groups):
            if not isinstance(raw_group, dict):
                continue
            group_id = _group_id(raw_group, group_order)
            group_items = _items_from_group(raw_group)
            if not group_items:
                references = raw_group.get("item_ids")
                if not isinstance(references, list):
                    references = raw_group.get("related_items")
                if isinstance(references, list):
                    group_items = [
                        copy.deepcopy(items_by_id[str(item_id)])
                        for item_id in references
                        if str(item_id) in items_by_id
                    ]
            groups.append((
                group_order,
                group_id,
                group_items,
                _group_context(raw_group, group_id),
            ))
    else:
        grouped: dict[str, list[dict[str, Any]]] = {}
        group_orders: dict[str, int] = {}
        for item_order, item in enumerate(raw_items):
            if not isinstance(item, dict):
                continue
            group_id = _as_text(item.get("group_id") or item.get("group_key") or "group-000001")
            group_orders.setdefault(group_id, len(group_orders))
            grouped.setdefault(group_id, []).append(copy.deepcopy(item))
        groups = [
            (
                group_orders[group_id],
                group_id,
                grouped[group_id],
                {"group_id": group_id},
            )
            for group_id in sorted(grouped, key=lambda value: group_orders[value])
        ]

    # Formal compact plans are allowed to carry complete items only inside
    # groups. Populate the endpoint index before hashing dependencies so a
    # changed upstream task invalidates the dependent task's review job.
    for _group_order, group_id, group_items, _group_context_value in groups:
        for raw_item in group_items:
            if not isinstance(raw_item, dict) or not _item_id(raw_item):
                continue
            item = copy.deepcopy(raw_item)
            item.setdefault("group_id", group_id)
            item_id = _item_id(item)
            existing = items_by_id.get(item_id)
            if existing is None:
                items_by_id[item_id] = item
            elif not _as_text(existing.get("group_id")):
                # Flat ``plan.items`` often omits group_id; enrich that
                # endpoint without replacing the canonical task body.
                existing["group_id"] = group_id

    jobs: list[ReviewJob] = []
    seen_identities: set[tuple[str, str]] = set()
    for group_order, group_id, group_items, group_context in groups:
        for item_order, item in enumerate(group_items):
            item_id = _item_id(item)
            if not item_id:
                raise ReviewQueueError(f"group {group_id} contains an item without item_id")
            identity = (group_id, item_id)
            if identity in seen_identities:
                raise DuplicateReviewJob(f"duplicate task item: {group_id}/{item_id}")
            seen_identities.add(identity)
            item = copy.deepcopy(item)
            item.setdefault("group_id", group_id)
            dependencies = dependency_refs_for_item(item, items_by_id)
            jobs.append(
                ReviewJob(
                    review_job_id=f"zhongshu:{revision}:{group_id}:{item_id}",
                    revision_id=revision,
                    group_id=group_id,
                    item_id=item_id,
                    group_order=group_order,
                    item_order=item_order,
                    task_hash=stable_hash({
                        "task": item,
                        "group_context": group_context,
                    }),
                    dependency_hash=stable_hash(dependencies),
                )
            )

    if not jobs:
        raise ReviewQueueError("candidate plan contains no reviewable tasks")
    return TaskReviewQueue(
        revision_id=revision,
        plan_hash=_as_text(plan_hash) or stable_hash(plan),
        jobs=jobs,
    )


def task_review_queue_from_payload(value: Any) -> TaskReviewQueue | None:
    if not isinstance(value, dict) or not value:
        return None
    return TaskReviewQueue.from_dict(value)
