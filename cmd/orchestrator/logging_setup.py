from __future__ import annotations

import logging
import os
import queue
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import TextIO


DEFAULT_RETENTION_HOURS = 72
LOGGER_NAME = "review_orchestrator_fsm"
_HANDLER_MARKER = "_review_orchestrator_handler"


class HourlyFileHandler(logging.Handler):
    """Append records to one UTF-8 file per local clock hour."""

    def __init__(self, directory: str | Path, retention_hours: int = DEFAULT_RETENTION_HOURS) -> None:
        super().__init__()
        self.directory = Path(directory)
        self.retention_hours = max(1, int(retention_hours))
        self.directory.mkdir(parents=True, exist_ok=True)
        self._cleanup_lock = threading.Lock()
        self._last_cleanup_at: datetime | None = None
        setattr(self, _HANDLER_MARKER, True)

    def _path_for(self, now: datetime) -> Path:
        return self.directory / f"orchestrator-{now:%Y%m%d-%H}.log"

    def emit(self, record: logging.LogRecord) -> None:
        try:
            now = datetime.now().astimezone()
            path = self._path_for(now)
            rendered = self.format(record)
            with self.lock:
                with path.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(rendered + "\n")
                    handle.flush()
            self._cleanup_if_due(now)
        except Exception:
            self.handleError(record)

    def _cleanup_if_due(self, now: datetime) -> None:
        if self._last_cleanup_at and (now - self._last_cleanup_at).total_seconds() < 60:
            return
        with self._cleanup_lock:
            if self._last_cleanup_at and (now - self._last_cleanup_at).total_seconds() < 60:
                return
            cutoff = now - timedelta(hours=self.retention_hours)
            for path in self.directory.glob("orchestrator-*.log"):
                try:
                    modified = datetime.fromtimestamp(path.stat().st_mtime, tz=now.tzinfo)
                    if modified < cutoff:
                        path.unlink()
                except FileNotFoundError:
                    continue
                except OSError:
                    # A locked file must not stop the workflow.
                    continue
            self._last_cleanup_at = now

    def close(self) -> None:
        super().close()


def default_log_directory() -> Path:
    configured = os.environ.get("ORCHESTRATOR_LOG_DIR")
    if configured:
        return Path(configured)
    # app.py is cmd/orchestrator/app.py; parents[2] is nexus-agents.
    return Path(__file__).resolve().parents[2] / "multica"


class NonBlockingConsoleHandler(logging.Handler):
    """Emit formatted records to a console stream without blocking the caller.

    A console stream can stall indefinitely (dead console handle, full pipe
    whose relay reader stopped). Blocking on it inside emit() freezes the
    workflow loop, so records are formatted on the caller thread and handed
    to a daemon writer over a bounded queue. Overflow drops the record
    because the hourly file handler stays the source of truth.
    """

    def __init__(self, stream: TextIO, owns_stream: bool = False, max_pending: int = 256) -> None:
        super().__init__()
        self._stream = stream
        self._owns_stream = owns_stream
        self._pending: queue.Queue[str | None] = queue.Queue(maxsize=max_pending)
        self._writer = threading.Thread(
            target=self._drain, name="orchestrator-console-writer", daemon=True
        )
        self._writer.start()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            rendered = self.format(record) + "\n"
        except Exception:
            self.handleError(record)
            return
        try:
            self._pending.put_nowait(rendered)
        except queue.Full:
            pass

    def _drain(self) -> None:
        while True:
            rendered = self._pending.get()
            if rendered is None:
                return
            try:
                self._stream.write(rendered)
                self._stream.flush()
            except Exception:
                pass

    def close(self) -> None:
        for _ in range(3):
            try:
                self._pending.put_nowait(None)
                break
            except queue.Full:
                try:
                    self._pending.get_nowait()
                except queue.Empty:
                    pass
        super().close()
        if self._owns_stream:
            try:
                self._stream.close()
            except Exception:
                pass


def usable_console_stream(stream: TextIO | None) -> bool:
    if stream is None or getattr(stream, "closed", False):
        return False
    try:
        fileno = stream.fileno()
        if not isinstance(fileno, int) or fileno < 0:
            return False
        os.fstat(fileno)
    except (AttributeError, OSError, ValueError):
        return False
    return True


def resolve_console_stream(stream: TextIO | None = None) -> tuple[TextIO, bool]:
    """Return the console stream to write to and whether we own (opened) it."""
    candidate = sys.stderr if stream is None else stream
    if usable_console_stream(candidate):
        return candidate, False
    return open(os.devnull, "w", encoding="utf-8"), True


def configure_logging(
    log_directory: str | Path | None = None,
    retention_hours: int = DEFAULT_RETENTION_HOURS,
    console_stream: TextIO | None = None,
) -> Path:
    """Configure console + hourly file logging once and return the directory."""
    directory = Path(log_directory) if log_directory else default_log_directory()
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        "%Y-%m-%dT%H:%M:%S%z",
    )

    for handler in list(logger.handlers):
        if getattr(handler, _HANDLER_MARKER, False):
            logger.removeHandler(handler)
            handler.close()

    resolved_stream, owns_stream = resolve_console_stream(console_stream)
    console = NonBlockingConsoleHandler(resolved_stream, owns_stream=owns_stream)
    console.setLevel(logging.INFO)
    console.setFormatter(formatter)
    setattr(console, _HANDLER_MARKER, True)

    file_handler = HourlyFileHandler(directory, retention_hours)
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)

    logger.addHandler(console)
    logger.addHandler(file_handler)
    logger.info(
        "LOGGING_READY directory=%s retention_hours=%s",
        directory,
        retention_hours,
    )
    return directory

