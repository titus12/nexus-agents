"""Runtime execution of one idempotent agent effect.

This module is the only place where agent transport, polling, admission, and
result artifacts meet.  It returns an ``EffectOutcome``; it never changes the
workflow snapshot.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
import time
from typing import Callable

from ..domain.decisions import EffectRequest
from ..domain.errors import (
    CONTRACT_REJECTED_EVENT,
    FailureRecord,
    LeaseLostError,
    OUTPUT_OVERFLOW_FEEDBACK,
    TransportError,
    UNSTRUCTURED_REPLY_EVENT,
    is_infrastructure_failure,
    is_output_overflow_reply,
)
from ..domain.policies.parallel import aggregate_zhongshu_workers
from ..domain.policies.menxia import (
    menxia_evidence_contribution,
    menxia_item_next_stage,
    menxia_wave_action,
)
from ..domain.policies.menxia_group import (
    MENXIA_GROUP_TARGETS,
    menxia_group_next_stage,
    menxia_group_wave_action,
)
from ..domain.menxia_doc import (
    MenxiaGroupDoc,
    menxia_approval_blockers,
    verify_group_reply,
)
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


def _sha256_hex(text: str) -> str:
    """Content hash used for the group document version chain."""

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _menxia_infra_failure(result: WorkerResult) -> bool:
    """True when a wave worker failed for an agent/transport reason."""


    failure = result.failure
    return (
        result.status == "FAILED"
        and failure is not None
        and bool(getattr(failure, "retryable", False))
        and is_infrastructure_failure(getattr(failure, "error_code", ""))
    )

# A run that failed after the agent delivered its reply is salvaged with a
# short bounded read: enough to cover comment-visibility lag around the
# terminal error, small enough to keep a genuine failure fast.
REMOTE_FAILURE_SALVAGE_POLLS = 3
REMOTE_FAILURE_SALVAGE_INTERVAL = 2.0

# Once the remote run is terminal no more work can happen, so a reply that is
# still absent can only be comment-visibility lag.  Reading is bounded by this
# grace instead of the full dispatch deadline (task-20260928-f13561: four
# terminal runs without a result each burned the whole 2000s window).
COMPLETED_RESULT_GRACE_SECONDS = 60.0

# Per-role dispatch timeouts (seconds).  Group revision and group review
# workers re-read the full prompt bundle and routinely exceed a single
# global cap (incident task-20260928-835a07: three deterministic 900.0s
# AGENT_TIMEOUT hits on one solver revision), while analyst hops stay well
# under it.  Roles are shared across phases (`_ROLE_BY_STATE` maps
# ZHONGSHU_* and MENXIA_* workers onto the same review-* roles), so the
# table is deliberately phase-agnostic: the same remote role does the same
# kind of work wherever it runs.  ZHONGSHU_DISPATCH_TIMEOUT_OVERRIDES
# (JSON role -> seconds) beats this table; an EffectRequest payload
# ``timeout_seconds`` beats both; the runner-level timeout stays the
# fallback for unknown roles.
ROLE_DISPATCH_TIMEOUTS: dict[str, float] = {
    "review-analyst": 900.0,
    "review-critic": 1200.0,
    "review-solver": 1800.0,
}


def resolve_dispatch_role_timeouts(*, include_defaults: bool = True) -> dict[str, float]:
    """Merge the role table with ZHONGSHU_DISPATCH_TIMEOUT_OVERRIDES.

    ``include_defaults=False`` keeps only the env-explicit roles: when the
    operator runs with a non-default global timeout the baked-in table stays
    out of the way, but an explicit env override is still honoured instead of
    being silently dropped alongside it.
    """

    merged = dict(ROLE_DISPATCH_TIMEOUTS) if include_defaults else {}
    raw = os.environ.get("ZHONGSHU_DISPATCH_TIMEOUT_OVERRIDES", "").strip()
    if not raw:
        return merged
    try:
        parsed = json.loads(raw)
    except ValueError:
        logger.warning("DISPATCH_TIMEOUT_OVERRIDES_INVALID overrides=%r", raw)
        return merged
    if not isinstance(parsed, dict):
        logger.warning("DISPATCH_TIMEOUT_OVERRIDES_INVALID overrides=%r", raw)
        return merged
    for role, seconds in parsed.items():
        try:
            merged[str(role)] = max(1.0, float(seconds))
        except (TypeError, ValueError):
            logger.warning(
                "DISPATCH_TIMEOUT_OVERRIDE_INVALID role=%s value=%r",
                role,
                seconds,
            )
    return merged


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
        role_timeout_seconds: Mapping[str, float] | None = None,
        agent_ids: Mapping[str, str] | None = None,
        completed_result_grace_seconds: float = COMPLETED_RESULT_GRACE_SECONDS,
    ) -> None:
        self._transport = transport
        self._admission = admission
        self._completed_result_grace = max(0.0, completed_result_grace_seconds)
        self._artifacts = artifacts
        self._role_decoder = role_decoder or (lambda payload: dict(payload))
        self._clock = clock
        self._utc_now = utc_now or (lambda: datetime.now(timezone.utc).isoformat())
        self._poll_interval = max(0.0, poll_interval)
        self._max_polls = max(1, max_polls)
        self._timeout_seconds = max(0.0, timeout_seconds)
        self._role_timeout_seconds = {
            str(role): max(1.0, float(seconds))
            for role, seconds in (role_timeout_seconds or {}).items()
        }
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

    def _effective_dispatch_limits(
        self, role: str, payload: Mapping[str, object]
    ) -> tuple[float, int]:
        """Resolve the ``(timeout, max_polls)`` pair for one dispatch.

        Precedence: the EffectRequest payload ``timeout_seconds`` beats the
        injected role table, which beats the runner-level timeout.  When the
        poll interval is positive, ``max_polls`` is re-derived from the
        effective timeout (deadline / interval + 2, the same formula the app
        wiring uses for the global pair) so a longer deadline is not cut
        short by poll-count exhaustion; the configured ``max_polls`` stays
        the floor.  With no sleep between polls the loop is deadline-bound
        and the configured ``max_polls`` is kept verbatim.
        """

        effective = self._timeout_seconds
        override_raw = payload.get("timeout_seconds")
        payload_override = None
        if override_raw is not None:
            try:
                payload_override = max(1.0, float(override_raw))
            except (TypeError, ValueError):
                logger.warning(
                    "DISPATCH_TIMEOUT_OVERRIDE_INVALID value=%r", override_raw
                )
        if payload_override is not None:
            effective = payload_override
        else:
            role_key = str(role or "").strip()
            if role_key in self._role_timeout_seconds:
                effective = self._role_timeout_seconds[role_key]
        if self._poll_interval > 0 and effective > 0:
            derived = int(effective / self._poll_interval) + 2
            return effective, max(self._max_polls, derived)
        return effective, self._max_polls

    def run_once(self, request: EffectRequest) -> EffectOutcome:
        payload = request.payload
        dispatch = self._dispatch_request(request, payload)
        effective_timeout, effective_max_polls = self._effective_dispatch_limits(
            dispatch.role, payload
        )
        deadline = self._clock() + effective_timeout
        deadline_at = request.deadline_at or datetime.fromtimestamp(
            datetime.now(timezone.utc).timestamp() + effective_timeout,
            timezone.utc,
        ).isoformat()
        logger.info(
            "AGENT_DISPATCH_START task_id=%s request_id=%s phase=%s role=%s "
            "timeout_seconds=%s deadline_at=%s max_polls=%s poll_interval=%s",
            request.task_id,
            dispatch.request_id,
            dispatch.phase,
            dispatch.role,
            effective_timeout,
            deadline_at,
            effective_max_polls,
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
            for _ in range(effective_max_polls):
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
        result_deadline = min(deadline, self._clock() + self._completed_result_grace)
        replies = self._poll_completed(poll, result_deadline)
        if not replies:
            logger.warning(
                "AGENT_RESULT_MISSING_AFTER_TERMINAL task_id=%s request_id=%s "
                "operation_id=%s grace_seconds=%s",
                request.task_id,
                poll.request_id,
                receipt.operation_id,
                self._completed_result_grace,
            )
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
        """Read the result of a terminal run until ``deadline`` (visibility lag only)."""
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
        "the required JSON result contract: the reply body must be exactly "
        "one complete JSON object (no prose, no Markdown, no code fences, "
        "no extra comments)",
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
    if action == _UNSTRUCTURED_REPLY_ACTION and is_output_overflow_reply(payload):
        # Oversized output was discarded by the platform wholesale: surface a
        # dedicated code so the re-ask (and the operator) see the real cause.
        return "AGENT_REPLY_OUTPUT_OVERFLOW", OUTPUT_OVERFLOW_FEEDBACK
    reason = str(payload.get("contract_rejection") or "")
    if not reason and action == _UNSTRUCTURED_REPLY_ACTION:
        reason = _json_parse_hint(payload)
    return error_code, f"{default_message}: {reason}" if reason else default_message


def _json_parse_hint(payload: Mapping[str, object]) -> str:
    """Tell the agent where its reply stopped being JSON and the usual cause."""

    error = str(payload.get("json_error") or "").strip()
    if not error or str(payload.get("json_status") or "") != "invalid":
        return ""
    position = payload.get("json_error_position")
    where = f" at char {position}" if position is not None else ""
    context = " ".join(str(payload.get("json_error_context") or "").split())
    near = f" near {context!r}" if context else ""
    return (
        f"JSON parse error: {error}{where}{near}. Escape every double quote "
        'inside a string value as \\" (or use 「」), write each backslash as '
        "\\\\ and each line break as \\n, and send raw JSON with no code fence"
    )


def _is_unstructured_reply(result: WorkerResult) -> bool:
    """True when a worker result is a transport placeholder, not a domain action."""

    return _rejected_reply_code(result) is not None


def _worker_rejection_entry(result: WorkerResult) -> tuple[str, str]:
    """One worker's rejection line for the wave failure's per-worker ledger."""

    worker_id = str(result.worker_id or "")
    if result.failure is not None:
        failure = result.failure
        return worker_id, f"{failure.error_code}: {failure.message}"
    rejected = _rejected_reply_code(result)
    if rejected is not None:
        error_code, message = rejected
        return worker_id, f"{error_code}: {message}"
    return worker_id, "worker delivered no usable reply"


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
        salvaged_worker_payloads: tuple[dict[str, object], ...] = (),
        evidence_requester: str = "",
        binding_contexts: Mapping[str, Mapping[str, object]] | None = None,
        menxia_stage_census: Mapping[str, object] | None = None,
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
        self._salvaged_worker_payloads: tuple[dict[str, object], ...] = tuple(
            dict(row)
            for row in (salvaged_worker_payloads or ())
            if isinstance(row, Mapping) and str(row.get("worker_id") or "")
        )
        self._evidence_requester = str(evidence_requester or "")
        self._binding_contexts: dict[str, dict[str, object]] = {
            str(worker_id): dict(ctx)
            for worker_id, ctx in (binding_contexts or {}).items()
            if isinstance(ctx, Mapping)
        }
        self._menxia_stage_census: dict[str, str] = {
            str(item_id): str(stage)
            for item_id, stage in (menxia_stage_census or {}).items()
        }

    def join(self, results: tuple[WorkerResult, ...]):
        if (
            self._state.startswith("MENXIA_")
            and self._dispatch_mode == "menxia_item_pipeline"
        ):
            menxia = self._join_menxia(results)
            if menxia is not None:
                return menxia
        if (
            self._state in MENXIA_GROUP_TARGETS
            and self._dispatch_mode == "menxia_group_pipeline"
        ):
            group = self._join_menxia_group(results)
            if group is not None:
                return group
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
            # Every failed worker's own rejection rides the wave failure, so
            # the retry re-asks each one with its own defect instead of
            # feeding all of them the first error found (task-20260929-dad75d).
            worker_rejections = tuple(
                _worker_rejection_entry(result) for result in failed
            )
            failure = next((result.failure for result in failed if result.failure), None)
            if failure is not None:
                failure = replace(failure, worker_rejections=worker_rejections)
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
                        worker_rejections=worker_rejections,
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
                        worker_rejections=worker_rejections,
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
            if self._analyst_salvage_enabled():
                salvaged_rows = self._analyst_salvage_rows(results, failed)
                if salvaged_rows:
                    aggregate["salvaged_worker_payloads"] = [
                        {**row, "salvage_revision_id": self._revision_id}
                        for row in salvaged_rows
                    ]
            if self._dispatch_mode == "group_revise":
                # Fix 4 (2026-09-28): a sibling worker's timeout must not
                # discard the groups whose patches are already fully valid.
                group_salvage = self._salvage_failed_group_revision(results)
                if group_salvage is not None:
                    aggregate.update(group_salvage)
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
        analyst_salvage = (
            self._salvaged_worker_payloads
            if self._analyst_salvage_enabled()
            else ()
        )
        if analyst_salvage:
            fresh_ids = {
                str(payload.get("worker_id") or "") for payload in worker_payloads
            }
            worker_payloads.extend(
                dict(row)
                for row in analyst_salvage
                if str(row.get("worker_id") or "") not in fresh_ids
            )
        if self._task_review_queue is not None:
            return self._join_task_review(results, worker_payloads, partial_reviews)

        if self._dispatch_mode == "group_review":
            return self._join_group_review(results, worker_payloads)

        if self._dispatch_mode == "group_revise":
            return self._join_group_revise(results, worker_payloads)

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
            # Escalations are wave-legitimate: one blocked/gated worker routes
            # the whole wave up (escalation-max) instead of killing it as an
            # action conflict (task-20260929-2f77e6).
            escalations = [action for action in ("BLOCKED", "HUMAN_GATE") if action in actions]
            if not escalations:
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
            actions = escalations
        if self._state.startswith("ZHONGSHU_"):
            try:
                aggregate = aggregate_zhongshu_workers(
                    self._task_id,
                    self._state,
                    self._revision_id,
                    self._plan_hash,
                    worker_payloads,
                    self._canonical_requirements or None,
                    evidence_requester=self._evidence_requester,
                )
                if analyst_salvage:
                    # The carried payloads were consumed by this fan-in;
                    # clear the salvage set so the next wave starts clean.
                    aggregate["salvaged_worker_payloads"] = []
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

    def _menxia_item_id(self, result: WorkerResult, payload: Mapping[str, object]) -> str:
        context = self._binding_contexts.get(result.worker_id) or {}
        item_id = str(context.get("item_id") or "") or str(payload.get("item_id") or "")
        return item_id.strip()

    def _join_menxia(self, results: tuple[WorkerResult, ...]) -> NodeResult | None:
        """Fan-in for the menxia stage-barrier wave (continue_and_block_group).

        Content-level worker failures park their item as ``BLOCKED`` instead of
        failing the whole node; infrastructure failures return ``None`` so the
        standard retry path takes over.  The aggregate carries the per-item
        results (folded into the menxia pipeline rows by the FSM) and the
        reduced wave action.
        """

        infra_failed = [
            result
            for result in results
            if result.status != "SUCCEEDED"
            and _menxia_infra_failure(result)
        ]
        if infra_failed:
            return None
        succeeded = [
            result
            for result in results
            if result.status == "SUCCEEDED"
            and isinstance(result.result_payload, Mapping)
        ]
        succeeded_ids = {id(result) for result in succeeded}
        content_failed = [
            result for result in results if id(result) not in succeeded_ids
        ]
        item_results: list[dict[str, object]] = []
        result_actions: dict[str, str] = {}
        evidence_contributions: list[dict[str, object]] = []
        for result in succeeded:
            payload = result.result_payload
            assert isinstance(payload, Mapping)
            item_id = self._menxia_item_id(result, payload)
            action = str(payload.get("action") or "").strip()
            if not item_id or not action:
                return None
            row: dict[str, object] = {"item_id": item_id, "action": action}
            fingerprint = str(payload.get("fingerprint") or "")
            if fingerprint:
                row["fingerprint"] = fingerprint
            if self._state == "MENXIA_ITEM_SOLVER":
                # Persist the proposal on the fold row so the item Critic can
                # weigh it instead of re-demanding a proposal the run made.
                proposal = payload.get("implementation_proposal")
                if isinstance(proposal, Mapping):
                    row["implementation_proposal"] = dict(proposal)
            elif self._state == "MENXIA_ITEM_ANALYST":
                # Carry the analyst's evidence into the shared packet; the
                # item Critic slices it from its dispatch context instead of
                # re-requesting evidence the wave just produced.
                contribution = menxia_evidence_contribution(
                    item_id, payload, str(result.worker_id or "")
                )
                if contribution is not None:
                    evidence_contributions.append(contribution)
            item_results.append(row)
            result_actions[item_id] = action
        for result in content_failed:
            context = self._binding_contexts.get(result.worker_id) or {}
            item_id = str(context.get("item_id") or "").strip()
            if not item_id:
                return None
            reason = str(
                (result.failure.error_code if result.failure else "")
                or "MENXIA_ITEM_BLOCKED"
            )
            item_results.append(
                {
                    "item_id": item_id,
                    "action": "BLOCKED",
                    "blocked_reason": reason,
                }
            )
            result_actions[item_id] = "BLOCKED"
        if not item_results:
            return None
        census = dict(self._menxia_stage_census)
        for item_id, action in result_actions.items():
            next_stage = menxia_item_next_stage(action)
            if next_stage is None:
                next_stage = census.get(item_id) or "SOLVING"
            census[item_id] = next_stage
        aggregate: dict[str, object] = {
            "action": menxia_wave_action(
                self._state, result_actions=result_actions, census=census
            ),
            "menxia_item_results": item_results,
            "worker_results": [
                dict(payload)
                for payload in (
                    result.result_payload
                    for result in succeeded
                    if isinstance(result.result_payload, Mapping)
                )
            ],
        }
        findings: list[dict[str, object]] = []
        for result in succeeded:
            payload = result.result_payload
            assert isinstance(payload, Mapping)
            raw_findings = payload.get("findings")
            if isinstance(raw_findings, list):
                findings.extend(
                    dict(finding)
                    for finding in raw_findings
                    if isinstance(finding, Mapping)
                )
        if findings:
            aggregate["findings"] = findings
        if evidence_contributions:
            aggregate["menxia_evidence"] = evidence_contributions
        return NodeResult(self._node_run_id, "SUCCEEDED", results, aggregate=aggregate)

    def _join_menxia_group(self, results: tuple[WorkerResult, ...]) -> NodeResult | None:
        """Fan-in for the group-pipeline wave (shared document model).

        Each group is one worker, so the mechanical document checks run here
        before anything folds: a reply that fails ``verify_group_reply`` is
        demoted to a ``BLOCKED`` row carrying the violations, never folded
        into the document chain.  Infrastructure failures return ``None`` so
        the standard retry path takes over.
        """

        infra_failed = [
            result
            for result in results
            if result.status != "SUCCEEDED"
            and _menxia_infra_failure(result)
        ]
        if infra_failed:
            return None
        succeeded = [
            result
            for result in results
            if result.status == "SUCCEEDED"
            and isinstance(result.result_payload, Mapping)
        ]
        succeeded_ids = {id(result) for result in succeeded}
        content_failed = [
            result for result in results if id(result) not in succeeded_ids
        ]
        role = {
            "MENXIA_GROUP_SOLVER": "solver",
            "MENXIA_GROUP_ANALYST": "analyst",
            "MENXIA_GROUP_CRITIC": "critic",
        }[self._state]
        group_rows: list[dict[str, object]] = []
        result_actions: dict[str, str] = {}
        for result in succeeded:
            payload = result.result_payload
            assert isinstance(payload, Mapping)
            context = self._binding_contexts.get(result.worker_id) or {}
            # The dispatch binding is the identity authority: the reply's
            # group_id is attacker/worker-claimed data, so the fold must key
            # on the bound group and reject replies that name another one.
            group_id = str(context.get("group_id") or "").strip()
            action = str(payload.get("action") or "").strip()
            if not group_id or not action:
                return None
            claimed_group_id = str(payload.get("group_id") or "").strip()
            if claimed_group_id and claimed_group_id != group_id:
                group_rows.append({
                    "group_id": group_id,
                    "action": "BLOCKED",
                    "blocked_reason": (
                        "MENXIA_GROUP_IDENTITY_MISMATCH: reply claims "
                        f"{claimed_group_id!r}, dispatch bound {group_id!r}"
                    ),
                })
                result_actions[group_id] = "BLOCKED"
                continue
            previous_markdown = str(context.get("doc_markdown") or "")
            previous = (
                MenxiaGroupDoc.parse(previous_markdown)
                if previous_markdown.strip() else None
            )
            violations = verify_group_reply(
                previous, role=role, reply=payload,
            )
            if violations:
                group_rows.append({
                    "group_id": group_id,
                    "action": "BLOCKED",
                    "blocked_reason": "MENXIA_GROUP_DOC_BREACH: "
                    + "; ".join(violations),
                })
                result_actions[group_id] = "BLOCKED"
                continue
            doc_markdown = str(payload.get("doc_markdown"))
            doc = MenxiaGroupDoc.parse(doc_markdown)
            approval_blockers: tuple[str, ...] = ()
            if role == "critic" and action == "APPROVE_GROUP":
                # The approval map must close against the group requirement
                # document (§9): no undecided markers, no incomplete
                # exception rows, no acceptance-map entries the requirement
                # never stated.  Legacy bindings without a requirement
                # markdown skip the map closure.
                approval_blockers = menxia_approval_blockers(
                    doc.body_markdown,
                    requirement_markdown=str(
                        context.get("requirement_markdown") or ""
                    ),
                )
            if (
                role == "critic"
                and action == "APPROVE_GROUP"
                and (
                    doc.open_blocking()
                    or doc.pending_rejections()
                    or approval_blockers
                )
            ):
                # The convergence rule gates the approval: a critic cannot
                # approve a group whose document still carries unresolved
                # blocking suggestions, unconfirmed rejections, or plan-body
                # blockers.  Fold the demand back to the solver instead of
                # advancing to the gate.
                action = "REQUEST_SOLVER_REVISION"
            row: dict[str, object] = {
                "group_id": group_id,
                "action": action,
                "doc_version": doc.version,
                "doc_hash": _sha256_hex(doc_markdown),
                "doc_markdown": doc_markdown,
                "open_count": doc.open_count(),
            }
            fingerprint = str(payload.get("fingerprint") or "")
            if fingerprint:
                row["fingerprint"] = fingerprint
            if role == "solver":
                # Doc mutations the fold needs, claimed by the worker and
                # already mechanically verified against the section rules.
                absorbed_ids = payload.get("absorbed_ids")
                rejected_ids = payload.get("rejected_ids")
                if isinstance(absorbed_ids, (list, tuple)):
                    row["absorbed_ids"] = list(absorbed_ids)
                if isinstance(rejected_ids, (list, tuple)):
                    row["rejected_ids"] = list(rejected_ids)
            group_rows.append(row)
            result_actions[group_id] = action
        for result in content_failed:
            context = self._binding_contexts.get(result.worker_id) or {}
            group_id = str(context.get("group_id") or "").strip()
            if not group_id:
                return None
            reason = str(
                (result.failure.error_code if result.failure else "")
                or "MENXIA_GROUP_BLOCKED"
            )
            group_rows.append(
                {
                    "group_id": group_id,
                    "action": "BLOCKED",
                    "blocked_reason": reason,
                }
            )
            result_actions[group_id] = "BLOCKED"
        if not group_rows:
            return None
        census = dict(self._menxia_stage_census)
        for group_id, action in result_actions.items():
            next_stage = menxia_group_next_stage(action)
            if next_stage is None:
                next_stage = census.get(group_id) or "SOLVING"
            census[group_id] = next_stage
        aggregate: dict[str, object] = {
            "action": menxia_group_wave_action(
                self._state, result_actions=result_actions, census=census
            ),
            "menxia_group_results": group_rows,
            "worker_results": [
                dict(payload)
                for payload in (
                    result.result_payload
                    for result in succeeded
                    if isinstance(result.result_payload, Mapping)
                )
            ],
        }
        return NodeResult(
            self._node_run_id, "SUCCEEDED", results, aggregate=aggregate
        )

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

    def _analyst_salvage_enabled(self) -> bool:
        """Analyst evidence waves are the only generic-mode salvage source."""

        return (
            self._task_review_queue is None
            and self._dispatch_mode != "item_revise"
            and self._state == "ZHONGSHU_ANALYST"
        )

    def _analyst_salvage_rows(
        self,
        results: tuple[WorkerResult, ...],
        failed: list[WorkerResult],
    ) -> list[dict[str, object]]:
        """Evidence payloads to carry across a failed Analyst wave.

        Fresh successful workers' payloads come first (they win over any
        carried row for the same worker id); rows salvaged by an earlier
        failed round and handed back through the retry dispatch fill in the
        slots that are still missing, so a repeatedly failing wave never
        loses the evidence it already paid for.
        """

        failed_ids = {result.worker_id for result in failed}
        rows: list[dict[str, object]] = []
        fresh_ids: set[str] = set()
        for result in results:
            if result.worker_id in failed_ids:
                continue
            payload = result.result_payload
            if not isinstance(payload, Mapping):
                continue
            fresh_ids.add(result.worker_id)
            rows.append({**dict(payload), "worker_id": result.worker_id})
        for row in self._salvaged_worker_payloads:
            if str(row.get("worker_id") or "") in fresh_ids:
                continue
            rows.append(dict(row))
        return rows

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

    def _join_group_review(self, results, worker_payloads):
        """Aggregate one group verdict per binding into the wave report.

        The wave action is the escalation-max of the per-group verdicts
        (BLOCKED > HUMAN_GATE > REQUEST_ANALYST_EVIDENCE > revision > approve)
        so the FSM edge keeps its legacy meaning while ``group_reviews``
        carries each group's own verdict for the fold.
        """

        from ..contracts.zhongshu_critic import (
            GROUP_MODE_ACTIONS,
            group_finding_scope,
            normalize_group_action,
        )

        member_ids_by_group = {
            str(context.get("group_id") or ""): [
                str(value) for value in (context.get("member_ids") or ())
            ]
            for context in self._binding_contexts.values()
        }
        group_reviews: list[dict[str, object]] = []
        findings: list[dict[str, object]] = []
        rejected: list[str] = []
        group_by_worker = {
            worker_id: str(context.get("group_id") or "")
            for worker_id, context in self._binding_contexts.items()
        }
        for payload in worker_payloads:
            group_id = (
                str(payload.get("group_id") or "").strip()
                or group_by_worker.get(str(payload.get("worker_id") or ""), "")
            )
            raw_action = str(payload.get("action") or "")
            action = normalize_group_action(raw_action)
            if not group_id or action not in GROUP_MODE_ACTIONS:
                rejected.append(
                    f"GROUP_REVIEW_RESULT_INVALID:{group_id or '?'}:{raw_action}"
                )
                continue
            scoped: list[dict[str, object]] = []
            unscoped: list[str] = []
            for raw in payload.get("findings") or ():
                if not isinstance(raw, Mapping):
                    continue
                entry = dict(raw)
                item_id, error = group_finding_scope(
                    entry, member_ids_by_group.get(group_id, ())
                )
                if error:
                    unscoped.append(error)
                    continue
                entry["item_id"] = item_id
                entry.setdefault("group_id", group_id)
                scoped.append(entry)
            if unscoped:
                rejected.extend(unscoped)
                continue
            responses = [
                dict(entry)
                for entry in payload.get("finding_responses") or ()
                if isinstance(entry, Mapping)
            ]
            group_reviews.append(
                {
                    "group_id": group_id,
                    "action": action,
                    "member_ids": member_ids_by_group.get(group_id, []),
                    "findings": scoped,
                    "finding_responses": responses,
                    "reviewed_plan_hash": str(payload.get("reviewed_plan_hash") or ""),
                }
            )
            findings.extend(scoped)
        if rejected:
            logger.warning(
                "NODE_GROUP_REVIEW_RESULT_INVALID node_run_id=%s rejected=%s",
                self._node_run_id,
                len(rejected),
            )
            return NodeResult(
                self._node_run_id,
                "FAILED",
                results,
                aggregate={
                    "action": "FAIL",
                    "group_reviews": group_reviews,
                    "findings": findings,
                },
                failure=FailureRecord(
                    failure_id=f"{self._node_run_id}:NODE_GROUP_REVIEW_RESULT_INVALID",
                    stage="node_join",
                    owner_component="zhongshu_group_review_fan_in",
                    task_id=self._task_id,
                    state=self._state,
                    sequence=self._sequence,
                    node_run_id=self._node_run_id,
                    worker_id=None,
                    effect_id=None,
                    error_code="NODE_GROUP_REVIEW_RESULT_INVALID",
                    retryable=True,
                    message="; ".join(rejected[:6]),
                    cause_type="FanInValidation",
                ),
            )
        actions = [str(row["action"]) for row in group_reviews]
        if "BLOCKED" in actions:
            overall = "BLOCKED"
        elif "HUMAN_GATE" in actions:
            overall = "HUMAN_GATE"
        elif "REQUEST_ANALYST_EVIDENCE" in actions:
            overall = "REQUEST_ANALYST_EVIDENCE"
        elif "REVISE_GROUP" in actions:
            overall = "REQUEST_SOLVER_REVISION"
        else:
            overall = "APPROVE_CRITIC"
        return NodeResult(
            self._node_run_id,
            "SUCCEEDED",
            results,
            aggregate={
                "action": overall,
                "group_reviews": group_reviews,
                "findings": findings,
                "finding_responses": [
                    entry
                    for row in group_reviews
                    for entry in row.get("finding_responses") or ()
                ],
                "group_review_mode": True,
                "group_review_complete": True,
                "reviewed_plan_hash": next(
                    (
                        str(row.get("reviewed_plan_hash") or "")
                        for row in group_reviews
                        if row.get("reviewed_plan_hash")
                    ),
                    "",
                ),
            },
        )

    def _join_group_revise(self, results, worker_payloads):
        """Merge one group-revision patch per affected group (parallel wave).

        Each worker owns exactly one group and returns its patched member
        items plus its requirement document; the joiner freezes everything
        outside the finding-owning member set (``merge_group_revision_items``)
        and everything outside the patched groups (``carry_forward_group_docs``).
        """

        from ..domain.policies.item_revise import merge_item_patches  # noqa: F401  (keeps import surface stable)
        from ..domain.policies.solver_plan import (
            carry_forward_group_docs,
            structural_integrity_errors,
        )
        from ..domain.zhongshu_doc import project_acceptance_signals
        from ..zhongshu_parallel import canonical_plan_hash
        from ..zhongshu_review_queue import structural_gate

        merged = self._base_plan
        if not isinstance(merged, Mapping):
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
                    message="group-revision wave needs the reviewed plan",
                    cause_type="GroupRevisionJoin",
                ),
            )
        group_docs: list[object] = []
        finding_resolutions: list[object] = []
        patched_groups: list[str] = []
        patch_errors: list[tuple[str, str]] = []
        merged, group_docs, finding_resolutions, patched_groups, patch_errors = (
            self._fold_group_revision_payloads(worker_payloads, merged)
        )
        if patch_errors:
            # A group-revision wave is one patch per group: a patch the joiner
            # refuses must not discard the patches that were valid (live run
            # task-20260927-616863 re-dispatched the healthy group in all four
            # waves because one group kept a mislabeled group_id).  Fold the
            # fully valid groups into the FAIL aggregate so the retry wave
            # re-dispatches only the groups still missing.
            salvage = self._salvage_group_revision(
                merged,
                group_docs=group_docs,
                finding_resolutions=finding_resolutions,
                patched_groups=patched_groups,
            )
            aggregate: dict[str, object] = {"action": "FAIL"}
            if salvage is not None:
                aggregate.update(salvage)
            failed_worker, failed_message = patch_errors[0]
            return NodeResult(
                self._node_run_id,
                "FAILED",
                results,
                aggregate=aggregate,
                failure=FailureRecord(
                    failure_id=f"{self._node_run_id}:NODE_ITEM_REVISION_INVALID",
                    stage="node_join",
                    owner_component="agent_node_joiner",
                    task_id=self._task_id,
                    state=self._state,
                    sequence=self._sequence,
                    node_run_id=self._node_run_id,
                    worker_id=failed_worker or None,
                    effect_id=None,
                    error_code="NODE_ITEM_REVISION_INVALID",
                    retryable=True,
                    message=failed_message,
                    cause_type="GroupRevisionJoin",
                ),
            )
        editable_groups = sorted(set(patched_groups))
        previous_rows: tuple = ()
        previous = self._previous_review
        if isinstance(previous, Mapping):
            from ..domain.context import ZhongshuGroupState

            previous_rows = tuple(
                ZhongshuGroupState.from_dict(row)
                for row in (previous.get("zhongshu_groups") or ())
                if isinstance(row, Mapping)
            )
        folded_docs, doc_error = carry_forward_group_docs(
            group_docs,
            previous_rows,
            editable_group_ids=editable_groups,
            required_group_ids=editable_groups,
            review=self._previous_review,
            plan=merged,
        )
        if doc_error:
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
                    worker_id=None,
                    effect_id=None,
                    error_code="NODE_ITEM_REVISION_INVALID",
                    retryable=True,
                    message=doc_error,
                    cause_type="GroupRevisionJoin",
                ),
            )
        # §8 stays the single author of acceptance_signals: the projection
        # runs over the merged plan and the authoritative documents before
        # the structural gate judges the projected signals.
        doc_map = {
            str(getattr(row, "group_id", "") or ""): str(
                getattr(row, "doc_markdown", "") or ""
            )
            for row in previous_rows
        }
        for group_id, row in folded_docs.items():
            doc_map[str(group_id)] = str(row.get("markdown") or "")
        projected, projection_error = project_acceptance_signals(merged, doc_map)
        if projection_error:
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
                    worker_id=None,
                    effect_id=None,
                    error_code="NODE_ITEM_REVISION_INVALID",
                    retryable=True,
                    message=projection_error,
                    cause_type="GroupRevisionJoin",
                ),
            )
        merged = projected
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
                    failure_id=f"{self._node_run_id}:NODE_ITEM_REVISION_STRUCTURE_INVALID",
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
                    cause_type="GroupRevisionJoin",
                ),
            )
        logger.info(
            "NODE_GROUP_REVISION_MERGED node_run_id=%s groups=%s",
            self._node_run_id,
            editable_groups,
        )
        return NodeResult(
            self._node_run_id,
            "SUCCEEDED",
            results,
            aggregate={
                "action": "READY_FOR_CRITIC",
                "plan": merged,
                "plan_hash": canonical_plan_hash(merged),
                "finding_resolutions": finding_resolutions,
                "group_docs": group_docs or None,
                "patched_group_ids": editable_groups,
                "summary": (
                    "group-scoped revision merged for "
                    + ",".join(editable_groups)
                ),
            },
        )

    def _salvage_group_revision(
        self,
        merged,
        *,
        group_docs: list[object],
        finding_resolutions: list[object],
        patched_groups: list[str],
    ) -> dict[str, object] | None:
        """Fold the fully valid groups' revision work into a FAIL aggregate.

        Only a group whose patch AND requirement document survive the full
        post-merge pipeline (doc fold, §8 projection, structural gates) is
        salvaged: folding a plan patch without its document would diverge the
        item's acceptance_signals from §8 and the next projection would
        silently undo the patch.  When the pipeline refuses, the wave fails
        whole (legacy behaviour) and the retry re-dispatches every group.
        """

        if not patched_groups:
            return None
        from ..domain.policies.solver_plan import (
            carry_forward_group_docs,
            structural_integrity_errors,
        )
        from ..domain.zhongshu_doc import project_acceptance_signals
        from ..zhongshu_parallel import canonical_plan_hash
        from ..zhongshu_review_queue import structural_gate

        previous_rows: tuple = ()
        previous = self._previous_review
        if isinstance(previous, Mapping):
            from ..domain.context import ZhongshuGroupState

            previous_rows = tuple(
                ZhongshuGroupState.from_dict(row)
                for row in (previous.get("zhongshu_groups") or ())
                if isinstance(row, Mapping)
            )
        editable_groups = sorted(set(patched_groups))
        folded_docs, doc_error = carry_forward_group_docs(
            group_docs,
            previous_rows,
            editable_group_ids=editable_groups,
            required_group_ids=editable_groups,
            review=self._previous_review,
            plan=merged,
        )
        if doc_error:
            logger.warning(
                "NODE_GROUP_REVISION_SALVAGE_SKIPPED node_run_id=%s reason=%s",
                self._node_run_id,
                doc_error,
            )
            return None
        doc_map = {
            str(getattr(row, "group_id", "") or ""): str(
                getattr(row, "doc_markdown", "") or ""
            )
            for row in previous_rows
        }
        for group_id, row in folded_docs.items():
            doc_map[str(group_id)] = str(row.get("markdown") or "")
        projected, projection_error = project_acceptance_signals(merged, doc_map)
        if projection_error:
            logger.warning(
                "NODE_GROUP_REVISION_SALVAGE_SKIPPED node_run_id=%s reason=%s",
                self._node_run_id,
                projection_error,
            )
            return None
        problems = list(structural_integrity_errors(projected)) + list(
            structural_gate(projected)
        )
        if problems:
            logger.warning(
                "NODE_GROUP_REVISION_SALVAGE_SKIPPED node_run_id=%s issues=%s",
                self._node_run_id,
                problems[:6],
            )
            return None
        salvage_rows = [
            {"group_id": group_id, **dict(row)}
            for group_id, row in sorted(folded_docs.items())
        ]
        logger.info(
            "NODE_GROUP_REVISION_SALVAGED node_run_id=%s groups=%s",
            self._node_run_id,
            editable_groups,
        )
        return {
            "plan": projected,
            "plan_hash": canonical_plan_hash(projected),
            "finding_resolutions": list(finding_resolutions),
            "salvaged_group_ids": editable_groups,
            "salvaged_group_rows": salvage_rows,
        }

    def _fold_group_revision_payloads(
        self,
        worker_payloads: list[dict[str, object]],
        merged: object,
    ) -> tuple[
        object,
        list[object],
        list[object],
        list[str],
        list[tuple[str, str]],
    ]:
        """Apply one group patch per worker payload to the running plan.

        Returns ``(merged, group_docs, finding_resolutions, patched_groups,
        patch_errors)``.  A refused patch never aborts the loop: the caller
        decides whether the valid sibling groups are salvageable.
        """

        from ..domain.policies.solver_plan import merge_group_revision_items

        group_docs: list[object] = []
        finding_resolutions: list[object] = []
        patched_groups: list[str] = []
        patch_errors: list[tuple[str, str]] = []
        group_by_worker = {
            worker_id: str(context.get("group_id") or "")
            for worker_id, context in self._binding_contexts.items()
        }
        for payload in worker_payloads:
            worker_id = str(payload.get("worker_id") or "")
            group_id = (
                str(payload.get("group_id") or "").strip()
                or group_by_worker.get(worker_id, "")
            )
            if str(payload.get("action") or "") != "READY_FOR_CRITIC":
                patch_errors.append(
                    (
                        worker_id,
                        f"{group_id or 'group'}: group-revision workers must "
                        "return READY_FOR_CRITIC",
                    )
                )
                continue
            editable: set[str] = set()
            for binding in self._binding_contexts.values():
                if str(binding.get("group_id") or "") != group_id:
                    continue
                editable |= {
                    str(value).strip()
                    for value in (binding.get("editable_item_ids") or ())
                    if str(value).strip()
                }
            plan = payload.get("plan")
            raw_items = (
                plan.get("items")
                if isinstance(plan, Mapping)
                else payload.get("items")
            )
            merged_plan, error = merge_group_revision_items(
                merged,
                group_id=group_id,
                patched_items=raw_items if isinstance(raw_items, list) else [],
                editable_item_ids=editable,
            )
            if error:
                logger.warning(
                    "NODE_GROUP_REVISION_PATCH_INVALID node_run_id=%s group_id=%s "
                    "error=%s",
                    self._node_run_id,
                    group_id,
                    error,
                )
                patch_errors.append((worker_id, error))
                continue
            merged = merged_plan
            for entry in payload.get("group_docs") or ():
                if isinstance(entry, dict):
                    group_docs.append(entry)
            for entry in payload.get("finding_resolutions") or ():
                if isinstance(entry, dict):
                    finding_resolutions.append(entry)
            if group_id:
                patched_groups.append(group_id)
        return merged, group_docs, finding_resolutions, patched_groups, patch_errors

    def _salvage_failed_group_revision(
        self,
        results: tuple[WorkerResult, ...],
    ) -> dict[str, object] | None:
        """Carry a group-revision wave's finished groups across a failure.

        One sibling's transport-level failure (timeout, agent crash) must not
        discard the groups whose patches the joiner can still fully validate:
        the same per-group fold and post-merge pipeline as the fold-phase
        salvage runs over the surviving payloads, and the FAIL aggregate
        carries the result so the retry re-dispatches only the groups still
        missing (live run task-20260928-835a07 re-paid a finished group's
        whole revision after a sibling timed out).
        """

        if not isinstance(self._base_plan, Mapping):
            return None
        worker_payloads: list[dict[str, object]] = []
        for result in results:
            payload = result.result_payload
            if result.status != "SUCCEEDED" or not isinstance(payload, Mapping):
                continue
            if _is_unstructured_reply(result):
                continue
            worker_payloads.append({**dict(payload), "worker_id": result.worker_id})
        if not worker_payloads:
            return None
        merged, group_docs, finding_resolutions, patched_groups, _errors = (
            self._fold_group_revision_payloads(worker_payloads, self._base_plan)
        )
        # Patch errors here mean that group's own work was invalid; the group
        # simply stays out of the salvage and is re-dispatched by the retry.
        return self._salvage_group_revision(
            merged,
            group_docs=group_docs,
            finding_resolutions=finding_resolutions,
            patched_groups=patched_groups,
        )

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
        # A patch that rewrites acceptance signals changes the owning group's
        # document closure (§8), so the worker may re-author that document;
        # carry the submissions (and which groups were patched) through to
        # the FSM fold.
        group_docs: list[object] = []
        for payload in worker_payloads:
            docs = payload.get("group_docs")
            if isinstance(docs, list):
                group_docs.extend(doc for doc in docs if isinstance(doc, dict))
        patched_group_ids = sorted({
            str((patch.item or {}).get("group_id") or "").strip()
            for patch in patches
            if isinstance(patch.item, dict)
            and str((patch.item or {}).get("group_id") or "").strip()
        })
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
                "group_docs": group_docs or None,
                "patched_group_ids": patched_group_ids,
            },
        )


__all__ = ["AgentNodeJoiner", "AgentNodeWorkerRunner", "AgentWorkerRunner"]
