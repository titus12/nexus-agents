"""Direct, event-bound Feishu notifications for the linear workflow."""

from __future__ import annotations

from collections.abc import Mapping
import json
import logging

from .domain.context import WorkflowContext
from .domain.events import DomainEvent
from .runtime.ports import NotificationPort, NotificationRequest


logger = logging.getLogger("review_orchestrator_fsm")

ROLE_NAMES = {
    "ZHONGSHU_ANALYST": "分析师",
    "ZHONGSHU_SOLVER": "规划师",
    "ZHONGSHU_CRITIC": "审查员",
    "ZHONGSHU_FREEZE_CHECK": "审查员",
    "MENXIA_ITEM_SOLVER": "规划师",
    "MENXIA_ITEM_ANALYST": "分析师",
    "MENXIA_ITEM_CRITIC": "审查员",
    "MENXIA_GROUP_GATE": "审查员",
}

ACTION_LABELS = {
    "READY_FOR_SOLVER": "证据已提交给规划师",
    "READY_FOR_CRITIC": "方案已提交给审查员",
    "READY_FOR_ANALYST": "条目已提交给分析师",
    "EVIDENCE_SUFFICIENT": "证据审查通过",
    "APPROVE_ITEM": "条目审查通过",
    "APPROVE_GROUP": "当前组审查通过",
    "APPROVE_CRITIC": "审查通过",
    "TASK_APPROVED": "任务审查通过",
    "REQUEST_ANALYST_EVIDENCE": "需要分析师补充证据",
    "NEEDS_MORE_EVIDENCE": "需要补充证据",
    "REQUEST_SOLVER_REVISION": "需要规划师修订",
    "REQUEST_REGROUP": "需要重新分组",
    "REVISE_ITEM": "当前条目需要修订",
    "HUMAN_GATE": "需要人工决策",
    "HUMAN_DECISION_RECEIVED": "已收到人工决策",
    "RETRY_RESUMED": "系统自动重试",
    "DONE": "任务已完成",
}

PRESENTATION_EVENTS = frozenset(
    {
        "STATE_ENTER",
        "AGENT_REPLY_ACCEPTED",
        "AGENT_REPLY_REJECTED",
        "AGENT_REPLY_CONTRACT_REJECTED",
        "LOCAL_VALIDATION_PASSED",
        "HUMAN_DECISION_RECEIVED",
        "RETRY_RESUMED",
        "HEARTBEAT",
        "ZHONGSHU_FANOUT_STARTED",
        "ZHONGSHU_WORKER_RETRY",
        "ZHONGSHU_FANIN_COMPLETED",
    }
)

HIDDEN_EVENTS = frozenset({"POLL_START", "POLL_END", "DISPATCH_START", "DISPATCH_END"})
SYSTEM_EVENTS = frozenset(
    {
        "START",
        "RESUME",
        "RETRY",
        "BLOCK",
        "BLOCKED",
        "OPEN_HUMAN_GATE",
        "HUMAN_GATE",
        "FAIL",
        "CANCEL",
        "TIMEOUT",
        "NOOP",
        "NODE_COMPLETED",
    }
)


def should_emit_notification(event_name: str) -> bool:
    return event_name in PRESENTATION_EVENTS


