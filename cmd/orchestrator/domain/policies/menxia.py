"""Pure Menxia scope, item finding, and stage-barrier scheduling rules."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from ..context import MenxiaItemState, MenxiaParallelLimits, ReviewState
from ..errors import InvariantViolation
from ..findings import Finding


MENXIA_ITEM_TARGETS = ("MENXIA_ITEM_SOLVER", "MENXIA_ITEM_ANALYST", "MENXIA_ITEM_CRITIC")

# The FSM state an item's sub-phase must match for that state's wave to
# dispatch it (stage barrier model, design §6.1).
MENXIA_STAGE_BY_TARGET = {
    "MENXIA_ITEM_SOLVER": "SOLVING",
    "MENXIA_ITEM_ANALYST": "ANALYZING",
    "MENXIA_ITEM_CRITIC": "REVIEWING",
}

MENXIA_ACTIVE_STAGES = ("SOLVING", "ANALYZING", "REVIEWING")
MENXIA_TERMINAL_STAGES = ("APPROVED", "REMOVED", "BLOCKED", "ESCALATED")

# Contract action → the item's next sub-phase.  ``None`` parks the item in its
# current stage (a per-item HUMAN_GATE verdict pauses the whole run via the
# aggregate action instead of folding a stage change).
MENXIA_STAGE_BY_ACTION = {
    "FEASIBLE": "ANALYZING",
    "READY_FOR_ANALYST": "ANALYZING",
    "READY_FOR_CRITIC": "REVIEWING",
    "EVIDENCE_SUFFICIENT": "REVIEWING",
    "APPROVE_ITEM": "APPROVED",
    "REVISE_ITEM": "SOLVING",
    "SPLIT_ITEM": "SOLVING",
    "MERGE_ITEM": "SOLVING",
    "REQUEST_SOLVER_REVISION": "SOLVING",
    "NEEDS_MORE_EVIDENCE": "SOLVING",
    "REMOVE_ITEM": "REMOVED",
    "BLOCKED": "BLOCKED",
    "HUMAN_GATE": None,
}

# Actions that send an item back to its Solver: each consumes one unit of the
# item's attempt-scoped revision budget (design §4).
MENXIA_REVISION_DEMAND_ACTIONS = frozenset(
    {"REVISE_ITEM", "SPLIT_ITEM", "MERGE_ITEM", "REQUEST_SOLVER_REVISION", "NEEDS_MORE_EVIDENCE"}
)

# Solver contract actions that carry an ``implementation_proposal`` worth
# persisting on the pipeline row for the item's Critic.
MENXIA_SOLVER_FORWARD_ACTIONS = frozenset(
    {"FEASIBLE", "READY_FOR_ANALYST", "READY_FOR_CRITIC"}
)

# Shared evidence-record shape (kept in sync with the packet slice fields the
# dispatch context projects in states._item_evidence_context).
_EVIDENCE_RECORD_FIELDS = (
    "evidence_id", "requirement_id", "item_id", "decision_relevance",
    "conclusion", "source", "confidence",
)
_EVIDENCE_TEXT_LIMITS = {"conclusion": 400, "source": 200}
_MAX_ITEM_EVIDENCE_CONTRIBUTION = 16
_MAX_SOLVER_PROPOSAL_ROWS = 12


def validate_scope(
    findings: Iterable[Finding],
    *,
    group_id: str,
    item_id: str,
) -> tuple[Finding, ...]:
    group = str(group_id or "").strip()
    item = str(item_id or "").strip()
    if not group or not item:
        raise InvariantViolation("Menxia item finding scope requires group_id and item_id")
    result = tuple(findings)
    for finding in result:
        if finding.group_id and finding.group_id != group:
            raise InvariantViolation("finding group_id does not match active Menxia group")
        if finding.item_id and finding.item_id != item:
            raise InvariantViolation("finding item_id does not match active Menxia item")
    return result


def findings_for_scope(
    findings: Iterable[Finding],
    *,
    group_id: str,
    item_id: str | None = None,
    include_global: bool = False,
) -> tuple[Finding, ...]:
    group = str(group_id or "").strip()
    item = str(item_id or "").strip()
    if item and not group:
        raise InvariantViolation("item scope requires group_id")
    result = []
    for finding in findings:
        if item:
            if finding.group_id == group and finding.item_id == item:
                result.append(finding)
        elif finding.group_id == group or (
            include_global and not finding.group_id and not finding.item_id
        ):
            result.append(finding)
    return tuple(result)


def menxia_item_next_stage(action: str) -> str | None:
    """Map one contract action to the item's next sub-phase (None = park)."""

    return MENXIA_STAGE_BY_ACTION.get(str(action or "").strip())


