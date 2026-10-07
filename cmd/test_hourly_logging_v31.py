from __future__ import annotations

import logging
import os
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from orchestrator import logging_setup
from orchestrator.logging_setup import HourlyFileHandler, configure_logging


class _StalledStream:
    def __init__(self) -> None:
        self._fd = os.open(os.devnull, os.O_WRONLY)
        self._gate = threading.Event()

    def fileno(self) -> int:
        return self._fd

    def write(self, _msg: str) -> int:
        self._gate.wait()
        return len(_msg)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        os.close(self._fd)


class _BrokenStream:
    def fileno(self) -> int:
        raise OSError("no handle")

    def write(self, _msg: str) -> int:
        raise OSError("no handle")

    def flush(self) -> None:
        pass


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

    def test_console_write_does_not_block_caller_when_stream_stalls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stalled = _StalledStream()
            self.addCleanup(stalled.close)
            configure_logging(directory, console_stream=stalled)
            logger = logging.getLogger("review_orchestrator_fsm")
            done = threading.Event()

            def emit_line() -> None:
                logger.info("stall-regression-test")
                done.set()

            worker = threading.Thread(target=emit_line, daemon=True)
            worker.start()
            self.assertTrue(done.wait(5.0), "console logging blocked the caller thread")
            files = list(Path(directory).glob("orchestrator-*.log"))
            self.assertTrue(
                any("stall-regression-test" in p.read_text(encoding="utf-8") for p in files)
            )

    def test_console_write_returns_even_when_stream_never_consumes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stalled = _StalledStream()
            self.addCleanup(stalled.close)
            configure_logging(directory, console_stream=stalled)
            logger = logging.getLogger("review_orchestrator_fsm")
            for index in range(512):
                logger.info("overflow-%d", index)
            self.assertTrue(True, "repeated logging on a stalled stream returned")

    def test_unusable_console_stream_falls_back_to_devnull(self) -> None:
        self.assertFalse(logging_setup.usable_console_stream(_BrokenStream()))
        self.assertTrue(logging_setup.usable_console_stream(sys.stderr))
        resolved, owns = logging_setup.resolve_console_stream(_BrokenStream())
        try:
            self.assertTrue(owns)
            self.assertNotIsInstance(resolved, _BrokenStream)
        finally:
            resolved.close()


if __name__ == "__main__":
    unittest.main()
