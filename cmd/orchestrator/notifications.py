from __future__ import annotations

import json
from typing import Any


ROLE_NAMES = {
    "review-analyst": "分析师",
    "review-solver": "规划师",
    "review-critic": "审查员",
}

ACTION_LABELS = {
    "FEASIBLE": "方案已提出",
    "EVIDENCE_SUFFICIENT": "证据审查通过",
    "APPROVE_ITEM": "方案审查通过",
    "REQUEST_SOLVER_REVISION": "需要规划师修订",
    "REQUEST_ANALYST_EVIDENCE": "需要分析师补充证据",
    "NEEDS_MORE_EVIDENCE": "需要补充证据",
    "TASK_CHANGES_REQUIRED": "任务需要修订",
    "TASK_APPROVED": "任务审查通过",
    "REVISE_ITEM": "当前条目需要修订",
    "HUMAN_GATE": "需要人工决策",
    "APPROVE_GROUP": "当前组审查通过",
    "DONE": "任务已完成",
}

PRESENTATION_EVENTS = {
    "STATE_ENTER",
    "AGENT_REPLY_ACCEPTED",
    "AGENT_REPLY_REJECTED",
    "AGENT_REPLY_CONTRACT_REJECTED",
    "LOCAL_VALIDATION_PASSED",
    "HUMAN_DECISION_RECEIVED",
    "HEARTBEAT",
    "ZHONGSHU_FANOUT_STARTED",
    "ZHONGSHU_WORKER_RETRY",
    "ZHONGSHU_FANIN_COMPLETED",
}

HIDDEN_EVENTS = {
    "POLL_START",
    "POLL_END",
    "DISPATCH_START",
    "DISPATCH_END",
}


def should_emit_notification(event_name: str) -> bool:
    """Return whether an event contains developer-facing information."""
    return event_name in PRESENTATION_EVENTS


def build_agent_notification(
    role: str,
    state: str,
    event_name: str,
    ctx: Any,
    payload: dict[str, Any] | None = None,
    limit: int = 1400,
) -> str:
    """Render one concise Feishu-facing task discussion update."""
    if payload is None:
        if event_name == "STATE_ENTER":
            request_payload = getattr(ctx, "request_payload", {})
            payload = (
                request_payload.get("notification_payload", {})
                if isinstance(request_payload, dict)
                else {}
            )
        else:
            payload = getattr(ctx, "last_agent_payload", {}) or {}

    display_role = ROLE_NAMES.get(role, role)
    if event_name == "HEARTBEAT":
        elapsed = payload.get("elapsed_seconds")
        wait_for = payload.get("expected_agent_id") or role
        status_by_state = {
            "ZHONGSHU_ANALYST": "分析师正在整理证据和任务边界。",
            "ZHONGSHU_SOLVER": "规划师正在整理执行方案。",
            "ZHONGSHU_CRITIC": "审查员正在检查方案质量。",
            "MENXIA_ITEM_SOLVER": "规划师正在细化当前 item 的执行方案。",
            "MENXIA_ITEM_ANALYST": "分析师正在核验当前 item 的证据。",
            "MENXIA_ITEM_CRITIC": "审查员正在检查当前 item 的风险和可验证性。",
            "MENXIA_GROUP_GATE": "审查员正在汇总当前组的审查结果。",
        }
        lines = [f"【{display_role}｜任务仍在运行】"]
        lines.extend(_context_lines(ctx))
        lines.extend(
            [
                "",
                f"状态：{status_by_state.get(state, '正在等待 Agent 返回结构化结果。')}",
                f"已等待：{_fmt_elapsed(elapsed)}",
                f"等待角色：{wait_for}",
            ]
        )
        return _bounded("\n".join(lines), limit)
    if not should_emit_notification(event_name):
        return ""

    lines = [f"【{display_role}｜{_state_label(state, event_name, payload, ctx)}】"]
    lines.extend(_context_lines(ctx))

    if event_name in {"AGENT_REPLY_REJECTED", "AGENT_REPLY_CONTRACT_REJECTED"}:
        lines.extend(_render_reply_rejection(ctx))
    elif event_name == "STATE_ENTER" and _is_reply_retry(ctx):
        lines.extend(_render_reply_retry(ctx))
    elif state == "DONE" or payload.get("action") == "DONE":
        lines.extend(_render_final_delivery(payload, ctx))
    elif state == "MENXIA_GROUP_GATE" or payload.get("action") == "APPROVE_GROUP":
        lines.extend(_render_group_summary(payload, ctx))
    elif role == "review-solver":
        lines.extend(_render_solver(payload, ctx))
    elif role == "review-analyst":
        lines.extend(_render_analyst(payload, ctx))
    elif role == "review-critic":
        lines.extend(_render_critic(payload, ctx))
    else:
        lines.append("当前任务已收到更新。")

    text = "\n".join(line for line in lines if line)
    if state == "DONE":
        render_limit = limit
    elif role == "review-critic" and event_name == "AGENT_REPLY_ACCEPTED":
        render_limit = 2800
    elif state == "MENXIA_ITEM_ANALYST" and event_name == "AGENT_REPLY_ACCEPTED":
        render_limit = 2200
    else:
        render_limit = limit
    return _bounded(text, render_limit)