def ready_stage_items(
    review: ReviewState,
    *,
    target: str,
    limits: MenxiaParallelLimits | None = None,
) -> tuple[MenxiaItemState, ...]:
    """Pick the items a menxia wave dispatches, group-fair and capped.

    Items are ordered round-robin between groups (by group order, then item
    order) so no group starves.  With ``limits.enabled`` the selection is
    bounded by ``max_concurrent_groups`` and ``max_concurrent_items``;
    otherwise only the first ready item is returned (legacy serial behaviour).
    """

    stage = MENXIA_STAGE_BY_TARGET.get(target)
    if stage is None:
        raise InvariantViolation(f"state {target!r} is not a menxia item pipeline state")
    rows = {row.item_id: row for row in review.seed_menxia_items()}
    satisfied = set(review.completed_item_ids) | {
        item_id for item_id, row in rows.items() if row.stage == "REMOVED"
    }
    group_order = {group.group_id: group.order for group in review.task_groups}
    candidates: list[tuple[int, int, str, MenxiaItemState]] = []
    for item in sorted(review.task_items, key=lambda entry: (entry.order, entry.item_id)):
        row = rows.get(item.item_id)
        if row is None or row.stage != stage:
            continue
        if not set(item.dependencies).issubset(satisfied):
            continue
        candidates.append(
            (group_order.get(item.group_id, 1 << 30), item.order, item.item_id, row)
        )
    if not candidates:
        return ()
    if limits is None or not limits.enabled:
        return (candidates[0][3],)
    buckets: dict[str, list[MenxiaItemState]] = {}
    for _, _, _, row in candidates:
        buckets.setdefault(row.group_id, []).append(row)
    ordered_groups = sorted(buckets, key=lambda gid: group_order.get(gid, 1 << 30))
    max_items = max(1, int(limits.max_concurrent_items))
    max_groups = max(1, int(limits.max_concurrent_groups))
    selected: list[MenxiaItemState] = []
    while len(selected) < max_items:
        picked = False
        for group_id in ordered_groups:
            if len(selected) >= max_items:
                break
            bucket = buckets.get(group_id)
            if not bucket:
                continue
            selected_groups = {row.group_id for row in selected}
            if group_id not in selected_groups and len(selected_groups) >= max_groups:
                continue
            selected.append(bucket.pop(0))
            picked = True
        if not picked:
            break
    return tuple(selected)


def _menxia_evidence_record(item_id: str, value: object) -> dict[str, object] | None:
    """Project one analyst evidence entry onto the shared record shape."""

    if not isinstance(value, Mapping):
        text = " ".join(str(value or "").split())
        if not text:
            return None
        value = {"conclusion": text}
    record: dict[str, object] = {}
    for key in _EVIDENCE_RECORD_FIELDS:
        raw = value.get(key)
        if raw in (None, "", [], {}):
            continue
        record[key] = raw
    if not record.get("evidence_id"):
        # Analyst facts may be keyed as ``fact_id``/``id``; the identity
        # matters for packet dedup and for findings-named slice matching.
        for alt_key in ("fact_id", "id"):
            alt = str(value.get(alt_key) or "").strip()
            if alt:
                record["evidence_id"] = alt
                break
    statement = str(value.get("statement") or "").strip()
    conclusion = str(record.get("conclusion") or "").strip() or statement
    conclusion = " ".join(conclusion.split())
    if not conclusion:
        return None
    limit = _EVIDENCE_TEXT_LIMITS.get("conclusion")
    record["conclusion"] = conclusion[:limit] if limit else conclusion
    source = record.get("source")
    if isinstance(source, str):
        source = " ".join(source.split())
        source_limit = _EVIDENCE_TEXT_LIMITS.get("source")
        record["source"] = source[:source_limit] if source_limit else source
    # The binding context is the authoritative item scope: a payload that
    # tags another item's id must not leak across packet item slices.
    record["item_id"] = item_id
    return record


