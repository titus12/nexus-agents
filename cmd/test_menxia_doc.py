from __future__ import annotations

import unittest

from orchestrator.domain.context import (
    MenxiaParallelLimits,
    ParallelState,
    ProgressState,
    ReviewState,
    ReviewTaskGroup,
    ReviewTaskItem,
    TaskIdentity,
    WorkflowContext,
    ZhongshuGroupState,
)
from orchestrator.domain.menxia_doc import (
    BODY_PART_TITLES,
    MenxiaDocError,
    MenxiaGroupDoc,
    SUGGESTION_CLOSED_ACCEPTED,
    SUGGESTION_CLOSED_AUTO,
    SUGGESTION_DEFERRED,
    SUGGESTION_OPEN,
    SUGGESTION_REJECTED_PENDING,
    changed_body_sections,
    menxia_approval_blockers,
    render_initial,
    verify_absorption,
    verify_group_reply,
)


def _doc(**overrides) -> MenxiaGroupDoc:
    base = MenxiaGroupDoc(
        group_id="group-000001",
        version=1,
        title="group-000001 门下省方案",
        baseline_markdown="需求基线",
        body_markdown="## 步骤\n\n先做 A。",
        suggestions=(),
        ledger=(),
        signoffs=(),
    )
    return MenxiaGroupDoc(**{**base.__dict__, **overrides})


def _six_part_body(
    acceptance_entries: tuple[str, ...] = ("Task A is observable",),
    extra: str = "",
) -> str:
    parts = {
        "目标与设计决策": "按基线执行，不做额外决策。",
        "现状与目标行为": "当前行为与目标行为如基线所述。",
        "统一约束": "遵循统一约束。",
        "任务分解": "- 任务 A -> 一次执行。",
        "验收映射": "\n".join(
            f"- {entry} -> 验收用例覆盖。" for entry in acceptance_entries
        ),
        "风险与兼容": "风险可控。" + extra,
    }
    return "\n\n".join(
        f"### {title}\n{parts[title]}" for title in BODY_PART_TITLES
    )


class TestParseRenderRoundtrip(unittest.TestCase):
    def test_initial_render_parses_back(self) -> None:
        markdown = render_initial(
            group_id="group-000001",
            baseline_markdown="需求基线内容",
        )
        doc = MenxiaGroupDoc.parse(markdown, group_id="group-000001")
        self.assertEqual(doc.version, 1)
        self.assertEqual(doc.title, "group-000001 门下省方案")
        self.assertEqual(doc.baseline_markdown, "需求基线内容")
        self.assertEqual(doc.body_markdown, "")
        self.assertEqual(doc.suggestions, ())
        self.assertEqual(doc.ledger, ())
        self.assertEqual(doc.signoffs, ())
        self.assertEqual(doc.render(), markdown)

    def test_parse_render_is_hash_stable(self) -> None:
        markdown = _doc().render()
        first = MenxiaGroupDoc.parse(markdown)
        second = MenxiaGroupDoc.parse(first.render())
        self.assertEqual(first.content_hash(), second.content_hash())

    def test_missing_baseline_section_defaults_empty(self) -> None:
        markdown = "\n".join([
            "# t [v2]",
            "## 1. 方案正文",
            "正文",
            "## 2. 建议段",
            "## 3. 吸收记录",
            "## 4. 签核区",
        ])
        doc = MenxiaGroupDoc.parse(markdown)
        self.assertEqual(doc.version, 2)
        self.assertEqual(doc.baseline_markdown, "")

    def test_rejects_empty_and_bad_title(self) -> None:
        with self.assertRaises(MenxiaDocError):
            MenxiaGroupDoc.parse("   ")
        with self.assertRaises(MenxiaDocError):
            MenxiaGroupDoc.parse("# 没有版本号")
        with self.assertRaises(MenxiaDocError):
            MenxiaGroupDoc.parse("# t [v0]")

    def test_rejects_missing_required_sections(self) -> None:
        with self.assertRaises(MenxiaDocError):
            MenxiaGroupDoc.parse("# t [v1]\n## 2. 建议段")

    def test_extra_sections_are_preserved(self) -> None:
        markdown = "\n".join([
            "# t [v1]",
            "## 1. 方案正文",
            "正文",
            "## 2. 建议段",
            "## 9. 附件",
            "额外内容",
        ])
        doc = MenxiaGroupDoc.parse(markdown)
        self.assertEqual(
            doc.extra_sections, ((9, "附件", "额外内容"),)
        )


