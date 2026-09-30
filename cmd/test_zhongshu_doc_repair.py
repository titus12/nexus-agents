"""Deterministic repair of format-only group document slips."""

from __future__ import annotations

import unittest

from orchestrator.domain.policies.solver_plan import carry_forward_group_docs
from orchestrator.domain.zhongshu_doc import (
    SECTION_TITLES,
    ZhongshuRequirementDoc,
    doc_violation_details,
)
from orchestrator.domain.zhongshu_doc_repair import repair_group_doc
from test_zhongshu_doc import _doc_markdown, _review

GROUP = "group-000001"
MEMBERS = frozenset({"item-000001"})
ACCEPTANCE = {"item-000001": ("A is observable",)}


def _valid(**kwargs: object) -> str:
    kwargs.setdefault("acceptance_by_item", ACCEPTANCE)
    return _doc_markdown(**kwargs)  # type: ignore[arg-type]


def _repair(markdown: str, *, version: int = 1, members=MEMBERS):
    return repair_group_doc(
        markdown, group_id=GROUP, version=version, member_item_ids=members
    )


class TitleRepairTests(unittest.TestCase):
    def test_canonical_document_is_untouched(self) -> None:
        markdown = _valid()

        result = _repair(markdown)

        self.assertEqual(result.markdown, markdown)
        self.assertEqual(result.repairs, ())

    def test_declared_version_is_kept_for_the_arithmetic_check(self) -> None:
        markdown = _valid(version=7)

        result = _repair(markdown, version=3)

        self.assertEqual(result.repairs, ())
        self.assertEqual(result.markdown, markdown)

    def test_decorated_title_is_normalized_keeping_its_version(self) -> None:
        markdown = _valid(version=2).replace(
            f"# {GROUP} 需求文档 [v2]", f"# **{GROUP}** 需求文档 [ v2 ]"
        )

        result = _repair(markdown, version=5)

        self.assertEqual(result.repairs, ("title",))
        self.assertTrue(result.markdown.startswith(f"# {GROUP} 需求文档 [v2]\n"))

    def test_title_without_version_gets_the_orchestrator_version(self) -> None:
        markdown = _valid().replace(f"# {GROUP} 需求文档 [v1]", f"# {GROUP} 需求文档")

        result = _repair(markdown, version=3)

        self.assertEqual(result.repairs, ("title",))
        self.assertTrue(result.markdown.startswith(f"# {GROUP} 需求文档 [v3]\n"))

    def test_title_naming_another_group_is_left_for_validation(self) -> None:
        markdown = _valid(group_id="group-000009")

        result = _repair(markdown)

        self.assertEqual(result.repairs, ())
        self.assertEqual(result.markdown, markdown)

    def test_leading_blank_lines_before_title_are_removed(self) -> None:
        result = _repair("\n\n" + _valid())

        self.assertEqual(result.repairs, ("title",))
        self.assertTrue(result.markdown.startswith("# "))

    def test_document_without_h1_is_not_invented(self) -> None:
        markdown = "just prose\n" + _valid()

        self.assertEqual(_repair(markdown).markdown, markdown)


class SectionNameRepairTests(unittest.TestCase):
    def test_wrong_section_name_is_canonicalized(self) -> None:
        markdown = _valid(section_overrides={5: ("行为", "必须返回结构化结果。")})

        result = _repair(markdown)

        self.assertEqual(result.repairs, ("section_name:5",))
        self.assertIn(f"## 5. {SECTION_TITLES[4]}", result.markdown)
        self.assertIn("必须返回结构化结果。", result.markdown)

    def test_missing_section_is_not_invented(self) -> None:
        markdown = _valid(drop_sections=(9,))

        result = _repair(markdown)

        self.assertEqual(result.repairs, ())
        self.assertNotIn(f"## 9. {SECTION_TITLES[8]}", result.markdown)


