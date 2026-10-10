"""Compile drafts and commit against an explicit revision, never against Markdown."""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
import re

from .model import PlanRevision, canonical_json, content_hash
from ..policies.plan_structure import blocking_structure_errors


class PlanCompileError(ValueError):
    pass


def compile_plan(draft, *, approved_requirements=None):
    if not isinstance(draft, Mapping):
        raise PlanCompileError("SOLVER_PLAN_STRUCTURE_INVALID:plan_object")
    try:
        plan = json.loads(canonical_json(dict(draft)))
    except (TypeError, ValueError) as exc:
        raise PlanCompileError("SOLVER_PLAN_STRUCTURE_INVALID:json_value") from exc
    for key, id_key in (("items", "item_id"), ("groups", "group_id")):
        rows = plan.get(key)
        if not isinstance(rows, list) or not rows:
            raise PlanCompileError(f"SOLVER_PLAN_STRUCTURE_INVALID:{key}_array")
        ids = []
        for row in rows:
            identity = row.get(id_key) if isinstance(row, dict) else None
            if not isinstance(identity, str) or not identity.strip() or identity != identity.strip():
                raise PlanCompileError(f"SOLVER_PLAN_STRUCTURE_INVALID:{id_key}")
            ids.append(identity)
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", identity):
                raise PlanCompileError(f"SOLVER_PLAN_STRUCTURE_INVALID:{id_key}_format")
        if len(set(ids)) != len(ids):
            raise PlanCompileError(f"SOLVER_PLAN_STRUCTURE_INVALID:duplicate_{id_key}")
    owners = {}
    for group in plan["groups"]:
        ids = group.get("item_ids")
        if (not isinstance(ids, list) or not ids or any(not isinstance(i, str) for i in ids)
                or len(set(ids)) != len(ids)):
            raise PlanCompileError("SOLVER_PLAN_STRUCTURE_INVALID:group_item_ids")
        for iid in ids:
            if iid in owners:
                raise PlanCompileError("SOLVER_PLAN_STRUCTURE_INVALID:multiple_owners")
            owners[iid] = group["group_id"]
    requirements = plan.get("requirements")
    if not isinstance(requirements, list) or any(not isinstance(r, dict) for r in requirements):
        raise PlanCompileError("SOLVER_PLAN_STRUCTURE_INVALID:requirements_array")
    requirement_ids = [r.get("requirement_id") for r in requirements]
    if (not requirement_ids or any(not isinstance(i, str) or not i for i in requirement_ids)
            or len(set(requirement_ids)) != len(requirement_ids)):
        raise PlanCompileError("SOLVER_PLAN_STRUCTURE_INVALID:requirement_ids")
    for item in plan["items"]:
        iid = item["item_id"]
        for key in ("title", "objective"):
            if not isinstance(item.get(key), str) or not item[key].strip():
                raise PlanCompileError(f"SOLVER_PLAN_STRUCTURE_INVALID:{key}:{iid}")
        if iid not in owners or item.get("group_id") not in (None, "", owners[iid]):
            raise PlanCompileError(f"SOLVER_PLAN_STRUCTURE_INVALID:item_owner:{iid}")
        item["group_id"] = owners[iid]
        for key in ("acceptance_signals", "source_requirement_ids", "dependencies"):
            values = item.get(key, [] if key == "dependencies" else None)
            if (not isinstance(values, list) or any(not isinstance(v, str) or not v.strip() for v in values)
                    or (key != "dependencies" and not values)):
                raise PlanCompileError(f"SOLVER_PLAN_STRUCTURE_INVALID:{key}:{iid}")
        if not set(item["source_requirement_ids"]).issubset(requirement_ids):
            raise PlanCompileError(f"SOLVER_PLAN_STRUCTURE_INVALID:unknown_requirement:{iid}")
        if any("\n" in s or "\r" in s or len(s) > 298 or s.lstrip().startswith(("#", "```"))
               for s in item["acceptance_signals"]):
            raise PlanCompileError(f"SOLVER_PLAN_STRUCTURE_INVALID:acceptance_line:{iid}")
    errors = blocking_structure_errors(plan)
    if approved_requirements is not None:
        if len(plan['groups']) != 1:
            raise PlanCompileError('REQUIREMENTS_IMPLEMENTATION_REQUIRES_ONE_GROUP')
        errors = [e for e in errors if not e.startswith('GROUP_MAX_ITEMS')]
        baseline = {c.requirement_id for c in approved_requirements.claims}
        if set(requirement_ids) != baseline:
            raise PlanCompileError('APPROVED_REQUIREMENT_BASELINE_MISMATCH')
        covered = {rid for item in plan['items'] for rid in item['source_requirement_ids']}
        if covered != baseline:
            raise PlanCompileError('APPROVED_REQUIREMENT_COVERAGE_INCOMPLETE')
    if errors:
        raise PlanCompileError("SOLVER_PLAN_STRUCTURE_INVALID:" + ";".join(errors[:10]))
    return plan


