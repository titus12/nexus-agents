"""Solver revision freeze zone for group requirement docs (plan Task 4).

A revising Solver may only rewrite the requirement documents of the groups
its dictated batch actually touches; documents of every other group must be
submitted byte-identical or not at all, and frozen groups are never editable
even when the batch names their findings.
"""

from __future__ import annotations

import hashlib
import unittest

from orchestrator.domain.context import ReviewState, ZhongshuGroupState
from orchestrator.domain.findings import Finding
from orchestrator.domain.policies.solver_plan import (
    carry_forward_group_docs,
    is_retryable_solver_reply_error,
)
from orchestrator.domain.zhongshu.solver import (
    ReviseSolverLogic,
    SolverBatch,
    batch_group_scope,
)


def _finding(finding_id: str, group_id: str, item_id: str) -> Finding:
    return Finding(
        finding_id=finding_id,
        severity="P1",
        status="OPEN",
        group_id=group_id,
        item_id=item_id,
    )


def _doc(group_id: str, version: int) -> str:
    sections = (
        "背景", "目标", "标识与范围", "状态与边界语义", "行为要求",
        "责任边界", "交叉不变量", "验收标准", "非目标",
    )
    lines = [f"# {group_id} 需求文档 [v{version}]"]
    for number, name in enumerate(sections, start=1):
        lines.append(f"## {number}. {name}")
        if name == "背景":
            lines.append(f"{group_id} 的上下文。")
    return "\n".join(lines)


def _review(
    findings: tuple[Finding, ...],
    rows: tuple[ZhongshuGroupState, ...],
    ledger: tuple[tuple[str, str], ...] = (),
) -> ReviewState:
    from orchestrator.domain.context import ReviewTaskItem, ReviewTaskRecord

    return ReviewState(
        revision_id="R1",
        plan={"items": [], "groups": []},
        findings=findings,
        task_items=tuple(
            ReviewTaskItem(item_id=item_id, group_id=group_id, order=index)
            for index, (group_id, item_id) in enumerate(
                [("group-000001", "item-000001"), ("group-000002", "item-000002")],
                start=1,
            )
        ),
        task_review_ledger=tuple(
            ReviewTaskRecord(item_id=item_id, status="APPROVED")
            for _, item_id in ledger
        ),
        zhongshu_groups=rows,
    )


_ROW_A = ZhongshuGroupState(
    group_id="group-000001",
    doc_version=2,
    doc_markdown="# group-000001 doc v2\n",
)
_ROW_B = ZhongshuGroupState(
    group_id="group-000002",
    doc_version=1,
    doc_markdown="# group-000002 doc v1\n",
)


class CarryForwardGroupDocsTests(unittest.TestCase):
    def test_foreign_group_doc_variant_rejected(self):
        folded, error = carry_forward_group_docs(
            [
                {
                    "group_id": "group-000001",
                    "markdown": "# group-000001 doc v2 (tweaked)\n",
                }
            ],
            (_ROW_A, _ROW_B),
            editable_group_ids=("group-000002",),
        )
        self.assertEqual(error, "SOLVER_GROUP_DOC_FROZEN:['group-000001']")
        self.assertEqual(folded, {})
        self.assertTrue(is_retryable_solver_reply_error(error))

    def test_foreign_group_doc_identical_accepted(self):
        folded, error = carry_forward_group_docs(
            [{"group_id": "group-000001", "markdown": _ROW_A.doc_markdown}],
            (_ROW_A, _ROW_B),
            editable_group_ids=("group-000002",),
        )
        self.assertEqual(error, "")

    def test_foreign_group_doc_omitted_authoritative_kept(self):
        folded, error = carry_forward_group_docs(
            [],
            (_ROW_A, _ROW_B),
            editable_group_ids=("group-000002",),
        )
        self.assertEqual(error, "")
        self.assertNotIn("group-000001", folded)
        self.assertNotIn("group-000002", folded)

    def test_editable_group_doc_replaces_and_bumps_version(self):
        new_markdown = _doc("group-000002", 2)
        folded, error = carry_forward_group_docs(
            [{"group_id": "group-000002", "markdown": new_markdown}],
            (_ROW_A, _ROW_B),
            editable_group_ids=("group-000002",),
        )
        self.assertEqual(error, "")
        self.assertEqual(
            folded["group-000002"],
            {
                "markdown": new_markdown,
                "doc_version": 2,
                "doc_hash": hashlib.sha256(new_markdown.encode("utf-8")).hexdigest(),
                "doc_source_hash": hashlib.sha256(b"").hexdigest(),
            },
        )

    def test_editable_group_doc_with_version_skip_rejected(self):
        folded, error = carry_forward_group_docs(
            [{"group_id": "group-000002", "markdown": _doc("group-000002", 3)}],
            (_ROW_A, _ROW_B),
            editable_group_ids=("group-000002",),
        )
        self.assertTrue(
            error.startswith("SOLVER_GROUP_DOC_INVALID:doc_version_jump:1->3"),
            error,
        )
        self.assertEqual(folded, {})

    def test_frozen_group_never_editable_even_if_batch_names_it(self):
        review = _review(
            (_finding("f-1", "group-000001", "item-000001"),),
            (
                ZhongshuGroupState(group_id="group-000001", stage="FROZEN"),
                _ROW_B,
            ),
        )
        scope = batch_group_scope(review, SolverBatch(("f-1",), ()))
        self.assertNotIn("group-000001", scope)


