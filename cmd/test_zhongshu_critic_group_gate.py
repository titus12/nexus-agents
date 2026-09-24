"""ZhongshuCriticState group-level wiring (implementation plan Task 6).

The Critic state folds each round into the group pipeline rows: the revision
budget becomes group-scoped, frozen groups stop blocking approvals, all-parked
groups drain out to a human gate with the per-group blocker list, and the
global fingerprint fuse retires in favour of the group stall rule.  The stuck
fuse and the bounded freeze-with-followups exit are preserved.
"""

from __future__ import annotations

from dataclasses import replace

import unittest

from orchestrator.domain.context import (
    ProgressState,
    RecoveryState,
    ReviewState,
    ReviewTaskGroup,
    ReviewTaskItem,
    ReviewTaskRecord,
    TaskIdentity,
    WorkflowContext,
    ZhongshuGroupState,
)
from orchestrator.domain.events import DomainEvent
from orchestrator.domain.findings import Finding
from orchestrator.domain.policies.zhongshu import blocker_fingerprint
from orchestrator.domain.states import ZhongshuCriticState
from orchestrator.runtime.reducer import LinearContextReducer
from orchestrator.runtime.repository import WorkflowSnapshot


def _items() -> tuple[ReviewTaskItem, ...]:
    return (
        ReviewTaskItem(item_id="item-000001", group_id="group-000001", order=1),
        ReviewTaskItem(item_id="item-000002", group_id="group-000001", order=2),
        ReviewTaskItem(
            item_id="item-000003",
            group_id="group-000002",
            order=3,
            dependencies=("item-000001",),
        ),
        ReviewTaskItem(item_id="item-000004", group_id="group-000003", order=4),
    )


def _groups() -> tuple[ReviewTaskGroup, ...]:
    return (
        ReviewTaskGroup(
            group_id="group-000001",
            item_ids=("item-000001", "item-000002"),
            order=1,
        ),
        ReviewTaskGroup(group_id="group-000002", item_ids=("item-000003",), order=2),
        ReviewTaskGroup(group_id="group-000003", item_ids=("item-000004",), order=3),
    )


def _two_group_items() -> tuple[ReviewTaskItem, ...]:
    return (
        ReviewTaskItem(item_id="item-000001", group_id="group-000001", order=1),
        ReviewTaskItem(item_id="item-000002", group_id="group-000002", order=2),
    )


def _two_groups() -> tuple[ReviewTaskGroup, ...]:
    return (
        ReviewTaskGroup(group_id="group-000001", item_ids=("item-000001",), order=1),
        ReviewTaskGroup(group_id="group-000002", item_ids=("item-000002",), order=2),
    )


def _finding(
    finding_id: str,
    *,
    item_id: str,
    group_id: str,
    severity: str = "P1",
    status: str = "OPEN",
    stuck_rounds: int = 0,
) -> Finding:
    return Finding(
        finding_id=finding_id,
        severity=severity,
        status=status,
        group_id=group_id,
        item_id=item_id,
        stuck_rounds=stuck_rounds,
        claim="blocking claim",
        required_action="fix it",
    )


def _ledger(item_status: dict[str, str]) -> tuple[ReviewTaskRecord, ...]:
    return tuple(
        ReviewTaskRecord(item_id=item_id, status=status)
        for item_id, status in item_status.items()
    )


def _context(
    *,
    findings: tuple[Finding, ...] = (),
    ledger: tuple[ReviewTaskRecord, ...] = (),
    rows: tuple[ZhongshuGroupState, ...] = (),
    attempted: tuple[str, ...] = (),
    fingerprint: str = "",
    no_progress: int = 0,
    max_no_progress: int = 3,
    revision_round: int = 0,
    max_revision_rounds: int = 8,
    two_groups: bool = False,
) -> WorkflowContext:
    return WorkflowContext(
        identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
        progression=ProgressState("ZHONGSHU_CRITIC", 6, "2026-09-24T00:00:00Z"),
        recovery=RecoveryState(
            no_progress_count=no_progress, max_no_progress=max_no_progress
        ),
        review=ReviewState(
            revision_id="task-1:ZHONGSHU_ANALYST:2",
            task_items=_two_group_items() if two_groups else _items(),
            task_groups=_two_groups() if two_groups else _groups(),
            findings=findings,
            task_review_ledger=ledger,
            zhongshu_groups=rows,
            attempted_item_ids=attempted,
            last_reply_fingerprint=fingerprint,
            zhongshu_revision_round=revision_round,
            max_zhongshu_revision_rounds=max_revision_rounds,
            max_zhongshu_group_rounds=5,
        ),
    )


def _rows(**stages: str) -> tuple[ZhongshuGroupState, ...]:
    """Build group rows keyed by short group suffix with defaults."""

    defaults = {
        "group-000001": ("REVIEWING", 0),
        "group-000002": ("REVIEWING", 0),
        "group-000003": ("REVIEWING", 0),
    }
    result = []
    for group_id, (default_stage, _) in defaults.items():
        stage = stages.get(group_id.split("-")[-1], default_stage)
        result.append(ZhongshuGroupState(group_id=group_id, stage=stage))
    return tuple(result)