def menxia_evidence_contribution(
    item_id: str,
    payload: Mapping[str, object],
    worker_id: str,
) -> dict[str, object] | None:
    """Collect one analyst worker's evidence into the shared packet shape.

    The menxia item Analyst verifies evidence per item, but the wave fan-in
    used to drop it, so the item Critic re-demanded evidence the run already
    possessed and items stalled on phantom gaps.  The contribution carries the
    worker's records plus the worker id so the packet's ``worker_evidence``
    lens map stays truthful.
    """

    item = str(item_id or "").strip()
    if not item or not isinstance(payload, Mapping):
        return None
    records: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    for key in ("evidence", "confirmed_facts"):
        raw = payload.get(key)
        if not isinstance(raw, list):
            continue
        for value in raw:
            record = _menxia_evidence_record(item, value)
            if record is None:
                continue
            dedup = (
                str(record.get("evidence_id") or ""),
                str(record.get("conclusion") or ""),
            )
            if dedup in seen:
                continue
            seen.add(dedup)
            records.append(record)
            if len(records) >= _MAX_ITEM_EVIDENCE_CONTRIBUTION:
                break
        if len(records) >= _MAX_ITEM_EVIDENCE_CONTRIBUTION:
            break
    if not records:
        return None
    worker = str(worker_id or "").strip()
    return {
        "item_id": item,
        "worker_ids": [worker] if worker else [],
        "records": records,
    }


def merge_menxia_evidence(
    existing_packet: object,
    contributions: Iterable[Mapping[str, object]],
) -> dict[str, object] | None:
    """Fold menxia analyst evidence contributions into the evidence packet.

    Returns ``None`` when nothing new was contributed, so the fold keeps the
    current packet untouched.  Zhongshu packet fields are preserved: the
    menxia records extend ``evidence_updates``/``worker_evidence`` instead of
    replacing them, and only a first packet is created from a skeleton.
    """

    rows = [
        dict(row)
        for row in (contributions or ())
        if isinstance(row, Mapping) and str(row.get("item_id") or "").strip()
    ]
    if not rows:
        return None
    packet = dict(existing_packet) if isinstance(existing_packet, Mapping) else {}
    if not packet:
        packet.update(
            {
                "plan_id": "",
                "version": 1,
                "phase": "MENXIA",
                "evidence_packet_version": 1,
            }
        )
    updates = [
        dict(item)
        for item in packet.get("evidence_updates") or []
        if isinstance(item, Mapping)
    ]

    def dedup_key(record: Mapping[str, object]) -> str:
        # Evidence ids are run-scoped: one id is one record no matter which
        # worker re-derived it or which item it was filed under.  Records
        # without an id fall back to per-item conclusion identity.
        return str(record.get("evidence_id") or "")

    index_by_id: dict[str, int] = {}
    for position, item in enumerate(updates):
        key = dedup_key(item)
        if key:
            index_by_id.setdefault(key, position)
    seen_unkeyed = {
        (str(item.get("item_id") or ""), str(item.get("conclusion") or ""))
        for item in updates
        if not dedup_key(item)
    }
    worker_evidence = (
        dict(packet.get("worker_evidence"))
        if isinstance(packet.get("worker_evidence"), Mapping)
        else {}
    )
    added = 0
    refreshed = 0
    for row in rows:
        item_id = str(row.get("item_id") or "").strip()
        worker_ids = [
            str(worker).strip()
            for worker in row.get("worker_ids") or []
            if str(worker).strip()
        ]
        records = [
            dict(record)
            for record in row.get("records") or []
            if isinstance(record, Mapping)
        ]
        if not records:
            continue
        for record in records:
            # Attribution follows the contribution's item, never the payload.
            record["item_id"] = item_id
            key = dedup_key(record)
            if key:
                existing_index = index_by_id.get(key)
                if existing_index is not None:
                    # Same evidence id: keep the widest-scoped record but
                    # refresh its content with the latest derivation.
                    existing = updates[existing_index]
                    if existing.get("conclusion") != record.get("conclusion"):
                        updates[existing_index] = {
                            **existing,
                            "conclusion": record.get("conclusion"),
                        }
                        refreshed += 1
                    continue
                index_by_id[key] = len(updates)
                updates.append(record)
                added += 1
                continue
            unkeyed = (item_id, str(record.get("conclusion") or ""))
            if unkeyed in seen_unkeyed:
                continue
            seen_unkeyed.add(unkeyed)
            updates.append(record)
            added += 1
        for worker_id in worker_ids:
            slot = worker_evidence.get(worker_id)
            slot = dict(slot) if isinstance(slot, Mapping) else {}
            facts = [
                dict(fact)
                for fact in slot.get("confirmed_facts") or []
                if isinstance(fact, Mapping)
            ]
            for record in records:
                if record not in facts:
                    facts.append(record)
            slot["confirmed_facts"] = facts
            worker_evidence[worker_id] = slot
    if not added and not refreshed:
        return None
    packet["evidence_updates"] = updates
    packet["worker_evidence"] = worker_evidence
    return packet


