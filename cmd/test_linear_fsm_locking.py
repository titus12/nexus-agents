from __future__ import annotations

import json
import time
import unittest
import uuid
from pathlib import Path

from orchestrator.domain.errors import LeaseLostError
from orchestrator.locks import TaskLock, TaskLockError


class TaskLockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).parent / "test-runs" / f"lock-{uuid.uuid4().hex}"
        self.root.mkdir(parents=True, exist_ok=True)

    def test_live_owner_cannot_be_reclaimed(self) -> None:
        first = TaskLock(self.root / "task.lock")
        first.acquire()
        try:
            with self.assertRaises(TaskLockError):
                TaskLock(self.root / "task.lock").acquire()
        finally:
            first.release()

    def test_expired_owner_is_atomically_quarantined_before_reclaim(self) -> None:
        path = self.root / "task.lock"
        meta = path.with_suffix(path.suffix + ".json")
        path.write_text("stale", encoding="utf-8")
        meta.write_text(
            json.dumps(
                {
                    "owner_token": "stale-owner",
                    "pid": 99999999,
                    "lease_until": time.time() - 1,
                }
            ),
            encoding="utf-8",
        )

        lock = TaskLock(path)
        lock.acquire()
        try:
            self.assertTrue(path.exists())
            self.assertEqual(json.loads(meta.read_text(encoding="utf-8"))["owner_token"], lock.owner_token)
            self.assertTrue(list(self.root.glob("task.lock.reclaim.*")))
        finally:
            lock.release()

    def test_refresh_and_release_fail_closed_after_ownership_loss(self) -> None:
        path = self.root / "task.lock"
        lock = TaskLock(path)
        lock.acquire()
        meta = path.with_suffix(path.suffix + ".json")
        value = json.loads(meta.read_text(encoding="utf-8"))
        value["owner_token"] = "different-owner"
        meta.write_text(json.dumps(value), encoding="utf-8")

        with self.assertRaises(LeaseLostError):
            lock.refresh()
        lock.release()
        self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