class AcceptanceOrphanRepairTests(unittest.TestCase):
    def _with_orphan(self) -> str:
        return _valid(
            acceptance_by_item={
                "item-000001": ("A is observable",),
                "item-999999": ("ghost signal",),
            }
        )

    def test_orphan_subsection_is_dropped_with_its_body(self) -> None:
        result = _repair(self._with_orphan())

        self.assertEqual(result.repairs, ("acceptance_orphan:item-999999",))
        self.assertNotIn("item-999999", result.markdown)
        self.assertNotIn("ghost signal", result.markdown)
        self.assertIn("### item-000001", result.markdown)
        self.assertIn("A is observable", result.markdown)
        self.assertIn(f"## 9. {SECTION_TITLES[8]}", result.markdown)

    def test_orphan_before_member_keeps_the_member_block(self) -> None:
        markdown = _valid(
            acceptance_by_item={
                "item-999999": ("ghost signal",),
                "item-000001": ("A is observable",),
            }
        )

        result = _repair(markdown)

        self.assertNotIn("ghost signal", result.markdown)
        self.assertIn("A is observable", result.markdown)

    def test_preamble_before_first_subsection_is_dropped(self) -> None:
        markdown = _valid().replace(
            "### item-000001", "以下为验收：\n### item-000001", 1
        )

        result = _repair(markdown)

        self.assertEqual(result.repairs, ("acceptance_preamble",))
        self.assertNotIn("以下为验收", result.markdown)

    def test_no_member_left_leaves_the_document_alone(self) -> None:
        markdown = _valid(acceptance_by_item={"item-999999": ("ghost signal",)})

        result = _repair(markdown)

        self.assertEqual(result.repairs, ())
        self.assertEqual(result.markdown, markdown)

    def test_legacy_flat_acceptance_is_untouched(self) -> None:
        markdown = _doc_markdown(acceptance_lines=("A is observable", "ORPHAN line"))

        result = _repair(markdown)

        self.assertEqual(result.repairs, ())
        self.assertEqual(result.markdown, markdown)

    def test_without_member_ids_nothing_is_dropped(self) -> None:
        markdown = self._with_orphan()

        self.assertEqual(_repair(markdown, members=frozenset()).markdown, markdown)


class RepairContractTests(unittest.TestCase):
    def test_repair_is_idempotent(self) -> None:
        broken = _valid(
            version=4,
            section_overrides={2: ("目的", "输出 A 与 B。")},
            acceptance_by_item={
                "item-000001": ("A is observable",),
                "item-999999": ("ghost signal",),
            },
        )

        once = _repair(broken)
        twice = _repair(once.markdown)

        self.assertTrue(once.repairs)
        self.assertEqual(twice.repairs, ())
        self.assertEqual(twice.markdown, once.markdown)

    def test_repaired_document_passes_validation(self) -> None:
        broken = _valid(
            section_overrides={2: ("目的", "输出 A 与 B。")},
            acceptance_by_item={
                "item-000001": ("A is observable",),
                "item-999999": ("ghost signal",),
            },
        )
        review = _review(acceptance=("A is observable",))

        before = doc_violation_details(
            broken, previous_version=None, review=review, group_id=GROUP
        )
        repaired = _repair(broken, version=1).markdown
        after = doc_violation_details(
            repaired, previous_version=None, review=review, group_id=GROUP
        )

        self.assertTrue(before)
        self.assertFalse(after)
        ZhongshuRequirementDoc.parse(repaired)

    def test_content_of_member_items_is_preserved(self) -> None:
        markdown = _valid(section_overrides={2: ("目的", "输出 A 与 B。")})

        self.assertIn("输出 A 与 B。", _repair(markdown).markdown)


class CarryForwardIntegrationTests(unittest.TestCase):
    def _carry(self, markdown: str, previous: tuple = ()):
        return carry_forward_group_docs(
            [{"group_id": GROUP, "markdown": markdown}],
            previous,
            editable_group_ids=(GROUP,),
            required_group_ids=(GROUP,),
            review=_review(acceptance=("A is observable",)),
            plan={"items": []},
        )

    def test_format_slips_no_longer_reject_the_reply(self) -> None:
        broken = _valid(
            section_overrides={5: ("行为", "必须返回结构化结果。")},
            acceptance_by_item={
                "item-000001": ("A is observable",),
                "item-999999": ("ghost signal",),
            },
        )

        rows, error = self._carry(broken)

        self.assertEqual(error, "")
        markdown = str(rows[GROUP]["markdown"])
        self.assertIn(f"## 5. {SECTION_TITLES[4]}", markdown)
        self.assertNotIn("ghost signal", markdown)

    def test_unrepairable_content_still_rejects(self) -> None:
        missing_signal = _valid(acceptance_by_item={"item-999999": ("ghost signal",)})
        review = _review(acceptance=("A is observable",))

        _, error = carry_forward_group_docs(
            [{"group_id": GROUP, "markdown": missing_signal}],
            (),
            editable_group_ids=(GROUP,),
            required_group_ids=(GROUP,),
            review=review,
            plan={"items": []},
        )

        self.assertTrue(error.startswith("SOLVER_GROUP_DOC_INVALID:"), error)


if __name__ == "__main__":
    unittest.main()
