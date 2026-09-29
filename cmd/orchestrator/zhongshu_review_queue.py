"""Durable per-task Critic review queue for Zhongshu task-queue mode.

This module is pure: it flattens a Solver task graph into one review job per
``(revision_id, group_id, item_id)``, owns lease-based claiming, and computes
the deterministic structural gate.  It performs no I/O and knows nothing about
transport, persistence, or notifications; the state layer persists its snapshot
and the runtime dispatches one external request per claimed job.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Any, Iterable, Mapping

from .acceptance_standards import acceptance_signal_gate


PENDING = "PENDING"
RUNNING = "RUNNING"
COMPLETED = "COMPLETED"
RETRYABLE = "RETRYABLE"
CANCELLED = "CANCELLED"
HUMAN_GATE = "HUMAN_GATE"

# A group review job carries every member item in one capsule; beyond this
# many members the capsule stops being a bounded review unit and the plan
# must split the group (requirements 2026-09-26, Task 3).
GROUP_MAX_ITEMS = 6


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
        "acceptance_signals": sorted(_surface_list(item.get("acceptance_signals") or item.get("acceptance"))),
    }


@dataclass(frozen=True)
class ReviewJob:
    review_job_id: str
    revision_id: str
    group_id: str
    item_id: str
    status: str = PENDING
    task_hash: str = ""
    dependency_hash: str = ""

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
            task_hash=str(data.get("task_hash") or ""),
            dependency_hash=str(data.get("dependency_hash") or ""),
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


def task_surface_hash(item: Mapping[str, Any], group_item_ids: Iterable[str]) -> str:
    """Deterministic hash of the review surface of one task item."""

    return canonical_hash(review_surface(item, group_item_ids=group_item_ids))


def group_surface_hash(
    group_id: str,
    member_hashes: Iterable[str],
    *,
    doc_hash: str = "",
) -> str:
    """Deterministic hash of one group's review surface.

    Covers the requirement document and every member task surface, so a
    change to either invalidates the group approval ratchet while a
    formatting-only edit does not.
    """

    return canonical_hash(
        {
            "group_id": str(group_id or ""),
            "members": sorted(str(value) for value in member_hashes if str(value)),
            "doc_hash": str(doc_hash or ""),
        }
    )


@dataclass(frozen=True)
class GroupReviewJob:
    """One durable review job for a whole group (2026-09-26 group pipeline)."""

    review_job_id: str
    revision_id: str
    group_id: str
    status: str = PENDING
    task_hash: str = ""
    doc_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_group_review_jobs(
    plan: Mapping[str, Any],
    revision_id: str,
    *,
    plan_hash: str = "",
    doc_hashes: Mapping[str, str] | None = None,
) -> tuple[GroupReviewJob, ...]:
    """Flatten a plan into one PENDING review job per group."""

    revision = str(revision_id or "").strip()
    if not revision:
        raise ReviewQueueError("revision_id is required")
    records = flatten_plan_items(plan)
    members: dict[str, list[Mapping[str, Any]]] = {}
    order: list[str] = []
    for record in records:
        group_id = str(record["group_id"])
        if group_id not in members:
            members[group_id] = []
            order.append(group_id)
        members[group_id].append(record["raw"])
    jobs: list[GroupReviewJob] = []
    for group_id in order:
        raw_items = members[group_id]
        member_ids = [
            str(item.get("item_id") or "") for item in raw_items if item.get("item_id")
        ]
        member_hashes = [
            task_surface_hash(item, member_ids) for item in raw_items
        ]
        doc_hash = str((doc_hashes or {}).get(group_id) or "")
        jobs.append(
            GroupReviewJob(
                review_job_id=f"zhongshu:{revision}:{group_id}",
                revision_id=revision,
                group_id=group_id,
                task_hash=group_surface_hash(
                    group_id, member_hashes, doc_hash=doc_hash
                ),
                doc_hash=doc_hash,
            )
        )
    return tuple(jobs)


def build_group_capsule(
    *,
    group_id: str,
    revision_id: str,
    doc_markdown: str,
    members_full: Iterable[Mapping[str, Any]],
    members_surface: Iterable[tuple[str, str]] = (),
    findings: Iterable[Mapping[str, Any]] = (),
    finding_responses: Iterable[Mapping[str, Any]] = (),
    evidence: Iterable[tuple[str, Iterable[str]]] = (),
    header: str = "",
) -> str:
    """Assemble one group review capsule.

    Changed members ride verbatim; unchanged members are projected as a
    surface hash only (incremental capsule, requirements §6), so a group
    re-review does not re-pay full text for members nobody touched.
    """

    lines = [
        header
        or (
            f"Group review job: group {group_id} (revision {revision_id}). "
            "One group verdict only: APPROVE_GROUP or REVISE_GROUP (plus the "
            "escalation actions). Every finding must carry the owning item_id."
        ),
        "",
        "[Delivery discipline] The reply body must be exactly one complete "
        "structured JSON object matching the injected result contract: no "
        "prose, no Markdown, no code fences, no extra comments. The platform "
        "silently discards oversized posted output: if your result is too "
        "large to post, write the complete result to the result file "
        "described in the prompt bundle manifest and keep the posted reply a "
        "short summary (that fallback exists for overflow only; normal-size "
        "results stay inline). Cap long per-field texts (claims, conclusions, "
        "assessments) at ~400 characters each.",
        "",
        "[Requirement document]",
        str(doc_markdown or "").strip(),
    ]
    full = [dict(item) for item in members_full or ()]
    if full:
        lines += ["", "[Members - changed (verbatim)"]
        for item in full:
            lines.append(f"{item.get('item_id')} (group {group_id})")
            for field in (
                "title",
                "objective",
                "dependencies",
                "source_requirement_ids",
                "acceptance_signals",
            ):
                value = item.get(field)
                if value:
                    lines.append(f"  {field}: {value}")
    surface = [(str(item_id), str(digest)) for item_id, digest in members_surface or ()]
    if surface:
        lines += ["", "[Members - unchanged (surface only)]"]
        for item_id, digest in surface:
            lines.append(f"  {item_id} surface_hash={digest}")
    findings_list = [dict(entry) for entry in findings or ()]
    if findings_list:
        lines += ["", "[Active findings]"]
        for entry in findings_list:
            lines.append(
                f"  {entry.get('finding_id')} [item {entry.get('item_id') or '-'}] "
                f"{entry.get('claim') or entry.get('required_action') or ''}"
            )
    responses = [dict(entry) for entry in finding_responses or ()]
    if responses:
        lines += ["", "[Finding responses]"]
        for entry in responses:
            # Analyst answers carry the text under ``answer``; the Critic's own
            # disposition rows use ``response``.  Reading only ``response``
            # rendered every analyst answer blank, so the re-dispatched Critic
            # saw "no answer" and re-demanded evidence it already held
            # (task-20260929-c261a8: four evidence round-trips).
            text = str(
                entry.get("answer")
                or entry.get("response")
                or entry.get("note")
                or ""
            )
            lines.append(f"  {entry.get('finding_id')}: {text}")
    evidence_rows = [(str(item_id), tuple(records)) for item_id, records in evidence or ()]
    if evidence_rows:
        lines += ["", "[Evidence by member]"]
        for item_id, records in evidence_rows:
            lines.append(f"  {item_id}: {', '.join(records) or 'none'}")
    return "\n".join(lines) + "\n"


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
        record["item_id"]: task_surface_hash(
            record["raw"], group_members.get(str(record["group_id"]), [])
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
    """Coverage-checked review queue with a durable snapshot.

    The queue is a plain job register: production builds it fresh per node
    run (``build_review_jobs`` for dispatch enumeration, ``queue_from_dispatch_contexts``
    for fan-in aggregation) and never leases jobs out; claiming/failing is the
    runtime's job, not the queue's.
    """

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

    member_counts = Counter(
        str(record["group_id"] or group_of.get(record["item_id"], ""))
        for record in records
    )
    for group_id in sorted(member_counts):
        if group_id and member_counts[group_id] > GROUP_MAX_ITEMS:
            issues.append(f"GROUP_MAX_ITEMS:{group_id}:{member_counts[group_id]}")

    requirements = plan.get("requirements")
    known_requirements = {
        str(item.get("requirement_id"))
        for item in (requirements or [])
        if isinstance(item, Mapping) and item.get("requirement_id")
    }
    # A requirement the planner explicitly scoped out (with a reason) is no
    # longer part of the deliverable: dropping its covering items is a legal
    # discard, so the coverage check must not resurrect it.
    out_of_scope = {
        str(item.get("requirement_id"))
        for item in (requirements or [])
        if isinstance(item, Mapping)
        and item.get("requirement_id")
        and str(item.get("scope") or "").strip().lower() == "out"
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
    for requirement_id in sorted(known_requirements - covered - out_of_scope):
        issues.append(f"REQUIREMENT_UNCOVERED:{requirement_id}")

    for record in records:
        for dependency in record["dependencies"]:
            if dependency not in by_item:
                issues.append(f"DEPENDENCY_UNKNOWN_ITEM:{record['item_id']}->{dependency}")

    for cycle in _dependency_cycles(by_item):
        issues.append("DEPENDENCY_CYCLE:" + ",".join(cycle))
    issues.extend(acceptance_signal_gate(plan))
    return issues


def _dependency_cycles(by_item: Mapping[str, dict[str, Any]]) -> list[list[str]]:
    """Iterative DFS back-edge detection; deep graphs cannot exhaust the stack."""

    color: dict[str, int] = {}
    cycles: list[list[str]] = []
    for root in by_item:
        if color.get(root, 0):
            continue
        stack: list[tuple[str, int]] = [(root, 0)]
        path: list[str] = []
        while stack:
            node, dep_index = stack[-1]
            if dep_index == 0:
                color[node] = 1
                path.append(node)
            dependencies = [
                str(dep)
                for dep in by_item.get(node, {}).get("dependencies", [])
                if str(dep) in by_item
            ]
            if dep_index < len(dependencies):
                stack[-1] = (node, dep_index + 1)
                dependency = dependencies[dep_index]
                state = color.get(dependency, 0)
                if state == 1:
                    start = path.index(dependency)
                    cycles.append(path[start:] + [dependency])
                elif state == 0:
                    stack.append((dependency, 0))
            else:
                stack.pop()
                path.pop()
                color[node] = 2
    return cycles


__all__ = [
    "CANCELLED",
    "COMPLETED",
    "GROUP_MAX_ITEMS",
    "GroupReviewJob",
    "HUMAN_GATE",
    "PENDING",
    "RETRYABLE",
    "ReviewJob",
    "ReviewQueueError",
    "RUNNING",
    "TaskReviewQueue",
    "build_group_capsule",
    "build_group_review_jobs",
    "build_review_jobs",
    "canonical_hash",
    "flatten_plan_items",
    "group_surface_hash",
    "queue_from_dispatch_contexts",
    "review_job_id",
    "structural_gate",
    "task_surface_hash",
]
