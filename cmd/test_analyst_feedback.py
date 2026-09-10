from __future__ import annotations

import json
import unittest

from orchestrator.context import StateContext
from orchestrator.states import ZhongshuAnalystState


class AnalystFeedbackTests(unittest.TestCase):
    def test_critic_evidence_request_is_injected(self):
        ctx = StateContext(
            task_id="task-1",
            raw_request="检查知识库覆盖",
            workflow_state="ZHONGSHU_ANALYST",
            request_payload={
                "zhongshu_critic_review": {
                    "action": "REQUEST_ANALYST_EVIDENCE",
                    "findings": [
                        {
                            "finding_id": "finding-1",
                            "severity": "P1",
                            "title": "缺少能力覆盖基线",
                        }
                    ],
                    "missing_evidence": ["ev-coverage"],
                    "questions_for_analyst": ["请输出 capability map"],
                    "remaining_blockers": ["REQ-003"],
                }
            },
        )
        prompt = json.loads(ZhongshuAnalystState().request(ctx).prompt)
        self.assertEqual(
            prompt["critic_feedback"]["action"],
            "REQUEST_ANALYST_EVIDENCE",
        )
        self.assertEqual(
            prompt["critic_feedback"]["questions_for_analyst"],
            ["请输出 capability map"],
        )
        self.assertEqual(prompt["critic_feedback"]["remaining_blockers"], ["REQ-003"])
        self.assertIn("只针对 Critic 指定的证据缺口补证", prompt["repair_instruction"])

    def test_non_evidence_critic_action_is_not_injected(self):
        ctx = StateContext(
            task_id="task-1",
            raw_request="检查知识库覆盖",
            workflow_state="ZHONGSHU_ANALYST",
            request_payload={
                "zhongshu_critic_review": {
                    "action": "REQUEST_SOLVER_REVISION",
                    "findings": [{"title": "方案需要调整"}],
                }
            },
        )
        prompt = json.loads(ZhongshuAnalystState().request(ctx).prompt)
        self.assertNotIn("critic_feedback", prompt)
        self.assertNotIn("repair_instruction", prompt)


if __name__ == "__main__":
    unittest.main()