def build_agent_notification_parts(
    role: str,
    state: str,
    event_name: str,
    ctx: Any,
    payload: dict[str, Any] | None = None,
    limit: int = 2600,
) -> list[str]:
    """Render DONE as bounded Feishu messages without dropping delivery fields."""
    if state != "DONE":
        text = build_agent_notification(role, state, event_name, ctx, payload, limit)
        return [text] if text else []
    full_text = build_agent_notification(
        role,
        state,
        event_name,
        ctx,
        payload,
        limit=100000,
    )
    if len(full_text) <= limit:
        return [full_text]
    lines = full_text.splitlines()
    chunks: list[str] = []
    current: list[str] = []
    current_length = 0
    for line in lines:
        extra = len(line) + (1 if current else 0)
        if current and current_length + extra > limit:
            chunks.append("\n".join(current))
            current = []
            current_length = 0
        current.append(line)
        current_length += len(line) + (1 if len(current) > 1 else 0)
    if current:
        chunks.append("\n".join(current))
    if len(chunks) > 1:
        for index in range(1, len(chunks)):
            chunks[index] = "【任务已完成｜最终交付方案（续）】\n" + chunks[index]
    return chunks


def build_zhongshu_parallel_notification(
    ctx: Any,
    event_name: str,
    summary: dict[str, Any],
    limit: int = 1800,
) -> str:
    """Render bounded Zhongshu fan-out/retry/fan-in status for Feishu."""
    phase = str(summary.get("phase") or getattr(ctx, "workflow_state", "ZHONGSHU"))
    revision_id = str(summary.get("revision_id") or summary.get("plan_revision_id") or "")
    lines = ["【中书省并发进度】", f"阶段：{phase}"]
    if revision_id:
        lines.append(f"方案版本：{revision_id}")
    lines.append(f"事件：{event_name}")

    workers = summary.get("workers") or []
    if isinstance(workers, list) and workers:
        lines.extend(["", "Worker 状态："])
        for worker in workers[:8]:
            if not isinstance(worker, dict):
                continue
            worker_id = str(worker.get("worker_id") or "unknown")
            status = str(worker.get("status") or "unknown").upper()
            attempt = worker.get("attempt")
            max_attempts = worker.get("max_attempts", 3)
            error = str(worker.get("error") or "").strip()
            marker = {
                "COMPLETED": "✓",
                "RUNNING": "…",
                "RETRYING": "↻",
                "FAILED": "✗",
                "REJECTED": "!",
            }.get(status, "?")
            attempt_text = (
                f"（第 {attempt}/{max_attempts} 次）"
                if attempt is not None
                else ""
            )
            line = f"- {marker} {worker_id}：{status}{attempt_text}"
            if error:
                line += f"；{_brief(error, 160)}"
            lines.append(line)
        if len(workers) > 8:
            lines.append(f"- 其余 {len(workers) - 8} 个 Worker 见任务日志")

    for label, key in (
        ("已完成", "completed"),
        ("失败", "failed"),
        ("被并发上限拒绝", "rejected"),
        ("未解决冲突", "unresolved_conflicts"),
        ("已解决冲突", "resolved_conflicts"),
    ):
        value = summary.get(key)
        if isinstance(value, (list, tuple)):
            if value:
                lines.append(f"{label}：{len(value)}")
        elif value not in (None, ""):
            lines.append(f"{label}：{value}")
    action = str(summary.get("action") or "")
    if action:
        lines.append(f"下一步：{action}")
    return _bounded("\n".join(lines), limit)


