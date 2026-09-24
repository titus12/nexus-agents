from __future__ import annotations

import unittest

from orchestrator.domain.errors import FailureRecord
from orchestrator.domain.menxia_doc import (
    BODY_PART_TITLES,
    MenxiaGroupDoc,
    render_initial,
)
from orchestrator.runtime.agent_effects import AgentNodeJoiner
from orchestrator.runtime.nodes import WorkerResult


def _six_part_body(
    acceptance_entries: tuple[str, ...] = ("Task A is observable",),
) -> str:
    parts = {
        "目标与设计决策": "按基线执行，不做额外决策。",
        "现状与目标行为": "当前行为与目标行为如基线所述。",
        "统一约束": "遵循统一约束。",
        "任务分解": "- 任务 A -> 一次执行。",
        "验收映射": "\n".join(
            f"- {entry} -> 验收用例覆盖。" for entry in acceptance_entries
        ),
        "风险与兼容": "风险可控。",
    }
    return "\n\n".join(
        f"### {title}\n{parts[title]}" for title in BODY_PART_TITLES
    )


REQUIREMENT_MARKDOWN = "\n".join([
    "# group-000001 需求文档 [v1]",
    "## 1. 背景",
    "任务来自 issue 需求。",
    "## 2. 目标",
    "输出 A。",
    "## 3. 标识与范围",
    "group 覆盖 item。",
    "## 4. 状态与边界语义",
    "明确边界。",
    "## 5. 行为要求",
    "返回结构化结果。",
    "## 6. 责任边界",
    "Solver 撰写，Critic 裁决。",
    "## 7. 交叉不变量",
    "依赖组冻结前本组不得冻结。",
    "## 8. 验收标准",
    "Task A is observable",
    "## 9. 非目标",
    "不包含部署。",
])


def _doc_v1() -> str:
    return render_initial(
        group_id="group-000001",
        baseline_markdown="需求基线",
        body_markdown=_six_part_body(),
    )


def _doc_v2(touched: bool = True) -> str:
    return _doc_v1().replace("[v1]", "[v2]").replace(
        "按基线执行，不做额外决策。", "按基线执行，追加 B。"
    )


def _solver_joiner(previous: str | None = None) -> AgentNodeJoiner:
    context = {"group_id": "group-000001"}
    if previous is not None:
        context["doc_markdown"] = previous
    return AgentNodeJoiner(
        "node-1",
        task_id="task-1",
        state="MENXIA_GROUP_SOLVER",
        dispatch_mode="menxia_group_pipeline",
        binding_contexts={"worker-1": context},
        menxia_stage_census={"group-000001": "SOLVING"},
    )


def _result(payload: object) -> WorkerResult:
    return WorkerResult("worker-1", "SUCCEEDED", None, result_payload=payload)


