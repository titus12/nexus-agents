from __future__ import annotations

import unittest

from orchestrator.domain.context import (
    MenxiaGroupState,
    MenxiaParallelLimits,
    ReviewState,
    ReviewTaskGroup,
    ReviewTaskItem,
)
from orchestrator.domain.errors import InvariantViolation
from orchestrator.domain.policies.menxia_group import (
    MENXIA_GROUP_STALL_LIMIT,
    apply_menxia_group_results,
    menxia_group_next_stage,
    menxia_group_stage_census,
    menxia_group_wave_action,
    menxia_groups_have_blockers,
    ready_stage_groups,
)


def _review(
    *,
    groups: tuple[ReviewTaskGroup, ...] = (
        ReviewTaskGroup(group_id="group-000001", item_ids=("item-1", "item-2"), order=0),
        ReviewTaskGroup(group_id="group-000002", item_ids=("item-3",), order=1),
    ),
    items: tuple[ReviewTaskItem, ...] = (
        ReviewTaskItem(item_id="item-1", group_id="group-000001", order=0),
        ReviewTaskItem(item_id="item-2", group_id="group-000001", order=1),
        ReviewTaskItem(item_id="item-3", group_id="group-000002", order=2),
    ),
    menxia_groups: tuple[MenxiaGroupState, ...] = (),
    completed: tuple[str, ...] = (),
) -> ReviewState:
    return ReviewState(
        revision_id="rev-1",
        task_groups=groups,
        task_items=items,
        menxia_groups=menxia_groups,
        completed_item_ids=completed,
    )


def _result(group_id: str, action: str, **extra) -> dict[str, object]:
    return {"group_id": group_id, "action": action, **extra}


class TestStageMapping(unittest.TestCase):
    def test_action_mapping(self) -> None:
        self.assertEqual(menxia_group_next_stage("FEASIBLE"), "ANALYZING")
        self.assertEqual(menxia_group_next_stage("READY_FOR_CRITIC"), "REVIEWING")
        self.assertEqual(menxia_group_next_stage("APPROVE_GROUP"), "APPROVED")
        self.assertEqual(menxia_group_next_stage("REQUEST_EVIDENCE"), "ANALYZING")
        self.assertEqual(menxia_group_next_stage("REQUEST_SOLVER_REVISION"), "SOLVING")
        self.assertIsNone(menxia_group_next_stage("HUMAN_GATE"))
        self.assertIsNone(menxia_group_next_stage("UNKNOWN_ACTION"))


class TestReadyStageGroups(unittest.TestCase):
    def test_seeds_and_selects_by_stage(self) -> None:
        review = _review()
        selected = ready_stage_groups(review, target="MENXIA_GROUP_SOLVER")
        self.assertEqual(
            [row.group_id for row in selected],
            ["group-000001", "group-000002"],
        )
        self.assertEqual(selected[0].stage, "SOLVING")

    def test_unknown_target_is_rejected(self) -> None:
        with self.assertRaises(InvariantViolation):
            ready_stage_groups(_review(), target="MENXIA_ITEM_SOLVER")

    def test_limits_cap_concurrent_groups(self) -> None:
        review = _review()
        limits = MenxiaParallelLimits(enabled=True, max_concurrent_groups=1)
        selected = ready_stage_groups(
            review, target="MENXIA_GROUP_SOLVER", limits=limits
        )
        self.assertEqual([row.group_id for row in selected], ["group-000001"])

    def test_completed_groups_are_not_seeded(self) -> None:
        review = _review(completed=("item-1", "item-2", "item-3"))
        self.assertEqual(
            ready_stage_groups(review, target="MENXIA_GROUP_SOLVER"), ()
        )


