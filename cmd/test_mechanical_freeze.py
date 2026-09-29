"""Mechanical freeze (2026-09-28 efficiency plan Task 1).

When every group row is terminal with a fresh approval ratchet, the item
ledger closes on current review-surface hashes, and the dependency graph is
sane, the freeze-check agent hop is replaced by a synthesized FREEZE_APPROVED
event.  The real release semantics stay with ``_group_freeze_decision``
(ready computation + document verification), so the precheck may be looser.
"""

from __future__ import annotations

import os
os.environ.setdefault("NEXUS_TEST_NO_EXTERNAL_NOTIFICATIONS", "1")
from dataclasses import replace
import unittest

from orchestrator.domain.context import (
    ParallelState,
    ProgressState,
    ReviewState,
    ReviewTaskGroup,
    ReviewTaskItem,
    ReviewTaskRecord,
    TaskIdentity,
    WorkflowContext,
    ZhongshuGroupState,
    ZhongshuParallelLimits,
)
from orchestrator.domain.events import DomainEvent
from orchestrator.domain.policies.zhongshu_group import (
    apply_group_verdict,
    current_member_hashes,
)
from orchestrator.domain.states import (
    ZhongshuFreezeCheckState,
    mechanical_freeze_ready,
)
from orchestrator.runtime.reducer import LinearContextReducer
from orchestrator.runtime.repository import WorkflowSnapshot


SECTIONS = (
    ("背景", "任务来自 issue 需求。"),
    ("目标", "输出 A 与 B。"),
    ("标识与范围", "本组覆盖列出的成员任务。"),
    ("状态与边界语义", "仅在依赖组冻结后生效。"),
    ("行为要求", "必须返回结构化结果。"),
    ("责任边界", "Solver 撰写，Critic 裁决。"),
    ("交叉不变量", "依赖组冻结前本组不得冻结。"),
    ("验收标准", ""),
    ("非目标", "不包含部署。"),
)


def _doc_markdown(group_id: str, item_ids: tuple[str, ...]) -> str:
    lines = [f"# {group_id} 需求文档 [v1]"]
    for number, (name, body) in enumerate(SECTIONS, start=1):
        if number == 8:
            body = "\n".join(
                line
                for item_id in item_ids
                for line in (f"### {item_id}", f"{item_id} result is observable")
            )
        lines.append(f"## {number}. {name}")
        if body:
            lines.append(body)
    return "\n".join(lines)


def _items() -> tuple[ReviewTaskItem, ...]:
    return (
        ReviewTaskItem(
            item_id="item-000001",
            group_id="group-000001",
            order=1,
            title="A",
            objective="obj A",
            acceptance_signals=("A is observable",),
        ),
        ReviewTaskItem(
            item_id="item-000002",
            group_id="group-000001",
            order=2,
            title="B",
            objective="obj B",
            acceptance_signals=("B is observable",),
        ),
        ReviewTaskItem(
            item_id="item-000003",
            group_id="group-000002",
            order=3,
            title="C",
            objective="obj C",
            dependencies=("item-000001",),
            acceptance_signals=("C is observable",),
        ),
    )


def _groups() -> tuple[ReviewTaskGroup, ...]:
    return (
        ReviewTaskGroup(
            group_id="group-000001",
            item_ids=("item-000001", "item-000002"),
            order=1,
        ),
        ReviewTaskGroup(group_id="group-000002", item_ids=("item-000003",), order=2),
    )


def _converged_review(
    items: tuple[ReviewTaskItem, ...] | None = None,
) -> ReviewState:
    """A group round folded through the production verdict path."""

    review = ReviewState(
        revision_id="R1",
        task_items=items or _items(),
        task_groups=_groups(),
    )
    ledger: tuple[ReviewTaskRecord, ...] = ()
    rows: tuple[ZhongshuGroupState, ...] = review.seed_zhongshu_groups()
    for group_id in ("group-000001", "group-000002"):
        view = replace(review, task_review_ledger=ledger, zhongshu_groups=rows)
        ledger, rows = apply_group_verdict(
            view,
            group_id,
            "APPROVE_GROUP",
            member_hashes=current_member_hashes(view, group_id),
        )
    return replace(review, task_review_ledger=ledger, zhongshu_groups=rows)


def _context(
    review: ReviewState | None = None,
    **zhongshu_kwargs: object,
) -> WorkflowContext:
    return WorkflowContext(
        identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
        progression=ProgressState(
            "ZHONGSHU_FREEZE_CHECK", 6, "2026-09-13T00:00:00Z"
        ),
        parallel=ParallelState(zhongshu=ZhongshuParallelLimits(**zhongshu_kwargs)),
        review=review or ReviewState(revision_id="R1"),
    )


