"""Pure group-level Zhongshu convergence rules (shared requirements model).

The group pipeline mirrors the Menxia group pipeline (``menxia_group.py``)
but one row covers the Zhongshu convergence of a whole review group: the
group converges on one shared versioned requirements document, the revision
budget is group-wide, and the fold tracks the same stall rule (open blocker
count not shrinking for consecutive revision rounds).

The functions here are pure: they take the immutable review projection and
return new group rows, never touching the FSM or dispatch layers.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace

from ..context import ReviewState, ZhongshuGroupState
from .zhongshu import _ACTIVE_STATUSES, _BLOCKING_SEVERITIES, approved_item_ids

# Consecutive revision rounds without a shrinking open-blocker count before
# the group parks as STALLED (mirrors MENXIA_GROUP_STALL_LIMIT).
ZHONGSHU_GROUP_STALL_LIMIT = 2

# Terminal-ish stages that never consume revision budget again.
_ZHONGSHU_GROUP_TERMINAL_STAGES = frozenset({"CONVERGED", "FROZEN"})

# Stages the drainout check treats as parked for a human.
_ZHONGSHU_GROUP_PARKED_STAGES = frozenset({"STALLED", "BLOCKED"})


def group_item_ids(review: ReviewState, group_id: str) -> tuple[str, ...]:
    """Return the task item ids belonging to ``group_id`` (projection order)."""

    return tuple(
        item.item_id
        for item in review.task_items
        if item.group_id == group_id
    )


def group_of_item(review: ReviewState, item_id: str) -> str:
    """Return the group id owning ``item_id`` (empty when unknown)."""

    for item in review.task_items:
        if item.item_id == item_id:
            return item.group_id
    return ""


def group_dependencies(review: ReviewState, group_id: str) -> tuple[str, ...]:
    """Return the groups ``group_id`` transitively starts from.

    Composed from the member items' dependency endpoints mapped back to
    their owning groups; self-references and unknown endpoints are dropped
    so a group can never block on itself.
    """

    group_by_item = {item.item_id: item.group_id for item in review.task_items}
    deps = {
        group_by_item.get(dependency, "")
        for item in review.task_items
        if item.group_id == group_id
        for dependency in item.dependencies
    }
    deps.discard("")
    deps.discard(group_id)
    return tuple(sorted(deps))


def group_open_blockers(review: ReviewState, group_id: str) -> tuple[object, ...]:
    """Return the open P0/P1 findings attached to the group's member items."""

    members = set(group_item_ids(review, group_id))
    return tuple(
        finding
        for finding in review.findings
        if str(getattr(finding, "item_id", "") or "") in members
        and str(getattr(finding, "status", "") or "").strip().upper()
        in _ACTIVE_STATUSES
        and str(getattr(finding, "severity", "") or "").strip().upper()
        in _BLOCKING_SEVERITIES
    )


def zhongshu_group_converged(review: ReviewState, group_id: str) -> bool:
    """True when every member item is ledger-approved with no open blocker.

    The ledger folds every round's verdict and approval is a ratchet, so a
    group that once converged cannot silently regress.
    """

    members = set(group_item_ids(review, group_id))
    if not members:
        return False
    approved = set(approved_item_ids(review.task_review_ledger))
    return members.issubset(approved) and not group_open_blockers(review, group_id)


def apply_zhongshu_round(
    review: ReviewState,
    *,
    attempted_item_ids: Iterable[str],
    max_rounds: int,
) -> tuple[ZhongshuGroupState, ...]:
    """Fold one Zhongshu round into the group pipeline rows.

    The round's scope is the set of groups owning ``attempted_item_ids``.
    Groups that already converged (recomputed here from the ledger ratchet
    and open blockers) move to ``CONVERGED`` without consuming budget —
    whether they are in scope or not.  In-scope, non-terminal groups bump
    their group-wide ``revision_round``; the stall rule mirrors
    ``menxia_group.py``: the first revision round only seeds the baseline,
    afterwards an open-blocker count that fails to shrink accumulates
    ``stalled_rounds``, and reaching the limit or ``max_rounds`` parks the
    group as ``STALLED``.  Out-of-scope groups never consume budget and
    never have their stall counters cleared.

    The returned rows follow ``seed_zhongshu_groups()`` order for
    deterministic snapshots.
    """

    rows = {row.group_id: row for row in review.seed_zhongshu_groups()}
    scope = {
        group_of_item(review, str(item_id))
        for item_id in (attempted_item_ids or ())
    }
    scope.discard("")
    for group_id, row in rows.items():
        if zhongshu_group_converged(review, group_id):
            if row.stage != "FROZEN":
                rows[group_id] = replace(row, stage="CONVERGED")
            continue
        if group_id not in scope or row.stage in _ZHONGSHU_GROUP_TERMINAL_STAGES:
            continue
        open_count = len(group_open_blockers(review, group_id))
        revision_round = row.revision_round + 1
        if row.revision_round > 0 and open_count >= row.last_open_count:
            stalled_rounds = row.stalled_rounds + 1
        else:
            stalled_rounds = 0
        if (
            0 < max_rounds <= revision_round
            or stalled_rounds >= ZHONGSHU_GROUP_STALL_LIMIT
        ):
            stage = "STALLED"
        else:
            stage = "REVIEWING"
        rows[group_id] = replace(
            row,
            stage=stage,
            revision_round=revision_round,
            last_open_count=open_count,
            stalled_rounds=stalled_rounds,
        )
    ordered_ids = [row.group_id for row in review.seed_zhongshu_groups()]
    return tuple(rows[group_id] for group_id in ordered_ids if group_id in rows)