def build_notification(
    state: str,
    event_name: str,
    event: DomainEvent,
    context: WorkflowContext,
    *,
    limit: int = 1400,
) -> str:
    """Render a bounded human-facing update without transport envelopes."""

    payload = event.payload if isinstance(event.payload, Mapping) else {}
    role = ROLE_NAMES.get(state, state)
    action = str(payload.get("action") or event.name or "").strip()
    label = ACTION_LABELS.get(action, ACTION_LABELS.get(event_name, "任务状态更新"))
    if event_name == "STATE_ENTER":
        label = "开始处理" if context.progression.sequence <= 1 else f"进入{role}阶段"
    if event_name == "HEARTBEAT":
        label = "任务仍在运行"
    if event_name == "AGENT_REPLY_REJECTED":
        label = "Agent 回复需要修正"

    lines = [f"【{role}｜{label}】", f"任务：{context.identity.task_id}", f"状态：{state}"]
    if context.request.raw_request:
        lines.append(f"请求：{_brief(context.request.raw_request, 220)}")
    if context.review and context.review.active_item_id:
        lines.append(f"当前条目：{context.review.active_item_id}")

    if event_name == "HEARTBEAT":
        lines.append(f"已等待：{payload.get('elapsed_seconds', 0)} 秒")
        lines.append(f"等待角色：{payload.get('expected_agent_id') or context.delivery.expected_agent_id or role}")
    elif event_name in {"AGENT_REPLY_REJECTED", "AGENT_REPLY_CONTRACT_REJECTED"}:
        reason = payload.get("reason") or payload.get("message") or payload.get("error_code")
        if reason:
            lines.append(f"原因：{_brief(reason, 420)}")
    elif event_name == "HUMAN_DECISION_RECEIVED":
        answer = payload.get("answer") or payload.get("selected")
        if answer:
            lines.append(f"选择：{_brief(answer, 180)}")
    elif event_name == "RETRY_RESUMED":
        failure = context.recovery.last_failure
        reason = str(getattr(failure, "error_code", "") or "")
        if reason:
            lines.append(f"重试原因：{reason}")
        lines.append(
            f"重试次数：{context.recovery.retry_count}/{context.recovery.max_retries}"
        )
    elif event_name == "DONE" or state == "DONE":
        _append_payload(lines, payload, "final_delivery")
        lines.append("方案已完成；不代表代码已实现或测试已执行。")
    else:
        gate_lines = _human_gate_lines(payload)
        if gate_lines:
            lines.extend(gate_lines)
        for field in ("notification", "summary", "confirmed_facts", "missing_evidence", "questions_for_solver"):
            _append_payload(lines, payload, field)
        progress = _review_progress_lines(state, event_name, payload, context)
        if progress:
            lines.extend(progress)
        else:
            _append_payload(lines, payload, "findings")

    return _brief("\n".join(lines), limit)


def build_agent_notification(
    role: str,
    state: str,
    event_name: str,
    context: object,
    payload: Mapping[str, object] | None = None,
    limit: int = 1400,
) -> str:
    """Compatibility renderer for callers of the legacy notification API."""

    if isinstance(context, WorkflowContext):
        task_id = context.identity.task_id
        workflow_context = context
    else:
        task_id = str(getattr(context, "task_id", ""))
        workflow_context = _legacy_context(task_id, state, context)
    event = DomainEvent(
        name=event_name,
        task_id=task_id or "legacy-task",
        sequence=workflow_context.progression.sequence,
        payload=dict(payload or {}),
        occurred_at=workflow_context.progression.entered_at,
    )
    return build_notification(state, event_name, event, workflow_context, limit=limit)


class DirectNotificationEmitter:
    """Send notifications at the workflow event boundary and return keys."""

    def __init__(self, port: NotificationPort) -> None:
        self._port = port

    def emit(
        self,
        before: WorkflowContext,
        event: DomainEvent,
        after: WorkflowContext,
    ) -> tuple[str, ...]:
        notifications: list[tuple[str, str, WorkflowContext, DomainEvent]] = []
        event_name = _presentation_event_name(event, before.progression.state)
        if event_name:
            notifications.append((event_name, "event", before, event))
        if before.progression.state != after.progression.state:
            if after.progression.state == "HUMAN_GATE":
                notifications.append(("HUMAN_GATE", "human-gate", after, event))
            elif after.progression.state == "DONE":
                notifications.append(("DONE", "done", after, event))
            else:
                notifications.append(("STATE_ENTER", "state-enter", after, event))

        sent: list[str] = []
        already_sent = set(after.audit.sent_notification_keys)
        for presentation_event, suffix, context, source in notifications:
            key = f"{event.task_id}:{after.progression.sequence}:{suffix}:{presentation_event}"
            if key in already_sent:
                continue
            body = build_notification(context.progression.state, presentation_event, source, context)
            if not body:
                continue
            logger.info(
                "FEISHU_NOTIFY_START task_id=%s sequence=%s state=%s event=%s notification_key=%s text_chars=%s",
                event.task_id,
                after.progression.sequence,
                context.progression.state,
                presentation_event,
                key,
                len(body),
            )
            try:
                receipt = self._port.send(NotificationRequest(event.task_id, key, body))
                if receipt.delivered:
                    logger.info("FEISHU_NOTIFY_SUCCESS task_id=%s sequence=%s event=%s notification_key=%s", event.task_id, after.progression.sequence, presentation_event, key)
                else:
                    logger.warning("FEISHU_NOTIFY_FAILED task_id=%s sequence=%s event=%s notification_key=%s reason=not_delivered", event.task_id, after.progression.sequence, presentation_event, key)
            except Exception:
                logger.exception("FEISHU_NOTIFY_FAILED task_id=%s sequence=%s event=%s notification_key=%s", event.task_id, after.progression.sequence, presentation_event, key)
            sent.append(key)
            already_sent.add(key)
        return tuple(sent)