def _state_label(
    state: str,
    event_name: str,
    payload: dict[str, Any],
    ctx: Any,
) -> str:
    action = payload.get("action")
    if event_name == "STATE_ENTER":
        if _is_reply_retry(ctx):
            return "回复修正重试"
        return "开始处理"
    if event_name in {"AGENT_REPLY_REJECTED", "AGENT_REPLY_CONTRACT_REJECTED"}:
        return "回复需要修正"
    if event_name == "LOCAL_VALIDATION_PASSED":
        return "本地校验通过"
    if event_name == "HUMAN_DECISION_RECEIVED":
        return "人工决策已收到"
    return ACTION_LABELS.get(action, "处理结果")


def _is_reply_retry(ctx: Any) -> bool:
    if isinstance(ctx, dict):
        return int(ctx.get("reply_retry_count") or 0) > 0
    return int(getattr(ctx, "reply_retry_count", 0) or 0) > 0


def _render_reply_rejection(ctx: Any) -> list[str]:
    error = getattr(ctx, "last_error", None)
    reason = ""
    if isinstance(error, dict):
        reason = str(error.get("reason") or error.get("message") or "").strip()
    lines = [
        "",
        "上一次 Agent 回复未通过协议校验，系统将重新派发修订任务。",
    ]
    if reason:
        lines.append(f"校验原因：{_brief(reason, 300)}")
    return lines


def _render_reply_retry(ctx: Any) -> list[str]:
    error = getattr(ctx, "last_error", None)
    reason = ""
    contract_id = ""
    contract_errors: list[str] = []
    if isinstance(error, dict):
        reason = str(error.get("reason") or error.get("message") or "").strip()
        contract_id = str(error.get("contract_id") or "").strip()
        parallel = error.get("parallel")
        if isinstance(parallel, dict) and not contract_id:
            contract_id = str(parallel.get("contract_id") or "").strip()
        reasons = [reason]
        if isinstance(parallel, dict):
            reasons.extend(
                str(worker.get("error") or worker.get("reason") or "")
                for worker in (
                    *parallel.get("failed_workers", ()),
                    *parallel.get("rejected_workers", ()),
                )
                if isinstance(worker, dict)
            )
        marker = "STRUCTURED_ROLE_CONTRACT_INVALID:"
        for item in reasons:
            if marker in item:
                contract_errors.extend(
                    value.strip()
                    for value in item.split(marker, 1)[1].split(";")
                    if value.strip()
                )
    lines = [
        "",
        "这是对上一次未通过协议校验的回复进行重试，不是新的任务。",
    ]
    if reason:
        lines.append(f"上次校验原因：{_brief(reason, 300)}")
    if contract_id:
        lines.append(f"contract_id={contract_id}")
    if contract_errors:
        lines.append(
            "contract_repair_paths="
            + "; ".join(list(dict.fromkeys(contract_errors))[:20])
        )
    return lines


