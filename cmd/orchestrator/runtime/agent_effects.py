"""Runtime execution of one idempotent agent effect.

This module is the only place where agent transport, polling, admission, and
result artifacts meet.  It returns an ``EffectOutcome``; it never changes the
workflow snapshot.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import json
import logging
import time
from typing import Callable

from ..domain.decisions import EffectRequest
from ..domain.errors import (
    CONTRACT_REJECTED_EVENT,
    FailureRecord,
    LeaseLostError,
    TransportError,
    UNSTRUCTURED_REPLY_EVENT,
)
from ..domain.policies.parallel import aggregate_zhongshu_workers
from ..transport import RawTransportReply, ReplyBinding, ReplyNormalizer
from .effects import EffectOutcome
from .ports import (
    AdmissionKey,
    AgentDispatchRequest,
    AgentTransportPort,
    ArtifactInput,
    ArtifactPort,
    DispatchReceipt,
    PollRequest,
    RemoteRunStatus,
    ConcurrencyAdmissionPort,
)
from .nodes import NodeContext, WorkerBinding, WorkerResult
from .nodes import NodeResult


logger = logging.getLogger("review_orchestrator_fsm")

# A run that failed after the agent delivered its reply is salvaged with a
# short bounded read: enough to cover comment-visibility lag around the
# terminal error, small enough to keep a genuine failure fast.
REMOTE_FAILURE_SALVAGE_POLLS = 3
REMOTE_FAILURE_SALVAGE_INTERVAL = 2.0


class AgentWorkerRunner:
    """Dispatch, observe, normalize, and archive one agent execution."""

    def __init__(
        self,
        transport: AgentTransportPort,
        *,
        admission: ConcurrencyAdmissionPort | None = None,
        artifacts: ArtifactPort | None = None,
        role_decoder: Callable[[Mapping[str, object]], object] | None = None,
        clock: Callable[[], float] = time.monotonic,
        utc_now: Callable[[], str] | None = None,
        poll_interval: float = 0.0,
        max_polls: int = 30,
        timeout_seconds: float = 900.0,
        agent_ids: Mapping[str, str] | None = None,
    ) -> None:
        self._transport = transport
        self._admission = admission
        self._artifacts = artifacts
        self._role_decoder = role_decoder or (lambda payload: dict(payload))
        self._clock = clock
        self._utc_now = utc_now or (lambda: datetime.now(timezone.utc).isoformat())
        self._poll_interval = max(0.0, poll_interval)
        self._max_polls = max(1, max_polls)
        self._timeout_seconds = max(0.0, timeout_seconds)
        self._agent_ids = dict(agent_ids or {})
        self._normalizer = ReplyNormalizer()

    def resolve_agent_pool(self, target_state: str) -> tuple[str, ...]:
        """Return the configured agent identities for a role, in order."""

        configured = str(self._agent_ids.get(target_state) or "").strip()
        return tuple(item.strip() for item in configured.split(",") if item.strip())

    def agent_pool_summary(self) -> dict[str, int]:
        """Expose how many distinct identities each role can run concurrently."""

        return {
            state: len(self.resolve_agent_pool(state)) or 1
            for state in self._agent_ids
        }

    def resolve_worker_agent(self, target_state: str, worker_id: str) -> str:
        """Return the agent identity a worker binds to, mirroring dispatch."""

        configured = self.resolve_agent_pool(target_state)
        if not configured:
            return ""
        try:
            worker_index = int(str(worker_id).rsplit("-", 1)[-1]) - 1
        except ValueError:
            worker_index = 0
        return configured[worker_index % len(configured)]

    def parallel_width(
        self,
        target_state: str,
        bindings: tuple[WorkerBinding, ...],
        issue_id: str,
    ) -> int:
        """Bound fan-out by distinct (issue, agent) targets, not by worker count.

        The external service coalesces or serializes two live runs that share an
        ``(issue_id, agent_id)`` target, so concurrency is limited by how many
        distinct identities the role was configured with.  Workers beyond the
        pool size run in waves: a freed identity is claimed by the next worker
        instead of the whole node collapsing to a single lane.

        A binding may carry its own ``issue_id`` (a per-worker child issue); that
        makes every worker a distinct target and lets a single identity fan out
        concurrently instead of queueing on one conversation.
        """

        if not bindings:
            return 1
        configured_ids = self.resolve_agent_pool(target_state)
        if configured_ids:
            target_ids = tuple(
                configured_ids[index % len(configured_ids)]
                for index in range(len(bindings))
            )
        else:
            target_ids = tuple(binding.agent_id for binding in bindings)
        target_issues = tuple(
            str(binding.issue_id or issue_id or "") for binding in bindings
        )
        distinct_targets = {
            (target_issues[index], str(target_ids[index] or ""))
            for index in range(len(bindings))
        }
        width = max(1, min(len(bindings), len(distinct_targets)))
        logger.info(
            "PARALLEL_WIDTH target_state=%s issue_id=%s bindings=%s "
            "configured_pool=%s distinct_targets=%s width=%s waves=%s",
            target_state,
            issue_id,
            len(bindings),
            len(configured_ids) or len(target_ids),
            len(distinct_targets),
            width,
            -(-len(bindings) // width),
        )
        return width

    def run_once(self, request: EffectRequest) -> EffectOutcome:
        payload = request.payload
        dispatch = self._dispatch_request(request, payload)
        deadline = self._clock() + self._timeout_seconds
        deadline_at = request.deadline_at or datetime.fromtimestamp(
            datetime.now(timezone.utc).timestamp() + self._timeout_seconds,
            timezone.utc,
        ).isoformat()
        logger.info(
            "AGENT_DISPATCH_START task_id=%s request_id=%s phase=%s role=%s "
            "timeout_seconds=%s deadline_at=%s max_polls=%s poll_interval=%s",
            request.task_id,
            dispatch.request_id,
            dispatch.phase,
            dispatch.role,
            self._timeout_seconds,
            deadline_at,
            self._max_polls,
            self._poll_interval,
        )
        lease_id: str | None = None
        if self._admission is not None:
            worker_id = str(payload.get("worker_id") or dispatch.agent_id)
            external_target_key = (
                f"{dispatch.issue_id}:{dispatch.agent_id}"
                if dispatch.issue_id else ""
            )
            # Fan-out workers each get their own child issue, so they are
            # distinct external targets even though they share one identity.
            # Without the worker suffix the admission layer would serialise
            # them on the shared parent issue key.
            if payload.get("fanout_parent_id") and external_target_key:
                external_target_key = f"{external_target_key}:{worker_id}"
            key = AdmissionKey(
                task_id=request.task_id,
                phase=dispatch.phase,
                worker_id=worker_id,
                revision_id=str(payload.get("revision_id") or request.idempotency_key),
                external_target_key=external_target_key,
            )
            receipt = self._admission.acquire(key, deadline)
            if receipt is None:
                raise TransportError("agent concurrency admission deadline reached")
            lease_id = receipt.lease_id

        try:
            recover = getattr(self._transport, "recover_completed", None)
            if callable(recover):
                recovered = recover(dispatch)
                if recovered is not None:
                    logger.info(
                        "AGENT_RESULT_RECOVERED task_id=%s request_id=%s "
                        "phase=%s role=%s",
                        dispatch.task_id, dispatch.request_id,
                        dispatch.phase, dispatch.role,
                    )
                    receipt = DispatchReceipt(
                        operation_id=dispatch.idempotency_key,
                        external_message_id="",
                        confirmed=True,
                        request_id=dispatch.request_id,
                    )
                    return self._complete_with_reply(
                        request, dispatch, receipt,
                        recovered, deadline_at,
                    )

            finder = getattr(self._transport, "find_existing", None)
            receipt = finder(dispatch) if callable(finder) else None
            if receipt is None:
                receipt = self._transport.dispatch(dispatch)
            if not isinstance(receipt, DispatchReceipt) or not receipt.confirmed:
                raise TransportError("agent dispatch was not confirmed")
            correlated_request_id = receipt.request_id or dispatch.request_id
            poll = PollRequest(
                request.task_id,
                correlated_request_id,
                receipt.operation_id,
            )
            last_status: RemoteRunStatus | None = None
            for _ in range(self._max_polls):
                if self._clock() > deadline:
                    break
                if lease_id is not None and not self._admission.refresh(lease_id):
                    raise LeaseLostError("agent admission lease was lost")
                status = self._transport.status(poll)
                if not isinstance(status, RemoteRunStatus):
                    raise TransportError("agent transport returned an invalid run status")
                last_status = status
                if status.status == "FAILED":
                    # The run can fail AFTER the agent delivered its structured
                    # reply (observed live: the model runtime errored seconds
                    # after the contract comment landed).  The work is done and
                    # correlated by request id, so salvage the reply instead of
                    # discarding it and re-running the whole dispatch.
                    salvaged = self._salvage_replies(poll, deadline)
                    if salvaged:
                        logger.warning(
                            "REMOTE_RUN_FAILED_REPLY_SALVAGED task_id=%s "
                            "request_id=%s replies=%s remote_error=%r",
                            request.task_id,
                            poll.request_id,
                            len(salvaged),
                            status.error_message or "",
                        )
                        return self._completed_outcome(
                            request, dispatch, receipt, poll, deadline_at, salvaged
                        )
                    failure = self._remote_failure(request, status, receipt)
                    logger.warning(
                        "REMOTE_RUN_FAILED task_id=%s request_id=%s "
                        "operation_id=%s remote_status=%s remote_error=%s "
                        "error_code=%s retryable=%s deadline_at=%s",
                        request.task_id,
                        poll.request_id,
                        receipt.operation_id,
                        status.status,
                        status.error_message or "",
                        failure.error_code,
                        failure.retryable,
                        deadline_at,
                    )
                    return EffectOutcome(
                        status="FAILED",
                        event_name="FAIL",
                        event_payload={
                            "request_id": correlated_request_id,
                            "operation_id": receipt.operation_id,
                            "remote_status": status.status,
                            "deadline_at": deadline_at,
                            "retryable": failure.retryable,
                        },
                        request_id=correlated_request_id,
                        operation_id=receipt.operation_id,
                        deadline_at=deadline_at,
                        failure=failure,
                    )
                if status.status == "COMPLETED":
                    return self._completed(request, dispatch, receipt, poll, deadline, deadline_at)
                if status.status not in {"RUNNING", "UNKNOWN"}:
                    raise TransportError(f"unsupported remote run status: {status.status}")
                if self._poll_interval:
                    time.sleep(min(self._poll_interval, max(0.0, deadline - self._clock())))

            if last_status is not None and last_status.status == "UNKNOWN":
                code = "REMOTE_RUN_CORRELATION_UNKNOWN"
                message = last_status.error_message or "remote run correlation remained unknown"
            else:
                code = "AGENT_TIMEOUT"
                message = "agent run did not reach a terminal state before deadline"
            # The remote run keeps consuming tokens after this give-up, and
            # the FSM retry's new run would be merged/queued behind it (live
            # incident task-20260920-3c6b68).  Cancel the stale runs first.
            self._cancel_remote_runs(poll)
            logger.warning(
                "REMOTE_RUN_TERMINAL_STATE_NOT_REACHED task_id=%s request_id=%s "
                "operation_id=%s error_code=%s last_status=%s deadline_at=%s",
                request.task_id,
                correlated_request_id,
                receipt.operation_id,
                code,
                last_status.status if last_status else "none",
                deadline_at,
            )
            failure = self._failure(
                request,
                stage="remote_run",
                code=code,
                retryable=True,
                message=message,
                external_id=(last_status.operation_id if last_status else receipt.operation_id),
            )
            return EffectOutcome(
                status="FAILED",
                event_name="FAIL",
                event_payload={
                    "request_id": correlated_request_id,
                    "operation_id": receipt.operation_id,
                    "deadline_at": deadline_at,
                    "retryable": failure.retryable,
                },
                request_id=correlated_request_id,
                operation_id=receipt.operation_id,
                deadline_at=deadline_at,
                failure=failure,
            )
        finally:
            if lease_id is not None:
                self._admission.release(lease_id)

    def _cancel_remote_runs(self, poll: PollRequest) -> None:
        """Best-effort stop the stale remote runs before the FSM retries."""

        cancel = getattr(self._transport, "cancel_runs", None)
        if not callable(cancel):
            return
        try:
            cancelled = cancel(poll)
        except Exception as error:
            logger.warning(
                "AGENT_REMOTE_RUN_CANCEL_ERROR task_id=%s request_id=%s error=%s",
                poll.task_id,
                poll.request_id,
                str(error)[:300],
            )
            return
        if cancelled:
            logger.warning(
                "AGENT_REMOTE_RUN_CANCEL_ISSUED task_id=%s request_id=%s runs=%s",
                poll.task_id,
                poll.request_id,
                ",".join(str(item) for item in cancelled),
            )

    def _completed(
        self,
        request: EffectRequest,
        dispatch: AgentDispatchRequest,
        receipt: DispatchReceipt,
        poll: PollRequest,
        deadline: float,
        deadline_at: str,
    ) -> EffectOutcome:
        replies = self._poll_completed(poll, deadline)
        if not replies:
            failure = self._failure(
                request,
                stage="agent_result",
                code="AGENT_RESULT_MISSING",
                retryable=True,
                message="agent run completed without a correlated result",
                external_id=receipt.external_message_id or receipt.operation_id,
            )
            return EffectOutcome(
                status="FAILED",
                event_name="FAIL",
                event_payload={
                    "request_id": poll.request_id,
                    "deadline_at": deadline_at,
                    "retryable": failure.retryable,
                },
                request_id=poll.request_id,
                operation_id=receipt.operation_id,
                deadline_at=deadline_at,
                failure=failure,
            )
        return self._completed_outcome(
            request, dispatch, receipt, poll, deadline_at, replies
        )

    def _completed_outcome(
        self,
        request: EffectRequest,
        dispatch: AgentDispatchRequest,
        receipt: DispatchReceipt,
        poll: PollRequest,
        deadline_at: str,
        replies: tuple[RawTransportReply, ...],
    ) -> EffectOutcome:
        raw = replies[-1]
        if not isinstance(raw, RawTransportReply):
            raise TransportError("agent transport returned an invalid reply")
        # ``target_state`` is carried in the effect payload but intentionally
        # not in the transport request DTO.  A missing value is still safe:
        # role/phase/request binding remains mandatory.
        target_state = str(request.payload.get("target_state") or "")
        binding = ReplyBinding(
            task_id=dispatch.task_id,
            request_id=poll.request_id,
            author_id=dispatch.agent_id,
            phase=dispatch.phase,
            role=dispatch.role,
            target_state=target_state or dispatch.phase,
        )
        envelope = self._normalizer.normalize(raw, binding, self._role_decoder)
        result_payload = envelope.payload
        if not isinstance(result_payload, Mapping):
            raise TransportError("normalized agent payload must be a mapping")
        result_payload = dict(result_payload)
        artifact_id: str | None = None
        if self._artifacts is not None:
            content = json.dumps(result_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            artifact = self._artifacts.write(
                ArtifactInput(
                    task_id=request.task_id,
                    name=f"agent/{dispatch.request_id}.json",
                    content=content,
                )
            )
            artifact_id = artifact.artifact_id
        if artifact_id:
            result_payload["result_artifact_id"] = artifact_id
        event_name = str(result_payload.get("action") or "")
        if not event_name:
            raise TransportError("normalized agent result has no action")
        result_payload = {
            **dict(result_payload),
            "request_id": poll.request_id,
            "operation_id": receipt.operation_id,
            "external_message_id": receipt.external_message_id,
            "idempotency_key": request.idempotency_key,
        }
        return EffectOutcome(
            status="SUCCEEDED",
            event_name=event_name,
            event_payload=result_payload,
            request_id=poll.request_id,
            operation_id=receipt.operation_id,
            deadline_at=deadline_at,
        )

    def _poll_completed(
        self,
        poll: PollRequest,
        deadline: float,
    ) -> tuple[RawTransportReply, ...]:
        """Bound the post-terminal result read to tolerate comment visibility lag."""
        replies = tuple(self._transport.poll(poll))
        for _ in range(max(0, self._max_polls - 1)):
            if replies:
                break
            if self._poll_interval:
                remaining = deadline - self._clock()
                if remaining <= 0:
                    break
                time.sleep(min(self._poll_interval, remaining))
            elif self._clock() > deadline:
                break
            replies = tuple(self._transport.poll(poll))
        return replies

    def _salvage_replies(
        self,
        poll: PollRequest,
        deadline: float,
    ) -> tuple[RawTransportReply, ...]:
        """Short bounded read for a reply delivered before the run failed.

        A genuinely failed run must stay a fast failure: this only tolerates
        the few seconds of comment-visibility lag around the terminal error,
        not the full result window.
        """

        for attempt in range(REMOTE_FAILURE_SALVAGE_POLLS):
            replies = tuple(self._transport.poll(poll))
            if replies:
                return replies
            if attempt + 1 < REMOTE_FAILURE_SALVAGE_POLLS and self._clock() < deadline:
                time.sleep(REMOTE_FAILURE_SALVAGE_INTERVAL)
        return ()

    def _complete_with_reply(
        self,
        request: EffectRequest,
        dispatch: AgentDispatchRequest,
        receipt: DispatchReceipt,
        raw: RawTransportReply,
        deadline_at: str,
    ) -> EffectOutcome:
        target_state = str(request.payload.get("target_state") or "")
        binding = ReplyBinding(
            task_id=dispatch.task_id,
            request_id=receipt.request_id or dispatch.request_id,
            author_id=dispatch.agent_id,
            phase=dispatch.phase,
            role=dispatch.role,
            target_state=target_state or dispatch.phase,
        )
        envelope = self._normalizer.normalize(raw, binding, self._role_decoder)
        result_payload = envelope.payload
        if not isinstance(result_payload, Mapping):
            raise TransportError("normalized agent payload must be a mapping")
        result_payload = dict(result_payload)
        artifact_id: str | None = None
        if self._artifacts is not None:
            content = json.dumps(result_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            artifact = self._artifacts.write(
                ArtifactInput(
                    task_id=request.task_id,
                    name=f"agent/{dispatch.request_id}.json",
                    content=content,
                )
            )
            artifact_id = artifact.artifact_id
        if artifact_id:
            result_payload["result_artifact_id"] = artifact_id
        event_name = str(result_payload.get("action") or "")
        if not event_name:
            raise TransportError("normalized agent result has no action")
        result_payload = {
            **dict(result_payload),
            "request_id": receipt.request_id or dispatch.request_id,
            "operation_id": receipt.operation_id,
            "external_message_id": receipt.external_message_id,
            "idempotency_key": request.idempotency_key,
        }
        return EffectOutcome(
            status="SUCCEEDED",
            event_name=event_name,
            event_payload=result_payload,
            request_id=receipt.request_id or dispatch.request_id,
            operation_id=receipt.operation_id,
            deadline_at=deadline_at,
        )

    def _dispatch_request(
        self,
        request: EffectRequest,
        payload: Mapping[str, object],
    ) -> AgentDispatchRequest:
        target_state = str(payload.get("target_state") or "")
        worker_id = str(payload.get("worker_id") or "")
        agent_id = self.resolve_worker_agent(target_state, worker_id) or str(
            payload.get("agent_id") or ""
        )
        pool_size = len(self.resolve_agent_pool(target_state)) or 1
        logger.info(
            "AGENT_ID_BOUND task_id=%s target_state=%s worker_id=%s agent_id=%s "
            "pool_size=%s external_target=%s",
            request.task_id,
            target_state,
            worker_id,
            agent_id,
            pool_size,
            f"{payload.get('issue_id') or ''}:{agent_id}",
        )
        dispatch_context = payload.get("dispatch_context")
        request_context = (
            dict(dispatch_context) if isinstance(dispatch_context, Mapping) else {}
        )
        fanout_parent = str(payload.get("fanout_parent_id") or "")
        if fanout_parent:
            request_context["fanout_parent_id"] = fanout_parent
            request_context["fanout_title"] = str(payload.get("fanout_title") or "")
        return AgentDispatchRequest(
            task_id=request.task_id,
            issue_id=str(payload.get("issue_id") or ""),
            request_id=str(payload.get("request_id") or request.idempotency_key),
            agent_id=agent_id,
            role=str(payload.get("role") or ""),
            phase=str(payload.get("phase") or ""),
            prompt_ref=str(payload.get("prompt_ref") or request.payload_ref or ""),
            idempotency_key=request.idempotency_key,
            sent_after=self._utc_now(),
            target_state=target_state,
            revision_id=str(payload.get("revision_id") or ""),
            plan_hash=str(payload.get("plan_hash") or ""),
            context=request_context,
        )

    @staticmethod
    def _remote_failure(
        request: EffectRequest,
        status: RemoteRunStatus,
        receipt: DispatchReceipt,
    ) -> FailureRecord:
        return AgentWorkerRunner._failure(
            request,
            stage="remote_run",
            code=status.error_code or "REMOTE_RUN_FAILED",
            retryable=True,
            message=status.error_message or "remote agent run failed",
            external_id=status.operation_id or receipt.operation_id,
        )

    @staticmethod
    def _failure(
        request: EffectRequest,
        *,
        stage: str,
        code: str,
        retryable: bool,
        message: str,
        external_id: str | None,
    ) -> FailureRecord:
        return FailureRecord(
            failure_id=f"{request.effect_id}:{code}",
            stage=stage,
            owner_component=request.effect_type,
            task_id=request.task_id,
            state=str(request.payload.get("target_state") or ""),
            sequence=int(request.payload.get("sequence") or 0),
            node_run_id=(str(request.payload["node_run_id"]) if request.payload.get("node_run_id") else None),
            worker_id=(str(request.payload["worker_id"]) if request.payload.get("worker_id") else None),
            effect_id=request.effect_id,
            error_code=code,
            retryable=retryable,
            message=message,
            cause_type="RemoteRun",
            external_id=external_id,
        )


class AgentNodeWorkerRunner:
    """Adapt one agent effect runner to the ``NodeExecutor`` worker port."""

    def __init__(self, runner: AgentWorkerRunner) -> None:
        self._runner = runner

    def run(self, binding: WorkerBinding, context: NodeContext) -> WorkerResult:
        attempt = max(1, int(getattr(binding, "attempt", 1) or 1))
        request_id = _attempt_request_id(binding.request_id, attempt)
        dispatch_context = dict(binding.dispatch_context or {})
        if attempt > 1:
            dispatch_context["dispatch_attempt"] = attempt
        payload = {
            "issue_id": binding.issue_id or context.issue_id,
            "request_id": request_id,
            "agent_id": binding.agent_id,
            "role": binding.role,
            "phase": binding.phase,
            "target_state": context.state,
            "node_run_id": context.node_run_id,
            "worker_id": binding.worker_id,
            "group_id": binding.group_id if binding.group_id is not None else context.group_id,
            "item_id": binding.item_id if binding.item_id is not None else context.item_id,
            "sequence": context.sequence,
            "revision_id": context.revision_id,
            "plan_hash": context.plan_hash,
            "prompt_ref": binding.prompt_ref or context.prompt_ref,
        }
        if dispatch_context:
            payload["dispatch_context"] = dispatch_context
        if binding.fanout_parent_id:
            payload["fanout_parent_id"] = binding.fanout_parent_id
            payload["fanout_title"] = binding.fanout_title
        logger.info(
            "AGENT_WORKER_BOUND task_id=%s node_run_id=%s worker_id=%s "
            "group_id=%s item_id=%s dispatch_mode=%s prompt_chars=%s attempt=%s",
            context.task_id,
            context.node_run_id,
            binding.worker_id,
            payload.get("group_id") or "",
            payload.get("item_id") or "",
            str(binding.dispatch_context.get("zhongshu_dispatch_mode") or ""),
            len(str(payload.get("prompt_ref") or "")),
            attempt,
        )
        request = EffectRequest(
            effect_id=_attempt_effect_id(context.node_run_id, binding.worker_id, attempt),
            effect_type="agent_dispatch",
            task_id=context.task_id,
            idempotency_key=request_id,
            payload=payload,
        )
        outcome = self._runner.run_once(request)
        if outcome.status != "SUCCEEDED":
            return WorkerResult(
                binding.worker_id,
                "FAILED",
                None,
                outcome.failure,
                external_issue_id=(
                    str(outcome.operation_id or "")
                    if binding.fanout_parent_id
                    else ""
                ),
            )
        artifact_id = None
        if isinstance(outcome.event_payload, Mapping):
            value = outcome.event_payload.get("result_artifact_id")
            artifact_id = str(value) if value else None
        return WorkerResult(
            binding.worker_id,
            "SUCCEEDED",
            artifact_id,
            result_payload=outcome.event_payload,
            external_issue_id=(
                str(outcome.operation_id or "") if binding.fanout_parent_id else ""
            ),
        )


_UNSTRUCTURED_REPLY_ACTION = UNSTRUCTURED_REPLY_EVENT
_CONTRACT_REJECTED_ACTION = CONTRACT_REJECTED_EVENT

_REPLY_ACTION_FAILURES = {
    _UNSTRUCTURED_REPLY_ACTION: (
        "AGENT_REPLY_UNSTRUCTURED",
        "agent worker returned an unstructured reply instead of "
        "the required JSON result contract",
    ),
    _CONTRACT_REJECTED_ACTION: (
        "AGENT_REPLY_CONTRACT_REJECTED",
        "agent worker reply violated the role result contract",
    ),
}


def _rejected_reply_code(result: WorkerResult) -> tuple[str, str] | None:
    """Return the reply-shape failure a delivered-but-unusable result implies.

    A transport reports such a reply with a synthetic action (see ``adapters``).
    Letting the sentinel into the fan-in action set made a worker collide with its
    peers' real actions and produced a non-retryable ``NODE_ACTION_CONFLICT``; it
    is handled here as a retryable reply-shape failure instead, so the workflow
    re-asks the agent rather than reporting a missing result.
    """

    payload = result.result_payload
    if not isinstance(payload, Mapping):
        return None
    action = str(payload.get("action") or "")
    mapped = _REPLY_ACTION_FAILURES.get(action)
    if mapped is None:
        return None
    error_code, default_message = mapped
    reason = str(payload.get("contract_rejection") or "")
    return error_code, f"{default_message}: {reason}" if reason else default_message


def _is_unstructured_reply(result: WorkerResult) -> bool:
    """True when a worker result is a transport placeholder, not a domain action."""

    return _rejected_reply_code(result) is not None


_TASK_REVIEW_ACTIONS = {"TASK_APPROVED", "TASK_CHANGES_REQUIRED"}


def _attempt_request_id(request_id: str, attempt: int) -> str:
    """Scope a worker request id to its dispatch attempt.

    The attempt suffix is the only thing that makes a node-level worker retry a
    *new* external request; without it the transport's idempotency lookup
    returns the finished run and the retry can only poll a drained run.
    """

    return request_id if attempt <= 1 else f"{request_id}:attempt-{attempt}"


def _attempt_effect_id(node_run_id: str, worker_id: str, attempt: int) -> str:
    effect_id = f"worker:{node_run_id}:{worker_id}"
    return effect_id if attempt <= 1 else f"{effect_id}:attempt-{attempt}"


def _partial_task_reviews(
    results: tuple[WorkerResult, ...],
    failed: list[WorkerResult],
    binding_contexts: Mapping[str, Mapping[str, object]] | None = None,
) -> list[dict[str, object]]:
    """Per-task verdicts from the workers that succeeded despite a node failure.

    Only the ledger-relevant identity/verdict fields are kept; re-reviewing an
    approved task is the expensive part we want a retry to skip.  The identity
    fields come from the binding's dispatch record, not from the worker reply.
    """

    failed_ids = {result.worker_id for result in failed}
    reviews: list[dict[str, object]] = []
    for result in results:
        if result.worker_id in failed_ids:
            continue
        payload = result.result_payload
        if not isinstance(payload, Mapping):
            continue
        action = str(payload.get("action") or "")
        context = binding_contexts.get(result.worker_id) if binding_contexts else None
        context = context if isinstance(context, Mapping) else None
        item_id = str(
            (context or {}).get("item_id") or payload.get("item_id") or ""
        )
        if action not in _TASK_REVIEW_ACTIONS or not item_id:
            continue
        reviews.append(
            {
                "item_id": item_id,
                "action": action,
                "reviewed_task_hash": (
                    (context or {}).get("task_hash") or payload.get("reviewed_task_hash")
                ),
                "reviewed_dependency_hash": (
                    (context or {}).get("dependency_hash")
                    or payload.get("reviewed_dependency_hash")
                ),
            }
        )
    return reviews


def _salvage_payload_from_report(report: Mapping[str, object]) -> dict[str, object] | None:
    """Turn a task-review aggregate into the FAIL payload's salvage section.

    The aggregate's ``task_reviews`` rows carry the ledger identity fields and
    its ``findings`` carry the consolidated finding records; embedding each
    item's findings into its row yields a self-contained salvage row the FSM
    can store on the review state and the next round's fan-in can reuse
    verbatim.
    """

    findings_by_item: dict[str, list[dict[str, object]]] = {}
    for finding in report.get("findings") or []:
        if not isinstance(finding, Mapping):
            continue
        item_id = str(finding.get("item_id") or "")
        if item_id:
            findings_by_item.setdefault(item_id, []).append(dict(finding))
    rows: list[dict[str, object]] = []
    for row in report.get("task_reviews") or []:
        if not isinstance(row, Mapping):
            continue
        item_id = str(row.get("item_id") or "")
        rows.append(
            {
                **dict(row),
                "findings": list(findings_by_item.get(item_id, [])),
            }
        )
    if not rows:
        return None
    return {
        "task_reviews": [
            dict(row)
            for row in report.get("task_reviews") or []
            if isinstance(row, Mapping)
        ],
        "findings": [
            dict(finding)
            for finding in report.get("findings") or []
            if isinstance(finding, Mapping)
        ],
        "salvaged_task_reviews": rows,
    }


class AgentNodeJoiner:
    """Join agent worker replies into one deterministic node decision."""

    def __init__(
        self,
        node_run_id: str,
        *,
        task_id: str = "",
        state: str = "",
        sequence: int = 0,
        revision_id: str = "",
        plan_hash: str = "",
        task_review_queue: object | None = None,
        canonical_requirements: tuple[dict[str, object], ...] = (),
        dispatch_mode: str = "",
        base_plan: object | None = None,
        previous_review: object | None = None,
        binding_contexts: Mapping[str, Mapping[str, object]] | None = None,
    ) -> None:
        self._node_run_id = node_run_id
        self._task_id = task_id
        self._state = state
        self._sequence = sequence
        self._revision_id = revision_id
        self._plan_hash = plan_hash
        self._task_review_queue = task_review_queue
        self._canonical_requirements = canonical_requirements
        self._dispatch_mode = dispatch_mode
        self._base_plan = base_plan
        self._previous_review = (
            dict(previous_review)
            if isinstance(previous_review, Mapping)
            else None
        )
        self._binding_contexts: dict[str, dict[str, object]] = {
            str(worker_id): dict(ctx)
            for worker_id, ctx in (binding_contexts or {}).items()
            if isinstance(ctx, Mapping)
        }

    def join(self, results: tuple[WorkerResult, ...]):
        failed = [
            result
            for result in results
            if result.status != "SUCCEEDED" or _is_unstructured_reply(result)
        ]
        # Harvest the verdicts of the workers that did finish exactly once, so
        # every failure exit can hand them to the FSM ledger and the retry only
        # has to re-dispatch the tasks that are still missing.
        partial_reviews = (
            _partial_task_reviews(results, failed, self._binding_contexts)
            if self._task_review_queue is not None
            else []
        )
        if failed:
            failure = next((result.failure for result in failed if result.failure), None)
            if failure is None:
                unstructured = next(
                    (result for result in failed if _is_unstructured_reply(result)),
                    None,
                )
                if unstructured is not None:
                    error_code, message = _rejected_reply_code(unstructured) or (
                        "AGENT_REPLY_UNSTRUCTURED",
                        "agent worker returned an unstructured reply instead of "
                        "the required JSON result contract",
                    )
                    failure = FailureRecord(
                        failure_id=f"{self._node_run_id}:NODE_UNSTRUCTURED_REPLY",
                        stage="node_join",
                        owner_component="agent_node_joiner",
                        task_id=self._task_id,
                        state=self._state,
                        sequence=self._sequence,
                        node_run_id=self._node_run_id,
                        worker_id=unstructured.worker_id,
                        effect_id=None,
                        error_code=error_code,
                        retryable=True,
                        message=message,
                        cause_type="WorkerResult",
                    )
                else:
                    failure = FailureRecord(
                        failure_id=f"{self._node_run_id}:NODE_WORKER_FAILED",
                        stage="node_join",
                        owner_component="agent_node_joiner",
                        task_id=self._task_id,
                        state=self._state,
                        sequence=self._sequence,
                        node_run_id=self._node_run_id,
                        worker_id=failed[0].worker_id,
                        effect_id=None,
                        error_code="NODE_WORKER_FAILED",
                        retryable=True,
                        message="one or more node workers failed",
                        cause_type="WorkerResult",
                    )
            aggregate: dict[str, object] = {"action": "FAIL"}
            if partial_reviews:
                # The FSM folds these into the review ledger even on the
                # FAIL -> RETRY path (see _select_review_jobs).  When the
                # queue-mode fan-in can still normalize the surviving
                # verdicts, carry the full salvage payload (rows + findings)
                # so the retry re-dispatches only the tasks still missing.
                salvage = (
                    self._salvage_failed_round(results, failed)
                    if self._task_review_queue is not None
                    else None
                )
                if salvage is not None:
                    aggregate.update(salvage)
                else:
                    aggregate["task_reviews"] = partial_reviews
            return NodeResult(
                self._node_run_id,
                "FAILED",
                results,
                aggregate=aggregate,
                failure=failure,
            )

        worker_payloads = []
        for result in results:
            payload = result.result_payload
            if not isinstance(payload, Mapping):
                continue
            merged = {**dict(payload), "worker_id": result.worker_id}
            # The binding's dispatch record owns the routing key: the worker
            # never needs to echo which job it reviewed.
            context = self._binding_contexts.get(result.worker_id)
            if context and context.get("review_job_id"):
                merged["review_job_id"] = str(context["review_job_id"])
            worker_payloads.append(merged)
        if self._task_review_queue is not None:
            return self._join_task_review(results, worker_payloads, partial_reviews)

        if self._dispatch_mode == "item_revise":
            return self._join_item_revise(results, worker_payloads)

        payloads = [
            result.result_payload
            for result in results
            if isinstance(result.result_payload, Mapping)
        ]
        actions = sorted({str(payload.get("action") or "") for payload in payloads})
        actions = [action for action in actions if action]
        if not actions:
            return NodeResult(
                self._node_run_id,
                "FAILED",
                results,
                aggregate={"action": "FAIL"},
                failure=FailureRecord(
                    failure_id=f"{self._node_run_id}:NODE_ACTION_MISSING",
                    stage="node_join",
                    owner_component="agent_node_joiner",
                    task_id=self._task_id,
                    state=self._state,
                    sequence=self._sequence,
                    node_run_id=self._node_run_id,
                    worker_id=None,
                    effect_id=None,
                    error_code="NODE_ACTION_MISSING",
                    retryable=True,
                    message="node workers completed without a domain action",
                    cause_type="WorkerResult",
                ),
            )
        if len(actions) != 1:
            return NodeResult(
                self._node_run_id,
                "FAILED",
                results,
                aggregate={"actions": actions},
                failure=FailureRecord(
                    failure_id=f"{self._node_run_id}:NODE_ACTION_CONFLICT",
                    stage="node_join",
                    owner_component="agent_node_joiner",
                    task_id=self._task_id,
                    state=self._state,
                    sequence=self._sequence,
                    node_run_id=self._node_run_id,
                    worker_id=None,
                    effect_id=None,
                    error_code="NODE_ACTION_CONFLICT",
                    retryable=False,
                    message="node workers returned conflicting domain actions",
                    cause_type="WorkerResult",
                ),
            )
        if self._state.startswith("ZHONGSHU_"):
            try:
                aggregate = aggregate_zhongshu_workers(
                    self._task_id,
                    self._state,
                    self._revision_id,
                    self._plan_hash,
                    worker_payloads,
                    self._canonical_requirements or None,
                )
            except ValueError as error:
                return NodeResult(
                    self._node_run_id,
                    "FAILED",
                    results,
                    aggregate={"action": "FAIL"},
                    failure=FailureRecord(
                        failure_id=f"{self._node_run_id}:NODE_FANIN_REJECTED",
                        stage="node_join",
                        owner_component="zhongshu_fan_in",
                        task_id=self._task_id,
                        state=self._state,
                        sequence=self._sequence,
                        node_run_id=self._node_run_id,
                        worker_id=None,
                        effect_id=None,
                        error_code="NODE_FANIN_REJECTED",
                        retryable=True,
                        message=str(error),
                        cause_type="FanInValidation",
                    ),
                )
        else:
            aggregate = {"action": actions[0], "worker_count": len(results)}
        return NodeResult(self._node_run_id, "SUCCEEDED", results, aggregate=aggregate)

    def _salvage_failed_round(
        self,
        results: tuple[WorkerResult, ...],
        failed: list[WorkerResult],
    ) -> dict[str, object] | None:
        """Normalize the surviving workers' verdicts through the real fan-in.

        Running ``aggregate_task_review_results`` over the successful payloads
        yields the same identity stamping, pseudo-blocked demotion, and finding
        consolidation a healthy round gets, so the salvaged rows are exactly
        what a retry's fan-in expects to receive back.  Any fan-in rejection
        falls back to the slim ledger rows collected by
        ``_partial_task_reviews``.
        """

        from ..zhongshu_review import aggregate_task_review_results

        failed_ids = {result.worker_id for result in failed}
        payloads: list[dict[str, object]] = []
        for result in results:
            if result.worker_id in failed_ids:
                continue
            payload = result.result_payload
            if not isinstance(payload, Mapping):
                continue
            if str(payload.get("action") or "") not in _TASK_REVIEW_ACTIONS:
                continue
            merged = {**dict(payload), "worker_id": result.worker_id}
            context = self._binding_contexts.get(result.worker_id)
            if context and context.get("review_job_id"):
                merged["review_job_id"] = str(context["review_job_id"])
            payloads.append(merged)
        if not payloads:
            return None
        try:
            report = aggregate_task_review_results(
                self._revision_id,
                self._plan_hash,
                self._task_review_queue,
                payloads,
                previous=self._previous_review,
            )
        except (ValueError, TypeError, KeyError) as error:
            logger.warning(
                "NODE_TASK_REVIEW_SALVAGE_FAILED node_run_id=%s error=%s",
                self._node_run_id,
                error,
            )
            return None
        return _salvage_payload_from_report(report)

    def _join_task_review(self, results, worker_payloads, partial_reviews=()):
        from ..zhongshu_review import aggregate_task_review_results
        from ..zhongshu_review_queue import ReviewQueueError

        try:
            report = aggregate_task_review_results(
                self._revision_id,
                self._plan_hash,
                self._task_review_queue,
                worker_payloads,
                previous=self._previous_review,
            )
        except (ValueError, ReviewQueueError) as error:
            aggregate: dict[str, object] = {"action": "FAIL"}
            if partial_reviews:
                # Even when fan-in is rejected, keep the per-task verdicts we
                # did obtain so the retry is incremental.
                aggregate["task_reviews"] = list(partial_reviews)
            return NodeResult(
                self._node_run_id,
                "FAILED",
                results,
                aggregate=aggregate,
                failure=FailureRecord(
                    failure_id=f"{self._node_run_id}:NODE_TASK_REVIEW_REJECTED",
                    stage="node_join",
                    owner_component="zhongshu_task_review_fan_in",
                    task_id=self._task_id,
                    state=self._state,
                    sequence=self._sequence,
                    node_run_id=self._node_run_id,
                    worker_id=None,
                    effect_id=None,
                    error_code="NODE_TASK_REVIEW_REJECTED",
                    retryable=False,
                    message=str(error),
                    cause_type="FanInValidation",
                ),
            )
        # The closed FSM uses APPROVE_CRITIC as its critic edge; the per-task
        # review protocol names the same decision APPROVE_FREEZE.
        if report.get("action") == "APPROVE_FREEZE" and self._state == "ZHONGSHU_CRITIC":
            report["action"] = "APPROVE_CRITIC"
        logger.info(
            "NODE_TASK_REVIEW_FANIN node_run_id=%s action=%s complete=%s "
            "completed=%s/%s affected_items=%s reverified_items=%s active_blockers=%s",
            self._node_run_id,
            report.get("action"),
            report.get("task_review_complete"),
            report.get("completed_task_count"),
            report.get("total_task_count"),
            report.get("affected_item_ids"),
            sorted(report.get("affected_item_reasons") or {}),
            report.get("active_p0_p1_finding_ids"),
        )
        rejected = report.get("rejected_reviews") or []
        for entry in rejected:
            logger.warning(
                "NODE_TASK_REVIEW_REJECTED node_run_id=%s review_job_id=%s "
                "group_id=%s item_id=%s reason=%s mismatch=%s",
                self._node_run_id,
                entry.get("review_job_id") or "",
                entry.get("group_id") or "",
                entry.get("item_id") or "",
                entry.get("reason") or "",
                entry.get("mismatch") or "",
            )
        accepted_ids = {
            str(item.get("review_job_id") or "")
            for item in report.get("task_reviews") or []
            if item.get("review_job_id")
        }
        rejected_ids = {
            str(entry.get("review_job_id") or "")
            for entry in rejected
            if entry.get("review_job_id")
        }
        queue = self._task_review_queue
        if queue is not None:
            completed_ids = {
                job.review_job_id
                for job in getattr(queue, "jobs", ())
                if getattr(job, "status", "") == "COMPLETED"
            }
            for missing_id in sorted(completed_ids - accepted_ids - rejected_ids):
                logger.warning(
                    "NODE_TASK_REVIEW_UNBOUND node_run_id=%s review_job_id=%s "
                    "reason=%s",
                    self._node_run_id,
                    missing_id,
                    "COMPLETED_JOB_WITHOUT_RESULT",
                )
        if rejected and not report.get("task_review_complete"):
            # One or more workers replied with a contract-valid envelope that is
            # not a usable per-task verdict (bad identity/format).  That is an
            # execution-integrity slip, not a content disagreement, so re-ask the
            # affected workers on a bounded retry instead of escalating straight
            # to a human gate.  The accepted verdicts ride along so the retry
            # only re-dispatches the tasks still missing.
            logger.warning(
                "NODE_TASK_REVIEW_RESULT_INVALID node_run_id=%s rejected=%s",
                self._node_run_id,
                len(rejected),
            )
            return NodeResult(
                self._node_run_id,
                "FAILED",
                results,
                aggregate={
                    "action": "FAIL",
                    "task_reviews": report.get("task_reviews") or [],
                    **(
                        _salvage_payload_from_report(report)
                        or {"task_reviews": report.get("task_reviews") or []}
                    ),
                },
                failure=FailureRecord(
                    failure_id=f"{self._node_run_id}:NODE_TASK_REVIEW_RESULT_INVALID",
                    stage="node_join",
                    owner_component="zhongshu_task_review_fan_in",
                    task_id=self._task_id,
                    state=self._state,
                    sequence=self._sequence,
                    node_run_id=self._node_run_id,
                    worker_id=None,
                    effect_id=None,
                    error_code="NODE_TASK_REVIEW_RESULT_INVALID",
                    retryable=True,
                    message="one or more task-review replies were unusable",
                    cause_type="FanInValidation",
                ),
            )
        return NodeResult(self._node_run_id, "SUCCEEDED", results, aggregate=report)

    def _join_item_revise(self, results, worker_payloads):
        """Merge one-patch-per-item worker replies into the revised plan.

        Every worker owns exactly one contested item and returns the patched
        item object.  The joiner materializes each patch against its item id
        (reply-shape failures charge the reply budget), merges them onto the
        dispatch-time plan, and re-hashes the merged graph — the orchestrator
        owns the plan, workers never mutate shared state.
        """

        from ..domain.policies.item_revise import (
            materialize_item_patch,
            merge_item_patches,
        )
        from ..domain.policies.solver_plan import structural_integrity_errors
        from ..zhongshu_parallel import canonical_plan_hash
        from ..zhongshu_review_queue import structural_gate

        patches = []
        for payload in worker_payloads:
            if str(payload.get("action") or "") != "READY_FOR_CRITIC":
                return NodeResult(
                    self._node_run_id,
                    "FAILED",
                    results,
                    aggregate={"action": "FAIL"},
                    failure=FailureRecord(
                        failure_id=f"{self._node_run_id}:ITEM_REVISION_ACTION_INVALID",
                        stage="node_join",
                        owner_component="agent_node_joiner",
                        task_id=self._task_id,
                        state=self._state,
                        sequence=self._sequence,
                        node_run_id=self._node_run_id,
                        worker_id=str(payload.get("worker_id") or ""),
                        effect_id=None,
                        error_code="NODE_ITEM_REVISION_INVALID",
                        retryable=True,
                        message=(
                            "item-revision workers must return READY_FOR_CRITIC "
                            f"with a patch, got {payload.get('action')!r}"
                        ),
                        cause_type="ItemRevisionJoin",
                    ),
                )
            item_id = str(payload.get("item_id") or "")
            patch, error = materialize_item_patch(payload, item_id)
            if error:
                logger.warning(
                    "NODE_ITEM_REVISION_PATCH_INVALID node_run_id=%s item_id=%s "
                    "error=%s",
                    self._node_run_id,
                    item_id,
                    error,
                )
                return NodeResult(
                    self._node_run_id,
                    "FAILED",
                    results,
                    aggregate={"action": "FAIL"},
                    failure=FailureRecord(
                        failure_id=f"{self._node_run_id}:NODE_ITEM_REVISION_INVALID",
                        stage="node_join",
                        owner_component="agent_node_joiner",
                        task_id=self._task_id,
                        state=self._state,
                        sequence=self._sequence,
                        node_run_id=self._node_run_id,
                        worker_id=str(payload.get("worker_id") or ""),
                        effect_id=None,
                        error_code="NODE_ITEM_REVISION_INVALID",
                        retryable=True,
                        message=error,
                        cause_type="ItemRevisionJoin",
                    ),
                )
            patches.append(patch)
        merged, error = merge_item_patches(self._base_plan, patches)
        if error:
            return NodeResult(
                self._node_run_id,
                "FAILED",
                results,
                aggregate={"action": "FAIL"},
                failure=FailureRecord(
                    failure_id=f"{self._node_run_id}:ITEM_REVISION_MERGE_INVALID",
                    stage="node_join",
                    owner_component="agent_node_joiner",
                    task_id=self._task_id,
                    state=self._state,
                    sequence=self._sequence,
                    node_run_id=self._node_run_id,
                    worker_id=None,
                    effect_id=None,
                    error_code="NODE_ITEM_REVISION_INVALID",
                    retryable=False,
                    message=error,
                    cause_type="ItemRevisionJoin",
                ),
            )
        # The chef's consistency check: before the merged plan goes back to the
        # FSM, run the same shape and structural gates a Solver reply would
        # face — a worker patch must not introduce a dependency cycle or break
        # the graph shape.  Failure names the gate issues so the re-ask knows
        # exactly what to fix.
        integrity = structural_integrity_errors(merged)
        gate_issues = structural_gate(merged)
        problems = integrity + list(gate_issues)
        if problems:
            logger.warning(
                "NODE_ITEM_REVISION_STRUCTURE_INVALID node_run_id=%s issues=%s",
                self._node_run_id,
                problems[:6],
            )
            return NodeResult(
                self._node_run_id,
                "FAILED",
                results,
                aggregate={"action": "FAIL"},
                failure=FailureRecord(
                    failure_id=f"{self._node_run_id}:ITEM_REVISION_STRUCTURE_INVALID",
                    stage="node_join",
                    owner_component="agent_node_joiner",
                    task_id=self._task_id,
                    state=self._state,
                    sequence=self._sequence,
                    node_run_id=self._node_run_id,
                    worker_id=None,
                    effect_id=None,
                    error_code="NODE_ITEM_REVISION_INVALID",
                    retryable=True,
                    message="merged plan failed the structural gate: "
                    + ";".join(problems[:6]),
                    cause_type="ItemRevisionJoin",
                ),
            )
        logger.info(
            "NODE_ITEM_REVISION_MERGED node_run_id=%s items=%s",
            self._node_run_id,
            [patch.item_id for patch in patches],
        )
        return NodeResult(
            self._node_run_id,
            "SUCCEEDED",
            results,
            aggregate={
                "action": "READY_FOR_CRITIC",
                "plan": merged,
                "plan_hash": canonical_plan_hash(merged),
                "summary": (
                    "item-scoped revision merged for "
                    + ",".join(patch.item_id for patch in patches)
                ),
            },
        )


__all__ = ["AgentNodeJoiner", "AgentNodeWorkerRunner", "AgentWorkerRunner"]
