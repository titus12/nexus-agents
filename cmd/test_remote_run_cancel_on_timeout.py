from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from orchestrator.adapters import MulticaCliAdapter
from orchestrator.domain.decisions import EffectRequest
from orchestrator.runtime.agent_effects import AgentWorkerRunner
from orchestrator.runtime.ports import (
    AgentDispatchRequest,
    DispatchReceipt,
    PollRequest,
    RemoteRunStatus,
)
from orchestrator.transport.external import AgentRequest
from orchestrator.transport.replies import RawTransportReply


class _StalledRunTransport:
    """Dispatch succeeds, the remote run never reaches a terminal state."""

    def __init__(self) -> None:
        self.cancelled: list[PollRequest] = []

    def dispatch(self, request: AgentDispatchRequest) -> DispatchReceipt:
        return DispatchReceipt(
            operation_id=f"op-{request.request_id}",
            external_message_id="msg-1",
            confirmed=True,
            request_id=request.request_id,
            issue_id=request.issue_id,
        )

    def find_existing(self, request: AgentDispatchRequest) -> DispatchReceipt | None:
        return None

    def poll(self, request: PollRequest) -> tuple[RawTransportReply, ...]:
        return ()

    def status(self, request: PollRequest) -> RemoteRunStatus:
        return RemoteRunStatus(
            request_id=request.request_id,
            operation_id=request.operation_id,
            status="RUNNING",
        )

    def lookup(self, operation_id: str) -> DispatchReceipt | None:
        return None

    def cancel_runs(self, request: PollRequest) -> list[str]:
        self.cancelled.append(request)
        return ["run-stale-1"]


class _NoCancelSupportTransport(_StalledRunTransport):
    """Same as above but declares no cancel capability."""

    cancel_runs = None  # type: ignore[assignment]


def _effect_request() -> EffectRequest:
    return EffectRequest(
        effect_id="dispatch:task-1:ZHONGSHU_ANALYST:1",
        effect_type="agent_dispatch",
        task_id="task-1",
        idempotency_key="task-1:ZHONGSHU_ANALYST:1",
        payload={
            "issue_id": "issue-1",
            "request_id": "task-1:ZHONGSHU_ANALYST:1",
            "agent_id": "analyst-agent",
            "role": "review-analyst",
            "phase": "ZHONGSHU",
            "target_state": "ZHONGSHU_ANALYST",
            "prompt_ref": "prompt",
        },
    )


class _SteppedClock:
    """Deterministic clock: scripted values, then +10s per call."""

    def __init__(self, values: list[float]) -> None:
        self._values = iter(values)
        self._current = 0.0

    def __call__(self) -> float:
        try:
            self._current = next(self._values)
        except StopIteration:
            self._current += 10.0
        return self._current


class RemoteRunCancelOnTimeoutTests(unittest.TestCase):
    """When the runner gives up on a remote run, the stale run must be
    cancelled so the FSM retry's new run is not merged/queued behind it."""

    def _effects(self, transport: object, values: list[float]) -> AgentEffects:
        return AgentWorkerRunner(
            transport,  # type: ignore[arg-type]
            poll_interval=0.0,
            max_polls=10,
            timeout_seconds=1.0,
            clock=_SteppedClock(values),
        )

    def test_timeout_cancels_correlated_remote_run(self):
        transport = _StalledRunTransport()
        effects = self._effects(transport, [0.0, 0.5, 0.6, 2.0])

        outcome = effects.run_once(_effect_request())

        self.assertEqual(outcome.status, "FAILED")
        self.assertEqual(outcome.event_name, "FAIL")
        self.assertEqual(len(transport.cancelled), 1)
        self.assertEqual(
            transport.cancelled[0].request_id,
            "task-1:ZHONGSHU_ANALYST:1",
        )

    def test_correlation_unknown_path_also_cancels(self):
        transport = _StalledRunTransport()

        def unknown_status(request: PollRequest) -> RemoteRunStatus:
            return RemoteRunStatus(
                request_id=request.request_id,
                operation_id=request.operation_id,
                status="UNKNOWN",
            )

        transport.status = unknown_status  # type: ignore[method-assign]
        effects = self._effects(transport, [0.0, 0.5, 0.6, 2.0])

        outcome = effects.run_once(_effect_request())

        self.assertEqual(outcome.status, "FAILED")
        self.assertEqual(len(transport.cancelled), 1)

    def test_transport_without_cancel_support_is_tolerated(self):
        effects = self._effects(
            _NoCancelSupportTransport(), [0.0, 0.5, 0.6, 2.0]
        )

        outcome = effects.run_once(_effect_request())

        self.assertEqual(outcome.status, "FAILED")

    def test_cancel_errors_do_not_mask_the_original_failure(self):
        transport = _StalledRunTransport()

        def broken_cancel(request: PollRequest) -> list[str]:
            raise RuntimeError("cancel channel exploded")

        transport.cancel_runs = broken_cancel  # type: ignore[method-assign]
        effects = self._effects(transport, [0.0, 0.5, 0.6, 2.0])

        outcome = effects.run_once(_effect_request())

        self.assertEqual(outcome.status, "FAILED")
        self.assertEqual(outcome.event_name, "FAIL")


