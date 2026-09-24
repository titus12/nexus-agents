"""Pure group-level Menxia scheduling and fold rules (shared document model).

The group pipeline mirrors the item pipeline (``menxia.py``) but one row
covers a whole review group: the group's items share one versioned plan
document, so the wave dispatches groups instead of items, the revision
budget is group-wide, and the fold also tracks the suggestion-ledger stall
signals (open count not shrinking, repeated tug-of-war).
"""

from __future__ import annotations

import hashlib

from collections.abc import Iterable, Mapping
from ..context import MenxiaGroupState, MenxiaParallelLimits, ReviewState
from ..errors import InvariantViolation


MENXIA_GROUP_TARGETS = (
    "MENXIA_GROUP_SOLVER",
    "MENXIA_GROUP_ANALYST",
    "MENXIA_GROUP_CRITIC",
)

# The FSM state a group's sub-phase must match for that state's wave to
# dispatch it (stage barrier model, mirroring the item pipeline).
MENXIA_GROUP_STAGE_BY_TARGET = {
    "MENXIA_GROUP_SOLVER": "SOLVING",
    "MENXIA_GROUP_ANALYST": "ANALYZING",
    "MENXIA_GROUP_CRITIC": "REVIEWING",
}

MENXIA_GROUP_ACTIVE_STAGES = ("SOLVING", "ANALYZING", "REVIEWING")
MENXIA_GROUP_TERMINAL_STAGES = ("APPROVED", "BLOCKED", "ESCALATED")

# Contract action → the group's next sub-phase.  ``None`` parks the group in
# its current stage (a HUMAN_GATE verdict pauses the run via the aggregate
# action instead of folding a stage change).
MENXIA_GROUP_STAGE_BY_ACTION = {
    "FEASIBLE": "ANALYZING",
    "READY_FOR_ANALYST": "ANALYZING",
    "READY_FOR_CRITIC": "REVIEWING",
    "EVIDENCE_SUFFICIENT": "REVIEWING",
    "APPROVE_GROUP": "APPROVED",
    "REQUEST_SOLVER_REVISION": "SOLVING",
    "NEEDS_MORE_EVIDENCE": "SOLVING",
    "REQUEST_EVIDENCE": "ANALYZING",
    "BLOCKED": "BLOCKED",
    "HUMAN_GATE": None,
}

# Actions that send a group back to its Solver: each consumes one unit of the
# group-wide revision budget.
MENXIA_GROUP_REVISION_DEMAND_ACTIONS = frozenset(
    {"REQUEST_SOLVER_REVISION", "NEEDS_MORE_EVIDENCE"}
)

# Consecutive revision rounds without a shrinking open-suggestion count
# before the group escalates (design §6 stall rule).
MENXIA_GROUP_STALL_LIMIT = 2

# Fields a group result may carry to update the shared document chain.
MENXIA_GROUP_DOC_FIELDS = ("doc_version", "doc_hash", "doc_ref")


def menxia_group_next_stage(action: str) -> str | None:
    """Map one contract action to the group's next sub-phase (None = park)."""

    return MENXIA_GROUP_STAGE_BY_ACTION.get(str(action or "").strip())


def ready_stage_groups(
    review: ReviewState,
    *,
    target: str,
    limits: MenxiaParallelLimits | None = None,
) -> tuple[MenxiaGroupState, ...]:
    """Pick the groups a menxia group wave dispatches, capped.

    Groups whose stage matches the target are returned ordered by the task
    projection's group order.  With ``limits.enabled`` the selection is
    bounded by ``max_concurrent_groups``; otherwise every ready group is
    returned (groups run concurrently between themselves).
    """

    stage = MENXIA_GROUP_STAGE_BY_TARGET.get(target)
    if stage is None:
        raise InvariantViolation(
            f"state {target!r} is not a menxia group pipeline state"
        )
    rows = {row.group_id: row for row in review.seed_menxia_groups()}
    group_order = {group.group_id: group.order for group in review.task_groups}
    candidates = [
        (group_order.get(group_id, 1 << 30), group_id, row)
        for group_id, row in sorted(rows.items())
        if row.stage == stage
    ]
    candidates.sort()
    if limits is None or not limits.enabled:
        return tuple(row for _, _, row in candidates)
    max_groups = max(1, int(limits.max_concurrent_groups))
    return tuple(row for _, _, row in candidates[:max_groups])


