from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
from typing import Any, Dict, Optional


_WRAPPER_FILE = Path(__file__).resolve()


def _load_original():
    cache = _WRAPPER_FILE.with_name("__pycache__")
    candidates = sorted(cache.glob("review_orchestrator_v2.cpython-*.pyc"))
    if not candidates:
        raise RuntimeError("Cannot find compiled review_orchestrator_v2 module")
    spec = importlib.util.spec_from_file_location("_review_orchestrator_v2_original", candidates[-1])
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load compiled review_orchestrator_v2 module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_ORIGINAL = _load_original()

# Preserve the original public API for tests and command-line callers.
for _name, _value in vars(_ORIGINAL).items():
    if not _name.startswith("__"):
        globals()[_name] = _value


def _format_human_gate_question(value: Any, limit: int = 200) -> str:
    """Render a HUMAN_GATE question safely for text notifications."""
    if isinstance(value, dict):
        text = value.get("question") or value.get("text") or value.get("title")
        if text is None:
            text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    else:
        text = value
    text = str(text or "").replace("\r", " ").replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _question_as_string(value: Any) -> str:
    if not isinstance(value, dict):
        return _format_human_gate_question(value)
    text = _format_human_gate_question(value)
    context = _format_human_gate_question(value.get("context", ""), 120)
    options = value.get("options")
    if context:
        text += "（背景：%s）" % context
    if isinstance(options, list) and options:
        text += "；选项：" + " / ".join(
            _format_human_gate_question(option, 80) for option in options[:4]
        )
    return _format_human_gate_question(text)


_original_handle_human_gate = _ORIGINAL.handle_human_gate


def handle_human_gate(
    store,
    feishu,
    issue_id: str,
    gate_request: Dict[str, Any],
    phase: str,
    context_payload: Optional[Dict[str, Any]] = None,
    role: str = "",
):
    """Normalize structured questions before invoking the original gate handler."""
    normalized_gate = copy.deepcopy(gate_request or {})
    if isinstance(normalized_gate.get("question"), dict):
        normalized_gate["question"] = _question_as_string(normalized_gate["question"])
    normalized_context = copy.deepcopy(context_payload) if isinstance(context_payload, dict) else context_payload
    if isinstance(normalized_context, dict):
        questions = normalized_context.get("questions_for_user")
        if isinstance(questions, list):
            normalized_context["questions_for_user"] = [_question_as_string(q) for q in questions]
    return _original_handle_human_gate(
        store,
        feishu,
        issue_id,
        normalized_gate,
        phase,
        normalized_context,
        role,
    )


_ORIGINAL.handle_human_gate = handle_human_gate


