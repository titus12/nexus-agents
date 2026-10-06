"""Thin plan-review document + human pre-review gate (2026-09-27).

The Zhongshu deliverable for its human reviewer is one plan-review document;
freeze approval pauses at a human gate so the operator can sign off before the
Menxia hand-over. Only explicit gate off keeps the direct hand-over.
"""

from __future__ import annotations

import unittest
import tempfile
from dataclasses import replace
from unittest.mock import patch

from orchestrator.app import OrchestratorApp, _is_plan_review_rejection, _is_plan_review_approval
from orchestrator.adapters import FakeMulticaAdapter, FeishuHttpAdapter
from orchestrator.domain.context import (
    MenxiaParallelLimits,
    HumanGateState,
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
from orchestrator.notifications import build_notification_parts, DirectNotificationEmitter
from orchestrator.runtime.ports import NotificationReceipt
from orchestrator.transport.external import HumanGate, HumanReply
from test_human_gate_reply_poll import _allow_adapter_poll

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
        self.assertIn("仅回复「通过」", text)

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
            "# 中书省需求文档 [v1]",
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
        self.assertEqual(types[0], "plan_review_doc")

    def test_fast_track_keeps_human_review(self) -> None:
        registry = StateRegistry.default()
        decision = registry.get("ZHONGSHU_FREEZE_CHECK").handle(
            _context(gate=True, fast_track=True), _freeze_event()
        )

        self.assertEqual(decision.transition.action, "PLAN_REVIEW")

    def test_rejection_dispatches_full_human_revision(self) -> None:
        registry = StateRegistry.default()
        gate = _context(state="HUMAN_GATE")
        decision = registry.get("HUMAN_GATE").handle(
            gate,
            _event({"answer": "拒绝：目标一节要拆细"}, name="PLAN_REVIEW_REJECTED"),
        )

        self.assertEqual(decision.transition.action, "PLAN_REVIEW_REJECTED")
        feedback = decision.update.review.plan_review["pending_feedback"]
        self.assertEqual(feedback[0]["answer"], "拒绝：目标一节要拆细")
        self.assertEqual(decision.effects[0].payload["dispatch_context"]["zhongshu_dispatch_mode"], "human_plan_revision")
        self.assertTrue(all(row.stage == "REVIEWING" for row in decision.update.review.zhongshu_groups))

    def test_reject_markers(self) -> None:
        self.assertTrue(_is_plan_review_rejection("拒绝：改一下"))
        self.assertTrue(_is_plan_review_rejection("REJECT"))
        self.assertTrue(_is_plan_review_rejection("批准"))
        self.assertFalse(_is_plan_review_rejection("通过"))
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


class _ReviewPort:
    def __init__(self, delivered=True):
        self.requests = []
        self.delivered = delivered
        self.replies = []

    def send(self, request):
        self.requests.append(request)
        return NotificationReceipt(request.notification_key, self.delivered)

    def poll_reply(self, gate):
        return self.replies


class CompletePlanDeliveryTests(unittest.TestCase):
    def test_latest_readonly_null_setting_pauses_and_delivers_before_menxia(self):
        adapter, port = FakeMulticaAdapter(), _ReviewPort()
        with tempfile.TemporaryDirectory() as directory:
            app = OrchestratorApp(_context(gate=None), root=directory,
                                  multica=adapter, notification_port=port)
            app.engine.dispatch(_freeze_event())
            app.effects.execute_pending(TASK)
            context = app.repository.load(TASK).context
            self.assertEqual(context.progression.state, "HUMAN_GATE")
            self.assertEqual(context.human_gate.reason_code, "PLAN_REVIEW")
            self.assertEqual(adapter.dispatched, [])
            persisted = (app.root / "zhongshu-requirements.md").read_text(encoding="utf-8")
            self.assertIn(_doc("group-000001"), persisted)
            offers = [request for request in port.requests if request.document_markdown]
            self.assertEqual(len(offers), 1)
            self.assertIn("分组需求正文", offers[0].document_markdown)
            self.assertIn("不实施任何代码改动。", offers[0].document_markdown)
            self.assertNotIn("分组需求正文", offers[0].body)
            self.assertFalse(app.resume())
            self.assertTrue(app.resume("通过"))
            event = app.inbox.next(TASK)
            app.engine.dispatch(event)
            self.assertEqual(app.repository.load(TASK).context.progression.state, "MENXIA_GROUP_SOLVER")

    def test_one_notice_preserves_long_unicode_document_in_metadata(self):
        source = _context()
        doc = _doc("group-000001") + "\n原文🙂" * 5000 + "\n完整文档末尾标记"
        review = replace(source.review, zhongshu_groups=(replace(source.review.zhongshu_groups[0], doc_markdown=doc),))
        source = replace(source, review=review)
        decision = StateRegistry.default().get("ZHONGSHU_FREEZE_CHECK").handle(source, _freeze_event())
        context = replace(source, progression=replace(source.progression, state="HUMAN_GATE", sequence=4),
                          human_gate=decision.update.human_gate)
        parts = build_notification_parts("HUMAN_GATE", "HUMAN_GATE", _freeze_event(), context)
        self.assertEqual(len(parts), 1)
        self.assertTrue(all(len(body.encode("utf-8")) <= 8000 for body in parts))
        self.assertTrue(all(context.human_gate.decision_id in body for body in parts))
        self.assertNotIn(doc, parts[0])
        self.assertNotIn("分段", parts[0])
        port = _ReviewPort()
        keys = DirectNotificationEmitter(port).emit(source, _freeze_event(), context)
        offers = [request for request in port.requests if request.document_markdown]
        self.assertEqual(len(offers), 1)
        self.assertIn(doc, offers[0].document_markdown)
        self.assertIn("完整文档末尾标记", offers[0].document_markdown)
        self.assertEqual(len(keys), len(set(keys)))
        audited = replace(context, audit=replace(context.audit, sent_notification_keys=keys))
        self.assertEqual(DirectNotificationEmitter(port).emit(source, _freeze_event(), audited), ())

    def test_failed_delivery_is_not_recorded_as_sent(self):
        source = _context()
        decision = StateRegistry.default().get("ZHONGSHU_FREEZE_CHECK").handle(source, _freeze_event())
        context = replace(source, progression=replace(source.progression, state="HUMAN_GATE", sequence=4),
                          human_gate=decision.update.human_gate)
        self.assertEqual(DirectNotificationEmitter(_ReviewPort(False)).emit(source, _freeze_event(), context), ())

    def test_poll_preserves_feedback_before_later_pass(self):
        source = _context()
        decision = StateRegistry.default().get("ZHONGSHU_FREEZE_CHECK").handle(source, _freeze_event())
        context = replace(source, progression=replace(source.progression, state="HUMAN_GATE", sequence=4),
                          human_gate=HumanGateState(
                              decision_id=decision.update.human_gate.decision_id,
                              reason_code="PLAN_REVIEW", resume_state="MENXIA_GROUP_SOLVER"))
        port = _ReviewPort()
        port.replies = [HumanReply(context.human_gate.decision_id, "通过", "u"),
                        HumanReply(context.human_gate.decision_id, "拒绝：旧批注", "u")]
        with tempfile.TemporaryDirectory() as directory:
            app = OrchestratorApp(context, root=directory, multica=FakeMulticaAdapter(), notification_port=port)
            app._poll_human_gate_reply(TASK)
            event = app.inbox.next(TASK)
            self.assertEqual(event.name, "PLAN_REVIEW_REJECTED")
            self.assertEqual(event.payload["answer"], "拒绝：旧批注")

    def test_only_explicit_approval_signs_off(self):
        for answer in ("通过", " 通过\n"):
            self.assertTrue(_is_plan_review_approval(answer))
        for answer in ("", "批准", "同意", "通过。", "APPROVED", "稍等", "继续", "是否可以批准？", "不同意"):
            self.assertFalse(_is_plan_review_approval(answer))

    def test_reply_to_older_document_part_across_pages_is_recognized(self):
        decision = "task-1:plan-review:4"
        pages = [
            {"code": 0, "data": {"items": [
                {"message_id": "reply", "parent_id": "part1", "body": {"content": "批准"}},
                {"message_id": "part2", "sender": {"sender_type": "app"},
                 "body": {"content": decision + "\n分段：2/2"}},
            ], "has_more": True, "page_token": "older"}},
            {"code": 0, "data": {"items": [
                {"message_id": "part1", "sender": {"sender_type": "app"},
                 "body": {"content": decision + "\n分段：1/2"}},
            ], "has_more": False}},
        ]
        adapter = FeishuHttpAdapter()
        adapter.chat_id = "chat-1"
        with _allow_adapter_poll(), patch.object(adapter, "_token", return_value="fake"), \
                patch.object(adapter, "_request", side_effect=pages) as http:
            replies = adapter.poll_reply(HumanGate(decision, TASK, "MENXIA_GROUP_SOLVER", "", message_id="part2"))
        self.assertEqual(http.call_count, 2)
        self.assertEqual([reply.answer for reply in replies], ["批准"])

    def test_feishu_transport_preserves_approval_looking_punctuation(self):
        adapter = FeishuHttpAdapter()
        adapter.chat_id = "chat-1"
        page = {"code": 0, "data": {"items": [
            {"message_id": "a", "parent_id": "root", "body": {"content": "通过，"}},
            {"message_id": "b", "parent_id": "root", "body": {"content": "通过："}},
            {"message_id": "c", "parent_id": "root", "body": {"content": "gate-1：通过。"}},
        ]}}
        with _allow_adapter_poll(), patch.object(adapter, "_token", return_value="fake"), patch.object(adapter, "_request", return_value=page):
            replies = adapter.poll_reply(HumanGate("gate-1", TASK, "MENXIA_GROUP_SOLVER", "", "root"))
        self.assertEqual([reply.answer for reply in replies], ["通过，", "通过：", "通过。"])
        self.assertTrue(all(not _is_plan_review_approval(reply.answer) for reply in replies))


if __name__ == "__main__":
    unittest.main()