def apply_menxia_group_results(
    review: ReviewState,
    results: Iterable[Mapping[str, object]],
    *,
    max_rounds: int,
) -> tuple[MenxiaGroupState, ...]:
    """Fold one wave's per-group verdicts into the group pipeline rows.

    Each result carries ``group_id``, the contract ``action``, and optionally
    the new document chain (``doc_version``/``doc_hash``/``doc_ref``), the
    post-round open-suggestion count, a reply ``fingerprint`` and
    ``blocked_reason``.  Revision-demanding actions bump the group-wide
    round; a round reaching ``max_rounds`` — or the stall rule firing (open
    count not shrinking for ``MENXIA_GROUP_STALL_LIMIT`` consecutive
    revision rounds) — parks the group as ``ESCALATED`` instead of looping.

    ``budget_reset`` (set by the group gate) un-parks a parked row with a
    fresh budget and cleared stall counters.
    """

    rows = {row.group_id: row for row in review.seed_menxia_groups()}
    for raw in results:
        if not isinstance(raw, Mapping):
            raise InvariantViolation("menxia group result must be an object")
        group_id = str(raw.get("group_id") or "").strip()
        action = str(raw.get("action") or "").strip()
        if not group_id or not action:
            raise InvariantViolation("menxia group result requires group_id and action")
        row = rows.get(group_id)
        if row is None:
            raise InvariantViolation(
                f"menxia group result names unknown group {group_id!r}"
            )
        budget_reset = bool(raw.get("budget_reset"))
        if row.stage in MENXIA_GROUP_TERMINAL_STAGES:
            if not budget_reset:
                continue
            rows[group_id] = MenxiaGroupState(
                group_id=row.group_id,
                stage="SOLVING",
                revision_round=0,
                doc_version=row.doc_version,
                doc_hash=row.doc_hash,
                doc_ref=row.doc_ref,
                doc_markdown=row.doc_markdown,
                last_verdict=action,
                fingerprint=str(raw.get("fingerprint") or row.fingerprint),
            )
            continue
        next_stage = menxia_group_next_stage(action)
        revision_round = row.revision_round
        stalled_rounds = row.stalled_rounds
        stage = row.stage
        open_count = row.last_open_count
        raw_open = raw.get("open_count")
        if isinstance(raw_open, int) and not isinstance(raw_open, bool):
            open_count = max(0, raw_open)
        if next_stage is None:
            stage = row.stage
        elif next_stage == "SOLVING" and action in MENXIA_GROUP_REVISION_DEMAND_ACTIONS:
            revision_round += 1
            # The stall rule compares consecutive reviewer observations.  The
            # first revision demand only seeds the baseline (last_open_count
            # starts at 0, so ``>=`` alone would count a fresh suggestion
            # burst as "no progress").
            if row.revision_round > 0 and open_count >= row.last_open_count:
                stalled_rounds += 1
            else:
                stalled_rounds = 0
            if (
                0 < max_rounds <= revision_round
                or stalled_rounds >= MENXIA_GROUP_STALL_LIMIT
            ):
                stage = "ESCALATED"
            else:
                stage = next_stage
        else:
            stage = next_stage
            stalled_rounds = 0
        updates: dict[str, object] = {}
        for field in MENXIA_GROUP_DOC_FIELDS:
            value = raw.get(field)
            if value in (None, ""):
                continue
            updates[field] = int(value) if field == "doc_version" else str(value)
        raw_doc = raw.get("doc_markdown")
        if isinstance(raw_doc, str) and raw_doc.strip():
            # The authoritative markdown travels with the fold; its hash is
            # recomputed here so the chain never trusts a worker's claim.
            updates["doc_markdown"] = raw_doc
            updates["doc_hash"] = hashlib.sha256(raw_doc.encode("utf-8")).hexdigest()
            claimed_version = raw.get("doc_version")
            if isinstance(claimed_version, int) and not isinstance(claimed_version, bool):
                updates["doc_version"] = claimed_version
        if action == "BLOCKED":
            updates["blocked_reason"] = str(
                raw.get("blocked_reason") or "MENXIA_GROUP_BLOCKED"
            )
        rows[group_id] = MenxiaGroupState(
            group_id=row.group_id,
            stage=stage,
            revision_round=revision_round,
            doc_version=int(updates.get("doc_version", row.doc_version)),
            doc_hash=str(updates.get("doc_hash", row.doc_hash)),
            doc_ref=str(updates.get("doc_ref", row.doc_ref)),
            doc_markdown=str(updates.get("doc_markdown", row.doc_markdown)),
            last_verdict=action,
            fingerprint=str(raw.get("fingerprint") or row.fingerprint),
            blocked_reason=str(updates.get("blocked_reason", row.blocked_reason)),
            last_open_count=open_count,
            stalled_rounds=stalled_rounds,
        )
    # Keep the seed order for deterministic snapshots.
    ordered_ids = [row.group_id for row in review.seed_menxia_groups()]
    return tuple(rows[group_id] for group_id in ordered_ids if group_id in rows)


