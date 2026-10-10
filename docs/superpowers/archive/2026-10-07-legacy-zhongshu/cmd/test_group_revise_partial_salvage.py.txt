"""Group-revision partial salvage + identity feedback detail (2026-09-27).

Live run task-20260927-616863 burned all four group-revision waves
(SOLVER:11/13/15/17) on one group's mislabeled ``group_id`` while the other
group's valid patch was discarded and re-dispatched every round, and the bare
``ITEM_PATCH_IDENTITY:item-000001`` feedback left the re-ask guessing which
identity field drifted.  A refused patch must not discard the valid patches,
and the rejection must state expected/actual.
"""

from __future__ import annotations

import unittest

from orchestrator.domain.context import (
    ProgressState,
    ReviewState,
    ReviewTaskGroup,
    ReviewTaskItem,
    TaskIdentity,
    WorkflowContext,
    ZhongshuGroupState,
)
from orchestrator.domain.findings import Finding
from orchestrator.domain.policies.item_revise import materialize_item_patch
from orchestrator.domain.policies.solver_plan import merge_group_revision_items
from orchestrator.domain.states import ZhongshuCriticState
from orchestrator.runtime.agent_effects import AgentNodeJoiner
from orchestrator.runtime.nodes import WorkerResult


def _doc(group_id: str, version: int, item_ids: tuple[str, ...]) -> str:
    sections = (
        "背景", "目标", "标识与范围", "状态与边界语义", "行为要求",
        "责任边界", "交叉不变量", "验收标准", "非目标",
    )
    lines = [f"# {group_id} 需求文档 [v{version}]"]
    for number, name in enumerate(sections, start=1):
        lines.append(f"## {number}. {name}")
        if name == "背景":
            lines.append(f"{group_id} 的上下文。")
        if name == "验收标准":
            for item_id in item_ids:
                lines.append(f"### {item_id}")
                lines.append(f"accept-{item_id}")
    return "\n".join(lines)


def _plan() -> dict:
    items = []
    groups = []
    pairs = [
        ("group-000001", ("item-000001", "item-000002")),
        ("group-000002", ("item-000003", "item-000004")),
    ]
    for group_id, item_ids in pairs:
        groups.append({"group_id": group_id, "item_ids": list(item_ids)})
        for item_id in item_ids:
            items.append(
                {
                    "item_id": item_id,
                    "title": f"title-{item_id}",
                    "objective": f"objective-{item_id}",
                    "dependencies": [],
                    "source_requirement_ids": ["req-000001"],
                    "acceptance_signals": [f"accept-{item_id}"],
                }
            )
    # Real plans keep membership in ``groups``; items carry no group_id.
    return {"items": items, "groups": groups, "requirements": []}


def _patched_item(plan: dict, item_id: str, **overrides) -> dict:
    item = next(
        dict(entry) for entry in plan["items"] if entry["item_id"] == item_id
    )
    item.update(overrides)
    return item


def _previous_review() -> dict:
    return {
        "zhongshu_groups": [
            ZhongshuGroupState(
                group_id="group-000001",
                doc_version=1,
                doc_markdown=_doc("group-000001", 1, ("item-000001", "item-000002")),
            ).to_dict(),
            ZhongshuGroupState(
                group_id="group-000002",
                doc_version=1,
                doc_markdown=_doc("group-000002", 1, ("item-000003", "item-000004")),
            ).to_dict(),
        ]
    }


def _joiner() -> AgentNodeJoiner:
    return AgentNodeJoiner(
        "node:task-1:ZHONGSHU_SOLVER:11",
        task_id="task-1",
        state="ZHONGSHU_SOLVER",
        sequence=11,
        revision_id="R1",
        plan_hash="plan-hash",
        dispatch_mode="group_revise",
        base_plan=_plan(),
        previous_review=_previous_review(),
        binding_contexts={
            "worker-01": {
                "group_id": "group-000001",
                "editable_item_ids": ("item-000001", "item-000002"),
            },
            "worker-02": {
                "group_id": "group-000002",
                "editable_item_ids": ("item-000003", "item-000004"),
            },
        },
    )


def _good_payload(plan: dict) -> dict:
    return {
        "worker_id": "worker-01",
        "action": "READY_FOR_CRITIC",
        "group_id": "group-000001",
        "plan": {
            "items": [
                _patched_item(plan, "item-000001", objective="objective-fixed"),
                _patched_item(plan, "item-000002"),
            ]
        },
        "group_docs": [
            {
                "group_id": "group-000001",
                "markdown": _doc("group-000001", 2, ("item-000001", "item-000002")),
            }
        ],
        "finding_resolutions": [
            {"finding_id": "f-good", "response": "absorbed", "note": "patched"}
        ],
    }