class TestSuggestionLifecycle(unittest.TestCase):
    def test_rejected_p0_stays_pending_until_author_confirms(self) -> None:
        doc = _doc().add_suggestion(
            author="analyst", severity="P0", body="缺回滚方案"
        )
        suggestion = doc.suggestions[0]
        self.assertEqual(suggestion.status, SUGGESTION_OPEN)
        self.assertTrue(suggestion.blocking)

        rejected = doc.reject(suggestion.suggestion_id, reason="范围外")
        self.assertEqual(
            rejected.suggestions[0].status, SUGGESTION_REJECTED_PENDING
        )
        self.assertFalse(rejected.converged())

        insisted = rejected.confirm_rejection(suggestion.suggestion_id, accept=False)
        self.assertEqual(insisted.suggestions[0].status, SUGGESTION_OPEN)

        closed = rejected.confirm_rejection(suggestion.suggestion_id, accept=True)
        self.assertEqual(
            closed.suggestions[0].status, SUGGESTION_CLOSED_ACCEPTED
        )
        self.assertTrue(closed.suggestions[0].resolved)

    def test_confirm_rejection_requires_pending_status(self) -> None:
        doc = _doc().add_suggestion(
            author="critic", severity="P2", body="小问题"
        )
        with self.assertRaises(MenxiaDocError):
            doc.confirm_rejection(doc.suggestions[0].suggestion_id, accept=True)

    def test_non_blocking_rejection_auto_closes(self) -> None:
        doc = _doc().add_suggestion(
            author="critic", severity="P3", body="措辞"
        )
        rejected = doc.reject(doc.suggestions[0].suggestion_id, reason="不改")
        closed = rejected.auto_close_stale_rejections()
        self.assertEqual(closed.suggestions[0].status, SUGGESTION_CLOSED_AUTO)
        self.assertEqual(
            rejected.auto_close_stale_rejections().suggestions[0].status,
            SUGGESTION_CLOSED_AUTO,
        )

    def test_auto_close_ignores_blocking_rejections(self) -> None:
        doc = _doc().add_suggestion(
            author="analyst", severity="P1", body="严重"
        )
        rejected = doc.reject(doc.suggestions[0].suggestion_id, reason="不做")
        self.assertEqual(
            rejected.auto_close_stale_rejections().suggestions[0].status,
            SUGGESTION_REJECTED_PENDING,
        )

    def test_defer_is_p2_p3_only(self) -> None:
        doc = _doc().add_suggestion(
            author="critic", severity="P2", body="优化"
        )
        deferred = doc.defer(
            doc.suggestions[0].suggestion_id, target_version=3
        )
        self.assertEqual(deferred.suggestions[0].status, SUGGESTION_DEFERRED)
        self.assertEqual(deferred.suggestions[0].deferred_target, 3)

        blocking = _doc().add_suggestion(
            author="analyst", severity="P0", body="必须"
        )
        with self.assertRaises(MenxiaDocError):
            blocking.defer(blocking.suggestions[0].suggestion_id, target_version=3)

    def test_absorb_moves_suggestion_into_ledger(self) -> None:
        doc = _doc().add_suggestion(
            author="analyst", severity="P1", body="补监控"
        )
        suggestion_id = doc.suggestions[0].suggestion_id
        absorbed = doc.absorb(suggestion_id, note="v2 已加")
        self.assertEqual(absorbed.suggestions, ())
        self.assertEqual(len(absorbed.ledger), 1)
        self.assertEqual(absorbed.ledger[0].suggestion_ids, (suggestion_id,))
        self.assertIn(suggestion_id, absorbed.ledger[0].text)

        with self.assertRaises(MenxiaDocError):
            absorbed.absorb(suggestion_id, note="重复")

    def test_absorb_merges_entries_of_same_version(self) -> None:
        doc = _doc()
        first = doc.add_suggestion(author="analyst", severity="P2", body="a")
        second = first.add_suggestion(author="critic", severity="P3", body="b")
        merged = second.absorb(
            first.suggestions[0].suggestion_id, note="x"
        ).absorb(second.suggestions[1].suggestion_id, note="y")
        self.assertEqual(len(merged.ledger), 1)
        self.assertEqual(len(merged.ledger[0].suggestion_ids), 2)

    def test_ids_are_never_reused_after_absorb(self) -> None:
        doc = _doc().add_suggestion(author="analyst", severity="P2", body="a")
        absorbed = doc.absorb(doc.suggestions[0].suggestion_id, note="n")
        again = absorbed.add_suggestion(author="critic", severity="P2", body="b")
        self.assertNotEqual(
            again.suggestions[0].suggestion_id, doc.suggestions[0].suggestion_id
        )

    def test_prune_closed_keeps_unresolved(self) -> None:
        doc = _doc()
        doc = doc.add_suggestion(author="analyst", severity="P0", body="a")
        doc = doc.add_suggestion(author="critic", severity="P3", body="b")
        doc = doc.reject(doc.suggestions[1].suggestion_id, reason="n")
        doc = doc.auto_close_stale_rejections()
        pruned = doc.prune_closed()
        self.assertEqual(len(pruned.suggestions), 1)
        self.assertEqual(pruned.suggestions[0].severity, "P0")

    def test_add_suggestion_validates_author_and_severity(self) -> None:
        with self.assertRaises(MenxiaDocError):
            _doc().add_suggestion(author="solver", severity="P1", body="x")
        with self.assertRaises(MenxiaDocError):
            _doc().add_suggestion(author="analyst", severity="P9", body="x")


