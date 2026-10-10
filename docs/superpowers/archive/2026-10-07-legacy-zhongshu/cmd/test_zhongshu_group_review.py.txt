"""Group review unit for Zhongshu: one review job per group (2026-09-26).

The review wave's dispatch unit is the group (requirements §2/§5.2): one
group_review job carries the requirement document, every member item
(changed members verbatim, unchanged members as surface+hash), the merged
findings and per-member evidence slices.  The verdict is one group action
(``APPROVE_GROUP`` / ``REVISE_GROUP`` / ...); findings keep their item
coordinates for revision targeting and evidence routing.
"""

from __future__ import annotations

import unittest

from orchestrator.zhongshu_review_queue import (
    build_group_capsule,
    build_group_review_jobs,
    group_surface_hash,
    structural_gate,
    task_surface_hash,
)


def _item(item_id: str, group_id: str, objective: str = "") -> dict:
    return {
        "item_id": item_id,
        "group_id": group_id,
        "title": item_id,
        "objective": objective or f"objective-{item_id}",
        "source_requirement_ids": ["req-000001"],
        "dependencies": [],
        "acceptance_signals": [f"accept-{item_id}"],
    }


def _plan(group_sizes=(2, 2)) -> dict:
    items: list[dict] = []
    groups: list[dict] = []
    counter = 0
    for index, size in enumerate(group_sizes, start=1):
        group_id = f"group-{index:06d}"
        member_ids = []
        for _ in range(size):
            counter += 1
            item_id = f"item-{counter:06d}"
            items.append(_item(item_id, group_id))
            member_ids.append(item_id)
        groups.append({"group_id": group_id, "item_ids": member_ids})
    return {"items": items, "groups": groups, "requirements": []}


DOC_MARKDOWN = "# group-000001 需求文档 [v1]\n## 8. 验收标准\n### item-000001\naccept-item-000001\n### item-000002\naccept-item-000002"


class BuildGroupReviewJobsTests(unittest.TestCase):
    def test_one_job_per_group(self) -> None:
        jobs = build_group_review_jobs(
            _plan(),
            "R1",
            plan_hash="plan-hash",
            doc_hashes={"group-000001": "doc-hash-1", "group-000002": "doc-hash-2"},
        )
        self.assertEqual([job.group_id for job in jobs], ["group-000001", "group-000002"])
        self.assertEqual(jobs[0].review_job_id, "zhongshu:R1:group-000001")
        self.assertTrue(all(job.task_hash for job in jobs))
        self.assertNotEqual(jobs[0].task_hash, jobs[1].task_hash)

    def test_group_surface_hash_covers_doc_and_members(self) -> None:
        plan = _plan(group_sizes=(2,))
        members = [
            item for item in plan["items"] if item["group_id"] == "group-000001"
        ]
        member_hashes = [
            task_surface_hash(item, ["item-000001", "item-000002"]) for item in members
        ]
        base = group_surface_hash("group-000001", member_hashes, doc_hash="d1")
        self.assertEqual(
            base,
            group_surface_hash("group-000001", list(reversed(member_hashes)), doc_hash="d1"),
        )
        self.assertNotEqual(
            base, group_surface_hash("group-000001", member_hashes, doc_hash="d2")
        )


class GroupMaxItemsGateTests(unittest.TestCase):
    def test_oversized_group_is_a_structural_violation(self) -> None:
        issues = structural_gate(_plan(group_sizes=(7,)))
        self.assertIn("GROUP_MAX_ITEMS:group-000001:7", issues)

    def test_sized_group_passes(self) -> None:
        issues = structural_gate(_plan(group_sizes=(6,)))
        self.assertNotIn("GROUP_MAX_ITEMS:group-000001:6", issues)