def _bad_payload(plan: dict) -> dict:
    return {
        "worker_id": "worker-02",
        "action": "READY_FOR_CRITIC",
        "group_id": "group-000002",
        "plan": {
            "items": [
                # The incident shape: the patch relabels its owning group.
                _patched_item(
                    plan, "item-000003", group_id="group-000001", objective="x"
                ),
            ]
        },
        "group_docs": [
            {
                "group_id": "group-000002",
                "markdown": _doc("group-000002", 2, ("item-000003", "item-000004")),
            }
        ],
        "finding_resolutions": [],
    }


class IdentityFeedbackDetailTests(unittest.TestCase):
    def test_group_id_mismatch_names_expected_and_actual(self) -> None:
        plan = _plan()
        _, error = merge_group_revision_items(
            plan,
            group_id="group-000002",
            patched_items=[
                _patched_item(plan, "item-000003", group_id="group-000001")
            ],
            editable_item_ids=("item-000003", "item-000004"),
        )
        self.assertIn("ITEM_PATCH_IDENTITY:item-000003", error)
        self.assertIn("group_id expected=group-000002 actual=group-000001", error)

    def test_group_echo_on_item_without_owner_is_dropped(self) -> None:
        plan = _plan()
        for item in plan["items"]:
            item.pop("group_id", None)
        patched = _patched_item(plan, "item-000003", group_id="group-000002")
        merged, error = merge_group_revision_items(
            plan,
            group_id="group-000002",
            patched_items=[patched],
            editable_item_ids=("item-000003", "item-000004"),
        )
        self.assertEqual(error, "")
        merged_item = next(i for i in merged["items"] if i["item_id"] == "item-000003")
        self.assertNotIn("group_id", merged_item)

    def test_foreign_group_on_item_without_owner_is_still_rejected(self) -> None:
        plan = _plan()
        for item in plan["items"]:
            item.pop("group_id", None)
        _, error = merge_group_revision_items(
            plan,
            group_id="group-000002",
            patched_items=[_patched_item(plan, "item-000003", group_id="group-000001")],
            editable_item_ids=("item-000003", "item-000004"),
        )
        self.assertIn("group_id expected=group-000002 actual=group-000001", error)

    def test_reply_item_id_mismatch_names_actual(self) -> None:
        _, error = materialize_item_patch(
            {"item_id": "item-000009", "item": {"item_id": "item-000009"}},
            "item-000001",
        )
        self.assertIn("expected item-000001 actual=item-000009", error)

    def test_nested_item_id_mismatch_names_actual(self) -> None:
        _, error = materialize_item_patch(
            {"item_id": "item-000001", "item": {"item_id": "item-000002"}},
            "item-000001",
        )
        self.assertIn("item.item_id must stay item-000001", error)
        self.assertIn("actual=item-000002", error)


class GroupRevisionPartialSalvageTests(unittest.TestCase):
    def _result(self, payloads):
        plan = _plan()
        return _joiner()._join_group_revise(
            tuple(
                WorkerResult(
                    worker_id=str(payload.get("worker_id") or ""),
                    status="SUCCEEDED",
                    payload_ref=None,
                    result_payload=payload,
                )
                for payload in payloads
            ),
            [dict(payload) for payload in payloads],
        )

    def test_valid_patch_survives_a_refused_sibling(self) -> None:
        plan = _plan()
        result = self._result([_good_payload(plan), _bad_payload(plan)])

        self.assertEqual(result.status, "FAILED")
        self.assertEqual(result.failure.error_code, "NODE_ITEM_REVISION_INVALID")
        self.assertIn("group_id expected=group-000002 actual=group-000001", result.failure.message)
        aggregate = result.aggregate
        self.assertEqual(aggregate["action"], "FAIL")
        self.assertEqual(aggregate["salvaged_group_ids"], ["group-000001"])
        salvaged = {
            item["item_id"]: item for item in aggregate["plan"]["items"]
        }
        self.assertEqual(salvaged["item-000001"]["objective"], "objective-fixed")
        self.assertEqual(
            salvaged["item-000003"]["objective"], "objective-item-000003"
        )
        rows = aggregate["salvaged_group_rows"]
        self.assertEqual([row["group_id"] for row in rows], ["group-000001"])
        self.assertEqual(rows[0]["doc_version"], 2)
        self.assertEqual(
            [entry["finding_id"] for entry in aggregate["finding_resolutions"]],
            ["f-good"],
        )

    def test_order_does_not_matter(self) -> None:
        plan = _plan()
        result = self._result([_bad_payload(plan), _good_payload(plan)])

        self.assertEqual(result.status, "FAILED")
        self.assertEqual(result.aggregate["salvaged_group_ids"], ["group-000001"])

    def test_nothing_salvages_when_every_patch_is_refused(self) -> None:
        plan = _plan()
        bad_two = {
            "worker_id": "worker-01",
            "action": "READY_FOR_CRITIC",
            "group_id": "group-000001",
            "plan": {
                "items": [
                    _patched_item(plan, "item-000001", group_id="group-000002")
                ]
            },
            "group_docs": [],
            "finding_resolutions": [],
        }
        result = self._result([bad_two, _bad_payload(plan)])

        self.assertEqual(result.status, "FAILED")
        self.assertEqual(result.aggregate, {"action": "FAIL"})

    def test_wrong_action_is_a_per_group_error_not_a_wave_abort(self) -> None:
        plan = _plan()
        wrong_action = dict(_bad_payload(plan))
        wrong_action["action"] = "BLOCKED"
        wrong_action["worker_id"] = "worker-02"
        result = self._result([_good_payload(plan), wrong_action])

        self.assertEqual(result.status, "FAILED")
        self.assertIn("READY_FOR_CRITIC", result.failure.message)
        self.assertEqual(result.aggregate["salvaged_group_ids"], ["group-000001"])