class TestConvergence(unittest.TestCase):
    def test_converged_requires_clean_blocking_section_and_fresh_signoff(self) -> None:
        doc = _doc().add_suggestion(
            author="analyst", severity="P1", body="严重"
        )
        signed = (
            doc.sign_doc(author="analyst", approved=True)
            .sign_doc(author="critic", approved=True)
        )
        self.assertFalse(signed.converged(), "open P1 阻塞收敛")

        absorbed = signed.absorb(doc.suggestions[0].suggestion_id, note="done")
        resigned = absorbed.sign_doc(
            author="analyst", approved=True
        ).sign_doc(author="critic", approved=True)
        resigned = MenxiaGroupDoc.parse(resigned.render())
        self.assertTrue(resigned.converged())

    def test_signoff_only_counts_current_version(self) -> None:
        doc = _doc().sign_doc(
            author="analyst", approved=True
        ).sign_doc(author="critic", approved=True)
        self.assertTrue(doc.approved_by("analyst"))
        bumped = doc.with_version(2)
        self.assertFalse(bumped.approved_by("analyst"))
        self.assertFalse(bumped.converged())


class TestVerifyAbsorption(unittest.TestCase):
    def test_clean_solver_round(self) -> None:
        before = _doc().add_suggestion(author="analyst", severity="P1", body="a")
        after = before.absorb(before.suggestions[0].suggestion_id, note="ok")
        self.assertEqual(
            verify_absorption(
                before,
                after,
                absorbed_ids=(before.suggestions[0].suggestion_id,),
            ),
            (),
        )

    def test_vanished_without_claim_is_a_breach(self) -> None:
        before = _doc().add_suggestion(author="analyst", severity="P1", body="a")
        after = before.absorb(before.suggestions[0].suggestion_id, note="ok")
        errors = verify_absorption(before, after, absorbed_ids=())
        self.assertEqual(len(errors), 1)
        self.assertIn("without an absorbed/rejected claim", errors[0])

    def test_unknown_claimed_id_is_a_breach(self) -> None:
        before = _doc()
        after = before
        errors = verify_absorption(before, after, absorbed_ids=("S-099",))
        self.assertEqual(len(errors), 1)
        self.assertIn("never a suggestion", errors[0])

    def test_absorbed_id_must_leave_the_section(self) -> None:
        before = _doc().add_suggestion(author="analyst", severity="P2", body="a")
        errors = verify_absorption(
            before,
            before,
            absorbed_ids=(before.suggestions[0].suggestion_id,),
        )
        self.assertEqual(len(errors), 1)
        self.assertIn("still in the section", errors[0])

    def test_rejected_claim_keeps_suggestion_pending(self) -> None:
        before = _doc().add_suggestion(author="analyst", severity="P0", body="a")
        after = before.reject(before.suggestions[0].suggestion_id, reason="n")
        suggestion_id = before.suggestions[0].suggestion_id
        self.assertEqual(
            verify_absorption(before, after, rejected_ids=(suggestion_id,)), ()
        )

    def test_cannot_absorb_a_rejection_awaiting_confirmation(self) -> None:
        base = _doc().add_suggestion(author="analyst", severity="P0", body="a")
        before = base.reject(base.suggestions[0].suggestion_id, reason="n")
        suggestion_id = before.suggestions[0].suggestion_id
        forced = before.absorb(suggestion_id, note="偷删")
        errors = verify_absorption(before, forced, absorbed_ids=(suggestion_id,))
        self.assertEqual(len(errors), 1)
        self.assertIn("cannot absorb", errors[0])

    def test_flip_to_pending_without_claim_is_a_breach(self) -> None:
        before = _doc().add_suggestion(author="critic", severity="P1", body="a")
        after = before.reject(before.suggestions[0].suggestion_id, reason="n")
        errors = verify_absorption(before, after, rejected_ids=())
        self.assertEqual(len(errors), 1)
        self.assertIn("without a claim", errors[0])