def _context_lines(ctx: Any) -> list[str]:
    lines: list[str] = []
    raw_request = getattr(ctx, "raw_request", "")
    if raw_request:
        lines.append(f"本轮目标：{_brief(raw_request, 220)}")
    phase = getattr(ctx, "current_phase", "")
    if phase:
        lines.append(f"阶段：{_phase_label(phase)}")
    group_id = getattr(ctx, "active_group_id", None)
    item_id = getattr(ctx, "active_item_id", None)
    if group_id:
        lines.append(f"当前组：{group_id}")
    if item_id:
        lines.append(f"当前 item：{item_id}")
    request_payload = getattr(ctx, "request_payload", {})
    if isinstance(request_payload, dict):
        active_group = request_payload.get("active_group")
        active_item = request_payload.get("active_item")
        if isinstance(active_group, dict) and active_group.get("title"):
            lines.append(f"组内容：{_brief(active_group['title'], 160)}")
        if isinstance(active_group, dict) and active_group.get("objective"):
            lines.append(f"组目标：{_brief(active_group['objective'], 200)}")
        if isinstance(active_item, dict) and active_item.get("title"):
            lines.append(f"任务内容：{_brief(active_item['title'], 180)}")
        if isinstance(active_item, dict) and active_item.get("objective"):
            lines.append(f"任务目标：{_brief(active_item['objective'], 220)}")
    return lines


def _render_solver(payload: dict[str, Any], ctx: Any) -> list[str]:
    plan = payload.get("plan") if isinstance(payload.get("plan"), dict) else {}
    groups = plan.get("groups") or plan.get("formal_groups")
    implementation = payload.get("implementation_proposal") or payload.get("implementation")
    if not isinstance(implementation, dict):
        implementation = {}
    lines = ["", "执行方案："]
    if isinstance(groups, list):
        item_by_id = {
            str(item.get("item_id")): item
            for item in plan.get("items", [])
            if isinstance(item, dict) and item.get("item_id")
        }
        rendered_groups = [
            group for group in groups
            if isinstance(group, dict)
        ]
        item_count = sum(
            len(_notification_group_items(group, item_by_id))
            for group in rendered_groups
        )
        lines.append(f"方案范围：{len(rendered_groups)} 个组，{item_count} 个条目")
        for group_index, group in enumerate(rendered_groups):
            title = group.get("title") or group.get("group_id")
            objective = group.get("objective") or group.get("goal")
            if title:
                lines.append(f"{group_index + 1}. {_brief(title, 140)}")
            if objective:
                lines.append(f"   目标：{_brief(objective, 180)}")
            for item in _notification_group_items(group, item_by_id):
                item_id = str(item.get("item_id") or "")
                item_title = item.get("title") or item.get("objective") or item_id
                if item_id and item_title:
                    lines.append(f"   - {item_id}：{_brief(item_title, 180)}")
    _append_field(lines, "目标", implementation.get("objective") or implementation.get("goal"), 220)
    _append_field(
        lines,
        "涉及位置",
        implementation.get("affected_modules") or implementation.get("files"),
        220,
    )
    _append_field(
        lines,
        "执行步骤",
        implementation.get("steps")
        or implementation.get("control_flow")
        or implementation.get("data_flow"),
        260,
    )
    _append_field(
        lines,
        "验证方式",
        implementation.get("verification")
        or implementation.get("tests")
        or implementation.get("validation"),
        220,
    )
    _append_field(lines, "回滚方式", implementation.get("rollback"), 180)
    action = payload.get("action")
    if action and action not in {"FEASIBLE", "READY_FOR_CRITIC"}:
        lines.append(f"结果：{_action_label(action)}")
    return lines if len(lines) > 1 else ["", "规划师正在整理当前条目的执行方案。"]