def _presentation_event_name(event: DomainEvent, from_state: str = "") -> str | None:
    if event.name in PRESENTATION_EVENTS:
        return event.name
    if event.name == "FAIL":
        return "AGENT_REPLY_REJECTED"
    if event.name == "RESUME":
        # Only a resume from a state that actually waits for a person is a human
        # decision.  RETRY_WAIT (and PERSISTENCE_DEGRADED) are resumed
        # automatically by the runner, so labelling those "已收到人工决策" is wrong.
        if from_state in {"HUMAN_GATE", "BLOCKED"}:
            return "HUMAN_DECISION_RECEIVED"
        return "RETRY_RESUMED"
    if event.name == "NODE_COMPLETED":
        payload = event.payload if isinstance(event.payload, Mapping) else {}
        return "AGENT_REPLY_ACCEPTED" if payload.get("action") else "ZHONGSHU_FANIN_COMPLETED"
    if event.name in HIDDEN_EVENTS or event.name in SYSTEM_EVENTS:
        return None
    return "AGENT_REPLY_ACCEPTED"


_BLOCKING_SEVERITIES = frozenset({"P0", "P1"})
_RESOLVED_FINDING_STATUSES = frozenset({"RESOLVED", "WONT_FIX", "DEFERRED"})
_OWNER_LABELS = {
    "review-solver": "规划师",
    "review-analyst": "分析师",
    "review-critic": "审查员",
    "human": "人工",
}
_REVIEW_PROGRESS_STATES = frozenset(
    {
        "ZHONGSHU_CRITIC",
        "ZHONGSHU_FREEZE_CHECK",
        "ZHONGSHU_SOLVER",
        "MENXIA_ITEM_SOLVER",
        "MENXIA_ITEM_ANALYST",
        "MENXIA_ITEM_CRITIC",
        "MENXIA_GROUP_GATE",
    }
)


def _human_gate_lines(payload: Mapping[str, object]) -> list[str]:
    """Render the structured human-gate reason and decision as a short block."""

    gate = payload.get("human_gate_request")
    if not isinstance(gate, Mapping):
        # Backward compatibility for payloads persisted before the field was
        # renamed away from the FSM's ``context.human_gate``.
        gate = payload.get("human_gate")
    if not isinstance(gate, Mapping) or not gate:
        return []
    lines: list[str] = []
    reason = str(gate.get("reason") or "").strip()
    if reason:
        lines.append(f"人工门原因：{_brief(reason, 320)}")
    question = str(gate.get("question") or "").strip()
    if question:
        lines.append(f"需要你决策：{_brief(question, 320)}")
    options = [str(item) for item in gate.get("options") or [] if str(item)]
    for index, option in enumerate(options, 1):
        lines.append(f"  {index}) {_brief(option, 160)}")
    details = [str(item) for item in gate.get("details") or [] if str(item)]
    if details:
        lines.append("具体问题：")
        for detail in details[:6]:
            lines.append(f"  - {_brief(detail, 200)}")
    return lines


