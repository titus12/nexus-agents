"""Group requirement document model: nine sections, plan Task 5.

One versioned markdown document per Zhongshu group carries the group
requirement through the convergence loop.  The document must parse, keep
its nine sections complete and ordered, avoid undecidable wording in the
goal section, close acceptance both ways against the group's item
acceptance signals, and stay free of implementation markers.  Solver
replies fold these documents only through mechanical checks.
"""

from __future__ import annotations

import hashlib
import unittest

from orchestrator.domain.context import (
    ReviewState,
    ReviewTaskGroup,
    ReviewTaskItem,
    ZhongshuGroupState,
)
from orchestrator.domain.findings import Finding
from orchestrator.domain.policies.solver_plan import (
    is_retryable_solver_reply_error,
)
from orchestrator.domain.zhongshu.solver import (
    FormalizeSolverLogic,
    ReviseSolverLogic,
    SolverBatch,
)


SECTIONS = (
    ("背景", "任务来自 issue 需求。"),
    ("目标", "输出 A 与 B。"),
    ("标识与范围", "group-000001 覆盖 item-000001。"),
    ("状态与边界语义", "仅在依赖组冻结后生效。"),
    ("行为要求", "必须返回结构化结果。"),
    ("责任边界", "Solver 撰写，Critic 裁决。"),
    ("交叉不变量", "依赖组冻结前本组不得冻结。"),
    ("验收标准", ""),
    ("非目标", "不包含部署。"),
)


def _doc_markdown(
    group_id: str = "group-000001",
    version: int = 1,
    acceptance_lines: tuple[str, ...] = ("A is observable", "B is observable"),
    acceptance_by_item: dict[str, tuple[str, ...]] | None = None,
    section_overrides: dict[int, tuple[str, str]] | None = None,
    drop_sections: tuple[int, ...] = (),
) -> str:
    overrides = section_overrides or {}
    lines = [f"# {group_id} 需求文档 [v{version}]"]
    for number, (name, body) in enumerate(SECTIONS, start=1):
        if number in drop_sections:
            continue
        name, body = overrides.get(number, (name, body))
        if number == 8:
            if acceptance_by_item is not None:
                body_lines: list[str] = []
                for item_id, signals in acceptance_by_item.items():
                    body_lines.append(f"### {item_id}")
                    body_lines.extend(signals)
                body = "\n".join(body_lines)
            else:
                body = "\n".join(acceptance_lines)
        lines.append(f"## {number}. {name}")
        if body:
            lines.append(body)
    return "\n".join(lines)


def _review(
    *,
    acceptance: tuple[str, ...] = ("A is observable", "B is observable"),
    group_id: str = "group-000001",
) -> ReviewState:
    return ReviewState(
        revision_id="R1",
        plan={"items": [], "groups": []},
        task_items=(
            ReviewTaskItem(
                item_id="item-000001",
                group_id=group_id,
                order=1,
                acceptance_signals=acceptance,
            ),
        ),
        task_groups=(ReviewTaskGroup(group_id=group_id, item_ids=("item-000001",), order=1),),
    )


def _revision_review(rows: tuple[ZhongshuGroupState, ...]) -> ReviewState:
    return ReviewState(
        revision_id="R1",
        plan={
            "requirements": [],
            "items": [],
            "groups": [{"group_id": "group-000001", "item_ids": ["item-000001"]}],
            "dependencies": [],
            "scope": {},
            "unknowns": [],
            "risks": [],
        },
        findings=(
            Finding(
                finding_id="f-1",
                severity="P1",
                status="OPEN",
                group_id="group-000001",
                item_id="item-000001",
            ),
        ),
        task_items=(
            ReviewTaskItem(
                item_id="item-000001",
                group_id="group-000001",
                order=1,
                acceptance_signals=("A is observable",),
            ),
        ),
        task_groups=(
            ReviewTaskGroup(group_id="group-000001", item_ids=("item-000001",), order=1),
        ),
        zhongshu_groups=rows,
    )


class DocRoundTripsParseRenderTests(unittest.TestCase):
    def test_parse_render_round_trip_is_byte_identical(self) -> None:
        from orchestrator.domain.zhongshu_doc import ZhongshuRequirementDoc

        markdown = _doc_markdown()
        doc = ZhongshuRequirementDoc.parse(markdown)
        self.assertEqual(doc.render(), markdown)
        self.assertEqual(doc.group_id, "group-000001")