def _event(action: str) -> DomainEvent:
    return DomainEvent(
        "NODE_COMPLETED",
        "task-1",
        6,
        {"action": action},
        "2026-09-13T00:00:00Z",
    )


def _dispatch(context: WorkflowContext, review: ReviewState):
    return ZhongshuFreezeCheckState()._dispatch_effect(
        context,
        "ZHONGSHU_FREEZE_CHECK",
        revision_id=review.revision_id,
        effective_review=review,
    )


class MechanicalFreezeReadyTests(unittest.TestCase):
    def test_fully_converged_group_round_is_ready(self) -> None:
        review = _converged_review()
        ready, failures = mechanical_freeze_ready(_context(review))
        self.assertTrue(ready, failures)
        self.assertEqual(failures, [])

    def test_frozen_rows_are_terminal(self) -> None:
        review = _converged_review()
        review = replace(
            review,
            zhongshu_groups=tuple(
                replace(row, stage="FROZEN") for row in review.zhongshu_groups
            ),
        )
        ready, failures = mechanical_freeze_ready(_context(review))
        self.assertTrue(ready, failures)

    def test_reviewing_row_blocks(self) -> None:
        review = _converged_review()
        review = replace(
            review,
            zhongshu_groups=(
                replace(review.zhongshu_groups[0], stage="REVIEWING"),
                review.zhongshu_groups[1],
            ),
        )
        ready, failures = mechanical_freeze_ready(_context(review))
        self.assertFalse(ready)
        self.assertTrue(
            any(item.startswith("GROUP_NOT_TERMINAL:group-000001:REVIEWING")
                for item in failures),
            failures,
        )

    def test_broken_ratchet_blocks_converged_row(self) -> None:
        review = _converged_review()
        review = replace(
            review,
            zhongshu_groups=(
                replace(
                    review.zhongshu_groups[0], approved_surface_hash="stale-hash"
                ),
                review.zhongshu_groups[1],
            ),
        )
        ready, failures = mechanical_freeze_ready(_context(review))
        self.assertFalse(ready)
        self.assertTrue(
            any(item.startswith("GROUP_NOT_TERMINAL:group-000001:CONVERGED")
                for item in failures),
            failures,
        )

    def test_stale_ledger_task_hash_blocks(self) -> None:
        review = _converged_review()
        review = replace(
            review,
            task_review_ledger=tuple(
                replace(record, task_hash="stale")
                if record.item_id == "item-000002"
                else record
                for record in review.task_review_ledger
            ),
        )
        ready, failures = mechanical_freeze_ready(_context(review))
        self.assertFalse(ready)
        self.assertIn("LEDGER_STALE:item-000002", failures)

    def test_missing_ledger_record_blocks(self) -> None:
        review = _converged_review()
        review = replace(
            review,
            task_review_ledger=tuple(
                record
                for record in review.task_review_ledger
                if record.item_id != "item-000003"
            ),
        )
        ready, failures = mechanical_freeze_ready(_context(review))
        self.assertFalse(ready)
        self.assertIn("LEDGER_STALE:item-000003", failures)

    def test_unapproved_ledger_record_blocks(self) -> None:
        review = _converged_review()
        review = replace(
            review,
            task_review_ledger=tuple(
                replace(record, status="CHANGES_REQUIRED")
                if record.item_id == "item-000001"
                else record
                for record in review.task_review_ledger
            ),
        )
        ready, failures = mechanical_freeze_ready(_context(review))
        self.assertFalse(ready)
        self.assertIn("LEDGER_STALE:item-000001", failures)

    def test_ungrouped_item_blocks(self) -> None:
        items = _items() + (
            ReviewTaskItem(item_id="item-000009", group_id="group-000009"),
        )
        review = _converged_review(items=items)
        ready, failures = mechanical_freeze_ready(_context(review))
        self.assertFalse(ready)
        self.assertIn("ITEM_UNGROUPED:item-000009", failures)

    def test_dependency_cycle_blocks(self) -> None:
        items = (
            replace(_items()[0], dependencies=("item-000003",)),
            _items()[1],
            _items()[2],
        )
        review = _converged_review(items=items)
        ready, failures = mechanical_freeze_ready(_context(review))
        self.assertFalse(ready)
        self.assertTrue(
            any(item.startswith("DEPENDENCY_CYCLE:") for item in failures),
            failures,
        )

    def test_unresolved_dependency_blocks(self) -> None:
        items = (
            _items()[0],
            _items()[1],
            replace(_items()[2], dependencies=("item-000999",)),
        )
        review = _converged_review(items=items)
        ready, failures = mechanical_freeze_ready(_context(review))
        self.assertFalse(ready)
        self.assertIn("DEPENDENCY_UNRESOLVED:item-000003:item-000999", failures)

    def test_non_group_round_never_qualifies(self) -> None:
        ready, failures = mechanical_freeze_ready(_context(None))
        self.assertFalse(ready)
        self.assertEqual(failures, ["NON_GROUP_ROUND"])
        ready, failures = mechanical_freeze_ready(_context(ReviewState(revision_id="R1")))
        self.assertFalse(ready)
        self.assertEqual(failures, ["NON_GROUP_ROUND"])