def freeze_ready_groups(review: ReviewState) -> tuple[ZhongshuGroupState, ...]:
    """Return CONVERGED groups whose dependency groups are all FROZEN.

    Groups are ordered by the task projection's group order, so the freeze
    wave releases them in dependency-safe sequence.
    """

    rows = {row.group_id: row for row in review.seed_zhongshu_groups()}
    order = {group.group_id: group.order for group in review.task_groups}
    ready = [
        (order.get(group_id, 1 << 30), group_id)
        for group_id, row in rows.items()
        if row.stage == "CONVERGED"
        and all(
            rows.get(dependency) is not None
            and rows[dependency].stage == "FROZEN"
            for dependency in group_dependencies(review, group_id)
        )
    ]
    ready.sort()
    return tuple(rows[group_id] for _, group_id in ready)


def zhongshu_drainout_parked(review: ReviewState) -> bool:
    """True when every non-frozen group is parked (STALLED or BLOCKED).

    With at least one group row present, this is the drainout condition:
    no group can make progress without a human, so the run must surface
    the blockers instead of looping.
    """

    rows = review.seed_zhongshu_groups()
    if not rows:
        return False
    return all(
        row.stage in _ZHONGSHU_GROUP_PARKED_STAGES or row.stage == "FROZEN"
        for row in rows
    ) and any(row.stage in _ZHONGSHU_GROUP_PARKED_STAGES for row in rows)


def zhongshu_group_resets_from_payload(payload: object) -> tuple[Mapping, ...]:
    """Extract directed unstick entries from a human-gate resume payload.

    The gate reply format mirrors the Menxia ``REQUEST_GROUP_REVISION``
    payload: ``zhongshu_group_resets=[{"group_id": ..., "budget_reset": ...}]``.
    """

    if not isinstance(payload, Mapping):
        return ()
    raw = payload.get("zhongshu_group_resets")
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(item for item in raw if isinstance(item, Mapping))


def apply_zhongshu_group_resets(
    rows: Iterable[ZhongshuGroupState],
    resets: Iterable[object],
) -> tuple[ZhongshuGroupState, ...]:
    """Unstick the named groups with a fresh budget (gate-issued).

    Mirrors the Menxia gate's ``budget_reset``: a listed group un-parks to
    ``REVIEWING`` with round 0 and cleared stall counters, keeping its
    document chain.  FROZEN rows are never resurrected (a frozen document is
    authoritative) and unknown group ids are ignored.  Row order is
    preserved.
    """

    by_id = {row.group_id: row for row in rows or ()}
    for raw in resets or ():
        if not isinstance(raw, Mapping):
            continue
        group_id = str(raw.get("group_id") or "").strip()
        if not group_id or group_id not in by_id:
            continue
        if not bool(raw.get("budget_reset")):
            continue
        row = by_id[group_id]
        if row.stage == "FROZEN":
            continue
        by_id[group_id] = ZhongshuGroupState(
            group_id=row.group_id,
            doc_version=row.doc_version,
            doc_hash=row.doc_hash,
            doc_markdown=row.doc_markdown,
            doc_source_hash=row.doc_source_hash,
        )
    return tuple(by_id.values())


__all__ = [
    "ZHONGSHU_GROUP_STALL_LIMIT",
    "apply_zhongshu_group_resets",
    "apply_zhongshu_round",
    "freeze_ready_groups",
    "group_dependencies",
    "group_item_ids",
    "group_of_item",
    "group_open_blockers",
    "zhongshu_drainout_parked",
    "zhongshu_group_converged",
    "zhongshu_group_resets_from_payload",
]
