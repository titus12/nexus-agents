"""ItemWorkflow dispatch-table folding (A2/P2).

The authoritative verdict record is ``task_review_ledger``; this policy folds
the same task-review round into the per-item *pipeline* table the ItemWorkflow
executor (P3) consumes.  The two must never disagree: ``rounds`` mirrors the
ledger's ``changes_rounds`` counting rule (attempt-scoped, ratchet-consistent)
and ``ESCALATED`` marks the same condition the gate escalates
(``stalled_item_ids``: rounds >= max_item_revision_rounds).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace

from ..context import ItemWorkflow

_APPROVED = "TASK_APPROVED"


def update_item_workflows(
    current: Iterable[ItemWorkflow],
    task_reviews: Iterable[Mapping[str, object]] | None,
    *,
    attempted_item_ids: Iterable[str] | None,
    max_rounds: int,
    failed_round: bool = False,
) -> tuple[ItemWorkflow, ...] | None:
    """Fold one task-review round into the dispatch table.

    Rows are created on first sight and updated per the round's verdict.  The
    counting rule mirrors ``_task_review_ledger_update``: an unknown attempt
    set keeps the historical count-every-rejection behaviour, and a task the
    Solver's batch never attempted does not consume its stall budget.  A
    protocol-failed wave (``failed_round``) records verdicts for state-keeping
    only -- the retry re-reviews non-approved tasks and charges them then, so
    one worker's protocol slip must not spend other items' stall budget.
    Returns ``None`` when there is nothing to fold, so the caller keeps the
    current table instead of pretending the round changed it.
    """

    if not isinstance(task_reviews, list) or not task_reviews:
        return None
    rows = {row.item_id: row for row in (current or ())}
    attempted = {
        str(item).strip()
        for item in (attempted_item_ids or ())
        if str(item).strip()
    }
    updated = False
    for entry in task_reviews:
        if not isinstance(entry, Mapping):
            continue
        item_id = str(entry.get("item_id") or "").strip()
        if not item_id:
            continue
        action = str(entry.get("action") or "")
        previous = rows.get(item_id)
        approved = action == _APPROVED
        counted = not failed_round and (not attempted or item_id in attempted)
        if approved:
            rounds = 0
            phase = "APPROVED"
        elif counted:
            rounds = (previous.rounds if previous else 0) + 1
            phase = "ESCALATED" if (max_rounds > 0 and rounds >= max_rounds) else "REVISING"
        else:
            rounds = previous.rounds if previous else 0
            phase = previous.phase if previous else "REVIEWING"
        rows[item_id] = replace(
            previous or ItemWorkflow(item_id=item_id),
            item_id=item_id,
            phase=phase,
            rounds=rounds,
            last_verdict=action,
            finding_ids=_entry_finding_ids(entry),
        )
        updated = True
    return tuple(rows.values()) if updated else None


def _entry_finding_ids(entry: Mapping[str, object]) -> tuple[str, ...]:
    raw = entry.get("finding_ids")
    if isinstance(raw, (list, tuple)):
        return tuple(
            dict.fromkeys(
                str(value).strip() for value in raw if str(value).strip()
            )
        )
    finding_id = str(entry.get("finding_id") or "").strip()
    return (finding_id,) if finding_id else ()


__all__ = ["update_item_workflows"]