class TestChangedBodySections(unittest.TestCase):
    def test_reports_changed_added_and_removed(self) -> None:
        before = _doc(
            body_markdown="## 步骤\n\n先做 A。\n\n## 风险\n\n低。\n\n## 删除项\n\n内容"
        )
        after = _doc(
            body_markdown="## 步骤\n\n先做 B。\n\n## 风险\n\n低。\n\n## 新增\n\n新内容"
        )
        self.assertEqual(
            changed_body_sections(before, after),
            ("删除项", "新增", "步骤"),
        )

    def test_unchanged_body_is_silent(self) -> None:
        doc = _doc()
        self.assertEqual(changed_body_sections(doc, doc), ())


class TestVerifyGroupReply(unittest.TestCase):
    def _reply(self, **overrides) -> dict[str, object]:
        reply: dict[str, object] = {
            "doc_markdown": "",
            "absorbed_ids": (),
            "rejected_ids": (),
            "touched_scope": (),
        }
        reply.update(overrides)
        return reply

    def test_solver_happy_path(self) -> None:
        previous = MenxiaGroupDoc.parse(render_initial(
            group_id="g1", baseline_markdown="基线",
            body_markdown=_six_part_body(),
        ))
        evolved = MenxiaGroupDoc.parse(
            previous.render().replace("[v1]", "[v2]")
            .replace("按基线执行，不做额外决策。", "按基线执行，追加 B。")
        )
        reply = self._reply(
            doc_markdown=evolved.render(),
            touched_scope=("目标与设计决策",),
        )
        self.assertEqual(
            verify_group_reply(previous, role="solver", reply=reply), ()
        )

    def test_solver_initial_fill_keeps_skeleton_version(self) -> None:
        # Regression: task-20260923-613874 parked every group as
        # MENXIA_GROUP_DOC_BREACH because the first solver round was forced
        # to advance the skeleton version 1 -> 2 while the natural (and
        # contract-consistent) reply keeps [v1].  Filling the skeleton body
        # at the skeleton version, without a touched_scope list, must fold.
        previous = MenxiaGroupDoc.parse(render_initial(
            group_id="g1", baseline_markdown="基线",
        ))
        filled = previous.render().replace(
            "## 2. 建议段",
            _six_part_body() + "\n\n## 2. 建议段",
        )
        reply = self._reply(
            doc_markdown=filled,
            touched_scope=(),
        )
        self.assertEqual(
            verify_group_reply(previous, role="solver", reply=reply), ()
        )

    def test_solver_initial_fill_must_write_a_body(self) -> None:
        previous = MenxiaGroupDoc.parse(render_initial(
            group_id="g1", baseline_markdown="基线",
        ))
        reply = self._reply(doc_markdown=previous.render())
        errors = verify_group_reply(previous, role="solver", reply=reply)
        self.assertTrue(any("fill the empty document body" in e for e in errors))

    def test_solver_initial_fill_rejects_version_jump(self) -> None:
        previous = MenxiaGroupDoc.parse(render_initial(
            group_id="g1", baseline_markdown="基线",
        ))
        jumped = previous.render().replace("[v1]", "[v3]").replace(
            "## 2. 建议段",
            "## 1.1 执行方案\n\n做 A。\n\n## 2. 建议段",
        )
        reply = self._reply(doc_markdown=jumped)
        errors = verify_group_reply(previous, role="solver", reply=reply)
        self.assertTrue(any("keep or advance the version" in e for e in errors))

    def test_solver_version_must_advance_by_one(self) -> None:
        # Revision round: the previous document carries a real body, so the
        # solver must advance the version by exactly one.
        previous = MenxiaGroupDoc.parse(render_initial(
            group_id="g1", baseline_markdown="b", body_markdown="## 步骤\n\n做 A。",
        ))
        reply = self._reply(doc_markdown=previous.render())
        errors = verify_group_reply(previous, role="solver", reply=reply)
        self.assertTrue(any("advance the version" in e for e in errors))

    def test_solver_absorption_needs_claim(self) -> None:
        previous = MenxiaGroupDoc.parse(render_initial(group_id="g1", baseline_markdown="b"))
        with_suggestion = previous.add_suggestion(
            author="analyst", severity="P1", body="改"
        )
        after_review = MenxiaGroupDoc.parse(with_suggestion.render())
        suggestion_id = with_suggestion.suggestions[0].suggestion_id
        reply = self._reply(
            doc_markdown=MenxiaGroupDoc(
                **{**after_review.__dict__, "version": 2, "suggestions": ()}
            ).render(),
        )
        errors = verify_group_reply(after_review, role="solver", reply=reply)
        self.assertTrue(
            any(suggestion_id in e and "claim" in e for e in errors)
        )

    def test_solver_undeclared_body_change_is_rejected(self) -> None:
        previous = MenxiaGroupDoc.parse(render_initial(
            group_id="g1", baseline_markdown="b", body_markdown="## 步骤\n\n做 A。",
        ))
        evolved = MenxiaGroupDoc.parse(
            previous.render().replace("[v1]", "[v2]").replace("做 A。", "做 C。")
        )
        reply = self._reply(doc_markdown=evolved.render(), touched_scope=())
        errors = verify_group_reply(previous, role="solver", reply=reply)
        self.assertTrue(any("touched_scope" in e for e in errors))

    def test_reviewer_cannot_touch_frozen_sections(self) -> None:
        previous = MenxiaGroupDoc.parse(render_initial(
            group_id="g1", baseline_markdown="b", body_markdown="## 步骤\n\n做 A。",
        ))
        mutated = previous.render().replace("做 A。", "做 B。")
        reply = self._reply(doc_markdown=mutated)
        errors = verify_group_reply(previous, role="analyst", reply=reply)
        self.assertTrue(any("frozen sections" in e for e in errors))

    def test_reviewer_appends_suggestions_only(self) -> None:
        previous = MenxiaGroupDoc.parse(render_initial(group_id="g1", baseline_markdown="b"))
        with_line = previous.render().replace(
            "## 2. 建议段",
            "## 2. 建议段\n[S-001][analyst][v1 base][P1][open] 补回滚",
        )
        reply = self._reply(
            doc_markdown=with_line,
            added_suggestion_ids=("S-001",),
        )
        self.assertEqual(
            verify_group_reply(previous, role="analyst", reply=reply), ()
        )

    def test_reviewer_claim_must_match_appended_ids(self) -> None:
        previous = MenxiaGroupDoc.parse(render_initial(group_id="g1", baseline_markdown="b"))
        with_line = previous.render().replace(
            "## 2. 建议段",
            "## 2. 建议段\n[S-001][analyst][v1 base][P1][open] 补回滚",
        )
        reply = self._reply(
            doc_markdown=with_line,
            added_suggestion_ids=("S-002",),
        )
        errors = verify_group_reply(previous, role="analyst", reply=reply)
        self.assertTrue(any("added_suggestion_ids" in e for e in errors))

    def test_reviewer_cannot_impersonate(self) -> None:
        previous = MenxiaGroupDoc.parse(render_initial(group_id="g1", baseline_markdown="b"))
        with_line = previous.render().replace(
            "## 2. 建议段",
            "## 2. 建议段\n[S-001][critic][v1 base][P1][open] 补回滚",
        )
        reply = self._reply(
            doc_markdown=with_line,
            added_suggestion_ids=("S-001",),
        )
        errors = verify_group_reply(previous, role="analyst", reply=reply)
        self.assertTrue(any("author" in e for e in errors))

    def test_reviewer_cannot_edit_existing_suggestion(self) -> None:
        base = MenxiaGroupDoc.parse(render_initial(group_id="g1", baseline_markdown="b"))
        previous = MenxiaGroupDoc.parse(
            base.render().replace(
                "## 2. 建议段",
                "## 2. 建议段\n[S-001][analyst][v1 base][P1][open] 补回滚",
            )
        )
        reply = self._reply(
            doc_markdown=previous.render().replace("补回滚", "改措辞"),
            added_suggestion_ids=(),
        )
        errors = verify_group_reply(previous, role="critic", reply=reply)
        self.assertTrue(any("S-001" in e and "edited" in e for e in errors))

    def test_oversized_doc_is_rejected(self) -> None:
        previous = MenxiaGroupDoc.parse(render_initial(group_id="g1", baseline_markdown="b"))
        reply = self._reply(doc_markdown="x" * 200_000)
        errors = verify_group_reply(previous, role="solver", reply=reply)
        self.assertTrue(any("exceeds" in e for e in errors))

    def test_unparseable_doc_is_rejected(self) -> None:
        previous = MenxiaGroupDoc.parse(render_initial(group_id="g1", baseline_markdown="b"))
        reply = self._reply(doc_markdown="完全没有标题")
        errors = verify_group_reply(previous, role="solver", reply=reply)
        self.assertTrue(any("does not parse" in e for e in errors))

    def test_first_solver_round_needs_no_previous(self) -> None:
        previous = None
        doc = MenxiaGroupDoc.parse(render_initial(group_id="g1", baseline_markdown="b"))
        reply = self._reply(doc_markdown=doc.render())
        self.assertEqual(
            verify_group_reply(previous, role="solver", reply=reply), ()
        )
        errors = verify_group_reply(previous, role="analyst", reply=reply)
        self.assertTrue(any("no previous document" in e for e in errors))


