"""Item-scoped Solver revision (A2/P3).

When the item workflow is enabled, a revision edge dispatches one Solver
worker per contested item instead of one single-writer revision.  Each worker
sees exactly one item's ingredients (task, findings, evidence, standard) and
returns an item patch; this module derives the contested set, assembles the
ingredients, and materializes/merges the patches deterministically.  The merge
is the "chef's consistency check": item identity is immutable, patches may not
touch other items, and the merged plan is re-hashed by the caller.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

# Fields an item worker may rewrite.  item_id/group_id are identity and stay
# fixed; everything else that defines the task capsule is patchable.
_PATCHABLE_ITEM_FIELDS = (
    "title",
    "objective",
    "dependencies",
    "acceptance_signals",
    "source_requirement_ids",
    "unknowns",
    "risks",
    "parallelizable",
    "rationale",
    "benefit",
    "tradeoffs",
)


def contested_item_ids(ledger: Iterable[object]) -> tuple[str, ...]:
    """Items whose latest Critic verdict is not an approval, in stable order."""

    contested: list[str] = []
    for record in ledger or ():
        item_id = str(getattr(record, "item_id", "") or "").strip()
        status = str(getattr(record, "status", "") or "").strip().upper()
        if item_id and status != "APPROVED":
            contested.append(item_id)
    return tuple(dict.fromkeys(contested))


def item_findings(
    findings: Iterable[object], item_id: str
) -> tuple[dict[str, Any], ...]:
    """Active findings owned by one item, as payload dicts."""

    payload: list[dict[str, Any]] = []
    for finding in findings or ():
        if not getattr(finding, "active", False):
            continue
        if str(getattr(finding, "item_id", "") or "").strip() != item_id:
            continue
        to_dict = getattr(finding, "to_dict", None)
        payload.append(dict(to_dict()) if callable(to_dict) else dict(finding))
    return tuple(payload)


def plan_item(plan: Mapping[str, Any] | None, item_id: str) -> dict[str, Any] | None:
    """The current item object inside the plan, or ``None``."""

    if not isinstance(plan, Mapping):
        return None
    for item in plan.get("items") or ():
        if isinstance(item, Mapping) and str(item.get("item_id") or "") == item_id:
            return dict(item)
    return None


@dataclass(frozen=True)
class ItemPatch:
    """One worker's item patch, already validated against its item id."""

    item_id: str
    item: dict[str, Any]
    finding_resolutions: tuple[dict[str, Any], ...]


def materialize_item_patch(
    reply: Mapping[str, Any],
    item_id: str,
) -> tuple[ItemPatch | None, str]:
    """Validate one item worker reply into an :class:`ItemPatch`.

    Errors are reply-shape failures (``ITEM_PATCH_*``) charged to the reply
    budget, not content disagreements.
    """

    if str(reply.get("item_id") or "").strip() != item_id:
        return None, f"ITEM_PATCH_IDENTITY:expected {item_id}"
    raw_item = reply.get("item")
    if not isinstance(raw_item, Mapping):
        return None, "ITEM_PATCH_MISSING:reply must carry the patched item object"
    patched_item = dict(raw_item)
    if str(patched_item.get("item_id") or "").strip() != item_id:
        return None, f"ITEM_PATCH_IDENTITY:item.item_id must stay {item_id}"
    group_id = str(patched_item.get("group_id") or "").strip()
    if not group_id:
        return None, "ITEM_PATCH_GROUP_MISSING:item.group_id is required"
    unknown_fields = sorted(set(patched_item) - set(_PATCHABLE_ITEM_FIELDS) - {"item_id", "group_id"})
    if unknown_fields:
        return None, (
            "ITEM_PATCH_FIELD_FORBIDDEN:" + ",".join(unknown_fields[:8])
        )
    resolutions = tuple(
        dict(entry)
        for entry in (reply.get("finding_resolutions") or ())
        if isinstance(entry, Mapping)
    )
    return ItemPatch(item_id=item_id, item=patched_item, finding_resolutions=resolutions), ""


def merge_item_patches(
    plan: Mapping[str, Any] | None,
    patches: Iterable[ItemPatch],
) -> tuple[dict[str, Any] | None, str]:
    """Apply per-item patches onto a copy of the plan.

    Each contested item is patched by exactly one worker; items without a
    patch pass through unchanged.  The merged plan is a fresh deep copy — the
    orchestrator owns the graph, workers never mutate shared state.
    """

    if not isinstance(plan, Mapping):
        return None, "ITEM_MERGE_NO_PLAN"
    patch_list = list(patches)
    if not patch_list:
        return None, "ITEM_MERGE_NO_PATCHES"
    seen: set[str] = set()
    for patch in patch_list:
        if patch.item_id in seen:
            return None, f"ITEM_MERGE_DUPLICATE:{patch.item_id}"
        seen.add(patch.item_id)
    merged = deepcopy(dict(plan))
    items = merged.get("items")
    if not isinstance(items, list):
        return None, "ITEM_MERGE_NO_ITEMS"
    by_id = {patch.item_id: patch for patch in patch_list}
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            continue
        item_id = str(item.get("item_id") or "")
        patch = by_id.pop(item_id, None)
        if patch is not None:
            items[index] = deepcopy(patch.item)
    if by_id:
        missing = ",".join(sorted(by_id))
        return None, f"ITEM_MERGE_UNKNOWN_ITEM:{missing}"
    return merged, ""


__all__ = [
    "ItemPatch",
    "contested_item_ids",
    "item_findings",
    "materialize_item_patch",
    "merge_item_patches",
    "plan_item",
]
