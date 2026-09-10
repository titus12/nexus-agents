from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from orchestrator.concurrency import (
    ConcurrencyAdmission,
    ConcurrencyLimits,
    DuplicateLeaseError,
)
from orchestrator.zhongshu_parallel import (
    CriticConflictResolver,
    canonical_plan_hash,
    canonical_plan_json,
)
from orchestrator.zhongshu_parallel import (
    ZhongshuFanoutCoordinator,
    ZhongshuWorkerSpec,
)


class ZhongshuConcurrencyTests(unittest.TestCase):
    def test_global_task_and_phase_limits_are_enforced(self):
        admission = ConcurrencyAdmission(
            ConcurrencyLimits(
                global_max=3,
                per_task_max=2,
                analyst_max=3,
                critic_max=3,
            )
        )
        a = admission.try_acquire("task-a", "ZHONGSHU_ANALYST", "code", "rev-1")
        b = admission.try_acquire("task-a", "ZHONGSHU_ANALYST", "runtime", "rev-1")
        c = admission.try_acquire("task-b", "ZHONGSHU_ANALYST", "code", "rev-1")
        self.assertIsNotNone(a)
        self.assertIsNotNone(b)
        self.assertIsNotNone(c)
        self.assertIsNone(
            admission.try_acquire("task-a", "ZHONGSHU_ANALYST", "risk", "rev-1")
        )
        self.assertEqual(admission.snapshot().global_inflight, 3)

    def test_duplicate_worker_revision_is_rejected(self):
        admission = ConcurrencyAdmission()
        admission.try_acquire("task-a", "ZHONGSHU_CRITIC", "evidence", "rev-1")
        with self.assertRaises(DuplicateLeaseError):
            admission.try_acquire("task-a", "ZHONGSHU_CRITIC", "evidence", "rev-1")

    def test_expired_lease_is_reclaimed(self):
        current = [datetime.now(timezone.utc)]
        admission = ConcurrencyAdmission(
            ConcurrencyLimits(lease_ttl_seconds=1),
            clock=lambda: current[0],
        )
        lease = admission.try_acquire("task-a", "ZHONGSHU_ANALYST", "code", "rev-1")
        self.assertIsNotNone(lease)
        current[0] += timedelta(seconds=2)
        expired = admission.expire()
        self.assertEqual([item.lease_id for item in expired], [lease.lease_id])
        self.assertEqual(admission.snapshot().global_inflight, 0)

    def test_fanout_runs_three_workers_and_releases_leases(self):
        admission = ConcurrencyAdmission(
            ConcurrencyLimits(global_max=3, per_task_max=3, analyst_max=3)
        )
        coordinator = ZhongshuFanoutCoordinator(admission, max_workers=3)
        specs = [
            ZhongshuWorkerSpec(f"analyst-{name}", "ZHONGSHU_ANALYST", name)
            for name in ("code", "runtime", "risk")
        ]
        seen = []
        result = coordinator.run(
            "task-a",
            "rev-1",
            specs,
            lambda spec: seen.append(spec.worker_id) or {"ok": True},
        )
        self.assertEqual(sorted(seen), ["analyst-code", "analyst-risk", "analyst-runtime"])
        self.assertEqual(len(result.completed), 3)
        self.assertEqual(admission.snapshot().global_inflight, 0)

    def test_fanout_does_not_exceed_global_limit(self):
        admission = ConcurrencyAdmission(
            ConcurrencyLimits(global_max=2, per_task_max=3, analyst_max=3)
        )
        coordinator = ZhongshuFanoutCoordinator(admission, max_workers=3)
        specs = [
            ZhongshuWorkerSpec(f"analyst-{name}", "ZHONGSHU_ANALYST", name)
            for name in ("code", "runtime", "risk")
        ]
        result = coordinator.run("task-a", "rev-1", specs, lambda spec: {"ok": True})
        self.assertEqual(len(result.completed), 2)
        self.assertEqual(len(result.rejected), 1)
        self.assertEqual(admission.snapshot().global_inflight, 0)

    def test_failed_worker_retries_up_to_three_times(self):
        admission = ConcurrencyAdmission(
            ConcurrencyLimits(global_max=3, per_task_max=3, analyst_max=3)
        )
        coordinator = ZhongshuFanoutCoordinator(admission, max_workers=3, max_attempts=3)
        attempts = {}

        def execute(spec, attempt):
            attempts[spec.worker_id] = attempt
            if spec.worker_id == "analyst-runtime":
                raise RuntimeError("temporary provider error")
            return {"ok": True}

        result = coordinator.run(
            "task-a",
            "rev-1",
            [
                ZhongshuWorkerSpec("analyst-code", "ZHONGSHU_ANALYST", "code"),
                ZhongshuWorkerSpec("analyst-runtime", "ZHONGSHU_ANALYST", "runtime"),
                ZhongshuWorkerSpec("analyst-risk", "ZHONGSHU_ANALYST", "risk"),
            ],
            execute,
        )
        self.assertEqual(len(result.completed), 2)
        self.assertEqual(len(result.failed), 1)
        self.assertEqual(result.failed[0]["attempts"], 3)
        self.assertEqual(attempts["analyst-runtime"], 3)
        self.assertEqual(admission.snapshot().global_inflight, 0)

    def test_worker_can_succeed_on_third_attempt(self):
        admission = ConcurrencyAdmission(
            ConcurrencyLimits(global_max=1, per_task_max=1, analyst_max=3)
        )
        coordinator = ZhongshuFanoutCoordinator(admission, max_workers=1, max_attempts=3)
        attempts = []

        def execute(spec, attempt):
            attempts.append(attempt)
            if attempt < 3:
                raise RuntimeError("retryable")
            return {"ok": True}

        result = coordinator.run(
            "task-a",
            "rev-1",
            [ZhongshuWorkerSpec("analyst-code", "ZHONGSHU_ANALYST", "code")],
            execute,
        )
        self.assertEqual(attempts, [1, 2, 3])
        self.assertEqual(result.completed[0]["attempt"], 3)
        self.assertEqual(result.failed, ())

    def test_fanout_emits_feishu_status(self):
        admission = ConcurrencyAdmission(
            ConcurrencyLimits(global_max=1, per_task_max=1, analyst_max=3)
        )
        messages = []
        coordinator = ZhongshuFanoutCoordinator(
            admission,
            max_workers=1,
            notify=lambda text, role: messages.append((text, role)),
        )
        coordinator.run(
            "task-a",
            "rev-1",
            [ZhongshuWorkerSpec("analyst-code", "ZHONGSHU_ANALYST", "code")],
            lambda spec: {"ok": True},
        )
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0][1], "gate")
        self.assertIn("analyst-code", messages[0][0])
        self.assertIn("ZHONGSHU_FANIN_COMPLETED", messages[1][0])


