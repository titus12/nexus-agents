"""Zhongshu group convergence policy (implementation plan Task 2).

Pure-function tests for the group-level fold: scoped budget consumption,
convergence recompute, the stall rule mirrored from the Menxia group
pipeline, freeze readiness across group dependencies, drainout detection,
and out-of-scope reconvergence without budget.
"""

from __future__ import annotations

import unittest

from orchestrator.domain.context import (
    ReviewState,
    ReviewTaskGroup,
    ReviewTaskItem,
    ReviewTaskRecord,
    ZhongshuGroupState,
)
from orchestrator.domain.findings import Finding
from orchestrator.domain.policies.zhongshu_group import (
    ZHONGSHU_GROUP_STALL_LIMIT,
    apply_zhongshu_round,
    freeze_ready_groups,
    zhongshu_drainout_parked,
)


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


def _review(
    *,
    findings: tuple[Finding, ...] = (),
    ledger: tuple[ReviewTaskRecord, ...] = (),
    rows: tuple[ZhongshuGroupState, ...] = (),
) -> ReviewState:
    return ReviewState(
        revision_id="R1",
        task_items=_items(),
        task_groups=_groups(),
        findings=findings,
        task_review_ledger=ledger,
        zhongshu_groups=rows,
    )


def _open_blockers(count: int, item_id: str = "item-000001") -> tuple[Finding, ...]:
    return tuple(
        Finding(
            finding_id=f"finding-{index:06d}",
            severity="P0",
            status="OPEN",
            item_id=item_id,
            group_id="group-000001",
        )
        for index in range(count)
    )


def _approved_ledger(item_ids: tuple[str, ...]) -> tuple[ReviewTaskRecord, ...]:
    return tuple(
        ReviewTaskRecord(item_id=item_id, status="APPROVED")
        for item_id in item_ids
    )


class ApplyRoundTests(unittest.TestCase):
    def test_bumps_only_scope_groups(self):
        rows = apply_zhongshu_round(
            _review(),
            attempted_item_ids=("item-000001", "item-000003"),
            max_rounds=5,
        )
        by_id = {row.group_id: row for row in rows}
        self.assertEqual(by_id["group-000001"].revision_round, 1)
        self.assertEqual(by_id["group-000002"].revision_round, 1)
        self.assertEqual(by_id["group-000003"].revision_round, 0)

    def test_converged_group_skips_budget(self):
        review = _review(
            ledger=_approved_ledger(("item-000001", "item-000002")),
        )
        rows = apply_zhongshu_round(
            review,
            attempted_item_ids=("item-000001",),
            max_rounds=5,
        )
        by_id = {row.group_id: row for row in rows}
        self.assertEqual(by_id["group-000001"].stage, "CONVERGED")
        self.assertEqual(by_id["group-000001"].revision_round, 0)
        self.assertEqual(by_id["group-000002"].revision_round, 0)

    def test_out_of_scope_group_reconverges_without_budget(self):
        review = _review(
            ledger=_approved_ledger(("item-000004",)),
            rows=(ZhongshuGroupState(group_id="group-000003", revision_round=2),),
        )
        rows = apply_zhongshu_round(
            review,
            attempted_item_ids=("item-000001", "item-000003"),
            max_rounds=5,
        )
        by_id = {row.group_id: row for row in rows}
        self.assertEqual(by_id["group-000003"].stage, "CONVERGED")
        self.assertEqual(by_id["group-000003"].revision_round, 2)
        self.assertEqual(by_id["group-000001"].revision_round, 1)


