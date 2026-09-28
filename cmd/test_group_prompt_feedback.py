"""Group wave prompts must carry delivery discipline and re-ask feedback.

Live incident task-20260927-de54aa: the group capsule is the whole prompt.txt
for a group worker, so the single-worker ``build_prompt`` guarantees ("one
strictly valid JSON result", ``retry_feedback``) never reached group critics.
Waves 8/10 were re-sampled blind and died unstructured until the reply budget
expired into a human gate.
"""

from __future__ import annotations

import unittest

from orchestrator.domain.context import (
    ProgressState,
    RecoveryState,
    ReviewState,
    ReviewTaskGroup,
    ReviewTaskItem,
    TaskIdentity,
    WorkflowContext,
    ZhongshuGroupState,
)
from orchestrator.domain.errors import FailureRecord
from orchestrator.domain.states import (
    _group_review_bindings,
    _group_revise_bindings,
)
from orchestrator.zhongshu_review_queue import build_group_capsule


DOC = (
    "# group-000001 需求文档 [v1]\n"
    "## 1. 背景\n背景\n## 2. 目标\n目标\n## 3. 标识与范围\n范围\n"
    "## 4. 状态与边界语义\n边界\n## 5. 行为要求\n行为\n## 6. 责任边界\n责任\n"
    "## 7. 交叉不变量\n不变量\n## 8. 验收标准\n### item-000001\naccept-item-000001\n"
    "### item-000002\naccept-item-000002\n## 9. 非目标\n非目标"
)


def _failure(code: str, state: str, message: str = "boom") -> FailureRecord:
    return FailureRecord(
        failure_id="f-1",
        stage="node_join",
        owner_component="agent_node_joiner",
        task_id="task-1",
        state=state,
        sequence=10,
        node_run_id="node:task-1:ZHONGSHU_CRITIC:10",
        worker_id="zhongshu_critic-worker-02",
        effect_id=None,
        error_code=code,
        retryable=True,
        message=message,
        cause_type="WorkerResult",
    )


def _context(failure: FailureRecord | None) -> WorkflowContext:
    review = ReviewState(
        revision_id="R1",
        plan_hash="plan-hash",
        task_items=(
            ReviewTaskItem(
                item_id="item-000001",
                group_id="group-000001",
                title="t1",
                objective="o1",
                source_requirement_ids=("req-000001",),
                acceptance_signals=("accept-item-000001",),
            ),
            ReviewTaskItem(
                item_id="item-000002",
                group_id="group-000001",
                title="t2",
                objective="o2",
                source_requirement_ids=("req-000001",),
                acceptance_signals=("accept-item-000002",),
            ),
        ),
        task_groups=(
            ReviewTaskGroup(
                group_id="group-000001",
                item_ids=("item-000001", "item-000002"),
            ),
        ),
        zhongshu_groups=(
            ZhongshuGroupState(
                group_id="group-000001",
                doc_version=1,
                doc_hash="doc-hash",
                doc_markdown=DOC,
            ),
        ),
    )
    return WorkflowContext(
        identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
        progression=ProgressState("ZHONGSHU_CRITIC", 10, "2026-09-27T00:00:00Z"),
        recovery=RecoveryState(last_failure=failure),
        review=review,
    )


class GroupCapsuleDisciplineTests(unittest.TestCase):
    def test_capsule_states_the_single_json_rule(self) -> None:
        capsule = build_group_capsule(
            group_id="group-000001",
            revision_id="R1",
            doc_markdown=DOC,
            members_full=[
                {
                    "item_id": "item-000001",
                    "title": "t1",
                    "objective": "o1",
                    "dependencies": [],
                    "source_requirement_ids": ["req-000001"],
                    "acceptance_signals": ["accept-item-000001"],
                }
            ],
        )

        self.assertIn("[Delivery discipline]", capsule)
        self.assertIn("exactly one complete", capsule)
        self.assertIn("no prose", capsule)

    def test_discipline_applies_to_custom_headers_too(self) -> None:
        capsule = build_group_capsule(
            group_id="group-000001",
            revision_id="R1",
            doc_markdown=DOC,
            members_full=(),
            header="Group revision job: custom",
        )

        self.assertIn("Group revision job: custom", capsule)
        self.assertIn("[Delivery discipline]", capsule)


class GroupReviewBindingFeedbackTests(unittest.TestCase):
    def test_review_capsule_restates_the_reply_rejection(self) -> None:
        failure = _failure(
            "AGENT_REPLY_UNSTRUCTURED",
            "ZHONGSHU_CRITIC",
            "agent worker returned an unstructured reply instead of "
            "the required JSON result contract: the reply body must be "
            "exactly one complete JSON object",
        )
        bindings = _group_review_bindings(_context(failure), "R1", "plan-hash")

        assert bindings
        prompt = str(bindings[0]["prompt_ref"])
        self.assertIn("[Delivery discipline]", prompt)
        self.assertIn("[Retry feedback]", prompt)
        self.assertIn("unstructured reply", prompt)
        self.assertIn("exactly one complete JSON object", prompt)

    def test_review_capsule_is_silent_without_a_matching_failure(self) -> None:
        bindings = _group_review_bindings(
            _context(_failure("AGENT_REPLY_UNSTRUCTURED", "ZHONGSHU_SOLVER")),
            "R1",
            "plan-hash",
        )

        assert bindings
        prompt = str(bindings[0]["prompt_ref"])
        self.assertIn("[Delivery discipline]", prompt)
        self.assertNotIn("[Retry feedback]", prompt)

    def test_review_envelope_declares_the_transient_feedback(self) -> None:
        bindings = _group_review_bindings(_context(None), "R1", "plan-hash")

        assert bindings
        ingredients = bindings[0]["dispatch_context"]["envelope"].get(
            "ingredients", ()
        )
        keys = [entry.get("key") for entry in ingredients]
        self.assertIn("retry_feedback", keys)

    def test_revise_capsule_restates_the_solver_rejection(self) -> None:
        failure = _failure(
            "SOLVER_GROUP_DOC_INVALID:acceptance_orphan:all",
            "ZHONGSHU_SOLVER",
            "SOLVER_GROUP_DOC_INVALID:acceptance_orphan",
        )
        bindings = _group_revise_bindings(
            _context(failure),
            "R1",
            "plan-hash",
            affected_groups=("group-000001",),
            editable_by_group={"group-000001": ("item-000001", "item-000002")},
        )

        assert bindings
        prompt = str(bindings[0]["prompt_ref"])
        self.assertIn("[Delivery discipline]", prompt)
        self.assertIn("[Retry feedback]", prompt)
        self.assertIn("acceptance_orphan", prompt)


if __name__ == "__main__":
    unittest.main()
