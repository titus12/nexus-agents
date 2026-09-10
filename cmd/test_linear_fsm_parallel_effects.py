from __future__ import annotations

import tempfile
import unittest

from orchestrator.domain.decisions import EffectRequest
from orchestrator.runtime.agent_effects import AgentWorkerRunner
from orchestrator.runtime.compat_effects import FileArtifactStore
from orchestrator.runtime.concurrency import ConcurrencyLimits, RuntimeConcurrencyAdmission
from orchestrator.runtime.ports import DispatchReceipt, RemoteRunStatus, PollRequest
from orchestrator.transport import RawTransportReply


class _Transport:
    def __init__(self, status: str = "COMPLETED") -> None:
        self.status_value = status
        self.dispatched = []
        self.polled = 0

    def dispatch(self, request):
        self.dispatched.append(request)
        return DispatchReceipt("op-1", "trigger-1", True)

    def status(self, request: PollRequest):
        return RemoteRunStatus(request.request_id, request.operation_id, self.status_value)

    def poll(self, request: PollRequest):
        self.polled += 1
        return (
            RawTransportReply(
                author_id="review-analyst",
                external_message_id="reply-1",
                request_id=request.request_id,
                payload={"action": "READY_FOR_SOLVER"},
                received_at="2026-09-10T00:00:00Z",
                source="test",
            ),
        )

    def lookup(self, _operation_id):
        return None


class _RecoveredTransport(_Transport):
    def __init__(self) -> None:
        super().__init__()
        self.lookups = 0

    def find_existing(self, request):
        self.lookups += 1
        return DispatchReceipt("op-existing", "trigger-existing", True, "provider-request-1")


def _request() -> EffectRequest:
    return EffectRequest(
        effect_id="effect-1",
        effect_type="agent_dispatch",
        task_id="task-1",
        idempotency_key="idem-1",
        payload={
            "issue_id": "issue-1",
            "request_id": "request-1",
            "agent_id": "review-analyst",
            "role": "review-analyst",
            "phase": "ZHONGSHU",
            "target_state": "ZHONGSHU_ANALYST",
            "sequence": 1,
        },
    )


class LinearParallelEffectTests(unittest.TestCase):
    def test_restart_reuses_existing_remote_request_before_dispatch(self):
        transport = _RecoveredTransport()
        runner = AgentWorkerRunner(transport, timeout_seconds=1)

        outcome = runner.run_once(_request())

        self.assertEqual(outcome.status, "SUCCEEDED")
        self.assertEqual(transport.lookups, 1)
        self.assertEqual(transport.dispatched, [])
        self.assertEqual(outcome.request_id, "provider-request-1")

    def test_completed_agent_is_normalized_and_archived_without_context_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            transport = _Transport()
            runner = AgentWorkerRunner(
                transport,
                admission=RuntimeConcurrencyAdmission(
                    ConcurrencyLimits(global_max=1, per_task_max=1, analyst_max=1, critic_max=1)
                ),
                artifacts=FileArtifactStore(directory),
            )
            outcome = runner.run_once(_request())

        self.assertEqual(outcome.status, "SUCCEEDED")
        self.assertEqual(outcome.event_name, "READY_FOR_SOLVER")
        self.assertEqual(outcome.event_payload["result_artifact_id"][:1], directory[:1])
        self.assertEqual(transport.polled, 1)

    def test_remote_failed_is_terminal_even_without_polling_or_reply(self):
        transport = _Transport("FAILED")
        runner = AgentWorkerRunner(transport, timeout_seconds=1)

        outcome = runner.run_once(_request())

        self.assertEqual(outcome.status, "FAILED")
        self.assertEqual(outcome.event_name, "FAIL")
        self.assertIsNotNone(outcome.failure)
        self.assertEqual(outcome.failure.error_code, "REMOTE_RUN_FAILED")
        self.assertEqual(transport.polled, 0)


if __name__ == "__main__":
    unittest.main()