class TestGroupSolverJoin(unittest.TestCase):
    def test_valid_solver_reply_folds_doc_chain(self) -> None:
        joiner = _solver_joiner(_doc_v1())
        payload = {
            "action": "READY_FOR_ANALYST",
            "doc_markdown": _doc_v2(),
            "doc_version": 2,
            "absorbed_ids": [],
            "rejected_ids": [],
            "touched_scope": ["目标与设计决策"],
        }
        node = joiner.join((_result(payload),))
        self.assertEqual(node.status, "SUCCEEDED")
        aggregate = node.aggregate
        self.assertEqual(aggregate["action"], "READY_FOR_ANALYST")
        rows = aggregate["menxia_group_results"]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["group_id"], "group-000001")
        self.assertEqual(row["action"], "READY_FOR_ANALYST")
        self.assertEqual(row["doc_version"], 2)
        self.assertTrue(row["doc_hash"])
        self.assertEqual(row["open_count"], 0)

    def test_breach_is_demoted_to_blocked(self) -> None:
        joiner = _solver_joiner(_doc_v1())
        payload = {
            "action": "READY_FOR_ANALYST",
            "doc_markdown": _doc_v2(),
            "touched_scope": [],
        }
        node = joiner.join((_result(payload),))
        self.assertEqual(node.status, "SUCCEEDED")
        aggregate = node.aggregate
        row = aggregate["menxia_group_results"][0]
        self.assertEqual(row["action"], "BLOCKED")
        self.assertIn("MENXIA_GROUP_DOC_BREACH", row["blocked_reason"])
        # Mirrors the item pipeline: the joiner routes by active stages;
        # the drain-out tail (only parked rows) is intercepted by the
        # state-level wave decision, not by the aggregate action.
        self.assertEqual(aggregate["action"], "READY_FOR_ANALYST")

    def test_first_round_needs_no_previous_document(self) -> None:
        joiner = _solver_joiner(None)
        payload = {
            "action": "FEASIBLE",
            "doc_markdown": _doc_v1(),
        }
        node = joiner.join((_result(payload),))
        row = node.aggregate["menxia_group_results"][0]
        self.assertEqual(row["action"], "FEASIBLE")
        self.assertEqual(row["doc_version"], 1)

    def test_infra_failure_returns_none_for_retry(self) -> None:
        joiner = _solver_joiner(_doc_v1())
        failure = FailureRecord(
            failure_id="f1",
            stage="agent",
            owner_component="t",
            task_id="task-1",
            state="MENXIA_GROUP_SOLVER",
            sequence=0,
            node_run_id="node-1",
            worker_id="worker-1",
            effect_id=None,
            error_code="AGENT_TIMEOUT",
            retryable=True,
            message="timeout",
            cause_type="t",
        )
        results = (WorkerResult("worker-1", "FAILED", None, failure=failure),)
        self.assertIsNone(joiner._join_menxia_group(results))

    def test_content_failure_blocks_the_group(self) -> None:
        joiner = _solver_joiner(_doc_v1())
        failure = FailureRecord(
            failure_id="f1",
            stage="agent",
            owner_component="t",
            task_id="task-1",
            state="MENXIA_GROUP_SOLVER",
            sequence=0,
            node_run_id="node-1",
            worker_id="worker-1",
            effect_id=None,
            error_code="CONTRACT_INVALID",
            retryable=False,
            message="bad reply",
            cause_type="t",
        )
        results = (WorkerResult("worker-1", "FAILED", None, failure=failure),)
        node = joiner._join_menxia_group(results)
        row = node.aggregate["menxia_group_results"][0]
        self.assertEqual(row["action"], "BLOCKED")
        self.assertEqual(row["blocked_reason"], "CONTRACT_INVALID")


