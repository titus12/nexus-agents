from __future__ import annotations

import unittest

from orchestrator.domain.errors import FailureRecord, is_reply_failure
from orchestrator.runtime.agent_effects import AgentNodeJoiner
from orchestrator.runtime.nodes import WorkerResult


def _result(
    worker_id: str,
    action: str | None,
    status: str = "SUCCEEDED",
    extra: dict | None = None,
) -> WorkerResult:
    payload = {"action": action, **(extra or {})} if action is not None else None
    return WorkerResult(worker_id, status, None, failure=None, result_payload=payload)


class UnstructuredReplyFanInTest(unittest.TestCase):
    """``__UNSTRUCTURED_REPLY__`` must not pollute the fan-in action set."""

    @staticmethod
    def _joiner(state: str = "MENXIA_ITEM_ANALYST") -> AgentNodeJoiner:
        return AgentNodeJoiner(
            "node-1",
            task_id="task-1",
            state=state,
            sequence=1,
        )

    def test_unstructured_reply_becomes_retryable_worker_failure(self) -> None:
        results = (
            _result("worker-01", "__UNSTRUCTURED_REPLY__"),
            _result("worker-02", "EVIDENCE_PACKET_READY"),
            _result("worker-03", "EVIDENCE_PACKET_READY"),
        )
        node = self._joiner("ZHONGSHU_ANALYST").join(results)
        self.assertEqual(node.status, "FAILED")
        self.assertIsNotNone(node.failure)
        assert node.failure is not None
        self.assertEqual(node.failure.error_code, "AGENT_REPLY_UNSTRUCTURED")
        self.assertTrue(node.failure.retryable)
        self.assertEqual(node.failure.worker_id, "worker-01")

    def test_oversized_output_placeholder_gets_its_own_error_code(self) -> None:
        results = (
            _result(
                "worker-01",
                "__UNSTRUCTURED_REPLY__",
                extra={
                    "raw_reply": (
                        "This task completed, but its output was too large "
                        "to post safely. The raw output was not posted."
                    ),
                },
            ),
            _result("worker-02", "EVIDENCE_PACKET_READY"),
        )
        node = self._joiner("ZHONGSHU_ANALYST").join(results)

        self.assertEqual(node.status, "FAILED")
        assert node.failure is not None
        self.assertEqual(node.failure.error_code, "AGENT_REPLY_OUTPUT_OVERFLOW")
        self.assertTrue(node.failure.retryable)
        self.assertIn("too large", node.failure.message)
        self.assertIn("result file", node.failure.message)
        # The overflow code rides the reply budget like every unusable reply.
        self.assertTrue(is_reply_failure("AGENT_REPLY_OUTPUT_OVERFLOW"))

    def test_wave_failure_carries_every_workers_rejection(self) -> None:
        results = (
            _result(
                "worker-01",
                "__UNSTRUCTURED_REPLY__",
                extra={"raw_reply": "not json at all"},
            ),
            _result(
                "worker-02",
                "__CONTRACT_REJECTED__",
                extra={"contract_rejection": "ev-003 quote is 78 lines"},
            ),
            _result("worker-03", "EVIDENCE_PACKET_READY"),
        )
        node = self._joiner("ZHONGSHU_ANALYST").join(results)

        self.assertEqual(node.status, "FAILED")
        assert node.failure is not None
        rejections = dict(node.failure.worker_rejections)
        self.assertIn("worker-01", rejections)
        self.assertIn("worker-02", rejections)
        self.assertIn("78 lines", rejections["worker-02"])
        self.assertNotIn("worker-03", rejections)

    def test_worker_failure_record_carries_the_wave_ledger(self) -> None:
        failure = FailureRecord(
            failure_id="f-1",
            stage="agent_result",
            owner_component="agent_dispatch",
            task_id="task-1",
            state="ZHONGSHU_ANALYST",
            sequence=1,
            node_run_id=None,
            worker_id="worker-01",
            effect_id=None,
            error_code="AGENT_TIMEOUT",
            retryable=True,
            message="no result",
            cause_type="Timeout",
        )
        results = (
            WorkerResult("worker-01", "FAILED", None, failure=failure, result_payload=None),
            _result("worker-02", "EVIDENCE_PACKET_READY"),
        )
        node = self._joiner("ZHONGSHU_ANALYST").join(results)

        self.assertEqual(node.status, "FAILED")
        assert node.failure is not None
        self.assertEqual(node.failure.error_code, "AGENT_TIMEOUT")
        self.assertEqual(
            dict(node.failure.worker_rejections),
            {"worker-01": "AGENT_TIMEOUT: no result"},
        )

    def test_one_escalated_worker_does_not_kill_the_wave(self) -> None:
        # task-20260929-2f77e6: a quote-frustrated analyst returned BLOCKED
        # beside two valid evidence peers and the wave died non-retryable on
        # NODE_ACTION_CONFLICT.  Escalation-max routes the wave up instead.
        results = (
            _result("worker-01", "BLOCKED"),
            _result("worker-02", "EVIDENCE_PACKET_READY"),
            _result("worker-03", "EVIDENCE_PACKET_READY"),
        )
        node = self._joiner("ZHONGSHU_ANALYST").join(results)

        self.assertEqual(node.status, "SUCCEEDED")
        assert node.aggregate is not None
        self.assertEqual(node.aggregate.get("action"), "BLOCKED")

    def test_human_gate_also_wins_the_wave(self) -> None:
        results = (
            _result("worker-01", "EVIDENCE_PACKET_READY"),
            _result("worker-02", "HUMAN_GATE"),
        )
        node = self._joiner("ZHONGSHU_ANALYST").join(results)

        self.assertEqual(node.status, "SUCCEEDED")
        assert node.aggregate is not None
        self.assertEqual(node.aggregate.get("action"), "HUMAN_GATE")

    def test_contract_rejected_reply_keeps_its_own_error_code(self) -> None:
        results = (
            _result(
                "worker-01",
                "__CONTRACT_REJECTED__",
                extra={"contract_rejection": "structured result shape invalid: X"},
            ),
            _result("worker-02", "EVIDENCE_PACKET_READY"),
        )
        node = self._joiner("ZHONGSHU_ANALYST").join(results)

        self.assertEqual(node.status, "FAILED")
        assert node.failure is not None
        self.assertEqual(node.failure.error_code, "AGENT_REPLY_CONTRACT_REJECTED")
        self.assertTrue(node.failure.retryable)
        self.assertIn("structured result shape invalid: X", node.failure.message)

    def test_conflicting_real_actions_remain_non_retryable(self) -> None:
        results = (
            _result("worker-01", "EVIDENCE_PACKET_READY"),
            _result("worker-02", "REQUIREMENT_CONTRACT_READY"),
        )
        node = self._joiner().join(results)
        self.assertEqual(node.status, "FAILED")
        self.assertIsNotNone(node.failure)
        assert node.failure is not None
        self.assertEqual(node.failure.error_code, "NODE_ACTION_CONFLICT")
        self.assertFalse(node.failure.retryable)

    def test_matching_actions_still_succeed(self) -> None:
        results = (
            _result("worker-01", "EVIDENCE_PACKET_READY"),
            _result("worker-02", "EVIDENCE_PACKET_READY"),
        )
        node = self._joiner().join(results)
        self.assertEqual(node.status, "SUCCEEDED")


if __name__ == "__main__":
    unittest.main()
