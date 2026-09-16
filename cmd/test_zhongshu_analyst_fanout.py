from __future__ import annotations

from dataclasses import replace
import tempfile
from pathlib import Path
import types
import unittest
from unittest import mock

from orchestrator.adapters import MulticaCliAdapter, _extract_issue_id, _iter_child_issues
from orchestrator.app import OrchestratorApp
from orchestrator.runtime.agent_effects import AgentWorkerRunner
from orchestrator.runtime.nodes import (
    NodeContext,
    NodeResult,
    ReviewNode,
    WorkerBinding,
    WorkerResult,
)


class _FakeCli(MulticaCliAdapter):
    def __init__(self) -> None:
        self.log_dir = Path(tempfile.mkdtemp(prefix="fanout-test-"))
        self.children: list[tuple[str, str]] = []
        self.calls: list[tuple] = []

    def _run_with_read_retry(self, *args: str):
        self.calls.append(args)
        if args[:2] == ("issue", "children"):
            return {
                "stages": [],
                "total": len(self.children),
                "unstaged": [
                    {"id": child_id, "title": title}
                    for child_id, title in self.children
                ],
            }
        if args[:2] == ("issue", "get"):
            return {"id": args[2], "project_id": "proj-1"}
        raise AssertionError(f"unexpected read: {args}")

    def _run(self, *args: str):
        self.calls.append(args)
        if args[:2] == ("issue", "create"):
            title = args[args.index("--title") + 1]
            child_id = f"child-{len(self.children) + 1:02d}"
            self.children.append((child_id, title))
            return {"id": child_id, "title": title}
        if args[:2] == ("issue", "status"):
            return {"id": args[2], "status": args[3]}
        raise AssertionError(f"unexpected write: {args}")


class MulticaChildIssueTests(unittest.TestCase):
    def test_child_issue_is_created_once_and_reused(self) -> None:
        cli = _FakeCli()
        first = cli.ensure_child_issue("parent-1", "agent-1", "[fanout] task-1")
        second = cli.ensure_child_issue("parent-1", "agent-1", "[fanout] task-1")
        self.assertEqual(first, second)
        self.assertEqual(len(cli.children), 1)

    def test_child_issue_inherits_parent_project(self) -> None:
        cli = _FakeCli()
        cli.ensure_child_issue("parent-1", "agent-1", "[fanout] task-1")
        create = next(args for args in cli.calls if args[:2] == ("issue", "create"))
        self.assertIn("--project", create)
        self.assertEqual(create[create.index("--project") + 1], "proj-1")

    def test_close_issue_sets_status(self) -> None:
        cli = _FakeCli()
        self.assertTrue(cli.close_issue("child-01", "done"))
        self.assertIn(("issue", "status", "child-01", "done", "--output", "json"), cli.calls)

    def test_extract_and_iter_helpers(self) -> None:
        self.assertEqual(_extract_issue_id({"id": "child-9"}), "child-9")
        self.assertEqual(_extract_issue_id({"issue": {"id": "child-8"}}), "child-8")
        self.assertEqual(_extract_issue_id("nope"), "")
        found = _iter_child_issues(
            {"stages": [{"issues": [{"id": "c1", "title": "t1"}]}],
             "unstaged": [{"id": "c2", "title": "t2"}]}
        )
        self.assertEqual(sorted(item["id"] for item in found), ["c1", "c2"])


class _RebindAdapter:
    def dispatch(self, request):
        from orchestrator.transport.external import DispatchReceipt

        return DispatchReceipt(
            operation_id="child-01",
            external_message_id="",
            confirmed=True,
            request_id=request.request_id,
            issue_id="child-01",
        )

    def find_existing_request(self, idempotency_key, issue_id=""):
        return None

    def poll(self, request):
        return []

    def get_run_status(self, request):
        return "running"


class TransportRebindTests(unittest.TestCase):
    def test_fanout_receipt_rebinds_request_to_child_issue(self) -> None:
        from orchestrator.runtime.compat_effects import MulticaTransportAdapter
        from orchestrator.runtime.ports import AgentDispatchRequest

        adapter = MulticaTransportAdapter(_RebindAdapter())
        request = AgentDispatchRequest(
            task_id="task-1",
            issue_id="parent-1",
            request_id="task-1:ZHONGSHU_ANALYST:1:worker-01",
            agent_id="analyst-1",
            role="review-analyst",
            phase="ZHONGSHU",
            prompt_ref="prompt",
            idempotency_key="key-1",
            target_state="ZHONGSHU_ANALYST",
            context={"fanout_parent_id": "parent-1", "fanout_title": "Analyst 维度 01"},
        )
        receipt = adapter.dispatch(request)
        self.assertEqual(receipt.issue_id, "child-01")
        stored = adapter._requests[request.request_id]
        self.assertEqual(stored.issue_id, "child-01")
        self.assertEqual(stored.dispatch_external_message_id, "")


def _binding(index: int) -> WorkerBinding:
    return WorkerBinding(
        worker_id=f"zhongshu_analyst-worker-{index:02d}",
        agent_id=f"zhongshu-analyst-{index:02d}",
        task_id="task-1",
        request_id=f"task-1:ZHONGSHU_ANALYST:1:worker-{index:02d}",
        role="review-analyst",
        phase="ZHONGSHU",
    )


