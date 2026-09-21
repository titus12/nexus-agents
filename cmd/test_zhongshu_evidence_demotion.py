from __future__ import annotations

import unittest

from orchestrator.zhongshu_parallel import (
    ANALYST_MAX_EVIDENCE_UPDATES_PER_WORKER,
    merge_analyst_evidence,
)


def _evidence(index: int) -> dict[str, object]:
    return {
        "evidence_id": f"ev-{index:02d}",
        "requirement_id": "req-1",
        "source": "unit-test",
        "source_type": "test",
        "conclusion": f"observation {index}",
        "decision_relevance": "risk",
        "confidence": 0.5,
    }


def _worker(worker_id: str, update_count: int, id_base: int = 0) -> dict[str, object]:
    return {
        "worker_id": worker_id,
        "revision_id": "rev-1",
        "action": "EVIDENCE_PACKET_READY",
        "phase": "ZHONGSHU",
        "requirements": [
            {"requirement_id": "req-1", "statement": "requirement one"}
        ],
        "evidence_updates": [
            _evidence(id_base + i) for i in range(update_count)
        ],
        "task_proposals": [],
        "candidate_items": [],
        "candidate_groups": [],
    }


class AnalystEvidenceDemotionTests(unittest.TestCase):
    def test_overflow_is_demoted_not_fatal(self) -> None:
        with self.assertLogs("review_orchestrator_fsm", level="WARNING") as captured:
            aggregate = merge_analyst_evidence(
                "task-1",
                "rev-1",
                [_worker("worker-01", 10), _worker("worker-02", 6, id_base=100)],
            )
        packet = aggregate["evidence_packet"]
        self.assertEqual(len(packet["evidence_updates"]), 12)
        self.assertTrue(
            any("UPDATES_DEMOTED" in line for line in captured.output)
        )
        deferred = packet["deferred_evidence_updates"]
        self.assertEqual(len(deferred), 4)
        self.assertEqual(
            sorted(item["evidence_id"] for item in deferred),
            ["ev-06", "ev-07", "ev-08", "ev-09"],
        )
        for item in deferred:
            self.assertEqual(item["worker_id"], "worker-01")

    def test_within_budget_round_has_empty_deferred(self) -> None:
        aggregate = merge_analyst_evidence(
            "task-1",
            "rev-1",
            [_worker("worker-01", ANALYST_MAX_EVIDENCE_UPDATES_PER_WORKER), _worker("worker-02", 3, id_base=100)],
        )
        packet = aggregate["evidence_packet"]
        self.assertEqual(packet["deferred_evidence_updates"], [])
        self.assertEqual(len(packet["evidence_updates"]), 9)

    def test_demoted_duplicate_of_kept_evidence_is_dropped(self) -> None:
        worker_one = _worker("worker-01", 7)
        worker_two = _worker("worker-02", 6)
        worker_two["evidence_updates"][-1] = _evidence(6)
        aggregate = merge_analyst_evidence(
            "task-1", "rev-1", [worker_one, worker_two]
        )
        deferred = aggregate["evidence_packet"]["deferred_evidence_updates"]
        self.assertEqual(deferred, [])

    def test_invalid_update_inside_kept_prefix_still_raises(self) -> None:
        worker = _worker("worker-01", 8)
        worker["evidence_updates"][2] = {"conclusion": "no id"}
        with self.assertRaises(ValueError) as ctx:
            merge_analyst_evidence("task-1", "rev-1", [worker])
        self.assertIn("UPDATE_ID_MISSING:2", str(ctx.exception))

    def test_worker_payload_shape_matches_runtime(self) -> None:
        runtime_result = {**_worker("worker-01", 9), "worker_id": "worker-01"}
        with self.assertLogs("review_orchestrator_fsm", level="WARNING"):
            aggregate = merge_analyst_evidence(
                "task-1", "rev-1", [runtime_result]
            )
        packet = aggregate["evidence_packet"]
        self.assertEqual(len(packet["evidence_updates"]), 6)
        self.assertEqual(len(packet["deferred_evidence_updates"]), 3)


if __name__ == "__main__":
    unittest.main()