class SalvageReducerFoldTests(unittest.TestCase):
    """The FAIL payload folds plan+resolutions+doc rows like a success wave."""

    def _context(self) -> WorkflowContext:
        return WorkflowContext(
            identity=TaskIdentity("task-1", "issue-1", "", "request-1"),
            progression=ProgressState("ZHONGSHU_SOLVER", 11, "2026-09-27T00:00:00Z"),
            review=ReviewState(
                revision_id="R1",
                plan_hash="plan-hash",
                task_items=tuple(
                    ReviewTaskItem(
                        item_id=item_id,
                        group_id=group_id,
                        title=f"title-{item_id}",
                        objective=f"objective-{item_id}",
                        source_requirement_ids=("req-000001",),
                        acceptance_signals=(f"accept-{item_id}",),
                    )
                    for group_id, item_ids in (
                        ("group-000001", ("item-000001", "item-000002")),
                        ("group-000002", ("item-000003", "item-000004")),
                    )
                    for item_id in item_ids
                ),
                task_groups=(
                    ReviewTaskGroup("group-000001", ("item-000001", "item-000002")),
                    ReviewTaskGroup("group-000002", ("item-000003", "item-000004")),
                ),
                zhongshu_groups=(
                    ZhongshuGroupState(
                        group_id="group-000001",
                        doc_version=1,
                        doc_markdown=_doc("group-000001", 1, ("item-000001", "item-000002")),
                    ),
                    ZhongshuGroupState(
                        group_id="group-000002",
                        doc_version=1,
                        doc_markdown=_doc("group-000002", 1, ("item-000003", "item-000004")),
                    ),
                ),
                findings=(
                    Finding(
                        finding_id="f-good",
                        severity="P1",
                        status="OPEN",
                        group_id="group-000001",
                        item_id="item-000001",
                    ),
                    Finding(
                        finding_id="f-bad",
                        severity="P1",
                        status="OPEN",
                        group_id="group-000002",
                        item_id="item-000003",
                    ),
                ),
            ),
        )

    def test_fail_payload_folds_the_salvaged_wave(self) -> None:
        plan = _plan()
        result = _joiner()._join_group_revise(
            (
                WorkerResult("worker-01", "SUCCEEDED", None, result_payload=_good_payload(plan)),
                WorkerResult("worker-02", "SUCCEEDED", None, result_payload=_bad_payload(plan)),
            ),
            [_good_payload(plan), _bad_payload(plan)],
        )
        payload = dict(result.aggregate)
        payload["action"] = "FAIL"

        update = ZhongshuCriticState._review_update(self._context(), payload)

        self.assertIsNotNone(update)
        rows = {row.group_id: row for row in update.zhongshu_groups}
        self.assertEqual(rows["group-000001"].doc_version, 2)
        self.assertEqual(rows["group-000002"].doc_version, 1)
        items = {item.item_id: item for item in update.task_items}
        self.assertEqual(items["item-000001"].objective, "objective-fixed")
        self.assertEqual(items["item-000003"].objective, "objective-item-000003")
        findings = {finding.finding_id: finding for finding in update.findings}
        self.assertFalse(findings["f-good"].active)
        self.assertTrue(findings["f-bad"].active)


if __name__ == "__main__":
    unittest.main()