class MechanicalDispatchTests(unittest.TestCase):
    def test_ready_round_synththesizes_freeze_approved_effect(self) -> None:
        review = _converged_review()
        effect = _dispatch(_context(review), review)
        self.assertEqual(effect.effect_type, "mechanical_freeze")
        self.assertTrue(effect.effect_id.startswith("mechanical-freeze:"))
        self.assertEqual(effect.payload.get("action"), "FREEZE_APPROVED")
        self.assertEqual(effect.payload.get("target_state"), "ZHONGSHU_FREEZE_CHECK")
        self.assertEqual(effect.payload.get("revision_id"), "R1")

    def test_failed_precheck_falls_back_to_agent_with_note(self) -> None:
        review = replace(
            _converged_review(),
            zhongshu_groups=(
                replace(_converged_review().zhongshu_groups[0], stage="REVIEWING"),
                _converged_review().zhongshu_groups[1],
            ),
        )
        effect = _dispatch(_context(review), review)
        self.assertEqual(effect.effect_type, "agent_dispatch")
        prompt = str(effect.payload.get("prompt_ref") or "")
        self.assertIn("[Freeze precheck]", prompt)
        self.assertIn("GROUP_NOT_TERMINAL:group-000001:REVIEWING", prompt)

    def test_disabled_flag_keeps_plain_agent_dispatch(self) -> None:
        review = _converged_review()
        effect = _dispatch(_context(review, mechanical_freeze=False), review)
        self.assertEqual(effect.effect_type, "agent_dispatch")
        self.assertNotIn("[Freeze precheck]", str(effect.payload.get("prompt_ref") or ""))

    def test_legacy_non_group_round_is_untouched(self) -> None:
        review = ReviewState(revision_id="R1")
        effect = _dispatch(_context(review), review)
        self.assertEqual(effect.effect_type, "agent_dispatch")
        self.assertNotIn("[Freeze precheck]", str(effect.payload.get("prompt_ref") or ""))

    def test_fast_track_takes_precedence(self) -> None:
        review = _converged_review()
        effect = _dispatch(_context(review, fast_track=True), review)
        self.assertEqual(effect.effect_type, "fast_track")
        self.assertEqual(effect.payload.get("action"), "FREEZE_APPROVED")


class MechanicalFreezeFoldTests(unittest.TestCase):
    def _docs_attached(self, review: ReviewState) -> ReviewState:
        return replace(
            review,
            zhongshu_groups=tuple(
                replace(
                    row,
                    doc_version=1,
                    doc_markdown=_doc_markdown(
                        row.group_id,
                        tuple(
                            item.item_id
                            for item in review.task_items
                            if item.group_id == row.group_id
                        ),
                    ),
                )
                for row in review.zhongshu_groups
            ),
        )

    def test_synthesized_event_releases_flat_graph_in_one_stroke(self) -> None:
        items = (
            _items()[0],
            _items()[1],
            replace(_items()[2], dependencies=()),
        )
        review = self._docs_attached(_converged_review(items=items))
        context = _context(review, plan_review_gate=False)
        decision = ZhongshuFreezeCheckState().handle(
            context, _event("FREEZE_APPROVED")
        )
        self.assertEqual(decision.transition.action, "FREEZE_APPROVED")
        rows = {
            row.group_id: row
            for row in (decision.update.review.zhongshu_groups or ())
        }
        self.assertEqual({row.stage for row in rows.values()}, {"FROZEN"})
        after = LinearContextReducer().apply(
            WorkflowSnapshot("task-1", context, 0), decision
        )
        self.assertEqual(after.context.progression.state, "MENXIA_GROUP_SOLVER")

    def test_layered_graph_freezes_only_the_ready_batch(self) -> None:
        # group-000002 depends on group-000001: the first release freezes the
        # dependency root only; _group_freeze_decision keeps the rest grinding
        # exactly as the agent path would (defense in depth, not a shortcut).
        review = self._docs_attached(_converged_review())
        context = _context(review, plan_review_gate=False)
        decision = ZhongshuFreezeCheckState().handle(
            context, _event("FREEZE_APPROVED")
        )
        self.assertEqual(decision.transition.action, "FREEZE_APPROVED")
        rows = {
            row.group_id: row
            for row in (decision.update.review.zhongshu_groups or ())
        }
        self.assertEqual(rows["group-000001"].stage, "FROZEN")
        self.assertEqual(rows["group-000002"].stage, "CONVERGED")


if __name__ == "__main__":
    unittest.main()
