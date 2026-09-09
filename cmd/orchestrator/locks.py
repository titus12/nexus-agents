from __future__ import annotations

import os
import socket
import time
import uuid
import json
import tempfile
from pathlib import Path
from typing import Any

from .domain.errors import LeaseLostError

class TaskLockError(LeaseLostError):
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
                reclaim_path = self.path.with_name(
                    f"{self.path.name}.reclaim.{self.owner_token}"
                )
                try:
                    # Rename is atomic on the same volume and does not delete
                    # a lock that another contender may have created after the
                    # stale owner was observed.
                    self.path.rename(reclaim_path)
                except (FileNotFoundError, OSError) as reclaim_error:
                    raise TaskLockError(
                        f"task lock changed while reclaiming: {self.path}"
                    ) from reclaim_error
                try:
                    self._handle = self.path.open("x", encoding="utf-8")
                except FileExistsError as acquire_error:
                    raise TaskLockError(
                        f"task lock was acquired by another owner: {self.path}"
                    ) from acquire_error
                try:
                    self._write_metadata()
                except Exception:
                    self._handle.close()
                    self._handle = None
                    try:
                        self.path.unlink()
                    except OSError:
                        pass
                    raise
                return
            raise TaskLockError(f"task lock already exists: {self.path}") from error
        try:
            self._write_metadata()
        except Exception:
            self._handle.close()
            self._handle = None
            try:
                self.path.unlink()
            except OSError:
                pass
            raise

    def refresh(self) -> None:
        if self._handle is None:
            raise TaskLockError("lock is not held")
        metadata = self._read_metadata()
        if metadata.get("owner_token") != self.owner_token:
            raise TaskLockError(f"task lock ownership lost: {self.path}")
        self._write_metadata()

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            self._handle.close()
        finally:
            self._handle = None
        metadata = self._read_metadata()
        if metadata.get("owner_token") != self.owner_token:
            return
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
        fd, temporary = tempfile.mkstemp(
            prefix=self.meta_path.name + ".",
            suffix=".tmp",
            dir=str(self.meta_path.parent),
        )
        temporary_path = Path(temporary)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.meta_path)
        finally:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass

    def _read_metadata(self) -> dict[str, Any]:
        try:
            return json.loads(self.meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _can_reclaim(self, metadata: dict[str, Any]) -> bool:
        try:
            lease_until = float(metadata.get("lease_until", 0))
            pid = int(metadata.get("pid", 0) or 0)
        except (TypeError, ValueError):
            return False
        if lease_until >= time.time() or pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except OSError:
            return True
        return False