class DocStructureViolationTests(unittest.TestCase):
    def test_missing_section_is_reported(self) -> None:
        from orchestrator.domain.zhongshu_doc import group_doc_violations

        markdown = _doc_markdown(drop_sections=(6,))
        violations = group_doc_violations(
            markdown, previous_version=None, review=_review()
        )
        self.assertTrue(
            any(v.startswith("missing_section:6") for v in violations),
            violations,
        )

    def test_section_order_is_enforced(self) -> None:
        from orchestrator.domain.zhongshu_doc import group_doc_violations

        markdown = _doc_markdown(section_overrides={3: ("验收标准-moved", "")})
        lines = markdown.splitlines()
        section8_start = next(
            index for index, line in enumerate(lines) if line.startswith("## 8.")
        )
        section9_start = next(
            index for index, line in enumerate(lines) if line.startswith("## 9.")
        )
        reordered = (
            lines[:2]
            + lines[section8_start:section9_start]
            + lines[2:section8_start]
            + lines[section9_start:]
        )
        violations = group_doc_violations(
            "\n".join(reordered), previous_version=None, review=_review()
        )
        self.assertTrue(
            any(v.startswith("section_order") for v in violations),
            violations,
        )

    def test_undecidable_wording_in_goals_is_rejected(self) -> None:
        from orchestrator.domain.zhongshu_doc import group_doc_violations

        markdown = _doc_markdown(
            section_overrides={2: ("目标", "尽量快地输出 A 与 B。")},
            acceptance_lines=(),
        )
        violations = group_doc_violations(
            markdown, previous_version=None, review=_review()
        )
        self.assertTrue(
            any(v.startswith("undecidable_wording:2") for v in violations),
            violations,
        )

    def test_implementation_marker_is_rejected(self) -> None:
        from orchestrator.domain.zhongshu_doc import group_doc_violations

        markdown = _doc_markdown(
            section_overrides={5: ("行为要求", "```python\ndef run(): ...\n```")},
            acceptance_lines=(),
        )
        violations = group_doc_violations(
            markdown, previous_version=None, review=_review()
        )
        self.assertTrue(
            any(v.startswith("implementation_marker:5") for v in violations),
            violations,
        )


class AcceptanceClosureTests(unittest.TestCase):
    def test_both_directions_close(self) -> None:
        from orchestrator.domain.zhongshu_doc import group_doc_violations

        violations = group_doc_violations(
            _doc_markdown(), previous_version=None, review=_review()
        )
        self.assertEqual(
            [v for v in violations if v.startswith("acceptance_")], []
        )

    def test_orphan_acceptance_line_is_reported(self) -> None:
        from orchestrator.domain.zhongshu_doc import group_doc_violations

        violations = group_doc_violations(
            _doc_markdown(
                acceptance_lines=("A is observable", "B is observable", "C is observable")
            ),
            previous_version=None,
            review=_review(),
        )
        self.assertTrue(
            any(v == "acceptance_orphan:C is observable" for v in violations),
            violations,
        )

    def test_missing_item_signal_is_reported(self) -> None:
        from orchestrator.domain.zhongshu_doc import group_doc_violations

        violations = group_doc_violations(
            _doc_markdown(acceptance_lines=("A is observable",)),
            previous_version=None,
            review=_review(),
        )
        self.assertTrue(
            any(v == "acceptance_missing:B is observable" for v in violations),
            violations,
        )