def _row(
    group_id: str,
    *,
    stage: str = "REVIEWING",
    revision_round: int = 0,
    stalled_rounds: int = 0,
    last_open_count: int = 0,
) -> ZhongshuGroupState:
    return ZhongshuGroupState(
        group_id=group_id,
        stage=stage,
        revision_round=revision_round,
        stalled_rounds=stalled_rounds,
        last_open_count=last_open_count,
    )


def _event(action: str) -> DomainEvent:
    return DomainEvent(
        "NODE_COMPLETED",
        "task-1",
        6,
        {"action": action},
        "2026-09-24T00:00:00Z",
    )


class ZhongshuCriticGroupGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = ZhongshuCriticState()
        self.reducer = LinearContextReducer()

    def _apply(self, context: WorkflowContext, decision) -> WorkflowContext:
        return self.reducer.apply(
            WorkflowSnapshot("task-1", context, 0), decision
        ).context

    def _rows_by_id(self, review: ReviewState) -> dict[str, ZhongshuGroupState]:
        return {row.group_id: row for row in review.zhongshu_groups}

    def test_revision_round_folds_group_rows(self) -> None:
        context = _context(
            findings=(
                _finding("f-1", item_id="item-000001", group_id="group-000001"),
            ),
            attempted=("item-000001",),
        )
        decision = self.state.handle(context, _event("REQUEST_SOLVER_REVISION"))

        self.assertEqual(decision.transition.action, "REQUEST_SOLVER_REVISION")
        rows = self._rows_by_id(decision.update.review)
        self.assertEqual(rows["group-000001"].revision_round, 1)
        self.assertEqual(rows["group-000001"].stage, "REVIEWING")
        self.assertEqual(rows["group-000002"].revision_round, 0)
        self.assertEqual(rows["group-000003"].revision_round, 0)

    def test_converged_group_not_consumed_by_other_groups_revisions(self) -> None:
        context = _context(
            findings=(
                _finding("f-1", item_id="item-000003", group_id="group-000002"),
            ),
            ledger=_ledger(
                {"item-000001": "APPROVED", "item-000002": "APPROVED"}
            ),
            rows=_rows(**{"000001": "CONVERGED"}),
            attempted=("item-000003",),
            max_no_progress=50,
        )
        for _ in range(3):
            decision = self.state.handle(context, _event("REQUEST_SOLVER_REVISION"))
            rows = self._rows_by_id(decision.update.review)
            self.assertEqual(rows["group-000001"].stage, "CONVERGED")
            self.assertEqual(rows["group-000001"].revision_round, 0)
            context = self._apply(context, decision)
            # The revision dispatches the Solver; reset the stage for the next
            # Critic round the way the scripted waves would.
            context = replace(
                context,
                progression=ProgressState(
                    "ZHONGSHU_CRITIC",
                    context.progression.sequence + 1,
                    context.progression.entered_at,
                ),
            )
        rows = self._rows_by_id(context.review)
        self.assertEqual(rows["group-000002"].revision_round, 3)

    def test_approval_hold_ignores_frozen_group_items(self) -> None:
        # group-000001 is FROZEN with items the ledger never approved; the
        # other groups are fully approved.  The frozen group's items must not
        # hold the freeze back any more.
        context = _context(
            ledger=_ledger(
                {
                    "item-000003": "APPROVED",
                    "item-000004": "APPROVED",
                }
            ),
            rows=_rows(**{"000001": "FROZEN"}),
        )
        decision = self.state.handle(context, _event("APPROVE_CRITIC"))

        self.assertEqual(decision.transition.action, "APPROVE_CRITIC")
        after = self._apply(context, decision)
        self.assertEqual(after.progression.state, "ZHONGSHU_FREEZE_CHECK")

    def test_drainout_parked_only_opens_human_gate_with_group_list(self) -> None:
        context = _context(
            findings=(
                _finding(
                    "f-1",
                    item_id="item-000001",
                    group_id="group-000001",
                    stuck_rounds=3,
                ),
            ),
            rows=(
                _row(
                    "group-000001",
                    revision_round=3,
                    stalled_rounds=1,
                    last_open_count=1,
                ),
                _row(
                    "group-000002",
                    stage="STALLED",
                    revision_round=5,
                    stalled_rounds=2,
                    last_open_count=1,
                ),
            ),
            attempted=("item-000001",),
            two_groups=True,
        )
        decision = self.state.handle(context, _event("REQUEST_SOLVER_REVISION"))

        self.assertEqual(decision.transition.action, "OPEN_HUMAN_GATE")
        self.assertEqual(
            decision.transition.reason_code, "ZHONGSHU_GROUP_STALLED"
        )
        self.assertEqual(
            decision.update.recovery.blocked_reason, "ZHONGSHU_GROUP_STALLED"
        )
        parks = decision.update.human_gate.zhongshu_group_parks
        by_group = {park["group_id"]: park for park in parks}
        self.assertEqual(
            {park["stage"] for park in parks}, {"STALLED"}
        )
        self.assertEqual(
            by_group["group-000001"]["open_finding_ids"], ("f-1",)
        )
        self.assertEqual(
            by_group["group-000001"]["unapproved_item_ids"], ("item-000001",)
        )
        self.assertEqual(
            by_group["group-000002"]["unapproved_item_ids"], ("item-000002",)
        )

    def test_stuck_finding_fuse_retained(self) -> None:
        # The stuck fuse fires before the group pipeline parks anything: one
        # finding the Solver keeps re-raising escalates on its own.
        context = _context(
            findings=(
                _finding(
                    "f-1",
                    item_id="item-000001",
                    group_id="group-000001",
                    stuck_rounds=3,
                ),
            ),
            attempted=("item-000001",),
        )
        decision = self.state.handle(context, _event("REQUEST_SOLVER_REVISION"))

        self.assertEqual(decision.transition.action, "HUMAN_GATE")
        self.assertEqual(
            decision.transition.reason_code, "ZHONGSHU_STUCK_FINDING"
        )
        rows = self._rows_by_id(decision.update.review)
        self.assertEqual(rows["group-000001"].revision_round, 1)

    def test_followup_freeze_still_bounded(self) -> None:
        findings = (
            _finding("f-1", item_id="item-000001", group_id="group-000001"),
        )
        context = _context(
            findings=findings,
            ledger=_ledger(
                {
                    "item-000001": "APPROVED",
                    "item-000002": "APPROVED",
                    "item-000003": "APPROVED",
                    "item-000004": "APPROVED",
                }
            ),
            rows=(),
            attempted=("item-000001",),
            fingerprint=blocker_fingerprint(findings),
            no_progress=2,
            max_no_progress=3,
        )
        decision = self.state.handle(context, _event("REQUEST_SOLVER_REVISION"))

        self.assertEqual(decision.transition.action, "APPROVE_CRITIC")
        self.assertEqual(
            decision.transition.reason_code, "ZHONGSHU_FREEZE_WITH_FOLLOWUPS"
        )
        rows = self._rows_by_id(decision.update.review)
        self.assertEqual(rows["group-000001"].revision_round, 1)
        after = self._apply(context, decision)
        self.assertEqual(after.progression.state, "ZHONGSHU_FREEZE_CHECK")
        (finding,) = after.review.findings
        self.assertEqual(finding.status, "DEFERRED")

    def test_global_fingerprint_fuse_retired(self) -> None:
        # The blocker set is unchanged, but the group rows still carry budget
        # and the item-level exits do not fire: the run must not die in a
        # generic ZHONGSHU_NO_PROGRESS block while the group stall rule owns
        # convergence.
        findings = (
            _finding("f-1", item_id="item-000002", group_id="group-000001"),
        )
        context = _context(
            findings=findings,
            ledger=_ledger({"item-000001": "REJECTED"}),
            attempted=("item-000002",),
            fingerprint=blocker_fingerprint(findings),
            no_progress=3,
            max_no_progress=3,
        )
        decision = self.state.handle(context, _event("REQUEST_SOLVER_REVISION"))

        self.assertEqual(decision.transition.action, "REQUEST_SOLVER_REVISION")
        self.assertNotEqual(
            decision.update.recovery.blocked_reason, "ZHONGSHU_NO_PROGRESS"
        )
        rows = self._rows_by_id(decision.update.review)
        self.assertEqual(rows["group-000001"].revision_round, 1)

    def test_revision_budget_group_scoped(self) -> None:
        # The global counter is spent (8/8), but only group-000001 burned its
        # group budget; group-000002 still has rounds left, so the revision
        # must proceed instead of blocking the whole graph.
        context = _context(
            findings=(
                _finding("f-1", item_id="item-000003", group_id="group-000002"),
            ),
            rows=(
                _row(
                    "group-000001",
                    stage="STALLED",
                    revision_round=5,
                    stalled_rounds=2,
                    last_open_count=1,
                ),
                _row("group-000002", revision_round=2, last_open_count=1),
                _row("group-000003"),
            ),
            attempted=("item-000003",),
            revision_round=8,
            max_revision_rounds=8,
        )
        decision = self.state.handle(context, _event("REQUEST_SOLVER_REVISION"))

        self.assertEqual(decision.transition.action, "REQUEST_SOLVER_REVISION")
        self.assertNotEqual(
            decision.update.recovery.blocked_reason,
            "ZHONGSHU_REVISION_BUDGET_EXHAUSTED",
        )
        rows = self._rows_by_id(decision.update.review)
        self.assertEqual(rows["group-000002"].revision_round, 3)


if __name__ == "__main__":
    unittest.main()
