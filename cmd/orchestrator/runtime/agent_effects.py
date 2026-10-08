"""Runtime execution of one idempotent agent effect.

This module is the only place where agent transport, polling, admission, and
result artifacts meet.  It returns an ``EffectOutcome``; it never changes the
workflow snapshot.
"""
from __future__ import annotations
from collections import Counter
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
import time
from typing import Callable
from ..contracts.analyst_gate import analyst_questions
from ..domain.decisions import EffectRequest
from ..domain.errors import CONTRACT_REJECTED_EVENT, FailureRecord, DomainError, LeaseLostError, OUTPUT_OVERFLOW_FEEDBACK, TransportError, UNSTRUCTURED_REPLY_EVENT, is_infrastructure_failure, is_output_overflow_reply
from ..domain.policies.menxia import menxia_evidence_contribution, menxia_item_next_stage, menxia_wave_action
from ..domain.policies.menxia_group import MENXIA_GROUP_TARGETS, menxia_group_next_stage, menxia_group_wave_action
from ..domain.menxia_doc import MenxiaGroupDoc, LedgerEntry, menxia_approval_blockers, verify_group_reply, solver_reply_repairable
from ..transport import RawTransportReply, ReplyBinding, ReplyNormalizer
from .effects import EffectOutcome
from .ports import AdmissionKey, AgentDispatchRequest, AgentTransportPort, ArtifactInput, ArtifactPort, DispatchReceipt, PollRequest, RemoteRunStatus, ConcurrencyAdmissionPort
from .nodes import NodeContext, WorkerBinding, WorkerResult
from .nodes import NodeResult
logger = logging.getLogger('review_orchestrator_fsm')

def _sha256_hex(text: str) -> str:
    """Content hash used for the group document version chain."""
    return hashlib.sha256(text.encode('utf-8')).hexdigest()

def _menxia_infra_failure(result: WorkerResult) -> bool:
    """True when a wave worker failed for an agent/transport reason."""
    failure = result.failure
    return result.status == 'FAILED' and failure is not None and bool(getattr(failure, 'retryable', False)) and is_infrastructure_failure(getattr(failure, 'error_code', ''))
REMOTE_FAILURE_SALVAGE_POLLS = 3
REMOTE_FAILURE_SALVAGE_INTERVAL = 2.0
COMPLETED_RESULT_GRACE_SECONDS = 60.0
ROLE_DISPATCH_TIMEOUTS: dict[str, float] = {'review-analyst': 1800.0, 'review-critic': 1200.0, 'review-solver': 1800.0}

def resolve_dispatch_role_timeouts(*, include_defaults: bool=True) -> dict[str, float]:
    """Merge the role table with ZHONGSHU_DISPATCH_TIMEOUT_OVERRIDES.

    ``include_defaults=False`` keeps only the env-explicit roles: when the
    operator runs with a non-default global timeout the baked-in table stays
    out of the way, but an explicit env override is still honoured instead of
    being silently dropped alongside it.
    """
    merged = dict(ROLE_DISPATCH_TIMEOUTS) if include_defaults else {}
    raw = os.environ.get('ZHONGSHU_DISPATCH_TIMEOUT_OVERRIDES', '').strip()
    if not raw:
        return merged
    try:
        parsed = json.loads(raw)
    except ValueError:
        logger.warning('DISPATCH_TIMEOUT_OVERRIDES_INVALID overrides=%r', raw)
        return merged
    if not isinstance(parsed, dict):
        logger.warning('DISPATCH_TIMEOUT_OVERRIDES_INVALID overrides=%r', raw)
        return merged
    for role, seconds in parsed.items():
        try:
            merged[str(role)] = max(1.0, float(seconds))
        except (TypeError, ValueError):
            logger.warning('DISPATCH_TIMEOUT_OVERRIDE_INVALID role=%s value=%r', role, seconds)
    return merged

