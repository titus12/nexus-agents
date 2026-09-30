"""A terminal remote run must not be re-polled for the whole dispatch window."""

from __future__ import annotations

import unittest

from orchestrator.domain.decisions import EffectRequest
from orchestrator.runtime.agent_effects import (
    COMPLETED_RESULT_GRACE_SECONDS,
    AgentWorkerRunner,
)
from orchestrator.runtime.ports import (
    AgentDispatchRequest,
    DispatchReceipt,
    PollRequest,
    RemoteRunStatus,
)


class _CompletedWithoutResultTransport:
    """Run is COMPLETED, but no correlated reply ever shows up."""

    def __init__(self, reply_on_poll: int | None = None) -> None:
        self.poll_calls = 0
        self._reply_on_poll = reply_on_poll

    def dispatch(self, request: AgentDispatchRequest) -> DispatchReceipt:
        return DispatchReceipt(
            operation_id=f"op-{request.request_id}",
            external_message_id="msg-1",
            confirmed=True,
            request_id=request.request_id,
        )

    def find_existing(self, request: AgentDispatchRequest) -> DispatchReceipt | None:
        return None

    def status(self, request: PollRequest) -> RemoteRunStatus:
        return RemoteRunStatus(
            request_id=request.request_id,
            operation_id=request.operation_id,
            status="COMPLETED",
        )

    def poll(self, request: PollRequest) -> tuple[object, ...]:
        self.poll_calls += 1
        if self._reply_on_poll is not None and self.poll_calls >= self._reply_on_poll:
            return ("reply",)
        return ()


class _TenSecondClock:
    """Every reading advances time by ten seconds."""

    def __init__(self) -> None:
        self._now = 0.0

    def __call__(self) -> float:
        self._now += 10.0
        return self._now


def _effect_request() -> EffectRequest:
    return EffectRequest(
        effect_id="dispatch:task-1:ZHONGSHU_SOLVER:1",
        effect_type="agent_dispatch",
        task_id="task-1",
        idempotency_key="task-1:ZHONGSHU_SOLVER:1",
        payload={
            "issue_id": "issue-1",
            "request_id": "task-1:ZHONGSHU_SOLVER:1",
            "agent_id": "solver-agent",
            "role": "review-solver",
            "phase": "ZHONGSHU",
            "target_state": "ZHONGSHU_SOLVER",
            "prompt_ref": "prompt",
        },
    )


def _runner(
    transport: object, **kwargs: float
) -> AgentWorkerRunner:
    return AgentWorkerRunner(
        transport,  # type: ignore[arg-type]
        poll_interval=0.0,
        max_polls=1000,
        timeout_seconds=2000.0,
        clock=_TenSecondClock(),
        **kwargs,
    )


class CompletedResultGraceTests(unittest.TestCase):
    def test_missing_result_fails_after_the_grace_not_the_dispatch_window(self) -> None:
        transport = _CompletedWithoutResultTransport()

        outcome = _runner(transport).run_once(_effect_request())

        self.assertEqual(outcome.status, "FAILED")
        self.assertEqual(outcome.failure.error_code, "AGENT_RESULT_MISSING")
        self.assertTrue(outcome.failure.retryable)
        # 60s grace at 10s per clock reading: a handful of reads, not the
        # ~200 that the 2000s dispatch window would allow.
        self.assertLessEqual(transport.poll_calls, 10)

    def test_a_larger_grace_keeps_reading_longer(self) -> None:
        short = _CompletedWithoutResultTransport()
        long = _CompletedWithoutResultTransport()

        _runner(short, completed_result_grace_seconds=30.0).run_once(_effect_request())
        _runner(long, completed_result_grace_seconds=600.0).run_once(_effect_request())

        self.assertGreater(long.poll_calls, short.poll_calls)

    def test_zero_grace_reads_the_result_exactly_once(self) -> None:
        transport = _CompletedWithoutResultTransport()

        outcome = _runner(
            transport, completed_result_grace_seconds=0.0
        ).run_once(_effect_request())

        self.assertEqual(outcome.failure.error_code, "AGENT_RESULT_MISSING")
        self.assertEqual(transport.poll_calls, 1)

    def test_lagging_reply_inside_the_grace_is_still_read(self) -> None:
        transport = _CompletedWithoutResultTransport(reply_on_poll=3)
        runner = _runner(transport)

        replies = runner._poll_completed(
            PollRequest("task-1", "req-1", "op-1"),
            deadline=runner._clock() + COMPLETED_RESULT_GRACE_SECONDS,
        )

        self.assertEqual(replies, ("reply",))
        self.assertEqual(transport.poll_calls, 3)


if __name__ == "__main__":
    unittest.main()
