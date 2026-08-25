from __future__ import annotations

import logging
import os
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


def configure_logging(
    log_directory: str | Path | None = None,
    retention_hours: int = DEFAULT_RETENTION_HOURS,
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

    console = logging.StreamHandler()
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