class StallRuleTests(unittest.TestCase):
    def test_first_revision_round_only_baseline(self):
        review = _review(findings=_open_blockers(3))
        rows = apply_zhongshu_round(
            review, attempted_item_ids=("item-000001",), max_rounds=5
        )
        row = rows[0]
        self.assertEqual(row.revision_round, 1)
        self.assertEqual(row.stalled_rounds, 0)
        self.assertEqual(row.stage, "REVIEWING")
        self.assertEqual(row.last_open_count, 3)

    def test_two_rounds_no_shrink_escalates(self):
        rows = ()
        review = _review(findings=_open_blockers(3))
        for expected_stage in ("REVIEWING", "REVIEWING", "STALLED"):
            if rows:
                review = _review_with_rows(review, rows)
            rows = apply_zhongshu_round(
                review, attempted_item_ids=("item-000001",), max_rounds=5
            )
            self.assertEqual(rows[0].stage, expected_stage)
        self.assertEqual(rows[0].stalled_rounds, ZHONGSHU_GROUP_STALL_LIMIT)
        self.assertEqual(rows[0].revision_round, 3)

    def test_open_count_drops_resets_counter(self):
        review = _review(findings=_open_blockers(3))
        rows = apply_zhongshu_round(
            review, attempted_item_ids=("item-000001",), max_rounds=5
        )
        self.assertEqual(rows[0].last_open_count, 3)
        shrunk = _review_with_rows(_review(findings=_open_blockers(1)), rows)
        rows = apply_zhongshu_round(
            shrunk,
            attempted_item_ids=("item-000001",),
            max_rounds=5,
        )
        self.assertEqual(rows[0].stalled_rounds, 0)

    def test_budget_exhausted_escalates_even_with_progress(self):
        counts = (4, 3, 2, 1, 0)
        rows = ()
        for index, count in enumerate(counts):
            review = _review(findings=_open_blockers(count))
            if rows:
                review = _review_with_rows(review, rows)
            rows = apply_zhongshu_round(
                review,
                attempted_item_ids=("item-000001",),
                max_rounds=5,
            )
            if index < len(counts) - 1:
                self.assertEqual(rows[0].stage, "REVIEWING")
        self.assertEqual(rows[0].stage, "STALLED")
        self.assertEqual(rows[0].revision_round, 5)


def _review_with_rows(
    review: ReviewState, rows: tuple[ZhongshuGroupState, ...]
) -> ReviewState:
    return ReviewState(
        revision_id=review.revision_id,
        task_items=review.task_items,
        task_groups=review.task_groups,
        findings=review.findings,
        task_review_ledger=review.task_review_ledger,
        zhongshu_groups=rows,
    )


class FreezeAndDrainoutTests(unittest.TestCase):
    def test_freeze_ready_requires_dependency_groups_frozen(self):
        converged_b = _review(
            ledger=_approved_ledger(("item-000003",)),
            rows=(
                ZhongshuGroupState(group_id="group-000001", stage="REVIEWING"),
                ZhongshuGroupState(group_id="group-000002", stage="CONVERGED"),
            ),
        )
        self.assertEqual(freeze_ready_groups(converged_b), ())
        frozen_a = _review(
            ledger=_approved_ledger(("item-000003",)),
            rows=(
                ZhongshuGroupState(group_id="group-000001", stage="FROZEN"),
                ZhongshuGroupState(group_id="group-000002", stage="CONVERGED"),
            ),
        )
        ready = freeze_ready_groups(frozen_a)
        self.assertEqual([row.group_id for row in ready], ["group-000002"])

    def test_drainout_all_remaining_parked_true(self):
        parked = _review(
            rows=(
                ZhongshuGroupState(group_id="group-000001", stage="STALLED"),
                ZhongshuGroupState(group_id="group-000002", stage="BLOCKED"),
                ZhongshuGroupState(group_id="group-000003", stage="STALLED"),
            ),
        )
        self.assertTrue(zhongshu_drainout_parked(parked))
        active = _review(
            rows=(
                ZhongshuGroupState(group_id="group-000001", stage="STALLED"),
                ZhongshuGroupState(group_id="group-000002", stage="BLOCKED"),
                ZhongshuGroupState(group_id="group-000003", stage="REVIEWING"),
            ),
        )
        self.assertFalse(zhongshu_drainout_parked(active))
        self.assertFalse(zhongshu_drainout_parked(_review()))


if __name__ == "__main__":
    unittest.main()
