"""Human-facing plan review document rendered at Zhongshu completion.

Thin-doc positioning (2026-09-27): the Zhongshu deliverable for its human
reviewer is ONE document -- background, goals, boundaries, constraints,
task decomposition, acceptance assertions, open questions, risks -- that a
person can read in three screens and sign off before the run hands over to
Menxia.  The per-group nine-section documents stay the machine model
(projection, freeze zone, version chain); this renderer assembles their
human-relevant sections plus the structured review state into the review
document.  Pure text projection: no I/O, no new state fields.
"""

from __future__ import annotations

from collections.abc import Mapping

from .domain.context import WorkflowContext
from .domain.decisions import EffectRequest
from .domain.zhongshu_doc import ZhongshuRequirementDoc

__all__ = ["render_plan_review_doc", "render_plan_review_packet", "plan_review_doc_effect"]


def _group_rows(context: WorkflowContext) -> list[object]:
    review = context.review
    return [
        row
        for row in (getattr(review, "zhongshu_groups", ()) or ())
        if str(getattr(row, "doc_markdown", "") or "").strip()
    ]


def _doc_sections(row: object) -> dict[int, str]:
    markdown = str(getattr(row, "doc_markdown", "") or "")
    try:
        doc = ZhongshuRequirementDoc.parse(markdown)
    except Exception:
        return {}
    return {
        index: (doc.section(index) or "").strip()
        for index in range(1, 10)
    }


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _header(context: WorkflowContext) -> list[str]:
    review = context.review
    rows = _group_rows(context)
    converged = sum(
        1
        for row in rows
        if str(getattr(row, "stage", "") or "") in ("CONVERGED", "FROZEN")
    )
    items = getattr(review, "task_items", ()) or ()
    open_findings = sum(
        1
        for finding in (getattr(review, "findings", ()) or ())
        if getattr(finding, "active", False)
    )
    return [
        "# 中书省方案（审阅稿）",
        "",
        "| | |",
        "|---|---|",
        f"| 任务 | {context.identity.task_id} |",
        f"| 需求来源 | {context.request.raw_request.strip()} |",
        f"| 规模 | {len(items)} 个任务 / {len(rows)} 个组 |",
        f"| 中书审定 | {converged}/{len(rows)} 组通过（APPROVE_GROUP） |",
        f"| 待办 | **人工预审 → 批准后交门下省实施**（未决 finding：{open_findings}） |",
        "",
    ]


def _grouped_sections(context: WorkflowContext, indexes: tuple[int, ...]) -> list[str]:
    out: list[str] = []
    for row in _group_rows(context):
        sections = _doc_sections(row)
        pieces = [
            sections[index]
            for index in indexes
            if sections.get(index)
        ]
        if not pieces:
            continue
        group_id = str(getattr(row, "group_id", "") or "")
        out.append(f"**{group_id}**：")
        for piece in pieces:
            out.extend(_lines(piece))
        out.append("")
    return out


def _task_table(context: WorkflowContext) -> list[str]:
    review = context.review
    plan = getattr(review, "plan", None) or {}
    deps_by_item: dict[str, list[str]] = {}
    if isinstance(plan, Mapping):
        for item in plan.get("items") or []:
            if isinstance(item, Mapping) and item.get("item_id"):
                deps_by_item[str(item["item_id"])] = [
                    str(value) for value in (item.get("dependencies") or ())
                ]
    out = [
        "| 任务 | 做什么 | 组 | 来源需求 | 依赖 | 规模 |",
        "|---|---|---|---|---|---|",
    ]
    for item in getattr(review, "task_items", ()) or ():
        item_id = str(getattr(item, "item_id", "") or "")
        deps = deps_by_item.get(item_id) or list(getattr(item, "dependencies", ()) or ())
        out.append(
            "| {item} | {title} | {group} | {reqs} | {deps} | {size} |".format(
                item=item_id,
                title=str(getattr(item, "title", "") or "").strip(),
                group=str(getattr(item, "group_id", "") or ""),
                reqs=", ".join(
                    str(value)
                    for value in (getattr(item, "source_requirement_ids", ()) or ())
                ),
                deps=", ".join(deps) or "无",
                size="M",
            )
        )
    out.append("")
    return out


def _acceptance_checklist(context: WorkflowContext) -> list[str]:
    out: list[str] = []
    for item in getattr(context.review, "task_items", ()) or ():
        signals = tuple(getattr(item, "acceptance_signals", ()) or ())
        if not signals:
            continue
        out.append(f"**{getattr(item, 'item_id', '')}**")
        for signal in signals:
            out.append(f"- [ ] {str(signal).strip()}")
        out.append("")
    return out


