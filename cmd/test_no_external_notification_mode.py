from __future__ import annotations

import os
import unittest
from unittest import mock

from orchestrator.adapters import feishu_notifications_enabled


class NoExternalNotificationModeTests(unittest.TestCase):
    def test_test_mode_env_disables_feishu(self) -> None:
        with mock.patch.dict(
            os.environ, {"NEXUS_TEST_NO_EXTERNAL_NOTIFICATIONS": "1"}, clear=True
        ):
            self.assertFalse(feishu_notifications_enabled())

    def test_enable_env_disables_feishu(self) -> None:
        with mock.patch.dict(
            os.environ, {"ENABLE_FEISHU_NOTIFICATIONS": "0"}, clear=True
        ):
            self.assertFalse(feishu_notifications_enabled())

    def test_default_enables_feishu(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertTrue(feishu_notifications_enabled())


if __name__ == "__main__":
    unittest.main()