class TitleVersionArithmeticTests(unittest.TestCase):
    def test_title_version_parses(self) -> None:
        from orchestrator.domain.zhongshu_doc import ZhongshuRequirementDoc

        doc = ZhongshuRequirementDoc.parse(
            _doc_markdown(version=2, acceptance_lines=())
        )
        self.assertEqual(doc.version, 2)

    def test_v1_to_v2_is_legal(self) -> None:
        from orchestrator.domain.zhongshu_doc import group_doc_violations

        violations = group_doc_violations(
            _doc_markdown(version=2, acceptance_lines=("A is observable",)),
            previous_version=1,
            review=_review(acceptance=("A is observable",)),
        )
        self.assertEqual(
            [v for v in violations if v.startswith("doc_version")], []
        )

    def test_version_skip_is_illegal(self) -> None:
        from orchestrator.domain.zhongshu_doc import group_doc_violations

        violations = group_doc_violations(
            _doc_markdown(version=3, acceptance_lines=("A is observable",)),
            previous_version=1,
            review=_review(acceptance=("A is observable",)),
        )
        self.assertTrue(
            any(v == "doc_version_jump:1->3" for v in violations),
            violations,
        )

    def test_initial_document_must_be_v1(self) -> None:
        from orchestrator.domain.zhongshu_doc import group_doc_violations

        violations = group_doc_violations(
            _doc_markdown(version=2, acceptance_lines=("A is observable",)),
            previous_version=None,
            review=_review(acceptance=("A is observable",)),
        )
        self.assertTrue(
            any(v.startswith("doc_version_initial") for v in violations),
            violations,
        )


def _two_group_review() -> ReviewState:
    base = _review()
    return ReviewState(
        revision_id="R1",
        plan={"items": [], "groups": []},
        task_items=base.task_items,
        task_groups=(
            ReviewTaskGroup(group_id="group-000001", item_ids=("item-000001",), order=1),
            ReviewTaskGroup(group_id="group-000002", item_ids=("item-000002",), order=2),
        ),
    )


class SolverReplyGroupDocsGateTests(unittest.TestCase):
    def test_formalize_reply_without_group_docs_is_missing_all(self) -> None:
        review = _review()
        logic = FormalizeSolverLogic()
        _, error = logic.materialize(
            {
                "plan": {"items": [], "groups": [{"group_id": "group-000001"}]},
                "changes": [],
            },
            review,
        )
        self.assertEqual(error, "SOLVER_GROUP_DOC_MISSING:all")
        self.assertTrue(is_retryable_solver_reply_error(error))

    def test_formalize_partial_group_docs_names_missing_group(self) -> None:
        review = _two_group_review()
        logic = FormalizeSolverLogic()
        _, error = logic.materialize(
            {
                "plan": {
                    "items": [],
                    "groups": [
                        {"group_id": "group-000001"},
                        {"group_id": "group-000002"},
                    ],
                },
                "changes": [],
                "group_docs": [
                    {
                        "group_id": "group-000001",
                        "markdown": _doc_markdown(
                            acceptance_lines=("A is observable", "B is observable")
                        ),
                    }
                ],
            },
            review,
        )
        self.assertEqual(error, "SOLVER_GROUP_DOC_MISSING:group-000002")

    def test_invalid_group_doc_reports_details(self) -> None:
        review = _review()
        logic = FormalizeSolverLogic()
        _, error = logic.materialize(
            {
                "plan": {"items": [], "groups": [{"group_id": "group-000001"}]},
                "changes": [],
                "group_docs": [
                    {
                        "group_id": "group-000001",
                        "markdown": _doc_markdown(drop_sections=(6,)),
                    }
                ],
            },
            review,
        )
        self.assertTrue(
            error.startswith("SOLVER_GROUP_DOC_INVALID:missing_section:6"),
            error,
        )
        self.assertTrue(is_retryable_solver_reply_error(error))


