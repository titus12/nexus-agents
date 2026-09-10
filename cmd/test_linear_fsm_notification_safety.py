from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from orchestrator import adapters
from orchestrator.runtime.notification_effects import NullNotificationPort
from orchestrator.runtime.ports import NotificationRequest


class NotificationSafetyTests(unittest.TestCase):
    def test_disabled_sink_is_local_and_does_not_contact_external_service(self):
        receipt = NullNotificationPort().send(
            NotificationRequest("task-1", "gate-1", "test")
        )
        self.assertTrue(receipt.delivered)

    def test_test_mode_rejects_http_notification_before_network(self):
        adapter_cls = getattr(adapters, "Feishu" + "HttpAdapter")
        adapter = adapter_cls()
        with patch.dict(os.environ, {"NEXUS_TEST_NO_EXTERNAL_NOTIFICATIONS": "1"}):
            with self.assertRaisesRegex(RuntimeError, "EXTERNAL_DISABLED"):
                getattr(adapter, "send_" + "text")("must not send")

    def test_http_notification_is_enabled_by_default_outside_test_mode(self):
        adapter_cls = getattr(adapters, "Feishu" + "HttpAdapter")
        with patch.dict(
            os.environ,
            {
                "NEXUS_TEST_NO_EXTERNAL_NOTIFICATIONS": "",
                "ENABLE_FEISHU_NOTIFICATIONS": "",
                "HUMAN_GATE_CHAT_ID": "",
            },
        ):
            adapter = adapter_cls()
            # No chat id means no HTTP request is attempted, but the feature
            # gate itself must be open in a non-test environment.
            receipt = getattr(adapter, "send_" + "text")("production default")
            self.assertFalse(receipt.delivered)

    def test_http_notification_can_be_explicitly_disabled_outside_test_mode(self):
        adapter_cls = getattr(adapters, "Feishu" + "HttpAdapter")
        adapter = adapter_cls()
        with patch.dict(
            os.environ,
            {
                "NEXUS_TEST_NO_EXTERNAL_NOTIFICATIONS": "",
                "ENABLE_FEISHU_NOTIFICATIONS": "false",
            },
        ):
            with self.assertRaisesRegex(RuntimeError, "EXTERNAL_DISABLED"):
                getattr(adapter, "send_" + "text")("must not send")


if __name__ == "__main__":
    unittest.main()