class GroupCapsuleTests(unittest.TestCase):
    def _capsule(self, changed_ids=("item-000001",)) -> str:
        plan = _plan(group_sizes=(2,))
        members = [
            item for item in plan["items"] if item["group_id"] == "group-000001"
        ]
        member_ids = [item["item_id"] for item in members]
        changed = [item for item in members if item["item_id"] in changed_ids]
        surface = [
            (item["item_id"], task_surface_hash(item, member_ids))
            for item in members
            if item["item_id"] not in changed_ids
        ]
        return build_group_capsule(
            group_id="group-000001",
            revision_id="R1",
            doc_markdown=DOC_MARKDOWN,
            members_full=changed,
            members_surface=surface,
            findings=(
                {"finding_id": "f-1", "item_id": "item-000001", "claim": "claim-1"},
            ),
            finding_responses=(),
            evidence=(("item-000001", ("ev-000001",)),),
        )

    def test_capsule_carries_doc_and_changed_members(self) -> None:
        capsule = self._capsule()
        self.assertIn("需求文档 [v1]", capsule)
        self.assertIn("objective-item-000001", capsule)
        self.assertIn("claim-1", capsule)
        self.assertIn("ev-000001", capsule)

    def test_unchanged_members_surface_only(self) -> None:
        capsule = self._capsule()
        self.assertNotIn("objective-item-000002", capsule)
        self.assertIn("item-000002", capsule)
        self.assertIn("surface_hash", capsule)


class GroupReviewContractTests(unittest.TestCase):
    def test_group_mode_actions(self) -> None:
        from orchestrator.contracts.zhongshu_critic import (
            GROUP_MODE_ACTIONS,
            allowed_actions_for_mode,
        )

        self.assertEqual(
            tuple(allowed_actions_for_mode("REVIEW_GROUP")),
            (
                "APPROVE_GROUP",
                "REVISE_GROUP",
                "REQUEST_ANALYST_EVIDENCE",
                "HUMAN_GATE",
                "BLOCKED",
            ),
        )
        self.assertEqual(GROUP_MODE_ACTIONS, allowed_actions_for_mode("REVIEW_GROUP"))

    def test_review_group_is_a_declared_mode(self) -> None:
        from orchestrator.contracts.zhongshu_critic import _MODES

        self.assertIn("REVIEW_GROUP", _MODES)

    def test_legacy_action_names_normalize_to_group_verdicts(self) -> None:
        from orchestrator.contracts.zhongshu_critic import normalize_group_action

        # Live run task-20260927-862584: the group reviewer answered the
        # revision with the legacy REQUEST_SOLVER_REVISION and the wave was
        # rejected.  Vocabulary drift must not cost a whole wave.
        self.assertEqual(
            normalize_group_action("REQUEST_SOLVER_REVISION"), "REVISE_GROUP"
        )
        self.assertEqual(
            normalize_group_action("TASK_CHANGES_REQUIRED"), "REVISE_GROUP"
        )
        self.assertEqual(normalize_group_action("approve_group"), "APPROVE_GROUP")
        self.assertEqual(normalize_group_action("APPROVE_FREEZE"), "APPROVE_GROUP")
        self.assertEqual(normalize_group_action("TASK_APPROVED"), "APPROVE_GROUP")
        self.assertEqual(normalize_group_action("NOT_AN_ACTION"), "")

    def test_finding_scope_normalizes_target_only_owner(self) -> None:
        from orchestrator.contracts.zhongshu_critic import group_finding_scope

        item_id, error = group_finding_scope(
            {"finding_id": "f-1", "target": "group-000001/item-000002.acceptance_signals"},
            {"item-000001", "item-000002"},
        )
        self.assertEqual(error, "")
        self.assertEqual(item_id, "item-000002")

    def test_finding_without_owner_is_rejected(self) -> None:
        from orchestrator.contracts.zhongshu_critic import group_finding_scope

        item_id, error = group_finding_scope(
            {"finding_id": "f-1", "target": "the whole picture"},
            {"item-000001", "item-000002"},
        )
        self.assertEqual(item_id, "")
        self.assertTrue(error.startswith("GROUP_FINDING_UNSCOPED:"), error)


