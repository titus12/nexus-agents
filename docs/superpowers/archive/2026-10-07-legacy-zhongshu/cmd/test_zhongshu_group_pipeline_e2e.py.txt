"""Zhongshu group pipeline end-to-end (2026-09-26 group verdict wave).

Two groups run the whole linear FSM through the real application in group
review mode: one group is approved on the first wave (and its approval is
held - zero dispatch on the second wave), the other is revised once through
the parallel group-revision wave and then approved.  The run must reach DONE
with every member approved and both groups frozen into Menxia.
"""

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
from orchestrator.transport.external import ExternalMessage
from test_group_doc_loop import GroupWaveScripting


_SECTIONS = (
    ("背景", "现状可观察。"),
    ("目标", "只能存在一个入口。"),
    ("标识与范围", "本组处理自身任务。"),
    ("状态与边界语义", "状态封闭枚举。"),
    ("行为要求", "输入返回结构化结果。"),
    ("责任边界", "Solver 撰写，Critic 裁决。"),
    ("交叉不变量", "开关关闭时目标仍成立。"),
    ("验收标准", ""),
    ("非目标", "不做实现。"),
)


def _doc(group_id: str, version: int, acceptance_by_item: dict[str, tuple[str, ...]]) -> str:
    lines = [f"# {group_id} 需求文档 [v{version}]"]
    for number, (name, body) in enumerate(_SECTIONS, start=1):
        lines.append(f"## {number}. {name}")
        if number == 8:
            for item_id, signals in acceptance_by_item.items():
                lines.append(f"### {item_id}")
                lines.extend(signals)
        elif body:
            lines.append(body)
    return "\n".join(lines)