def _review_progress_lines(
    state: str,
    event_name: str,
    payload: Mapping[str, object],
    context: WorkflowContext,
) -> list[str]:
    """Render an item-scoped review view: pass/fail per task and current target."""

    review = getattr(context, "review", None)
    if review is None or state not in _REVIEW_PROGRESS_STATES:
        return []
    findings = _progress_findings(payload, review)
    items = _progress_items(review)
    if not findings and not items:
        return []
    blocking = [
        finding
        for finding in findings
        if _finding_severity(finding) in _BLOCKING_SEVERITIES
        and _finding_active(finding)
    ]
    scoped_blocking = [finding for finding in blocking if _finding_item_id(finding)]
    global_blocking = [finding for finding in blocking if not _finding_item_id(finding)]
    blocked_items = _ordered_unique(
        _finding_item_id(finding) for finding in scoped_blocking
    )
    item_ids = [item.item_id for item in items]
    if not item_ids:
        item_ids = _ordered_unique(
            _finding_item_id(finding)
            for finding in findings
            if _finding_item_id(finding)
        )
    blocked_set = set(blocked_items)

    if state.startswith("MENXIA"):
        return _menxia_progress_lines(review, item_ids)

    lines: list[str] = []
    labels = _item_labels(items)
    if item_ids and scoped_blocking:
        passed = [item_id for item_id in item_ids if item_id not in blocked_set]
        lines.append(
            f"任务级审查：通过 {len(passed)}/{len(item_ids)}，"
            f"未通过 {len(blocked_items)}"
        )
        details = "、".join(
            f"{item_id}（{_blocked_label(scoped_blocking, item_id)}）"
            for item_id in blocked_items[:8]
        )
        if len(blocked_items) > 8:
            details += " …"
        lines.append(f"未通过：{details}")
        if passed:
            lines.append(f"通过：{_item_list(passed, labels)}")
    elif item_ids and not blocking:
        lines.append(f"任务级审查：全部通过（{len(item_ids)} 项）")
        lines.append(f"通过：{_item_list(item_ids, labels)}")
    elif item_ids:
        lines.append(f"任务级审查：{len(item_ids)} 项均未标注任务归属")

    if global_blocking:
        counts = _severity_counts(global_blocking)
        lines.append(f"全局未解决：{counts}，共 {len(global_blocking)} 项")
        for finding in global_blocking[:2]:
            detail = _finding_detail(finding)
            if detail:
                lines.append(f"  · {_brief(detail, 140)}")

    group_line = _group_progress_lines(review, blocked_set)
    if group_line:
        lines.append(group_line)

    if state == "ZHONGSHU_SOLVER":
        parts: list[str] = []
        if blocked_items:
            parts.append("任务 " + "、".join(blocked_items[:6]))
        if global_blocking:
            parts.append(f"全局 {len(global_blocking)} 项")
        if parts:
            lines.append("规划师本轮待处理：" + "；".join(parts))

    return lines


def _severity_counts(findings: list[object]) -> str:
    counts: dict[str, int] = {}
    for finding in findings:
        severity = _finding_severity(finding) or "?"
        counts[severity] = counts.get(severity, 0) + 1
    return "、".join(f"{severity}×{counts[severity]}" for severity in sorted(counts))


def _finding_detail(finding: object) -> str:
    for name in ("claim", "required_action", "target"):
        value = _finding_field(finding, name)
        if value:
            return value
    return ""


def _menxia_progress_lines(review: object, item_ids: list[str]) -> list[str]:
    lines: list[str] = []
    active_item = getattr(review, "active_item_id", None)
    if active_item:
        active_group = getattr(review, "active_group_id", None)
        suffix = f"（组 {_short_group(active_group)}）" if active_group else ""
        lines.append(f"当前条目：{active_item}{suffix}")
    total = len(item_ids)
    if total:
        completed = len(getattr(review, "completed_item_ids", ()) or ())
        lines.append(f"门下省完成：{completed}/{total}")
    return lines


def _progress_findings(
    payload: Mapping[str, object],
    review: object,
) -> list[object]:
    raw = payload.get("findings")
    if isinstance(raw, list) and raw:
        return list(raw)
    return list(getattr(review, "findings", ()) or ())


