"""Durable per-task Critic review queue for Zhongshu task-queue mode.

This module is pure: it flattens a Solver task graph into one review job per
``(revision_id, group_id, item_id)``, owns lease-based claiming, and computes
the deterministic structural gate.  It performs no I/O and knows nothing about
transport, persistence, or notifications; the state layer persists its snapshot
and the runtime dispatches one external request per claimed job.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, replace
import hashlib
import json
import threading
import uuid
from typing import Any, Iterable, Mapping


PENDING = "PENDING"
RUNNING = "RUNNING"
COMPLETED = "COMPLETED"
RETRYABLE = "RETRYABLE"
CANCELLED = "CANCELLED"
HUMAN_GATE = "HUMAN_GATE"


class StaleReviewLease(RuntimeError):
    """Raised when a completion/failure does not own the current lease."""


class ReviewQueueError(RuntimeError):
    """Raised when a queue operation is structurally invalid."""


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def review_job_id(revision_id: str, group_id: str, item_id: str) -> str:
    return f"zhongshu:{revision_id}:{group_id}:{item_id}"


def _surface_text(value: object) -> str:
    return " ".join(str(value or "").split())


def _surface_list(value: object) -> list[str]:
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_surface_text(item) for item in value if _surface_text(item)]
    text = _surface_text(value)
    return [text] if text else []


def review_surface(item: Mapping[str, Any], *, group_item_ids: Iterable[str] = ()) -> dict[str, Any]:
    """Project an item onto exactly the fields a Critic capsule exposes.

    The hash of this projection decides whether a task must be re-reviewed, so
    it must exclude everything the Critic never sees (Solver rationale, benefit,
    tradeoffs, unknowns, risks).  Whitespace and collection order are normalized
    so formatting-only edits do not invalidate a prior approval.
    """

    return {
        "item_id": _surface_text(item.get("item_id") or item.get("task_key")),
        "group_id": _surface_text(item.get("group_id")),
        "group_item_ids": sorted({_surface_text(v) for v in group_item_ids if _surface_text(v)}),
        "title": _surface_text(item.get("title")),
        "objective": _surface_text(item.get("objective")),
        "dependencies": sorted(_surface_list(item.get("dependencies"))),
        "source_requirement_ids": sorted(
            _surface_list(item.get("source_requirement_ids") or item.get("requirement_ids"))
        ),
        "acceptance_signals": _surface_list(item.get("acceptance_signals") or item.get("acceptance")),
    }


@dataclass(frozen=True)
class ReviewJob:
    review_job_id: str
    revision_id: str
    group_id: str
    item_id: str
    status: str = PENDING
    lease_id: str = ""
    worker_id: str = ""
    attempt: int = 0
    request_id: str = ""
    task_hash: str = ""
    dependency_hash: str = ""
    result_path: str = ""

    @property
    def idempotency_key(self) -> str:
        return f"{self.review_job_id}:attempt-{max(1, self.attempt)}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ReviewJob":
        if not isinstance(value, Mapping):
            raise ReviewQueueError("review job must be an object")
        data = {name: value.get(name) for name in cls.__dataclass_fields__}
        review_id = str(data.get("review_job_id") or "").strip()
        revision_id = str(data.get("revision_id") or "").strip()
        if not revision_id:
            raise ReviewQueueError("review job revision_id is required")
        status = str(data.get("status") or PENDING).strip().upper()
        if status not in {PENDING, RUNNING, COMPLETED, RETRYABLE, CANCELLED, HUMAN_GATE}:
            raise ReviewQueueError(f"review job has unknown status: {status}")
        return cls(
            review_job_id=review_id or review_job_id(
                revision_id,
                str(data.get("group_id") or ""),
                str(data.get("item_id") or ""),
            ),
            revision_id=revision_id,
            group_id=str(data.get("group_id") or "").strip(),
            item_id=str(data.get("item_id") or "").strip(),
            status=status,
            lease_id=str(data.get("lease_id") or ""),
            worker_id=str(data.get("worker_id") or ""),
            attempt=int(data.get("attempt") or 0),
            request_id=str(data.get("request_id") or ""),
            task_hash=str(data.get("task_hash") or ""),
            dependency_hash=str(data.get("dependency_hash") or ""),
            result_path=str(data.get("result_path") or ""),
        )


def flatten_plan_items(plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return one normalized item record per task, preserving group ownership."""

    if not isinstance(plan, Mapping):
        raise ReviewQueueError("plan must be an object")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(raw: Mapping[str, Any], group_id: str) -> None:
        item_id = str(raw.get("item_id") or raw.get("task_key") or "").strip()
        if not item_id:
            raise ReviewQueueError("plan item is missing item_id")
        if item_id in seen:
            return
        seen.add(item_id)
        result.append(
            {
                "item_id": item_id,
                "group_id": str(raw.get("group_id") or group_id or "").strip(),
                "dependencies": [
                    str(dep).strip()
                    for dep in (raw.get("dependencies") or [])
                    if str(dep).strip()
                ],
                "raw": dict(raw),
            }
        )

    items = plan.get("items")
    if isinstance(items, list):
        for raw in items:
            if isinstance(raw, Mapping):
                add(raw, str(raw.get("group_id") or ""))
    groups = plan.get("groups")
    if isinstance(groups, list):
        for group in groups:
            if not isinstance(group, Mapping):
                continue
            group_id = str(group.get("group_id") or "")
            for raw in group.get("items") or []:
                if isinstance(raw, Mapping):
                    add(raw, group_id)
    return result