class FoldGroupDocTests(unittest.TestCase):
    def test_fold_recomputes_hash_and_bumps_version(self) -> None:
        from orchestrator.zhongshu_parallel import canonical_plan_hash

        authoritative = _doc_markdown(
            version=1, acceptance_lines=("A is observable",)
        )
        submitted = _doc_markdown(version=2, acceptance_lines=("A is observable",))
        review = _revision_review(
            (
                ZhongshuGroupState(
                    group_id="group-000001",
                    doc_version=1,
                    doc_markdown=authoritative,
                ),
            )
        )
        logic = ReviseSolverLogic(SolverBatch(("f-1",), ()))
        materialized, error = logic.materialize(
            {
                "changes": [],
                "finding_batch": {
                    "selected_finding_ids": ["f-1"],
                    "remaining_finding_ids": [],
                },
                "group_docs": [
                    {"group_id": "group-000001", "markdown": submitted}
                ],
            },
            review,
        )
        self.assertEqual(error, "")
        fold = logic.folded_group_docs["group-000001"]
        self.assertEqual(fold["markdown"], submitted)
        self.assertEqual(fold["doc_version"], 2)
        self.assertEqual(
            fold["doc_hash"], hashlib.sha256(submitted.encode("utf-8")).hexdigest()
        )
        self.assertEqual(fold["doc_source_hash"], canonical_plan_hash(materialized))

    def test_fold_rejects_version_skip(self) -> None:
        authoritative = _doc_markdown(
            version=1, acceptance_lines=("A is observable",)
        )
        review = _revision_review(
            (
                ZhongshuGroupState(
                    group_id="group-000001",
                    doc_version=1,
                    doc_markdown=authoritative,
                ),
            )
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
                        "markdown": _doc_markdown(
                            version=3, acceptance_lines=("A is observable",)
                        ),
                    }
                ],
            },
            review,
        )
        self.assertTrue(
            error.startswith("SOLVER_GROUP_DOC_INVALID:doc_version_jump:1->3"),
            error,
        )


class Section8ProjectionTests(unittest.TestCase):
    """§8 item subsections are the single author of acceptance_signals."""

    def _plan(self, signals_a: tuple[str, ...] = ("WRONG model text",)) -> dict:
        def item(item_id: str, signals: list) -> dict:
            return {
                "item_id": item_id,
                "group_id": "group-000001",
                "title": item_id,
                "objective": "do it",
                "source_requirement_ids": ["req-000001"],
                "dependencies": [],
                "acceptance_signals": signals,
                "unknowns": [],
                "risks": [],
                "parallelizable": True,
            }

        return {
            "requirements": [],
            "items": [
                item("item-000001", list(signals_a)),
                item("item-000002", ["B is observable"]),
            ],
            "groups": [
                {"group_id": "group-000001", "item_ids": ["item-000001", "item-000002"]}
            ],
            "dependencies": [],
            "scope": {},
            "unknowns": [],
            "risks": [],
        }

    def _by_item(self) -> dict:
        return {
            "item-000001": ("A is observable", "A latency budget is observable"),
            "item-000002": ("B is observable",),
        }

    def test_parse_section8_item_subsections(self) -> None:
        from orchestrator.domain.zhongshu_doc import ZhongshuRequirementDoc

        doc = ZhongshuRequirementDoc.parse(
            _doc_markdown(acceptance_by_item=self._by_item())
        )
        self.assertEqual(
            doc.acceptance_item_signals(),
            {
                "item-000001": ("A is observable", "A latency budget is observable"),
                "item-000002": ("B is observable",),
            },
        )

    def test_project_signals_overwrites_plan_field(self) -> None:
        from orchestrator.domain.zhongshu_doc import project_acceptance_signals

        plan, error = project_acceptance_signals(
            self._plan(),
            {"group-000001": _doc_markdown(acceptance_by_item=self._by_item())},
        )
        self.assertEqual(error, "")
        by_id = {entry["item_id"]: entry for entry in plan["items"]}
        self.assertEqual(
            by_id["item-000001"]["acceptance_signals"],
            ["A is observable", "A latency budget is observable"],
        )
        self.assertEqual(by_id["item-000002"]["acceptance_signals"], ["B is observable"])

    def test_project_signals_missing_item_section_rejected(self) -> None:
        from orchestrator.domain.zhongshu_doc import project_acceptance_signals

        plan, error = project_acceptance_signals(
            self._plan(),
            {
                "group-000001": _doc_markdown(
                    acceptance_by_item={"item-000001": ("A is observable",)}
                )
            },
        )
        self.assertIsNone(plan)
        self.assertEqual(error, "PLAN_HAS_NO_ACCEPTANCE:item-000002")

    def test_project_is_retryable(self) -> None:
        from orchestrator.domain.policies.solver_plan import (
            is_retryable_solver_reply_error,
        )

        self.assertTrue(
            is_retryable_solver_reply_error("PLAN_HAS_NO_ACCEPTANCE:item-000002")
        )

    def test_legacy_doc_without_subsections_keeps_model_signals(self) -> None:
        from orchestrator.domain.zhongshu_doc import project_acceptance_signals

        plan, error = project_acceptance_signals(
            self._plan(),
            {"group-000001": _doc_markdown()},
        )
        self.assertEqual(error, "")
        by_id = {entry["item_id"]: entry for entry in plan["items"]}
        self.assertEqual(by_id["item-000001"]["acceptance_signals"], ["WRONG model text"])

    def test_orphan_subsection_is_reported(self) -> None:
        from orchestrator.domain.zhongshu_doc import group_doc_violations

        violations = group_doc_violations(
            _doc_markdown(
                acceptance_by_item={
                    "item-000001": ("A is observable",),
                    "item-000002": ("B is observable",),
                    "item-000099": ("ghost",),
                }
            ),
            previous_version=None,
            review=_review(),
            group_id="group-000001",
        )
        self.assertIn("acceptance_orphan:item-000099", violations)

    def test_process_reply_projects_signals(self) -> None:
        from orchestrator.domain.zhongshu.solver import process_solver_reply

        outcome = process_solver_reply(
            {
                "action": "READY_FOR_CRITIC",
                "mode": "TASK_GRAPH_FORMALIZATION_READ_ONLY",
                "plan": self._plan(),
                "changes": [],
                "group_docs": [
                    {
                        "group_id": "group-000001",
                        "markdown": _doc_markdown(acceptance_by_item=self._by_item()),
                    }
                ],
            },
            ReviewState(revision_id="R1", plan={"items": [], "groups": []}),
        )
        self.assertEqual(outcome.error, "")
        by_id = {entry["item_id"]: entry for entry in outcome.materialized["items"]}
        self.assertEqual(
            by_id["item-000001"]["acceptance_signals"],
            ["A is observable", "A latency budget is observable"],
        )

    def test_contract_acceptance_signals_optional(self) -> None:
        from orchestrator.contracts.zhongshu_solver import _TASK

        self.assertNotIn("acceptance_signals", _TASK["required"])


