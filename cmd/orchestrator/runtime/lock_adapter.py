"""Adapters between the task-aware runtime lock port and the legacy lock."""

from __future__ import annotations

from ..domain.errors import InvariantViolation
from ..locks import TaskLock


class TaskLockAdapter:
    """Bind one legacy ``TaskLock`` instance to exactly one task id."""

    def __init__(self, task_id: str, lock: TaskLock) -> None:
        if not isinstance(task_id, str) or not task_id:
            raise InvariantViolation("task lock adapter requires a task_id")
        self.task_id = task_id
        self._lock = lock

    def acquire(self, task_id: str) -> None:
        self._check_task(task_id)
        self._lock.acquire()

    def refresh(self, task_id: str) -> None:
        self._check_task(task_id)
        self._lock.refresh()

    def release(self, task_id: str) -> None:
        self._check_task(task_id)
        self._lock.release()

    def _check_task(self, task_id: str) -> None:
        if task_id != self.task_id:
            raise InvariantViolation(
                f"lock task mismatch: expected {self.task_id!r}, got {task_id!r}"
            )


__all__ = ["TaskLockAdapter"]