class _FakeTransport:
    def __init__(self) -> None:
        self.created: list[str] = []
        self.closed: list[str] = []

    def ensure_child_issue(self, parent: str, agent_id: str, title: str, marker: str = "") -> str:
        child = f"child-{len(self.created) + 1:02d}"
        self.created.append(child)
        return child

    def close_issue(self, issue_id: str, status: str = "done") -> bool:
        self.closed.append(issue_id)
        return True


class FanoutPrepareTests(unittest.TestCase):
    def _app(self, transport) -> OrchestratorApp:
        runner = AgentWorkerRunner(object(), agent_ids={"ZHONGSHU_ANALYST": "analyst-1"})
        return types.SimpleNamespace(transport=transport, runner=runner)

    def test_prepare_marks_each_worker_as_fanout(self) -> None:
        transport = _FakeTransport()
        app = self._app(transport)
        node = ReviewNode(
            "node:1", "ZHONGSHU_ANALYST", tuple(_binding(i) for i in (1, 2, 3))
        )
        context = NodeContext(
            task_id="task-1",
            node_run_id="node:1",
            revision_id="rev-1",
            state="ZHONGSHU_ANALYST",
            issue_id="parent-1",
        )
        prepared = OrchestratorApp._prepare_fanout_node(app, node, context)
        self.assertEqual(
            [b.fanout_parent_id for b in prepared.bindings],
            ["parent-1", "parent-1", "parent-1"],
        )
        self.assertEqual(
            [b.fanout_title for b in prepared.bindings],
            [
                "Analyst 维度 01 · task-1 · seq0",
                "Analyst 维度 02 · task-1 · seq0",
                "Analyst 维度 03 · task-1 · seq0",
            ],
        )
        self.assertEqual(
            [b.issue_id for b in prepared.bindings], ["", "", ""]
        )
        lenses = [b.dispatch_context.get("worker_lens") for b in prepared.bindings]
        self.assertEqual(len(set(lenses)), 3)
        self.assertTrue(all(lenses))
        self.assertEqual(
            [b.dispatch_context.get("worker_index") for b in prepared.bindings],
            [1, 2, 3],
        )

    def test_prepare_critic_keeps_review_context_and_no_lens(self) -> None:
        transport = _FakeTransport()
        app = self._app(transport)
        bindings = tuple(
            replace(
                _binding(i),
                worker_id=f"zhongshu_critic-worker-{i:02d}",
                item_id=f"item-{i:02d}",
                dispatch_context={
                    "zhongshu_dispatch_mode": "task_review",
                    "review_job_id": f"job-{i:02d}",
                    "item_id": f"item-{i:02d}",
                },
            )
            for i in (1, 2)
        )
        node = ReviewNode("node:1", "ZHONGSHU_CRITIC", bindings)
        context = NodeContext(
            task_id="task-1",
            node_run_id="node:1",
            revision_id="rev-1",
            state="ZHONGSHU_CRITIC",
            issue_id="parent-1",
            dispatch_mode="task_review",
        )
        prepared = OrchestratorApp._prepare_fanout_node(app, node, context)
        self.assertEqual(
            [b.fanout_title for b in prepared.bindings],
            [
                "Critic 任务 item-01 · task-1 · seq0",
                "Critic 任务 item-02 · task-1 · seq0",
            ],
        )
        for binding in prepared.bindings:
            self.assertNotIn("worker_lens", binding.dispatch_context)
            self.assertEqual(
                binding.dispatch_context.get("review_job_id"),
                f"job-{binding.item_id[-2:]}",
            )

    def test_lenses_can_be_overridden_and_cycle(self) -> None:
        transport = _FakeTransport()
        app = self._app(transport)
        node = ReviewNode(
            "node:1", "ZHONGSHU_ANALYST", tuple(_binding(i) for i in (1, 2, 3))
        )
        context = NodeContext(
            task_id="task-1",
            node_run_id="node:1",
            revision_id="rev-1",
            state="ZHONGSHU_ANALYST",
            issue_id="parent-1",
        )
        with mock.patch.dict(
            "os.environ", {"ANALYST_LENSES": "L1,L2"}, clear=False
        ):
            prepared = OrchestratorApp._prepare_fanout_node(app, node, context)
        self.assertEqual(
            [b.dispatch_context.get("worker_lens") for b in prepared.bindings],
            ["L1", "L2", "L1"],
        )

    def test_finalize_closes_every_child(self) -> None:
        transport = _FakeTransport()
        app = self._app(transport)
        result = NodeResult(
            "node:1",
            "SUCCEEDED",
            (
                WorkerResult("w1", "SUCCEEDED", None, external_issue_id="child-01"),
                WorkerResult("w2", "SUCCEEDED", None, external_issue_id="child-02"),
            ),
        )
        OrchestratorApp._finalize_fanout_children(app, result)
        self.assertEqual(transport.closed, ["child-01", "child-02"])

    def test_prepare_without_context_issue_is_noop(self) -> None:
        transport = _FakeTransport()
        app = self._app(transport)
        node = ReviewNode(
            "node:1", "ZHONGSHU_ANALYST", tuple(_binding(i) for i in (1, 2))
        )
        context = NodeContext(
            task_id="task-1", node_run_id="node:1", revision_id="rev-1",
            state="ZHONGSHU_ANALYST", issue_id="",
        )
        prepared = OrchestratorApp._prepare_fanout_node(app, node, context)
        self.assertIs(prepared, node)


if __name__ == "__main__":
    unittest.main()
