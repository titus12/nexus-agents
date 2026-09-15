"""Dedicated test runner that can never notify Feishu.

The production environment exports real ``FEISHU_*`` credentials and
``HUMAN_GATE_CHAT_ID``.  A bare ``python -m unittest`` therefore lets tests that
build an ``OrchestratorApp`` fall through to the live ``FeishuNotificationPort``
and actually post to the chat.

Run the suite through this entrypoint instead of ``python -m unittest``:
it sets ``NEXUS_TEST_NO_EXTERNAL_NOTIFICATIONS=1`` before any test module is
imported, so ``OrchestratorApp`` falls back to the in-process
``NullNotificationPort`` and ``FeishuHttpAdapter`` refuses to send.

Usage:
    python run_tests.py                  # discover ./test_*.py
    python run_tests.py test_a test_b    # specific test modules
"""

from __future__ import annotations

import os
import sys
import unittest


def main(argv: list[str]) -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)

    os.environ["NEXUS_TEST_NO_EXTERNAL_NOTIFICATIONS"] = "1"
    os.environ.setdefault("ENABLE_FEISHU_NOTIFICATIONS", "0")

    loader = unittest.defaultTestLoader
    if argv:
        suite = loader.loadTestsFromNames(argv)
    else:
        suite = loader.discover(here, pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