class TestGroupCriticJoin(unittest.TestCase):
    def _critic_joiner(self, previous: str) -> AgentNodeJoiner:
        return AgentNodeJoiner(
            "node-1",
            task_id="task-1",
            state="MENXIA_GROUP_CRITIC",
            dispatch_mode="menxia_group_pipeline",
            binding_contexts={"worker-1": {
                "group_id": "group-000001",
                "doc_markdown": previous,
                "requirement_markdown": REQUIREMENT_MARKDOWN,
            }},
            menxia_stage_census={"group-000001": "REVIEWING"},
        )

    def test_reply_group_id_mismatch_blocks(self) -> None:
        joiner = _solver_joiner(_doc_v1())
        payload = {
            "action": "READY_FOR_ANALYST",
            "group_id": "group-000002",
            "doc_markdown": _doc_v2(),
            "doc_version": 2,
            "touched_scope": ["步骤"],
        }
        node = joiner.join((_result(payload),))
        row = node.aggregate["menxia_group_results"][0]
        # The dispatch binding is the identity authority: a reply claiming
        # another group must never fold into the bound group's chain.
        self.assertEqual(row["group_id"], "group-000001")
        self.assertEqual(row["action"], "BLOCKED")
        self.assertIn("MENXIA_GROUP_IDENTITY_MISMATCH", row["blocked_reason"])
        self.assertIn("group-000002", row["blocked_reason"])

    def test_critic_approval_with_open_blockers_folds_back(self) -> None:
        with_p1 = _doc_v1().replace(
            "## 2. 建议段",
            "## 2. 建议段\n[S-001][critic][v1 base][P1][open] 补风险登记",
        )
        joiner = self._critic_joiner(with_p1)
        payload = {
            "action": "APPROVE_GROUP",
            "group_id": "group-000001",
            "doc_markdown": with_p1,
            "added_suggestion_ids": [],
        }
        node = joiner.join((_result(payload),))
        row = node.aggregate["menxia_group_results"][0]
        # The convergence rule gates the approval: open blocking suggestions
        # fold the wave back to the solver instead of advancing to the gate.
        self.assertEqual(row["action"], "REQUEST_SOLVER_REVISION")
        self.assertEqual(row["doc_version"], 1)

    def test_critic_approval_with_clean_document_passes(self) -> None:
        joiner = self._critic_joiner(_doc_v1())
        payload = {
            "action": "APPROVE_GROUP",
            "group_id": "group-000001",
            "doc_markdown": _doc_v1(),
            "added_suggestion_ids": [],
        }
        node = joiner.join((_result(payload),))
        row = node.aggregate["menxia_group_results"][0]
        self.assertEqual(row["action"], "APPROVE_GROUP")
        self.assertEqual(row["open_count"], 0)

    def test_critic_approval_map_beyond_requirement_folds_back(self) -> None:
        beyond = _doc_v1().replace(
            "- Task A is observable -> 验收用例覆盖。",
            "- Ghost is observable -> 验收用例覆盖。",
        )
        joiner = self._critic_joiner(beyond)
        payload = {
            "action": "APPROVE_GROUP",
            "group_id": "group-000001",
            "doc_markdown": beyond,
            "added_suggestion_ids": [],
        }
        node = joiner.join((_result(payload),))
        row = node.aggregate["menxia_group_results"][0]
        # The acceptance map must close against the requirement document's
        # §8 lines: an entry the requirement never stated folds the wave
        # back to the solver instead of advancing to the gate.
        self.assertEqual(row["action"], "REQUEST_SOLVER_REVISION")

    def test_critic_approval_without_requirement_doc_passes(self) -> None:
        # Legacy bindings carry no requirement_markdown: the map closure is
        # skipped and the approval folds as before.
        joiner = AgentNodeJoiner(
            "node-1",
            task_id="task-1",
            state="MENXIA_GROUP_CRITIC",
            dispatch_mode="menxia_group_pipeline",
            binding_contexts={"worker-1": {
                "group_id": "group-000001",
                "doc_markdown": _doc_v1(),
            }},
            menxia_stage_census={"group-000001": "REVIEWING"},
        )
        payload = {
            "action": "APPROVE_GROUP",
            "group_id": "group-000001",
            "doc_markdown": _doc_v1(),
            "added_suggestion_ids": [],
        }
        node = joiner.join((_result(payload),))
        row = node.aggregate["menxia_group_results"][0]
        self.assertEqual(row["action"], "APPROVE_GROUP")


class TestGroupAnalystJoin(unittest.TestCase):
    def test_analyst_appends_suggestion(self) -> None:
        with_line = _doc_v1().replace(
            "## 2. 建议段",
            "## 2. 建议段\n[S-001][analyst][v1 base][P1][open] 补回滚",
        )
        joiner = AgentNodeJoiner(
            "node-1",
            task_id="task-1",
            state="MENXIA_GROUP_ANALYST",
            dispatch_mode="menxia_group_pipeline",
            binding_contexts={"worker-1": {
                "group_id": "group-000001",
                "doc_markdown": _doc_v1(),
            }},
            menxia_stage_census={"group-000001": "ANALYZING"},
        )
        payload = {
            "action": "EVIDENCE_SUFFICIENT",
            "doc_markdown": with_line,
            "added_suggestion_ids": ["S-001"],
        }
        node = joiner.join((_result(payload),))
        row = node.aggregate["menxia_group_results"][0]
        self.assertEqual(row["action"], "EVIDENCE_SUFFICIENT")
        self.assertEqual(row["open_count"], 1)
        self.assertEqual(row["doc_version"], 1)

    def test_analyst_breach_blocks(self) -> None:
        joiner = AgentNodeJoiner(
            "node-1",
            task_id="task-1",
            state="MENXIA_GROUP_ANALYST",
            dispatch_mode="menxia_group_pipeline",
            binding_contexts={"worker-1": {
                "group_id": "group-000001",
                "doc_markdown": _doc_v1(),
            }},
            menxia_stage_census={"group-000001": "ANALYZING"},
        )
        payload = {
            "action": "EVIDENCE_SUFFICIENT",
            "doc_markdown": _doc_v1().replace("风险可控。", "风险失控。"),
        }
        node = joiner.join((_result(payload),))
        row = node.aggregate["menxia_group_results"][0]
        self.assertEqual(row["action"], "BLOCKED")
        self.assertIn("frozen sections", row["blocked_reason"])


if __name__ == "__main__":
    unittest.main()
