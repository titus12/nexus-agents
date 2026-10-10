"""A bounded, scripted request-to-plan acceptance run; no model or notifications.

Run: python cmd/smoke_orchestrator_plan_pipeline.py --repeat 3 --output runs/plan-smoke
Replies and the human approval are test fixtures. This checks the actual app,
joiners, document contracts, durable approval gate and recovery, not LLM quality.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path

# Set before importing the application: never inherit real Feishu credentials.
os.environ["NEXUS_TEST_NO_EXTERNAL_NOTIFICATIONS"] = "1"
os.environ["ENABLE_FEISHU_NOTIFICATIONS"] = "0"

from orchestrator.adapters import FakeMulticaAdapter, _apply_quote_gate
from orchestrator.app import OrchestratorApp
from orchestrator.domain.context import (
    MenxiaParallelLimits, ParallelState, ProgressState, RequestState, TaskIdentity,
    WorkflowContext, ZhongshuParallelLimits,
)
from orchestrator.domain.menxia_doc import BODY_PART_TITLES, MenxiaGroupDoc, changed_body_sections
from test_group_doc_loop import GroupWaveScripting, _acceptance_entries


REQUEST = "审查本仓库 orchestrator 的补证与修订路由，交付现状、缺陷证据、最小修复步骤和验收方法；保持现有服务可用，不引入无依据数量要求。"
SIGNALS = ("补证与修订的状态路径有对应代码证据。", "修复步骤包含改动位置、运行命令、通过条件和回退方式。")


def requirements():
    return [{"requirement_id": "req-000001", "statement": "审查补证与修订路由并交付可执行修复方案。",
             "source": "user_request", "source_quote": REQUEST, "priority": "must", "scope": "in",
             "kind": "task", "acceptance_signal": "现状与缺陷有证据，修复步骤可执行且有验收与回退。"}]


def plan():
    return {"plan_id": "plan-route-repair", "requirements": requirements(), "items": [
        {"item_id": "item-000001", "group_id": "group-000001", "title": "核对补证与修订路由",
         "objective": "比对 Critic、Analyst、Solver 的流转条件与机会计数。", "dependencies": [],
         "source_requirement_ids": ["req-000001"], "acceptance_signals": [SIGNALS[0]]},
        {"item_id": "item-000002", "group_id": "group-000001", "title": "形成最小修复与验收方案",
         "objective": "依据核对结果提出局部计数修复，给出安全回归命令与人工确认位置。",
         "dependencies": ["item-000001"], "source_requirement_ids": ["req-000001"],
         "acceptance_signals": [SIGNALS[1]]}],
        "groups": [{"group_id": "group-000001", "item_ids": ["item-000001", "item-000002"]}]}


def requirement_doc():
    sections = {
        "背景": "重复补证可能被计入 Solver 修订失败，造成错误停滞。",
        "目标": "审查路由并给出可执行的最小修复、验证和回退步骤。",
        "标识与范围": "group-000001；item-000001 核对路由，item-000002 形成修复。",
        "状态与边界语义": "补证进入 Analyst；方案改动进入 Solver；人工通过当前需求正文后才进入方案细化。",
        "行为要求": "补证与修订分别计数。回复失败不能伪造修订机会。停滞时列明对应组与缺失证据。",
        "责任边界": "Analyst 提供证据，Solver 修改文档，Critic 复核，人工确认需求。",
        "交叉不变量": "批准绑定当前正文；验证不影响当前服务，不发送外部通知。",
        "验收标准": "\n".join(SIGNALS),
        "非目标": "不宣称模型质量已验证，不重启当前服务，不改变业务需求。",
    }
    return "# group-000001 需求文档 [v1]\n" + "\n".join(
        f"## {index}. {name}\n{body}" for index, (name, body) in enumerate(sections.items(), 1))


def executable_body(entries, *, corrected):
    parts = {
        "目标与设计决策": "把补证、修订和格式修复作为不同工序，修订失败只统计实际处理过的 finding。",
        "现状与目标行为": "当前 Critic 可要求 Analyst 补证或 Solver 修订。目标：补证不增加 Solver stuck_rounds；已消费的机会不能在重试时重复计算。",
        "统一约束": "保留整份需求的人工通过门槛和批准哈希；不停止 8766 服务；不调用真实模型，不发送飞书通知。",
        "任务分解": (
            "- item-000001：读取 cmd/orchestrator/domain/states.py 的 _review_update 与 _fold_group_rows_with_resets；核对 cmd/orchestrator/domain/findings.py 的 age_unresolved_findings。\n"
            "- item-000002：将 Solver finding 批次在有效评审后按对应组消费；为补证设独立组预算；停滞组退出调度，其他组继续各自工序。\n"
            "- 执行 rtk proxy python -X utf8 cmd/run_tests.py test_orchestrator_stabilization test_linear_fsm_entrypoint test_zhongshu_convergence_e2e。\n"
            "- 定向检查通过后执行 rtk proxy python -X utf8 cmd/run_tests.py，并保存输出。"
        ),
        "验收映射": "\n".join(f"- {entry} -> {'检查状态路径与机会消费断言。' if index == 0 else '定向与全量检查退出码为零；审阅修改清单与回退说明。'}"
                                 for index, entry in enumerate(entries)),
        "风险与兼容": "旧快照缺少显式 Solver 批次时，补证不应视为修订；保留现有快照读取兼容。脚本通过只说明机制闭环，真实模型质量另行验证。",
    }
    if corrected:
        parts["风险与兼容"] += "\n回退：保留工作区原有修改，仅撤销本次计数、组隔离与补证预算补丁；保留运行快照及日志，不撤回已有人工批准历史。补证达到预算时，报告组 ID 与缺失证据供人工决定。"
    return "\n\n".join(f"### {title}\n{parts[title]}" for title in BODY_PART_TITLES)


class PlanPipelineScript(GroupWaveScripting, FakeMulticaAdapter):
    """A valid small plan with one malformed draft and one substantive repair."""
    def __init__(self, *, inject_bad_draft=True):
        super().__init__()
        self.inject_bad_draft = inject_bad_draft
        self.bad_draft_sent = False
        self.reviewed_once = False
        self.contract_checked = False

    def dispatch(self, request):
        receipt = super().dispatch(request)
        context, target = request.context, request.target_state
        if context.get("contract_mode") or context.get("zhongshu_dispatch_mode") == "requirement_contract":
            payload = {"action": "REQUIREMENT_CONTRACT_READY", "requirements": requirements()}
            _apply_quote_gate(request, payload)
            self.contract_checked = True
        elif target == "ZHONGSHU_ANALYST":
            source = Path(__file__).parent / "orchestrator/domain/states.py"
            lines = source.read_text(encoding="utf-8").splitlines()
            number = next(index for index, line in enumerate(lines, 1) if "def _review_update(" in line)
            payload = {"action": "EVIDENCE_PACKET_READY", "requirements": requirements(),
                       "task_proposals": [], "candidate_items": [], "candidate_groups": [],
                       "evidence_updates": [{"evidence_id": "route-source", "requirement_id": "req-000001",
                           "source": source.as_posix(), "conclusion": "机会计数在状态评审更新处，需分离补证与修订。",
                           "decision_relevance": "coverage", "quote": {"path": source.as_posix(),
                            "line_start": number, "line_end": number, "text": lines[number - 1]}}],
                       "evidence_requests": [], "finding_responses": []}
            payload = _apply_quote_gate(request, payload)
        elif target == "ZHONGSHU_SOLVER":
            payload = {"action": "READY_FOR_CRITIC", "plan": plan(), "changes": [],
                       "group_docs": [{"group_id": "group-000001", "markdown": requirement_doc()}]}
        elif target == "ZHONGSHU_CRITIC":
            payload = {"action": "APPROVE_GROUP", "group_id": "group-000001", "findings": [],
                       "reviewed_plan_hash": context.get("plan_hash", ""), "finding_responses": [], "review_checks": {}}
        elif target == "ZHONGSHU_FREEZE_CHECK":
            payload = {"action": "FREEZE_APPROVED"}
        elif target in {"MENXIA_GROUP_SOLVER", "MENXIA_GROUP_ANALYST", "MENXIA_GROUP_CRITIC"}:
            self._reply_group_wave(request)
            return receipt
        elif target == "MENXIA_GROUP_GATE":
            payload = {"action": "APPROVE_GROUP"}
        else:
            raise AssertionError("Unexpected dispatch: " + target)
        self._reply(request, payload)
        return receipt

    def _solver_reply(self, request, group_id, doc):
        previous = doc
        entries = _acceptance_entries(request.context.get("requirement_markdown", ""), request.context.get("items", ()))
        absorbed = [suggestion.suggestion_id for suggestion in doc.suggestions]
        doc = replace(doc, version=doc.version + 1, body_markdown=executable_body(entries, corrected=bool(absorbed)))
        for suggestion_id in absorbed:
            doc = doc.absorb(suggestion_id, note="风险与兼容部分已补充回退步骤与预算用尽处理。")
        if self.inject_bad_draft and not self.bad_draft_sent:
            self.bad_draft_sent = True
            doc = replace(doc, body_markdown="初稿缺少规定章节，预期被合同校验拒绝。")
        return {"action": "READY_FOR_ANALYST" if absorbed else "FEASIBLE", "summary": "路由修复方案",
                "group_id": group_id, "doc_version": doc.version, "doc_markdown": doc.render(),
                "touched_scope": list(changed_body_sections(previous, doc)), "absorbed_ids": absorbed, "rejected_ids": []}

    def _analyst_reply(self, group_id, doc):
        return self._reviewer_payload("EVIDENCE_SUFFICIENT", group_id, doc, [])

    def _critic_reply(self, group_id, doc):
        if not self.reviewed_once:
            self.reviewed_once = True
            doc = doc.add_suggestion(author="critic", severity="P1", body="补充回退步骤与补证预算用尽时的处理。")
            return self._reviewer_payload("REQUEST_SOLVER_REVISION", group_id, doc, [doc.suggestions[-1].suggestion_id])
        return self._reviewer_payload("APPROVE_GROUP", group_id, doc, [])


def drive(app, trace, *, limit=80):
    """Bounded driver: gates return control; missing work fails immediately."""
    task_id = app.context.identity.task_id
    app._ensure_start_event()
    for _ in range(limit):
        event = app.inbox.next(task_id)
        if event:
            app.engine.dispatch(event)
            app.inbox.ack(event)
            snapshot = app.repository.load(task_id).context
            trace.append({"sequence": snapshot.progression.sequence, "state": snapshot.progression.state,
                          "event_action": event.payload.get("action", ""),
                          "reason": snapshot.human_gate.reason_code if snapshot.human_gate else ""})
            app.effects.execute_pending(task_id)
            continue
        if app.repository.pending_effects(task_id):
            app.effects.execute_pending(task_id)
            continue
        snapshot = app.repository.load(task_id).context
        if snapshot.progression.state == "RETRY_WAIT":
            app.resume()
            continue
        if snapshot.progression.state in {"HUMAN_GATE", "DONE", "BLOCKED", "FAILED"}:
            return snapshot
        raise AssertionError("No pending work in " + snapshot.progression.state)
    raise AssertionError("Scripted pipeline exceeded its step budget")


def run_once(output: Path, *, index=1, inject_bad_draft=True):
    output.mkdir(parents=True, exist_ok=True)
    task_id = f"task-plan-smoke-{index}"
    if (output / task_id / "workflow-state.json").exists():
        raise ValueError("Use a fresh output directory for an independent acceptance run")
    context = WorkflowContext(identity=TaskIdentity(task_id, "issue-scripted", "", "request-scripted"),
        progression=ProgressState("REQUEST_INTAKE", 0, "2026-10-06T00:00:00Z"),
        request=RequestState(raw_request=REQUEST, project_type="python", task_type="review"),
        parallel=ParallelState(zhongshu=ZhongshuParallelLimits(plan_review_gate=True, mechanical_freeze=True),
                               menxia=MenxiaParallelLimits(enabled=True)))
    adapter, trace = PlanPipelineScript(inject_bad_draft=inject_bad_draft), []
    app = OrchestratorApp(context, root=output, multica=adapter, poll_interval=0, timeout_seconds=5)
    gate = drive(app, trace)
    if gate.progression.state != "HUMAN_GATE" or gate.human_gate.reason_code != "PLAN_REVIEW":
        raise AssertionError("Expected current-document approval gate: " + str(gate.progression))
    if any(request.target_state.startswith("MENXIA") for request in adapter.dispatched):
        raise AssertionError("Plan detail dispatched before human approval")
    (output / "requirements.md").write_text(gate.review.plan_review["markdown"], encoding="utf-8")
    # Reconstruct the app from its persisted gate; this approval is test data.
    app = OrchestratorApp(gate, root=output, multica=adapter, poll_interval=0, timeout_seconds=5)
    if not app.resume("通过"):
        raise AssertionError("Scripted current-document approval was not accepted")
    final = drive(app, trace)
    if final.progression.state != "DONE":
        raise AssertionError("Plan pipeline did not reach DONE: " + str(final.progression))
    docs = [MenxiaGroupDoc.parse(row.doc_markdown) for row in final.review.menxia_groups]
    if not docs or any(doc.open_blocking() or doc.pending_rejections() for doc in docs):
        raise AssertionError("Final plan still has blocking review suggestions")
    (output / "final-plan.md").write_text(
        "# 补证与修订路由修复方案（脚本验收产物）\n\n"
        "本产物由固定回复生成，用于检查工序闭环；不代表真实模型评估结果。\n\n"
        + "\n\n".join(doc.render() for doc in docs), encoding="utf-8")
    report = {"mode": "scripted", "model_calls": 0, "external_notifications": False,
              "simulated_human_approval": True, "restart_at_approval_gate": True,
              "request": REQUEST, "state": final.progression.state,
              "contract_transport_guard_checked": adapter.contract_checked,
              "bad_draft_injected": adapter.bad_draft_sent,
              "dispatches": [request.target_state for request in adapter.dispatched],
              "document_versions": [doc.version for doc in docs], "trace": trace}
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--output", type=Path,
                        default=Path("runs") / ("plan-pipeline-smoke-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")))
    args = parser.parse_args()
    if not 1 <= args.repeat <= 20:
        parser.error("--repeat must be between 1 and 20")
    reports = [run_once(args.output / f"run-{index}", index=index) for index in range(1, args.repeat + 1)]
    summary = {"mode": "scripted", "runs": len(reports), "done": sum(report["state"] == "DONE" for report in reports),
               "model_calls": 0, "output": str(args.output.resolve())}
    (args.output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