class GroupBindingTests(unittest.TestCase):
    """Wave bindings: one per group, ratchet/frozen groups never dispatch."""

    def _doc(
        self,
        group_id: str = "group-000001",
        item_ids: tuple[str, ...] = ("item-000001", "item-000002"),
    ) -> str:
        sections = (
            "背景", "目标", "标识与范围", "状态与边界语义", "行为要求",
            "责任边界", "交叉不变量", "验收标准", "非目标",
        )
        lines = [f"# {group_id} 需求文档 [v1]"]
        for number, name in enumerate(sections, start=1):
            lines.append(f"## {number}. {name}")
            if number == 1:
                lines.append("现状可观察。")
            elif number == 2:
                lines.append("只能存在一个入口。")
            elif number == 8:
                for item_id in item_ids:
                    lines.append(f"### {item_id}")
                    lines.append(f"accept-{item_id}")
            elif number == 9:
                lines.append("不做实现。")
        return "\n".join(lines)

    def _review(self, *, frozen=False, approved=False):
        from orchestrator.domain.context import (
            ReviewState,
            ReviewTaskGroup,
            ReviewTaskItem,
            ReviewTaskRecord,
            ZhongshuGroupState,
        )

        items = (
            ReviewTaskItem(
                item_id="item-000001",
                group_id="group-000001",
                order=1,
                title="item-000001",
                objective="objective-item-000001",
                acceptance_signals=("accept-item-000001",),
            ),
            ReviewTaskItem(
                item_id="item-000002",
                group_id="group-000001",
                order=2,
                title="item-000002",
                objective="objective-item-000002",
                acceptance_signals=("accept-item-000002",),
            ),
        )
        rows = (
            ZhongshuGroupState(
                group_id="group-000001",
                stage="FROZEN" if frozen else "REVIEWING",
                doc_version=1,
                doc_hash="doc-hash-1",
                doc_markdown=self._doc(),
            ),
        )
        ledger = ()
        if approved:
            from orchestrator.domain.policies.zhongshu_group import (
                apply_group_verdict,
                current_member_hashes,
            )

            review = ReviewState(
                revision_id="R1",
                task_items=items,
                task_groups=(
                    ReviewTaskGroup(
                        group_id="group-000001",
                        item_ids=("item-000001", "item-000002"),
                        order=1,
                    ),
                ),
                zhongshu_groups=rows,
            )
            ledger, rows = apply_group_verdict(
                review,
                "group-000001",
                "APPROVE_GROUP",
                member_hashes=current_member_hashes(review, "group-000001"),
            )
        return ReviewState(
            revision_id="R1",
            task_items=items,
            task_groups=(
                ReviewTaskGroup(
                    group_id="group-000001",
                    item_ids=("item-000001", "item-000002"),
                    order=1,
                ),
            ),
            task_review_ledger=ledger,
            zhongshu_groups=rows,
        )

    def _context(self, review) -> "object":
        from orchestrator.domain.context import (
            ProgressState,
            RequestState,
            TaskIdentity,
            WorkflowContext,
        )

        return WorkflowContext(
            identity=TaskIdentity("task-g", "issue-g", "", "request-g"),
            progression=ProgressState("ZHONGSHU_CRITIC", 3, "2026-09-26T00:00:00Z"),
            request=RequestState(raw_request="review", project_type="python", task_type="review"),
            review=review,
        )

    def _bindings(self, review):
        from orchestrator.domain.states import _group_review_bindings

        return _group_review_bindings(self._context(review), "task-g:4", "plan-hash")

    def test_one_binding_per_group_with_doc_and_members(self) -> None:
        bindings = self._bindings(self._review())
        self.assertEqual(len(bindings), 1)
        binding = bindings[0]
        self.assertEqual(binding["group_id"], "group-000001")
        self.assertEqual(
            binding["dispatch_context"]["zhongshu_dispatch_mode"], "group_review"
        )
        capsule = binding["prompt_ref"]
        self.assertIn("需求文档 [v1]", capsule)
        self.assertIn("objective-item-000001", capsule)
        self.assertIn("objective-item-000002", capsule)

    def test_frozen_group_gets_no_dispatch(self) -> None:
        self.assertEqual(self._bindings(self._review(frozen=True)), [])

    def test_all_held_wave_falls_back_to_re_review(self) -> None:
        # A lone held group (the Menxia gate bounce) must still produce a
        # verdict, so an all-held wave falls back to re-reviewing it - the
        # same empty-selection fallback the legacy job queue applies.
        bindings = self._bindings(self._review(approved=True))
        self.assertEqual(len(bindings), 1)

    def test_unchanged_members_surface_only(self) -> None:
        # One member already approved with a matching surface: it rides as
        # surface+hash only, the contested member carries full text.
        from dataclasses import replace

        from orchestrator.domain.policies.zhongshu_group import current_member_hashes

        review = self._review()
        hashes = current_member_hashes(review, "group-000001")
        from orchestrator.domain.context import ReviewTaskRecord

        ledger = (
            ReviewTaskRecord(
                item_id="item-000002",
                task_hash=hashes["item-000002"],
                status="APPROVED",
            ),
        )
        review = replace(review, task_review_ledger=ledger)
        capsule = self._bindings(review)[0]["prompt_ref"]
        self.assertIn("objective-item-000001", capsule)
        self.assertNotIn("objective-item-000002", capsule)
        self.assertIn("surface_hash", capsule)

    def test_unfit_group_skipped_while_fit_group_dispatches(self) -> None:
        # Drop section 6 from group-000001's document so the LLM-free
        # preflight gate flags it: the review wave covers only the fit group
        # (the unfit one is routed out instead of burning a worker round).
        from dataclasses import replace as dc_replace

        broken = "\n".join(
            line for line in self._doc().splitlines() if not line.startswith("## 6.")
        )
        review = self._review()
        review = dc_replace(
            review,
            task_items=review.task_items
            + (
                dc_replace(
                    review.task_items[0],
                    item_id="item-000003",
                    group_id="group-000002",
                    acceptance_signals=("accept-item-000003",),
                ),
            ),
            task_groups=review.task_groups
            + (
                dc_replace(
                    review.task_groups[0],
                    group_id="group-000002",
                    item_ids=("item-000003",),
                ),
            ),
            zhongshu_groups=(
                dc_replace(review.zhongshu_groups[0], doc_markdown=broken),
                dc_replace(
                    review.zhongshu_groups[0],
                    group_id="group-000002",
                    doc_markdown=self._doc("group-000002", ("item-000003",)),
                ),
            ),
        )
        bindings = self._bindings(review)
        self.assertEqual([binding["group_id"] for binding in bindings], ["group-000002"])

    def test_group_revise_bindings_per_affected_group(self) -> None:
        from orchestrator.domain.states import _group_revise_bindings

        review = self._review()
        bindings = _group_revise_bindings(
            self._context(review),
            "task-g:5",
            "plan-hash",
            affected_groups=("group-000001",),
            editable_by_group={"group-000001": ("item-000002",)},
        )
        self.assertEqual(len(bindings), 1)
        context = bindings[0]["dispatch_context"]
        self.assertEqual(context["zhongshu_dispatch_mode"], "group_revise")
        self.assertEqual(context["editable_item_ids"], ["item-000002"])


class DualTrackSwitchTests(unittest.TestCase):
    """parallel.zhongshu.review_unit selects the wave granularity."""

    def _unit(self, review_unit: str = "item") -> str:
        from orchestrator.domain.context import (
            ParallelState,
            ProgressState,
            RequestState,
            TaskIdentity,
            WorkflowContext,
            ZhongshuParallelLimits,
        )
        from orchestrator.domain.states import _review_dispatch_unit

        context = WorkflowContext(
            identity=TaskIdentity("task-g", "issue-g", "", "request-g"),
            progression=ProgressState("ZHONGSHU_CRITIC", 1, "2026-09-26T00:00:00Z"),
            request=RequestState(
                raw_request="review", project_type="python", task_type="review"
            ),
            parallel=ParallelState(
                zhongshu=ZhongshuParallelLimits(review_unit=review_unit)
            ),
        )
        return _review_dispatch_unit(context)

    def test_legacy_item_mode(self) -> None:
        self.assertEqual(self._unit("item"), "item")

    def test_group_mode(self) -> None:
        self.assertEqual(self._unit("group"), "group")


if __name__ == "__main__":
    unittest.main()