def commit_plan(current, draft, *, expected_ref, requirements=None, frozen_group_ids=(), approved_requirements=None):
    expected = current.reference if current else ""
    if expected_ref != expected:
        raise PlanCompileError("SOLVER_PLAN_VERSION_CONFLICT:expected_base")
    plan = compile_plan(draft, approved_requirements=approved_requirements)
    if requirements is None and current:
        requirements = current.plan["requirements"]
    if requirements is not None and canonical_json(plan["requirements"]) != canonical_json(list(requirements)):
        raise PlanCompileError("SOLVER_PLAN_STRUCTURE_INVALID:requirement_baseline")
    if current:
        old = current.plan
        for gid in frozen_group_ids:
            old_group = next((g for g in old["groups"] if g["group_id"] == gid), None)
            new_group = next((g for g in plan["groups"] if g["group_id"] == gid), None)
            members = set(old_group["item_ids"]) if old_group else set()
            if (old_group != new_group or [i for i in old["items"] if i["item_id"] in members]
                    != [i for i in plan["items"] if i["item_id"] in members]):
                raise PlanCompileError(f"SOLVER_PLAN_STRUCTURE_INVALID:frozen_group:{gid}")
            from .render import render_document
            if old_group and render_document(old, old_group) != render_document(plan, new_group):
                raise PlanCompileError(f"SOLVER_PLAN_STRUCTURE_INVALID:frozen_group_source:{gid}")
        if canonical_json(plan) == current.plan_json:
            return current
    return PlanRevision((current.number + 1) if current else 1, expected,
                        content_hash(plan), canonical_json(plan))


def project_tasks(plan, previous=()):
    from ..context import ReviewTaskItem, ReviewTaskGroup
    statuses = {i.item_id: i.status for i in previous}
    groups = tuple(ReviewTaskGroup(group_id=g["group_id"], item_ids=tuple(g["item_ids"])) for g in plan["groups"])
    items = tuple(ReviewTaskItem(item_id=i["item_id"], group_id=i["group_id"],
                  dependencies=tuple(sorted(set(i.get("dependencies", [])))), order=n,
                  status=statuses.get(i["item_id"], "PENDING"), title=str(i.get("title", "")),
                  objective=str(i.get("objective", "")), source_requirement_ids=tuple(i["source_requirement_ids"]),
                  acceptance_signals=tuple(i["acceptance_signals"])) for n, i in enumerate(plan["items"]))
    return items, groups


def revision_scope_error(base, candidate, editable_items, editable_groups):
    """Ordinary findings do not authorize regrouping or changing global scope."""
    if canonical_json({k: v for k, v in base.items() if k not in ("items", "groups")}) != canonical_json({
            k: v for k, v in candidate.items() if k not in ("items", "groups")}):
        return "SOLVER_ITEM_FROZEN:plan_fields"
    old_items = {i["item_id"]: i for i in base["items"]}
    new_items = {i["item_id"]: i for i in candidate["items"]}
    if list(old_items) != list(new_items):
        return "SOLVER_ITEM_FROZEN:item_ids"
    for iid, item in new_items.items():
        if item["group_id"] != old_items[iid]["group_id"] or (iid not in editable_items and canonical_json(item) != canonical_json(old_items[iid])):
            return "SOLVER_ITEM_FROZEN:" + iid
    old_groups = {g["group_id"]: g for g in base["groups"]}
    new_groups = {g["group_id"]: g for g in candidate["groups"]}
    if list(old_groups) != list(new_groups):
        return "SOLVER_ITEM_FROZEN:group_ids"
    for gid, group in new_groups.items():
        if group["item_ids"] != old_groups[gid]["item_ids"] or (gid not in editable_groups and canonical_json(group) != canonical_json(old_groups[gid])):
            return "SOLVER_ITEM_FROZEN:" + gid
    return ""


