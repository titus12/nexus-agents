from __future__ import annotations

import tempfile
import unittest

from orchestrator.adapters import FakeFeishuAdapter, FakeMulticaAdapter
from orchestrator.app import OrchestratorApp
from orchestrator.context import StateContext
from orchestrator.models import AgentRequest, ExternalMessage
from orchestrator.zhongshu_parallel import canonical_plan_hash


ANALYST_PLAN = {
    "plan_id": "ANALYSIS-001",
    "version": 1,
    "phase": "ZHONGSHU",
    "problem_interpretation": "检查 Proposal 覆盖缺口",
    "objective": "形成有证据支持的候选任务和分组",
    "success_definition": "Solver 无需重新调查即可制定方案",
    "requirements": [{
        "requirement_id": "REQ-1",
        "statement": "补齐覆盖",
        "source": "user",
        "priority": "must",
        "scope": "in",
        "acceptance_signal": "形成缺口清单"
    }],
    "goals": ["识别缺口"],
    "non_goals": ["不修改代码"],
    "project_context": {"project_type": "go"},
    "confirmed_facts": [{
        "evidence_id": "ev-1",
        "statement": "设计缺少知识库页面",
        "source_type": "code",
        "source": "design/pages",
        "confidence": 0.99,
        "relevance": "证明覆盖缺口"
    }],
    "conflicts": [],
    "candidate_directions": [],
    "selected_direction": {},
    "alternatives": [],
    "comparison": [],
    "recommendation": {},
    "candidate_items": [{
        "item_id": "item-1",
        "title": "页面覆盖",
        "problem_addressed": "页面缺失",
        "objective": "补齐页面说明",
        "basis_evidence": ["ev-1"],
        "why_needed": "保持一致",
        "dependencies": [],
        "acceptance_signals": ["存在明确页面清单"],
        "risk_signals": []
    }],
    "candidate_groups": [{
        "candidate_group_id": "group-1",
        "title": "页面补全",
        "objective": "补齐页面覆盖",
        "reason": "共享同一目标",
        "related_items": ["item-1"],
        "basis_evidence": ["ev-1"],
        "dependencies": [],
        "suggested_order": 1
    }],
    "dependencies": [],
    "constraints": ["只读"],
    "scope": {"in_scope": [], "out_of_scope": [], "protected_paths": []},
    "assumptions": [],
    "unknowns": [],
    "risks": [],
    "questions_for_solver": ["如何正式分组？"],
    "candidate_verification_questions": ["是否覆盖全部需求？"],
    "questions_for_user": []
}


class ScriptedMultica(FakeMulticaAdapter):
    def dispatch(self, request: AgentRequest):
        receipt = super().dispatch(request)
        if request.context.get("contract_mode"):
            payload = {
                "action": "REQUIREMENT_CONTRACT_READY",
                "requirements": ANALYST_PLAN["requirements"],
            }
        elif ":ZHONGSHU_ANALYST:" in request.request_id:
            payload = {"action": "READY_FOR_SOLVER", "plan": ANALYST_PLAN}
        elif ":ZHONGSHU_SOLVER:" in request.request_id:
            payload = {
                "action": "READY_FOR_CRITIC",
                "plan": {
                    "requirements": ANALYST_PLAN["requirements"],
                    "items": [{
                        "item_id": "item-1",
                        "title": "item one",
                        "objective": "objective one",
                        "source_requirement_ids": ["REQ-1"],
                        "dependencies": [],
                        "acceptance_signals": ["observable item one"],
                        "unknowns": [],
                        "risks": [],
                        "parallelizable": True,
                    }, {
                        "item_id": "item-2",
                        "title": "item two",
                        "objective": "objective two",
                        "source_requirement_ids": ["REQ-1"],
                        "dependencies": [],
                        "acceptance_signals": ["observable item two"],
                        "unknowns": [],
                        "risks": [],
                        "parallelizable": True,
                    }],
                    "groups": [{
                        "group_id": "group-1",
                        "title": "初始化",
                        "objective": "覆盖初始化逻辑",
                        "items": [{
                            "item_id": "item-1",
                            "title": "补充知识库描述",
                            "objective": "覆盖缺失内容",
                        }],
                    }, {
                        "group_id": "group-2",
                        "title": "校验",
                        "objective": "补充校验逻辑",
                        "items": [{
                            "item_id": "item-2",
                            "title": "补充校验描述",
                            "objective": "覆盖校验内容",
                        }],
                    }]
                },
            }
            payload["plan"]["items"] = [
                item
                for group in payload["plan"]["groups"]
                for item in group["items"]
            ]
            for item in payload["plan"]["items"]:
                item.update({
                    "source_requirement_ids": ["REQ-1"],
                    "dependencies": [],
                    "acceptance_signals": ["observable completion"],
                    "unknowns": [],
                    "risks": [],
                    "parallelizable": True,
                })
            self.critic_plan_hash = canonical_plan_hash(payload["plan"])
        elif ":ZHONGSHU_CRITIC:" in request.request_id:
            payload = {
                "action": "APPROVE_FREEZE",
                "findings": [],
                "plan_hash": self.critic_plan_hash,
            }
        elif ":MENXIA_ITEM_SOLVER:" in request.request_id:
            payload = {"action": "FEASIBLE", "implementation_proposal": {"files": ["x.go"]}}
        elif ":MENXIA_ITEM_ANALYST:" in request.request_id:
            payload = {"action": "EVIDENCE_SUFFICIENT", "evidence": ["existing API"]}
        elif ":MENXIA_ITEM_CRITIC:" in request.request_id:
            payload = {"action": "APPROVE_ITEM", "findings": []}
        elif ":MENXIA_GROUP_GATE:" in request.request_id:
            payload = {"action": "APPROVE_GROUP"}
        else:
            return receipt
        payload.update({
            "task_id": request.task_id,
            "request_id": request.request_id,
            "role": request.role,
            "phase": request.phase,
        })
        self.queue_reply(request.request_id, ExternalMessage(request.agent_id, payload, request.request_id))
        return receipt


class FullWorkflowTests(unittest.TestCase):
    def test_zhongshu_to_menxia_to_done(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter = ScriptedMultica()
            ctx = StateContext(
                task_id="task-full",
                issue_id="SER-1",
                raw_request="检查初始化 Proposal",
            )
            app = OrchestratorApp(
                ctx,
                root=directory,
                multica=adapter,
                feishu=FakeFeishuAdapter(),
                poll_interval=0,
                timeout_seconds=30,
            )
            self.assertTrue(app.run())
            self.assertEqual(app.ctx.workflow_state, "DONE")
            # Zhongshu now runs three Analyst and three Critic workers in
            # parallel; the old single-worker count was 11.
            self.assertEqual(len(adapter.dispatched), 16)
            self.assertEqual(app.ctx.active_finding_ids, [])


if __name__ == "__main__":
    unittest.main()
