"""Identity and revision preparation for the one human-approved document."""
from __future__ import annotations

from dataclasses import replace
import hashlib

from .context import ReviewState, ZhongshuGroupState


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def graph_hash(review: ReviewState) -> str:
    if review.plan_revision:
        return review.plan_revision.content_hash
    from ..zhongshu_parallel import canonical_plan_hash
    return canonical_plan_hash(review.plan or {})


def group_hashes(review: ReviewState) -> dict[str, str]:
    return {row.group_id: text_hash(row.doc_markdown) for row in review.zhongshu_groups}


def new_snapshot(context) -> dict[str, object]:
    from ..zhongshu_plan_doc import render_plan_review_packet
    previous = context.review.plan_review
    markdown = render_plan_review_packet(context, fresh=True)
    return {**previous, "markdown": markdown, "hash": text_hash(markdown),
            "plan_revision_ref": context.review.plan_revision.reference if context.review.plan_revision else "",
            "version": int(previous.get("version") or 0) + 1,
            "plan_hash": graph_hash(context.review), "group_hashes": group_hashes(context.review),
            "approved_hash": "", "pending_feedback": []}


def matches_current(review: ReviewState, offered_hash: str = "") -> bool:
    packet = review.plan_review
    markdown = str(packet.get("markdown") or "")
    return bool(markdown and not packet.get("pending_feedback")
                and (not review.plan_revision or packet.get("plan_revision_ref") == review.plan_revision.reference)
                and text_hash(markdown) == packet.get("hash")
                and (not offered_hash or offered_hash == packet.get("hash"))
                and graph_hash(review) == packet.get("plan_hash")
                and group_hashes(review) == packet.get("group_hashes"))


def group_chapter(markdown: str, group_id: str) -> str:
    """Extract exact approved chapter; legacy single-group documents pass through."""
    if "<!-- requirement-group:" not in markdown:
        return markdown
    begin = f"<!-- requirement-group:{group_id}:begin -->\n"
    end = f"\n<!-- requirement-group:{group_id}:end -->"
    if markdown.count(begin) != 1 or markdown.count(end) != 1:
        raise ValueError("PLAN_REVIEW_GROUP_CHAPTER_MISSING:" + group_id)
    return markdown.split(begin, 1)[1].split(end, 1)[0]


def prepare_human_revision(review: ReviewState, messages: list[dict], decision_id: str,
                           message_id: str = "") -> ReviewState:
    """Open the full planning scope; retain document history and exact feedback."""
    packet = dict(review.plan_review)
    history = list(packet.get("feedback_history") or [])
    seen = {entry.get("external_id") for entry in history if entry.get("external_id")}
    fresh = []
    for message in messages:
        identity = message.get("external_id")
        if identity and identity in seen:
            continue
        if identity:
            seen.add(identity)
        fresh.append({**message, "decision_id": message.get("decision_id") or decision_id,
                      "document_version": message.get("document_version", packet.get("version", 0)),
                      "document_hash": message.get("document_hash", packet.get("hash", ""))})
    anchors = list(packet.get("feedback_gates") or [])
    if decision_id and not any(entry["decision_id"] == decision_id for entry in anchors):
        anchors.append({"decision_id": decision_id, "message_id": message_id,
                        "document_version": packet.get("version", 0), "document_hash": packet.get("hash", "")})
    packet["feedback_gates"] = anchors
    packet.update(approved_hash="", pending_feedback=fresh, feedback_history=history + fresh)
    rows = tuple(ZhongshuGroupState(
        group_id=row.group_id, doc_version=row.doc_version, doc_hash=row.doc_hash,
        doc_markdown=row.doc_markdown, doc_source_hash=row.doc_source_hash,
    ) for row in review.zhongshu_groups)
    return replace(review, plan_review=packet, zhongshu_groups=rows,
                   task_review_ledger=(), item_workflows=(), menxia_groups=(), menxia_items=(),
                   salvaged_task_reviews=(), salvaged_worker_payloads=(),
                   zhongshu_revision_round=0, item_revision_round=0, freeze_check_attempt=0,
                   attempted_item_ids=(), attempted_finding_ids=())
