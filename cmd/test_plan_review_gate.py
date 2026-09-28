"""Thin plan-review document + human pre-review gate (2026-09-27).

The Zhongshu deliverable for its human reviewer is one plan-review document;
freeze approval pauses at a human gate so the operator can sign off before the
Menxia hand-over.  Gate off (or fast track) keeps the direct hand-over.
"""

from __future__ import annotations

import unittest

from orchestrator.app import _is_plan_review_rejection
from orchestrator.domain.context import (
    MenxiaParallelLimits,
    ParallelState,
    ProgressState,
    RequestState,
    ReviewState,
    ReviewTaskGroup,
    ReviewTaskItem,
    TaskIdentity,
    WorkflowContext,
    ZhongshuGroupState,
    ZhongshuParallelLimits,
)
from orchestrator.domain.events import DomainEvent
from orchestrator.domain.states import (
    StateRegistry,
)
from orchestrator.domain.transitions import TransitionRegistry
from orchestrator.zhongshu_plan_doc import render_plan_review_doc

TASK = "task-1"


def _doc(group_id: str, version: int = 1) -> str:
    sections = (
        ("背景", "用户要求审查 X 的优化空间。"),
        ("目标", "产出可核验的审查报告。"),
        ("标识与范围", "范围为 a.py、b.py。"),
        ("状态与边界语义", "口径：字节基线为派发产物字节。"),
        ("行为要求", "逐项列出建议。"),
        ("责任边界", "Solver 拆解，Analyst 执行。"),
        ("交叉不变量", "每条验收信号归属存在的 item。"),
        ("验收标准", ""),
        ("非目标", "不实施任何代码改动。"),
    )
    lines = [f"# {group_id} 需求文档 [v{version}]"]
    for index, (name, body) in enumerate(sections, start=1):
        lines.append(f"## {index}. {name}")
        if body:
            lines.append(body)
        if name == "验收标准":
            lines.append("### item-000001")
            lines.append("报告给出至少 3 条建议，每条标注代码位置。")
    return "\n".join(lines)


def _review() -> ReviewState:
    return ReviewState(
        revision_id="R1",
        plan_hash="plan-hash",
        plan={
            "items": [{"item_id": "item-000001"}],
            "groups": [{"group_id": "group-000001", "item_ids": ["item-000001"]}],
            "requirements": [],
            "unknowns": [{"unknown_id": "unk-001", "question": "FastTrack 为何默认关闭？"}],
            "risks": ["放宽并发可能触发限流"],
        },
        task_items=(
            ReviewTaskItem(
                item_id="item-000001",
                group_id="group-000001",
                title="审查 prompt 效率",
                objective="给出裁剪建议",
                source_requirement_ids=("req-001",),
                acceptance_signals=("报告给出至少 3 条建议，每条标注代码位置。",),
            ),
        ),
        task_groups=(ReviewTaskGroup("group-000001", ("item-000001",)),),
        zhongshu_groups=(
            ZhongshuGroupState(
                group_id="group-000001",
                stage="CONVERGED",
                doc_version=1,
                doc_markdown=_doc("group-000001"),
            ),
        ),
    )


def _context(
    *,
    gate: bool = True,
    fast_track: bool = False,
    state: str = "ZHONGSHU_FREEZE_CHECK",
) -> WorkflowContext:
    return WorkflowContext(
        identity=TaskIdentity(TASK, "issue-1", "proj", "request-1"),
        progression=ProgressState(state, 3, "2026-09-27T00:00:00Z"),
        request=RequestState(raw_request="审查 orchestrator 优化空间"),
        review=_review(),
        parallel=ParallelState(
            zhongshu=ZhongshuParallelLimits(
                plan_review_gate=gate, fast_track=fast_track
            ),
            menxia=MenxiaParallelLimits(enabled=True),
        ),
    )


def _event(payload: dict, name: str = "NODE_COMPLETED") -> DomainEvent:
    return DomainEvent(
        name=name,
        task_id=TASK,
        sequence=3,
        payload=payload,
        occurred_at="2026-09-27T00:00:01Z",
        event_id="evt-1",
    )


def _freeze_event() -> DomainEvent:
    return _event({"aggregate": {"action": "FREEZE_APPROVED"}})