def _compact_solver_proposal(value: Mapping[str, object]) -> dict[str, object]:
    """Bound the persisted proposal so snapshots stay small but usable."""

    proposal = dict(value)
    for key in ("files", "changes", "tests"):
        raw = proposal.get(key)
        if isinstance(raw, list):
            proposal[key] = raw[:_MAX_SOLVER_PROPOSAL_ROWS]
    return proposal


def apply_menxia_item_results(
    review: ReviewState,
    results: Iterable[Mapping[str, object]],
    *,
    max_rounds: int,
) -> tuple[MenxiaItemState, ...]:
    """Fold one wave's per-item verdicts into the menxia pipeline rows.

    Each result carries ``item_id`` and the contract ``action`` (plus an
    optional reply ``fingerprint`` and ``blocked_reason``).  Actions that
    demand a revision bump the item's attempt-scoped round; an item whose
    round reaches ``max_rounds`` is parked as ``ESCALATED`` instead of being
    sent back to its Solver (design §4 budget).

    A result may carry ``budget_reset`` (set by the group gate): it un-parks
    an ``ESCALATED``/``BLOCKED`` row with a fresh round budget.  ``REMOVED``
    rows can never restart.
    """

    rows = {row.item_id: row for row in review.seed_menxia_items()}
    for raw in results:
        if not isinstance(raw, Mapping):
            raise InvariantViolation("menxia item result must be an object")
        item_id = str(raw.get("item_id") or "").strip()
        action = str(raw.get("action") or "").strip()
        if not item_id or not action:
            raise InvariantViolation("menxia item result requires item_id and action")
        row = rows.get(item_id)
        if row is None:
            raise InvariantViolation(f"menxia item result names unknown item {item_id!r}")
        budget_reset = bool(raw.get("budget_reset"))
        if row.stage in MENXIA_TERMINAL_STAGES:
            if not budget_reset or row.stage == "REMOVED":
                # Terminal rows stay folded unless a group-gate revision
                # explicitly grants a fresh attempt (REMOVED items are gone
                # from the plan and cannot restart).
                continue
            rows[item_id] = MenxiaItemState(
                item_id=row.item_id,
                group_id=row.group_id,
                stage="SOLVING",
                revision_round=0,
                last_verdict=action,
                fingerprint=str(raw.get("fingerprint") or row.fingerprint),
                blocked_reason="",
                last_solver_proposal=row.last_solver_proposal,
            )
            continue
        next_stage = menxia_item_next_stage(action)
        revision_round = row.revision_round
        blocked_reason = row.blocked_reason
        raw_proposal = raw.get("implementation_proposal")
        last_solver_proposal = (
            _compact_solver_proposal(raw_proposal)
            if isinstance(raw_proposal, Mapping)
            and action in MENXIA_SOLVER_FORWARD_ACTIONS
            else row.last_solver_proposal
        )
        if next_stage is None:
            stage = row.stage
        elif next_stage == "SOLVING" and action in MENXIA_REVISION_DEMAND_ACTIONS:
            revision_round += 1
            if 0 < max_rounds <= revision_round:
                stage = "ESCALATED"
                blocked_reason = "MENXIA_ITEM_STALLED"
            else:
                stage = next_stage
        else:
            stage = next_stage
        if action == "BLOCKED":
            blocked_reason = str(raw.get("blocked_reason") or "MENXIA_ITEM_BLOCKED")
        rows[item_id] = MenxiaItemState(
            item_id=row.item_id,
            group_id=row.group_id,
            stage=stage,
            revision_round=revision_round,
            last_verdict=action,
            fingerprint=str(raw.get("fingerprint") or row.fingerprint),
            blocked_reason=blocked_reason,
            last_solver_proposal=last_solver_proposal,
        )
    # Keep the seed order (a set literal would iterate in hash order,
    # which is nondeterministic across processes and would churn snapshots).
    ordered_ids = [row.item_id for row in review.seed_menxia_items()]
    return tuple(rows[item_id] for item_id in ordered_ids if item_id in rows)


def menxia_stage_census(items: Iterable[MenxiaItemState]) -> dict[str, int]:
    """Count pipeline rows per sub-phase."""

    census: dict[str, int] = {}
    for row in items:
        census[row.stage] = census.get(row.stage, 0) + 1
    return census


