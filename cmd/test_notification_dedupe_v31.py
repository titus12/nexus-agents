from __future__ import annotations

import unittest

from orchestrator.context import StateContext
from orchestrator.states import ZhongshuAnalystState


class RecordingFeishu:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def notify(self, text: str, role: str) -> None:
        self.messages.append((text, role))


class NotificationDedupeTests(unittest.TestCase):
    def test_repeated_enter_emits_one_notification_for_same_sequence(self):
        feishu = RecordingFeishu()
        state = ZhongshuAnalystState(feishu=feishu)
        ctx = StateContext(task_id="task-1", workflow_state="ZHONGSHU_ANALYST")
        state.enter(ctx)
        state.enter(ctx)
        self.assertEqual(len(feishu.messages), 1)
        self.assertEqual(feishu.messages[0][1], "analyst")


if __name__ == "__main__":
    unittest.main()
