"""Deterministic group documents, with approval invalidation local to changed sources."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json

from .model import content_hash
from ..zhongshu_doc import SECTION_TITLES


def render_document(plan, group, version=1):
    members = [i for i in plan["items"] if i["item_id"] in group["item_ids"]]
    source_ids = {rid for i in members for rid in i["source_requirement_ids"]}
    requirements = [r for r in plan["requirements"] if r["requirement_id"] in source_ids]
    metadata = {k: v for k, v in plan.items() if k not in ("items", "groups", "requirements")}
    # All business fields survive into the review document, including optional
    # steps, inputs, outputs and risks; the renderer does not invent them.
    detail = json.dumps(members, ensure_ascii=False, sort_keys=True, indent=2)
    bodies = [
        json.dumps(requirements, ensure_ascii=False, sort_keys=True, indent=2),
        "\n".join(f"- {i['item_id']}: " + json.dumps(i.get('objective') or i.get('title') or i['item_id'], ensure_ascii=False) for i in members),
        json.dumps(dict(group=group, plan_metadata=metadata), ensure_ascii=False, sort_keys=True, indent=2),
        json.dumps(group.get("boundary") or "范围由本组成员及依赖关系确定。", ensure_ascii=False),
        detail,
        json.dumps(group.get("responsibilities") or "责任范围为本组列出的任务；未定义的责任需在结构化方案中补充。", ensure_ascii=False),
        "\n".join(f"- {i['item_id']} dependencies: {', '.join(i.get('dependencies', [])) or '[]'}" for i in members),
        "\n\n".join(f"### {i['item_id']}\n" + "\n".join(i["acceptance_signals"]) for i in members),
        json.dumps(group.get("non_goals") or "结构化方案未另行声明非目标。", ensure_ascii=False),
    ]
    document = f"# {group['group_id']} 需求文档 [v{version}]\n\n" + "\n\n".join(
        f"## {n}. {title}\n{body}" for n, (title, body) in enumerate(zip(SECTION_TITLES, bodies), 1)) + "\n"
    source_hash = content_hash(dict(group=group, items=members, requirements=requirements, plan_metadata=metadata))
    return document, source_hash


def render_group_rows(plan, current=(), proposed=(), *, validated_stage=False):
    from ..context import ZhongshuGroupState
    old = {r.group_id: r for r in current}
    incoming = {r.group_id: r for r in proposed}
    rows = []
    for group in plan["groups"]:
        gid = group["group_id"]
        prior = old.get(gid)
        markdown, source = render_document(plan, group, prior.doc_version if prior else 1)
        digest = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
        candidate = incoming.get(gid, prior) or ZhongshuGroupState(group_id=gid)
        unchanged = bool(prior and prior.doc_source_hash == source and prior.doc_markdown == markdown
                         and prior.doc_hash == digest)
        if unchanged:
            row = replace(candidate, doc_version=prior.doc_version, doc_hash=digest,
                          doc_markdown=markdown, doc_source_hash=source)
        else:
            version = prior.doc_version + 1 if prior else 1
            markdown, source = render_document(plan, group, version)
            digest = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
            row = replace(candidate, stage=candidate.stage if validated_stage else "REVIEWING", approved_surface_hash="",
                          doc_version=version, doc_hash=digest,
                          doc_markdown=markdown, doc_source_hash=source)
        rows.append(row)
    return tuple(rows)