class TestApplyGroupResults(unittest.TestCase):
    def test_forward_action_moves_stage_and_doc_chain(self) -> None:
        review = _review()
        rows = apply_menxia_group_results(
            review,
            [_result("group-000001", "FEASIBLE", doc_version=2, doc_hash="abc")],
            max_rounds=5,
        )
        row = next(r for r in rows if r.group_id == "group-000001")
        self.assertEqual(row.stage, "ANALYZING")
        self.assertEqual(row.doc_version, 2)
        self.assertEqual(row.doc_hash, "abc")
        self.assertEqual(row.revision_round, 0)

    def test_revision_demand_consumes_budget_then_escalates(self) -> None:
        review = _review()
        rows = review.menxia_groups
        for round_index in range(5):
            rows = apply_menxia_group_results(
                _review(menxia_groups=rows),
                [_result(
                    "group-000001",
                    "REQUEST_SOLVER_REVISION",
                    open_count=max(0, 4 - round_index),
                )],
                max_rounds=5,
            )
            row = next(r for r in rows if r.group_id == "group-000001")
            self.assertEqual(row.stage, "SOLVING" if round_index < 4 else "ESCALATED")
        self.assertEqual(row.stage, "ESCALATED")
        self.assertEqual(row.revision_round, 5)

    def test_budget_reset_unparks_with_fresh_budget(self) -> None:
        review = _review()
        rows = review.menxia_groups
        for _ in range(5):
            rows = apply_menxia_group_results(
                _review(menxia_groups=rows),
                [_result("group-000001", "REQUEST_SOLVER_REVISION", open_count=3)],
                max_rounds=5,
            )
        reset = apply_menxia_group_results(
            _review(menxia_groups=rows),
            [_result("group-000001", "REQUEST_SOLVER_REVISION", budget_reset=True, open_count=3)],
            max_rounds=5,
        )
        row = next(r for r in reset if r.group_id == "group-000001")
        self.assertEqual(row.stage, "SOLVING")
        self.assertEqual(row.revision_round, 0)
        self.assertEqual(row.stalled_rounds, 0)

    def test_open_count_shrink_resets_stall(self) -> None:
        review = _review()
        rows = apply_menxia_group_results(
            review,
            [_result("group-000001", "REQUEST_SOLVER_REVISION", open_count=4)],
            max_rounds=5,
        )
        rows = apply_menxia_group_results(
            _review(menxia_groups=rows),
            [_result("group-000001", "REQUEST_SOLVER_REVISION", open_count=1)],
            max_rounds=5,
        )
        row = next(r for r in rows if r.group_id == "group-000001")
        self.assertEqual(row.stage, "SOLVING")
        self.assertEqual(row.stalled_rounds, 0)

    def test_stall_rule_escalates_without_budget_exhaustion(self) -> None:
        review = _review()
        rows = apply_menxia_group_results(
            review,
            [_result("group-000001", "REQUEST_SOLVER_REVISION", open_count=4)],
            max_rounds=5,
        )
        for _ in range(MENXIA_GROUP_STALL_LIMIT):
            rows = apply_menxia_group_results(
                _review(menxia_groups=rows),
                [_result("group-000001", "REQUEST_SOLVER_REVISION", open_count=4)],
                max_rounds=5,
            )
        row = next(r for r in rows if r.group_id == "group-000001")
        self.assertEqual(row.stage, "ESCALATED")
        self.assertEqual(row.revision_round, 3)
        self.assertLess(row.revision_round, 5)

    def test_first_revision_seeds_baseline_without_stalling(self) -> None:
        review = _review()
        rows = apply_menxia_group_results(
            review,
            [_result("group-000001", "REQUEST_SOLVER_REVISION", open_count=4)],
            max_rounds=5,
        )
        row = next(r for r in rows if r.group_id == "group-000001")
        self.assertEqual(row.stage, "SOLVING")
        self.assertEqual(row.stalled_rounds, 0)
        self.assertEqual(row.last_open_count, 4)

    def test_approved_is_terminal_without_reset(self) -> None:
        review = _review()
        rows = apply_menxia_group_results(
            review,
            [_result("group-000001", "APPROVE_GROUP", doc_version=3)],
            max_rounds=5,
        )
        rows = apply_menxia_group_results(
            _review(menxia_groups=rows),
            [_result("group-000001", "FEASIBLE")],
            max_rounds=5,
        )
        row = next(r for r in rows if r.group_id == "group-000001")
        self.assertEqual(row.stage, "APPROVED")

    def test_blocked_records_reason(self) -> None:
        review = _review()
        rows = apply_menxia_group_results(
            review,
            [_result("group-000001", "BLOCKED", blocked_reason="需求缺失")],
            max_rounds=5,
        )
        row = next(r for r in rows if r.group_id == "group-000001")
        self.assertEqual(row.stage, "BLOCKED")
        self.assertEqual(row.blocked_reason, "需求缺失")

    def test_unknown_group_is_rejected(self) -> None:
        with self.assertRaises(InvariantViolation):
            apply_menxia_group_results(
                _review(), [_result("group-999999", "FEASIBLE")], max_rounds=5
            )

    def test_row_order_follows_seed_order(self) -> None:
        rows = apply_menxia_group_results(
            _review(),
            [
                _result("group-000002", "FEASIBLE"),
                _result("group-000001", "FEASIBLE"),
            ],
            max_rounds=5,
        )
        self.assertEqual(
            [row.group_id for row in rows],
            ["group-000001", "group-000002"],
        )