class CriticConflictTests(unittest.TestCase):
    def test_plan_hash_is_stable_across_key_order_and_whitespace(self):
        first = {"items": [{"item_id": "item-1"}], "requirements": []}
        second = {"requirements": [], "items": [{"item_id": "item-1"}]}
        self.assertEqual(canonical_plan_hash(first), canonical_plan_hash(second))
        self.assertNotIn(" ", canonical_plan_json(first))

    def test_conflicting_findings_block_freeze_and_request_revision(self):
        result = CriticConflictResolver().aggregate(
            "rev-1",
            "hash-1",
            [
                {
                    "worker_id": "critic-architecture",
                    "plan_revision_id": "rev-1",
                    "plan_hash": "hash-1",
                    "findings": [
                        {
                            "finding_id": "f-a",
                            "category": "dependency",
                            "target": "item-3",
                            "claim": "item-8 precedes item-3",
                            "decision": "REVISE",
                            "severity": "P1",
                            "evidence_strength": "direct_code",
                            "evidence_ids": ["ev-1"],
                        }
                    ],
                },
                {
                    "worker_id": "critic-execution",
                    "plan_revision_id": "rev-1",
                    "plan_hash": "hash-1",
                    "findings": [
                        {
                            "finding_id": "f-b",
                            "category": "dependency",
                            "target": "item-3",
                            "claim": "item-3 is independent",
                            "decision": "APPROVE",
                            "severity": "P1",
                            "evidence_strength": "inference",
                            "evidence_ids": ["ev-2"],
                        }
                    ],
                },
            ],
        )
        self.assertEqual(result.action, "REQUEST_SOLVER_REVISION")
        self.assertEqual(len(result.unresolved_conflicts), 1)
        self.assertNotEqual(result.action, "APPROVE_FREEZE")

    def test_p0_conflict_requires_human_gate(self):
        result = CriticConflictResolver().aggregate(
            "rev-1",
            "hash-1",
            [
                {
                    "worker_id": "critic-a",
                    "plan_revision_id": "rev-1",
                    "plan_hash": "hash-1",
                    "findings": [
                        {
                            "finding_id": "f-a",
                            "category": "safety",
                            "target": "global",
                            "claim": "unsafe",
                            "decision": "REVISE",
                            "severity": "P0",
                            "evidence_strength": "direct_code",
                        }
                    ],
                },
                {
                    "worker_id": "critic-b",
                    "plan_revision_id": "rev-1",
                    "plan_hash": "hash-1",
                    "findings": [
                        {
                            "finding_id": "f-b",
                            "category": "safety",
                            "target": "global",
                            "claim": "safe",
                            "decision": "APPROVE",
                            "severity": "P0",
                            "evidence_strength": "inference",
                        }
                    ],
                },
            ],
        )
        self.assertEqual(result.action, "HUMAN_GATE")

    def test_stale_plan_results_are_ignored(self):
        result = CriticConflictResolver().aggregate(
            "rev-2",
            "hash-2",
            [
                {
                    "worker_id": "critic-old",
                    "plan_revision_id": "rev-1",
                    "plan_hash": "hash-1",
                    "findings": [{"finding_id": "old", "claim": "bad"}],
                }
            ],
        )
        self.assertEqual(result.findings, ())
        self.assertEqual(result.action, "REQUEST_SOLVER_REVISION")

    def test_deferred_decision_is_serialized_as_terminal_finding_status(self):
        result = CriticConflictResolver().aggregate(
            "rev-1",
            "hash-1",
            [{
                "worker_id": "critic-a",
                "plan_revision_id": "rev-1",
                "plan_hash": "hash-1",
                "findings": [{
                    "finding_id": "f-deferred",
                    "category": "scope",
                    "target": "global",
                    "claim": "optional follow-up",
                    "decision": "DEFERRED",
                    "severity": "P2",
                }],
            }],
        )
        self.assertEqual(result.action, "APPROVE_FREEZE")
        self.assertEqual(result.to_dict()["findings"][0]["status"], "DEFERRED")

    def test_missing_plan_hash_is_not_accepted_by_fanin(self):
        result = CriticConflictResolver().aggregate(
            "rev-1",
            "hash-1",
            [{
                "worker_id": "critic-a",
                "plan_revision_id": "rev-1",
                "findings": [],
            }],
        )
        self.assertEqual(result.action, "REQUEST_SOLVER_REVISION")


if __name__ == "__main__":
    unittest.main()