def _open_questions(context: WorkflowContext) -> list[str]:
    review = context.review
    plan = getattr(review, "plan", None) or {}
    needs_decision: list[str] = []
    pending_measure: list[str] = []
    if isinstance(plan, Mapping):
        for entry in plan.get("unknowns") or []:
            if isinstance(entry, Mapping):
                text = str(
                    entry.get("question") or entry.get("unknown") or entry.get("text") or entry
                ).strip()
            else:
                text = str(entry).strip()
            if text:
                needs_decision.append(text)
        for requirement_id in plan.get("unknown_requirement_ids") or []:
            needs_decision.append(f"需求 {requirement_id} 归属未决")
    for finding in getattr(review, "findings", ()) or ():
        if not getattr(finding, "active", False):
            continue
        claim = str(getattr(finding, "claim", "") or "").strip()
        severity = str(getattr(finding, "severity", "") or "")
        if claim:
            needs_decision.append(f"[{severity}] {claim[:160]}")
    for item in getattr(review, "task_items", ()) or ():
        for signal in getattr(item, "acceptance_signals", ()) or ():
            text = str(signal)
            if "待实测" in text or "UNKNOWN" in text:
                pending_measure.append(
                    f"{getattr(item, 'item_id', '')}: {text.strip()[:160]}"
                )
    out = ["**需拍板**：", ""]
    if needs_decision:
        out.extend(f"- {entry}" for entry in needs_decision[:12])
    else:
        out.append("- （无）")
    out += ["", "**待实测**（实施完成后回填）：", ""]
    if pending_measure:
        out.extend(f"- {entry}" for entry in pending_measure[:12])
    else:
        out.append("- （无）")
    out.append("")
    return out


def _risks_and_tradeoffs(context: WorkflowContext) -> list[str]:
    review = context.review
    plan = getattr(review, "plan", None) or {}
    risks = plan.get("risks") if isinstance(plan, Mapping) else None
    out = ["**风险**：", ""]
    if risks:
        for entry in risks:
            if isinstance(entry, Mapping):
                text = str(
                    entry.get("risk") or entry.get("description") or entry
                ).strip()
            else:
                text = str(entry).strip()
            if text:
                out.append(f"- {text}")
    else:
        out.append("- （无）")
    out += ["", "**关键取舍**：", ""]
    out.append("- （无记录；拆解依据见各组需求文档）")
    out.append("")
    return out


def _handoff() -> list[str]:
    return [
        "交门下省时带：本方案全文、analyst 证据包、各组需求文档约束条款（原样带入，实施不得越界）。",
        "",
        "---",
        "*审阅规则：仅回复「通过」移交门下省；其他非空回复交 LLM 修订后再次送审。*",
    ]


def render_plan_review_doc(context: WorkflowContext) -> str:
    """Render the thin plan-review document from the workflow state."""

    out: list[str] = []
    out.extend(_header(context))
    out += ["## 1. 背景", ""]
    out.extend(_grouped_sections(context, (1,)))
    out += ["## 2. 目标", ""]
    out.extend(_grouped_sections(context, (2,)))
    out += ["## 3. 边界", ""]
    out.extend(_grouped_sections(context, (3,)))
    out += ["## 4. 约束（含口径定义）", ""]
    out.extend(_grouped_sections(context, (4, 7, 9)))
    out += ["## 5. 任务拆解", ""]
    out.extend(_task_table(context))
    out += ["## 6. 验收（审阅时逐条打勾）", ""]
    out.extend(_acceptance_checklist(context))
    out += ["## 7. 开放问题与待决事项", ""]
    out.extend(_open_questions(context))
    out += ["## 8. 风险与关键取舍", ""]
    out.extend(_risks_and_tradeoffs(context))
    out += ["## 9. 交接清单", ""]
    out.extend(_handoff())
    return "\n".join(out).rstrip() + "\n"


def render_plan_review_packet(context: WorkflowContext, *, fresh: bool = False) -> str:
    """The single canonical human/Menxia document; never reword group bodies."""
    review = context.review
    snapshot = review.plan_review if review else {}
    if not fresh and snapshot.get("markdown"):
        return str(snapshot["markdown"])
    version = int(snapshot.get("version") or 0) + 1
    from .zhongshu_parallel import canonical_plan_hash
    plan_hash = canonical_plan_hash(review.plan or {}) if review else ""
    parts = [
        f"# 中书省需求文档 [v{version}]\n\n",
        f"任务：{context.identity.task_id}\n\n",
        f"原始请求：{context.request.raw_request}\n\n",
        f"任务图哈希：{plan_hash}\n\n",
        "审阅规则：仅回复「通过」交门下省；其他非空回复交 LLM 修订，再送审。\n\n",
        "## 任务、分组与依赖\n\n",
        "\n".join(_task_table(context)) + "\n\n",
    ]
    if snapshot.get("revision_summary"):
        parts.append("## 本轮修订说明\n\n" + str(snapshot["revision_summary"]) + "\n\n")
    parts.append("## 分组需求正文\n")
    for row in _group_rows(context):
        parts.append(
            f"\n### {row.group_id}（版本 {row.doc_version}）\n"
            f"文档哈希：{getattr(row, 'doc_hash', '')}\n\n"
            f"<!-- requirement-group:{row.group_id}:begin -->\n"
        )
        parts.append(row.doc_markdown)
        parts.append(f"\n<!-- requirement-group:{row.group_id}:end -->\n")
    return "".join(parts)


def plan_review_doc_effect(
    task_id: str,
    sequence: int,
    content: str,
    *, version: int = 0,
) -> EffectRequest:
    """Sink effect: persist the plan-review document for the human gate."""

    return EffectRequest(
        effect_id=f"plan-review-doc:{task_id}:{sequence + 1}",
        effect_type="plan_review_doc",
        task_id=task_id,
        idempotency_key=f"{task_id}:plan-review-doc:{sequence + 1}",
        payload={"content": content, "sequence": sequence, "version": version},
    )