def menxia_wave_action(
    state: str,
    *,
    result_actions: Mapping[str, str],
    census: Mapping[str, int],
) -> str:
    """Reduce one wave's per-item outcomes to the FSM transition action.

    ``result_actions`` maps dispatched item ids to their raw contract
    actions; ``census`` counts every row's post-fold stage.  A per-item
    HUMAN_GATE verdict wins (the run pauses and the stage wave re-runs on
    resume); otherwise the earliest un-drained stage routes onward
    (design §6.3).
    """

    if "HUMAN_GATE" in result_actions.values():
        return "HUMAN_GATE"
    has = lambda stage: bool(census.get(stage))
    if state == "MENXIA_ITEM_SOLVER":
        if has("ANALYZING"):
            return "READY_FOR_ANALYST"
        if has("REVIEWING"):
            return "READY_FOR_CRITIC"
        return "READY_FOR_ANALYST"
    if state == "MENXIA_ITEM_ANALYST":
        if has("SOLVING"):
            return "NEEDS_MORE_EVIDENCE"
        return "EVIDENCE_SUFFICIENT"
    if state == "MENXIA_ITEM_CRITIC":
        if has("SOLVING"):
            return "REQUEST_SOLVER_REVISION"
        return "APPROVE_ITEM"
    raise InvariantViolation(f"state {state!r} has no menxia wave reduction")


def menxia_has_blockers(items: Iterable[MenxiaItemState]) -> bool:
    """True when any item is parked for a human (escalated or blocked)."""

    return any(row.stage in ("BLOCKED", "ESCALATED") for row in items)


def menxia_group_readiness(
    review: ReviewState,
    items: Iterable[MenxiaItemState],
) -> tuple[dict[str, object], ...]:
    """Per-group terminal readiness for the group gate (design §4.4).

    A group is ready for the gate agent when every one of its items is in a
    terminal-or-parked stage.  ``blockers`` lists the ESCALATED/BLOCKED items
    the gate must present to the decision maker.
    """

    stage_by_item = {row.item_id: row.stage for row in items}
    reports: list[dict[str, object]] = []
    for group in sorted(review.task_groups, key=lambda entry: (entry.order, entry.group_id)):
        member_stages = {
            item_id: stage_by_item.get(item_id, "SOLVING")
            for item_id in group.item_ids
        }
        blockers = sorted(
            item_id for item_id, stage in member_stages.items()
            if stage in ("BLOCKED", "ESCALATED")
        )
        completed = set(review.completed_item_ids)
        approved = sorted(item_id for item_id in group.item_ids if item_id in completed)
        reports.append(
            {
                "group_id": group.group_id,
                "ready": all(
                    stage in MENXIA_TERMINAL_STAGES for stage in member_stages.values()
                ),
                "approved": approved,
                "blockers": blockers,
            }
        )
    return tuple(reports)


def remove_plan_item(
    plan: Mapping[str, object] | None,
    item_id: str,
) -> dict[str, object] | None:
    """Return a copy of the plan with one item removed (REMOVE_ITEM fold)."""

    if not isinstance(plan, Mapping):
        return None
    updated = dict(plan)
    removed = False
    for key in ("candidate_items", "items"):
        raw_items = updated.get(key)
        if not isinstance(raw_items, list):
            continue
        filtered = []
        for raw in raw_items:
            raw_id = (
                str(raw.get("item_id") or raw.get("task_id") or "").strip()
                if isinstance(raw, Mapping)
                else str(raw).strip()
            )
            if raw_id == item_id:
                removed = True
                continue
            filtered.append(raw)
        if removed:
            updated[key] = filtered
    if not removed:
        return None
    for key in ("candidate_groups", "groups"):
        raw_groups = updated.get(key)
        if isinstance(raw_groups, list):
            updated[key] = [
                dict(group) if isinstance(group, Mapping) else group
                for group in raw_groups
            ]
    return updated


__all__ = [
    "MENXIA_ACTIVE_STAGES",
    "MENXIA_ITEM_TARGETS",
    "MENXIA_REVISION_DEMAND_ACTIONS",
    "MENXIA_STAGE_BY_ACTION",
    "MENXIA_STAGE_BY_TARGET",
    "MENXIA_TERMINAL_STAGES",
    "MENXIA_SOLVER_FORWARD_ACTIONS",
    "apply_menxia_item_results",
    "findings_for_scope",
    "menxia_evidence_contribution",
    "menxia_group_readiness",
    "menxia_has_blockers",
    "menxia_item_next_stage",
    "menxia_stage_census",
    "menxia_wave_action",
    "merge_menxia_evidence",
    "ready_stage_items",
    "remove_plan_item",
    "validate_scope",
]