def build_review_jobs(
    plan: Mapping[str, Any],
    revision_id: str,
    *,
    plan_hash: str = "",
) -> "TaskReviewQueue":
    """Flatten a plan into a fresh queue of PENDING review jobs."""

    revision = str(revision_id or "").strip()
    if not revision:
        raise ReviewQueueError("revision_id is required")
    records = flatten_plan_items(plan)
    group_members: dict[str, list[str]] = {}
    for record in records:
        group_members.setdefault(str(record["group_id"]), []).append(str(record["item_id"]))
    task_hashes = {
        record["item_id"]: canonical_hash(
            review_surface(
                record["raw"],
                group_item_ids=group_members.get(str(record["group_id"]), []),
            )
        )
        for record in records
    }
    jobs: list[ReviewJob] = []
    for record in records:
        item_id = record["item_id"]
        dependency_material = [
            {"item_id": dep, "task_hash": task_hashes.get(dep, "")}
            for dep in sorted(record["dependencies"])
        ]
        jobs.append(
            ReviewJob(
                review_job_id=review_job_id(revision, record["group_id"], item_id),
                revision_id=revision,
                group_id=record["group_id"],
                item_id=item_id,
                task_hash=task_hashes[item_id],
                dependency_hash=canonical_hash(dependency_material),
            )
        )
    return TaskReviewQueue(revision, plan_hash, jobs)


