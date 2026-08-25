from __future__ import annotations

import os
import socket
import time
import uuid
import json
from pathlib import Path
from typing import Any


class TaskLockError(RuntimeError):
    pass


class TaskLock:
    """Exclusive task lock with a metadata sidecar and lease timestamp."""

    def __init__(self, path: str | Path, lease_seconds: int = 120) -> None:
        self.path = Path(path)
        self.meta_path = self.path.with_suffix(self.path.suffix + ".json")
        self.lease_seconds = lease_seconds
        self.owner_token = uuid.uuid4().hex
        self._handle = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._handle = self.path.open("x", encoding="utf-8")
        except FileExistsError as error:
            metadata = self._read_metadata()
            if self._can_reclaim(metadata):
                try:
                    self.path.unlink()
                    self.meta_path.unlink(missing_ok=True)
                except OSError as reclaim_error:
                    raise TaskLockError(f"task lock exists and cannot be reclaimed: {self.path}") from reclaim_error
                self._handle = self.path.open("x", encoding="utf-8")
                self._write_metadata()
                return
            raise TaskLockError(f"task lock already exists: {self.path}") from error
        self._write_metadata()

    def refresh(self) -> None:
        if self._handle is None:
            raise TaskLockError("lock is not held")
        self._write_metadata()

    def release(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None
        for path in (self.path, self.meta_path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    def __enter__(self) -> "TaskLock":
        self.acquire()
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _tb: Any) -> None:
        self.release()

    def _write_metadata(self) -> None:
        data = {
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "owner_token": self.owner_token,
            "acquired_at": time.time(),
            "lease_until": time.time() + self.lease_seconds,
        }
        self.meta_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _read_metadata(self) -> dict[str, Any]:
        try:
            return json.loads(self.meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _can_reclaim(self, metadata: dict[str, Any]) -> bool:
        lease_until = float(metadata.get("lease_until", 0))
        pid = int(metadata.get("pid", 0) or 0)
        if lease_until >= time.time() or pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except OSError:
            return True
        return False