class AgentWorkerRunner:
    """Dispatch, observe, normalize, and archive one agent execution."""

    def __init__(self, transport: AgentTransportPort, *, admission: ConcurrencyAdmissionPort | None=None, artifacts: ArtifactPort | None=None, role_decoder: Callable[[Mapping[str, object]], object] | None=None, clock: Callable[[], float]=time.monotonic, utc_now: Callable[[], str] | None=None, poll_interval: float=0.0, max_polls: int=30, timeout_seconds: float=900.0, role_timeout_seconds: Mapping[str, float] | None=None, agent_ids: Mapping[str, str] | None=None, completed_result_grace_seconds: float=COMPLETED_RESULT_GRACE_SECONDS) -> None:
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
        self._role_timeout_seconds = {str(role): max(1.0, float(seconds)) for role, seconds in (role_timeout_seconds or {}).items()}
        self._agent_ids = dict(agent_ids or {})
        self._normalizer = ReplyNormalizer()

    def resolve_agent_pool(self, target_state: str) -> tuple[str, ...]:
        """Return the configured agent identities for a role, in order."""
        configured = str(self._agent_ids.get(target_state) or '').strip()
        return tuple((item.strip() for item in configured.split(',') if item.strip()))

    def agent_pool_summary(self) -> dict[str, int]:
        """Expose how many distinct identities each role can run concurrently."""
        return {state: len(self.resolve_agent_pool(state)) or 1 for state in self._agent_ids}

    def resolve_worker_agent(self, target_state: str, worker_id: str) -> str:
        """Return the agent identity a worker binds to, mirroring dispatch."""
        configured = self.resolve_agent_pool(target_state)
        if not configured:
            return ''
        try:
            worker_index = int(str(worker_id).rsplit('-', 1)[-1]) - 1
        except ValueError:
            worker_index = 0
        return configured[worker_index % len(configured)]

    def parallel_width(self, target_state: str, bindings: tuple[WorkerBinding, ...], issue_id: str) -> int:
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
            target_ids = tuple((configured_ids[index % len(configured_ids)] for index in range(len(bindings))))
        else:
            target_ids = tuple((binding.agent_id for binding in bindings))
        target_issues = tuple((str(binding.issue_id or issue_id or '') for binding in bindings))
        distinct_targets = {(target_issues[index], str(target_ids[index] or '')) for index in range(len(bindings))}
        width = max(1, min(len(bindings), len(distinct_targets)))
        logger.info('PARALLEL_WIDTH target_state=%s issue_id=%s bindings=%s configured_pool=%s distinct_targets=%s width=%s waves=%s', target_state, issue_id, len(bindings), len(configured_ids) or len(target_ids), len(distinct_targets), width, -(-len(bindings) // width))
        return width

    def _effective_dispatch_limits(self, role: str, payload: Mapping[str, object]) -> tuple[float, int]:
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
        override_raw = payload.get('timeout_seconds')
        payload_override = None
        if override_raw is not None:
            try:
                payload_override = max(1.0, float(override_raw))
            except (TypeError, ValueError):
                logger.warning('DISPATCH_TIMEOUT_OVERRIDE_INVALID value=%r', override_raw)
        if payload_override is not None:
            effective = payload_override
        else:
            role_key = str(role or '').strip()
            if role_key in self._role_timeout_seconds:
                effective = self._role_timeout_seconds[role_key]
        if self._poll_interval > 0 and effective > 0:
            derived = int(effective / self._poll_interval) + 2
            return (effective, max(self._max_polls, derived))
        return (effective, self._max_polls)

    def run_once(self, request: EffectRequest) -> EffectOutcome:
        payload = request.payload
        dispatch = self._dispatch_request(request, payload)
        effective_timeout, effective_max_polls = self._effective_dispatch_limits(dispatch.role, payload)
        deadline = self._clock() + effective_timeout
        deadline_at = request.deadline_at or datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() + effective_timeout, timezone.utc).isoformat()
        logger.info('AGENT_DISPATCH_START task_id=%s request_id=%s phase=%s role=%s timeout_seconds=%s deadline_at=%s max_polls=%s poll_interval=%s', request.task_id, dispatch.request_id, dispatch.phase, dispatch.role, effective_timeout, deadline_at, effective_max_polls, self._poll_interval)
        lease_id: str | None = None
        if self._admission is not None:
            worker_id = str(payload.get('worker_id') or dispatch.agent_id)
            external_target_key = f'{dispatch.issue_id}:{dispatch.agent_id}' if dispatch.issue_id else ''
            if payload.get('fanout_parent_id') and external_target_key:
                external_target_key = f'{external_target_key}:{worker_id}'
            key = AdmissionKey(task_id=request.task_id, phase=dispatch.phase, worker_id=worker_id, revision_id=str(payload.get('revision_id') or request.idempotency_key), external_target_key=external_target_key, role=dispatch.role)
            receipt = self._admission.acquire(key, deadline)
            if receipt is None:
                raise TransportError('agent concurrency admission deadline reached')
            lease_id = receipt.lease_id
        try:
            recover = getattr(self._transport, 'recover_completed', None)
            if callable(recover):
                recovered = recover(dispatch)
                if recovered is not None:
                    logger.info('AGENT_RESULT_RECOVERED task_id=%s request_id=%s phase=%s role=%s', dispatch.task_id, dispatch.request_id, dispatch.phase, dispatch.role)
                    receipt = DispatchReceipt(operation_id=dispatch.idempotency_key, external_message_id='', confirmed=True, request_id=dispatch.request_id)
                    return self._complete_with_reply(request, dispatch, receipt, recovered, deadline_at)
            finder = getattr(self._transport, 'find_existing', None)
            receipt = finder(dispatch) if callable(finder) else None
            if receipt is None:
                receipt = self._transport.dispatch(dispatch)
            if not isinstance(receipt, DispatchReceipt) or not receipt.confirmed:
                raise TransportError('agent dispatch was not confirmed')
            correlated_request_id = receipt.request_id or dispatch.request_id
            poll = PollRequest(request.task_id, correlated_request_id, receipt.operation_id)
            last_status: RemoteRunStatus | None = None
            for _ in range(effective_max_polls):
                if self._clock() > deadline:
                    break
                if lease_id is not None and (not self._admission.refresh(lease_id)):
                    raise LeaseLostError('agent admission lease was lost')
                status = self._transport.status(poll)
                if not isinstance(status, RemoteRunStatus):
                    raise TransportError('agent transport returned an invalid run status')
                last_status = status
                if status.status == 'FAILED':
                    salvaged = self._salvage_replies(poll, deadline)
                    if salvaged:
                        try:
                            outcome = self._completed_outcome(request, dispatch, receipt, poll, deadline_at, salvaged)
                        except DomainError as error:
                            logger.warning('REMOTE_RUN_FAILED_SALVAGE_REJECTED request_id=%s error=%s remote_error=%r', poll.request_id, error, status.error_message or '')
                        else:
                            if outcome.event_name not in {UNSTRUCTURED_REPLY_EVENT, CONTRACT_REJECTED_EVENT}:
                                logger.warning('REMOTE_RUN_FAILED_REPLY_SALVAGED task_id=%s request_id=%s replies=%s remote_error=%r', request.task_id, poll.request_id, len(salvaged), status.error_message or '')
                                return outcome
                    failure = self._remote_failure(request, status, receipt)
                    logger.warning('REMOTE_RUN_FAILED task_id=%s request_id=%s operation_id=%s remote_status=%s remote_error=%s error_code=%s retryable=%s deadline_at=%s', request.task_id, poll.request_id, receipt.operation_id, status.status, status.error_message or '', failure.error_code, failure.retryable, deadline_at)
                    return EffectOutcome(status='FAILED', event_name='FAIL', event_payload={'request_id': correlated_request_id, 'operation_id': receipt.operation_id, 'remote_status': status.status, 'deadline_at': deadline_at, 'retryable': failure.retryable}, request_id=correlated_request_id, operation_id=receipt.operation_id, deadline_at=deadline_at, failure=failure)
                if status.status == 'COMPLETED':
                    return self._completed(request, dispatch, receipt, poll, deadline, deadline_at)
                if status.status not in {'RUNNING', 'UNKNOWN'}:
                    raise TransportError(f'unsupported remote run status: {status.status}')
                if self._poll_interval:
                    time.sleep(min(self._poll_interval, max(0.0, deadline - self._clock())))
            if last_status is not None and last_status.status == 'UNKNOWN':
                code = 'REMOTE_RUN_CORRELATION_UNKNOWN'
                message = last_status.error_message or 'remote run correlation remained unknown'
            else:
                code = 'AGENT_TIMEOUT'
                message = 'agent run did not reach a terminal state before deadline'
            self._cancel_remote_runs(poll)
            logger.warning('REMOTE_RUN_TERMINAL_STATE_NOT_REACHED task_id=%s request_id=%s operation_id=%s error_code=%s last_status=%s deadline_at=%s', request.task_id, correlated_request_id, receipt.operation_id, code, last_status.status if last_status else 'none', deadline_at)
            failure = self._failure(request, stage='remote_run', code=code, retryable=True, message=message, external_id=last_status.operation_id if last_status else receipt.operation_id)
            return EffectOutcome(status='FAILED', event_name='FAIL', event_payload={'request_id': correlated_request_id, 'operation_id': receipt.operation_id, 'deadline_at': deadline_at, 'retryable': failure.retryable}, request_id=correlated_request_id, operation_id=receipt.operation_id, deadline_at=deadline_at, failure=failure)
        finally:
            if lease_id is not None:
                self._admission.release(lease_id)

    def _cancel_remote_runs(self, poll: PollRequest) -> None:
        """Best-effort stop the stale remote runs before the FSM retries."""
        cancel = getattr(self._transport, 'cancel_runs', None)
        if not callable(cancel):
            return
        try:
            cancelled = cancel(poll)
        except Exception as error:
            logger.warning('AGENT_REMOTE_RUN_CANCEL_ERROR task_id=%s request_id=%s error=%s', poll.task_id, poll.request_id, str(error)[:300])
            return
        if cancelled:
            logger.warning('AGENT_REMOTE_RUN_CANCEL_ISSUED task_id=%s request_id=%s runs=%s', poll.task_id, poll.request_id, ','.join((str(item) for item in cancelled)))

    def _completed(self, request: EffectRequest, dispatch: AgentDispatchRequest, receipt: DispatchReceipt, poll: PollRequest, deadline: float, deadline_at: str) -> EffectOutcome:
        result_deadline = min(deadline, self._clock() + self._completed_result_grace)
        replies = self._poll_completed(poll, result_deadline)
        if not replies:
            logger.warning('AGENT_RESULT_MISSING_AFTER_TERMINAL task_id=%s request_id=%s operation_id=%s grace_seconds=%s', request.task_id, poll.request_id, receipt.operation_id, self._completed_result_grace)
            failure = self._failure(request, stage='agent_result', code='AGENT_RESULT_MISSING', retryable=True, message='agent run completed without a correlated result', external_id=receipt.external_message_id or receipt.operation_id)
            return EffectOutcome(status='FAILED', event_name='FAIL', event_payload={'request_id': poll.request_id, 'deadline_at': deadline_at, 'retryable': failure.retryable}, request_id=poll.request_id, operation_id=receipt.operation_id, deadline_at=deadline_at, failure=failure)
        return self._completed_outcome(request, dispatch, receipt, poll, deadline_at, replies)

    def _completed_outcome(self, request: EffectRequest, dispatch: AgentDispatchRequest, receipt: DispatchReceipt, poll: PollRequest, deadline_at: str, replies: tuple[RawTransportReply, ...]) -> EffectOutcome:
        raw = replies[-1]
        if not isinstance(raw, RawTransportReply):
            raise TransportError('agent transport returned an invalid reply')
        target_state = str(request.payload.get('target_state') or '')
        binding = ReplyBinding(task_id=dispatch.task_id, request_id=poll.request_id, author_id=dispatch.agent_id, phase=dispatch.phase, role=dispatch.role, target_state=target_state or dispatch.phase)
        envelope = self._normalizer.normalize(raw, binding, self._role_decoder)
        result_payload = envelope.payload
        if not isinstance(result_payload, Mapping):
            raise TransportError('normalized agent payload must be a mapping')
        result_payload = dict(result_payload)
        artifact_id: str | None = None
        if self._artifacts is not None:
            content = json.dumps(result_payload, ensure_ascii=False, sort_keys=True).encode('utf-8')
            artifact = self._artifacts.write(ArtifactInput(task_id=request.task_id, name=f'agent/{dispatch.request_id}.json', content=content))
            artifact_id = artifact.artifact_id
        if artifact_id:
            result_payload['result_artifact_id'] = artifact_id
        event_name = str(result_payload.get('action') or '')
        if not event_name:
            raise TransportError('normalized agent result has no action')
        result_payload = {**dict(result_payload), 'request_id': poll.request_id, 'operation_id': receipt.operation_id, 'external_message_id': receipt.external_message_id, 'idempotency_key': request.idempotency_key}
        return EffectOutcome(status='SUCCEEDED', event_name=event_name, event_payload=result_payload, request_id=poll.request_id, operation_id=receipt.operation_id, deadline_at=deadline_at)

    def _poll_completed(self, poll: PollRequest, deadline: float) -> tuple[RawTransportReply, ...]:
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

    def _salvage_replies(self, poll: PollRequest, deadline: float) -> tuple[RawTransportReply, ...]:
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

    def _complete_with_reply(self, request: EffectRequest, dispatch: AgentDispatchRequest, receipt: DispatchReceipt, raw: RawTransportReply, deadline_at: str) -> EffectOutcome:
        target_state = str(request.payload.get('target_state') or '')
        binding = ReplyBinding(task_id=dispatch.task_id, request_id=receipt.request_id or dispatch.request_id, author_id=dispatch.agent_id, phase=dispatch.phase, role=dispatch.role, target_state=target_state or dispatch.phase)
        envelope = self._normalizer.normalize(raw, binding, self._role_decoder)
        result_payload = envelope.payload
        if not isinstance(result_payload, Mapping):
            raise TransportError('normalized agent payload must be a mapping')
        result_payload = dict(result_payload)
        artifact_id: str | None = None
        if self._artifacts is not None:
            content = json.dumps(result_payload, ensure_ascii=False, sort_keys=True).encode('utf-8')
            artifact = self._artifacts.write(ArtifactInput(task_id=request.task_id, name=f'agent/{dispatch.request_id}.json', content=content))
            artifact_id = artifact.artifact_id
        if artifact_id:
            result_payload['result_artifact_id'] = artifact_id
        event_name = str(result_payload.get('action') or '')
        if not event_name:
            raise TransportError('normalized agent result has no action')
        result_payload = {**dict(result_payload), 'request_id': receipt.request_id or dispatch.request_id, 'operation_id': receipt.operation_id, 'external_message_id': receipt.external_message_id, 'idempotency_key': request.idempotency_key}
        return EffectOutcome(status='SUCCEEDED', event_name=event_name, event_payload=result_payload, request_id=receipt.request_id or dispatch.request_id, operation_id=receipt.operation_id, deadline_at=deadline_at)

    def _dispatch_request(self, request: EffectRequest, payload: Mapping[str, object]) -> AgentDispatchRequest:
        target_state = str(payload.get('target_state') or '')
        worker_id = str(payload.get('worker_id') or '')
        agent_id = self.resolve_worker_agent(target_state, worker_id) or str(payload.get('agent_id') or '')
        pool_size = len(self.resolve_agent_pool(target_state)) or 1
        logger.info('AGENT_ID_BOUND task_id=%s target_state=%s worker_id=%s agent_id=%s pool_size=%s external_target=%s', request.task_id, target_state, worker_id, agent_id, pool_size, f"{payload.get('issue_id') or ''}:{agent_id}")
        dispatch_context = payload.get('dispatch_context')
        request_context = dict(dispatch_context) if isinstance(dispatch_context, Mapping) else {}
        fanout_parent = str(payload.get('fanout_parent_id') or '')
        if fanout_parent:
            request_context['fanout_parent_id'] = fanout_parent
            request_context['fanout_title'] = str(payload.get('fanout_title') or '')
        return AgentDispatchRequest(task_id=request.task_id, issue_id=str(payload.get('issue_id') or ''), request_id=str(payload.get('request_id') or request.idempotency_key), agent_id=agent_id, role=str(payload.get('role') or ''), phase=str(payload.get('phase') or ''), prompt_ref=str(payload.get('prompt_ref') or request.payload_ref or ''), idempotency_key=request.idempotency_key, sent_after=self._utc_now(), target_state=target_state, revision_id=str(payload.get('revision_id') or ''), plan_hash=str(payload.get('plan_hash') or ''), context=request_context)

    @staticmethod
    def _remote_failure(request: EffectRequest, status: RemoteRunStatus, receipt: DispatchReceipt) -> FailureRecord:
        return AgentWorkerRunner._failure(request, stage='remote_run', code=status.error_code or 'REMOTE_RUN_FAILED', retryable=True, message=status.error_message or 'remote agent run failed', external_id=status.operation_id or receipt.operation_id)

    @staticmethod
    def _failure(request: EffectRequest, *, stage: str, code: str, retryable: bool, message: str, external_id: str | None) -> FailureRecord:
        return FailureRecord(failure_id=f'{request.effect_id}:{code}', stage=stage, owner_component=request.effect_type, task_id=request.task_id, state=str(request.payload.get('target_state') or ''), sequence=int(request.payload.get('sequence') or 0), node_run_id=str(request.payload['node_run_id']) if request.payload.get('node_run_id') else None, worker_id=str(request.payload['worker_id']) if request.payload.get('worker_id') else None, effect_id=request.effect_id, error_code=code, retryable=retryable, message=message, cause_type='RemoteRun', external_id=external_id)

class AgentNodeWorkerRunner:
    """Adapt one agent effect runner to the ``NodeExecutor`` worker port."""

    def __init__(self, runner: AgentWorkerRunner) -> None:
        self._runner = runner

    def run(self, binding: WorkerBinding, context: NodeContext) -> WorkerResult:
        attempt = max(1, int(getattr(binding, 'attempt', 1) or 1))
        request_id = _attempt_request_id(binding.request_id, attempt)
        dispatch_context = dict(binding.dispatch_context or {})
        if attempt > 1:
            dispatch_context['dispatch_attempt'] = attempt
        payload = {'issue_id': binding.issue_id or context.issue_id, 'request_id': request_id, 'agent_id': binding.agent_id, 'role': binding.role, 'phase': binding.phase, 'target_state': context.state, 'node_run_id': context.node_run_id, 'worker_id': binding.worker_id, 'group_id': binding.group_id if binding.group_id is not None else context.group_id, 'item_id': binding.item_id if binding.item_id is not None else context.item_id, 'sequence': context.sequence, 'revision_id': context.revision_id, 'plan_hash': context.plan_hash, 'prompt_ref': binding.prompt_ref or context.prompt_ref}
        if dispatch_context:
            payload['dispatch_context'] = dispatch_context
        if binding.fanout_parent_id:
            payload['fanout_parent_id'] = binding.fanout_parent_id
            payload['fanout_title'] = binding.fanout_title
        logger.info('AGENT_WORKER_BOUND task_id=%s node_run_id=%s worker_id=%s group_id=%s item_id=%s dispatch_mode=%s prompt_chars=%s attempt=%s', context.task_id, context.node_run_id, binding.worker_id, payload.get('group_id') or '', payload.get('item_id') or '', str(binding.dispatch_context.get('zhongshu_dispatch_mode') or ''), len(str(payload.get('prompt_ref') or '')), attempt)
        request = EffectRequest(effect_id=_attempt_effect_id(context.node_run_id, binding.worker_id, attempt), effect_type='agent_dispatch', task_id=context.task_id, idempotency_key=request_id, payload=payload)
        outcome = self._runner.run_once(request)
        if outcome.status != 'SUCCEEDED':
            return WorkerResult(binding.worker_id, 'FAILED', None, outcome.failure, external_issue_id=str(outcome.operation_id or '') if binding.fanout_parent_id else '')
        artifact_id = None
        if isinstance(outcome.event_payload, Mapping):
            value = outcome.event_payload.get('result_artifact_id')
            artifact_id = str(value) if value else None
        return WorkerResult(binding.worker_id, 'SUCCEEDED', artifact_id, result_payload=outcome.event_payload, external_issue_id=str(outcome.operation_id or '') if binding.fanout_parent_id else '')
_UNSTRUCTURED_REPLY_ACTION = UNSTRUCTURED_REPLY_EVENT
_CONTRACT_REJECTED_ACTION = CONTRACT_REJECTED_EVENT
_REPLY_ACTION_FAILURES = {_UNSTRUCTURED_REPLY_ACTION: ('AGENT_REPLY_UNSTRUCTURED', 'agent worker returned an unstructured reply instead of the required JSON result contract: the reply body must be exactly one complete JSON object (no prose, no Markdown, no code fences, no extra comments)'), _CONTRACT_REJECTED_ACTION: ('AGENT_REPLY_CONTRACT_REJECTED', 'agent worker reply violated the role result contract')}

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
    action = str(payload.get('action') or '')
    mapped = _REPLY_ACTION_FAILURES.get(action)
    if mapped is None:
        return None
    error_code, default_message = mapped
    if action == _UNSTRUCTURED_REPLY_ACTION and is_output_overflow_reply(payload):
        return ('AGENT_REPLY_OUTPUT_OVERFLOW', OUTPUT_OVERFLOW_FEEDBACK)
    reason = str(payload.get('contract_rejection') or '')
    if not reason and action == _UNSTRUCTURED_REPLY_ACTION:
        reason = _json_parse_hint(payload)
    return (error_code, f'{default_message}: {reason}' if reason else default_message)

def _json_parse_hint(payload: Mapping[str, object]) -> str:
    """Tell the agent where its reply stopped being JSON and the usual cause."""
    error = str(payload.get('json_error') or '').strip()
    if not error or str(payload.get('json_status') or '') != 'invalid':
        return ''
    position = payload.get('json_error_position')
    where = f' at char {position}' if position is not None else ''
    context = ' '.join(str(payload.get('json_error_context') or '').split())
    near = f' near {context!r}' if context else ''
    return f'JSON parse error: {error}{where}{near}. Escape every double quote inside a string value as \\" (or use 「」), write each backslash as \\\\ and each line break as \\n, and send raw JSON with no code fence'

def _is_unstructured_reply(result: WorkerResult) -> bool:
    """True when a worker result is a transport placeholder, not a domain action."""
    return _rejected_reply_code(result) is not None
_TASK_REVIEW_ACTIONS = {'TASK_APPROVED', 'TASK_CHANGES_REQUIRED'}

def _attempt_request_id(request_id: str, attempt: int) -> str:
    """Scope a worker request id to its dispatch attempt.

    The attempt suffix is the only thing that makes a node-level worker retry a
    *new* external request; without it the transport's idempotency lookup
    returns the finished run and the retry can only poll a drained run.
    """
    return request_id if attempt <= 1 else f'{request_id}:attempt-{attempt}'

def _attempt_effect_id(node_run_id: str, worker_id: str, attempt: int) -> str:
    effect_id = f'worker:{node_run_id}:{worker_id}'
    return effect_id if attempt <= 1 else f'{effect_id}:attempt-{attempt}'

class AgentNodeJoiner:
    """Join agent worker replies into one deterministic node decision."""

    def __init__(self, node_run_id: str, *, task_id: str='', state: str='', sequence: int=0, revision_id: str='', plan_hash: str='', task_review_queue: object | None=None, canonical_requirements: tuple[dict[str, object], ...]=(), dispatch_mode: str='', base_plan: object | None=None, previous_review: object | None=None, salvaged_worker_payloads: tuple[dict[str, object], ...]=(), evidence_requester: str='', evidence_requires_solver_revision: bool=False, binding_contexts: Mapping[str, Mapping[str, object]] | None=None, menxia_stage_census: Mapping[str, object] | None=None) -> None:
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
        self._previous_review = dict(previous_review) if isinstance(previous_review, Mapping) else None
        self._salvaged_worker_payloads: tuple[dict[str, object], ...] = tuple((dict(row) for row in salvaged_worker_payloads or () if isinstance(row, Mapping) and str(row.get('worker_id') or '')))
        self._evidence_requester = str(evidence_requester or '')
        self._evidence_requires_solver_revision = evidence_requires_solver_revision
        self._binding_contexts: dict[str, dict[str, object]] = {str(worker_id): dict(ctx) for worker_id, ctx in (binding_contexts or {}).items() if isinstance(ctx, Mapping)}
        self._menxia_stage_census: dict[str, str] = {str(item_id): str(stage) for item_id, stage in (menxia_stage_census or {}).items()}

    def join(self, results):
        changes = [r for r in results if r.status == 'SUCCEEDED' and isinstance(r.result_payload, Mapping) and (r.result_payload.get('action') == 'REQUIREMENTS_CHANGE_REQUESTED')]
        if changes and all((r.status == 'SUCCEEDED' for r in results)):
            return NodeResult(self._node_run_id, 'SUCCEEDED', results, {'action': 'REQUIREMENTS_CHANGE_REQUESTED', 'questions_for_user': [q for r in changes for q in r.result_payload.get('questions_for_user', [])]})
        if self._state.startswith('MENXIA_') and self._dispatch_mode == 'menxia_item_pipeline':
            result = self._join_menxia(results)
            if result is not None:
                return result
        if self._state in MENXIA_GROUP_TARGETS and self._dispatch_mode == 'menxia_group_pipeline':
            result = self._join_menxia_group(results)
            if result is not None:
                return result
        failed = [r for r in results if r.status != 'SUCCEEDED' or _is_unstructured_reply(r)]
        if failed:
            rejections = tuple(_worker_rejection_entry(r) for r in failed)
            failure = next((r.failure for r in failed if r.failure is not None), None)
            if failure is not None:
                failure = replace(failure, worker_rejections=rejections)
            else:
                rejected = next((r for r in failed if _is_unstructured_reply(r)), failed[0])
                code, message = _rejected_reply_code(rejected) or ('AGENT_REPLY_UNSTRUCTURED', 'agent worker returned an unstructured reply instead of the required JSON result contract')
                failure = FailureRecord(failure_id=self._node_run_id+':NODE_WORKER_FAILED', stage='node_join', owner_component='agent_node_joiner', task_id=self._task_id, state=self._state, sequence=self._sequence, node_run_id=self._node_run_id, worker_id=rejected.worker_id, effect_id=None, error_code=code, retryable=True, message=message, cause_type='WorkerResult', worker_rejections=rejections)
            return NodeResult(self._node_run_id, 'FAILED', results, aggregate={'action': 'FAIL'}, failure=failure)
        actions = {str(r.result_payload.get('action') or '') for r in results if isinstance(r.result_payload, Mapping)}
        escalations = [action for action in ('BLOCKED', 'HUMAN_GATE') if action in actions]
        if escalations:
            actions = {escalations[0]}
        if len(actions) != 1 or not next(iter(actions), ''):
            failure = FailureRecord(failure_id=self._node_run_id + ':NODE_ACTION_CONFLICT', stage='node_join', owner_component='agent_node_joiner', task_id=self._task_id, state=self._state, sequence=self._sequence, node_run_id=self._node_run_id, worker_id=None, effect_id=None, error_code='NODE_ACTION_CONFLICT', retryable=False, message='node worker actions conflict', cause_type='WorkerResult')
            return NodeResult(self._node_run_id, 'FAILED', results, aggregate={'action': 'FAIL'}, failure=failure)
        return NodeResult(self._node_run_id, 'SUCCEEDED', results, aggregate={'action': next(iter(actions)), 'worker_count': len(results)})

    def _menxia_item_id(self, result: WorkerResult, payload: Mapping[str, object]) -> str:
        context = self._binding_contexts.get(result.worker_id) or {}
        item_id = str(context.get('item_id') or '') or str(payload.get('item_id') or '')
        return item_id.strip()

    def _join_menxia(self, results: tuple[WorkerResult, ...]) -> NodeResult | None:
        """Fan-in for the menxia stage-barrier wave (continue_and_block_group).

        Content-level worker failures park their item as ``BLOCKED`` instead of
        failing the whole node; infrastructure failures return ``None`` so the
        standard retry path takes over.  The aggregate carries the per-item
        results (folded into the menxia pipeline rows by the FSM) and the
        reduced wave action.
        """
        infra_failed = [result for result in results if result.status != 'SUCCEEDED' and _menxia_infra_failure(result)]
        if infra_failed:
            return None
        succeeded = [result for result in results if result.status == 'SUCCEEDED' and isinstance(result.result_payload, Mapping)]
        succeeded_ids = {id(result) for result in succeeded}
        content_failed = [result for result in results if id(result) not in succeeded_ids]
        item_results: list[dict[str, object]] = []
        result_actions: dict[str, str] = {}
        evidence_contributions: list[dict[str, object]] = []
        for result in succeeded:
            payload = result.result_payload
            assert isinstance(payload, Mapping)
            item_id = self._menxia_item_id(result, payload)
            action = str(payload.get('action') or '').strip()
            if not item_id or not action:
                return None
            row: dict[str, object] = {'item_id': item_id, 'action': action}
            fingerprint = str(payload.get('fingerprint') or '')
            if fingerprint:
                row['fingerprint'] = fingerprint
            if self._state == 'MENXIA_ITEM_SOLVER':
                proposal = payload.get('implementation_proposal')
                if isinstance(proposal, Mapping):
                    row['implementation_proposal'] = dict(proposal)
            elif self._state == 'MENXIA_ITEM_ANALYST':
                contribution = menxia_evidence_contribution(item_id, payload, str(result.worker_id or ''))
                if contribution is not None:
                    evidence_contributions.append(contribution)
            item_results.append(row)
            result_actions[item_id] = action
        for result in content_failed:
            context = self._binding_contexts.get(result.worker_id) or {}
            item_id = str(context.get('item_id') or '').strip()
            if not item_id:
                return None
            reason = str((result.failure.error_code if result.failure else '') or 'MENXIA_ITEM_BLOCKED')
            item_results.append({'item_id': item_id, 'action': 'BLOCKED', 'blocked_reason': reason})
            result_actions[item_id] = 'BLOCKED'
        if not item_results:
            return None
        census = dict(self._menxia_stage_census)
        for item_id, action in result_actions.items():
            next_stage = menxia_item_next_stage(action)
            if next_stage is None:
                next_stage = census.get(item_id) or 'SOLVING'
            census[item_id] = next_stage
        aggregate: dict[str, object] = {'action': menxia_wave_action(self._state, result_actions=result_actions, census=census), 'menxia_item_results': item_results, 'worker_results': [dict(payload) for payload in (result.result_payload for result in succeeded if isinstance(result.result_payload, Mapping))]}
        findings: list[dict[str, object]] = []
        for result in succeeded:
            payload = result.result_payload
            assert isinstance(payload, Mapping)
            raw_findings = payload.get('findings')
            if isinstance(raw_findings, list):
                findings.extend((dict(finding) for finding in raw_findings if isinstance(finding, Mapping)))
        if findings:
            aggregate['findings'] = findings
        if evidence_contributions:
            aggregate['menxia_evidence'] = evidence_contributions
        return NodeResult(self._node_run_id, 'SUCCEEDED', results, aggregate=aggregate)

    def _join_menxia_group(self, results: tuple[WorkerResult, ...]) -> NodeResult | None:
        """Fan-in for the group-pipeline wave (shared document model).

        Each group is one worker, so the mechanical document checks run here
        before anything folds. Repairable Solver shape defects return to the
        same group under a bounded reply budget; protected-document violations
        stay BLOCKED. Rejected drafts never fold into the accepted document.
        Infrastructure failures return None for the standard retry path.
        """
        infra_failed = [result for result in results if result.status != 'SUCCEEDED' and _menxia_infra_failure(result)]
        if infra_failed:
            return None
        succeeded = [result for result in results if result.status == 'SUCCEEDED' and isinstance(result.result_payload, Mapping)]
        succeeded_ids = {id(result) for result in succeeded}
        content_failed = [result for result in results if id(result) not in succeeded_ids]
        role = {'MENXIA_GROUP_SOLVER': 'solver', 'MENXIA_GROUP_ANALYST': 'analyst', 'MENXIA_GROUP_CRITIC': 'critic'}[self._state]
        group_rows: list[dict[str, object]] = []
        result_actions: dict[str, str] = {}
        human_questions: list[str] = []
        human_reasons: list[str] = []
        for result in succeeded:
            payload = result.result_payload
            assert isinstance(payload, Mapping)
            context = self._binding_contexts.get(result.worker_id) or {}
            group_id = str(context.get('group_id') or '').strip()
            action = str(payload.get('action') or '').strip()
            if not group_id or not action:
                return None
            claimed_group_id = str(payload.get('group_id') or '').strip()
            if claimed_group_id and claimed_group_id != group_id:
                group_rows.append({'group_id': group_id, 'action': 'BLOCKED', 'blocked_reason': f'MENXIA_GROUP_IDENTITY_MISMATCH: reply claims {claimed_group_id!r}, dispatch bound {group_id!r}'})
                result_actions[group_id] = 'BLOCKED'
                continue
            previous_markdown = str(context.get('doc_markdown') or '')
            gate_error = ''
            if action == 'HUMAN_GATE':
                questions = analyst_questions(payload)
                reason = str(payload.get('summary') or '').strip()
                if not reason:
                    reason = '; '.join((str(entry.get('reason') or '').strip() for entry in payload.get('questions_for_user') or () if isinstance(entry, Mapping) and entry.get('reason')))
                if questions and reason:
                    group_rows.append({'group_id': group_id, 'action': 'HUMAN_GATE'})
                    result_actions[group_id] = 'HUMAN_GATE'
                    human_questions.extend((f'{group_id}: {question}' for question in questions))
                    human_reasons.append(f'{group_id}: {reason}')
                    continue
                gate_error = 'human_gate_requires_concrete_question_and_reason'
            previous = MenxiaGroupDoc.parse(previous_markdown) if previous_markdown.strip() else None
            violations = (gate_error,) if gate_error else verify_group_reply(previous, role=role, reply=payload)
            if role != 'solver' and payload.get('reviewed_hash') not in (None, '', _sha256_hex(previous_markdown)):
                violations += (f'{role} reviewed_hash does not match the dispatched document',)
            if violations:
                if role == 'solver' and action != 'BLOCKED' and solver_reply_repairable(violations):
                    count = int(context.get('reply_retry_count') or 0)
                    maximum = int(context.get('max_reply_retries', 3))
                    repair = count < maximum
                    next_action = 'REPAIR_SOLVER_REPLY' if repair else 'BLOCKED'
                    reason = ('MENXIA_GROUP_REPLY_REPAIR: ' if repair else 'MENXIA_GROUP_REPLY_RETRIES_EXHAUSTED: ') + '; '.join(violations)
                    group_rows.append({'group_id': group_id, 'action': next_action, 'reply_rejection': reason, 'rejected_doc_markdown': str(payload.get('doc_markdown') or '')[:200000], 'blocked_reason': '' if repair else reason})
                    result_actions[group_id] = next_action
                    logger.warning('MENXIA_GROUP_REPLY_REJECTED group_id=%s retry=%s/%s reason=%s', group_id, count, maximum, reason)
                    continue
                group_rows.append({'group_id': group_id, 'action': 'BLOCKED', 'blocked_reason': 'MENXIA_GROUP_DOC_BREACH: ' + '; '.join(violations)})
                result_actions[group_id] = 'BLOCKED'
                continue
            doc_markdown = str(payload.get('doc_markdown'))
            doc = MenxiaGroupDoc.parse(doc_markdown)
            if role != 'solver':
                for suggestion_id in payload.get('confirmed_rejection_ids') or ():
                    doc = doc.confirm_rejection(str(suggestion_id), accept=True)
                    doc = replace(doc, ledger=doc.ledger + (LedgerEntry(doc.version, f'{role} 确认不采纳 {suggestion_id}', (str(suggestion_id),)),))
                for suggestion_id in payload.get('reaffirmed_rejection_ids') or ():
                    doc = doc.confirm_rejection(str(suggestion_id), accept=False)
                doc = doc.prune_closed()
            if role == 'solver' and action == 'READY_FOR_CRITIC':
                action = 'READY_FOR_ANALYST'
            approval_blockers: tuple[str, ...] = ()
            if role == 'critic' and action == 'APPROVE_GROUP':
                approval_blockers = menxia_approval_blockers(doc.body_markdown, group_id=str(context.get('group_id') or ''), requirement_markdown=str(context.get('requirement_markdown') or ''), approved_acceptance_markdown=str(context['approved_acceptance_markdown']) if context.get('workflow') == 'requirements-v1' else None)
            if role == 'critic' and action == 'APPROVE_GROUP' and (doc.open_blocking() or doc.pending_rejections() or approval_blockers):
                action = 'REQUEST_SOLVER_REVISION'
            if role == 'critic' and action == 'APPROVE_GROUP' and (not doc.approved_by('analyst')):
                action = 'REQUEST_EVIDENCE'
            if role != 'solver':
                approved = action in {'EVIDENCE_SUFFICIENT', 'READY_FOR_CRITIC', 'APPROVE_GROUP'} and (not any((s.author == role for s in (*doc.open_blocking(), *doc.pending_rejections()))))
                doc = doc.sign_doc(author=role, approved=approved)
                doc_markdown = doc.render()
            row: dict[str, object] = {'group_id': group_id, 'action': action, 'doc_version': doc.version, 'doc_hash': _sha256_hex(doc_markdown), 'doc_markdown': doc_markdown, 'open_count': doc.open_count(), 'open_suggestion_ids': sorted((s.suggestion_id for s in (*doc.open_blocking(), *doc.pending_rejections())))}
            fingerprint = str(payload.get('fingerprint') or '')
            if fingerprint:
                row['fingerprint'] = fingerprint
            if role == 'solver':
                row['clear_reply_repair'] = True
                absorbed_ids = payload.get('absorbed_ids')
                rejected_ids = payload.get('rejected_ids')
                if isinstance(absorbed_ids, (list, tuple)):
                    row['absorbed_ids'] = list(absorbed_ids)
                if isinstance(rejected_ids, (list, tuple)):
                    row['rejected_ids'] = list(rejected_ids)
            group_rows.append(row)
            result_actions[group_id] = action
        for result in content_failed:
            context = self._binding_contexts.get(result.worker_id) or {}
            group_id = str(context.get('group_id') or '').strip()
            if not group_id:
                return None
            reason = str((result.failure.error_code if result.failure else '') or 'MENXIA_GROUP_BLOCKED')
            group_rows.append({'group_id': group_id, 'action': 'BLOCKED', 'blocked_reason': reason})
            result_actions[group_id] = 'BLOCKED'
        if not group_rows:
            return None
        census = dict(self._menxia_stage_census)
        for group_id, action in result_actions.items():
            next_stage = menxia_group_next_stage(action)
            if next_stage is None:
                next_stage = census.get(group_id) or 'SOLVING'
            census[group_id] = next_stage
        aggregate: dict[str, object] = {'action': menxia_group_wave_action(self._state, result_actions=result_actions, census=dict(Counter(census.values()))), 'menxia_group_results': group_rows, 'worker_results': [dict(payload) for payload in (result.result_payload for result in succeeded if isinstance(result.result_payload, Mapping))]}
        if human_questions:
            aggregate['questions_for_user'] = human_questions
            aggregate['summary'] = '\n'.join(human_reasons)
        return NodeResult(self._node_run_id, 'SUCCEEDED', results, aggregate=aggregate)
__all__ = ['AgentNodeJoiner', 'AgentNodeWorkerRunner', 'AgentWorkerRunner']


def _worker_rejection_entry(result: WorkerResult) -> tuple[str, str]:
    """One worker's rejection line for the wave failure's per-worker ledger."""
    worker_id = str(result.worker_id or '')
    if result.failure is not None:
        failure = result.failure
        return (worker_id, f'{failure.error_code}: {failure.message}')
    rejected = _rejected_reply_code(result)
    if rejected is not None:
        error_code, message = rejected
        return (worker_id, f'{error_code}: {message}')
    return (worker_id, 'worker delivered no usable reply')