def _notification_group_items(
    group: dict[str, Any],
    item_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    items = group.get("items")
    if isinstance(items, list):
        if all(isinstance(item, dict) for item in items):
            return [item for item in items if item.get("item_id")]
        if all(isinstance(item, str) for item in items):
            return [
                item_by_id[item]
                for item in items
                if item in item_by_id
            ]
    item_ids = group.get("item_ids")
    if isinstance(item_ids, list):
        return [
            item_by_id[str(item_id)]
            for item_id in item_ids
            if str(item_id) in item_by_id
        ]
    return []


def _render_analyst(payload: dict[str, Any], ctx: Any) -> list[str]:
    lines = ["", "\u8bc1\u636e\u5ba1\u67e5："]
    if getattr(ctx, "current_phase", "") == "MENXIA":
        implementation = getattr(ctx, "request_payload", {}).get("implementation_proposal", {})
        if isinstance(implementation, dict):
            review_subject = (
                implementation.get("objective")
                or implementation.get("changes")
                or implementation.get("item_id")
            )
            if review_subject:
                lines.append(f"\u5ba1\u67e5\u5bf9\u8c61：{_brief(review_subject, 220)}")
        lines.append("\u6b63\u5411\u68c0\u67e5\u91cd\u70b9：\u5b9e\u73b0\u8fb9\u754c、\u73b0\u6709\u80fd\u529b\u590d\u7528、\u6570\u636e\u6d41和\u9a8c\u8bc1\u65b9\u5f0f。")
    plan = payload.get("plan")
    source = plan if isinstance(plan, dict) else payload

    action = payload.get("action") or source.get("action")
    assessment = payload.get("assessment") or source.get("assessment")
    verdict = assessment or action
    if verdict:
        lines.append(f"\u7ed3\u8bba：{_action_label(verdict)}")

    facts = source.get("confirmed_facts") or source.get("evidence")
    _append_analyst_entries(lines, "\u5df2\u786e\u8ba4", facts, 4, 190)

    gaps = source.get("missing_evidence") or source.get("unknowns")
    _append_analyst_entries(lines, "\u8bc1\u636e\u7f3a\u53e3 / \u672a\u77e5", gaps, 3, 190)

    trace = source.get("requirement_trace")
    _append_analyst_trace(lines, trace)

    questions = source.get("questions_for_solver") or source.get("follow_up")
    _append_analyst_entries(lines, "\u540e\u7eed\u8981\u6c42", questions, 3, 190)
    _append_analyst_entries(lines, "\u9700\u8981\u7528\u6237\u786e\u8ba4", source.get("questions_for_user"), 2, 180)
    return lines if len(lines) > 2 else ["", "\u5206\u6790\u5e08\u6b63\u5728\u6838\u9a8c\u5f53\u524d\u6761\u76ee\u7684\u8bc1\u636e。"]


def _append_analyst_trace(lines: list[str], value: Any) -> None:
    if not isinstance(value, dict):
        return
    status = value.get("status")
    covered = value.get("covered") or []
    partial = value.get("partial") or []
    missing = value.get("missing") or []
    parts = []
    if status:
        parts.append(f"status={status}")
    if covered:
        parts.append(f"covered={','.join(str(item) for item in covered[:8])}")
    if partial:
        parts.append(f"partial={','.join(str(item) for item in partial[:8])}")
    if missing:
        parts.append(f"missing={','.join(str(item) for item in missing[:8])}")
    if parts:
        lines.append(f"\u9700\u6c42\u8986\u76d6\uff1a{'; '.join(parts)}")


def _append_analyst_entries(
    lines: list[str],
    label: str,
    value: Any,
    max_items: int,
    item_limit: int,
) -> None:
    if value is None or value == "" or value == [] or value == {}:
        return
    entries = value if isinstance(value, list) else [value]
    lines.append(f"{label}：")
    for entry in entries[:max_items]:
        if isinstance(entry, dict):
            evidence_id = str(entry.get("evidence_id") or entry.get("id") or "")
            statement = (
                entry.get("statement")
                or entry.get("question")
                or entry.get("description")
                or entry.get("reason")
                or _compact_dict(entry)
            )
            source = entry.get("source") or entry.get("source_type")
            prefix = f"{evidence_id}：" if evidence_id else "- "
            suffix = f" ({_brief(source, 100)})" if source else ""
            lines.append(f"  - {prefix}{_brief(statement, item_limit)}{suffix}")
        else:
            lines.append(f"  - {_brief(entry, item_limit)}")
    if len(entries) > max_items:
        lines.append(f"  - 其余 {len(entries) - max_items} 项见任务记录")


def _append_critic_entries(
    lines: list[str],
    label: str,
    value: Any,
    max_items: int,
    item_limit: int,
) -> None:
    """Render critic follow-up requests without exposing the raw payload."""
    if value is None or value == "" or value == [] or value == {}:
        return
    entries = value if isinstance(value, list) else [value]
    lines.append(f"{label}：")
    for entry in entries[:max_items]:
        if isinstance(entry, dict):
            text = (
                entry.get("question")
                or entry.get("required_change")
                or entry.get("required_action")
                or entry.get("description")
                or entry.get("reason")
                or _compact_dict(entry)
            )
        else:
            text = entry
        lines.append(f"  - {_brief(text, item_limit)}")
    if len(entries) > max_items:
        lines.append(f"  - 其余 {len(entries) - max_items} 项见任务记录")


def _render_critic(payload: dict[str, Any], ctx: Any) -> list[str]:
    lines = ["", "方案审查："]
    action = payload.get("action")
    if action:
        action_text = str(action)
        lines.append(f"结论：{_action_label(action_text)}")
        if action_text in {"REQUEST_ANALYST_EVIDENCE", "NEEDS_MORE_EVIDENCE"}:
            lines.append("后续处理：先补充证据，再由规划师重新处理当前方案。")
    findings = payload.get("findings")
    if isinstance(findings, list) and findings:
        severity_counts: dict[str, int] = {}
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            severity = _finding_severity(finding)
            if severity:
                severity_counts[severity] = severity_counts.get(severity, 0) + 1
        if severity_counts:
            summary = "，".join(
                f"{severity}×{severity_counts[severity]}"
                for severity in ("P0", "P1", "P2", "P3")
                if severity in severity_counts
            )
            lines.append(f"问题等级：{summary}")
        lines.append("关键问题：")
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            severity = _finding_severity(finding)
            status = _finding_status(finding)
            title = finding.get("title") or finding.get("claim") or finding.get("finding_id")
            scope = "/".join(
                str(value)
                for value in (finding.get("group_id"), finding.get("item_id"))
                if value
            )
            required = (
                finding.get("required_change")
                or finding.get("required_action")
                or finding.get("next_action")
            )
            prefix = "、".join(value for value in (severity, status) if value)
            title_text = _brief(title, 150) if title else "未命名问题"
            if scope:
                title_text = f"{scope}：{title_text}"
            lines.append(
                f"- [{prefix}] {title_text}"
                if prefix
                else f"- {title_text}"
            )
            if required:
                lines.append(f"  要求：{_brief(required, 180)}")
    _append_critic_entries(
        lines,
        "补证据说明",
        payload.get("questions_for_analyst") or payload.get("missing_evidence"),
        5,
        150,
    )
    _append_field(
        lines,
        "要求修改",
        payload.get("required_changes") or payload.get("required_change"),
        300,
    )
    if len(lines) == 1:
        lines.append("审查员正在检查当前方案的边界、风险和可验证性。")
    return lines


def _finding_severity(finding: dict[str, Any]) -> str:
    value = str(finding.get("severity") or finding.get("priority") or "").upper()
    return value if value in {"P0", "P1", "P2", "P3"} else ""


def _finding_status(finding: dict[str, Any]) -> str:
    status = str(finding.get("status") or "").upper()
    if status in {"DEFERRED", "FOLLOW_UP", "ACCEPTED_RISK"}:
        return "非阻塞·已延期"
    if status in {"RESOLVED", "WONT_FIX"}:
        return "已处理"
    if status in {"OPEN", "ASSIGNED_TO_ANALYST", "ASSIGNED_TO_SOLVER", "IN_REVIEW", "REOPENED"}:
        severity = _finding_severity(finding)
        return "阻塞" if severity in {"P0", "P1"} else "待处理"
    if finding.get("blocking") is True:
        return "阻塞"
    return ""


def _render_group_summary(payload: dict[str, Any], ctx: Any) -> list[str]:
    lines = ["", "组级结果："]
    action = payload.get("action")
    if action:
        lines.append(f"结论：{_action_label(action)}")
    _append_field(lines, "已完成条目", payload.get("completed_item_count"), 80)
    _append_field(lines, "修订次数", payload.get("revision_count"), 80)
    _append_field(lines, "人工决策", payload.get("human_gate_count"), 80)
    findings = payload.get("findings")
    if isinstance(findings, list) and findings:
        severity_counts: dict[str, int] = {}
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            severity = _finding_severity(finding)
            if severity:
                severity_counts[severity] = severity_counts.get(severity, 0) + 1
        if severity_counts:
            summary = "，".join(
                f"{severity}×{severity_counts[severity]}"
                for severity in ("P0", "P1", "P2", "P3")
                if severity in severity_counts
            )
            lines.append(f"问题等级：{summary}")
        for finding in findings[:3]:
            if not isinstance(finding, dict):
                continue
            severity = _finding_severity(finding)
            status = _finding_status(finding)
            title = finding.get("title") or finding.get("claim") or finding.get("finding_id")
            if not title:
                continue
            prefix = "、".join(value for value in (severity, status) if value)
            lines.append(
                f"关键问题：[{prefix}] {_brief(title, 180)}"
                if prefix
                else f"关键问题：{_brief(title, 180)}"
            )
    _append_field(
        lines,
        "一致性",
        payload.get("group_consistency") or payload.get("consistency"),
        220,
    )
    return lines


def _render_final_delivery(payload: dict[str, Any], ctx: Any) -> list[str]:
    delivery = (
        payload.get("final_delivery")
        if isinstance(payload.get("final_delivery"), dict)
        else payload
    )
    lines = ["", "最终交付方案："]
    groups = delivery.get("groups")
    items = delivery.get("items") if isinstance(delivery.get("items"), list) else []
    item_by_id = {
        str(item.get("item_id")): item
        for item in items
        if isinstance(item, dict) and item.get("item_id")
    }
    if isinstance(groups, list) and groups:
        lines.append(
            f"范围：{len(groups)} 个组，"
            f"{sum(len(_notification_group_items(group, item_by_id)) for group in groups if isinstance(group, dict))} 个条目"
        )
        for group_index, group in enumerate(groups):
            if not isinstance(group, dict):
                continue
            title = group.get("title") or group.get("group_id")
            if title:
                lines.append(f"{group_index + 1}. {_brief(title, 180)}")
            _append_final_field(lines, "目标", group.get("objective"), 220, indent="   ")
            group_items = _notification_group_items(group, item_by_id)
            for item in group_items:
                lines.extend(_render_final_item(item))
    else:
        _append_final_field(lines, "方案", items or delivery.get("plan"), 520)
    implementation = getattr(ctx, "request_payload", {}).get("implementation_proposal")
    if isinstance(implementation, dict):
        lines.append("实现方案：")
        _append_final_field(lines, "目标", implementation.get("objective"), 320, indent="   ")
        _append_final_field(
            lines,
            "涉及位置",
            implementation.get("affected_modules") or implementation.get("files"),
            420,
            indent="   ",
        )
        _append_final_field(
            lines,
            "执行步骤",
            implementation.get("control_flow")
            or implementation.get("steps")
            or implementation.get("data_flow"),
            560,
            indent="   ",
        )
        _append_final_field(
            lines,
            "流程/状态",
            implementation.get("state_transitions")
            or implementation.get("control_flow")
            or implementation.get("data_flow"),
            560,
            indent="   ",
        )
        _append_final_field(
            lines,
            "接口/数据契约",
            implementation.get("interfaces")
            or implementation.get("data_contracts"),
            460,
            indent="   ",
        )
        _append_final_field(
            lines,
            "复用/新增",
            implementation.get("reused_components")
            or implementation.get("new_components"),
            420,
            indent="   ",
        )
        _append_final_field(
            lines,
            "验证/回滚",
            implementation.get("verification")
            or implementation.get("verification_plan")
            or implementation.get("rollback"),
            460,
            indent="   ",
        )
        _append_final_field(
            lines,
            "异常与重试",
            implementation.get("error_handling")
            or implementation.get("timeout_retry"),
            420,
            indent="   ",
        )
        _append_final_field(
            lines,
            "交付物",
            implementation.get("completed_outputs"),
            360,
            indent="   ",
        )
        _append_final_field(
            lines,
            "执行边界",
            implementation.get("execution_status")
            or implementation.get("constraints")
            or implementation.get("migration")
            or implementation.get("configuration"),
            360,
            indent="   ",
        )
    _append_final_field(lines, "审查结果", delivery.get("review_results") or delivery.get("outcomes"), 260)
    _append_final_field(lines, "修订情况", delivery.get("revisions"), 220)
    _append_final_field(lines, "剩余风险", delivery.get("remaining_risks") or delivery.get("risks"), 360)
    lines.append("说明：方案通过不代表代码已实现或测试已执行。")
    return lines


def _render_final_item(item: dict[str, Any]) -> list[str]:
    item_id = str(item.get("item_id") or "")
    title = item.get("title") or item.get("objective") or item_id
    lines = [f"   - {item_id}：{_brief(title, 220)}"] if item_id else []
    _append_final_field(lines, "问题", item.get("problem_addressed"), 320, indent="     ")
    _append_final_field(lines, "目标", item.get("objective"), 320, indent="     ")
    _append_final_field(lines, "关键证据", item.get("basis_evidence"), 300, indent="     ")
    _append_final_field(
        lines,
        "实施/调查步骤",
        item.get("implementation_steps")
        or item.get("steps")
        or item.get("expected_outputs"),
        520,
        indent="     ",
    )
    _append_final_field(
        lines,
        "验收标准",
        item.get("acceptance_signals") or item.get("acceptance_criteria"),
        420,
        indent="     ",
    )
    _append_final_field(lines, "风险", item.get("risk_signals"), 300, indent="     ")
    proposal = item.get("implementation_proposal")
    if isinstance(proposal, dict):
        _append_final_field(lines, "实现方案", proposal.get("objective"), 320, indent="     ")
        _append_final_field(
            lines,
            "涉及位置",
            proposal.get("affected_modules") or proposal.get("files"),
            360,
            indent="     ",
        )
        _append_final_field(
            lines,
            "执行步骤",
            proposal.get("control_flow")
            or proposal.get("steps")
            or proposal.get("diagnostic_scope"),
            520,
            indent="     ",
        )
    return lines


def _append_final_field(
    lines: list[str],
    label: str,
    value: Any,
    limit: int,
    indent: str = "",
) -> None:
    if value is None or value == "" or value == [] or value == {}:
        return
    if isinstance(value, list):
        parts = [_brief(item, max(80, limit // max(1, min(len(value), 5)))) for item in value]
        text = "；".join(parts)
    elif isinstance(value, dict):
        preferred = (
            value.get("summary")
            or value.get("description")
            or value.get("objective")
            or value.get("status")
        )
        text = _brief(preferred if preferred is not None else _compact_dict(value), limit)
    else:
        text = _brief(value, limit)
    lines.append(f"{indent}{label}：{_brief(text, limit)}")


def _append_field(lines: list[str], label: str, value: Any, limit: int) -> None:
    if value is None or value == "" or value == [] or value == {}:
        return
    if isinstance(value, list):
        values = [_brief(item, limit) for item in value[:4]]
        text = "；".join(values)
    elif isinstance(value, dict):
        preferred = (
            value.get("summary")
            or value.get("description")
            or value.get("status")
            or value.get("objective")
        )
        text = _brief(preferred if preferred is not None else _compact_dict(value), limit)
    else:
        text = _brief(value, limit)
    lines.append(f"{label}：{text}")


def _action_label(action: Any) -> str:
    return ACTION_LABELS.get(str(action), "待确认结果")


def _phase_label(phase: str) -> str:
    return {"ZHONGSHU": "中书省", "MENXIA": "门下省"}.get(phase, phase)


def _fmt_elapsed(seconds: Any) -> str:
    try:
        value = max(0, int(seconds))
    except (TypeError, ValueError):
        return "未知时长"
    minutes, remaining = divmod(value, 60)
    if minutes and remaining:
        return f"{minutes} 分 {remaining} 秒"
    if minutes:
        return f"{minutes} 分钟"
    return f"{remaining} 秒"


def _compact_dict(value: dict[str, Any]) -> str:
    parts = []
    for key, item in value.items():
        if item in (None, "", [], {}):
            continue
        parts.append(f"{key}={_brief(item, 80)}")
        if len(parts) >= 3:
            break
    return "；".join(parts)


def _brief(value: Any, limit: int = 180) -> str:
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _bounded(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 9].rstrip() + "\n（内容已压缩）"