def _plan() -> dict:
    return {
        "plan_id": "plan-1",
        "items": [
            {
                "item_id": "item-000001",
                "group_id": "group-000001",
                "title": "Task A",
                "objective": "do A",
                "dependencies": [],
                "source_requirement_ids": ["req-000001"],
                "acceptance_signals": ["A is observable"],
            },
            {
                "item_id": "item-000002",
                "group_id": "group-000001",
                "title": "Task B",
                "objective": "do B",
                "dependencies": [],
                "source_requirement_ids": ["req-000001"],
                "acceptance_signals": ["B is observable"],
            },
            {
                "item_id": "item-000003",
                "group_id": "group-000002",
                "title": "Task C",
                "objective": "do C",
                "dependencies": [],
                "source_requirement_ids": ["req-000001"],
                "acceptance_signals": ["C is observable"],
            },
        ],
        "groups": [
            {"group_id": "group-000001", "item_ids": ["item-000001", "item-000002"]},
            {"group_id": "group-000002", "item_ids": ["item-000003"]},
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


REVISED_SIGNAL = "C latency budget is observable via elapsed_ms"


class _GroupPipelineMultica(GroupWaveScripting, FakeMulticaAdapter):
    """Group-000001 approves immediately; group-000002 needs one revision."""

    critic_demands_revision = False

    def __init__(self) -> None:
        super().__init__()
        self.group2_revisions = 0
        self.group_review_dispatches: list[str] = []
        self.group_revise_dispatches: list[str] = []

    def dispatch(self, request):
        receipt = super().dispatch(request)
        context = request.context
        if context.get("contract_mode") or str(
            context.get("zhongshu_dispatch_mode") or ""
        ) == "requirement_contract":
            self._reply_requirement_contract(request)
            return receipt
        target = request.target_state
        mode = str(context.get("zhongshu_dispatch_mode") or "")
        if target == "ZHONGSHU_ANALYST":
            self._reply(request, {"action": "READY_FOR_SOLVER"})
        elif target == "ZHONGSHU_SOLVER" and mode == "group_revise":
            self._reply_group_revise(request)
        elif target == "ZHONGSHU_SOLVER":
            self._reply_solver(request)
        elif target == "ZHONGSHU_CRITIC" and mode == "group_review":
            self._reply_group_review(request)
        elif target in (
            "MENXIA_GROUP_SOLVER",
            "MENXIA_GROUP_ANALYST",
            "MENXIA_GROUP_CRITIC",
        ):
            self._reply_group_wave(request)
        else:
            action = {
                "ZHONGSHU_FREEZE_CHECK": "FREEZE_APPROVED",
                "MENXIA_GROUP_GATE": "APPROVE_GROUP",
            }.get(target)
            if action:
                self._reply(request, {"action": action})
        return receipt

    def _reply(self, request, payload: dict) -> None:
        context = request.context
        body = {
            "task_id": request.task_id,
            "request_id": request.request_id,
            "phase": request.phase,
            "role": request.role,
            "revision_id": str(context.get("revision_id") or ""),
            "plan_hash": str(context.get("plan_hash") or ""),
        }
        body.update(payload)
        self.queue_reply(
            request.request_id,
            ExternalMessage(request.agent_id, body, request.request_id),
        )

    def _reply_requirement_contract(self, request) -> None:
        self._reply(
            request,
            {
                "action": "REQUIREMENT_CONTRACT_READY",
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
        )

    def _reply_solver(self, request) -> None:
        self._reply(
            request,
            {
                "action": "READY_FOR_CRITIC",
                "plan": _plan(),
                "changes": [],
                "group_docs": [
                    {
                        "group_id": "group-000001",
                        "markdown": _doc(
                            "group-000001",
                            1,
                            {
                                "item-000001": ("A is observable",),
                                "item-000002": ("B is observable",),
                            },
                        ),
                    },
                    {
                        "group_id": "group-000002",
                        "markdown": _doc(
                            "group-000002", 1, {"item-000003": ("C is observable",)}
                        ),
                    },
                ],
            },
        )

    def _reply_group_review(self, request) -> None:
        group_id = str(request.context.get("group_id") or "")
        self.group_review_dispatches.append(group_id)
        revise = group_id == "group-000002" and self.group2_revisions == 0
        if revise:
            self.group2_revisions += 1
            findings = [
                {
                    # The live-incident shape: the owner is named only inside
                    # the free-text target, never in a dedicated item_id field.
                    "finding_id": "f-g2",
                    "category": "acceptance",
                    "target": "group-000002/item-000003.acceptance_signals",
                    "claim": "the acceptance signal is not observable",
                    "decision": "STILL_OPEN",
                    "severity": "P1",
                    "evidence_strength": "inference",
                    "required_action": "name an observable acceptance signal",
                }
            ]
        else:
            findings = []
        self._reply(
            request,
            {
                # Live run task-20260927-862584 answered the revision with
                # the legacy action name; the joiner must normalize it.
                "action": "REQUEST_SOLVER_REVISION" if revise else "APPROVE_GROUP",
                "group_id": group_id,
                "reviewed_plan_hash": str(request.context.get("plan_hash") or ""),
                "findings": findings,
                "finding_responses": [],
                "review_checks": {
                    "requirement_coverage": [],
                    "boundary": [],
                    "dependencies": [],
                    "acceptance": [],
                    "risks": [],
                },
            },
        )

    def _reply_group_revise(self, request) -> None:
        group_id = str(request.context.get("group_id") or "")
        self.group_revise_dispatches.append(group_id)
        plan = _plan()
        plan["items"][2]["acceptance_signals"] = [REVISED_SIGNAL]
        # Real solver agents echo the full plan and may omit the group_id:
        # the joiner must accept byte-identical foreign echoes and take the
        # group from the binding record.
        self._reply(
            request,
            {
                "action": "READY_FOR_CRITIC",
                "plan": plan,
                "group_docs": [
                    {
                        "group_id": "group-000002",
                        "markdown": _doc(
                            "group-000002",
                            2,
                            {"item-000003": (REVISED_SIGNAL,)},
                        ),
                    }
                ],
                "finding_resolutions": [
                    {
                        "finding_id": "f-g2",
                        "response": "absorbed",
                        "changed_fields": ["acceptance_signals"],
                        "evidence": ["ev-000003"],
                        "owner_role": "review-solver",
                        "next_action": "none",
                    }
                ],
            },
        )


def _context() -> WorkflowContext:
    return WorkflowContext(
        identity=TaskIdentity("task-group-e2e", "issue-group-e2e", "", "request-group-e2e"),
        progression=ProgressState("REQUEST_INTAKE", 0, "2026-09-26T00:00:00Z"),
        request=RequestState(
            raw_request="run a review", project_type="python", task_type="review"
        ),
        parallel=ParallelState(
            zhongshu=ZhongshuParallelLimits(
                review_unit="group", plan_review_gate=False, mechanical_freeze=False
            ),
            menxia=MenxiaParallelLimits(
                enabled=True, max_concurrent_groups=2, max_concurrent_items=3
            ),
        ),
    )


class GroupPipelineEndToEndTests(unittest.TestCase):
    """The whole linear FSM in group review mode, driven through the app."""

    def _run(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter = _GroupPipelineMultica()
            app = OrchestratorApp(
                _context(),
                root=directory,
                multica=adapter,
                poll_interval=0,
                timeout_seconds=5,
            )
            self.assertTrue(app.run())
            snapshot = app.repository.load("task-group-e2e")
        return snapshot, adapter

    def test_run_reaches_done_with_both_groups_approved(self) -> None:
        snapshot, adapter = self._run()

        self.assertEqual(snapshot.context.progression.state, "DONE")
        ledger = {
            record.item_id: record.status
            for record in snapshot.context.review.task_review_ledger
        }
        self.assertEqual(
            ledger,
            {
                "item-000001": "APPROVED",
                "item-000002": "APPROVED",
                "item-000003": "APPROVED",
            },
        )
        self.assertEqual(adapter.group2_revisions, 1)

    def test_clean_group_approval_is_held_across_waves(self) -> None:
        _, adapter = self._run()

        # Wave 1 reviews both groups; wave 2 re-reviews only the revised one.
        flat = adapter.group_review_dispatches
        self.assertIn("group-000001", flat)
        self.assertGreaterEqual(flat.count("group-000002"), 2)
        self.assertEqual(flat.count("group-000001"), 1)

    def test_revision_wave_is_group_scoped(self) -> None:
        _, adapter = self._run()

        self.assertEqual(set(adapter.group_revise_dispatches), {"group-000002"})

    def test_revised_signal_lands_through_projection(self) -> None:
        snapshot, _ = self._run()

        item = next(
            entry
            for entry in snapshot.context.review.task_items
            if entry.item_id == "item-000003"
        )
        self.assertEqual(tuple(item.acceptance_signals), (REVISED_SIGNAL,))


if __name__ == "__main__":
    unittest.main()