class MaterializeGroupDocGateTests(unittest.TestCase):
    def test_revision_submitting_foreign_group_doc_variant_rejected(self):
        review = _review(
            (
                _finding("f-1", "group-000002", "item-000002"),
            ),
            (_ROW_A, _ROW_B),
        )
        logic = ReviseSolverLogic(SolverBatch(("f-1",), ()))
        _, error = logic.materialize(
            {
                "changes": [],
                "finding_batch": {
                    "selected_finding_ids": ["f-1"],
                    "remaining_finding_ids": [],
                },
                "group_docs": [
                    {
                        "group_id": "group-000001",
                        "markdown": _doc("group-000001", 3),
                    },
                    {"group_id": "group-000002", "markdown": _doc("group-000002", 2)},
                ],
            },
            review,
        )
        self.assertEqual(error, "SOLVER_GROUP_DOC_FROZEN:['group-000001']")

    def test_revision_with_identical_foreign_doc_passes(self):
        review = _review(
            (
                _finding("f-1", "group-000002", "item-000002"),
            ),
            (_ROW_A, _ROW_B),
        )
        logic = ReviseSolverLogic(SolverBatch(("f-1",), ()))
        _, error = logic.materialize(
            {
                "changes": [],
                "finding_batch": {
                    "selected_finding_ids": ["f-1"],
                    "remaining_finding_ids": [],
                },
                "group_docs": [
                    {"group_id": "group-000001", "markdown": _ROW_A.doc_markdown},
                    {"group_id": "group-000002", "markdown": _doc("group-000002", 2)},
                ],
            },
            review,
        )
        self.assertEqual(error, "")

    def test_revision_missing_scope_group_doc_rejected(self):
        review = _review(
            (
                _finding("f-1", "group-000002", "item-000002"),
            ),
            (_ROW_A, _ROW_B),
        )
        logic = ReviseSolverLogic(SolverBatch(("f-1",), ()))
        _, error = logic.materialize(
            {
                "changes": [],
                "finding_batch": {
                    "selected_finding_ids": ["f-1"],
                    "remaining_finding_ids": [],
                },
                "group_docs": [
                    {"group_id": "group-000001", "markdown": _ROW_A.doc_markdown},
                ],
            },
            review,
        )
        self.assertEqual(error, "SOLVER_GROUP_DOC_MISSING:group-000002")


class PlanMembershipProjectionTests(unittest.TestCase):
    """§8 closure must read membership from ``groups[].item_ids`` as fallback.

    Regression (task-20260928-bc58c1 SOLVER:3): a contract-legal plan carried
    membership only in ``groups[].item_ids`` — the items had no ``group_id``
    field — so the projection built an empty member set and orphaned every
    §8 subsection, burning a full solver round on a false rejection.
    """

    def _plan(self) -> dict:
        def _item(item_id: str) -> dict:
            return {
                "item_id": item_id,
                "objective": "目标。",
                "acceptance_signals": [],
                "dependencies": [],
            }

        return {
            "items": [_item("item-000001"), _item("item-000002")],
            "groups": [
                {"group_id": "group-000001", "item_ids": ["item-000001"]},
                {"group_id": "group-000002", "item_ids": ["item-000002"]},
            ],
        }

    def _doc_with_subsections(self, group_id: str, item_ids: tuple) -> str:
        sections = (
            "背景", "目标", "标识与范围", "状态与边界语义", "行为要求",
            "责任边界", "交叉不变量", "验收标准", "非目标",
        )
        lines = [f"# {group_id} 需求文档 [v1]"]
        for number, name in enumerate(sections, start=1):
            lines.append(f"## {number}. {name}")
            if name == "背景":
                lines.append(f"{group_id} 的上下文。")
            if name == "验收标准":
                for item_id in item_ids:
                    lines.append(f"### {item_id}")
                    lines.append("结果可观测。")
        return "\n".join(lines)

    def test_membership_from_group_list_closes(self):
        folded, error = carry_forward_group_docs(
            [
                {
                    "group_id": "group-000001",
                    "markdown": self._doc_with_subsections(
                        "group-000001", ("item-000001",)
                    ),
                },
                {
                    "group_id": "group-000002",
                    "markdown": self._doc_with_subsections(
                        "group-000002", ("item-000002",)
                    ),
                },
            ],
            (),
            editable_group_ids=("group-000001", "group-000002"),
            required_group_ids=("group-000001", "group-000002"),
            plan=self._plan(),
        )
        self.assertEqual(error, "", error)
        self.assertEqual(set(folded), {"group-000001", "group-000002"})

    def test_item_level_group_id_still_wins(self):
        plan = self._plan()
        plan["items"][0]["group_id"] = "group-000002"
        folded, error = carry_forward_group_docs(
            [
                {
                    "group_id": "group-000001",
                    "markdown": self._doc_with_subsections(
                        "group-000001", ("item-000001",)
                    ),
                },
            ],
            (),
            editable_group_ids=("group-000001",),
            required_group_ids=("group-000001",),
            plan=plan,
        )
        self.assertTrue(
            error.startswith("SOLVER_GROUP_DOC_INVALID:acceptance_orphan"),
            error,
        )


if __name__ == "__main__":
    unittest.main()