def menxia_group_stage_census(
    groups: Iterable[MenxiaGroupState],
) -> dict[str, int]:
    """Count pipeline rows per sub-phase."""

    census: dict[str, int] = {}
    for row in groups:
        census[row.stage] = census.get(row.stage, 0) + 1
    return census


def menxia_group_wave_action(
    state: str,
    *,
    result_actions: Mapping[str, str],
    census: Mapping[str, int],
) -> str:
    """Reduce one wave's per-group outcomes to the FSM transition action.

    ``result_actions`` maps dispatched group ids to their raw contract
    actions; ``census`` counts every row's post-fold stage.  A HUMAN_GATE
    verdict wins; otherwise the earliest un-drained stage routes onward.
    """

    if "HUMAN_GATE" in result_actions.values():
        return "HUMAN_GATE"
    has = lambda stage: bool(census.get(stage))
    if state == "MENXIA_GROUP_SOLVER":
        if has("ANALYZING"):
            return "READY_FOR_ANALYST"
        if has("REVIEWING"):
            return "READY_FOR_CRITIC"
        return "READY_FOR_ANALYST"
    if state == "MENXIA_GROUP_ANALYST":
        if has("SOLVING"):
            return "NEEDS_MORE_EVIDENCE"
        return "EVIDENCE_SUFFICIENT"
    if state == "MENXIA_GROUP_CRITIC":
        if has("SOLVING"):
            return "REQUEST_SOLVER_REVISION"
        if has("ANALYZING"):
            return "REQUEST_EVIDENCE"
        return "APPROVE_GROUP"
    raise InvariantViolation(f"state {state!r} has no menxia group wave reduction")


def menxia_group_pipeline_readiness(
    review: ReviewState,
    groups: Iterable[MenxiaGroupState] | None = None,
) -> tuple[dict[str, object], ...]:
    """Per-group gate readiness from the group pipeline rows (design §4.4).

    A group is ready for the gate agent when its row reached a terminal
    stage; ``blockers`` lists the groups parked as BLOCKED/ESCALATED the
    gate must present to the decision maker.  This is the shared-document
    counterpart of ``menxia_group_readiness`` (item rows) and uses the same
    report shape so the gate agent's envelope stays unchanged.
    """

    rows = {
        row.group_id: row
        for row in (groups if groups is not None else review.seed_menxia_groups())
    }
    reports: list[dict[str, object]] = []
    for group in sorted(review.task_groups, key=lambda entry: (entry.order, entry.group_id)):
        row = rows.get(group.group_id)
        stage = row.stage if row is not None else "SOLVING"
        parked = stage in ("BLOCKED", "ESCALATED")
        reports.append(
            {
                "group_id": group.group_id,
                "ready": stage in MENXIA_GROUP_TERMINAL_STAGES,
                "approved": sorted(group.item_ids) if stage == "APPROVED" else [],
                "blockers": [group.group_id] if parked else [],
            }
        )
    return tuple(reports)


def menxia_groups_have_blockers(groups: Iterable[MenxiaGroupState]) -> bool:
    """True when any group is parked for a human (escalated or blocked)."""

    return any(
        row.stage in ("BLOCKED", "ESCALATED") for row in groups
    )


__all__ = [
    "MENXIA_GROUP_ACTIVE_STAGES",
    "MENXIA_GROUP_REVISION_DEMAND_ACTIONS",
    "MENXIA_GROUP_STAGE_BY_ACTION",
    "MENXIA_GROUP_STAGE_BY_TARGET",
    "MENXIA_GROUP_STALL_LIMIT",
    "MENXIA_GROUP_TARGETS",
    "MENXIA_GROUP_TERMINAL_STAGES",
    "apply_menxia_group_results",
    "menxia_group_next_stage",
    "menxia_group_pipeline_readiness",
    "menxia_group_stage_census",
    "menxia_group_wave_action",
    "menxia_groups_have_blockers",
    "ready_stage_groups",
]
