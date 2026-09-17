"""Critic role logic: fold per-task verdicts, then gate the round.

The aggregation (folding the per-item verdicts into one ledger) and the gate
(deciding whether the round freezes, revises, escalates, or blocks) are pure
computations over plain data.  The FSM state translates the verdict into
decisions, effects, and logs; it owns no review policy of its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Mapping

from ..policies.zhongshu import (
    active_blocker_count,
    revision_made_progress,
    stalled_item_ids,
    stuck_blockers,
    unapproved_item_ids,
)
from ..policies.zhongshu import active_findings_by_severity


@dataclass(frozen=True)
class ReviewRound:
    """One aggregated Critic round, before any gating decision."""

    ledger: tuple[Any, ...]
    findings: tuple[Any, ...]
    expected_item_ids: tuple[str, ...]
    active_blockers: int
    stalled_item_ids: tuple[str, ...]

    @property
    def unapproved_item_ids(self) -> tuple[str, ...]:
        return unapproved_item_ids(self.ledger, self.expected_item_ids)

    @property
    def reviewed_item_ids(self) -> set[str]:
        return {
            str(getattr(record, "item_id", "") or "") for record in self.ledger
        }


@dataclass(frozen=True)
class GateVerdict:
    """What the round should do next; the FSM state renders it."""

    action: str  # PROCEED | APPROVE_CRITIC | HUMAN_GATE | BLOCKED | FREEZE_WITH_FOLLOWUPS
    reason_code: str = ""
    followup_finding_ids: tuple[str, ...] = ()
    deferred_followup_unreviewed: tuple[str, ...] = ()
    stuck_finding_ids: tuple[str, ...] = ()
    no_progress_count: int = 0


def fold_round(
    *,
    ledger: Iterable[Any],
    findings: Iterable[Any],
    expected_item_ids: Iterable[str],
    max_item_rounds: int,
) -> ReviewRound:
    """Aggregate one round's verdicts, findings, and coverage."""

    ledger_tuple = tuple(ledger or ())
    findings_tuple = tuple(findings or ())
    expected = tuple(dict.fromkeys(
        str(item_id).strip() for item_id in expected_item_ids or () if str(item_id).strip()
    ))
    return ReviewRound(
        ledger=ledger_tuple,
        findings=findings_tuple,
        expected_item_ids=expected,
        active_blockers=active_blocker_count(findings_tuple),
        stalled_item_ids=stalled_item_ids(ledger_tuple, max_item_rounds),
    )


def evaluate_gate(
    round: ReviewRound,
    *,
    revision_allowed: bool,
    previous_fingerprint: str | None,
    no_progress_count: int,
    max_no_progress: int,
    max_stuck_rounds: int,
) -> GateVerdict:
    """Decide what the round does next, in priority order.

    1. the revision budget is spent -> block;
    2. every reviewed task is approved and no blocker survives -> freeze
       instead of spending a round on a no-op;
    3. while the blocker set is still shrinking, a single task rejected for too
       many rounds escalates on its own;
    4. once the blocker set stops shrinking, residual P1s may be accepted as
       follow-ups (never P0, and only when every expected task was reviewed),
       otherwise the stall/stuck/no-progress exits apply in that order.
    """

    if not revision_allowed:
        return GateVerdict(action="BLOCKED", reason_code="ZHONGSHU_REVISION_BUDGET_EXHAUSTED")

    pending = round.unapproved_item_ids
    if round.ledger and not pending and round.active_blockers == 0:
        return GateVerdict(action="APPROVE_CRITIC", reason_code="ZHONGSHU_FREEZE_WITHOUT_REVISION")

    no_progress = (
        0
        if revision_made_progress(previous_fingerprint, round.active_blockers)
        else no_progress_count + 1
    )
    deferred_followup_unreviewed: tuple[str, ...] = ()
    if no_progress < max_no_progress:
        if round.stalled_item_ids:
            return GateVerdict(
                action="HUMAN_GATE",
                reason_code="ZHONGSHU_ITEM_STALLED",
                no_progress_count=no_progress,
            )
    else:
        # The blocker set stopped shrinking.  A run whose only residual
        # blockers are P1s still has a bounded way out: freeze and record them
        # as follow-ups, so one opinion the Critic keeps restating cannot
        # consume the whole budget and end the run in a generic block.
        residual_p0 = active_findings_by_severity(round.findings, ("P0",))
        followups = active_findings_by_severity(round.findings, ("P1",))
        if not residual_p0 and followups:
            followup_ids = tuple(
                str(getattr(item, "finding_id", "") or "") for item in followups
            )
            unreviewed = tuple(
                item
                for item in round.expected_item_ids
                if item not in round.reviewed_item_ids
            )
            if not unreviewed:
                return GateVerdict(
                    action="FREEZE_WITH_FOLLOWUPS",
                    reason_code="ZHONGSHU_FREEZE_WITH_FOLLOWUPS",
                    followup_finding_ids=tuple(value for value in followup_ids if value),
                    no_progress_count=no_progress,
                )
            deferred_followup_unreviewed = unreviewed
        if round.stalled_item_ids:
            return GateVerdict(
                action="HUMAN_GATE",
                reason_code="ZHONGSHU_ITEM_STALLED",
                deferred_followup_unreviewed=deferred_followup_unreviewed,
                no_progress_count=no_progress,
            )
    stuck = stuck_blockers(round.findings, max_stuck_rounds)
    if stuck:
        return GateVerdict(
            action="HUMAN_GATE",
            reason_code="ZHONGSHU_STUCK_FINDING",
            stuck_finding_ids=tuple(
                str(getattr(item, "finding_id", "") or "") for item in stuck
            ),
            no_progress_count=no_progress,
        )
    if no_progress >= max_no_progress:
        return GateVerdict(
            action="BLOCKED",
            reason_code="ZHONGSHU_NO_PROGRESS",
            deferred_followup_unreviewed=deferred_followup_unreviewed,
            no_progress_count=no_progress,
        )
    return GateVerdict(action="PROCEED", no_progress_count=no_progress)


def mark_followups(
    findings: Iterable[Any],
    followup_ids: Iterable[str],
    note: str,
) -> tuple[Any, ...]:
    """Record the accepted follow-ups as DEFERRED findings."""

    wanted = {value for value in followup_ids if value}
    return tuple(
        replace(
            finding,
            status="DEFERRED",
            resolution=getattr(finding, "resolution", None) or note,
        )
        if str(getattr(finding, "finding_id", "") or "") in wanted
        else finding
        for finding in findings
    )


__all__ = [
    "GateVerdict",
    "ReviewRound",
    "evaluate_gate",
    "fold_round",
    "mark_followups",
]
