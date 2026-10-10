"""One blocking plan policy and the authority boundary for regrouping.

Coverage findings remain advisory. Group topology repair is deliberately
narrower than a new plan: tasks, dependencies, and requirement text are fixed.
"""
from __future__ import annotations

from collections.abc import Mapping

from ...graph_validation import structural_gate

BLOCKING_STRUCTURE_PREFIXES = (
    "PLAN_NOT_OBJECT", "PLAN_HAS_NO_ITEMS", "ITEM_IN_MULTIPLE_GROUPS",
    "ITEM_WITHOUT_GROUP", "DEPENDENCY_UNKNOWN_ITEM", "DEPENDENCY_CYCLE",
    "GROUP_DEPENDENCY_CYCLE", "GROUP_ID_INVALID", "GROUP_MAX_ITEMS",
    "ACCEPTANCE_SIGNAL_UNVERIFIABLE",
)
GROUP_REPAIR_PREFIXES = (
    "GROUP_MAX_ITEMS:", "GROUP_DEPENDENCY_CYCLE:", "GROUP_ID_INVALID:", "GROUP_MEMBERSHIP_MISMATCH:",
)


def blocking_structure_errors(plan: object) -> list[str]:
    if not isinstance(plan, Mapping):
        return ["PLAN_NOT_OBJECT"]
    errors = [issue for issue in structural_gate(plan) if issue.startswith(BLOCKING_STRUCTURE_PREFIXES)]
    item_ids = {str(item.get("item_id")) for item in plan.get("items") or []
                if isinstance(item, Mapping) and item.get("item_id")}
    owners = {}
    for group in plan.get("groups") or []:
        if isinstance(group, Mapping):
            for item_id in group.get("item_ids") or []:
                owners[str(item_id)] = str(group.get("group_id") or "")
                if str(item_id) not in item_ids:
                    errors.append(f"GROUP_UNKNOWN_ITEM:{group.get('group_id') or ''}->{item_id}")
    for item in plan.get("items") or []:
        if (plan.get("groups") and isinstance(item, Mapping) and item.get("group_id")
                and str(item.get("group_id")) != owners.get(str(item.get("item_id")))):
            errors.append(f"GROUP_MEMBERSHIP_MISMATCH:{item.get('item_id')}")
    return list(dict.fromkeys(errors))


def group_repair_errors(plan: object) -> list[str]:
    # Analyst evidence packets occupy the legacy plan slot without a graph.
    if not isinstance(plan, Mapping) or not plan.get("items"):
        return []
    return [issue for issue in blocking_structure_errors(plan) if issue.startswith(GROUP_REPAIR_PREFIXES)]


def plan_group_map(plan: Mapping) -> dict[str, dict]:
    return {str(g.get("group_id") or ""): dict(g) for g in plan.get("groups") or [] if isinstance(g, Mapping)}


def regrouping_boundary_error(current: Mapping, candidate: Mapping, frozen_groups=()) -> str:
    """A full transport reply does not confer full business rewrite authority."""
    def content(plan):
        return {str(i.get("item_id") or ""): {k: v for k, v in i.items() if k != "group_id"}
                for i in plan.get("items") or [] if isinstance(i, Mapping)}

    old, new = content(current), content(candidate)
    if (set(old) != set(new) or len(new) != len(candidate.get("items") or [])
            or len(old) != len(current.get("items") or [])):
        return "SOLVER_STRUCTURE_REPAIR_FROZEN:item_ids"
    for iid in sorted(old):
        if old[iid] != new[iid]:
            return f"SOLVER_STRUCTURE_REPAIR_FROZEN:item:{iid}"
    if ({k: v for k, v in current.items() if k not in {"items", "groups"}}
            != {k: v for k, v in candidate.items() if k not in {"items", "groups"}}):
        return "SOLVER_STRUCTURE_REPAIR_FROZEN:plan_fields"
    old_groups, new_groups = plan_group_map(current), plan_group_map(candidate)
    if len(new_groups) != len(candidate.get("groups") or []):
        return "SOLVER_STRUCTURE_REPAIR_FROZEN:duplicate_group_ids"
    for gid in frozen_groups:
        if old_groups.get(gid) != new_groups.get(gid):
            return f"SOLVER_STRUCTURE_REPAIR_FROZEN:group:{gid}"
        members = set(old_groups.get(gid, {}).get("item_ids") or [])
        if any(i.get("item_id") in members and i.get("group_id") not in {None, "", gid}
               for i in candidate.get("items") or []):
            return f"SOLVER_STRUCTURE_REPAIR_FROZEN:group:{gid}"
    return ""
