"""Group freeze check and directed unstick (implementation plan Task 7/8).

The freeze-check state releases only groups the group pipeline says are
ready: CONVERGED with every dependency group FROZEN and a re-verified
document.  Ready groups freeze and hand over to Menxia in the same
decision (early hand-over: the wave dispatches only FROZEN groups while
the rest keep grinding in Zhongshu); form violations reject the freeze
with mechanical details; the global graph checks run once before the
first group freezes; and a human-gate resume can unstick named groups
with a fresh budget.
"""

from __future__ import annotations

import unittest

from orchestrator.domain.context import (
    MenxiaParallelLimits,
    ParallelState,
    ProgressState,
    ReviewState,
    ReviewTaskGroup,
    ReviewTaskItem,
    ReviewTaskRecord,
    ReviewUpdate,
    TaskIdentity,
    WorkflowContext,
    ZhongshuGroupState,
    apply_review_update,
)
from orchestrator.domain.events import DomainEvent
from orchestrator.domain.states import (
    StateRegistry,
    ZhongshuCriticState,
    ZhongshuSolverState,
)
from orchestrator.runtime.reducer import LinearContextReducer
from orchestrator.runtime.repository import WorkflowSnapshot


SECTIONS = (
    ("背景", "任务来自 issue 需求。"),
    ("目标", "输出 A 与 B。"),
    ("标识与范围", "group 覆盖 item。"),
    ("状态与边界语义", "仅在依赖组冻结后生效。"),
    ("行为要求", "必须返回结构化结果。"),
    ("责任边界", "Solver 撰写，Critic 裁决。"),
    ("交叉不变量", "依赖组冻结前本组不得冻结。"),
    ("验收标准", ""),
    ("非目标", "不包含部署。"),
)


def _doc_markdown(
    group_id: str,
    version: int = 1,
    acceptance_lines: tuple[str, ...] = (),
    drop_sections: tuple[int, ...] = (),
) -> str:
    lines = [f"# {group_id} 需求文档 [v{version}]"]
    for number, (name, body) in enumerate(SECTIONS, start=1):
        if number in drop_sections:
            continue
        if number == 8:
            body = "\n".join(acceptance_lines)
        lines.append(f"## {number}. {name}")
        if body:
            lines.append(body)
    return "\n".join(lines)


def _items(*, cycle: bool = False) -> tuple[ReviewTaskItem, ...]:
    b_deps: tuple[str, ...] = ("item-000001",)
    if cycle:
        return (
            ReviewTaskItem(
                item_id="item-000001",
                group_id="group-000001",
                order=1,
                dependencies=("item-000002",),
                acceptance_signals=("A is observable", "B is observable"),
            ),
            ReviewTaskItem(
                item_id="item-000002",
                group_id="group-000002",
                order=2,
                dependencies=b_deps,
                acceptance_signals=("C is observable",),
            ),
        )
    return (
        ReviewTaskItem(
            item_id="item-000001",
            group_id="group-000001",
            order=1,
            acceptance_signals=("A is observable", "B is observable"),
        ),
        ReviewTaskItem(
            item_id="item-000002",
            group_id="group-000002",
            order=2,
            dependencies=b_deps,
            acceptance_signals=("C is observable",),
        ),
    )


def _groups() -> tuple[ReviewTaskGroup, ...]:
    return (
        ReviewTaskGroup(group_id="group-000001", item_ids=("item-000001",), order=1),
        ReviewTaskGroup(group_id="group-000002", item_ids=("item-000002",), order=2),
    )


def _row(
    group_id: str,
    *,
    stage: str = "REVIEWING",
    revision_round: int = 0,
    stalled_rounds: int = 0,
    doc_version: int = 0,
    doc_markdown: str = "",
) -> ZhongshuGroupState:
    return ZhongshuGroupState(
        group_id=group_id,
        stage=stage,
        revision_round=revision_round,
        stalled_rounds=stalled_rounds,
        doc_version=doc_version,
        doc_markdown=doc_markdown,
    )


