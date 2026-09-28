from __future__ import annotations

import tempfile
import unittest

from orchestrator.adapters import FakeMulticaAdapter
from orchestrator.app import OrchestratorApp
from orchestrator.domain.context import (
    MenxiaParallelLimits,
    ParallelState,
    ZhongshuParallelLimits,
    ProgressState,
    RequestState,
    TaskIdentity,
    WorkflowContext,
)
from orchestrator.transport.external import ExternalMessage
from test_group_doc_loop import GroupWaveScripting


def _entry_plan() -> dict:
    def item(item_id: str, group_id: str, title: str) -> dict:
        return {
            "item_id": item_id,
            "group_id": group_id,
            "title": title,
            "objective": f"do {title}",
            "dependencies": [],
            "source_requirement_ids": ["req-000001"],
            "acceptance_signals": [f"{title} is observable"],
        }

    return {
        "plan_id": "plan-entry",
        "items": [
            item("item-000001", "group-000001", "Task A"),
            item("item-000002", "group-000001", "Task B"),
        ],
        "groups": [
            {"group_id": "group-000001", "item_ids": ["item-000001", "item-000002"]},
        ],
        "requirements": [
            {
                "requirement_id": "req-000001",
                "statement": "run a review",
                "priority": "must",
                "scope": "in",
                "kind": "task",
            }
        ],
    }


def _entry_group_doc() -> str:
    sections = (
        "背景", "目标", "标识与范围", "状态与边界语义", "行为要求",
        "责任边界", "交叉不变量", "验收标准", "非目标",
    )
    lines = ["# group-000001 需求文档 [v1]"]
    for number, name in enumerate(sections, start=1):
        lines.append(f"## {number}. {name}")
        if name == "背景":
            lines.append("group-000001 的上下文。")
        # §8 must close with the plan's acceptance_signals verbatim; the
        # freeze check re-verifies the closure against the folded items.
        elif name == "验收标准":
            lines.extend(("Task A is observable", "Task B is observable"))
    return "\n".join(lines)


class _ScriptedMultica(GroupWaveScripting, FakeMulticaAdapter):
    critic_demands_revision = False

    def dispatch(self, request):
        receipt = super().dispatch(request)
        if (
            request.context.get("contract_mode")
            or request.context.get("zhongshu_dispatch_mode") == "requirement_contract"
        ):
            self.queue_reply(
                request.request_id,
                ExternalMessage(
                    request.agent_id,
                    {
                        "action": "REQUIREMENT_CONTRACT_READY",
                        "task_id": request.task_id,
                        "request_id": request.request_id,
                        "phase": request.phase,
                        "role": request.role,
                        "requirements": [
                            {
                                "requirement_id": "req-000001",
                                "statement": "run a review",
                                "priority": "must",
                                "scope": "in",
                                "kind": "task",
                            }
                        ],
                    },
                    request.request_id,
                ),
            )
            return receipt
        target = request.target_state
        if target in (
            "MENXIA_GROUP_SOLVER",
            "MENXIA_GROUP_ANALYST",
            "MENXIA_GROUP_CRITIC",
        ):
            self._reply_group_wave(request)
            return receipt
        if target == "ZHONGSHU_CRITIC":
            context = request.context
            self._reply(
                request,
                {
                    "action": "APPROVE_GROUP",
                    "group_id": str(context.get("group_id") or ""),
                    "reviewed_plan_hash": str(context.get("plan_hash") or ""),
                    "findings": [],
                    "finding_responses": [],
                    "review_checks": {},
                },
            )
            return receipt
        actions = {
            "ZHONGSHU_ANALYST": "READY_FOR_SOLVER",
            "ZHONGSHU_SOLVER": "READY_FOR_CRITIC",
            "ZHONGSHU_FREEZE_CHECK": "FREEZE_APPROVED",
            "MENXIA_GROUP_GATE": "APPROVE_GROUP",
        }
        action = actions.get(target)
        if action:
            body: dict[str, object] = {"action": action}
            if target == "ZHONGSHU_SOLVER":
                # The solver establishes the plan (the menxia group pipeline
                # seeds its documents from it) and the plan hash the whole
                # review chain (critic fan-in included) must echo.
                body["plan_hash"] = "entry-plan"
                body["plan"] = _entry_plan()
                body["changes"] = []
                body["group_docs"] = [
                    {"group_id": "group-000001", "markdown": _entry_group_doc()}
                ]
            self._reply(request, body)
        return receipt


def _context() -> WorkflowContext:
    return WorkflowContext(
        identity=TaskIdentity("task-entry", "issue-entry", "", "request-entry"),
        progression=ProgressState("REQUEST_INTAKE", 0, "2026-09-10T00:00:00Z"),
        request=RequestState(raw_request="run a review", project_type="go", task_type="review"),
        parallel=ParallelState(
            zhongshu=ZhongshuParallelLimits(plan_review_gate=False),
            menxia=MenxiaParallelLimits(
                enabled=True,
                max_concurrent_groups=2,
                max_concurrent_items=3,
            )
        ),
    )


class LinearEntrypointTests(unittest.TestCase):
    def test_application_runs_only_through_new_engine(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter = _ScriptedMultica()
            app = OrchestratorApp(
                _context(),
                root=directory,
                multica=adapter,
                poll_interval=0,
                timeout_seconds=2,
            )

            self.assertTrue(app.run())
            snapshot = app.repository.load("task-entry")

        self.assertEqual(snapshot.context.progression.state, "DONE")
        self.assertEqual(snapshot.context.progression.sequence, 10)
        # Analyst contract + lens wave, solver, the group Critic wave (one
        # review job for the single group), freeze-check, the menxia group
        # waves (solver/analyst/critic for the single group) and the group
        # gate.
        self.assertEqual(len(adapter.dispatched), 11)
        self.assertTrue(all(request.request_id for request in adapter.dispatched))


if __name__ == "__main__":
    unittest.main()