class ViolationDetailAndFeedbackTests(unittest.TestCase):
    """Mechanical rejections carry an expected/actual diff for the re-ask."""

    def _details(self):
        from orchestrator.domain.zhongshu_doc import doc_violation_details

        return doc_violation_details(
            _doc_markdown(
                acceptance_lines=("A is observable", "ORPHAN line"),
            ),
            previous_version=None,
            review=_review(),
            group_id="group-000001",
        )

    def test_details_carry_kind_item_expected_actual(self) -> None:
        orphan = [d for d in self._details() if d.kind == "acceptance_orphan"]
        self.assertTrue(orphan)
        self.assertEqual(orphan[0].actual, "ORPHAN line")
        self.assertEqual(orphan[0].item_id, "")
        self.assertIn("B is observable", orphan[0].expected)

    def test_invalid_doc_error_carries_expected_rows(self) -> None:
        from orchestrator.domain.policies.solver_plan import carry_forward_group_docs

        _, error = carry_forward_group_docs(
            [
                {
                    "group_id": "group-000001",
                    "markdown": _doc_markdown(
                        acceptance_lines=("A is observable", "ORPHAN line")
                    ),
                }
            ],
            (),
            editable_group_ids=("group-000001",),
            required_group_ids=("group-000001",),
            review=_review(),
            plan={"items": []},
        )
        self.assertTrue(error.startswith("SOLVER_GROUP_DOC_INVALID:"), error)
        self.assertIn("expected_rows=", error)
        self.assertIn("B is observable", error)