def _progress_items(review: object) -> list[object]:
    items = list(getattr(review, "task_items", ()) or ())
    return sorted(
        items,
        key=lambda item: (getattr(item, "order", 0), getattr(item, "item_id", "")),
    )


def _item_labels(items: list[object]) -> dict[str, str]:
    """Map item_id to a human-readable ``item_id（title）`` label."""

    labels: dict[str, str] = {}
    for item in items:
        item_id = str(getattr(item, "item_id", "") or "")
        if not item_id:
            continue
        title = str(getattr(item, "title", "") or "").strip()
        labels[item_id] = f"{item_id}（{_brief(title, 48)}）" if title else item_id
    return labels


def _item_list(
    item_ids: list[str], labels: Mapping[str, str], limit: int = 8
) -> str:
    shown = "、".join(labels.get(item_id, item_id) for item_id in item_ids[:limit])
    if len(item_ids) > limit:
        shown += " …"
    return shown


def _finding_severity(finding: object) -> str:
    return _finding_field(finding, "severity").upper()


def _finding_item_id(finding: object) -> str:
    return _finding_field(finding, "item_id")


def _finding_field(finding: object, name: str) -> str:
    if isinstance(finding, Mapping):
        value = finding.get(name, "")
    else:
        value = getattr(finding, name, "")
    return str(value or "")


def _finding_active(finding: object) -> bool:
    if isinstance(finding, Mapping):
        status = str(finding.get("status") or "").strip().upper()
        return not status or status not in _RESOLVED_FINDING_STATUSES
    return bool(getattr(finding, "active", False))


def _blocked_label(blocking: list[object], item_id: str) -> str:
    severities = sorted(
        {
            _finding_severity(finding)
            for finding in blocking
            if _finding_item_id(finding) == item_id and _finding_severity(finding)
        }
    )
    owners: list[str] = []
    for finding in blocking:
        if _finding_item_id(finding) != item_id:
            continue
        owner = _finding_field(finding, "owner_role")
        label = _OWNER_LABELS.get(owner, owner)
        if label and label not in owners:
            owners.append(label)
    severity_label = "/".join(severities) if severities else "问题"
    return f"{severity_label},{owners[0]}" if owners else severity_label


def _group_progress_lines(review: object, blocked_set: set[str]) -> str:
    groups = list(getattr(review, "task_groups", ()) or ())
    if not groups:
        return ""
    parts: list[str] = []
    for group in sorted(
        groups,
        key=lambda item: (getattr(item, "order", 0), getattr(item, "group_id", "")),
    ):
        group_items = [str(item) for item in (getattr(group, "item_ids", ()) or ())]
        if not group_items:
            continue
        passed = sum(1 for item_id in group_items if item_id not in blocked_set)
        parts.append(f"{_short_group(getattr(group, 'group_id', ''))} {passed}/{len(group_items)}")
    if not parts:
        return ""
    return "分组：" + "、".join(parts[:12])


def _short_group(group_id: object) -> str:
    text = str(group_id or "")
    if text.startswith("group-"):
        stripped = text[len("group-"):].lstrip("0")
        return "G" + (stripped or "0")
    return text or "G"


def _ordered_unique(values) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "")
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _append_payload(lines: list[str], payload: Mapping[str, object], name: str) -> None:
    value = payload.get(name)
    if value in (None, "", [], {}):
        return
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    lines.append(f"{name}：{_brief(value, 420)}")


def _brief(value: object, limit: int) -> str:
    text = str(value).replace("\r", " ").strip()
    return text if len(text) <= limit else text[: max(1, limit - 1)] + "…"


def _legacy_context(task_id: str, state: str, value: object) -> WorkflowContext:
    from .domain.context import ProgressState, RequestState, TaskIdentity

    raw_request = str(getattr(value, "raw_request", ""))
    return WorkflowContext(
        identity=TaskIdentity(task_id or "legacy-task", "", "", ""),
        progression=ProgressState(state, 0, ""),
        request=RequestState(raw_request=raw_request),
    )


__all__ = ["DirectNotificationEmitter", "HIDDEN_EVENTS", "PRESENTATION_EVENTS", "build_agent_notification", "build_notification", "should_emit_notification"]