def _requirement_markdown(signals: tuple[str, ...]) -> str:
    """A nine-section zhongshu requirement document with §8 acceptance lines."""

    sections = (
        "背景", "目标", "标识与范围", "状态与边界语义", "行为要求",
        "责任边界", "交叉不变量", "验收标准", "非目标",
    )
    lines = ["# group-000001 需求文档 [v1]"]
    for number, name in enumerate(sections, start=1):
        lines.append(f"## {number}. {name}")
        if name == "验收标准":
            lines.extend(signals)
    return "\n".join(lines)


def _solver_fill_reply(body_markdown: str) -> dict[str, object]:
    return {
        "doc_markdown": body_markdown,
        "absorbed_ids": (),
        "rejected_ids": (),
        "touched_scope": (),
    }


class TestBodySixParts(unittest.TestCase):
    """The plan body must carry the six fixed subsections (Task 9)."""

    def test_solver_body_missing_part_violation(self) -> None:
        previous = MenxiaGroupDoc.parse(render_initial(
            group_id="g1", baseline_markdown="基线",
        ))
        partial = "\n\n".join(
            section
            for section in _six_part_body().split("\n\n")
            if not section.startswith("### 验收映射")
        )
        filled = MenxiaGroupDoc(
            **{**previous.__dict__, "body_markdown": partial}
        ).render()
        errors = verify_group_reply(
            previous, role="solver", reply=_solver_fill_reply(filled),
        )
        self.assertTrue(
            any("body_missing_part:验收映射" in error for error in errors),
            errors,
        )

    def test_body_six_parts_present_passes(self) -> None:
        previous = MenxiaGroupDoc.parse(render_initial(
            group_id="g1", baseline_markdown="基线",
        ))
        filled = MenxiaGroupDoc(
            **{**previous.__dict__, "body_markdown": _six_part_body()}
        ).render()
        self.assertEqual(
            verify_group_reply(
                previous, role="solver", reply=_solver_fill_reply(filled),
            ),
            (),
        )

    def test_body_parts_out_of_order_violation(self) -> None:
        previous = MenxiaGroupDoc.parse(render_initial(
            group_id="g1", baseline_markdown="基线",
        ))
        sections = _six_part_body().split("\n\n")
        reordered = "\n\n".join([sections[1], sections[0]] + sections[2:])
        filled = MenxiaGroupDoc(
            **{**previous.__dict__, "body_markdown": reordered}
        ).render()
        errors = verify_group_reply(
            previous, role="solver", reply=_solver_fill_reply(filled),
        )
        self.assertTrue(
            any("body_part_order" in error for error in errors), errors
        )


