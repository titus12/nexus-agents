from __future__ import annotations

import importlib
import unittest


def _load_failure_record():
    try:
        module = importlib.import_module("orchestrator.domain.errors")
        return module.FailureRecord
    except (ImportError, AttributeError) as error:
        raise AssertionError(
            "failure contract is not implemented: "
            f"orchestrator.domain.errors.FailureRecord: {error}"
        ) from error


class FailureAttributionContractTests(unittest.TestCase):
    def test_failure_contains_required_correlation(self):
        FailureRecord = _load_failure_record()

        failure = FailureRecord(
            failure_id="failure-1",
            stage="node",
            owner_component="zhongshu_analyst",
            task_id="task-1",
            state="ZHONGSHU_ANALYST",
            sequence=4,
            node_run_id="node-1",
            worker_id="analyst-2",
            effect_id=None,
            error_code="WORKER_TIMEOUT",
            retryable=True,
            message="worker timed out",
            cause_type="WorkerTimeoutError",
        )

        self.assertTrue(failure.failure_id)
        self.assertEqual(failure.stage, "node")
        self.assertEqual(failure.owner_component, "zhongshu_analyst")
        self.assertEqual(failure.task_id, "task-1")
        self.assertEqual(failure.state, "ZHONGSHU_ANALYST")
        self.assertEqual(failure.sequence, 4)
        self.assertEqual(failure.node_run_id, "node-1")
        self.assertEqual(failure.worker_id, "analyst-2")
        self.assertEqual(failure.error_code, "WORKER_TIMEOUT")

    def test_failure_can_correlate_an_effect_without_worker(self):
        FailureRecord = _load_failure_record()

        failure = FailureRecord(
            failure_id="failure-2",
            stage="effect",
            owner_component="agent_transport",
            task_id="task-1",
            state="ZHONGSHU_SOLVER",
            sequence=5,
            node_run_id=None,
            worker_id=None,
            effect_id="effect-1",
            error_code="TRANSPORT_ERROR",
            retryable=False,
            message="dispatch failed",
            cause_type="TransportError",
        )

        self.assertEqual(failure.effect_id, "effect-1")
        self.assertIsNone(failure.worker_id)
        self.assertIsNone(failure.node_run_id)

    def test_typed_error_preserves_failure_record(self):
        module = importlib.import_module("orchestrator.domain.errors")
        failure = module.FailureRecord(
            failure_id="failure-3",
            stage="transport",
            owner_component="multica",
            task_id="task-1",
            state="ZHONGSHU_SOLVER",
            sequence=5,
            node_run_id=None,
            worker_id=None,
            effect_id="effect-1",
            error_code="TRANSPORT_ERROR",
            retryable=True,
            message="dispatch failed",
            cause_type="TransportError",
        )

        error = module.TransportError("dispatch failed", failure)

        self.assertIs(error.failure, failure)
        self.assertEqual(error.error_code, "TRANSPORT_ERROR")


if __name__ == "__main__":
    unittest.main()