class TaskReviewQueue:
    """Lease-based, coverage-checked review queue with a durable snapshot."""

    def __init__(
        self,
        revision_id: str,
        plan_hash: str,
        jobs: Iterable[ReviewJob],
        *,
        group_index: int = 0,
    ) -> None:
        self.revision_id = str(revision_id or "")
        self.plan_hash = str(plan_hash or "")
        self.group_index = int(group_index or 0)
        self._jobs: dict[str, ReviewJob] = {}
        self._order: list[str] = []
        for job in jobs:
            if job.review_job_id in self._jobs:
                raise ReviewQueueError(f"duplicate review job: {job.review_job_id}")
            self._jobs[job.review_job_id] = job
            self._order.append(job.review_job_id)
        self._lock = threading.RLock()

    @property
    def jobs(self) -> tuple[ReviewJob, ...]:
        return tuple(self._jobs[job_id] for job_id in self._order)

    def counts(self) -> dict[str, int]:
        return dict(Counter(job.status for job in self.jobs))

    def job_for_item(self, group_id: str, item_id: str) -> ReviewJob:
        for job in self.jobs:
            if job.item_id == str(item_id) and (
                not group_id or job.group_id == str(group_id)
            ):
                return job
        raise ReviewQueueError(f"no review job for {group_id}:{item_id}")

    def get(self, job_id: str) -> ReviewJob | None:
        return self._jobs.get(str(job_id))

    def claim_next(self, worker_id: str) -> ReviewJob | None:
        worker = str(worker_id or "")
        if not worker:
            raise ReviewQueueError("worker_id is required to claim a job")
        with self._lock:
            for job_id in self._order:
                job = self._jobs[job_id]
                if job.status not in {PENDING, RETRYABLE}:
                    continue
                recovering = bool(job.request_id) and job.attempt > 0
                attempt = job.attempt if recovering else job.attempt + 1
                request_id = job.request_id or f"{job.review_job_id}:attempt-{attempt}"
                claimed = replace(
                    job,
                    status=RUNNING,
                    lease_id=uuid.uuid4().hex,
                    worker_id=worker,
                    attempt=attempt,
                    request_id=request_id,
                    result_path="",
                )
                self._jobs[job_id] = claimed
                return claimed
        return None

    def complete(
        self,
        job_id: str,
        lease_id: str,
        attempt: int,
        result_path: str,
    ) -> ReviewJob:
        with self._lock:
            job = self._require_lease(job_id, lease_id, attempt)
            completed = replace(job, status=COMPLETED, result_path=str(result_path or ""))
            self._jobs[job.review_job_id] = completed
            return completed

    def fail(
        self,
        job_id: str,
        lease_id: str,
        attempt: int,
        error: str,
        *,
        retryable: bool,
    ) -> ReviewJob:
        with self._lock:
            job = self._require_lease(job_id, lease_id, attempt)
            failed = replace(
                job,
                status=RETRYABLE if retryable else HUMAN_GATE,
                lease_id="",
                result_path="",
            )
            expected = failed.request_id if retryable else ""
            failed = replace(failed, request_id=expected)
            self._jobs[job.review_job_id] = failed
            return failed

    def _require_lease(self, job_id: str, lease_id: str, attempt: int) -> ReviewJob:
        job = self._jobs.get(str(job_id))
        if job is None:
            raise StaleReviewLease(f"unknown review job: {job_id}")
        if job.lease_id != str(lease_id) or int(job.attempt) != int(attempt):
            raise StaleReviewLease(
                f"stale review lease for {job_id}: "
                f"lease={lease_id!r} attempt={attempt} "
                f"current={job.lease_id!r} attempt={job.attempt}"
            )
        return job

    def recover_running(self) -> None:
        """Return RUNNING leases to the pool, keeping the external request."""

        with self._lock:
            for job_id, job in list(self._jobs.items()):
                if job.status == RUNNING:
                    self._jobs[job_id] = replace(job, status=RETRYABLE, lease_id="")

    def requeue(self, job_ids: Iterable[str], revision_id: str = "") -> None:
        revision = str(revision_id or self.revision_id)
        with self._lock:
            for raw_id in job_ids:
                job_id = str(raw_id)
                job = self._jobs.get(job_id)
                if job is None:
                    raise ReviewQueueError(f"cannot requeue unknown job: {job_id}")
                self._jobs[job_id] = replace(
                    job,
                    status=PENDING,
                    lease_id="",
                    worker_id="",
                    request_id="",
                    result_path="",
                    revision_id=revision,
                )

    def all_completed(self) -> bool:
        return bool(self._order) and all(
            job.status == COMPLETED for job in self.jobs
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "revision_id": self.revision_id,
            "plan_hash": self.plan_hash,
            "group_index": self.group_index,
            "jobs": [job.to_dict() for job in self.jobs],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TaskReviewQueue":
        if not isinstance(value, Mapping):
            raise ReviewQueueError("review queue must be an object")
        jobs_value = value.get("jobs", [])
        if not isinstance(jobs_value, list):
            raise ReviewQueueError("review queue jobs must be an array")
        return cls(
            revision_id=str(value.get("revision_id") or ""),
            plan_hash=str(value.get("plan_hash") or ""),
            group_index=int(value.get("group_index") or 0),
            jobs=[ReviewJob.from_dict(item) for item in jobs_value],
        )


def queue_from_dispatch_contexts(
    contexts: Iterable[Mapping[str, Any]],
    revision_id: str,
    plan_hash: str,
) -> "TaskReviewQueue | None":
    """Rebuild a completed queue from node bindings for fan-in aggregation."""

    jobs: list[ReviewJob] = []
    for context in contexts:
        if not isinstance(context, Mapping):
            continue
        if str(context.get("zhongshu_dispatch_mode") or "") != "task_review":
            continue
        revision = str(context.get("revision_id") or revision_id)
        group_id = str(context.get("group_id") or "")
        item_id = str(context.get("item_id") or "")
        jobs.append(
            ReviewJob(
                review_job_id=str(
                    context.get("review_job_id")
                    or review_job_id(revision, group_id, item_id)
                ),
                revision_id=revision,
                group_id=group_id,
                item_id=item_id,
                status=COMPLETED,
                task_hash=str(context.get("task_hash") or ""),
                dependency_hash=str(context.get("dependency_hash") or ""),
            )
        )
    if not jobs:
        return None
    return TaskReviewQueue(revision_id, plan_hash, jobs)


def structural_gate(plan: Mapping[str, Any]) -> list[str]:
    """Deterministic, LLM-free checks the orchestrator owns before freeze."""

    if not isinstance(plan, Mapping):
        return ["PLAN_NOT_OBJECT"]
    records = flatten_plan_items(plan)
    if not records:
        return ["PLAN_HAS_NO_ITEMS"]
    issues: list[str] = []

    by_item = {record["item_id"]: record for record in records}

    group_of: dict[str, str] = {}
    for group in plan.get("groups") or []:
        if not isinstance(group, Mapping):
            continue
        group_id = str(group.get("group_id") or "")
        for item_id in group.get("item_ids") or []:
            item_id = str(item_id)
            if item_id in group_of:
                issues.append(f"ITEM_IN_MULTIPLE_GROUPS:{item_id}")
            group_of[item_id] = group_id
    for record in records:
        item_id = record["item_id"]
        assigned = record["group_id"] or group_of.get(item_id, "")
        if not assigned:
            issues.append(f"ITEM_WITHOUT_GROUP:{item_id}")

    requirements = plan.get("requirements")
    known_requirements = {
        str(item.get("requirement_id"))
        for item in (requirements or [])
        if isinstance(item, Mapping) and item.get("requirement_id")
    }
    covered: set[str] = set()
    for record in records:
        raw = record["raw"]
        sources = [
            str(req).strip()
            for req in (raw.get("source_requirement_ids") or [])
            if str(req).strip()
        ]
        if not sources:
            issues.append(f"ITEM_WITHOUT_SOURCE_REQUIREMENT:{record['item_id']}")
        covered.update(sources)
    for requirement_id in sorted(known_requirements - covered):
        issues.append(f"REQUIREMENT_UNCOVERED:{requirement_id}")

    for record in records:
        for dependency in record["dependencies"]:
            if dependency not in by_item:
                issues.append(f"DEPENDENCY_UNKNOWN_ITEM:{record['item_id']}->{dependency}")

    for cycle in _dependency_cycles(by_item):
        issues.append("DEPENDENCY_CYCLE:" + ",".join(cycle))
    return issues


def _dependency_cycles(by_item: Mapping[str, dict[str, Any]]) -> list[list[str]]:
    color: dict[str, int] = {}
    cycles: list[list[str]] = []
    stack: list[str] = []

    def visit(node: str) -> None:
        color[node] = 1
        stack.append(node)
        for dependency in by_item.get(node, {}).get("dependencies", []):
            if dependency not in by_item:
                continue
            state = color.get(dependency, 0)
            if state == 1:
                start = stack.index(dependency)
                cycles.append(stack[start:] + [dependency])
            elif state == 0:
                visit(dependency)
        stack.pop()
        color[node] = 2

    for node in by_item:
        if color.get(node, 0) == 0:
            visit(node)
    return cycles


__all__ = [
    "CANCELLED",
    "COMPLETED",
    "HUMAN_GATE",
    "PENDING",
    "RETRYABLE",
    "ReviewJob",
    "ReviewQueueError",
    "RUNNING",
    "StaleReviewLease",
    "TaskReviewQueue",
    "build_review_jobs",
    "canonical_hash",
    "flatten_plan_items",
    "queue_from_dispatch_contexts",
    "review_job_id",
    "structural_gate",
]