class TestApprovalBlockers(unittest.TestCase):
    """Critic APPROVE_GROUP blockers beyond the suggestion ledger (Task 9)."""

    def test_undecided_marker_blocks_approval(self) -> None:
        body = _six_part_body() + "\n待定：后续补充。"
        blockers = menxia_approval_blockers(
            body, requirement_markdown=_requirement_markdown(
                ("Task A is observable",)
            ),
        )
        self.assertTrue(
            any("undecided_marker" in blocker for blocker in blockers),
            blockers,
        )

    def test_exception_row_without_three_elements_blocks(self) -> None:
        body = _six_part_body(extra="\n- 例外：跳过校验。")
        blockers = menxia_approval_blockers(
            body, requirement_markdown=_requirement_markdown(
                ("Task A is observable",)
            ),
        )
        self.assertTrue(
            any("exception_row" in blocker for blocker in blockers), blockers
        )

    def test_exception_row_with_elements_passes(self) -> None:
        body = _six_part_body(
            extra="\n- 例外：跳过校验；替代验证：人工抽查；补测触发：下个迭代。"
        )
        self.assertEqual(
            menxia_approval_blockers(
                body, requirement_markdown=_requirement_markdown(
                    ("Task A is observable",)
                ),
            ),
            (),
        )

    def test_acceptance_map_beyond_requirement_blocks(self) -> None:
        body = _six_part_body(
            acceptance_entries=(
                "Task A is observable", "Ghost is observable",
            ),
        )
        blockers = menxia_approval_blockers(
            body, requirement_markdown=_requirement_markdown(
                ("Task A is observable",)
            ),
        )
        self.assertTrue(
            any(
                "acceptance_map_orphan" in blocker
                and "Ghost" in blocker
                for blocker in blockers
            ),
            blockers,
        )

    def test_clean_body_has_no_blockers(self) -> None:
        self.assertEqual(
            menxia_approval_blockers(
                _six_part_body(acceptance_entries=("Task A is observable",)),
                requirement_markdown=_requirement_markdown(
                    ("Task A is observable",)
                ),
            ),
            (),
        )

    def test_missing_requirement_markdown_skips_map_closure(self) -> None:
        # Legacy rows without a requirement document only get the marker and
        # exception-row checks; the map closure needs the §8 source.
        self.assertEqual(
            menxia_approval_blockers(
                _six_part_body(
                    acceptance_entries=("Anything goes",)
                ),
                requirement_markdown="",
            ),
            (),
        )