def _clip(value: Any, limit: int = 180) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _plan_payload(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    plan = payload.get("plan")
    return plan if isinstance(plan, dict) else payload


def _build_role_notification(self, role: str, phase: str, payload: Dict[str, Any], action: str, elapsed: int = 0) -> str:
    payload = payload if isinstance(payload, dict) else {}
    lines = ["【%s】✅ 已完成 %s 阶段工作" % (self._role(role), self._phase(phase))]
    if elapsed > 0:
        lines.append("耗时：%s" % self._fmt_elapsed(elapsed))
    lines.append("动作：%s" % action)

    if role == "analyst":
        context = payload.get("context_check", {})
        revalidation = payload.get("source_revalidation", {})
        findings = context.get("new_findings", []) if isinstance(context, dict) else []
        evidence = revalidation.get("new_evidence", []) if isinstance(revalidation, dict) else []
        lines += [
            "",
            "本轮分析内容：",
            "• 核对需求涉及的文档、代码和前端入口",
            "• 提取并验证证据，检查现有方案覆盖完整性",
            "• 识别影响方案方向的未知项和风险",
            "主要产出：%d 条新发现，%d 条新证据" % (len(findings), len(evidence)),
        ]
        for finding in findings[:3]:
            if isinstance(finding, dict):
                lines.append("• 发现：%s" % _clip(
                    finding.get("statement") or finding.get("title") or finding.get("finding_id")
                ))
        unknowns = context.get("direction_changing_unknowns", []) if isinstance(context, dict) else []
        if unknowns:
            lines.append("待确认方向：%s" % _clip(", ".join(map(str, unknowns[:4])), 160))
        lines.append("交付物：EvidencePacket / CandidateGroups")

    elif role == "solver":
        plan = _plan_payload(payload)
        groups = plan.get("groups") or plan.get("formal_groups") or []
        if not isinstance(groups, list):
            groups = []
        selected = plan.get("selected_direction", {})
        selected_id = selected.get("option_id") if isinstance(selected, dict) else None
        item_count = sum(
            len(group.get("items", []))
            for group in groups
            if isinstance(group, dict) and isinstance(group.get("items"), list)
        )
        lines += [
            "",
            "本轮方案内容：",
            "• 将需求拆成可独立审查的 item，再按目标和依赖组织成正式 group",
            "• 方案方向：%s" % _clip(selected_id or plan.get("objective") or "未声明"),
            "• 方案结构：%d 个 group，%d 个 item" % (len(groups), item_count),
        ]
        for index, group in enumerate(groups[:4], 1):
            if not isinstance(group, dict):
                continue
            lines += [
                "",
                "Group %d：%s" % (
                    index,
                    _clip(group.get("title") or group.get("objective") or group.get("group_id"), 120),
                ),
                "目标：%s" % _clip(group.get("objective") or "未提供", 180),
            ]
            items = group.get("items") if isinstance(group.get("items"), list) else []
            for item in items[:4]:
                if isinstance(item, dict):
                    lines.append("  • %s" % _clip(
                        item.get("title") or item.get("objective") or item.get("item_id"), 130
                    ))
        scope = plan.get("scope")
        if isinstance(scope, dict):
            lines += [
                "",
                "范围：纳入 %d 项，排除 %d 项" % (
                    len(scope.get("in_scope") or []),
                    len(scope.get("out_of_scope") or []),
                ),
            ]
        lines.append("交付物：ZhongshuPlan，完整字段已保存到 artifact")

    elif role == "critic":
        findings = payload.get("findings", [])
        questions = payload.get("questions_for_user", [])
        next_actions = payload.get("next_actions", [])
        blockers = [
            finding for finding in findings
            if isinstance(finding, dict)
            and finding.get("severity") in ("P0", "P1")
            and finding.get("status") in ("open", "needs_human")
        ]
        lines += [
            "",
            "本轮审查内容：",
            "• 检查需求覆盖、证据充分性、group/item 结构、依赖关系和可验证性",
            "• 结论：%d 个发现，其中 %d 个 P0/P1 阻塞问题" % (len(findings), len(blockers)),
        ]
        for finding in findings[:4]:
            if isinstance(finding, dict):
                lines.append("• 【%s】%s" % (
                    finding.get("severity", "P?"),
                    _clip(finding.get("title") or finding.get("claim") or finding.get("finding_id")),
                ))
        if questions:
            lines.append("需要人工确认：")
            for question in questions[:3]:
                lines.append("  • %s" % _format_human_gate_question(question))
        if next_actions:
            lines.append("下一步：")
            for next_action in next_actions[:3]:
                if isinstance(next_action, dict):
                    lines.append("  • %s：%s" % (
                        next_action.get("target", "相关角色"),
                        _clip(next_action.get("request", ""), 160),
                    ))
                else:
                    lines.append("  • %s" % _format_human_gate_question(next_action, 160))
        lines.append("交付物：ZhongshuCriticFindings")

    lines.append(self._task_ref())
    return "\n".join(lines)


def _notify_agent_result(self, role: str, phase: str, payload: Dict[str, Any], action: str, elapsed: int = 0):
    self._notify(_build_role_notification(self, role, phase, payload, action, elapsed), role=role)


_ORIGINAL.OrchestratorV2._notify_agent_result = _notify_agent_result
_ORIGINAL.OrchestratorV2._build_role_notification = _build_role_notification


_original_wait_and_parse = _ORIGINAL.OrchestratorV2._wait_and_parse


def _wait_and_parse(self, phase: str, role: str, sent_at: str):
    original_notify = self._notify

    def filtered_notify(text: str, notify_role: str = ""):
        # Suppress the old terse completion message; preserve heartbeats and errors.
        if "已完成工作" in str(text) or ("用时" in str(text) and "：" in str(text) and self._role(role) in str(text)):
            return
        return original_notify(text, notify_role)

    self._notify = filtered_notify
    try:
        payload = _original_wait_and_parse(self, phase, role, sent_at)
    finally:
        self._notify = original_notify
    if isinstance(payload, dict):
        action = payload.get("action") or payload.get("status") or "RECEIVED"
        elapsed = 0
        self._notify_agent_result(role, phase, payload, str(action), elapsed)
    return payload


_ORIGINAL.OrchestratorV2._wait_and_parse = _wait_and_parse


if __name__ == "__main__":
    _ORIGINAL.main()