def _doc_row(group_id: str, stage: str, signals: tuple[str, ...]) -> ZhongshuGroupState:
    return _row(
        group_id,
        stage=stage,
        doc_version=1,
        doc_markdown=_doc_markdown(group_id, 1, signals),
    )


def _context(
    rows: tuple[ZhongshuGroupState, ...],
    *,
    cycle: bool = False,
    state: str = "ZHONGSHU_FREEZE_CHECK",
    ledger: tuple[ReviewTaskRecord, ...] = (),
) -> WorkflowContext:
    return WorkflowContext(
        identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
        progression=ProgressState(state, 8, "2026-09-24T00:00:00Z"),
        review=ReviewState(
            revision_id="task-1:ZHONGSHU_ANALYST:2",
            task_items=_items(cycle=cycle),
            task_groups=_groups(),
            task_review_ledger=ledger,
            zhongshu_groups=rows,
        ),
        parallel=ParallelState(
            menxia=MenxiaParallelLimits(
                enabled=True, max_concurrent_groups=2, max_concurrent_items=3
            )
        ),
    )


def _event(action: str, **extra) -> DomainEvent:
    return DomainEvent(
        "NODE_COMPLETED",
        "task-1",
        8,
        {"action": action, **extra},
        "2026-09-24T00:00:00Z",
    )


def _rows_by_id(review: ReviewState) -> dict[str, ZhongshuGroupState]:
    return {row.group_id: row for row in review.zhongshu_groups}


class FreezeGroupsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = StateRegistry.default()
        self.state = self.registry.get("ZHONGSHU_FREEZE_CHECK")
        self.reducer = LinearContextReducer()

    def _rows(self, review_update) -> dict[str, ZhongshuGroupState]:
        return {row.group_id: row for row in review_update.zhongshu_groups}

    def test_freeze_approval_marks_ready_groups_frozen(self) -> None:
        context = _context(
            (
                _doc_row(
                    "group-000001",
                    "CONVERGED",
                    ("A is observable", "B is observable"),
                ),
                _row("group-000002", stage="REVIEWING"),
            )
        )
        decision = self.state.handle(context, _event("FREEZE_APPROVED"))

        rows = self._rows(decision.update.review)
        self.assertEqual(rows["group-000001"].stage, "FROZEN")
        self.assertEqual(rows["group-000002"].stage, "REVIEWING")
        # Early hand-over: group-000001 enters Menxia now while group-000002
        # keeps converging in Zhongshu.
        self.assertEqual(decision.transition.action, "FREEZE_APPROVED")
        self.assertEqual(decision.effects[0].effect_type, "node_dispatch")
        self.assertEqual(
            [
                binding["group_id"]
                for binding in decision.effects[0].payload["bindings"]
            ],
            ["group-000001"],
        )

    def test_freeze_with_unfrozen_groups_stays_in_freeze_check(self) -> None:
        context = _context(
            (
                _doc_row(
                    "group-000001",
                    "CONVERGED",
                    ("A is observable", "B is observable"),
                ),
                _doc_row("group-000002", "CONVERGED", ("C is observable",)),
            )
        )
        decision = self.state.handle(context, _event("FREEZE_APPROVED"))

        # group-000002 depends on group-000001 being FROZEN, so only
        # group-000001 may enter Menxia this round; the decision still names
        # the remaining groups for observability.
        self.assertEqual(decision.transition.action, "FREEZE_APPROVED")
        self.assertTrue(decision.effects)
        payload = decision.effects[0].payload
        self.assertEqual(payload.get("state"), "MENXIA_GROUP_SOLVER")
        self.assertEqual(
            tuple(payload.get("zhongshu_remaining_groups") or ()),
            ("group-000002",),
        )
        rows = self._rows(decision.update.review)
        self.assertEqual(rows["group-000001"].stage, "FROZEN")
        self.assertEqual(rows["group-000002"].stage, "CONVERGED")
        self.assertEqual(
            [
                binding["group_id"]
                for binding in payload["bindings"]
            ],
            ["group-000001"],
        )

    def test_doc_form_violation_rejects_with_details(self) -> None:
        broken = _row(
            "group-000001",
            stage="CONVERGED",
            doc_version=1,
            doc_markdown=_doc_markdown(
                "group-000001",
                1,
                ("A is observable", "B is observable"),
                drop_sections=(8,),
            ),
        )
        context = _context(
            (broken, _row("group-000002", stage="REVIEWING"))
        )
        decision = self.state.handle(context, _event("FREEZE_APPROVED"))

        self.assertEqual(decision.transition.action, "FREEZE_REJECTED")
        self.assertEqual(
            decision.transition.reason_code, "ZHONGSHU_DOC_FORM_INVALID"
        )
        rejections = decision.effects[0].payload.get("zhongshu_freeze_rejections")
        by_group = {row["group_id"]: row for row in rejections}
        self.assertIn("group-000001", by_group)
        self.assertTrue(by_group["group-000001"]["violations"])
        rows = self._rows(decision.update.review)
        self.assertEqual(rows["group-000001"].stage, "REVIEWING")

    def test_dependency_not_frozen_rejects_freeze(self) -> None:
        context = _context(
            (
                _row("group-000001", stage="REVIEWING"),
                _doc_row("group-000002", "CONVERGED", ("C is observable",)),
            )
        )
        decision = self.state.handle(context, _event("FREEZE_APPROVED"))

        rows = self._rows(decision.update.review)
        self.assertEqual(rows["group-000002"].stage, "CONVERGED")
        self.assertEqual(rows["group-000001"].stage, "REVIEWING")
        self.assertEqual(decision.transition.action, "REQUEST_SOLVER_REVISION")

    def test_global_checks_run_once_before_first_freeze(self) -> None:
        # (a) A dependency cycle intercepts the first freeze approval before
        # any group is FROZEN.
        context = _context(
            (
                _doc_row(
                    "group-000001",
                    "CONVERGED",
                    ("A is observable", "B is observable"),
                ),
                _row("group-000002", stage="REVIEWING"),
            ),
            cycle=True,
        )
        decision = self.state.handle(context, _event("FREEZE_APPROVED"))

        self.assertEqual(decision.transition.action, "FREEZE_REJECTED")
        self.assertTrue(
            decision.effects[0].payload.get("zhongshu_global_issues")
        )
        rows = self._rows(decision.update.review)
        self.assertEqual(rows["group-000001"].stage, "CONVERGED")

        # (b) With a FROZEN row present the global graph check is not
        # repeated: the remaining ready group freezes and Menxia opens.
        context = _context(
            (
                _row("group-000001", stage="FROZEN", doc_version=1),
                _doc_row("group-000002", "CONVERGED", ("C is observable",)),
            ),
            cycle=True,
        )
        decision = self.state.handle(context, _event("FREEZE_APPROVED"))

        self.assertEqual(decision.transition.action, "FREEZE_APPROVED")
        rows = self._rows(decision.update.review)
        self.assertEqual(rows["group-000002"].stage, "FROZEN")
        payload = decision.effects[0].payload
        self.assertEqual(
            payload.get("state") or payload.get("target_state"),
            "MENXIA_GROUP_SOLVER",
        )

    def test_gate_reset_named_group_only(self) -> None:
        rows = (
            _row(
                "group-000001",
                stage="STALLED",
                revision_round=3,
                stalled_rounds=2,
            ),
            _row("group-000002", stage="REVIEWING", revision_round=2),
        )
        resets = {
            "zhongshu_group_resets": [
                {"group_id": "group-000001", "budget_reset": True}
            ]
        }

        # Critic resume: the reset survives the round fold (the group is in
        # scope and would otherwise bump to round 4).
        critic_context = WorkflowContext(
            identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
            progression=ProgressState("ZHONGSHU_CRITIC", 9, "2026-09-24T00:00:00Z"),
            review=ReviewState(
                revision_id="task-1:ZHONGSHU_ANALYST:2",
                task_items=_items(),
                task_groups=_groups(),
                zhongshu_groups=rows,
                attempted_item_ids=("item-000001",),
            ),
        )
        decision = ZhongshuCriticState().handle(
            critic_context, _event("REQUEST_SOLVER_REVISION", **resets)
        )
        critic_rows = self._rows(decision.update.review)
        self.assertEqual(critic_rows["group-000001"].stage, "REVIEWING")
        self.assertEqual(critic_rows["group-000001"].revision_round, 0)
        self.assertEqual(critic_rows["group-000001"].stalled_rounds, 0)
        self.assertEqual(critic_rows["group-000002"].revision_round, 2)

        # Solver resume: the reset persists with the same shape.
        solver_context = WorkflowContext(
            identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
            progression=ProgressState("ZHONGSHU_SOLVER", 9, "2026-09-24T00:00:00Z"),
            review=ReviewState(
                revision_id="task-1:ZHONGSHU_ANALYST:2",
                task_items=_items(),
                task_groups=_groups(),
                zhongshu_groups=rows,
            ),
        )
        decision = ZhongshuSolverState().handle(
            solver_context, _event("READY_FOR_CRITIC", **resets)
        )
        solver_rows = self._rows(decision.update.review)
        self.assertEqual(solver_rows["group-000001"].stage, "REVIEWING")
        self.assertEqual(solver_rows["group-000001"].revision_round, 0)
        self.assertEqual(solver_rows["group-000002"].revision_round, 2)

    def test_regroup_reseeds_group_rows(self) -> None:
        """A re-cut group set invalidates the convergence rows.

        The regroup fold clears ``zhongshu_groups`` and re-seeds the new cut:
        stale rows from the vanished cut never survive (whether the fold
        carries rows or not), convergence accounting restarts with a fresh
        budget, and requirement documents re-issued by the same fold keep
        their chain on the fresh rows.
        """

        stale = (
            _row(
                "group-000001",
                stage="STALLED",
                revision_round=5,
                stalled_rounds=2,
                doc_version=2,
                doc_markdown=_doc_markdown(
                    "group-000001", 2, ("A is observable", "B is observable")
                ),
            ),
            _doc_row("group-000002", "CONVERGED", ("C is observable",)),
        )
        context = _context(stale)
        merged_group = ReviewTaskGroup(
            group_id="group-000003",
            item_ids=("item-000001", "item-000002"),
            order=1,
        )

        # (a) A regroup fold that carries no rows re-seeds the new cut
        #     fresh: the stale books are cleared and the budget restarts.
        review_after = apply_review_update(
            context.review,
            ReviewUpdate(
                revision_id="task-1:ZHONGSHU_SOLVER:9",
                task_groups=(merged_group,),
            ),
        )
        rows = {row.group_id: row for row in review_after.zhongshu_groups}
        self.assertEqual(set(rows), {"group-000003"})
        row = rows["group-000003"]
        self.assertEqual(row.stage, "REVIEWING")
        self.assertEqual(row.revision_round, 0)
        self.assertEqual(row.stalled_rounds, 0)
        self.assertEqual(row.doc_version, 0)
        self.assertEqual(row.doc_markdown, "")

        # (b) The fold's own row merge (stale rows plus the re-issued
        #     document) lands on the fresh seed: stale ids vanish, the
        #     document chain survives, and the accounting still restarts.
        reissued = ZhongshuGroupState(
            group_id="group-000003",
            stage="STALLED",
            revision_round=4,
            stalled_rounds=2,
            doc_version=1,
            doc_markdown=_doc_markdown(
                "group-000003",
                1,
                ("A is observable", "B is observable", "C is observable"),
            ),
        )
        review_after = apply_review_update(
            context.review,
            ReviewUpdate(
                revision_id="task-1:ZHONGSHU_SOLVER:10",
                task_groups=(merged_group,),
                zhongshu_groups=(*stale, reissued),
            ),
        )
        rows = {row.group_id: row for row in review_after.zhongshu_groups}
        self.assertEqual(set(rows), {"group-000003"})
        row = rows["group-000003"]
        self.assertEqual(row.stage, "REVIEWING")
        self.assertEqual(row.revision_round, 0)
        self.assertEqual(row.stalled_rounds, 0)
        self.assertEqual(row.doc_version, 1)
        self.assertEqual(row.doc_markdown, reissued.doc_markdown)


if __name__ == "__main__":
    unittest.main()