class TestRequirementMarkdownTravels(unittest.TestCase):
    """The frozen requirement doc rides the menxia row and dispatch."""

    def _review(self) -> ReviewState:
        return ReviewState(
            revision_id="rev-1",
            task_items=(
                ReviewTaskItem("item-000001", "group-000001", order=0),
                ReviewTaskItem("item-000002", "group-000002", order=1),
            ),
            task_groups=(
                ReviewTaskGroup("group-000001", ("item-000001",), order=0),
                ReviewTaskGroup("group-000002", ("item-000002",), order=1),
            ),
            zhongshu_groups=(
                ZhongshuGroupState(
                    "group-000001",
                    stage="FROZEN",
                    doc_markdown=_requirement_markdown(
                        ("Task A is observable",)
                    ),
                ),
                ZhongshuGroupState("group-000002", stage="REVIEWING"),
            ),
        )

    def test_seeded_menxia_row_carries_requirement_markdown(self) -> None:
        rows = {
            row.group_id: row for row in self._review().seed_menxia_groups()
        }
        self.assertEqual(
            rows["group-000001"].requirement_markdown,
            _requirement_markdown(("Task A is observable",)),
        )
        self.assertNotIn("group-000002", rows)

    def test_dispatch_context_carries_requirement_markdown(self) -> None:
        from orchestrator.domain.states import _menxia_group_dispatch_context

        review = self._review()
        row = review.seed_menxia_groups()[0]
        context = WorkflowContext(
            identity=TaskIdentity("task-1", "issue-1", "p", "r"),
            progression=ProgressState("MENXIA_GROUP_SOLVER", 3, "2026-09-24T00:00:00Z"),
            review=review,
            parallel=ParallelState(
                menxia=MenxiaParallelLimits(
                    enabled=True, max_concurrent_groups=2, max_concurrent_items=3,
                )
            ),
        )
        dispatch = _menxia_group_dispatch_context(
            context,
            "MENXIA_GROUP_SOLVER",
            group_id="group-000001",
            revision_id="rev-1",
            plan_hash="",
            row=row,
        )
        self.assertIsNotNone(dispatch)
        self.assertEqual(
            dispatch["requirement_markdown"],
            _requirement_markdown(("Task A is observable",)),
        )


if __name__ == "__main__":
    unittest.main()
