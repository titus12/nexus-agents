"""One-time conversion of legacy JSON state into a New runtime snapshot.

The converter accepts an untyped persisted mapping only.  The removed mutable
context class is intentionally not imported here, so the new runtime has no
in-process dependency on the old FSM.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from typing import Any

from ..domain.context import (
    AuditState,
    DeliveryState,
    HumanGateState,
    MenxiaParallelLimits,
    ParallelState,
    ProgressState,
    RecoveryState,
    RequestState,
    ReviewState,
    ReviewTaskGroup,
    ReviewTaskItem,
    TaskIdentity,
    WorkflowContext,
    ZhongshuParallelLimits,
)
from ..domain.errors import FailureRecord, InvariantViolation, PersistenceError
from ..domain.findings import Finding
from ..domain.transitions import ALL_STATES, RESUMABLE_BUSINESS_STATES
from .ports import ArtifactInput, ArtifactPort
from .repository import WorkflowSnapshot


_LEGACY_SYSTEM_STATE_MAP = {
    "TIMEOUT": "RETRY_WAIT",
    "INVALID_AGENT_REPLY": "RETRY_WAIT",
    "MULTICA_ERROR": "RETRY_WAIT",
    "HUMAN_GATE_TIMEOUT": "HUMAN_GATE",
    "HUMAN_GATE_ERROR": "HUMAN_GATE",
    "STATE_CORRUPTED": "PERSISTENCE_DEGRADED",
}


def legacy_dto_to_snapshot(
    value: Mapping[str, object],
    *,
    artifact_port: ArtifactPort | None = None,
    task_id: str | None = None,
) -> WorkflowSnapshot:
    """Convert one legacy state document with explicit field validation."""

    if not isinstance(value, Mapping):
        raise PersistenceError("legacy state must be an object")
    source_task_id = str(task_id or value.get("task_id") or "")
    if not source_task_id:
        raise PersistenceError("legacy state task_id is required")
    issue_id = str(value.get("issue_id") or source_task_id)
    sequence = _int(value, "sequence", 0)
    raw_state = str(value.get("workflow_state") or "REQUEST_INTAKE")
    state = _LEGACY_SYSTEM_STATE_MAP.get(raw_state, raw_state)
    if state not in ALL_STATES:
        raise PersistenceError(f"legacy state is not in the New graph: {raw_state}")

    resume_state = _text_or_none(value.get("resume_state"))
    if state in {"RETRY_WAIT", "HUMAN_GATE", "PERSISTENCE_DEGRADED", "BLOCKED"}:
        resume_state = resume_state or raw_state
        if resume_state not in RESUMABLE_BUSINESS_STATES:
            resume_state = "ZHONGSHU_SOLVER"
    elif state not in {"DONE", "FAILED", "CANCELLED"}:
        resume_state = None

    request_payload_ref = _artifact_ref(
        value.get("request_payload"),
        task_id=source_task_id,
        name="request-payload.json",
        artifact_port=artifact_port,
    )
    last_result_ref = _artifact_ref(
        value.get("last_agent_payload"),
        task_id=source_task_id,
        name="last-agent-result.json",
        artifact_port=artifact_port,
    )
    reply_history_ref = _artifact_ref(
        value.get("reply_history"),
        task_id=source_task_id,
        name="reply-history.json",
        artifact_port=artifact_port,
    )
    (
        queue_revision_id,
        queue_plan_hash,
        task_items,
        task_groups,
        completed_item_ids,
        queue_active_group_id,
        queue_active_item_id,
    ) = _legacy_task_graph(value.get("request_payload"))

    raw_findings = _list(value, "findings")
    if any(not isinstance(item, Mapping) for item in raw_findings):
        raise PersistenceError("legacy findings must contain only objects")
    findings = tuple(Finding.from_dict(dict(item)) for item in raw_findings)
    _verify_derived_finding_ids(value, findings)
    revision_id = str(
        queue_revision_id
        or value.get("revision_id")
        or f"{source_task_id}:r{sequence}"
    )
    identity = TaskIdentity(
        task_id=source_task_id,
        issue_id=issue_id,
        project=str(value.get("project") or ""),
        request_id=str(value.get("request_id") or f"request-{source_task_id}"),
    )
    context = WorkflowContext(
        identity=identity,
        progression=ProgressState(
            state=state,
            sequence=sequence,
            entered_at=str(value.get("entered_at") or _now()),
            resume_state=resume_state,
        ),
        delivery=DeliveryState(
            active_request_id=_text_or_none(value.get("active_request_id")),
            dispatch_operation_id=_text_or_none(value.get("dispatch_operation_id")),
            idempotency_key=_text_or_none(value.get("dispatch_idempotency_key")),
            status=str(value.get("dispatch_status") or "IDLE").upper(),
            external_message_id=_text_or_none(value.get("dispatch_external_message_id")),
            phase=str(value.get("current_phase") or ""),
            role=str(value.get("current_role") or ""),
            expected_agent_id=str(value.get("expected_agent_id") or ""),
            last_sent_at=str(value.get("last_sent_at") or ""),
            attempt=_int(value, "dispatch_attempt", 0),
            last_result_ref=last_result_ref,
        ),
        recovery=RecoveryState(
            retry_count=_int(value, "retry_count", 0),
            max_retries=_int(value, "max_retries", 3),
            recoverable=bool(value.get("recoverable", True)),
            blocked_reason=_text_or_none(value.get("blocked_reason")),
            external_retry_count=_int(value, "external_retry_count", 0),
            reply_retry_count=_int(value, "reply_retry_count", 0),
            max_external_retries=_int(value, "max_external_retries", 3),
            max_reply_retries=_int(value, "max_reply_retries", 3),
            max_state_write_retries=_int(value, "max_state_write_retries", 3),
            timeout_retry_count=_int(value, "timeout_retry_count", 0),
            max_timeout_retries=_int(value, "max_timeout_retries", 3),
            timeout_phase=_text_or_none(value.get("timeout_phase")),
            timeout_role=_text_or_none(value.get("timeout_role")),
            timeout_request_id=_text_or_none(value.get("timeout_request_id")),
            last_failure=_failure_from_legacy(value.get("last_error"), identity, state, sequence),
            no_progress_count=_int(value, "no_progress_count", 0),
            max_no_progress=_int(value, "max_no_progress", 3),
            max_stuck_finding_rounds=_int(value, "max_stuck_finding_rounds", 3),
        ),
        review=ReviewState(
            revision_id=revision_id,
            active_group_id=(
                _text_or_none(value.get("active_group_id"))
                or queue_active_group_id
            ),
            active_item_id=(
                _text_or_none(value.get("active_item_id"))
                or queue_active_item_id
            ),
            findings=findings,
            zhongshu_revision_round=_int(value, "zhongshu_revision_round", 0),
            max_zhongshu_revision_rounds=_int(value, "max_zhongshu_revision_rounds", 8),
            freeze_check_attempt=_int(value, "freeze_check_attempt", 0),
            max_freeze_check_attempts=_int(value, "max_freeze_check_attempts", 2),
            item_revision_round=_int(value, "item_revision_round", 0),
            max_item_revision_rounds=_int(value, "max_item_revision_rounds", 5),
            last_reply_fingerprint=str(value.get("last_reply_fingerprint") or ""),
            plan_ref=request_payload_ref if task_items else None,
            plan_hash=queue_plan_hash,
            task_graph_ref=request_payload_ref if task_items else None,
            task_items=task_items,
            task_groups=task_groups,
            completed_item_ids=completed_item_ids,
            plan=(
                dict(value["plan"])
                if isinstance(value.get("plan"), Mapping)
                else None
            ),
        ),
        human_gate=(
            HumanGateState(
                decision_id=str(value.get("active_decision_id")),
                reason_code=str(value.get("blocked_reason") or ""),
                resume_state=resume_state or "ZHONGSHU_SOLVER",
                message_id=_text_or_none(value.get("gate_message_id")),
            )
            if value.get("active_decision_id")
            else None
        ),
        request=RequestState(
            raw_request=str(value.get("raw_request") or ""),
            payload_ref=request_payload_ref,
            project_type=str(value.get("project_type") or "unknown"),
            task_type=str(value.get("task_type") or "review"),
        ),
        parallel=ParallelState(
            zhongshu=_zhongshu_limits(value.get("zhongshu_parallel")),
            menxia=_menxia_limits(value.get("menxia_parallel")),
            group_index=_optional_int(value.get("group_index")),
            item_index=_optional_int(value.get("item_index")),
            menxia_snapshot_ref=_artifact_ref(
                value.get("menxia_parallel"),
                task_id=source_task_id,
                name="menxia-parallel.json",
                artifact_port=artifact_port,
            ),
        ),
        audit=AuditState(
            sent_notification_keys=tuple(str(item) for item in _list(value, "sent_notification_keys")),
            heartbeat_count=_int(value, "heartbeat_count", 0),
            last_heartbeat_at=_epoch_to_iso(value.get("last_heartbeat_epoch")),
            reply_history_ref=reply_history_ref,
            last_artifact_id=_text_or_none(value.get("last_artifact_id")),
            updated_at=_text_or_none(value.get("updated_at")),
        ),
        schema_version="4.0",
    )
    return WorkflowSnapshot(
        task_id=source_task_id,
        context=context,
        state_version=_int(value, "state_version", 0),
    )


def _artifact_ref(
    value: object,
    *,
    task_id: str,
    name: str,
    artifact_port: ArtifactPort | None,
) -> str | None:
    if value in (None, "", [], {}):
        return None
    if isinstance(value, str):
        return value
    if artifact_port is None:
        raise PersistenceError(f"artifact port is required to migrate {name}")
    content = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    receipt = artifact_port.write(ArtifactInput(task_id=task_id, name=name, content=content))
    return receipt.artifact_id


def _legacy_task_graph(
    value: object,
) -> tuple[
    str | None,
    str | None,
    tuple[ReviewTaskItem, ...],
    tuple[ReviewTaskGroup, ...],
    tuple[str, ...],
    str | None,
    str | None,
]:
    """Convert the old mutable review queue without importing its class.

    The queue lived inside ``request_payload`` and therefore was not covered
    by the old top-level field mapping.  Its jobs are flattened into the New
    immutable item/group ledger; the original payload remains available as an
    artifact for fields that have no typed successor.
    """

    if not isinstance(value, Mapping):
        return None, None, (), (), (), None, None
    raw_queue = value.get("zhongshu_task_review_queue")
    if raw_queue in (None, {}):
        return None, None, (), (), (), None, None
    if not isinstance(raw_queue, Mapping):
        raise PersistenceError("legacy task review queue must be an object")
    revision_id = _text_or_none(raw_queue.get("revision_id"))
    plan_hash = _text_or_none(raw_queue.get("plan_hash"))
    if not revision_id or not plan_hash:
        raise PersistenceError("legacy task review queue identity is incomplete")
    raw_jobs = raw_queue.get("jobs")
    if not isinstance(raw_jobs, list) or not raw_jobs:
        raise PersistenceError("legacy task review queue jobs must be a non-empty array")
    items: list[ReviewTaskItem] = []
    group_items: dict[str, list[tuple[int, str]]] = {}
    completed: list[str] = []
    active: list[tuple[int, str, str]] = []
    for index, raw_job in enumerate(raw_jobs):
        if not isinstance(raw_job, Mapping):
            raise PersistenceError(f"legacy review queue job {index} must be an object")
        item_id = str(raw_job.get("item_id") or "").strip()
        group_id = str(raw_job.get("group_id") or "").strip()
        if not item_id or not group_id:
            raise PersistenceError(f"legacy review queue job {index} identity is incomplete")
        item_order = raw_job.get("item_order", index)
        group_order = raw_job.get("group_order", 0)
        if (
            isinstance(item_order, bool)
            or not isinstance(item_order, int)
            or item_order < 0
            or isinstance(group_order, bool)
            or not isinstance(group_order, int)
            or group_order < 0
        ):
            raise PersistenceError(f"legacy review queue job {index} order is invalid")
        status = raw_job.get("status", "PENDING")
        if not isinstance(status, str) or not status:
            raise PersistenceError(f"legacy review queue job {index} status is invalid")
        items.append(
            ReviewTaskItem(
                item_id=item_id,
                group_id=group_id,
                dependencies=(),
                order=(group_order * 1_000_000) + item_order,
                status=status,
            )
        )
        group_items.setdefault(group_id, []).append((item_order, item_id))
        if status == "COMPLETED":
            completed.append(item_id)
        if status == "RUNNING":
            active.append((group_order, group_id, item_id))
    groups = tuple(
        ReviewTaskGroup(
            group_id=group_id,
            item_ids=tuple(item_id for _, item_id in sorted(entries)),
            order=min(
                (item.order for item in items if item.group_id == group_id),
                default=0,
            ),
        )
        for group_id, entries in sorted(group_items.items())
    )
    active_group_id = active[0][1] if active else None
    active_item_id = active[0][2] if active else None
    return (
        revision_id,
        plan_hash,
        tuple(sorted(items, key=lambda item: (item.order, item.item_id))),
        groups,
        tuple(dict.fromkeys(completed)),
        active_group_id,
        active_item_id,
    )


def _failure_from_legacy(
    value: object,
    identity: TaskIdentity,
    state: str,
    sequence: int,
) -> FailureRecord | None:
    if value is None:
        return None
    data = dict(value) if isinstance(value, Mapping) else {"message": str(value)}
    return FailureRecord(
        failure_id=str(data.get("failure_id") or f"legacy:{identity.task_id}:{sequence}"),
        stage=str(data.get("stage") or "legacy"),
        owner_component=str(data.get("owner_component") or "migration"),
        task_id=identity.task_id,
        state=state,
        sequence=sequence,
        node_run_id=_text_or_none(data.get("node_run_id")),
        worker_id=_text_or_none(data.get("worker_id")),
        effect_id=_text_or_none(data.get("effect_id")),
        error_code=str(data.get("error_code") or data.get("code") or "LEGACY_FAILURE"),
        retryable=bool(data.get("retryable", True)),
        message=str(data.get("message") or "legacy failure"),
        cause_type=str(data.get("cause_type") or data.get("error_type") or "LegacyError"),
        external_id=_text_or_none(data.get("external_id")),
    )


def _verify_derived_finding_ids(value: Mapping[str, object], findings: tuple[Finding, ...]) -> None:
    actual = [finding.finding_id for finding in findings if finding.active]
    expected = [str(item) for item in _list(value, "active_finding_ids")]
    if expected and expected != actual:
        raise PersistenceError("legacy active_finding_ids does not match findings")
    pending_solver = [
        finding.finding_id
        for finding in findings
        if finding.active
        and str(finding.owner_role or "").strip().lower()
        in {"review-solver", "solver"}
    ]
    expected_pending = [
        str(item) for item in _list(value, "pending_solver_finding_ids")
    ]
    if expected_pending and expected_pending != pending_solver:
        raise PersistenceError(
            "legacy pending_solver_finding_ids does not match findings"
        )
    resolved = [finding.finding_id for finding in findings if finding.resolved]
    expected_resolved = [str(item) for item in _list(value, "resolved_finding_ids")]
    if expected_resolved and expected_resolved != resolved:
        raise PersistenceError("legacy resolved_finding_ids does not match findings")


def _zhongshu_limits(value: object) -> ZhongshuParallelLimits:
    data = dict(value) if isinstance(value, Mapping) else {}
    return ZhongshuParallelLimits(
        enabled=bool(data.get("enabled", True)),
        analyst_max_workers=_positive_int(data, "analyst_max_workers", 3),
        analyst_default_workers=_positive_int(data, "analyst_default_workers", 3),
        critic_max_workers=_positive_int(data, "critic_max_workers", 6),
        critic_default_workers=_positive_int(data, "critic_default_workers", 6),
        global_max_workers=_positive_int(data, "global_max_workers", 6),
        per_task_max_workers=_positive_int(data, "per_task_max_workers", 6),
    )


def _menxia_limits(value: object) -> MenxiaParallelLimits:
    data = dict(value) if isinstance(value, Mapping) else {}
    return MenxiaParallelLimits(
        enabled=bool(data.get("enabled", False)),
        max_concurrent_groups=_positive_int(data, "max_concurrent_groups", 1),
        max_concurrent_items=_positive_int(data, "max_concurrent_items", 3),
        failure_policy=str(data.get("failure_policy") or "continue_and_block_group"),
    )


def _list(value: Mapping[str, object], key: str) -> list[object]:
    raw = value.get(key, [])
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise PersistenceError(f"legacy field {key} must be an array")
    return raw


def _int(value: Mapping[str, object], key: str, default: int) -> int:
    raw = value.get(key, default)
    if type(raw) is not int:
        raise PersistenceError(f"legacy field {key} must be an integer")
    return raw


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int:
        raise PersistenceError("legacy parallel index must be an integer or null")
    return value


def _positive_int(value: Mapping[str, object], key: str, default: int) -> int:
    result = value.get(key, default)
    if type(result) is not int or result < 1:
        raise PersistenceError(f"legacy parallel limit {key} must be positive")
    return result


def _text_or_none(value: object) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise PersistenceError("legacy text field must be a string or null")
    return value


def _epoch_to_iso(value: object) -> str | None:
    if value in (None, 0, 0.0):
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PersistenceError("legacy last_heartbeat_epoch must be numeric")
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = ["legacy_dto_to_snapshot"]