class PlanReviewDocTests(unittest.TestCase):
    def test_skeleton_and_checkboxes(self) -> None:
        text = render_plan_review_doc(_context())

        for heading in (
            "# 中书省方案（审阅稿）",
            "## 1. 背景",
            "## 2. 目标",
            "## 3. 边界",
            "## 4. 约束（含口径定义）",
            "## 5. 任务拆解",
            "## 6. 验收（审阅时逐条打勾）",
            "## 7. 开放问题与待决事项",
            "## 8. 风险与关键取舍",
            "## 9. 交接清单",
        ):
            self.assertIn(heading, text)
        self.assertIn("- [ ] 报告给出至少 3 条建议", text)
        self.assertIn("FastTrack 为何默认关闭", text)
        self.assertIn("放宽并发可能触发限流", text)
        self.assertIn("预审批注区", text)

    def test_no_revision_history_and_bounded(self) -> None:
        text = render_plan_review_doc(_context())

        self.assertNotIn("v2 修订", text)
        self.assertLess(len(text), 15000)


class PlanReviewGateTests(unittest.TestCase):
    def test_freeze_approval_pauses_at_the_gate(self) -> None:
        registry = StateRegistry.default()
        decision = registry.get("ZHONGSHU_FREEZE_CHECK").handle(
            _context(gate=True), _freeze_event()
        )

        self.assertEqual(decision.transition.action, "PLAN_REVIEW")
        self.assertEqual(decision.update.progression.resume_state, "MENXIA_GROUP_SOLVER")
        self.assertEqual(decision.update.human_gate.reason_code, "PLAN_REVIEW")
        self.assertEqual(decision.update.human_gate.resume_state, "MENXIA_GROUP_SOLVER")
        # The Menxia dispatch built for the original target is dropped; the
        # doc sink effect stays.
        self.assertEqual(
            [effect.effect_type for effect in decision.effects],
            ["plan_review_doc"],
        )
        self.assertIn(
            "# 中书省方案（审阅稿）",
            str(decision.effects[0].payload.get("content") or ""),
        )

    def test_gate_off_keeps_direct_handover(self) -> None:
        registry = StateRegistry.default()
        decision = registry.get("ZHONGSHU_FREEZE_CHECK").handle(
            _context(gate=False), _freeze_event()
        )

        self.assertEqual(decision.transition.action, "FREEZE_APPROVED")
        types = [effect.effect_type for effect in decision.effects]
        self.assertIn("node_dispatch", types)
        self.assertIn("plan_review_doc", types)

    def test_fast_track_skips_the_gate(self) -> None:
        registry = StateRegistry.default()
        decision = registry.get("ZHONGSHU_FREEZE_CHECK").handle(
            _context(gate=True, fast_track=True), _freeze_event()
        )

        self.assertEqual(decision.transition.action, "FREEZE_APPROVED")

    def test_rejection_routes_to_the_planner_with_a_finding(self) -> None:
        registry = StateRegistry.default()
        gate = _context(state="HUMAN_GATE")
        decision = registry.get("HUMAN_GATE").handle(
            gate,
            _event({"answer": "拒绝：目标一节要拆细"}, name="PLAN_REVIEW_REJECTED"),
        )

        self.assertEqual(decision.transition.action, "PLAN_REVIEW_REJECTED")
        findings = decision.update.review.findings
        self.assertEqual(len(findings), 1)
        self.assertIn("目标一节要拆细", findings[0].claim)
        self.assertTrue(findings[0].active)

    def test_reject_markers(self) -> None:
        self.assertTrue(_is_plan_review_rejection("拒绝：改一下"))
        self.assertTrue(_is_plan_review_rejection("REJECT"))
        self.assertFalse(_is_plan_review_rejection("批准"))
        self.assertFalse(_is_plan_review_rejection(""))


class PlanReviewTransitionTests(unittest.TestCase):
    def test_registry_knows_the_gate_edges(self) -> None:
        registry = TransitionRegistry.default()

        self.assertEqual(
            registry.target_for("ZHONGSHU_FREEZE_CHECK", "PLAN_REVIEW"), "HUMAN_GATE"
        )
        self.assertEqual(
            registry.target_for("HUMAN_GATE", "PLAN_REVIEW_REJECTED"), "ZHONGSHU_SOLVER"
        )


if __name__ == "__main__":
    unittest.main()
