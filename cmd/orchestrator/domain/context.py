"""Immutable aggregates that represent the workflow's durable domain state."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field

from .findings import Finding
from .errors import FailureRecord


@dataclass(frozen=True)
class TaskIdentity:
    """Stable identity shared by every workflow event and effect."""

    task_id: str
    issue_id: str
    project: str
    request_id: str


@dataclass(frozen=True)
class RequestState:
    """Immutable request metadata and the artifact containing its payload."""

    raw_request: str = ""
    payload_ref: str | None = None
    project_type: str = "unknown"
    task_type: str = "review"


@dataclass(frozen=True)
class ZhongshuParallelLimits:
    enabled: bool = True
    analyst_max_workers: int = 3
    analyst_default_workers: int = 3
    critic_max_workers: int = 6
    critic_default_workers: int = 6
    global_max_workers: int = 6
    per_task_max_workers: int = 6


@dataclass(frozen=True)
class MenxiaParallelLimits:
    enabled: bool = False
    max_concurrent_groups: int = 1
    max_concurrent_items: int = 3
    failure_policy: str = "continue_and_block_group"


@dataclass(frozen=True)
class ParallelState:
    """Configuration and immutable pointers for node execution."""

    zhongshu: ZhongshuParallelLimits = field(default_factory=ZhongshuParallelLimits)
    menxia: MenxiaParallelLimits = field(default_factory=MenxiaParallelLimits)
    group_index: int | None = None
    item_index: int | None = None
    menxia_snapshot_ref: str | None = None
    active_node_run_id: str | None = None


@dataclass(frozen=True)
class ProgressState:
    """Position of the main linear FSM."""

    state: str
    sequence: int
    entered_at: str
    resume_state: str | None = None


@dataclass(frozen=True)
class DeliveryState:
    """Correlation and lifecycle data for the active external dispatch."""

    active_request_id: str | None = None
    dispatch_operation_id: str | None = None
    idempotency_key: str | None = None
    status: str = "IDLE"
    external_message_id: str | None = None
    phase: str = ""
    role: str = ""
    expected_agent_id: str = ""
    last_sent_at: str = ""
    attempt: int = 0
    last_result_ref: str | None = None


@dataclass(frozen=True)
class RecoveryState:
    """Retry and blocking information owned by workflow recovery."""

    retry_count: int = 0
    max_retries: int = 3
    recoverable: bool = True
    blocked_reason: str | None = None
    external_retry_count: int = 0
    reply_retry_count: int = 0
    max_external_retries: int = 3
    max_reply_retries: int = 3
    max_state_write_retries: int = 3
    timeout_retry_count: int = 0
    max_timeout_retries: int = 3
    timeout_phase: str | None = None
    timeout_role: str | None = None
    timeout_request_id: str | None = None
    last_failure: FailureRecord | None = None
    no_progress_count: int = 0
    max_no_progress: int = 3


@dataclass(frozen=True)
class ReviewState:
    """Immutable review ledger projection used by state decisions."""

    revision_id: str = ""
    active_group_id: str | None = None
    active_item_id: str | None = None
    findings: tuple[Finding, ...] = ()
    zhongshu_revision_round: int = 0
    max_zhongshu_revision_rounds: int = 8
    freeze_check_attempt: int = 0
    max_freeze_check_attempts: int = 2
    item_revision_round: int = 0
    max_item_revision_rounds: int = 3
    last_reply_fingerprint: str = ""
    plan_ref: str | None = None
    plan_hash: str | None = None
    task_graph_ref: str | None = None
    task_items: tuple[ReviewTaskItem, ...] = ()
    task_groups: tuple[ReviewTaskGroup, ...] = ()
    completed_item_ids: tuple[str, ...] = ()

    def next_menxia_item(self) -> ReviewTaskItem | None:
        completed = set(self.completed_item_ids)
        items = sorted(self.task_items, key=lambda item: (item.order, item.item_id))
        for item in items:
            if item.item_id in completed:
                continue
            if set(item.dependencies).issubset(completed):
                return item
        return None


@dataclass(frozen=True)
class AuditState:
    """Operational history kept outside business decisions."""

    sent_notification_keys: tuple[str, ...] = ()
    heartbeat_count: int = 0
    last_heartbeat_at: str | None = None
    reply_history_ref: str | None = None
    last_artifact_id: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True)
class HumanGateState:
    """Pending human decision and the state to resume afterwards."""

    decision_id: str
    reason_code: str
    resume_state: str
    message_id: str | None = None


@dataclass(frozen=True)
class ReviewTaskItem:
    """Immutable Menxia task-graph item projection."""

    item_id: str
    group_id: str
    dependencies: tuple[str, ...] = ()
    order: int = 0
    status: str = "PENDING"


@dataclass(frozen=True)
class ReviewTaskGroup:
    """Immutable Menxia group projection."""

    group_id: str
    item_ids: tuple[str, ...] = ()
    order: int = 0
    status: str = "PENDING"


@dataclass(frozen=True)
class ProgressUpdate:
    """Typed changes to the progress aggregate."""

    state: str | None = None
    resume_state: str | None = None
    entered_at: str | None = None


@dataclass(frozen=True)
class ParallelUpdate:
    """Typed changes to the active bounded node execution."""

    active_node_run_id: str | None = None
    clear_active_node_run: bool = False


@dataclass(frozen=True)
class ReviewUpdate:
    """Typed changes to the review aggregate."""

    revision_id: str | None = None
    active_group_id: str | None = None
    active_item_id: str | None = None
    findings: tuple[Finding, ...] | None = None
    plan_ref: str | None = None
    plan_hash: str | None = None
    task_graph_ref: str | None = None
    task_items: tuple[ReviewTaskItem, ...] | None = None
    task_groups: tuple[ReviewTaskGroup, ...] | None = None
    completed_item_ids: tuple[str, ...] | None = None


@dataclass(frozen=True)
class DeliveryUpdate:
    """Typed changes to the delivery aggregate."""

    active_request_id: str | None = None
    status: str | None = None
    dispatch_operation_id: str | None = None
    external_message_id: str | None = None
    idempotency_key: str | None = None
    phase: str | None = None
    role: str | None = None
    expected_agent_id: str | None = None
    last_sent_at: str | None = None
    attempt: int | None = None
    last_result_ref: str | None = None


@dataclass(frozen=True)
class HumanGateUpdate:
    """Typed changes to the human-gate aggregate."""

    decision_id: str | None = None
    resume_state: str | None = None
    reason_code: str | None = None
    message_id: str | None = None
    clear_gate: bool = False


@dataclass(frozen=True)
class AuditUpdate:
    """Typed operational bookkeeping updates."""

    sent_notification_key: str | None = None
    heartbeat_count: int | None = None
    last_heartbeat_at: str | None = None
    reply_history_ref: str | None = None
    last_artifact_id: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True)
class RecoveryUpdate:
    """Typed changes to retry and blocking state."""

    retry_count: int | None = None
    blocked_reason: str | None = None
    external_retry_count: int | None = None
    reply_retry_count: int | None = None
    timeout_retry_count: int | None = None
    last_failure: FailureRecord | None = None
    no_progress_count: int | None = None


@dataclass(frozen=True)
class WorkflowContext:
    """Complete immutable workflow snapshot composed from bounded aggregates."""

    identity: TaskIdentity
    progression: ProgressState
    delivery: DeliveryState = field(default_factory=DeliveryState)
    recovery: RecoveryState = field(default_factory=RecoveryState)
    review: ReviewState | None = None
    human_gate: HumanGateState | None = None
    request: RequestState = field(default_factory=RequestState)
    parallel: ParallelState = field(default_factory=ParallelState)
    audit: AuditState = field(default_factory=AuditState)
    schema_version: str = "4.0"


def context_to_dto(context: WorkflowContext) -> dict[str, object]:
    """Serialize the immutable aggregate without exposing mutable internals."""

    review = None
    if context.review:
        review = asdict(context.review)
        review["findings"] = [asdict(finding) for finding in context.review.findings]
        review["task_items"] = [asdict(item) for item in context.review.task_items]
        review["task_groups"] = [asdict(group) for group in context.review.task_groups]
        review["completed_item_ids"] = list(context.review.completed_item_ids)
    return {
        "identity": asdict(context.identity),
        "request": asdict(context.request),
        "progression": asdict(context.progression),
        "delivery": asdict(context.delivery),
        "recovery": {
            **asdict(context.recovery),
            "last_failure": (
                asdict(context.recovery.last_failure)
                if context.recovery.last_failure is not None
                else None
            ),
        },
        "parallel": {
            "zhongshu": asdict(context.parallel.zhongshu),
            "menxia": asdict(context.parallel.menxia),
            "group_index": context.parallel.group_index,
            "item_index": context.parallel.item_index,
            "menxia_snapshot_ref": context.parallel.menxia_snapshot_ref,
            "active_node_run_id": context.parallel.active_node_run_id,
        },
        "review": review,
        "human_gate": asdict(context.human_gate) if context.human_gate else None,
        "audit": asdict(context.audit),
        "schema_version": context.schema_version,
    }


def context_from_dto(value: Mapping[str, object]) -> WorkflowContext:
    """Deserialize a DTO into fresh immutable value objects."""

    identity = _context_part(value, "identity", TaskIdentity)
    request = _context_part(value, "request", RequestState, default=RequestState())
    progression = _context_part(value, "progression", ProgressState)
    delivery = _context_part(value, "delivery", DeliveryState)
    recovery_value = value.get("recovery")
    recovery = _recovery_from_dto(recovery_value)

    parallel_value = value.get("parallel")
    parallel = _parallel_from_dto(parallel_value)

    review_value = value.get("review")
    review = None
    if review_value is not None and not isinstance(review_value, Mapping):
        raise ValueError("context.review must be null or an object")
    if isinstance(review_value, Mapping):
        findings_value = review_value.get("findings", [])
        if not isinstance(findings_value, list):
            raise ValueError("review.findings must be an array")
        findings = tuple(Finding.from_dict(dict(item)) for item in findings_value)
        task_items = _review_task_items_from_dto(review_value.get("task_items", []))
        task_groups = _review_task_groups_from_dto(review_value.get("task_groups", []))
        completed_item_ids = _completed_item_ids_from_dto(
            review_value.get("completed_item_ids", [])
        )
        review = ReviewState(
            revision_id=str(review_value.get("revision_id") or ""),
            active_group_id=review_value.get("active_group_id"),
            active_item_id=review_value.get("active_item_id"),
            findings=findings,
            zhongshu_revision_round=int(review_value.get("zhongshu_revision_round", 0)),
            max_zhongshu_revision_rounds=int(review_value.get("max_zhongshu_revision_rounds", 8)),
            freeze_check_attempt=int(review_value.get("freeze_check_attempt", 0)),
            max_freeze_check_attempts=int(review_value.get("max_freeze_check_attempts", 2)),
            item_revision_round=int(review_value.get("item_revision_round", 0)),
            max_item_revision_rounds=int(review_value.get("max_item_revision_rounds", 3)),
            last_reply_fingerprint=str(review_value.get("last_reply_fingerprint") or ""),
            plan_ref=review_value.get("plan_ref"),
            plan_hash=review_value.get("plan_hash"),
            task_graph_ref=review_value.get("task_graph_ref"),
            task_items=task_items,
            task_groups=task_groups,
            completed_item_ids=completed_item_ids,
        )

    gate_value = value.get("human_gate")
    if gate_value is not None and not isinstance(gate_value, Mapping):
        raise ValueError("context.human_gate must be null or an object")
    human_gate = (
        _context_part(gate_value, "human_gate", HumanGateState)
        if isinstance(gate_value, Mapping)
        else None
    )
    audit = _audit_from_dto(value.get("audit"))
    return WorkflowContext(
        identity,
        progression,
        delivery,
        recovery,
        review,
        human_gate,
        request,
        parallel,
        audit,
        str(value.get("schema_version") or "4.0"),
    )


def _context_part(
    value: Mapping[str, object],
    name: str,
    cls: type,
    *,
    default: object | None = None,
):
    item = value.get(name)
    if item is None and default is not None:
        return default
    if not isinstance(item, Mapping):
        raise ValueError(f"context.{name} must be an object")
    return cls(**dict(item))


def _recovery_from_dto(value: object) -> RecoveryState:
    if value is None:
        return RecoveryState()
    if not isinstance(value, Mapping):
        raise ValueError("context.recovery must be an object")
    data = dict(value)
    failure_value = data.get("last_failure")
    if failure_value is not None:
        if not isinstance(failure_value, Mapping):
            raise ValueError("context.recovery.last_failure must be an object or null")
        data["last_failure"] = FailureRecord(**dict(failure_value))
    return RecoveryState(**data)


def _parallel_from_dto(value: object) -> ParallelState:
    if value is None:
        return ParallelState()
    if not isinstance(value, Mapping):
        raise ValueError("context.parallel must be an object")
    zhongshu_value = value.get("zhongshu", {})
    menxia_value = value.get("menxia", {})
    if not isinstance(zhongshu_value, Mapping) or not isinstance(menxia_value, Mapping):
        raise ValueError("context.parallel limits must be objects")
    return ParallelState(
        zhongshu=ZhongshuParallelLimits(**dict(zhongshu_value)),
        menxia=MenxiaParallelLimits(**dict(menxia_value)),
        group_index=value.get("group_index"),
        item_index=value.get("item_index"),
        menxia_snapshot_ref=value.get("menxia_snapshot_ref"),
        active_node_run_id=value.get("active_node_run_id"),
    )


def _audit_from_dto(value: object) -> AuditState:
    if value is None:
        return AuditState()
    if not isinstance(value, Mapping):
        raise ValueError("context.audit must be an object")
    data = dict(value)
    notifications = data.get("sent_notification_keys", ())
    if isinstance(notifications, str) or not isinstance(notifications, (list, tuple)):
        raise ValueError("context.audit.sent_notification_keys must be an array")
    data["sent_notification_keys"] = tuple(str(item) for item in notifications)
    return AuditState(**data)


def _review_task_items_from_dto(value: object) -> tuple[ReviewTaskItem, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("review.task_items must be an array")
    result: list[ReviewTaskItem] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise ValueError(f"review.task_items[{index}] must be an object")
        item_id = str(item.get("item_id") or "").strip()
        group_id = str(item.get("group_id") or "").strip()
        if not item_id or not group_id:
            raise ValueError(f"review.task_items[{index}] identity is incomplete")
        dependencies = item.get("dependencies", [])
        if not isinstance(dependencies, list):
            raise ValueError(f"review.task_items[{index}].dependencies must be an array")
        order = item.get("order", index)
        if isinstance(order, bool) or not isinstance(order, int):
            raise ValueError(f"review.task_items[{index}].order must be an integer")
        status = item.get("status", "PENDING")
        if not isinstance(status, str):
            raise ValueError(f"review.task_items[{index}].status must be a string")
        result.append(
            ReviewTaskItem(
                item_id=item_id,
                group_id=group_id,
                dependencies=tuple(str(dependency) for dependency in dependencies),
                order=order,
                status=status,
            )
        )
    return tuple(result)


def _review_task_groups_from_dto(value: object) -> tuple[ReviewTaskGroup, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("review.task_groups must be an array")
    result: list[ReviewTaskGroup] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise ValueError(f"review.task_groups[{index}] must be an object")
        group_id = str(item.get("group_id") or "").strip()
        if not group_id:
            raise ValueError(f"review.task_groups[{index}] identity is incomplete")
        item_ids = item.get("item_ids", [])
        if not isinstance(item_ids, list):
            raise ValueError(f"review.task_groups[{index}].item_ids must be an array")
        order = item.get("order", index)
        if isinstance(order, bool) or not isinstance(order, int):
            raise ValueError(f"review.task_groups[{index}].order must be an integer")
        status = item.get("status", "PENDING")
        if not isinstance(status, str):
            raise ValueError(f"review.task_groups[{index}].status must be a string")
        result.append(
            ReviewTaskGroup(
                group_id=group_id,
                item_ids=tuple(str(item_id) for item_id in item_ids),
                order=order,
                status=status,
            )
        )
    return tuple(result)


def _completed_item_ids_from_dto(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("review.completed_item_ids must be an array")
    return tuple(str(item_id) for item_id in value)


__all__ = [
    "DeliveryState",
    "DeliveryUpdate",
    "HumanGateState",
    "HumanGateUpdate",
    "AuditUpdate",
    "AuditState",
    "MenxiaParallelLimits",
    "ParallelState",
    "ParallelUpdate",
    "ProgressState",
    "ProgressUpdate",
    "RecoveryState",
    "RecoveryUpdate",
    "ReviewState",
    "ReviewTaskGroup",
    "ReviewTaskItem",
    "ReviewUpdate",
    "RequestState",
    "TaskIdentity",
    "ZhongshuParallelLimits",
    "WorkflowContext",
    "context_from_dto",
    "context_to_dto",
]
