from __future__ import annotations

import json
from typing import Any


ROLE_NAMES = {
    "review-analyst": "分析师",
    "review-solver": "规划师",
    "review-critic": "审查员",
}


def build_agent_notification(
    role: str,
    state: str,
    event_name: str,
    ctx: Any,
    payload: dict[str, Any] | None = None,
    limit: int = 1400,
) -> str:
    if payload is None:
        if event_name == "STATE_ENTER":
            request_payload = getattr(ctx, "request_payload", {})
            payload = request_payload.get("notification_payload", {}) if isinstance(request_payload, dict) else {}
        else:
            payload = getattr(ctx, "last_agent_payload", {}) or {}
    display_role = ROLE_NAMES.get(role, role)
    group_id = getattr(ctx, "active_group_id", None) or "未进入具体组"
    item_id = getattr(ctx, "active_item_id", None) or "未进入具体任务"
    lines = [f"【{display_role}】"]
    status_text = {
        "STATE_ENTER": "我开始处理",
        "HEARTBEAT": "我仍在等待 Agent 回复",
        "AGENT_TIMEOUT": "等待 Agent 回复已超时",
        "RESUME": "我正在恢复之前的工作",
    }.get(event_name, "我已完成")
    lines.append(f"{status_text} {state} 阶段。")

    raw_request = getattr(ctx, "raw_request", "")
    if raw_request:
        lines.append(f"本轮目标：{_brief(raw_request, 260)}")
    lines.append(f"当前组：{group_id}；当前任务：{item_id}。")
    if event_name == "HEARTBEAT":
        elapsed = payload.get("elapsed_seconds")
        agent_id = payload.get("expected_agent_id")
        lines.append(f"当前等待：{role} 返回结构化结果；已等待约 {elapsed} 秒。")
        if agent_id:
            lines.append(f"目标 Agent：{agent_id}。")

    request_payload = getattr(ctx, "request_payload", {})
    active_group = request_payload.get("active_group") if isinstance(request_payload, dict) else None
    active_item = request_payload.get("active_item") if isinstance(request_payload, dict) else None
    if isinstance(active_group, dict):
        title = active_group.get("title") or active_group.get("group_id") or group_id
        objective = active_group.get("objective") or active_group.get("goal")
        lines.append(f"组内容：{_brief(title, 140)}。")
        if objective:
            lines.append(f"组目标：{_brief(objective, 220)}。")
    if isinstance(active_item, dict):
        title = active_item.get("title") or active_item.get("item_id") or item_id
        objective = active_item.get("objective") or active_item.get("goal")
        lines.append(f"任务内容：{_brief(title, 160)}。")
        if objective:
            lines.append(f"任务目标：{_brief(objective, 240)}。")

    if role == "review-analyst":
        lines.extend(_analyst_lines(payload, ctx))
    elif role == "review-solver":
        lines.extend(_solver_lines(payload, ctx))
    elif role == "review-critic":
        lines.extend(_critic_lines(payload, ctx))
    else:
        lines.append(f"处理事件：{event_name}。")

    text = "\n".join(lines)
    return text if len(text) <= limit else text[: limit - 20].rstrip() + "\n（内容已压缩）"


def _analyst_lines(payload: dict[str, Any], ctx: Any) -> list[str]:
    lines: list[str] = []
    if getattr(ctx, "current_phase", "") == "MENXIA":
        implementation = getattr(ctx, "request_payload", {}).get("implementation_proposal", {})
        if implementation:
            lines.append(f"审查对象：{_brief(implementation, 240)}")
        lines.append("正向检查：实现是否正确、是否能复用现有能力、数据流是否完整、测试是否可验证。")
    evidence = payload.get("evidence") or payload.get("evidence_packet")
    tasks = payload.get("items") or payload.get("tasks")
    groups = payload.get("groups") or payload.get("candidate_groups")
    if evidence:
        lines.append(f"我核对了证据：{_brief(evidence)}")
    if isinstance(tasks, list):
        lines.append(f"我拆分出 {len(tasks)} 个任务。")
    if isinstance(groups, list):
        lines.append(f"我建议按 {len(groups)} 个组组织后续工作。")
    if payload.get("action"):
        lines.append(f"我的结论是：{payload['action']}。")
    return lines or ["本轮重点是核对需求覆盖、证据完整性和任务拆分。"]


def _solver_lines(payload: dict[str, Any], ctx: Any) -> list[str]:
    plan = payload.get("plan") if isinstance(payload.get("plan"), dict) else payload
    groups = plan.get("groups") or plan.get("formal_groups")
    lines: list[str] = []
    if isinstance(groups, list):
        item_count = sum(
            len(group.get("items", []))
            for group in groups
            if isinstance(group, dict) and isinstance(group.get("items"), list)
        )
        lines.append(f"我形成了 {len(groups)} 个组、{item_count} 个任务的方案。")
        for group in groups[:3]:
            if isinstance(group, dict):
                title = group.get("title") or group.get("group_id") or "未命名组"
                objective = group.get("objective") or "未说明目标"
                lines.append(f"组「{title}」：目标是{_brief(objective, 120)}。")
    if getattr(ctx, "current_phase", "") == "MENXIA":
        proposal = payload.get("implementation_proposal") if isinstance(payload, dict) else None
        if isinstance(proposal, dict):
            lines.append(f"本任务实现方案：{_brief(proposal, 360)}")
        lines.append("交付内容：代码修改范围、接口调用、数据流、测试和回滚方案。")
    if payload.get("action"):
        lines.append(f"方案状态：{payload['action']}。")
    return lines or ["我正在整理任务边界、依赖关系和可执行的实现方案。"]


def _critic_lines(payload: dict[str, Any], ctx: Any) -> list[str]:
    findings = payload.get("findings")
    lines: list[str] = []
    if getattr(ctx, "current_phase", "") == "MENXIA":
        implementation = getattr(ctx, "request_payload", {}).get("implementation_proposal", {})
        analyst_review = getattr(ctx, "request_payload", {}).get("analyst_review", {})
        if implementation:
            lines.append(f"反向审查对象：{_brief(implementation, 260)}")
        if analyst_review:
            lines.append(f"已参考分析师正向审查：{_brief(analyst_review, 220)}")
        lines.append("反向检查：修改范围、兼容性、性能、安全、回滚和测试遗漏。")
    if isinstance(findings, list):
        counts: dict[str, int] = {}
        for finding in findings:
            if isinstance(finding, dict):
                severity = str(finding.get("severity") or "未分级")
                counts[severity] = counts.get(severity, 0) + 1
        if counts:
            lines.append("我发现的问题：" + "、".join(
                f"{key} {value} 个" for key, value in counts.items()
            ) + "。")
        for finding in findings[:3]:
            if isinstance(finding, dict):
                title = finding.get("title") or finding.get("summary") or finding.get("finding_id")
                if title:
                    lines.append(f"重点关注：{_brief(title, 140)}。")
    action = payload.get("action")
    if action:
        lines.append(f"审查结论：{action}。")
    return lines or ["我正在从覆盖性、可行性和风险边界三个角度审查当前方案。"]


def _brief(value: Any, limit: int = 180) -> str:
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"
