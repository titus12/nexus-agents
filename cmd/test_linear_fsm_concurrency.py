from __future__ import annotations

from datetime import datetime, timezone
import unittest

from orchestrator.runtime.concurrency import ConcurrencyLimits, RuntimeConcurrencyAdmission
from orchestrator.runtime.ports import AdmissionKey


class LinearConcurrencyTests(unittest.TestCase):
    def test_global_and_phase_limits_are_enforced(self) -> None:
        admission = RuntimeConcurrencyAdmission(
            ConcurrencyLimits(global_max=1, per_task_max=1, analyst_max=1, critic_max=1),
        )
        first_key = AdmissionKey("task-1", "ZHONGSHU_ANALYST", "w-1", "r-1", "issue/agent")
        second_key = AdmissionKey("task-2", "ZHONGSHU_ANALYST", "w-2", "r-1", "issue/agent")

        first = admission.try_acquire(first_key)
        self.assertIsNotNone(first)
        self.assertIsNone(admission.try_acquire(second_key, log_rejection=False))
        self.assertEqual(admission.snapshot().global_inflight, 1)
        self.assertTrue(admission.release(first.lease_id))  # type: ignore[union-attr]
        self.assertIsNotNone(admission.try_acquire(second_key))

    def test_duplicate_logical_worker_is_rejected_and_refresh_is_fail_closed(self) -> None:
        admission = RuntimeConcurrencyAdmission(ConcurrencyLimits())
        key = AdmissionKey("task-1", "MENXIA", "w-1", "revision-1")
        lease = admission.try_acquire(key)
        self.assertIsNotNone(lease)
        with self.assertRaisesRegex(RuntimeError, "active lease"):
            admission.try_acquire(key)
        self.assertTrue(admission.refresh(lease.lease_id))  # type: ignore[union-attr]
        self.assertTrue(admission.release(lease.lease_id))  # type: ignore[union-attr]
        self.assertFalse(admission.refresh(lease.lease_id))  # type: ignore[union-attr]

    def test_expiration_releases_capacity(self) -> None:
        now = [datetime(2026, 9, 10, tzinfo=timezone.utc)]
        admission = RuntimeConcurrencyAdmission(
            ConcurrencyLimits(lease_ttl_seconds=1), clock=lambda: now[0]
        )
        key = AdmissionKey("task-1", "ZHONGSHU_ANALYST", "w-1", "r-1")
        lease = admission.try_acquire(key)
        now[0] = datetime(2026, 9, 10, 0, 0, 2, tzinfo=timezone.utc)
        self.assertEqual(admission.expire(), [lease])
        self.assertEqual(admission.snapshot().global_inflight, 0)


if __name__ == "__main__":
    unittest.main()
