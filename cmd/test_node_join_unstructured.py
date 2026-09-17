from __future__ import annotations

import unittest

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
