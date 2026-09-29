"""Fix 4: a worker-failure wave carries its finished groups across.

Live run task-20260928-835a07: one group's solver worker timed out and the
whole group-revision wave failed; the sibling group whose patch had already
landed was discarded and re-paid in full on every retry.  The fold-phase
salvage only covered refused patches; the transport-level failure branch now
runs the same per-group fold + post-merge pipeline over the surviving
payloads, so the FAIL aggregate carries the finished groups and the retry
re-dispatches only the groups still missing.
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
from orchestrator.domain.states import ZhongshuSolverState
from orchestrator.runtime.agent_effects import AgentNodeJoiner
from orchestrator.runtime.nodes import WorkerResult
from test_group_revise_partial_salvage import (
    _doc,
    _good_payload,
    _patched_item,
    _plan,
    _previous_review,
)


def _invalid_survivor_payload(plan: dict) -> dict:
    """A SUCCEEDED worker whose patch the joiner must refuse (identity)."""

    return {
        "worker_id": "worker-01",
        "action": "READY_FOR_CRITIC",
        "group_id": "group-000001",
        "plan": {
            "items": [
                _patched_item(plan, "item-000001", group_id="group-000002")
            ]
        },
        "group_docs": [
            {
                "group_id": "group-000001",
                "markdown": _doc("group-000001", 2, ("item-000001", "item-000002")),
            }
        ],
        "finding_resolutions": [],
    }


def _joiner() -> AgentNodeJoiner:
    return AgentNodeJoiner(
        "node:task-1:ZHONGSHU_SOLVER:12",
        task_id="task-1",
        state="ZHONGSHU_SOLVER",
        sequence=12,
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


class WorkerFailGroupSalvageTests(unittest.TestCase):
    """The failed branch salvages the wave's finished groups."""

    def test_failed_sibling_salvages_the_finished_group(self) -> None:
        plan = _plan()
        result = _joiner().join(
            (
                WorkerResult(
                    "worker-01",
                    "SUCCEEDED",
                    None,
                    result_payload=_good_payload(plan),
                ),
                # The incident shape: the sibling times out at the transport.
                WorkerResult("worker-02", "FAILED", None),
            )
        )

        self.assertEqual(result.status, "FAILED")
        self.assertEqual(result.failure.error_code, "NODE_WORKER_FAILED")
        aggregate = result.aggregate
        self.assertEqual(aggregate["action"], "FAIL")
        self.assertEqual(aggregate["salvaged_group_ids"], ["group-000001"])
        items = {item["item_id"]: item for item in aggregate["plan"]["items"]}
        self.assertEqual(items["item-000001"]["objective"], "objective-fixed")
        rows = aggregate["salvaged_group_rows"]
        self.assertEqual([row["group_id"] for row in rows], ["group-000001"])
        self.assertEqual(rows[0]["doc_version"], 2)
        self.assertEqual(
            [entry["finding_id"] for entry in aggregate["finding_resolutions"]],
            ["f-good"],
        )

    def test_invalid_survivor_payload_is_not_salvaged(self) -> None:
        plan = _plan()
        result = _joiner().join(
            (
                WorkerResult(
                    "worker-01",
                    "SUCCEEDED",
                    None,
                    result_payload=_invalid_survivor_payload(plan),
                ),
                WorkerResult("worker-02", "FAILED", None),
            )
        )

        self.assertEqual(result.status, "FAILED")
        self.assertEqual(result.aggregate, {"action": "FAIL"})

    def test_wave_without_survivors_fails_whole(self) -> None:
        result = _joiner().join(
            (
                WorkerResult("worker-01", "FAILED", None),
                WorkerResult("worker-02", "FAILED", None),
            )
        )

        self.assertEqual(result.status, "FAILED")
        self.assertEqual(result.aggregate, {"action": "FAIL"})

    def test_synthetic_action_survivor_is_not_salvaged(self) -> None:
        result = _joiner().join(
            (
                WorkerResult(
                    "worker-01",
                    "SUCCEEDED",
                    None,
                    result_payload={"action": "AGENT_RESULT_MISSING"},
                ),
                WorkerResult("worker-02", "FAILED", None),
            )
        )

        self.assertEqual(result.status, "FAILED")
        self.assertEqual(result.aggregate, {"action": "FAIL"})


class WorkerFailSalvageFoldTests(unittest.TestCase):
    """The Fix 4 FAIL payload folds exactly like the fold-phase salvage."""

    def _context(self) -> WorkflowContext:
        return WorkflowContext(
            identity=TaskIdentity("task-1", "issue-1", "", "request-1"),
            progression=ProgressState(
                "ZHONGSHU_SOLVER", 12, "2026-09-28T00:00:00Z"
            ),
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
                    ReviewTaskGroup(
                        "group-000001", ("item-000001", "item-000002")
                    ),
                    ReviewTaskGroup(
                        "group-000002", ("item-000003", "item-000004")
                    ),
                ),
                zhongshu_groups=(
                    ZhongshuGroupState(
                        group_id="group-000001",
                        doc_version=1,
                        doc_markdown=_doc(
                            "group-000001", 1, ("item-000001", "item-000002")
                        ),
                    ),
                    ZhongshuGroupState(
                        group_id="group-000002",
                        doc_version=1,
                        doc_markdown=_doc(
                            "group-000002", 1, ("item-000003", "item-000004")
                        ),
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
                        finding_id="f-timeout",
                        severity="P1",
                        status="OPEN",
                        group_id="group-000002",
                        item_id="item-000003",
                    ),
                ),
            ),
        )

    def test_fail_payload_folds_and_the_retry_skips_the_salvaged_group(
        self,
    ) -> None:
        plan = _plan()
        result = _joiner().join(
            (
                WorkerResult(
                    "worker-01",
                    "SUCCEEDED",
                    None,
                    result_payload=_good_payload(plan),
                ),
                WorkerResult("worker-02", "FAILED", None),
            )
        )
        payload = dict(result.aggregate)
        payload["action"] = "FAIL"

        update = ZhongshuSolverState._review_update(self._context(), payload)

        self.assertIsNotNone(update)
        # The salvaged group's findings close: the retry's findings-driven
        # binding builder therefore re-dispatches only the timed-out group.
        findings = {finding.finding_id: finding for finding in update.findings}
        self.assertFalse(findings["f-good"].active)
        self.assertTrue(findings["f-timeout"].active)
        active_groups = {
            finding.group_id
            for finding in update.findings
            if finding.active
        }
        self.assertEqual(active_groups, {"group-000002"})
        rows = {row.group_id: row for row in update.zhongshu_groups}
        self.assertEqual(rows["group-000001"].doc_version, 2)
        self.assertEqual(rows["group-000002"].doc_version, 1)
        items = {item.item_id: item for item in update.task_items}
        self.assertEqual(items["item-000001"].objective, "objective-fixed")


if __name__ == "__main__":
    unittest.main()
