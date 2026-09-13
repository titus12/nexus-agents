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
from ..domain.errors import FailureRecord, LeaseLostError, TransportError
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
                    failure = self._remote_failure(request, status, receipt)
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
        payload = {
            "issue_id": binding.issue_id or context.issue_id,
            "request_id": binding.request_id,
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
        if binding.dispatch_context:
            payload["dispatch_context"] = dict(binding.dispatch_context)
        if binding.fanout_parent_id:
            payload["fanout_parent_id"] = binding.fanout_parent_id
            payload["fanout_title"] = binding.fanout_title
        logger.info(
            "AGENT_WORKER_BOUND task_id=%s node_run_id=%s worker_id=%s "
            "group_id=%s item_id=%s dispatch_mode=%s prompt_chars=%s",
            context.task_id,
            context.node_run_id,
            binding.worker_id,
            payload.get("group_id") or "",
            payload.get("item_id") or "",
            str(binding.dispatch_context.get("zhongshu_dispatch_mode") or ""),
            len(str(payload.get("prompt_ref") or "")),
        )
        request = EffectRequest(
            effect_id=f"worker:{context.node_run_id}:{binding.worker_id}",
            effect_type="agent_dispatch",
            task_id=context.task_id,
            idempotency_key=binding.request_id,
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


_UNSTRUCTURED_REPLY_ACTION = "__UNSTRUCTURED_REPLY__"


def _is_unstructured_reply(result: WorkerResult) -> bool:
    """True when a worker result is the transport's non-JSON placeholder.

    Multica promotes a lone non-JSON reply to a synthetic result whose
    ``action`` is ``__UNSTRUCTURED_REPLY__`` (see ``adapters.py``).  That
    sentinel is not a domain action: the worker failed to produce the required
    JSON contract.  Letting it into the fan-in action set made one unstructured
    worker collide with its peers' real actions and produced a non-retryable
    ``NODE_ACTION_CONFLICT``.  It is handled here as a retryable worker failure
    instead.
    """

    payload = result.result_payload
    if not isinstance(payload, Mapping):
        return False
    return str(payload.get("action") or "") == _UNSTRUCTURED_REPLY_ACTION


_TASK_REVIEW_ACTIONS = {"TASK_APPROVED", "TASK_CHANGES_REQUIRED"}


def _partial_task_reviews(
    results: tuple[WorkerResult, ...],
    failed: list[WorkerResult],
) -> list[dict[str, object]]:
    """Per-task verdicts from the workers that succeeded despite a node failure.

    Only the ledger-relevant identity/verdict fields are kept; re-reviewing an
    approved task is the expensive part we want a retry to skip.
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
        item_id = str(payload.get("item_id") or "")
        if action not in _TASK_REVIEW_ACTIONS or not item_id:
            continue
        reviews.append(
            {
                "item_id": item_id,
                "action": action,
                "reviewed_task_hash": payload.get("reviewed_task_hash"),
                "reviewed_dependency_hash": payload.get("reviewed_dependency_hash"),
            }
        )
    return reviews


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
    ) -> None:
        self._node_run_id = node_run_id
        self._task_id = task_id
        self._state = state
        self._sequence = sequence
        self._revision_id = revision_id
        self._plan_hash = plan_hash
        self._task_review_queue = task_review_queue
        self._canonical_requirements = canonical_requirements

    def join(self, results: tuple[WorkerResult, ...]):
        failed = [
            result
            for result in results
            if result.status != "SUCCEEDED" or _is_unstructured_reply(result)
        ]
        if failed:
            failure = next((result.failure for result in failed if result.failure), None)
            if failure is None:
                unstructured = next(
                    (result for result in failed if _is_unstructured_reply(result)),
                    None,
                )
                if unstructured is not None:
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
                        error_code="AGENT_REPLY_UNSTRUCTURED",
                        retryable=True,
                        message=(
                            "agent worker returned an unstructured reply instead of "
                            "the required JSON result contract"
                        ),
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
            if self._task_review_queue is not None:
                # Persist the verdicts we did obtain so a retry only has to
                # re-dispatch the tasks that are still missing (see
                # _select_review_jobs).  The FSM folds these into the review
                # ledger even on the FAIL -> RETRY path.
                partial = _partial_task_reviews(results, failed)
                if partial:
                    aggregate["task_reviews"] = partial
            return NodeResult(
                self._node_run_id,
                "FAILED",
                results,
                aggregate=aggregate,
                failure=failure,
            )

        worker_payloads = [
            {**dict(payload), "worker_id": result.worker_id}
            for result in results
            for payload in (result.result_payload,)
            if isinstance(payload, Mapping)
        ]
        if self._task_review_queue is not None:
            return self._join_task_review(results, worker_payloads)

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

    def _join_task_review(self, results, worker_payloads):
        from ..zhongshu_review import aggregate_task_review_results

        try:
            report = aggregate_task_review_results(
                self._revision_id,
                self._plan_hash,
                self._task_review_queue,
                worker_payloads,
            )
        except ValueError as error:
            return NodeResult(
                self._node_run_id,
                "FAILED",
                results,
                aggregate={"action": "FAIL"},
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
            "completed=%s/%s affected_items=%s active_blockers=%s",
            self._node_run_id,
            report.get("action"),
            report.get("task_review_complete"),
            report.get("completed_task_count"),
            report.get("total_task_count"),
            report.get("affected_item_ids"),
            report.get("active_p0_p1_finding_ids"),
        )
        return NodeResult(self._node_run_id, "SUCCEEDED", results, aggregate=report)


__all__ = ["AgentNodeJoiner", "AgentNodeWorkerRunner", "AgentWorkerRunner"]
