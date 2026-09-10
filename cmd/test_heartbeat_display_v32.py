from __future__ import annotations

import unittest

from orchestrator.context import StateContext
from orchestrator.states import ZhongshuAnalystState


class HeartbeatDisplayV32Tests(unittest.TestCase):
    def test_heartbeat_is_sent_to_feishu_with_current_role(self):
        class RecordingFeishu:
            def __init__(self):
                self.messages = []

            def notify(self, text, role):
                self.messages.append((text, role))
                return "heartbeat-message"

        feishu = RecordingFeishu()
        ctx = StateContext(
            task_id="task-1",
            workflow_state="ZHONGSHU_ANALYST",
            current_phase="ZHONGSHU",
            current_role="review-analyst",
            active_item_id="item-000001",
        )
        ZhongshuAnalystState(feishu=feishu).notify_heartbeat(ctx, 120)
        self.assertEqual(len(feishu.messages), 1)
        text, role = feishu.messages[0]
        self.assertEqual(role, "analyst")
        self.assertIn("任务仍在运行", text)
        self.assertIn("分析师正在整理证据", text)
        self.assertIn("已等待：2 分钟", text)


if __name__ == "__main__":
    unittest.main()