class CancelStaleRunsFilteringTests(unittest.TestCase):
    """Only non-terminal correlated runs are cancelled; terminal ones stay."""

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._temporary.cleanup)
        self.log_dir = Path(self._temporary.name)

    def _adapter(self) -> MulticaCliAdapter:
        return MulticaCliAdapter(self.log_dir)

    @staticmethod
    def _request(**overrides) -> AgentRequest:
        values = {
            "task_id": "task-1",
            "issue_id": "issue-1",
            "request_id": "task-1:ZHONGSHU_ANALYST:1",
            "agent_id": "analyst-agent",
            "role": "review-analyst",
            "phase": "ZHONGSHU",
            "prompt": "analyse",
            "idempotency_key": "task-1:ZHONGSHU_ANALYST:1",
            "dispatch_external_message_id": "cmt-1",
            "sent_after": "2026-09-20T11:00:00+00:00",
        }
        values.update(overrides)
        return AgentRequest(**values)

    def test_only_non_terminal_runs_are_cancelled(self):
        adapter = self._adapter()
        runs = [
            {
                "id": "run-running",
                "agent_id": "analyst-agent",
                "status": "running",
                "trigger_comment_id": "cmt-1",
                "created_at": "2026-09-20T11:00:01Z",
            },
            {
                "id": "run-completed",
                "agent_id": "analyst-agent",
                "status": "completed",
                "trigger_comment_id": "cmt-1",
                "created_at": "2026-09-20T11:00:01Z",
            },
            {
                "id": "run-failed",
                "agent_id": "analyst-agent",
                "status": "cancelled",
                "trigger_comment_id": "cmt-1",
                "created_at": "2026-09-20T11:00:01Z",
            },
        ]
        cancel_calls: list[list[str]] = []

        def fake_run(*args: str, cwd: Path | None = None) -> dict:
            cancel_calls.append(list(args))
            return {"id": args[2]}

        with (
            patch.object(adapter, "_run_with_read_retry", return_value=runs),
            patch.object(adapter, "_run", side_effect=fake_run),
        ):
            cancelled = adapter.cancel_stale_runs(self._request())

        self.assertEqual(cancelled, ["run-running"])
        self.assertEqual(len(cancel_calls), 1)
        self.assertEqual(
            cancel_calls[0],
            [
                "issue", "cancel-task", "run-running",
                "--issue", "issue-1", "--output", "json",
            ],
        )

    def test_cancel_cli_failure_is_swallowed_per_run(self):
        adapter = self._adapter()
        runs = [
            {
                "id": "run-running",
                "agent_id": "analyst-agent",
                "status": "running",
                "trigger_comment_id": "cmt-1",
                "created_at": "2026-09-20T11:00:01Z",
            }
        ]

        def failing_run(*args: str, cwd: Path | None = None) -> dict:
            raise RuntimeError("multica CLI failed")

        with (
            patch.object(adapter, "_run_with_read_retry", return_value=runs),
            patch.object(adapter, "_run", side_effect=failing_run),
        ):
            cancelled = adapter.cancel_stale_runs(self._request())

        self.assertEqual(cancelled, [])

    def test_lookup_failure_returns_empty_list(self):
        adapter = self._adapter()

        with patch.object(
            adapter,
            "_run_with_read_retry",
            side_effect=RuntimeError("temporary read failure"),
        ):
            cancelled = adapter.cancel_stale_runs(self._request())

        self.assertEqual(cancelled, [])

    def test_no_correlated_runs_cancels_nothing(self):
        adapter = self._adapter()

        with patch.object(adapter, "_run_with_read_retry", return_value=[]):
            cancelled = adapter.cancel_stale_runs(self._request())

        self.assertEqual(cancelled, [])


