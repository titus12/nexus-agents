from __future__ import annotations

import logging
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from orchestrator.logging_setup import HourlyFileHandler, configure_logging


class HourlyLoggingTests(unittest.TestCase):
    def tearDown(self) -> None:
        logger = logging.getLogger("review_orchestrator_fsm")
        for handler in list(logger.handlers):
            if getattr(handler, "_review_orchestrator_handler", False):
                logger.removeHandler(handler)
                handler.close()

    def test_writes_hourly_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            configure_logging(directory, retention_hours=72)
            logging.getLogger("review_orchestrator_fsm").info("hourly-test")
            files = list(Path(directory).glob("orchestrator-*.log"))
            self.assertEqual(len(files), 1)
            self.assertIn("hourly-test", files[0].read_text(encoding="utf-8"))

    def test_removes_files_older_than_retention(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            old_file = Path(directory) / "orchestrator-old.log"
            new_file = Path(directory) / "orchestrator-new.log"
            old_file.write_text("old", encoding="utf-8")
            new_file.write_text("new", encoding="utf-8")
            old_time = time.time() - (73 * 60 * 60)
            os.utime(old_file, (old_time, old_time))

            handler = HourlyFileHandler(directory, retention_hours=72)
            handler._last_cleanup_at = datetime.now().astimezone() - timedelta(minutes=2)
            record = logging.LogRecord(
                "test", logging.INFO, __file__, 1, "cleanup-test", (), None
            )
            handler.emit(record)
            handler.close()

            self.assertFalse(old_file.exists())
            self.assertTrue(new_file.exists())

    def test_repeated_configuration_does_not_duplicate_handlers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            configure_logging(directory)
            configure_logging(directory)
            logger = logging.getLogger("review_orchestrator_fsm")
            marked = [
                handler for handler in logger.handlers
                if getattr(handler, "_review_orchestrator_handler", False)
            ]
            self.assertEqual(len(marked), 2)


if __name__ == "__main__":
    unittest.main()
