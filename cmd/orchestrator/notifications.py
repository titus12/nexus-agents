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

    lines = [f"【{display_role}｜{_state_label(state, event_name, payload)}】"]
    lines.extend(_context_lines(ctx))

    if state == "DONE" or payload.get("action") == "DONE":
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
    return _bounded(text, limit)


def _state_label(state: str, event_name: str, payload: dict[str, Any]) -> str:
    action = payload.get("action")
    if event_name == "STATE_ENTER":
        return "开始处理"
    if event_name == "LOCAL_VALIDATION_PASSED":
        return "本地校验通过"
    if event_name == "HUMAN_DECISION_RECEIVED":
        return "人工决策已收到"
    return ACTION_LABELS.get(action, "处理结果")


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
    lines = ["", "证据审查："]
    if getattr(ctx, "current_phase", "") == "MENXIA":
        implementation = getattr(ctx, "request_payload", {}).get("implementation_proposal", {})
        if implementation:
            lines.append(f"审查对象：{_brief(implementation, 240)}")
        lines.append("正向检查：实现边界、现有能力复用、数据流和验证方式。")
    action = payload.get("action")
    assessment = payload.get("assessment")
    verdict = assessment or action
    if verdict:
        lines.append(f"结论：{_action_label(verdict)}")
    _append_field(lines, "已确认", payload.get("confirmed_facts") or payload.get("evidence"), 300)
    _append_field(
        lines,
        "证据缺口",
        payload.get("missing_evidence") or payload.get("unknowns"),
        260,
    )
    _append_field(
        lines,
        "后续要求",
        payload.get("questions_for_solver") or payload.get("follow_up"),
        260,
    )
    return lines if len(lines) > 2 else ["", "分析师正在核验当前条目的证据。"]


def _render_critic(payload: dict[str, Any], ctx: Any) -> list[str]:
    lines = ["", "方案审查："]
    action = payload.get("action")
    if action:
        lines.append(f"结论：{_action_label(action)}")
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
        for finding in findings[:4]:
            if not isinstance(finding, dict):
                continue
            severity = _finding_severity(finding)
            status = _finding_status(finding)
            title = finding.get("title") or finding.get("claim") or finding.get("finding_id")
            required = (
                finding.get("required_change")
                or finding.get("required_action")
                or finding.get("next_action")
            )
            prefix = "、".join(value for value in (severity, status) if value)
            if title:
                lines.append(
                    f"- [{prefix}] {_brief(title, 180)}"
                    if prefix
                    else f"- {_brief(title, 180)}"
                )
            if required:
                lines.append(f"  要求：{_brief(required, 220)}")
    _append_field(lines, "要求修改", payload.get("required_changes"), 300)
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
    if isinstance(groups, list) and groups:
        item_by_id = {
            str(item.get("item_id")): item
            for item in delivery.get("items", [])
            if isinstance(item, dict) and item.get("item_id")
        }
        for group_index, group in enumerate(groups):
            if not isinstance(group, dict):
                continue
            title = group.get("title") or group.get("group_id")
            if title:
                lines.append(f"{group_index + 1}. {_brief(title, 180)}")
            group_items = _notification_group_items(group, item_by_id)
            for item in group_items:
                item_id = str(item.get("item_id") or "")
                item_title = item.get("title") or item.get("objective") or item_id
                if item_id and item_title:
                    lines.append(f"   - {item_id}：{_brief(item_title, 180)}")
    else:
        _append_field(lines, "方案", delivery.get("items") or delivery.get("plan"), 420)
    _append_field(lines, "审查结果", delivery.get("review_results") or delivery.get("outcomes"), 220)
    _append_field(lines, "修订情况", delivery.get("revisions"), 180)
    _append_field(lines, "剩余风险", delivery.get("remaining_risks") or delivery.get("risks"), 260)
    lines.append("说明：方案通过不代表代码已实现或测试已执行。")
    return lines


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