def validate_projection(review):
    if review.plan_revision is None:
        if isinstance(review.plan, Mapping) and "items" in review.plan:
            raise PlanCompileError("canonical formal plan requires PlanRevision")
        return
    revision = review.plan_revision
    PlanRevision.from_dict(revision.to_dict(),approved_requirements=review.approved_requirements_input)
    if content_hash(review.plan) != revision.content_hash or review.plan_hash != revision.content_hash:
        raise PlanCompileError("canonical plan mirror mismatch")
    items, groups = project_tasks(revision.plan, review.task_items)
    if items != review.task_items or groups != review.task_groups or list(review.requirements) != revision.plan["requirements"]:
        raise PlanCompileError("canonical task projection mismatch")
    if review.approved_requirements_input is not None:
        compile_plan(revision.plan, approved_requirements=review.approved_requirements_input)
        if review.zhongshu_groups:
            raise PlanCompileError('APPROVED_INPUT_CANNOT_HAVE_ZHONGSHU_GROUPS')
        if any(row.requirement_markdown != review.approved_requirements_input.markdown for row in review.menxia_groups):
            raise PlanCompileError('MENXIA_APPROVED_REQUIREMENTS_CHANGED')
        return
    from .render import render_document
    from ..document_utils import text_hash
    by_group = {r.group_id: r for r in review.zhongshu_groups}
    if len(by_group) != len(review.zhongshu_groups) or set(by_group) != {g["group_id"] for g in revision.plan["groups"]}:
        raise PlanCompileError("canonical document group mismatch")
    for group in revision.plan["groups"]:
        row = by_group[group["group_id"]]
        markdown, source = render_document(revision.plan, group, row.doc_version)
        if row.doc_version < 1 or row.doc_markdown != markdown or row.doc_source_hash != source or row.doc_hash != text_hash(markdown):
            raise PlanCompileError("canonical document mirror mismatch")


def prepare_update(current, update):
    """Single business write boundary; operational statuses remain independent."""
    from ..context import ReviewState
    from .render import render_group_rows
    current = current or ReviewState(revision_id=update.revision_id or "", plan_schema="canonical-v1")
    approved = update.approved_requirements_input or current.approved_requirements_input
    if current.approved_requirements_input is not None and approved != current.approved_requirements_input:
        raise PlanCompileError('APPROVED_INPUT_IMMUTABLE')
    validate_projection(current)
    revision = current.plan_revision
    if update.plan is not None and "items" in update.plan:
        baseline = update.requirements if update.human_revision_validated else current.requirements
        revision = commit_plan(revision, update.plan, expected_ref=update.plan_base_ref,
                               requirements=baseline or None,
                               frozen_group_ids=[r.group_id for r in current.zhongshu_groups if r.stage == "FROZEN"],
                               approved_requirements=approved)
        items, groups = project_tasks(revision.plan, update.task_items or current.task_items)
        rows = () if approved is not None else render_group_rows(revision.plan, current.zhongshu_groups,
                                 update.zhongshu_groups if update.zhongshu_groups is not None else current.zhongshu_groups,
                                 validated_stage=update.human_revision_validated)
        update = replace(update, plan=revision.plan, plan_hash=revision.content_hash,
                         task_items=items, task_groups=groups, requirements=tuple(revision.plan["requirements"]),
                         zhongshu_groups=rows)
    elif revision:
        if update.task_items is not None:
            items, _ = project_tasks(revision.plan, update.task_items)
            if items != update.task_items:
                raise PlanCompileError("canonical projection write rejected")
        if update.task_groups is not None and update.task_groups != current.task_groups:
            raise PlanCompileError("canonical group projection write rejected")
        if update.requirements is not None and update.requirements != current.requirements:
            raise PlanCompileError("canonical requirement write requires plan commit")
        if update.plan_hash is not None and update.plan_hash != revision.content_hash:
            raise PlanCompileError("canonical hash write rejected")
        # An Analyst packet is evidence, not a new formal plan.
        update = replace(update, plan=revision.plan, plan_hash=revision.content_hash,
                         task_groups=current.task_groups,
                         task_items=update.task_items if update.task_items is not None else current.task_items)
        if update.zhongshu_groups is not None:
            update = replace(update, zhongshu_groups=render_group_rows(revision.plan, current.zhongshu_groups, update.zhongshu_groups))
    return update, revision
