"""ZhongshuGroupState data basis (implementation plan Task 1).

Covers seeding, dict round-trip, legacy-snapshot compatibility, and
ReviewUpdate/DTO propagation for the per-group Zhongshu convergence rows.
"""

from __future__ import annotations

import unittest

from orchestrator.domain.context import (
    ProgressState,
    ReviewState,
    ReviewTaskGroup,
    ReviewTaskItem,
    ReviewUpdate,
    TaskIdentity,
    WorkflowContext,
    ZhongshuGroupState,
    apply_review_update,
    context_from_dto,
    context_to_dto,
)


def _review(**overrides) -> ReviewState:
    items = (
        ReviewTaskItem(item_id="item-000001", group_id="group-000001", order=1),
        ReviewTaskItem(item_id="item-000002", group_id="group-000001", order=2),
        ReviewTaskItem(item_id="item-000003", group_id="group-000002", order=3),
    )
    groups = (
        ReviewTaskGroup(
            group_id="group-000001",
            item_ids=("item-000001", "item-000002"),
            order=1,
        ),
        ReviewTaskGroup(group_id="group-000002", item_ids=("item-000003",), order=2),
    )
    base: dict[str, object] = {
        "revision_id": "R1",
        "task_items": items,
        "task_groups": groups,
    }
    base.update(overrides)
    return ReviewState(**base)


def _context(review: ReviewState) -> WorkflowContext:
    return WorkflowContext(
        identity=TaskIdentity(
            task_id="task-1", issue_id="issue-1", project="p", request_id="req-1"
        ),
        progression=ProgressState(
            state="ZHONGSHU_CRITIC", sequence=1, entered_at="2026-09-24T00:00:00Z"
        ),
        review=review,
    )


class SeedZhongshuGroupsTests(unittest.TestCase):
    def test_covers_every_task_group(self):
        rows = _review().seed_zhongshu_groups()
        self.assertEqual(
            [row.group_id for row in rows],
            ["group-000001", "group-000002"],
        )
        self.assertTrue(all(row.stage == "REVIEWING" for row in rows))
        self.assertTrue(all(row.revision_round == 0 for row in rows))

    def test_existing_rows_are_kept(self):
        parked = ZhongshuGroupState(
            group_id="group-000001", stage="STALLED", revision_round=3
        )
        rows = _review(zhongshu_groups=(parked,)).seed_zhongshu_groups()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].stage, "STALLED")
        self.assertEqual(rows[0].revision_round, 3)
        self.assertEqual(rows[1].group_id, "group-000002")
        self.assertEqual(rows[1].stage, "REVIEWING")


class ZhongshuGroupStateRoundTripTests(unittest.TestCase):
    def test_full_field_round_trip(self):
        row = ZhongshuGroupState(
            group_id="group-000001",
            stage="STALLED",
            revision_round=4,
            last_open_count=2,
            stalled_rounds=2,
            last_verdict="REQUEST_SOLVER_REVISION",
            doc_version=3,
            doc_hash="hash-1",
            doc_markdown="# doc",
            doc_source_hash="hash-2",
        )
        self.assertEqual(ZhongshuGroupState.from_dict(row.to_dict()), row)

    def test_defaults_and_int_coercion(self):
        row = ZhongshuGroupState.from_dict(
            {"group_id": "group-000001", "revision_round": "7"}
        )
        self.assertEqual(row.stage, "REVIEWING")
        self.assertEqual(row.revision_round, 7)
        self.assertEqual(row.stalled_rounds, 0)

    def test_missing_group_id_raises(self):
        with self.assertRaises(ValueError):
            ZhongshuGroupState.from_dict({"stage": "REVIEWING"})


class SerializationTests(unittest.TestCase):
    def test_review_update_propagates_rows(self):
        rows = (ZhongshuGroupState(group_id="group-000001", revision_round=1),)
        updated = apply_review_update(
            _review(), ReviewUpdate(revision_id="R1", zhongshu_groups=rows)
        )
        self.assertEqual(updated.zhongshu_groups, rows)

    def test_update_none_keeps_current(self):
        rows = (ZhongshuGroupState(group_id="group-000001"),)
        updated = apply_review_update(
            _review(zhongshu_groups=rows), ReviewUpdate(revision_id="R1")
        )
        self.assertEqual(updated.zhongshu_groups, rows)

    def test_context_round_trip(self):
        rows = (ZhongshuGroupState(group_id="group-000001", doc_markdown="# g1"),)
        dto = context_to_dto(_context(_review(zhongshu_groups=rows)))
        self.assertEqual(dto["review"]["zhongshu_groups"][0]["doc_markdown"], "# g1")
        restored = context_from_dto(dto)
        self.assertEqual(restored.review.zhongshu_groups, rows)

    def test_legacy_snapshot_without_key(self):
        dto = context_to_dto(_context(_review()))
        del dto["review"]["zhongshu_groups"]
        restored = context_from_dto(dto)
        self.assertEqual(restored.review.zhongshu_groups, ())


if __name__ == "__main__":
    unittest.main()