class ReviewPreflightTests(unittest.TestCase):
    """LLM-free pre-dispatch gate: unfit groups never cost a review round."""

    def _review(self, markdown: str, plan: dict | None = None):
        return ReviewState(
            revision_id="R1",
            plan=plan if plan is not None else {"items": [], "groups": []},
            task_items=(
                ReviewTaskItem(
                    item_id="item-000001",
                    group_id="group-000001",
                    order=1,
                    acceptance_signals=("A is observable",),
                ),
            ),
            zhongshu_groups=(
                ZhongshuGroupState(
                    group_id="group-000001", doc_markdown=markdown, doc_version=1
                ),
            ),
        )

    def test_preflight_flags_unfit_group(self) -> None:
        from orchestrator.domain.policies.zhongshu_group import (
            review_preflight_violations,
        )

        violations = review_preflight_violations(
            self._review(
                _doc_markdown(
                    drop_sections=(6,), acceptance_lines=("A is observable",)
                )
            )
        )
        self.assertTrue(
            any(v.startswith("group-000001:missing_section:6") for v in violations),
            violations,
        )

    def test_preflight_clean_review_passes(self) -> None:
        from orchestrator.domain.policies.zhongshu_group import (
            review_preflight_violations,
        )

        violations = review_preflight_violations(
            self._review(_doc_markdown(acceptance_lines=("A is observable",)))
        )
        self.assertEqual(violations, ())

    def test_preflight_flags_blocking_structural(self) -> None:
        from orchestrator.domain.policies.zhongshu_group import (
            review_preflight_violations,
        )

        plan = {
            "items": [
                {
                    "item_id": "item-000001",
                    "group_id": "group-000001",
                    "source_requirement_ids": [],
                    "dependencies": ["item-000002"],
                    "acceptance_signals": [],
                },
                {
                    "item_id": "item-000002",
                    "group_id": "group-000001",
                    "source_requirement_ids": [],
                    "dependencies": ["item-000001"],
                    "acceptance_signals": [],
                },
            ],
            "groups": [
                {"group_id": "group-000001", "item_ids": ["item-000001", "item-000002"]}
            ],
        }
        violations = review_preflight_violations(
            self._review(
                _doc_markdown(acceptance_lines=("A is observable",)), plan=plan
            )
        )
        self.assertTrue(
            any(v.startswith("plan:DEPENDENCY_CYCLE") for v in violations),
            violations,
        )


class MarkdownEmphasisToleranceTests(unittest.TestCase):
    """Id tokens wrapped in bold/backticks must resolve to the bare id.

    Regression (task-20260928-f13561): the solver wrote ``### **item-00001**``
    and a bolded title line; the raw tokens mismatched the member set and
    every signal under them was orphaned / the whole document was rejected as
    unparseable.
    """

    def _wrapped_doc(self) -> str:
        sections = (
            ("背景", "任务来自 issue 需求。"),
            ("目标", "输出 A 与 B。"),
            ("标识与范围", "group-000001 覆盖 item-000001。"),
            ("状态与边界语义", "仅在依赖组冻结后生效。"),
            ("行为要求", "必须返回结构化结果。"),
            ("责任边界", "Solver 撰写，Critic 裁决。"),
            ("交叉不变量", "依赖组冻结前本组不得冻结。"),
            ("验收标准", ""),
            ("非目标", "不包含部署。"),
        )
        lines = ["# **group-000001 需求文档 [v1]**"]
        for number, (name, body) in enumerate(sections, start=1):
            lines.append(f"## {number}. {name}")
            if body:
                lines.append(body)
            if name == "验收标准":
                lines.append("### **item-000001**")
                lines.append("A is observable")
                lines.append("### `item-000002` 要点")
                lines.append("B is observable")
        return "\n".join(lines)

    def test_bold_title_and_ids_parse_to_bare_tokens(self) -> None:
        from orchestrator.domain.zhongshu_doc import ZhongshuRequirementDoc

        doc = ZhongshuRequirementDoc.parse(self._wrapped_doc())

        self.assertEqual(doc.group_id, "group-000001")
        self.assertEqual(doc.version, 1)
        self.assertEqual(
            doc.acceptance_item_signals(),
            {
                "item-000001": ("A is observable",),
                "item-000002": ("B is observable",),
            },
        )

    def test_wrapped_ids_close_against_the_members(self) -> None:
        from orchestrator.domain.zhongshu_doc import doc_violation_details

        review = ReviewState(
            revision_id="R1",
            task_items=(
                ReviewTaskItem("item-000001", "group-000001"),
                ReviewTaskItem("item-000002", "group-000001"),
            ),
            task_groups=(
                ReviewTaskGroup("group-000001", ("item-000001", "item-000002")),
            ),
        )
        violations = doc_violation_details(
            self._wrapped_doc(), review=review, group_id="group-000001"
        )

        codes = [entry.code for entry in violations]
        self.assertNotIn("doc_unparseable", codes)
        self.assertFalse(
            any(code == "acceptance_orphan" for code in codes), violations
        )


if __name__ == "__main__":
    unittest.main()