class CancelStaleLiveRunsTests(unittest.TestCase):
    """Dispatch must cancel any live run for the same agent before posting the
    trigger comment.  A bare run (manual assignment, or the direct run fired
    by our own assign step) can never see the payload comment and Multica
    would merge the fresh comment run behind it
    (live incidents task-20260920-ac132f and task-20260920-599525)."""

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._temporary.cleanup)
        self.log_dir = Path(self._temporary.name)

    def _adapter(self) -> MulticaCliAdapter:
        return MulticaCliAdapter(self.log_dir)

    @staticmethod
    def _request(**overrides) -> AgentRequest:
        values = {
            "task_id": "task-1",
            "issue_id": "issue-1",
            "request_id": "task-1:ZHONGSHU_ANALYST:3",
            "agent_id": "analyst-agent",
            "role": "review-analyst",
            "phase": "ZHONGSHU",
            "prompt": "analyse",
            "idempotency_key": "task-1:ZHONGSHU_ANALYST:3",
            "sent_after": "2026-09-20T11:52:41+00:00",
        }
        values.update(overrides)
        return AgentRequest(**values)

    def test_live_runs_for_this_agent_are_cancelled(self):
        adapter = self._adapter()
        runs = [
            {
                "id": "run-stray",
                "agent_id": "analyst-agent",
                "kind": "direct",
                "status": "running",
                "created_at": "2026-09-20T11:52:41Z",
            },
            {
                "id": "run-own-assign",
                "agent_id": "analyst-agent",
                "kind": "direct",
                "status": "running",
            },
            {
                "id": "run-foreign-agent",
                "agent_id": "critic-agent",
                "kind": "direct",
                "status": "running",
            },
            {
                "id": "run-old-terminal",
                "agent_id": "analyst-agent",
                "kind": "comment",
                "status": "completed",
            },
        ]
        cancel_calls: list[list[str]] = []

        def fake_run(*args: str, cwd: Path | None = None) -> dict:
            cancel_calls.append(list(args))
            return {"id": args[2]}

        with (
            patch.object(adapter, "_run_with_read_retry", return_value=runs),
            patch.object(adapter, "_run", side_effect=fake_run),
        ):
            adapter._cancel_stale_live_runs("issue-1", self._request())

        self.assertEqual(
            [call[2] for call in cancel_calls],
            ["run-stray", "run-own-assign"],
        )
        for call in cancel_calls:
            self.assertEqual(
                call,
                [
                    "issue", "cancel-task", call[2],
                    "--issue", "issue-1", "--output", "json",
                ],
            )

    def test_dispatch_cancellation_failure_is_swallowed(self):
        adapter = self._adapter()
        runs = [
            {
                "id": "run-stray",
                "agent_id": "analyst-agent",
                "status": "running",
            }
        ]

        def failing_run(*args: str, cwd: Path | None = None) -> dict:
            raise RuntimeError("multica CLI failed")

        with (
            patch.object(adapter, "_run_with_read_retry", return_value=runs),
            patch.object(adapter, "_run", side_effect=failing_run),
        ):
            adapter._cancel_stale_live_runs("issue-1", self._request())

    def test_no_live_runs_is_a_noop(self):
        adapter = self._adapter()

        with patch.object(
            adapter, "_run_with_read_retry", return_value=[]
        ) as lookup, patch.object(adapter, "_run") as cancel:
            adapter._cancel_stale_live_runs("issue-1", self._request())

        lookup.assert_called_once()
        cancel.assert_not_called()

    def test_capture_run_watermark_returns_captured_ids(self):
        adapter = self._adapter()
        existing = [{"id": "run-a"}, {"id": "run-b"}]

        with patch.object(adapter, "_run_with_read_retry", return_value=existing):
            captured = adapter._capture_run_watermark(
                "issue-1", "task-1:ZHONGSHU_ANALYST:3"
            )

        self.assertEqual(captured, {"run-a", "run-b"})

    def test_capture_run_watermark_failure_returns_none(self):
        adapter = self._adapter()

        with patch.object(
            adapter,
            "_run_with_read_retry",
            side_effect=RuntimeError("temporary read failure"),
        ):
            captured = adapter._capture_run_watermark(
                "issue-1", "task-1:ZHONGSHU_ANALYST:3"
            )

        self.assertIsNone(captured)


if __name__ == "__main__":
    unittest.main()
