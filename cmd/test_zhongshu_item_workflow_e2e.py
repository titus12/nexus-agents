from __future__ import annotations

import tempfile
import unittest

from orchestrator.adapters import FakeMulticaAdapter
from orchestrator.app import OrchestratorApp
from orchestrator.domain.context import (
    MenxiaParallelLimits,
    ParallelState,
    ProgressState,
    RequestState,
    TaskIdentity,
    WorkflowContext,
    ZhongshuParallelLimits,
)
from test_zhongshu_convergence_e2e import _ConvergingMultica, _plan


def _revised_group_doc(acceptance: tuple[str, ...]) -> str:
    """The group-000001 requirement document v2 with a rewritten §8."""

    sections = (
        "背景", "目标", "标识与范围", "状态与边界语义", "行为要求",
        "责任边界", "交叉不变量", "验收标准", "非目标",
    )
    lines = ["# group-000001 需求文档 [v2]"]
    for number, name in enumerate(sections, start=1):
        lines.append(f"## {number}. {name}")
        if name == "背景":
            lines.append("group-000001 的上下文。")
        elif name == "验收标准":
            lines.extend(acceptance)
    return "\n".join(lines)


def _context() -> WorkflowContext:
    return WorkflowContext(
        identity=TaskIdentity("task-e2e-item", "issue-e2e", "", "request-e2e"),
        progression=ProgressState("REQUEST_INTAKE", 0, "2026-09-17T00:00:00Z"),
        request=RequestState(
            raw_request="run a review", project_type="python", task_type="review"
        ),
        parallel=ParallelState(
            zhongshu=ZhongshuParallelLimits(item_workflow_enabled=True, plan_review_gate=False),
            menxia=MenxiaParallelLimits(
                enabled=True,
                max_concurrent_groups=2,
                max_concurrent_items=3,
            ),
        ),
    )


class _ItemWorkflowMultica(_ConvergingMultica):
    """Same scenario as the convergence e2e, but revisions run per item."""

    def dispatch(self, request):
        receipt = super().dispatch(request)
        if (
            request.target_state == "ZHONGSHU_SOLVER"
            and str(request.context.get("zhongshu_dispatch_mode") or "") == "item_revise"
        ):
            self._reply_item_revise(request)
        return receipt

    def _reply_item_revise(self, request) -> None:
        item_id = str(request.context.get("item_id") or "")
        item = dict(request.context.get("item") or {})
        item["acceptance_signals"] = [
            "revised observable signal; measured in UTF-8 bytes; source states.py"
        ]
        self._reply(
            request,
            {
                "action": "READY_FOR_CRITIC",
                "item_id": item_id,
                "group_id": str(request.context.get("group_id") or ""),
                "item": item,
                "finding_resolutions": [
                    {
                        "finding_id": "finding-item2",
                        "response": "acceptance rewritten observably",
                        "changed_fields": ["acceptance_signals"],
                    }
                ],
                # The rewritten signal changes the group document's §8
                # closure, so the reply re-authors the owning group's
                # requirement document (supersede v1 with v2).
                "group_docs": [
                    {
                        "group_id": "group-000001",
                        "markdown": _revised_group_doc(
                            ("A is observable",
                             "revised observable signal; measured in UTF-8 "
                             "bytes; source states.py"),
                        ),
                    }
                ],
            },
        )


class ItemWorkflowEndToEndTests(unittest.TestCase):
    """A contested item is revised by its own Solver worker and re-reviewed.

    The full FSM runs with the item workflow enabled: the Critic rejects one
    task, the revision edge fans out one item-scoped Solver worker instead of
    the single writer, the joiner merges the patch into the orchestrator-owned
    plan, and the re-review approves the rewritten task before the freeze.
    """

    def test_contested_item_is_revised_in_its_own_stove(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            adapter = _ItemWorkflowMultica()
            app = OrchestratorApp(
                _context(),
                root=directory,
                multica=adapter,
                poll_interval=0,
                timeout_seconds=5,
            )

            self.assertTrue(app.run())
            snapshot = app.repository.load("task-e2e-item")
            dispatched = tuple(adapter.dispatched)

        self.assertEqual(snapshot.context.progression.state, "DONE")

        # One item-revise node dispatch for the single contested item.
        item_revisions = [
            request
            for request in dispatched
            if str(request.context.get("zhongshu_dispatch_mode") or "") == "item_revise"
        ]
        self.assertEqual(len(item_revisions), 1)
        self.assertEqual(item_revisions[0].context.get("item_id"), "item-000002")

        # The orchestrator-owned plan carries the patched acceptance signal,
        # and the dispatch table records the item as approved.
        review = snapshot.context.review
        patched = [
            item
            for item in (review.plan or {}).get("items", [])
            if item.get("item_id") == "item-000002"
        ]
        self.assertEqual(
            patched[0]["acceptance_signals"],
            ["revised observable signal; measured in UTF-8 bytes; source states.py"],
        )
        workflows = {row.item_id: row.phase for row in review.item_workflows}
        self.assertEqual(workflows.get("item-000002"), "APPROVED")


if __name__ == "__main__":
    unittest.main()
