from __future__ import annotations

import unittest

from orchestrator.prompt_bundle import remote_result_filename
from orchestrator.runtime.compat_effects import MulticaTransportAdapter
from orchestrator.runtime.concurrency import RuntimeConcurrencyAdmission
from orchestrator.runtime.ports import AdmissionKey, AgentDispatchRequest, PollRequest
from orchestrator.transport.external import DispatchReceipt as ExternalDispatchReceipt


class _FakeLegacyAdapter:
    def __init__(self) -> None:
        self.dispatched = None
        self.status_request = None

    def dispatch(self, request):
        self.dispatched = request
        return ExternalDispatchReceipt(
            operation_id="op-1",
            external_message_id="trigger-1",
            request_id="provider-request-1",
        )

    def get_run_status(self, request):
        self.status_request = request
        return "COMPLETED"


class DispatchCorrelationTests(unittest.TestCase):
    def test_dispatch_receipt_external_id_is_bound_before_status_poll(self):
        legacy = _FakeLegacyAdapter()
        transport = MulticaTransportAdapter(legacy)
        request = AgentDispatchRequest(
            task_id="task-1",
            issue_id="SER-1",
            request_id="request-1",
            agent_id="agent-1",
            role="review-analyst",
            phase="ZHONGSHU",
            prompt_ref="bundle-1",
            idempotency_key="idem-1",
            sent_after="2026-09-10T06:00:00+00:00",
        )

        receipt = transport.dispatch(request)
        status = transport.status(
            PollRequest("task-1", receipt.request_id, receipt.operation_id)
        )

        self.assertEqual(status.status, "COMPLETED")
        self.assertEqual(legacy.dispatched.sent_after, "2026-09-10T06:00:00+00:00")
        self.assertEqual(legacy.status_request.dispatch_external_message_id, "trigger-1")
        self.assertEqual(
            transport._requests[request.request_id].dispatch_external_message_id,
            "trigger-1",
        )


class RemoteResultIsolationTests(unittest.TestCase):
    def test_remote_result_filename_is_request_unique(self):
        first = remote_result_filename("task-1:worker-01:attempt-1")
        second = remote_result_filename("task-1:worker-01:attempt-2")
        self.assertNotEqual(first, second)
        self.assertEqual(first, "result_task-1_worker-01_attempt-1.json")

    def test_same_external_target_cannot_hold_two_live_leases(self):
        admission = RuntimeConcurrencyAdmission()
        first_key = AdmissionKey(
            task_id="task-1",
            phase="ZHONGSHU_ANALYST",
            worker_id="worker-01",
            revision_id="rev-1",
            external_target_key="SER-1:agent-1",
        )
        second_key = AdmissionKey(
            task_id="task-1",
            phase="ZHONGSHU_ANALYST",
            worker_id="worker-02",
            revision_id="rev-2",
            external_target_key="SER-1:agent-1",
        )

        first = admission.try_acquire(first_key)
        self.assertIsNotNone(first)
        self.assertIsNone(admission.try_acquire(second_key))
        admission.release(first.lease_id)
        self.assertIsNotNone(admission.try_acquire(second_key))


if __name__ == "__main__":
    unittest.main()