class TestWaveReduction(unittest.TestCase):
    def test_solver_wave_routes_to_earliest_stage(self) -> None:
        self.assertEqual(
            menxia_group_wave_action(
                "MENXIA_GROUP_SOLVER",
                result_actions={"group-000001": "FEASIBLE"},
                census={"ANALYZING": 1},
            ),
            "READY_FOR_ANALYST",
        )
        self.assertEqual(
            menxia_group_wave_action(
                "MENXIA_GROUP_SOLVER",
                result_actions={"group-000001": "READY_FOR_CRITIC"},
                census={"REVIEWING": 1},
            ),
            "READY_FOR_CRITIC",
        )

    def test_analyst_wave_demands_more_evidence_when_solving(self) -> None:
        self.assertEqual(
            menxia_group_wave_action(
                "MENXIA_GROUP_ANALYST",
                result_actions={},
                census={"SOLVING": 1, "ANALYZING": 1},
            ),
            "NEEDS_MORE_EVIDENCE",
        )

    def test_critic_wave_can_route_back_to_analyst(self) -> None:
        self.assertEqual(
            menxia_group_wave_action(
                "MENXIA_GROUP_CRITIC",
                result_actions={},
                census={"ANALYZING": 1},
            ),
            "REQUEST_EVIDENCE",
        )

    def test_human_gate_wins(self) -> None:
        self.assertEqual(
            menxia_group_wave_action(
                "MENXIA_GROUP_CRITIC",
                result_actions={"group-000001": "HUMAN_GATE"},
                census={"SOLVING": 1},
            ),
            "HUMAN_GATE",
        )

    def test_unknown_state_is_rejected(self) -> None:
        with self.assertRaises(InvariantViolation):
            menxia_group_wave_action(
                "MENXIA_GROUP_GATE", result_actions={}, census={}
            )


class TestCensusAndBlockers(unittest.TestCase):
    def test_census_counts_stages(self) -> None:
        rows = (
            MenxiaGroupState(group_id="g1", stage="SOLVING"),
            MenxiaGroupState(group_id="g2", stage="SOLVING"),
            MenxiaGroupState(group_id="g3", stage="APPROVED"),
        )
        self.assertEqual(
            menxia_group_stage_census(rows), {"SOLVING": 2, "APPROVED": 1}
        )

    def test_blockers_detect_parked_rows(self) -> None:
        self.assertTrue(
            menxia_groups_have_blockers(
                (MenxiaGroupState(group_id="g1", stage="ESCALATED"),)
            )
        )
        self.assertFalse(
            menxia_groups_have_blockers(
                (MenxiaGroupState(group_id="g1", stage="SOLVING"),)
            )
        )


if __name__ == "__main__":
    unittest.main()
