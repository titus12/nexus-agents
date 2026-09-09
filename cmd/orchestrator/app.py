from __future__ import annotations

import argparse
import copy
import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from dataclasses import replace
from pathlib import Path
from typing import Any

from .adapters import FeishuHttpAdapter, MulticaCliAdapter, NullFeishuAdapter, SystemClock
from .feishu_command_parser import normalize_task_request
from .context import StateContext
from .concurrency import ConcurrencyAdmission, ConcurrencyLimits
from .events import Event
from .locks import TaskLock, TaskLockError
from .lifecycle import (
    LifecyclePaths,
    atomic_json,
    write_context,
    write_stage_result,
    write_stage_verdict,
)
from .logging_setup import configure_logging
from .models import AgentBinding, ExternalMessage, HumanGate, Finding
from .notifications import build_agent_notification
from .persistence import JsonStateStore, PersistenceError
from .parallel_runtime import ParallelCoordinatorDriver, external_target_parallelism
from .recovery import RecoveryManager
from .state_machine import StateMachine
from .states import (
    _allowed_actions,
    _critic_finding_ids,
    _current_item_payload,
    _validate_menxia_analyst_reply,
    _validate_menxia_critic_reply,
    _semantic_reply_signature,
    _solver_response_finding_ids,
    _validate_zhongshu_critic_reply,
    ACTIVE_SKILL_BY_STATE,
    _zhongshu_task_critic_prompt,
    _active_skill_directive,
    _runtime_skill_lock,
    validate_zhongshu_task_critic_reply,
    validate_zhongshu_task_graph,
    build_state_registry,
)
from .zhongshu_parallel import (
    ANALYST_MAX_EVIDENCE_UPDATES_MERGED,
    ANALYST_MAX_EVIDENCE_UPDATES_PER_WORKER,
    ANALYST_MAX_EVIDENCE_REQUESTS,
    ANALYST_MAX_EVIDENCE_REQUESTS_PER_WORKER,
    AnalystEvidenceGap,
    bind_zhongshu_requirement_contract,
    canonicalize_task_graph,
    canonical_plan_hash,
    critic_semantic_fingerprint,
    merge_analyst_evidence,
    validate_zhongshu_evidence_packet,
    validate_zhongshu_requirement_contract,
)
from .transitions import TransitionError
from .validators import RejectedReply, validate_agent_reply
from .zhongshu_review import (
    ANALYST_ACTIONS,
    CRITIC_ACTIONS,
    TASK_CRITIC_ACTIONS,
    aggregate_task_review_results,
    review_identity_error,
    progress_signature,
    union_records,
)
from .zhongshu_tasks import supplement_plan, blocking_unknowns
from .zhongshu_review_queue import (
    ReviewJob,
    TaskReviewQueue,
    build_review_jobs,
    task_review_queue_from_payload,
)
from .structured_output import (
    build_structured_output_spec,
    role_result_template,
    validate_role_result_shape,
)
from .contracts import contract_for_state

logger = logging.getLogger("review_orchestrator_fsm")


def _resolve_task_root(runs_root: str | Path, task_id: str) -> Path:
    """Resolve a task directory without allowing it to escape the run root."""
    root = Path(runs_root).expanduser().resolve()
    candidate = (root / str(task_id)).resolve()
    if candidate == root or root not in candidate.parents:
        raise ValueError("task id must resolve within runs root")
    return candidate


def _finding_scope_error(
    payload: dict[str, Any],
    state_name: str,
    *,
    group_id: str = "",
    item_id: str = "",
) -> str:
    """Reject Finding scope claims that do not match the execution scope."""
    findings = payload.get("findings")
    if not isinstance(findings, list):
        return ""
    expected_group = str(group_id or "").strip()
    expected_item = str(item_id or "").strip()
    payload_group = str(payload.get("group_id") or "").strip()
    payload_item = str(
        payload.get("item_id") or payload.get("active_item_id") or ""
    ).strip()
    is_zhongshu = state_name.startswith("ZHONGSHU")
    is_zhongshu_task_review = (
        state_name == "ZHONGSHU_CRITIC"
        and expected_group
        and expected_item
    )
    is_menxia_item_critic = state_name == "MENXIA_ITEM_CRITIC"
    if is_zhongshu and not is_zhongshu_task_review and (payload_group or payload_item):
        return "FINDING_SCOPE_UNEXPECTED_FOR_ZHONGSHU"
    if is_menxia_item_critic:
        if not expected_group or not expected_item:
            return "FINDING_SCOPE_CONTEXT_MISSING"
        if payload_group and payload_group != expected_group:
            return "FINDING_GROUP_ID_MISMATCH"
        if payload_item and payload_item != expected_item:
            return "FINDING_ITEM_ID_MISMATCH"
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            continue
        finding_group = str(finding.get("group_id") or "").strip()
        finding_item = str(finding.get("item_id") or "").strip()
        if finding_item and not finding_group:
            return f"FINDING_SCOPE_INVALID:{index}"
        if is_zhongshu and not is_zhongshu_task_review and (finding_group or finding_item):
            return f"FINDING_SCOPE_UNEXPECTED_FOR_ZHONGSHU:{index}"
        if is_menxia_item_critic:
            if finding_group and finding_group != expected_group:
                return f"FINDING_GROUP_ID_MISMATCH:{index}"
            if finding_item and finding_item != expected_item:
                return f"FINDING_ITEM_ID_MISMATCH:{index}"
    return ""


class OrchestratorApp:
    def __init__(
        self,
        ctx: StateContext,
        root: str | Path = "runs",
        multica: Any | None = None,
        feishu: Any | None = None,
        poll_interval: float = 8.0,
        timeout_seconds: int = 900,
    ) -> None:
        self.ctx = ctx
        if not self.ctx.zhongshu_parallel.get("enabled") and os.environ.get("ORCHESTRATOR_PARALLEL_MODE", "true").lower() in {"1", "true", "yes", "on"}:
            self.ctx.zhongshu_parallel["enabled"] = True
        self.root = _resolve_task_root(root, ctx.task_id)
        self.lifecycle = LifecyclePaths(self.root)
        self.lifecycle.ensure()
        self.store = JsonStateStore(self.root)
        self.multica = multica or MulticaCliAdapter(self.root / "transport")
        if feishu is not None:
            self.feishu = feishu
        elif os.environ.get("NEXUS_TEST_NO_EXTERNAL_NOTIFICATIONS", "").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }:
            self.feishu = NullFeishuAdapter()
        else:
            self.feishu = FeishuHttpAdapter()
        self.poll_interval = poll_interval
        self.timeout_seconds = timeout_seconds
        lease_ttl_default = max(int(timeout_seconds) + 120, 900)
        self.admission = ConcurrencyAdmission(
            ConcurrencyLimits(
                global_max=int(os.environ.get("GLOBAL_MAX_WORKERS", "6")),
                per_task_max=int(os.environ.get("PER_TASK_MAX_WORKERS", "6")),
                analyst_max=int(os.environ.get("ANALYST_MAX_WORKERS", "3")),
                critic_max=int(os.environ.get("CRITIC_MAX_WORKERS", "6")),
                lease_ttl_seconds=int(
                    os.environ.get("LEASE_TTL_SECONDS", str(lease_ttl_default))
                ),
            )
        )
        self.heartbeat_interval = float(os.environ.get("HEARTBEAT_NOTIFY_SEC", "120"))
        self.started_at = time.time()
        self.states = build_state_registry(
            multica=self.multica,
            feishu=self.feishu,
            clock=SystemClock(),
            agent_ids={
                "review-analyst": os.environ.get("AGENT_ANALYST_ID", ""),
                "review-solver": os.environ.get("AGENT_SOLVER_ID", ""),
                "review-critic": os.environ.get("AGENT_CRITIC_ID", ""),
            },
            admission=self.admission,
        )
        self._pending_parallel_event: Event | None = None
        self._parallel_menxia_payloads: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._parallel_menxia_groups: set[str] = set()
        self._task_review_queue_lock = threading.RLock()
        self.machine = StateMachine(ctx, self.states, self.store.persist, self._dispatch_pending)
        write_context(self.lifecycle, self.ctx)
        self.parallel_driver = ParallelCoordinatorDriver(
            task_id=self.ctx.task_id,
            lifecycle=self.lifecycle,
            admission=self.admission,
            dispatch=self.multica.dispatch,
            poll=self.multica.poll,
            find_existing_request=getattr(self.multica, "find_existing_request", None),
            max_attempts=int(os.environ.get("PARALLEL_MAX_ATTEMPTS", "3")),
            poll_interval=float(os.environ.get("PARALLEL_POLL_INTERVAL_SEC", "2")),
            timeout_seconds=float(os.environ.get("PARALLEL_WORKER_TIMEOUT_SEC", str(timeout_seconds))),
        )
        self._task_lock: TaskLock | None = None
        self._lock_refresh_guard = threading.Lock()
        self._last_lock_refresh = 0.0

    def run(self) -> bool:
        lock = TaskLock(self.root / "task.lock")
        outcome = "not_started"
        try:
            lock.acquire()
        except TaskLockError:
            logger.error("TASK_LOCK_BUSY task_id=%s", self.ctx.task_id)
            return False
        self._task_lock = lock
        self._last_lock_refresh = 0.0
        logger.info(
            "PROCESS_START task_id=%s issue_id=%s state=%s poll_interval=%s timeout_seconds=%s pid=%s",
            self.ctx.task_id,
            self.ctx.issue_id,
            self.ctx.workflow_state,
            self.poll_interval,
            self.timeout_seconds,
            os.getpid(),
        )
        try:
            outcome = "running"
            self._ensure_started()
            write_context(self.lifecycle, self.ctx)
            while self.ctx.workflow_state not in {"DONE", "BLOCKED", "CANCELLED", "STATE_CORRUPTED"}:
                lock.refresh()
                event = self._next_event()
                if event.name == "NOOP":
                    time.sleep(self.poll_interval)
                    continue
                self.machine.dispatch(event)
                write_context(self.lifecycle, self.ctx)
                self._record_event_context(event)
            outcome = self.ctx.workflow_state.lower()
            if self.ctx.workflow_state == "DONE":
                self._write_final_delivery_files()
            return self.ctx.workflow_state == "DONE"
        except (TransitionError, PersistenceError, RuntimeError) as error:
            outcome = "fsm_fatal"
            logger.exception(
                "FSM_FATAL task_id=%s state=%s request_id=%s",
                self.ctx.task_id,
                self.ctx.workflow_state,
                self.ctx.active_request_id,
            )
            try:
                self.machine.dispatch(Event("STATE_PERSISTENCE_FAILED", {"reason": str(error)}))
            except Exception:
                logger.exception(
                    "FSM_FATAL_PERSIST_FAILED task_id=%s state=%s",
                    self.ctx.task_id,
                    self.ctx.workflow_state,
                )
                self.ctx.workflow_state = "STATE_CORRUPTED"
                self.store.save_state(self.ctx)
            return False
        except KeyboardInterrupt:
            outcome = "operator_interrupted"
            logger.warning(
                "PROCESS_INTERRUPTED task_id=%s state=%s request_id=%s",
                self.ctx.task_id,
                self.ctx.workflow_state,
                self.ctx.active_request_id,
            )
            self.record_interruption("operator interrupted process")
            return False
        except Exception as error:
            outcome = "unhandled_exception"
            logger.exception(
                "PROCESS_CRASHED task_id=%s state=%s request_id=%s error_type=%s",
                self.ctx.task_id,
                self.ctx.workflow_state,
                self.ctx.active_request_id,
                type(error).__name__,
            )
            self.ctx.last_error = {
                "code": "UNHANDLED_EXCEPTION",
                "message": str(error),
                "error_type": type(error).__name__,
                "state": self.ctx.workflow_state,
                "request_id": self.ctx.active_request_id,
            }
            self.ctx.recoverable = True
            try:
                self.store.save_state(self.ctx)
            except Exception:
                logger.exception(
                    "PROCESS_CRASH_STATE_SAVE_FAILED task_id=%s state=%s",
                    self.ctx.task_id,
                    self.ctx.workflow_state,
                )
            return False
        finally:
            logger.info(
                "PROCESS_EXIT task_id=%s state=%s request_id=%s outcome=%s",
                self.ctx.task_id,
                self.ctx.workflow_state,
                self.ctx.active_request_id,
                outcome,
            )
            self._task_lock = None
            lock.release()

    def record_interruption(self, reason: str) -> None:
        self.ctx.last_error = {
            "code": "PROCESS_INTERRUPTED",
            "message": reason,
            "state": self.ctx.workflow_state,
            "request_id": self.ctx.active_request_id,
        }
        self.ctx.updated_at = datetime.now(timezone.utc).isoformat()
        self.ctx.recoverable = True
        self.store.save_state(self.ctx)

    def cancel(self) -> bool:
        lock = TaskLock(self.root / "task.lock")
        acquired = False
        try:
            lock.acquire()
            acquired = True
            self._ensure_loaded()
            if self.ctx.workflow_state in {"DONE", "BLOCKED", "CANCELLED", "STATE_CORRUPTED"}:
                return False
            self.machine.dispatch(Event("CANCEL_REQUESTED", {"reason": "operator cancellation"}))
            return True
        finally:
            if acquired:
                lock.release()

    def _dispatch_pending(self, ctx: StateContext, state: Any) -> None:
        try:
            self._dispatch_pending_impl(ctx, state)
        except KeyboardInterrupt:
            raise
        except PersistenceError:
            raise
        except Exception as error:
            state_name = getattr(state, "name", "") or ctx.workflow_state
            logger.exception(
                "DISPATCH_PENDING_FAILED task_id=%s state=%s request_id=%s error_type=%s",
                ctx.task_id,
                state_name,
                ctx.active_request_id,
                type(error).__name__,
            )
            ctx.last_error = {
                "code": "MULTICA_DISPATCH_FAILED",
                "message": str(error),
                "state": state_name,
                "request_id": ctx.active_request_id,
            }
            self._pending_parallel_event = Event(
                "MULTICA_ERROR",
                {
                    "resume_state": state_name,
                    "max_retries": ctx.max_external_retries,
                    "reason": str(error),
                },
                str(error),
            )

    def _dispatch_pending_impl(self, ctx: StateContext, state: Any) -> None:
        state_name = getattr(state, "name", "")
        pending_event = ctx.request_payload.get("pending_fsm_event")
        if isinstance(pending_event, dict) and pending_event.get("name"):
            self._pending_parallel_event = Event(
                str(pending_event["name"]),
                copy.deepcopy(pending_event.get("payload") or {}),
                str(pending_event.get("reason") or ""),
            )
            return
        if self._parallel_enabled() and state_name in {"ZHONGSHU_ANALYST", "ZHONGSHU_CRITIC"}:
            self._pending_parallel_event = self._run_parallel_state(state_name)
            return
        if self._parallel_enabled() and state_name.startswith("MENXIA_ITEM_"):
            payload = self._parallel_menxia_payloads.pop(
                (str(ctx.active_group_id or ""), str(ctx.active_item_id or ""), state_name),
                None,
            )
            if payload is not None:
                self._pending_parallel_event = Event("AGENT_REPLY_ACCEPTED", payload)
                return
            if (
                state_name in {
                    "MENXIA_ITEM_SOLVER",
                    "MENXIA_ITEM_ANALYST",
                    "MENXIA_ITEM_CRITIC",
                }
                and str(ctx.active_group_id or "") not in self._parallel_menxia_groups
                and ctx.item_revision_round == 0
            ):
                prepared = self._prepare_parallel_menxia_group()
                if prepared is False:
                    return
                payload = self._parallel_menxia_payloads.pop(
                    (str(ctx.active_group_id or ""), str(ctx.active_item_id or ""), state_name),
                    None,
                )
                if payload is not None:
                    self._pending_parallel_event = Event("AGENT_REPLY_ACCEPTED", payload)
                    return
        pending = getattr(state, "dispatch_pending", None)
        if pending:
            pending(ctx)

    def _prepare_parallel_menxia_group(self) -> bool | None:
        group = self.ctx.request_payload.get("active_group")
        group_id = str(self.ctx.active_group_id or "")
        items = group.get("items") if isinstance(group, dict) else []
        if not group_id or not isinstance(items, list) or not items:
            return None
        from .models import AgentRequest
        from .parallel_runtime import ParallelWorker
        from .states import ROLE_BY_STATE
        retry_suffix = (
            f":retry-{self.ctx.reply_retry_count}"
            if self.ctx.reply_retry_count
            else ""
        )

        def fail(reason: str, result: Any | None = None) -> bool:
            logger.error(
                "MENXIA_PARALLEL_FANIN_INCOMPLETE task_id=%s group_id=%s reason=%s",
                self.ctx.task_id,
                group_id,
                reason,
            )
            self._pending_parallel_event = self._parallel_failure_event(
                "MENXIA_ITEM_SOLVER", reason, result
            )
            return False

        item_records: list[tuple[str, dict[str, Any]]] = []
        seen_item_ids: set[str] = set()
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                return fail(f"item at index {index} is not an object")
            item_id = str(item.get("item_id") or f"item-{index + 1:06d}").strip()
            if not item_id:
                return fail(f"item at index {index} has empty item_id")
            if item_id in seen_item_ids:
                return fail(f"duplicate item_id={item_id}")
            seen_item_ids.add(item_id)
            item_records.append((item_id, item))
        expected_item_ids = set(seen_item_ids)
        expected = len(item_records)

        def index_completed(
            stage: str,
            values: tuple[dict[str, Any], ...],
            expected_ids: set[str],
        ) -> tuple[dict[str, dict[str, Any]] | None, str]:
            indexed: dict[str, dict[str, Any]] = {}
            for value in values:
                item_id = str(
                    value.get("item_id")
                    or value.get("active_item_id")
                    or ""
                )
                if not item_id:
                    return None, f"{stage} result missing item_id"
                if item_id not in expected_ids:
                    return None, f"{stage} result has unknown item_id={item_id}"
                if item_id in indexed:
                    return None, f"{stage} result duplicated item_id={item_id}"
                indexed[item_id] = value
            missing = sorted(expected_ids - set(indexed))
            if missing:
                return None, f"{stage} result missing item_ids={','.join(missing)}"
            return indexed, ""

        revision = str(
            self.ctx.request_payload.get("frozen_plan", {}).get("plan_revision_id")
            or self.ctx.request_payload.get("candidate_plan", {}).get("plan_revision_id")
            or f"revision-{self.ctx.sequence + 1}"
        )

        def make_workers(
            state_name: str,
            source: dict[str, Any],
            batch: list[tuple[str, dict[str, Any]]],
        ) -> list[ParallelWorker]:
            workers = []
            for item_id, item in batch:
                source_item = source.get(item_id, {}) if isinstance(source, dict) else {}
                if not isinstance(source_item, dict):
                    source_item = {}
                clone = copy.deepcopy(self.ctx)
                clone.active_group_id = group_id
                clone.active_item_id = item_id
                clone.request_payload["active_group"] = copy.deepcopy(group)
                clone.request_payload["active_item"] = copy.deepcopy(item)
                # These are internal cross-wave inputs.  They intentionally do
                # not change any role's response protocol: the Solver owns the
                # proposal, the Analyst owns the review, and the Critic gets
                # both explicitly when its wave is built.
                clone.request_payload["implementation_proposal"] = copy.deepcopy(
                    source_item.get("implementation_proposal", {})
                )
                clone.request_payload["solver_result"] = copy.deepcopy(
                    source_item.get("solver_result", {})
                )
                clone.request_payload["analyst_review"] = copy.deepcopy(
                    source_item.get("analyst_review", {})
                )
                clone.current_phase, clone.current_role = ROLE_BY_STATE[state_name]
                clone.expected_agent_id = self.states[state_name].agent_ids.get(clone.current_role, clone.current_role)
                clone.active_request_id = (
                    f"{self.ctx.task_id}:{state_name}:{group_id}:{item_id}:parallel"
                    f"{retry_suffix}"
                )
                clone.dispatch_idempotency_key = clone.active_request_id
                request = self.states[state_name].request(clone)
                workers.append(
                    ParallelWorker(
                        f"{state_name.lower()}-{group_id}-{item_id}",
                        state_name,
                        request.role,
                        AgentRequest(
                            task_id=request.task_id,
                            request_id=request.request_id,
                            agent_id=request.agent_id,
                            role=request.role,
                            phase=request.phase,
                            prompt=request.prompt,
                            idempotency_key=request.idempotency_key,
                            issue_id=request.issue_id,
                            sent_after=request.sent_after,
                            context={**request.context, "group_id": group_id, "item_id": item_id, "revision_id": revision},
                            target_state=request.target_state,
                            target_role=request.target_role,
                            structured_output=copy.deepcopy(request.structured_output),
                        ),
                    )
                )
            return workers

        def batch_size_for(state_name: str) -> int:
            limits = self.admission.limits
            if "ANALYST" in state_name:
                phase_limit = limits.analyst_max
            elif "CRITIC" in state_name:
                phase_limit = limits.critic_max
            else:
                phase_limit = limits.per_task_max
            return max(1, min(limits.global_max, limits.per_task_max, phase_limit))

        def load_stage_results(state_name: str) -> dict[str, dict[str, Any]]:
            recovered: dict[str, dict[str, Any]] = {}
            last_error = self.ctx.last_error if isinstance(self.ctx.last_error, dict) else {}
            previous_parallel = last_error.get("parallel")
            if (
                last_error.get("code") == "AGENT_REPLY_FANIN_REJECTED"
                and isinstance(previous_parallel, dict)
                and str(previous_parallel.get("revision_id") or "") == revision
            ):
                logger.info(
                    "MENXIA_PARALLEL_STAGE_REUSE_SKIPPED_AFTER_FANIN_FAILURE "
                    "task_id=%s group_id=%s revision_id=%s state=%s",
                    self.ctx.task_id,
                    group_id,
                    revision,
                    state_name,
                )
                return recovered
            for item_id, _item in item_records:
                path = self.lifecycle.stage_result(
                    phase="MENXIA",
                    revision=revision,
                    state=state_name,
                    group_id=group_id,
                    item_id=item_id,
                )
                if not path.is_file():
                    continue
                try:
                    envelope = json.loads(path.read_text(encoding="utf-8"))
                    payload = envelope.get("payload") if isinstance(envelope, dict) else None
                except (OSError, UnicodeError, json.JSONDecodeError) as error:
                    logger.warning(
                        "MENXIA_PARALLEL_STAGE_RESULT_UNREADABLE task_id=%s group_id=%s "
                        "item_id=%s state=%s error_type=%s",
                        self.ctx.task_id,
                        group_id,
                        item_id,
                        state_name,
                        type(error).__name__,
                    )
                    continue
                if not isinstance(payload, dict):
                    continue
                if (
                    envelope.get("schema") != "nexus-orchestrator-stage-result-v1"
                    or str(envelope.get("phase") or "").upper() != "MENXIA"
                    or str(envelope.get("state") or "") != state_name
                    or str(envelope.get("revision") or "") != revision
                ):
                    logger.warning(
                        "MENXIA_PARALLEL_STAGE_RESULT_METADATA_MISMATCH task_id=%s "
                        "group_id=%s item_id=%s state=%s path=%s",
                        self.ctx.task_id,
                        group_id,
                        item_id,
                        state_name,
                        path,
                    )
                    continue
                stored_group = str(envelope.get("group_id") or "")
                stored_item = str(envelope.get("item_id") or "")
                payload_item = str(
                    payload.get("item_id") or payload.get("active_item_id") or ""
                )
                if (
                    stored_group != group_id
                    or stored_item != item_id
                    or payload_item != item_id
                ):
                    logger.warning(
                        "MENXIA_PARALLEL_STAGE_RESULT_SCOPE_MISMATCH task_id=%s "
                        "group_id=%s item_id=%s state=%s path=%s",
                        self.ctx.task_id,
                        group_id,
                        item_id,
                        state_name,
                        path,
                    )
                    continue
                try:
                    stage_worker = make_workers(
                        state_name,
                        {},  # Recovery only needs the binding/scope, not a new stage input.
                        [(item_id, next(item for candidate_id, item in item_records if candidate_id == item_id))],
                    )[0]
                    logical = str(payload.get("logical_request_id") or "")
                    attempt = payload.get("attempt")
                    if (logical != stage_worker.request.request_id or type(attempt) is not int or attempt < 1
                            or payload.get("request_id") != f"{logical}:attempt-{attempt}"):
                        raise ValueError("stage result request identity mismatch")
                    stage_worker = replace(stage_worker, request=replace(stage_worker.request, request_id=payload["request_id"]))
                    self._validate_parallel_worker_reply(payload, stage_worker, self.ctx)
                except (StopIteration, ValueError) as error:
                    logger.warning(
                        "MENXIA_PARALLEL_STAGE_RESULT_INVALID task_id=%s group_id=%s "
                        "item_id=%s state=%s error=%s",
                        self.ctx.task_id,
                        group_id,
                        item_id,
                        state_name,
                        str(error)[:300],
                    )
                    continue
                recovered[item_id] = payload
                self._parallel_menxia_payloads[(group_id, item_id, state_name)] = payload
                logger.info(
                    "MENXIA_PARALLEL_STAGE_RESULT_REUSED task_id=%s group_id=%s "
                    "item_id=%s state=%s path=%s",
                    self.ctx.task_id,
                    group_id,
                    item_id,
                    state_name,
                    path,
                )
            return recovered

        def run_stage_in_batches(
            state_name: str,
            source: dict[str, Any],
        ) -> tuple[dict[str, dict[str, Any]] | None, str, Any | None]:
            indexed = load_stage_results(state_name)
            pending = [
                (item_id, item)
                for item_id, item in item_records
                if item_id not in indexed
            ]
            capacity = batch_size_for(state_name)
            total_batches = (len(pending) + capacity - 1) // capacity
            for batch_number, offset in enumerate(
                range(0, len(pending), capacity),
                start=1,
            ):
                batch = pending[offset : offset + capacity]
                batch_ids = {item_id for item_id, _item in batch}
                logger.info(
                    "MENXIA_PARALLEL_BATCH_STARTED task_id=%s group_id=%s state=%s "
                    "batch=%s total_batches=%s batch_size=%s item_ids=%s",
                    self.ctx.task_id,
                    group_id,
                    state_name,
                    batch_number,
                    total_batches,
                    len(batch),
                    ",".join(item_id for item_id, _item in batch),
                )
                result = self.run_parallel_fanout(
                    phase="MENXIA",
                    revision_id=revision,
                    workers=make_workers(state_name, source, batch),
                    fan_in=lambda values, ids=tuple(sorted(batch_ids)): {
                        "worker_count": len(values),
                        "batch_item_ids": list(ids),
                    },
                    validate=self._validate_parallel_worker_reply,
                )
                if (
                    result.fanin_error
                    or len(result.completed) != len(batch)
                    or result.failed
                    or result.rejected
                ):
                    return None, (
                        f"menxia {state_name.lower()} batch incomplete: "
                        f"expected={len(batch)} completed={len(result.completed)} "
                        f"failed={len(result.failed)} rejected={len(result.rejected)} "
                        f"fanin_error={result.fanin_error or 'none'}"
                    ), result
                batch_indexed, index_error = index_completed(
                    state_name.lower(),
                    result.completed,
                    batch_ids,
                )
                if batch_indexed is None:
                    return None, index_error, result
                for item_id, value in batch_indexed.items():
                    indexed[item_id] = value
                    self._parallel_menxia_payloads[(group_id, item_id, state_name)] = value
                logger.info(
                    "MENXIA_PARALLEL_BATCH_COMPLETED task_id=%s group_id=%s state=%s "
                    "batch=%s total_batches=%s completed=%s",
                    self.ctx.task_id,
                    group_id,
                    state_name,
                    batch_number,
                    total_batches,
                    len(batch_indexed),
                )
            if set(indexed) != expected_item_ids:
                return None, (
                    f"{state_name.lower()} stage incomplete: "
                    f"expected={len(expected_item_ids)} completed={len(indexed)}"
                ), None
            return indexed, "", None

        solver_by_item, solver_error, solver_result = run_stage_in_batches(
            "MENXIA_ITEM_SOLVER",
            {},
        )
        if solver_by_item is None:
            return fail(solver_error, solver_result)
        analyst_by_item, analyst_error, analyst_result = run_stage_in_batches(
            "MENXIA_ITEM_ANALYST",
            solver_by_item,
        )
        if analyst_by_item is None:
            return fail(analyst_error, analyst_result)
        critic_inputs: dict[str, dict[str, Any]] = {}
        for item_id in expected_item_ids:
            solver_payload = solver_by_item.get(item_id, {})
            analyst_payload = analyst_by_item.get(item_id, {})
            critic_inputs[item_id] = {
                "implementation_proposal": copy.deepcopy(
                    solver_payload.get("implementation_proposal", {})
                    if isinstance(solver_payload, dict)
                    else {}
                ),
                "solver_result": copy.deepcopy(solver_payload),
                "analyst_review": copy.deepcopy(analyst_payload),
            }
        critic_by_item, critic_error, critic_result = run_stage_in_batches(
            "MENXIA_ITEM_CRITIC",
            critic_inputs,
        )
        if critic_by_item is None:
            return fail(critic_error, critic_result)
        self._parallel_menxia_groups.add(group_id)
        logger.info(
            "MENXIA_PARALLEL_GROUP_PREPARED task_id=%s group_id=%s items=%s solver_completed=%s analyst_completed=%s critic_completed=%s",
            self.ctx.task_id,
            group_id,
            expected,
            len(solver_by_item),
            len(analyst_by_item),
            len(critic_by_item),
        )

    def _parallel_enabled(self) -> bool:
        return os.environ.get("ORCHESTRATOR_PARALLEL_MODE", "true").lower() in {"1", "true", "yes", "on"} and bool(self.ctx.zhongshu_parallel.get("enabled"))

    @staticmethod
    def _bounded_parallel_payload(payload: Any, limit: int = 4000) -> Any:
        """Keep repair evidence useful without persisting unbounded worker replies."""
        if not isinstance(payload, dict):
            return str(payload or "")[:limit]
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if len(encoded) <= limit:
            return copy.deepcopy(payload)
        return {
            "truncated": True,
            "chars": len(encoded),
            "excerpt": encoded[:limit],
        }

    def _parallel_failure_event(
        self,
        state_name: str,
        reason: str,
        result: Any | None = None,
    ) -> Event:
        """Persist fan-out/fan-in failure details before asking the state to repair."""
        phase = "ZHONGSHU" if state_name.startswith("ZHONGSHU") else self.ctx.current_phase
        completed = list(getattr(result, "completed", ()) or ())
        failed = list(getattr(result, "failed", ()) or ())
        rejected = list(getattr(result, "rejected", ()) or ())
        revision_id = str(getattr(result, "revision_id", "") or "")
        independence_failure = (
            state_name == "ZHONGSHU_CRITIC"
            and "ZHONGSHU_CRITIC_INDEPENDENCE_INSUFFICIENT" in str(reason)
        )
        worker_payloads = [
            {
                "worker_id": str(item.get("worker_id") or ""),
                "action": str(item.get("action") or ""),
                "revision_id": str(item.get("revision_id") or item.get("plan_revision_id") or ""),
                "plan_hash": str(item.get("plan_hash") or item.get("reviewed_plan_hash") or ""),
                "semantic_fingerprint": (
                    critic_semantic_fingerprint(item)
                    if state_name == "ZHONGSHU_CRITIC"
                    else ""
                ),
                "finding_ids": sorted({
                    str(finding.get("finding_id") or finding.get("id"))
                    for finding in (item.get("findings") or [])
                    if isinstance(finding, dict)
                    and (finding.get("finding_id") or finding.get("id"))
                }),
                # The complete result remains in the immutable stage artifact.
                # Do not feed a duplicate Critic answer back to every worker;
                # that turns a protocol repair into answer copying.
                "payload": (
                    {"omitted_for_independence_repair": True}
                    if independence_failure
                    else self._bounded_parallel_payload(item)
                ),
            }
            for item in completed
            if isinstance(item, dict)
        ]
        details = {
            "state": state_name,
            "phase": phase,
            "contract_id": (
                contract_for_state(state_name).contract_id
                if state_name in {
                    "ZHONGSHU_ANALYST", "ZHONGSHU_SOLVER", "ZHONGSHU_CRITIC",
                    "MENXIA_ITEM_SOLVER", "MENXIA_ITEM_ANALYST",
                    "MENXIA_ITEM_CRITIC", "MENXIA_GROUP_GATE",
                }
                else ""
            ),
            "revision_id": revision_id,
            "reason": str(reason)[:2000],
            "completed_workers": [str(item.get("worker_id") or "") for item in completed],
            "failed_workers": [
                {
                    "worker_id": str(item.get("worker_id") or ""),
                    "error": str(item.get("error") or "")[:1000],
                    "rejected_reply_paths": copy.deepcopy(item.get("rejected_reply_paths") or []),
                }
                for item in failed
                if isinstance(item, dict)
            ],
            "rejected_workers": [
                {
                    "worker_id": str(item.get("worker_id") or ""),
                    "reason": str(item.get("reason") or "")[:1000],
                }
                for item in rejected
                if isinstance(item, dict)
            ],
            "worker_payloads": worker_payloads,
        }
        self.ctx.resume_state = state_name
        self.ctx.last_error = {
            "code": "AGENT_REPLY_FANIN_REJECTED",
            "message": details["reason"],
            "state": state_name,
            "phase": phase,
            "revision_id": revision_id,
            "request_id": self.ctx.active_request_id,
            "parallel": copy.deepcopy(details),
        }
        history = self.ctx.reply_history if isinstance(self.ctx.reply_history, list) else []
        history.append({
            "request_id": self.ctx.active_request_id,
            "state": state_name,
            "phase": phase,
            "role": self.ctx.current_role,
            "reason": details["reason"],
            "payload": copy.deepcopy(details),
        })
        self.ctx.reply_history = history[-8:]
        # Persist the FSM event before returning it.  If the process stops
        # after fan-in has failed but before StateMachine.dispatch() runs, a
        # restart must resume the rejection transition instead of launching
        # the same Critic request IDs and reusing the failed results forever.
        self.ctx.request_payload["pending_fsm_event"] = {
            "name": "AGENT_REPLY_REJECTED",
            "payload": {
                "reason": details["reason"],
                "resume_state": state_name,
                "max_retries": self.ctx.max_reply_retries,
            },
        }
        if revision_id:
            try:
                write_stage_verdict(
                    self.lifecycle,
                    phase=phase,
                    revision=revision_id,
                    verdict={
                        "action": "AGENT_REPLY_FANIN_REJECTED",
                        "phase": phase,
                        "revision_id": revision_id,
                        "reason": details["reason"],
                        "completed_workers": details["completed_workers"],
                        "failed_workers": details["failed_workers"],
                        "rejected_workers": details["rejected_workers"],
                    },
                )
            except OSError:
                logger.exception(
                    "PARALLEL_FAILURE_VERDICT_WRITE_FAILED task_id=%s phase=%s revision_id=%s",
                    self.ctx.task_id,
                    phase,
                    revision_id,
                )
        self.store.save_state(self.ctx)
        write_context(self.lifecycle, self.ctx)
        return Event("AGENT_REPLY_REJECTED", {
            "reason": details["reason"],
            "resume_state": state_name,
            "max_retries": self.ctx.max_reply_retries,
            "parallel_failure": details,
        })

    def _parallel_timeout_event(self, state_name: str, reason: str) -> Event:
        """Persist a synchronous fan-out deadline before returning AGENT_TIMEOUT.

        Parallel dispatch happens inside one FSM poll.  The normal outer poll
        timeout cannot run until that call returns, so the stage deadline must
        create the same durable timeout metadata itself.
        """
        phase = "ZHONGSHU" if state_name.startswith("ZHONGSHU") else self.ctx.current_phase
        self.ctx.resume_state = state_name
        self.ctx.timeout_phase = phase
        self.ctx.timeout_role = self.ctx.current_role
        self.ctx.timeout_request_id = self.ctx.active_request_id
        self.ctx.last_error = {
            "code": "AGENT_TIMEOUT",
            "message": str(reason)[:2000],
            "state": state_name,
            "phase": phase,
            "request_id": self.ctx.active_request_id,
        }
        pending = {
            "name": "AGENT_TIMEOUT",
            "payload": {
                "request_id": self.ctx.active_request_id,
                "reason": str(reason)[:2000],
            },
        }
        self.ctx.request_payload["pending_fsm_event"] = copy.deepcopy(pending)
        self.store.save_state(self.ctx)
        write_context(self.lifecycle, self.ctx)
        logger.warning(
            "ZHONGSHU_STAGE_TIMEOUT task_id=%s state=%s request_id=%s reason=%s",
            self.ctx.task_id,
            state_name,
            self.ctx.active_request_id,
            str(reason)[:500],
        )
        return Event("AGENT_TIMEOUT", copy.deepcopy(pending["payload"]), str(reason))

    def _load_zhongshu_stage_results(
        self,
        state_name: str,
        revision_id: str,
        workers: list[Any],
    ) -> list[dict[str, Any]]:
        """Reuse only validated durable worker results after a process restart."""
        recovered: list[dict[str, Any]] = []
        for worker in workers:
            path = self.lifecycle.stage_result(
                phase="ZHONGSHU",
                revision=revision_id,
                state=worker.role,
                worker_id=worker.worker_id,
            )
            if not path.exists():
                continue
            try:
                envelope = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(envelope, dict):
                    raise ValueError("stage result envelope is not an object")
                if (
                    envelope.get("schema") != "nexus-orchestrator-stage-result-v1"
                    or envelope.get("phase") != "ZHONGSHU"
                    or str(envelope.get("revision") or "") != revision_id
                    or str(envelope.get("state") or "") != worker.role
                ):
                    raise ValueError("stage result metadata mismatch")
                payload = envelope.get("payload")
                if not isinstance(payload, dict):
                    raise ValueError("stage result payload is not an object")
                persisted_worker_id = str(payload.get("worker_id") or "")
                if persisted_worker_id != worker.worker_id:
                    raise ValueError("stage result worker id mismatch")
                logical_request_id = str(payload.get("logical_request_id") or "")
                current_logical = worker.request.request_id
                if not logical_request_id or logical_request_id != current_logical:
                    raise ValueError("stage result logical request id mismatch")
                attempt = payload.get("attempt")
                if type(attempt) is not int or attempt < 1 or payload.get("request_id") != f"{logical_request_id}:attempt-{attempt}":
                    raise ValueError("stage result attempt request id mismatch")
                restored_request = replace(worker.request, request_id=payload["request_id"], context={**worker.request.context, "revision_id": revision_id, "logical_request_id": logical_request_id})
                self._validate_parallel_worker_reply(payload, replace(worker, request=restored_request), self.ctx)
            except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
                logger.warning(
                    "ZHONGSHU_STAGE_RESULT_IGNORED task_id=%s state=%s revision_id=%s worker_id=%s path=%s error=%s",
                    self.ctx.task_id,
                    state_name,
                    revision_id,
                    worker.worker_id,
                    path,
                    str(error)[:300],
                )
                continue
            recovered.append(payload)
            logger.info(
                "ZHONGSHU_STAGE_RESULT_REUSED task_id=%s state=%s revision_id=%s worker_id=%s path=%s",
                self.ctx.task_id,
                state_name,
                revision_id,
                worker.worker_id,
                path,
            )
        return recovered

    def _critic_quorum_error(
        self,
        result: Any,
        quorum: int,
    ) -> str:
        fingerprints = {
            critic_semantic_fingerprint(item)
            for item in (getattr(result, "completed", ()) or ())
            if isinstance(item, dict)
        }
        return (
            f"critic quorum incomplete: expected_at_least={quorum} "
            f"completed={len(getattr(result, 'completed', ()) or ())} "
            f"diagnostic_distinct_fingerprints={len(fingerprints)} "
            f"failures={len(getattr(result, 'failed', ()) or ())} "
            f"rejected={len(getattr(result, 'rejected', ()) or ())}"
        )

    @staticmethod
    def _analyst_evidence_gap_event(
        state_name: str,
        error: AnalystEvidenceGap,
    ) -> Event:
        requirement_ids = list(error.missing_requirement_ids)
        return Event("AGENT_REPLY_ACCEPTED", {
            "action": "HUMAN_GATE",
            "human_required": True,
            "human_gate": {
                "question": (
                    "以下可执行需求尚未能拆成可验证任务，请确认如何处理："
                    + ", ".join(requirement_ids)
                ),
                "next_state": state_name,
            },
            "unknown_requirement_ids": requirement_ids,
            "evidence_gap_context": copy.deepcopy(error.context),
        })

    def _run_analyst_supplement(
        self,
        base: Any,
        revision: str,
        *,
        stage_deadline: float | None = None,
    ) -> Event:
        from .parallel_runtime import ParallelWorker
        evidence_request = self.ctx.request_payload.get("zhongshu_evidence_request") or {}
        critic_review = self.ctx.request_payload.get("zhongshu_critic_review") or {}
        requests_by_item: dict[str, dict[str, Any]] = {}
        requests_by_requirement: dict[str, dict[str, Any]] = {}
        scope_errors: list[str] = []
        known_item_ids: set[str] = set()
        known_requirement_ids: set[str] = {
            str(item.get("requirement_id"))
            for item in self.ctx.request_payload.get("zhongshu_requirement_contract") or []
            if isinstance(item, dict) and item.get("requirement_id")
        }
        analyst_source = self.ctx.request_payload.get("analyst_plan")
        if isinstance(analyst_source, dict):
            known_requirement_ids.update(
                str(item.get("requirement_id"))
                for item in analyst_source.get("requirements") or []
                if isinstance(item, dict) and item.get("requirement_id")
            )
        current_plan = self.ctx.request_payload.get("candidate_plan")
        if isinstance(current_plan, dict):
            for raw_item in (
                current_plan.get("items")
                or current_plan.get("formal_items")
                or []
            ):
                if isinstance(raw_item, dict) and raw_item.get("item_id"):
                    known_item_ids.add(str(raw_item["item_id"]).strip())
            for raw_group in (
                current_plan.get("groups")
                or current_plan.get("formal_groups")
                or []
            ):
                if not isinstance(raw_group, dict):
                    continue
                for raw_item in raw_group.get("items") or []:
                    if isinstance(raw_item, dict) and raw_item.get("item_id"):
                        known_item_ids.add(str(raw_item["item_id"]).strip())
        if not known_item_ids and not known_requirement_ids:
            scope_errors.append("evidence_scope_unavailable")

        def add_request(
            item_id: str,
            finding: dict[str, Any] | None = None,
            request: dict[str, Any] | None = None,
        ) -> None:
            key = str(item_id or "").strip()
            if not key:
                scope_errors.append("missing_item_id")
                return
            if not known_item_ids:
                scope_errors.append("current_plan_item_scope_unavailable")
                return
            if known_item_ids and key not in known_item_ids:
                scope_errors.append(f"unknown_item_id:{key}")
                return
            bucket = requests_by_item.setdefault(
                key,
                {"item_id": key, "finding_ids": [], "questions": [], "missing_evidence": []},
            )
            if not isinstance(finding, dict):
                finding = {}
            finding_id = str(
                (finding or {}).get("finding_id")
                or (finding or {}).get("id")
                or (request or {}).get("finding_id")
                or ""
            ).strip()
            if finding_id and finding_id not in bucket["finding_ids"]:
                bucket["finding_ids"].append(finding_id)
            for field in ("questions_for_analyst", "questions", "required_action", "question"):
                raw = (finding or {}).get(field)
                if raw is None and request:
                    raw = request.get(field)
                values = raw if isinstance(raw, list) else [raw]
                for value in values:
                    if value and value not in bucket["questions"]:
                        bucket["questions"].append(copy.deepcopy(value))
            for value in (finding or {}).get("missing_evidence") or []:
                if value not in bucket["missing_evidence"]:
                    bucket["missing_evidence"].append(copy.deepcopy(value))
            if request and request.get("question") not in bucket["missing_evidence"]:
                bucket["missing_evidence"].append(copy.deepcopy(request["question"]))

        def add_requirement_request(
            requirement_id: str,
            request: dict[str, Any] | None = None,
            finding: dict[str, Any] | None = None,
        ) -> None:
            key = str(requirement_id or "").strip()
            if not key:
                scope_errors.append("missing_requirement_id")
                return
            if known_requirement_ids and key not in known_requirement_ids:
                scope_errors.append(f"unknown_requirement_id:{key}")
                return
            bucket = requests_by_requirement.setdefault(
                key,
                {
                    "requirement_id": key,
                    "finding_ids": [],
                    "questions": [],
                    "missing_evidence": [],
                },
            )
            for source in (finding or {}, request or {}):
                finding_id = str(source.get("finding_id") or source.get("id") or "").strip()
                if finding_id and finding_id not in bucket["finding_ids"]:
                    bucket["finding_ids"].append(finding_id)
                for field in ("questions_for_analyst", "questions", "required_action", "question"):
                    raw = source.get(field)
                    values = raw if isinstance(raw, list) else [raw]
                    for value in values:
                        if value and value not in bucket["questions"]:
                            bucket["questions"].append(copy.deepcopy(value))
                for value in source.get("missing_evidence") or []:
                    if value not in bucket["missing_evidence"]:
                        bucket["missing_evidence"].append(copy.deepcopy(value))
            if request and request.get("question") not in bucket["missing_evidence"]:
                bucket["missing_evidence"].append(copy.deepcopy(request["question"]))

        for request in evidence_request.get("evidence_requests") or []:
            if not isinstance(request, dict):
                scope_errors.append("evidence_request_invalid")
                continue
            request_item_id = str(request.get("item_id") or "").strip()
            request_requirement_id = str(request.get("requirement_id") or "").strip()
            if request_item_id:
                add_request(request_item_id, request=request)
            elif request_requirement_id:
                add_requirement_request(request_requirement_id, request=request)
            else:
                scope_errors.append("evidence_request_scope_missing")

        affected = evidence_request.get("affected_item_ids")
        if not isinstance(affected, list):
            affected = critic_review.get("affected_item_ids")
        for item_id in affected or []:
            add_request(str(item_id))
        raw_findings = evidence_request.get("findings") or critic_review.get("findings") or []
        for finding in raw_findings:
            if not isinstance(finding, dict):
                continue
            item_id = str(finding.get("item_id") or "").strip()
            related = finding.get("affected_item_ids") or []
            if item_id:
                add_request(item_id, finding)
            elif isinstance(related, list) and related:
                for related_item in related:
                    add_request(str(related_item), finding)
            elif isinstance(affected, list) and affected:
                for affected_item in affected:
                    add_request(str(affected_item), finding)
            elif len(requests_by_item) == 1:
                add_request(next(iter(requests_by_item)), finding)
            elif len(requests_by_requirement) == 1:
                add_requirement_request(next(iter(requests_by_requirement)), finding=finding)
            else:
                scope_errors.append(
                    f"finding_without_item_scope:{finding.get('finding_id') or finding.get('id') or 'unknown'}"
                )
        if not requests_by_item and not requests_by_requirement:
            scope_errors.append("no_scoped_evidence_request")

        if scope_errors:
            errors = sorted(set(scope_errors))
            logger.error(
                "ZHONGSHU_EVIDENCE_SCOPE_REJECTED task_id=%s revision_id=%s errors=%s",
                self.ctx.task_id,
                revision,
                ",".join(errors),
            )
            return Event("AGENT_REPLY_ACCEPTED", {
                "action": "HUMAN_GATE",
                "human_required": True,
                "notification": "Critic 的补证请求缺少明确的任务归属，已停止全局补证。",
                "evidence_scope_errors": errors,
                "human_gate": {
                    "question": (
                        "Critic 的补证请求没有完整的 item_id 归属，系统不会创建全局 Analyst Worker。"
                        "请确认是否退回 Critic 重新生成带任务归属的补证请求。"
                    ),
                    "next_state": "ZHONGSHU_CRITIC",
                },
            })

        workers = []
        scoped_requests = [
            ("item", item_id, "", scope)
            for item_id, scope in sorted(requests_by_item.items())
        ] + [
            ("requirement", "", requirement_id, scope)
            for requirement_id, scope in sorted(requests_by_requirement.items())
        ]
        for index, (scope_kind, item_id, requirement_id, scope) in enumerate(scoped_requests, 1):
            raw_scope_label = item_id or f"requirement-{requirement_id}"
            scope_label = "".join(
                char if char.isalnum() or char in {"-", "_"} else "_"
                for char in raw_scope_label
            )[:96] or "global"
            request_id = f"{self.ctx.task_id}:ZHONGSHU_ANALYST:{revision}:evidence:{scope_label}"
            if self.ctx.reply_retry_count:
                request_id += f":retry-{self.ctx.reply_retry_count}"
            evidence_structured_spec = build_structured_output_spec(
                "ZHONGSHU",
                "review-analyst",
                {
                    **base.context,
                    "zhongshu_dispatch_mode": "evidence_supplement",
                },
            )
            evidence_response_template = role_result_template(
                "ZHONGSHU",
                "review-analyst",
                state="ZHONGSHU_ANALYST",
                role_mode="EVIDENCE_SUPPLEMENT",
                schema_hash=(
                    evidence_structured_spec.schema_hash
                    if evidence_structured_spec
                    else ""
                ),
                action="EVIDENCE_SUPPLEMENT_READY",
            )
            evidence_response_template["evidence_updates"] = [{
                "evidence_id": "ev-001",
                "finding_id": "finding-001|null",
                "item_id": item_id or None,
                "requirement_id": requirement_id or None,
                "decision_relevance": "boundary|coverage|dependency|acceptance|risk",
                "source": "path:line or command",
                "conclusion": "verified conclusion or explicit unknown",
                "unknowns": [],
            }]
            prompt = {
                "task": self.ctx.raw_request,
                "mode": "EVIDENCE_SUPPLEMENT",
                "assigned_item_id": item_id or None,
                "assigned_requirement_id": requirement_id or None,
                "assigned_finding_ids": copy.deepcopy(scope["finding_ids"]),
                "assigned_questions": copy.deepcopy(scope["questions"]),
                "assigned_missing_evidence": copy.deepcopy(scope["missing_evidence"]),
                "requirement_contract": self.ctx.request_payload.get("zhongshu_requirement_contract"),
                "analyst_evidence": self.ctx.request_payload.get("analyst_evidence") or self.ctx.request_payload.get("analyst_plan"),
                "current_formal_plan": self.ctx.request_payload.get("candidate_plan"),
                "evidence_request": copy.deepcopy(evidence_request),
                "critic_review": copy.deepcopy(critic_review),
                "human_decision": self.ctx.request_payload.get("human_decision"),
                "scope_rule": (
                    "Only the assigned item_id/Finding may be investigated; never create or change tasks or groups."
                    if scope_kind == "item"
                    else "Only the assigned requirement_id evidence gap may be investigated; never create or change tasks or groups."
                ),
                "instruction": "Only investigate the specified evidence gaps. Preserve task and requirement identities. Do not redesign tasks or implementation. Return evidence_updates with finding_id/item_id/requirement_id, sources, conclusion, decision_relevance (boundary|coverage|dependency|acceptance|risk), and explicit unknowns. Do not execute tests or measurements merely because a future task requires them.",
                "required_response": evidence_response_template,
            }
            evidence_structured_output = (
                evidence_structured_spec.to_dict()
                if evidence_structured_spec
                else None
            )
            request = replace(
                base,
                request_id=request_id,
                idempotency_key=request_id,
                prompt=json.dumps(prompt, ensure_ascii=False),
                structured_output=evidence_structured_output,
                context={
                    **base.context,
                    "revision_id": revision,
                    "zhongshu_dispatch_mode": "evidence_supplement",
                    "worker_lens": f"{scope_kind}-scoped-evidence",
                    "item_id": item_id,
                    "evidence_item_scope": item_id,
                    "evidence_requirement_scope": requirement_id,
                    "evidence_finding_ids": copy.deepcopy(scope["finding_ids"]),
                    "structured_output": copy.deepcopy(evidence_structured_output),
                    "structured_output_role_mode": evidence_structured_spec.role_mode,
                },
            )
            workers.append(ParallelWorker(f"zhongshu_evidence-{scope_label}-{index}", "ZHONGSHU_ANALYST", base.role, request))
        recovered = self._load_zhongshu_stage_results(
            "ZHONGSHU_ANALYST", revision, workers
        )
        recovered_worker_ids = {
            str(item.get("worker_id") or "")
            for item in recovered
            if isinstance(item, dict) and item.get("worker_id")
        }
        for action in ("HUMAN_GATE", "BLOCKED"):
            special = next(
                (
                    item for item in recovered
                    if isinstance(item, dict) and item.get("action") == action
                ),
                None,
            )
            if special is not None:
                return Event(
                    "AGENT_REPLY_ACCEPTED",
                    {
                        **copy.deepcopy(special),
                        "worker_results": copy.deepcopy(recovered),
                    },
                )

        limits = self.admission.limits
        configured_slots = int(
            os.environ.get(
                "ANALYST_MAX_WORKERS",
                str(self.ctx.zhongshu_parallel.get("analyst_max_workers", 3)),
            )
        )
        batch_capacity = max(
            1,
            min(
                6,
                configured_slots,
                int(limits.global_max),
                int(limits.per_task_max),
                int(limits.analyst_max),
            ),
        )
        pending_workers = [
            worker for worker in workers if worker.worker_id not in recovered_worker_ids
        ]
        completed_results = list(recovered)
        last_batch_result = None
        total_batches = (
            (len(pending_workers) + batch_capacity - 1) // batch_capacity
            if pending_workers
            else 0
        )
        for batch_number, offset in enumerate(
            range(0, len(pending_workers), batch_capacity),
            start=1,
        ):
            batch = pending_workers[offset : offset + batch_capacity]
            logger.info(
                "ZHONGSHU_ANALYST_EVIDENCE_BATCH_STARTED task_id=%s revision_id=%s "
                "batch=%s total_batches=%s batch_size=%s item_ids=%s",
                self.ctx.task_id,
                revision,
                batch_number,
                total_batches,
                len(batch),
                ",".join(
                    str(
                        worker.request.context.get("evidence_item_scope")
                        or f"requirement:{worker.request.context.get('evidence_requirement_scope') or ''}"
                    )
                    for worker in batch
                ),
            )
            if stage_deadline is not None and stage_deadline - time.monotonic() <= 0:
                return self._parallel_timeout_event(
                    "ZHONGSHU_ANALYST",
                    "Analyst evidence supplement stage deadline exceeded before batch dispatch",
                )
            result = self.run_parallel_fanout(
                phase="ZHONGSHU",
                revision_id=revision,
                workers=batch,
                fan_in=lambda values: {
                    "action": "EVIDENCE_COLLECTED",
                    "worker_count": len(values),
                },
                validate=lambda payload, worker: self._validate_parallel_worker_reply(
                    payload, worker, self.ctx
                ),
                timeout_seconds=(
                    min(
                        float(os.environ.get("ZHONGSHU_WORKER_TIMEOUT_SEC", "900")),
                        max(0.1, stage_deadline - time.monotonic()),
                    )
                    if stage_deadline is not None
                    else float(os.environ.get("ZHONGSHU_WORKER_TIMEOUT_SEC", "900"))
                ),
                max_attempts=int(os.environ.get("ZHONGSHU_MAX_ATTEMPTS", "1")),
                recovered_results=[],
            )
            last_batch_result = result
            if stage_deadline is not None and time.monotonic() >= stage_deadline:
                return self._parallel_timeout_event(
                    "ZHONGSHU_ANALYST",
                    "Analyst evidence supplement stage deadline exceeded",
                )
            for action in ("HUMAN_GATE", "BLOCKED"):
                special = next(
                    (
                        item for item in result.completed
                        if item.get("action") == action
                    ),
                    None,
                )
                if special is not None:
                    return Event(
                        "AGENT_REPLY_ACCEPTED",
                        {
                            **copy.deepcopy(special),
                            "worker_results": copy.deepcopy(
                                completed_results + list(result.completed)
                            ),
                        },
                    )
            if (
                result.fanin_error
                or len(result.completed) != len(batch)
                or result.failed
                or result.rejected
            ):
                return self._parallel_failure_event(
                    "ZHONGSHU_ANALYST",
                    (
                        "evidence supplement batch incomplete: "
                        f"expected={len(batch)} completed={len(result.completed)} "
                        f"failed={len(result.failed)} rejected={len(result.rejected)} "
                        f"fanin_error={result.fanin_error or 'none'}"
                    ),
                    result,
                )
            completed_results.extend(result.completed)
            logger.info(
                "ZHONGSHU_ANALYST_EVIDENCE_BATCH_COMPLETED task_id=%s revision_id=%s "
                "batch=%s total_batches=%s completed=%s",
                self.ctx.task_id,
                revision,
                batch_number,
                total_batches,
                len(result.completed),
            )
        result_completed = completed_results
        expected_scopes = {
            (
                "item",
                str(worker.request.context.get("evidence_item_scope") or ""),
            )
            if worker.request.context.get("evidence_item_scope")
            else (
                "requirement",
                str(worker.request.context.get("evidence_requirement_scope") or ""),
            )
            for worker in workers
        }
        completed_scopes = {
            (
                "item",
                str(worker.request.context.get("evidence_item_scope") or ""),
            )
            if worker.request.context.get("evidence_item_scope")
            else (
                "requirement",
                str(worker.request.context.get("evidence_requirement_scope") or ""),
            )
            for worker in workers
            if any(
                str(item.get("worker_id") or "") == worker.worker_id
                for item in result_completed
                if isinstance(item, dict)
            )
        }
        if len(result_completed) != len(workers) or completed_scopes != expected_scopes:
            return self._parallel_failure_event(
                "ZHONGSHU_ANALYST",
                "evidence supplement scope coverage incomplete",
                last_batch_result,
            )
        plan = supplement_plan(
            self.ctx.request_payload["analyst_plan"], result_completed
        )
        return Event("AGENT_REPLY_ACCEPTED", {
            "action": "READY_FOR_SOLVER",
            "plan": plan,
            "evidence_packet": copy.deepcopy(plan),
            "analyst_evidence": copy.deepcopy(plan),
            "evidence_supplement": True,
            "worker_results": copy.deepcopy(result_completed),
        })

    def _current_zhongshu_plan(self) -> dict[str, Any]:
        value = self.ctx.request_payload.get("candidate_plan")
        if isinstance(value, dict) and isinstance(value.get("plan"), dict):
            value = value["plan"]
        return copy.deepcopy(value) if isinstance(value, dict) else {}

    def _persist_task_review_queue(self, queue: TaskReviewQueue) -> None:
        """Persist a queue snapshot while the process TaskLock is held."""
        with self._task_review_queue_lock:
            self.ctx.request_payload["zhongshu_task_review_queue"] = queue.to_dict()
            self.ctx.request_payload["zhongshu_task_review_queue_revision"] = queue.revision_id
            self.store.save_state(self.ctx)
            write_context(self.lifecycle, self.ctx)

    @staticmethod
    def _read_stage_result_payload(
        path: str | Path,
        *,
        expected_revision: str = "",
        expected_group_id: str = "",
        expected_item_id: str = "",
        expected_job_id: str = "",
    ) -> dict[str, Any] | None:
        try:
            envelope = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(envelope, dict) or not isinstance(envelope.get("payload"), dict):
            return None
        if envelope.get("phase") != "ZHONGSHU":
            return None
        if expected_revision and str(envelope.get("revision") or "") != expected_revision:
            return None
        if expected_group_id and str(envelope.get("group_id") or "") != expected_group_id:
            return None
        if expected_item_id and str(envelope.get("item_id") or "") != expected_item_id:
            return None
        payload = envelope["payload"]
        payload_job_id = str(payload.get("review_job_id") or "")
        # Task results are addressed by the durable job identity.  Accepting
        # a missing job id here would allow a same-revision/group/item file
        # from an older protocol to be mistaken for the current job.
        if expected_job_id and payload_job_id != expected_job_id:
            return None
        return copy.deepcopy(payload)

    @staticmethod
    def _validate_stored_task_review_payload(
        payload: dict[str, Any],
        job: ReviewJob,
    ) -> str:
        """Revalidate durable task output before recovery or fan-in."""
        spec = build_structured_output_spec(
            "ZHONGSHU",
            "review-critic",
            {
                "active_runtime_state": "ZHONGSHU_CRITIC",
                "target_state": "ZHONGSHU_CRITIC",
                "zhongshu_dispatch_mode": "task_review",
            },
        )
        if spec is None:
            return "ZHONGSHU_TASK_REVIEW_STRUCTURED_SPEC_MISSING"
        protocol_error = validate_role_result_shape(
            payload,
            phase="ZHONGSHU",
            role="review-critic",
            state="ZHONGSHU_CRITIC",
            role_mode="REVIEW_ONE_TASK",
            expected_schema_hash=spec.schema_hash,
        )
        return protocol_error or validate_zhongshu_task_critic_reply(payload, job)

    def _prepare_task_review_queue(
        self,
        plan: dict[str, Any],
        revision: str,
    ) -> TaskReviewQueue:
        plan_hash = canonical_plan_hash(plan)
        previous = task_review_queue_from_payload(
            self.ctx.request_payload.get("zhongshu_task_review_queue")
        )
        if previous is not None and previous.revision_id == revision and previous.plan_hash == plan_hash:
            # A process may stop after the immutable result file is written
            # but before the queue completion checkpoint. Recover that result
            # locally before reclaiming the remote request.
            for job in previous.jobs:
                if (
                    job.status != "RUNNING"
                    or not job.result_path
                    or not job.lease_id
                    or job.attempt < 1
                ):
                    continue
                payload = self._read_stage_result_payload(
                    job.result_path,
                    expected_revision=revision,
                    expected_group_id=job.group_id,
                    expected_item_id=job.item_id,
                    expected_job_id=job.review_job_id,
                )
                if payload is None:
                    continue
                if (
                    self._validate_stored_task_review_payload(payload, job)
                    or str(payload.get("plan_hash") or "") != plan_hash
                    or str(payload.get("reviewed_plan_hash") or "") != plan_hash
                ):
                    continue
                previous.complete(
                    job.review_job_id,
                    job.lease_id,
                    job.attempt,
                    job.result_path,
                )
                logger.info(
                    "TASK_REVIEW_RESULT_RECOVERED task_id=%s revision_id=%s "
                    "job_id=%s group_id=%s item_id=%s source=stage_result",
                    self.ctx.task_id,
                    revision,
                    job.review_job_id,
                    job.group_id,
                    job.item_id,
                )
            return previous
        queue = build_review_jobs(plan, revision, plan_hash=plan_hash)
        if previous is None:
            return queue

        previous_by_identity = {
            (job.group_id, job.item_id): job
            for job in previous.jobs
        }
        for job in queue.jobs:
            old = previous_by_identity.get((job.group_id, job.item_id))
            if (
                old is None
                or old.status != "COMPLETED"
                or old.task_hash != job.task_hash
                or old.dependency_hash != job.dependency_hash
                or not old.result_path
                or not Path(old.result_path).exists()
            ):
                continue
            payload = self._read_stage_result_payload(
                old.result_path,
                expected_revision=previous.revision_id,
                expected_group_id=old.group_id,
                expected_item_id=old.item_id,
                expected_job_id=old.review_job_id,
            )
            if payload is None:
                continue
            payload["revision_id"] = revision
            payload["plan_revision_id"] = revision
            payload["plan_hash"] = plan_hash
            payload["reviewed_plan_hash"] = plan_hash
            payload["group_id"] = job.group_id
            payload["item_id"] = job.item_id
            payload["review_job_id"] = job.review_job_id
            payload["worker_id"] = "carry-forward"
            payload["logical_request_id"] = f"{job.review_job_id}:carry-forward"
            payload["attempt"] = 0
            carry_forward_error = self._validate_stored_task_review_payload(payload, job)
            if carry_forward_error:
                logger.warning(
                    "TASK_REVIEW_CARRY_FORWARD_REJECTED task_id=%s "
                    "revision_id=%s job_id=%s reason=%s",
                    self.ctx.task_id,
                    revision,
                    job.review_job_id,
                    carry_forward_error,
                )
                continue
            result_path = write_stage_result(
                self.lifecycle,
                phase="ZHONGSHU",
                revision=revision,
                state="review-critic",
                group_id=job.group_id,
                item_id=job.item_id,
                worker_id="carry-forward",
                payload=payload,
            )
            queue.carry_forward(
                job.review_job_id,
                str(result_path),
                worker_id="carry-forward",
            )
            logger.info(
                "TASK_REVIEW_RESULT_CARRIED_FORWARD task_id=%s revision_id=%s "
                "group_id=%s item_id=%s previous_revision=%s",
                self.ctx.task_id,
                revision,
                job.group_id,
                job.item_id,
                previous.revision_id,
            )
        return queue

    def _load_task_review_results(self, queue: TaskReviewQueue) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for job in queue.jobs:
            if job.status != "COMPLETED" or not job.result_path:
                continue
            payload = self._read_stage_result_payload(
                job.result_path,
                expected_revision=queue.revision_id,
                expected_group_id=job.group_id,
                expected_item_id=job.item_id,
                expected_job_id=job.review_job_id,
            )
            if payload is None:
                logger.warning(
                    "TASK_REVIEW_RESULT_LOAD_FAILED task_id=%s revision_id=%s "
                    "job_id=%s path=%s",
                    self.ctx.task_id,
                    queue.revision_id,
                    job.review_job_id,
                    job.result_path,
                )
                continue
            validation_error = self._validate_stored_task_review_payload(payload, job)
            if validation_error:
                logger.warning(
                    "TASK_REVIEW_RESULT_PROTOCOL_INVALID task_id=%s revision_id=%s "
                    "job_id=%s reason=%s",
                    self.ctx.task_id,
                    queue.revision_id,
                    job.review_job_id,
                    validation_error,
                )
                continue
            payload.setdefault("review_job_id", job.review_job_id)
            payload.setdefault("group_id", job.group_id)
            payload.setdefault("item_id", job.item_id)
            payload.setdefault("worker_id", job.worker_id)
            results.append(payload)
        return results

    def _build_task_review_worker(
        self,
        plan: dict[str, Any],
        revision: str,
        job: ReviewJob,
        worker_id: str,
    ) -> Any:
        from .models import AgentRequest
        from .parallel_runtime import ParallelWorker

        context = {
            "active_runtime_skill": ACTIVE_SKILL_BY_STATE["ZHONGSHU_CRITIC"],
            "active_runtime_skill_directive": _active_skill_directive("ZHONGSHU_CRITIC"),
            "active_runtime_skill_lock": _runtime_skill_lock("ZHONGSHU_CRITIC"),
            "active_runtime_phase": "ZHONGSHU",
            "active_runtime_state": "ZHONGSHU_CRITIC",
            "target_state": "ZHONGSHU_CRITIC",
            "target_role": "review-critic",
            "reply_correlation_id": job.request_id,
            "structured_output_state": "ZHONGSHU_CRITIC",
            "structured_output_role_mode": "REVIEW_ONE_TASK",
            "zhongshu_dispatch_mode": "task_review",
            "revision_id": revision,
            "review_job_id": job.review_job_id,
            "group_id": job.group_id,
            "item_id": job.item_id,
            "task_hash": job.task_hash,
            "dependency_hash": job.dependency_hash,
        }
        spec = build_structured_output_spec(
            "ZHONGSHU",
            "review-critic",
            context,
        )
        if spec is None:
            raise RuntimeError("ZHONGSHU_TASK_REVIEW_STRUCTURED_SPEC_MISSING")
        prompt_value = json.loads(
            _zhongshu_task_critic_prompt(self.ctx, plan, job, worker_id)
        )
        prompt_value["stable_role_response"] = role_result_template(
            "ZHONGSHU",
            "review-critic",
            task_id=self.ctx.task_id,
            request_id=job.request_id,
            state="ZHONGSHU_CRITIC",
            role_mode=spec.role_mode,
            schema_hash=spec.schema_hash,
        )
        prompt_value["structured_output"] = spec.to_dict()
        prompt_value["response_contract"]["schema_hash"] = spec.schema_hash
        prompt = json.dumps(prompt_value, ensure_ascii=False, separators=(",", ":"))
        logger.info(
            "ZHONGSHU_TASK_REVIEW_PROMPT_READY task_id=%s revision_id=%s "
            "job_id=%s group_id=%s item_id=%s worker_id=%s task_hash=%s "
            "dependency_hash=%s prompt_chars=%s prompt_bytes=%s",
            self.ctx.task_id,
            revision,
            job.review_job_id,
            job.group_id,
            job.item_id,
            worker_id,
            job.task_hash,
            job.dependency_hash,
            len(prompt),
            len(prompt.encode("utf-8")),
        )
        agent_id = str(self.ctx.expected_agent_id or "").strip()
        if not agent_id:
            agent_id = str(self.states["ZHONGSHU_CRITIC"].agent_ids.get("review-critic") or "review-critic")
        request = AgentRequest(
            task_id=self.ctx.task_id,
            request_id=job.request_id,
            agent_id=agent_id,
            role="review-critic",
            phase="ZHONGSHU",
            prompt=prompt,
            idempotency_key=(
                job.idempotency_key
                or f"{self.ctx.task_id}:ZHONGSHU_CRITIC:{revision}:"
                f"{job.group_id}:{job.item_id}:attempt-{job.attempt}"
            ),
            issue_id=self.ctx.issue_id,
            sent_after=self.ctx.last_sent_at,
            context={
                **context,
                "worker_id": worker_id,
                "structured_output": spec.to_dict(),
            },
            target_state="ZHONGSHU_CRITIC",
            target_role="review-critic",
            structured_output=spec.to_dict(),
        )
        return ParallelWorker(worker_id, "ZHONGSHU_CRITIC", "review-critic", request)

    def _run_zhongshu_task_review_state(self) -> Event:
        plan = self._current_zhongshu_plan()
        if not plan:
            return self._parallel_failure_event(
                "ZHONGSHU_CRITIC",
                "ZHONGSHU_TASK_REVIEW_PLAN_MISSING",
            )
        revision = str(
            plan.get("plan_revision_id")
            or self.ctx.request_payload.get("plan_revision_id")
            or f"revision-{self.ctx.sequence + 1}"
        )
        try:
            queue = self._prepare_task_review_queue(plan, revision)
            self._persist_task_review_queue(queue)
        except Exception as error:
            logger.exception(
                "ZHONGSHU_TASK_REVIEW_QUEUE_PREPARE_FAILED task_id=%s "
                "revision_id=%s error=%s",
                self.ctx.task_id,
                revision,
                str(error)[:500],
            )
            return self._parallel_failure_event(
                "ZHONGSHU_CRITIC",
                f"task review queue prepare failed: {type(error).__name__}: {error}",
            )
        configured_slots = int(
            os.environ.get(
                "CRITIC_MAX_WORKERS",
                str(self.ctx.zhongshu_parallel.get("critic_max_workers", 6)),
            )
        )
        worker_count = max(1, min(6, configured_slots))
        worker_attempts = int(
            os.environ.get(
                "ZHONGSHU_MAX_ATTEMPTS",
                str(self.parallel_driver.max_attempts),
            )
        )
        logger.info(
            "ZHONGSHU_TASK_REVIEW_DISPATCH task_id=%s revision_id=%s "
            "total_tasks=%s queued_tasks=%s worker_slots=%s",
            self.ctx.task_id,
            revision,
            len(queue.jobs),
            len(queue.pending_ids()),
            worker_count,
        )
        try:
            queue = self.parallel_driver.run_task_review_queue(
                phase="ZHONGSHU",
                revision_id=revision,
                queue=queue,
                worker_count=worker_count,
                build_request=lambda job, worker_id: self._build_task_review_worker(
                    plan,
                    revision,
                    job,
                    worker_id,
                ),
                validate=lambda payload, worker, job: self._validate_parallel_worker_reply(
                    payload,
                    worker,
                    self.ctx,
                    job,
                ),
                persist_queue=self._persist_task_review_queue,
                timeout_seconds=float(os.environ.get("ZHONGSHU_WORKER_TIMEOUT_SEC", "900")),
                max_attempts=worker_attempts,
                heartbeat=self._refresh_task_lock,
            )
        except Exception as error:
            logger.exception(
                "ZHONGSHU_TASK_REVIEW_QUEUE_FAILED task_id=%s revision_id=%s error=%s",
                self.ctx.task_id,
                revision,
                str(error)[:500],
            )
            return self._parallel_failure_event(
                "ZHONGSHU_CRITIC",
                f"task review queue failed: {type(error).__name__}: {error}",
            )
        results = self._load_task_review_results(queue)
        try:
            report = aggregate_task_review_results(
                revision,
                canonical_plan_hash(plan),
                queue,
                results,
                previous=self.ctx.request_payload.get("zhongshu_critic_review"),
            )
        except Exception as error:
            logger.exception(
                "ZHONGSHU_TASK_REVIEW_FANIN_FAILED task_id=%s revision_id=%s error=%s",
                self.ctx.task_id,
                revision,
                str(error)[:500],
            )
            return self._parallel_failure_event(
                "ZHONGSHU_CRITIC",
                f"task review fan-in failed: {type(error).__name__}: {error}",
            )
        self.ctx.request_payload["zhongshu_critic_review"] = copy.deepcopy(report)
        self.ctx.request_payload["zhongshu_task_review_queue"] = queue.to_dict()
        self.store.save_state(self.ctx)
        write_context(self.lifecycle, self.ctx)
        logger.info(
            "ZHONGSHU_TASK_REVIEW_FANIN task_id=%s revision_id=%s "
            "completed_tasks=%s total_tasks=%s action=%s findings=%s",
            self.ctx.task_id,
            revision,
            report.get("completed_task_count"),
            report.get("total_task_count"),
            report.get("action"),
            len(report.get("findings") or []),
        )
        return Event("AGENT_REPLY_ACCEPTED", report)

    def _run_parallel_state(self, state_name: str) -> Event:
        from .models import AgentRequest
        from .parallel_runtime import ParallelWorker
        decision = self.ctx.request_payload.get("human_decision")
        if isinstance(decision, dict):
            decision_signature = progress_signature(decision)
            if decision_signature != self.ctx.request_payload.get("zhongshu_decision_signature"):
                # Human input changes the dispatch context even when the graph is unchanged.
                self.ctx.request_payload["zhongshu_decision_signature"] = decision_signature
                self.ctx.request_payload["zhongshu_analyst_revision_id"] = f"revision-{self.ctx.sequence + 1}"
                if state_name == "ZHONGSHU_CRITIC" and isinstance(self.ctx.request_payload.get("candidate_plan"), dict):
                    self.ctx.request_payload["candidate_plan"]["plan_revision_id"] = f"revision-{self.ctx.sequence + 1}"
                self.ctx.request_payload.pop("zhongshu_progress", None)
                self.store.save_state(self.ctx)
        if state_name == "ZHONGSHU_CRITIC":
            return self._run_zhongshu_task_review_state()
        state = self.states[state_name]
        base = state.request(self.ctx)
        candidate_plan = self.ctx.request_payload.get("candidate_plan")
        analyst_plan = self.ctx.request_payload.get("analyst_plan")
        candidate_revision = (
            candidate_plan.get("plan_revision_id")
            if isinstance(candidate_plan, dict)
            else ""
        )
        analyst_revision = (
            analyst_plan.get("plan_revision_id")
            if isinstance(analyst_plan, dict)
            else ""
        )
        if state_name == "ZHONGSHU_ANALYST":
            revision = str(
                self.ctx.request_payload.get("zhongshu_analyst_revision_id")
                or self.ctx.request_payload.get("plan_revision_id")
                or analyst_revision
                or f"revision-{self.ctx.sequence + 1}"
            )
            self.ctx.request_payload["zhongshu_analyst_revision_id"] = revision
        else:
            revision = str(
                candidate_revision
                or self.ctx.request_payload.get("plan_revision_id")
                or f"revision-{self.ctx.sequence + 1}"
            )
        workers = []
        dimensions = (
            ("code_architecture", "Analyze code, architecture, call chains, existing reusable capabilities, and direct implementation evidence.")
            if state_name == "ZHONGSHU_ANALYST"
            else ("requirements_boundaries", "Check requirement coverage, evidence alignment, architecture boundaries, and grouping/dependency correctness.")
        )
        if state_name == "ZHONGSHU_ANALYST":
            dimensions = [
                ("code_architecture", "Analyze code, architecture, call chains, existing reusable capabilities, and direct implementation evidence."),
                ("runtime_performance", "Analyze runtime behavior, performance, concurrency, resource lifecycle, and log/test evidence."),
                ("risk_delivery", "Analyze risks, dependencies, compatibility, testing, rollback, and implementation boundaries."),
            ]
        else:
            dimensions = [
                ("requirements_evidence", "Check requirement coverage and claim-to-evidence alignment."),
                ("architecture_grouping", "Check architecture boundaries, reuse, grouping, and dependencies."),
                ("runtime_risk", "Check performance, resource, compatibility, operations, testing, and rollback risks."),
            ]
        zhongshu_worker_timeout = float(
            os.environ.get("ZHONGSHU_WORKER_TIMEOUT_SEC", "900")
        )
        zhongshu_worker_attempts = int(
            os.environ.get("ZHONGSHU_MAX_ATTEMPTS", "1")
        )
        zhongshu_stage_timeout = float(
            os.environ.get(
                "ZHONGSHU_STAGE_TIMEOUT_SEC",
                str(max(zhongshu_worker_timeout * 2 + 60.0, 1860.0)),
            )
        )
        stage_deadline = (
            time.monotonic() + zhongshu_stage_timeout
            if state_name == "ZHONGSHU_ANALYST"
            else None
        )

        def remaining_stage_timeout() -> float:
            if stage_deadline is None:
                return zhongshu_worker_timeout
            return max(0.0, stage_deadline - time.monotonic())

        retry_suffix = (
            f":retry-{self.ctx.reply_retry_count}"
            if self.ctx.reply_retry_count
            else ""
        )
        canonical_requirements = copy.deepcopy(self.ctx.request_payload.get("zhongshu_requirement_contract"))
        if not canonical_requirements and isinstance(analyst_plan, dict) and analyst_plan.get("requirements"):
            canonical_requirements = copy.deepcopy(analyst_plan["requirements"])
            self.ctx.request_payload["zhongshu_requirement_contract"] = copy.deepcopy(canonical_requirements)
        if state_name == "ZHONGSHU_ANALYST" and self.ctx.request_payload.get("zhongshu_evidence_request") and isinstance(analyst_plan, dict):
            if remaining_stage_timeout() <= 0:
                return self._parallel_timeout_event(
                    state_name,
                    "Analyst stage deadline exceeded before evidence supplement dispatch",
                )
            return self._run_analyst_supplement(
                base,
                revision,
                stage_deadline=stage_deadline,
            )
        if state_name == "ZHONGSHU_ANALYST" and not canonical_requirements:
            if remaining_stage_timeout() <= 0:
                return self._parallel_timeout_event(
                    state_name,
                    "Analyst stage deadline exceeded before requirement contract dispatch",
                )
            contract_revision = f"{revision}-contract"
            contract_structured_spec = build_structured_output_spec(
                "ZHONGSHU",
                "review-analyst",
                {
                    **base.context,
                    "contract_mode": True,
                    "zhongshu_dispatch_mode": "requirement_contract",
                },
            )
            contract_structured_output = (
                contract_structured_spec.to_dict()
                if contract_structured_spec
                else None
            )
            contract_worker = ParallelWorker(
                "zhongshu_requirement_contract",
                state_name,
                base.role,
                AgentRequest(
                    task_id=base.task_id,
                    request_id=(
                        f"{self.ctx.task_id}:{state_name}:{contract_revision}:contract"
                        f"{retry_suffix}"
                    ),
                    agent_id=base.agent_id,
                    role=base.role,
                    phase=base.phase,
                    prompt=json.dumps({
                        "task": self.ctx.raw_request,
                        "role": "ZHONGSHU_ANALYST",
                        "mode": "REQUIREMENT_CONTRACT_ONLY",
                        "human_decision": copy.deepcopy(self.ctx.request_payload.get("human_decision")),
                        "role_objective": (
                            "Define the single authoritative requirement contract for this request. "
                            "Do not decompose tasks and do not design implementation."
                        ),
                        "working_rules": [
                            "Extract only user requirements and directly derived hard constraints.",
                            "Assign each requirement one stable requirement_id and never duplicate an id.",
                            "Keep each statement atomic, precise, and stable for downstream workers.",
                            "Separate scope, priority, and observable acceptance_signal.",
                            "Classify each entry with kind=task or kind=constraint; prohibitions and non-goals are constraints, not executable tasks.",
                            "Do not emit task_proposals, candidate_items, candidate_groups, or implementation details.",
                            "Write exactly one complete Analyst role-protocol JSON object to result_path using a real serializer; return only the result pointer and no Markdown or surrounding text.",
                        ],
                        "response_contract": {
                            "success_action": "REQUIREMENT_CONTRACT_READY",
                            "other_actions": ["HUMAN_GATE", "BLOCKED"],
                            "format": "result_file_json_plus_compact_pointer_no_markdown_no_code_fence",
                        },
                    }, ensure_ascii=False, separators=(",", ":")),
                    idempotency_key=f"{base.idempotency_key}:contract{retry_suffix}",
                    issue_id=base.issue_id,
                    sent_after=base.sent_after,
                    context={
                        **base.context,
                        "revision_id": contract_revision,
                        "contract_mode": True,
                        "zhongshu_dispatch_mode": "requirement_contract",
                        "structured_output": copy.deepcopy(contract_structured_output),
                        "structured_output_role_mode": contract_structured_spec.role_mode,
                    },
                    target_state=state_name,
                    target_role=base.role,
                    structured_output=contract_structured_output,
                ),
            )
            contract_result = self.run_parallel_fanout(
                phase="ZHONGSHU",
                revision_id=contract_revision,
                workers=[contract_worker],
                fan_in=lambda values: {"worker_count": len(values), "contract": True},
                validate=lambda payload, worker: self._validate_parallel_worker_reply(
                    payload, worker, self.ctx
                ),
                timeout_seconds=min(
                    zhongshu_worker_timeout,
                    remaining_stage_timeout(),
                ),
                max_attempts=zhongshu_worker_attempts,
                recovered_results=self._load_zhongshu_stage_results(
                    state_name, contract_revision, [contract_worker]
                ),
            )
            if (
                contract_result.fanin_error
                or len(contract_result.completed) != 1
                or contract_result.failed
                or contract_result.rejected
            ):
                if remaining_stage_timeout() <= 0:
                    return self._parallel_timeout_event(
                        state_name,
                        "Analyst stage deadline exceeded during requirement contract",
                    )
                contract_failures = ";".join(
                    f"{item.get('worker_id')}:{item.get('error') or item.get('reason')}"
                    for item in (*contract_result.failed, *contract_result.rejected)
                ) or contract_result.fanin_error or "unknown"
                logger.error(
                    "ZHONGSHU_REQUIREMENT_CONTRACT_FAILED task_id=%s revision_id=%s reason=%s",
                    self.ctx.task_id,
                    contract_revision,
                    contract_failures,
                )
                return self._parallel_failure_event(
                    state_name,
                    f"requirement contract unavailable: {contract_failures}",
                    contract_result,
                )
            canonical_requirements = copy.deepcopy(
                contract_result.completed[0].get("requirements") or []
            )
            if contract_result.completed[0].get("action") in {"HUMAN_GATE", "BLOCKED"}:
                return Event("AGENT_REPLY_ACCEPTED", copy.deepcopy(contract_result.completed[0]))
            self.ctx.request_payload["zhongshu_requirement_contract"] = copy.deepcopy(canonical_requirements)
            self.ctx.request_payload["zhongshu_requirement_contract_revision"] = contract_revision
            self.store.save_state(self.ctx)
            logger.info(
                "ZHONGSHU_REQUIREMENT_CONTRACT_READY task_id=%s revision_id=%s requirements=%s worker_id=%s",
                self.ctx.task_id,
                contract_revision,
                len(canonical_requirements),
                contract_result.completed[0].get("worker_id"),
            )
        analyst_agent_ids: list[str] = []
        if state_name == "ZHONGSHU_ANALYST":
            configured = [
                value.strip()
                for value in os.environ.get("AGENT_ANALYST_IDS", "").split(",")
                if value.strip()
            ]
            if len(configured) >= 3:
                analyst_agent_ids = configured[:3]
            else:
                shared = str(base.agent_id or "").strip()
                analyst_agent_ids = [
                    os.environ.get(f"AGENT_ANALYST_{index}_ID", "").strip() or shared
                    for index in range(1, 4)
                ]
            if len(set(analyst_agent_ids)) < 3:
                logger.warning(
                    "ZHONGSHU_ANALYST_SHARED_EXTERNAL_TARGET task_id=%s revision_id=%s "
                    "agent_ids=%s reason=configure_AGENT_ANALYST_IDS_for_independent_runs",
                    self.ctx.task_id,
                    revision,
                    analyst_agent_ids,
                )
        critic_agent_ids: list[str] = []
        if state_name == "ZHONGSHU_CRITIC":
            configured = [
                value.strip()
                for value in os.environ.get("AGENT_CRITIC_IDS", "").split(",")
                if value.strip()
            ]
            if len(configured) >= 3:
                critic_agent_ids = configured[:3]
            else:
                shared = str(base.agent_id or "").strip()
                critic_agent_ids = [
                    os.environ.get(f"AGENT_CRITIC_{index}_ID", "").strip() or shared
                    for index in range(1, 4)
                ]
            if len(set(critic_agent_ids)) < 3:
                logger.warning(
                    "ZHONGSHU_CRITIC_SHARED_EXTERNAL_TARGET task_id=%s revision_id=%s "
                    "agent_ids=%s reason=configure_AGENT_CRITIC_IDS_for_independent_runs",
                    self.ctx.task_id,
                    revision,
                    critic_agent_ids,
                )
        for index in range(3):
            worker_id = f"{state_name.lower()}-{index + 1}"
            dimension_name, dimension_instruction = dimensions[index]
            request_id = (
                f"{self.ctx.task_id}:{state_name}:{revision}:{index + 1}"
                f"{retry_suffix}"
            )
            worker_prompt = base.prompt
            if state_name == "ZHONGSHU_ANALYST":
                base_prompt_value: dict[str, Any] = {}
                try:
                    parsed_base_prompt = json.loads(base.prompt)
                    if isinstance(parsed_base_prompt, dict):
                        base_prompt_value = parsed_base_prompt
                except (TypeError, json.JSONDecodeError):
                    base_prompt_value = {}
                worker_prompt_value = {
                    "task": self.ctx.raw_request,
                    "role": "ZHONGSHU_ANALYST",
                    "mode": "EVIDENCE_COLLECTION_READ_ONLY",
                    "worker_lens": dimension_name,
                    "role_objective": (
                        "Collect a traceable evidence packet for Solver. Do not create or group tasks."
                    ),
                    "lens_instruction": dimension_instruction,
                    "limits": {
                        "max_evidence_updates": ANALYST_MAX_EVIDENCE_UPDATES_PER_WORKER,
                        "max_evidence_requests": ANALYST_MAX_EVIDENCE_REQUESTS_PER_WORKER,
                        "max_questions_for_solver": 0,
                    },
                    "working_rules": [
                        f"Return at most {ANALYST_MAX_EVIDENCE_UPDATES_PER_WORKER} concise evidence_updates; merge observations that support the same decision.",
                        f"Return at most {ANALYST_MAX_EVIDENCE_REQUESTS_PER_WORKER} evidence_requests, and only when a missing fact blocks Solver; every request must name item_id or requirement_id, question, and reason.",
                        "Copy every requirement object from requirement_contract verbatim and in the same order, including requirement_id, statement, source, priority, scope, kind, and acceptance_signal; never paraphrase, omit, invent, reorder, or redefine these immutable fields. The orchestrator owns this contract; you only add evidence.",
                        "Record only decision-relevant facts that change requirement coverage, task boundaries, dependencies, acceptance, scope, or risk.",
                        "Every evidence update should identify one requirement_id when applicable and use one concise conclusion.",
                        "Do not restate the same evidence in confirmed_facts and evidence_updates unless the fact adds unique confidence or scope information.",
                        "questions_for_solver must be [] unless a specific missing fact blocks task decomposition; do not ask open-ended implementation or optimization questions.",
                        "Record confirmed facts, source references, risks, unknowns, and conflicts.",
                        "Do not create, name, group, split, merge, or prioritize tasks.",
                        "Do not return candidate_groups, implementation_proposal, file_changes, code_changes, or function-level design.",
                        "Review the request independently through your assigned evidence lens; do not copy another worker's observations without checking the source.",
                        "Return task_proposals, candidate_items, and candidate_groups as empty arrays.",
                        "Write exactly one complete Analyst role-protocol JSON object to result_path using a real serializer; return only the result pointer and no Markdown.",
                    ],
                    "requirement_contract": copy.deepcopy(canonical_requirements or []),
                    "zhongshu_dispatch_mode": "evidence_collection",
                }
                for field_name in (
                    "critic_feedback",
                    "repair_instruction",
                    "repair_feedback",
                    "context_summary",
                    "questions_for_solver",
                    "human_decision",
                    "evidence_request",
                    "evidence_gap_context",
                ):
                    if field_name in base_prompt_value:
                        worker_prompt_value[field_name] = copy.deepcopy(
                            base_prompt_value[field_name]
                        )
                worker_prompt = json.dumps(
                    worker_prompt_value,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            else:
                base_prompt_value = {}
                try:
                    parsed_base_prompt = json.loads(base.prompt)
                    if isinstance(parsed_base_prompt, dict):
                        base_prompt_value = parsed_base_prompt
                except (TypeError, json.JSONDecodeError):
                    base_prompt_value = {}
                if not base_prompt_value:
                    base_prompt_value = {
                        "task": self.ctx.raw_request,
                        "role": state_name,
                        "mode": "REVIEW_CURRENT_TASK_GRAPH",
                        "base_prompt_fallback": base.prompt,
                    }
                base_prompt_value.update({
                    "worker_id": worker_id,
                    "worker_index": index + 1,
                    "worker_count": len(dimensions),
                    "worker_lens": dimension_name,
                    "lens_instruction": dimension_instruction,
                    "independence_instruction": (
                        "Review independently through this worker_lens. Do not assume, copy, "
                        "or paraphrase another worker's conclusion. Make lens-specific checks "
                        "visible in review_summary and evidence; agreement is allowed when independently justified."
                    ),
                        "worker_output_rules": [
                            f"Use {worker_id}:<local-id> for new finding IDs; preserve historical IDs.",
                            "Write exactly one complete Critic role-protocol JSON object to result_path using a real serializer; return only the result pointer and no Markdown or surrounding text.",
                            "Include at least one lens-specific checked claim or evidence reference in review_summary or evidence_alignment; do not return a copied generic review.",
                        ],
                })
                worker_prompt = json.dumps(
                    base_prompt_value,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            workers.append(
                ParallelWorker(
                    worker_id,
                    state_name,
                    base.role,
                    AgentRequest(
                        task_id=base.task_id,
                        request_id=request_id,
                        agent_id=(
                            critic_agent_ids[index]
                            if state_name == "ZHONGSHU_CRITIC"
                            else analyst_agent_ids[index]
                            if state_name == "ZHONGSHU_ANALYST"
                            else base.agent_id
                        ),
                        role=base.role,
                        phase=base.phase,
                        prompt=worker_prompt,
                        idempotency_key=(
                            f"{base.idempotency_key}:{index + 1}{retry_suffix}"
                        ),
                        issue_id=base.issue_id,
                        sent_after=base.sent_after,
                        context={
                            **base.context,
                            "revision_id": revision,
                            "requirement_contract": copy.deepcopy(canonical_requirements or []),
                            "zhongshu_dispatch_mode": (
                                "evidence_collection"
                                if state_name == "ZHONGSHU_ANALYST"
                                else "critic_review"
                            ),
                            "worker_lens": dimension_name,
                            "critic_source_id": (
                                critic_agent_ids[index]
                                if state_name == "ZHONGSHU_CRITIC"
                                else ""
                            ),
                        },
                        target_state=state_name,
                        target_role=base.role,
                        structured_output=copy.deepcopy(base.structured_output),
                    ),
                )
            )
        if state_name == "ZHONGSHU_CRITIC":
            from .zhongshu_parallel import CriticConflictResolver
            plan = self.ctx.request_payload.get("candidate_plan") or {}
            plan_hash = canonical_plan_hash(plan)
            resolver = CriticConflictResolver()
            critic_quorum = 2 if len(workers) >= 3 else len(workers)
            worker_lenses = {
                worker.worker_id: dimensions[index][0]
                for index, worker in enumerate(workers)
            }
            report: dict[str, Any] = {}
            def aggregate_once(values: list[dict[str, Any]]) -> dict[str, Any]:
                enriched_values: list[dict[str, Any]] = []
                for value in values:
                    enriched = copy.deepcopy(value)
                    body = (
                        enriched.get("payload")
                        if isinstance(enriched.get("payload"), dict)
                        else enriched
                    )
                    if isinstance(body, dict):
                        worker_id = str(
                            body.get("worker_id")
                            or enriched.get("worker_id")
                            or ""
                        )
                        body.setdefault("worker_lens", worker_lenses.get(worker_id))
                    enriched_values.append(enriched)
                report.update(resolver.aggregate(
                    revision, plan_hash, enriched_values,
                    previous=self.ctx.request_payload.get("zhongshu_critic_review"),
                    quorum=critic_quorum, expected_workers={worker.worker_id for worker in workers},
                ).to_dict())
                return copy.deepcopy(report)
            result = self.run_parallel_fanout(
                phase="ZHONGSHU",
                revision_id=revision,
                workers=workers,
                fan_in=aggregate_once,
                validate=lambda payload, worker: self._validate_parallel_worker_reply(
                    payload, worker, self.ctx
                ),
                timeout_seconds=zhongshu_worker_timeout,
                max_attempts=zhongshu_worker_attempts,
                recovered_results=self._load_zhongshu_stage_results(
                    state_name, revision, workers
                ),
            )
            critic_quorum = 2 if len(workers) >= 3 else len(workers)
            distinct_fingerprints = {
                critic_semantic_fingerprint(item)
                for item in result.completed
                if isinstance(item, dict)
            }
            logger.info(
                "ZHONGSHU_CRITIC_VALID_QUORUM task_id=%s revision_id=%s "
                "completed=%s quorum=%s diagnostic_distinct=%s fingerprints=%s valid=%s approvals=%s",
                self.ctx.task_id,
                revision,
                len(result.completed),
                critic_quorum,
                len(distinct_fingerprints),
                sorted(distinct_fingerprints),
                len(report.get("valid_worker_ids") or []),
                report.get("approval_count", 0),
            )
            if (
                result.fanin_error
                or len(result.completed) < critic_quorum
            ):
                reason = result.fanin_error or self._critic_quorum_error(
                    result, critic_quorum
                )
                return self._parallel_failure_event(state_name, reason, result)
            return Event("AGENT_REPLY_ACCEPTED", report)
        if remaining_stage_timeout() <= 0:
            return self._parallel_timeout_event(
                state_name,
                "Analyst stage deadline exceeded before evidence fan-out",
            )
        result = self.run_parallel_fanout(
            phase="ZHONGSHU",
            revision_id=revision,
            workers=workers,
            fan_in=lambda values: {
                "action": "TASK_GRAPH_FANIN",
                "worker_count": len(values),
            },
            validate=lambda payload, worker: self._validate_parallel_worker_reply(
                payload, worker, self.ctx
            ),
            timeout_seconds=min(
                zhongshu_worker_timeout,
                remaining_stage_timeout(),
            ),
            max_attempts=zhongshu_worker_attempts,
            recovered_results=self._load_zhongshu_stage_results(
                state_name, revision, workers
            ),
        )
        if (
            state_name == "ZHONGSHU_ANALYST"
            and remaining_stage_timeout() <= 0
            and len(result.completed) != len(workers)
        ):
            return self._parallel_timeout_event(
                state_name,
                "Analyst evidence fan-out exceeded stage deadline",
            )
        for action in ("HUMAN_GATE", "BLOCKED"):
            special = next((item for item in result.completed if item.get("action") == action), None)
            if special is not None:
                return Event("AGENT_REPLY_ACCEPTED", {**copy.deepcopy(special), "worker_results": copy.deepcopy(list(result.completed))})
        if result.fanin_error or len(result.completed) != len(workers):
            failures = ";".join(
                f"{item.get('worker_id')}:{item.get('error') or item.get('reason')}"
                for item in (*result.failed, *result.rejected)
            ) or result.fanin_error or "unknown"
            return self._parallel_failure_event(
                state_name,
                (
                    f"analyst evidence coverage incomplete: expected={len(workers)} "
                    f"completed={len(result.completed)} failures={failures}"
                ),
                result,
            )
        try:
            payload = merge_analyst_evidence(
                self.ctx.task_id,
                revision,
                list(result.completed),
                canonical_requirements=canonical_requirements,
            )
            validation_error = validate_zhongshu_evidence_packet(
                {
                    **copy.deepcopy(payload.get("plan") or {}),
                    "action": "EVIDENCE_PACKET_READY",
                    "phase": "ZHONGSHU",
                },
                canonical_requirements=canonical_requirements,
                max_evidence_updates=ANALYST_MAX_EVIDENCE_UPDATES_MERGED,
                max_evidence_requests=ANALYST_MAX_EVIDENCE_REQUESTS,
                require_decision_relevance=True,
            )
        except AnalystEvidenceGap as error:
            return self._analyst_evidence_gap_event(state_name, error)
        except ValueError as error:
            return self._parallel_failure_event(state_name, str(error), result)
        if validation_error:
            return self._parallel_failure_event(state_name, validation_error, result)
        logger.info(
            "ZHONGSHU_EVIDENCE_PACKET_READY task_id=%s revision_id=%s workers=%s evidence_updates=%s requirements=%s",
            self.ctx.task_id,
            revision,
            ",".join(payload.get("worker_ids") or []),
            len(payload.get("plan", {}).get("evidence_updates", [])),
            len(payload.get("plan", {}).get("requirements", [])),
        )
        return Event("AGENT_REPLY_ACCEPTED", payload)

    @staticmethod
    def _validate_parallel_worker_reply(
        payload: dict[str, Any],
        worker: Any,
        ctx: StateContext | None = None,
        review_job: Any | None = None,
    ) -> None:
        if payload.get("action") == "__UNSTRUCTURED_REPLY__":
            raise ValueError("REPLY_BODY_NOT_STRUCTURED")
        target_state = str(worker.request.target_state or "")
        canonical_requirements = (
            worker.request.context.get("requirement_contract")
            if target_state == "ZHONGSHU_ANALYST"
            and isinstance(worker.request.context.get("requirement_contract"), list)
            else None
        )
        if payload.get("action") == "EVIDENCE_PACKET_READY":
            bound_payload, _binding_notes = bind_zhongshu_requirement_contract(
                payload,
                canonical_requirements,
                worker_id=worker.worker_id,
            )
            payload.clear()
            payload.update(bound_payload)
        if isinstance(worker.request.structured_output, dict):
            protocol_error = validate_role_result_shape(
                payload,
                phase=worker.request.phase,
                role=worker.request.role,
                state=target_state,
                role_mode=str(
                    worker.request.context.get("structured_output_role_mode")
                    or worker.request.structured_output.get("role_mode")
                    or ""
                ),
                expected_schema_hash=str(
                    worker.request.structured_output.get("schema_hash") or ""
                ),
            )
            if protocol_error:
                raise ValueError(protocol_error)
        is_requirement_contract = payload.get("action") == "REQUIREMENT_CONTRACT_READY"
        allowed = _allowed_actions(target_state)
        if target_state == "ZHONGSHU_ANALYST":
            mode = "requirement_contract" if worker.request.context.get("contract_mode") else str(worker.request.context.get("zhongshu_dispatch_mode") or "evidence_collection")
            allowed = set(ANALYST_ACTIONS.get(mode, allowed))
        elif target_state == "ZHONGSHU_CRITIC":
            allowed = set(
                TASK_CRITIC_ACTIONS
                if str(worker.request.context.get("zhongshu_dispatch_mode") or "") == "task_review"
                else CRITIC_ACTIONS
            )
        if not allowed:
            allowed = {"READY_FOR_SOLVER", "HUMAN_GATE", "BLOCKED", "APPROVE_FREEZE", "REQUEST_ANALYST_EVIDENCE", "REQUEST_SOLVER_REVISION", "REQUEST_REGROUP"}
        result = validate_agent_reply(
            ExternalMessage(
                worker.request.agent_id,
                payload,
                payload.get("request_id", worker.request.request_id),
            ),
            AgentBinding(
                author_id=worker.request.agent_id,
                task_id=worker.request.task_id,
                request_id=worker.request.request_id,
                role=worker.request.role,
                phase=worker.request.phase,
                target_state=worker.request.target_state,
                target_role=worker.request.target_role,
            ),
            allowed,
        )
        if isinstance(result, RejectedReply):
            raise ValueError(result.reason)
        if payload.get("phase") != worker.request.phase:
            raise ValueError("PARALLEL_REPLY_PHASE_MISMATCH")
        if payload.get("worker_id") != worker.worker_id:
            raise ValueError("PARALLEL_REPLY_WORKER_ID_MISMATCH")
        revision = str(worker.request.context.get("revision_id") or "")
        if target_state.startswith("ZHONGSHU") and revision and any(str(payload[key]) != revision for key in ("revision_id", "plan_revision_id") if payload.get(key)):
            raise ValueError("PARALLEL_REPLY_REVISION_MISMATCH")
        scope_error = _finding_scope_error(
            payload,
            target_state,
            group_id=str(worker.request.context.get("group_id") or ""),
            item_id=str(
                worker.request.context.get("item_id")
                or worker.request.context.get("active_item_id")
                or ""
            ),
        )
        if scope_error:
            raise ValueError(scope_error)
        if target_state == "ZHONGSHU_ANALYST":
            if payload.get("action") in {"HUMAN_GATE", "BLOCKED"}:
                return
            if payload.get("action") == "EVIDENCE_SUPPLEMENT_READY":
                if not isinstance(payload.get("evidence_updates"), list):
                    raise ValueError("ZHONGSHU_EVIDENCE_UPDATES_MISSING")
                reason = validate_zhongshu_evidence_packet(
                    payload,
                    canonical_requirements=(
                        worker.request.context.get("requirement_contract")
                        if isinstance(worker.request.context.get("requirement_contract"), list)
                        else None
                    ),
                    max_evidence_updates=ANALYST_MAX_EVIDENCE_UPDATES_PER_WORKER,
                    max_evidence_requests=ANALYST_MAX_EVIDENCE_REQUESTS_PER_WORKER,
                    require_decision_relevance=True,
                )
                if reason:
                    raise ValueError(reason)
                expected_item_id = str(worker.request.context.get("evidence_item_scope") or "")
                expected_requirement_id = str(
                    worker.request.context.get("evidence_requirement_scope") or ""
                )
                if not expected_item_id and not expected_requirement_id:
                    raise ValueError("ZHONGSHU_EVIDENCE_SCOPE_MISSING")
                scoped_updates = [
                    item for item in payload.get("evidence_updates")
                    if isinstance(item, dict)
                ]
                if not scoped_updates or any(
                    (
                        str(item.get("item_id") or "") != expected_item_id
                        if expected_item_id
                        else str(item.get("requirement_id") or "") != expected_requirement_id
                    )
                    for item in scoped_updates
                ):
                    raise ValueError("ZHONGSHU_EVIDENCE_SCOPE_MISMATCH")
                supplement_plan((ctx.request_payload.get("analyst_plan") or {}) if ctx else {}, [payload])
                return
            if payload.get("action") == "EVIDENCE_PACKET_READY":
                reason = validate_zhongshu_evidence_packet(
                    payload,
                    canonical_requirements=canonical_requirements,
                    max_evidence_updates=ANALYST_MAX_EVIDENCE_UPDATES_PER_WORKER,
                    max_evidence_requests=ANALYST_MAX_EVIDENCE_REQUESTS_PER_WORKER,
                    require_decision_relevance=True,
                )
                if reason:
                    raise ValueError(reason)
                return
            if is_requirement_contract:
                reason = validate_zhongshu_requirement_contract(payload)
                if reason:
                    raise ValueError(reason)
                return
            reason = validate_zhongshu_task_graph(payload)
            if reason:
                raise ValueError(reason)
            contract = worker.request.context.get("requirement_contract")
            if isinstance(contract, list) and payload.get("requirements"):
                expected = {
                    str(item.get("requirement_id")): item
                    for item in contract
                    if isinstance(item, dict) and item.get("requirement_id")
                }
                for item in payload["requirements"]:
                    if not isinstance(item, dict):
                        raise ValueError("ZHONGSHU_REQUIREMENT_CONTRACT_VIOLATION")
                    requirement_id = str(item.get("requirement_id") or "")
                    reference = expected.get(requirement_id)
                    if reference is None or any(
                        item.get(field) != reference.get(field)
                        for field in (
                            "statement",
                            "source",
                            "priority",
                            "scope",
                            "kind",
                            "acceptance_signal",
                        )
                    ):
                        raise ValueError(
                            f"ZHONGSHU_REQUIREMENT_CONTRACT_VIOLATION:{requirement_id}"
                        )
        elif target_state == "ZHONGSHU_CRITIC":
            if ctx is None:
                raise ValueError("ZHONGSHU_CRITIC_CONTEXT_MISSING")
            task_review = str(
                worker.request.context.get("zhongshu_dispatch_mode") or ""
            ) == "task_review"
            if task_review:
                if review_job is None:
                    raise ValueError("ZHONGSHU_TASK_REVIEW_JOB_MISSING")
                reason = validate_zhongshu_task_critic_reply(payload, review_job)
            else:
                identity_error = review_identity_error(
                    payload,
                    revision,
                    canonical_plan_hash(ctx.request_payload.get("candidate_plan") or {}),
                )
                if identity_error:
                    raise ValueError(identity_error)
                reason = _validate_zhongshu_critic_reply(payload, ctx)
            if reason:
                raise ValueError(reason)
        if target_state == "MENXIA_ITEM_SOLVER":
            if payload.get("action") in {"FEASIBLE", "READY_FOR_CRITIC"} and not isinstance(payload.get("implementation_proposal"), dict):
                raise ValueError("MENXIA_SOLVER_PROPOSAL_MISSING")
        elif target_state == "MENXIA_ITEM_ANALYST":
            reason = _validate_menxia_analyst_reply(payload)
            if reason:
                raise ValueError(reason)
        elif target_state == "MENXIA_ITEM_CRITIC":
            reason = _validate_menxia_critic_reply(payload)
            if reason:
                raise ValueError(reason)

    def run_parallel_fanout(
        self,
        *,
        phase: str,
        revision_id: str,
        workers: list[Any],
        fan_in: Any,
        validate: Any = None,
        timeout_seconds: float | None = None,
        max_attempts: int | None = None,
        recovered_results: list[dict[str, Any]] | None = None,
    ) -> Any:
        """Run one bounded production fan-out through the app-owned driver."""
        max_external_workers = external_target_parallelism(workers)
        if max_external_workers < len(workers):
            target_keys = sorted({
                f"{worker.request.issue_id or worker.request.task_id}:"
                f"{worker.request.agent_id}"
                for worker in workers
            })
            logger.warning(
                "PARALLEL_SHARED_EXTERNAL_TARGET_SERIALIZED task_id=%s phase=%s "
                "revision_id=%s workers=%s max_workers=%s targets=%s",
                self.ctx.task_id,
                phase,
                revision_id,
                len(workers),
                max_external_workers,
                target_keys,
            )
        logger.info(
            "PARALLEL_COORDINATOR_DISPATCH task_id=%s phase=%s revision_id=%s workers=%s",
            self.ctx.task_id,
            phase,
            revision_id,
            len(workers),
        )
        result = self.parallel_driver.run(
            phase=phase,
            revision_id=revision_id,
            workers=workers,
            fan_in=fan_in,
            validate=validate,
            timeout_seconds=timeout_seconds,
            max_attempts=max_attempts,
            max_workers=max_external_workers,
            recovered_results=recovered_results,
            heartbeat=self._refresh_task_lock,
        )
        self.ctx.request_payload.setdefault("parallel_runs", []).append({
            "phase": phase,
            "revision_id": revision_id,
            "completed": [item.get("worker_id") for item in result.completed],
            "failed": [item.get("worker_id") for item in result.failed],
            "rejected": [item.get("worker_id") for item in result.rejected],
            "fanin_error": result.fanin_error,
        })
        self.store.save_state(self.ctx)
        write_context(self.lifecycle, self.ctx)
        return result

    def inspect(self) -> dict[str, Any]:
        value = self.ctx.to_dict()
        value["event_count"] = len(self.store.event_sequences())
        return value

    def _ensure_loaded(self) -> None:
        if self.store.state_path.exists():
            self.ctx = self.store.load()
            self.machine.ctx = self.ctx

    def _ensure_started(self) -> None:
        self._ensure_loaded()
        if not self.store.state_path.exists():
            self.machine.start()
        elif self.ctx.workflow_state == "REQUEST_INTAKE":
            self.machine.start()
        else:
            state = self.states[self.ctx.workflow_state]
            pending_event = self.ctx.request_payload.get("pending_fsm_event")
            # A legacy event-first transition could leave the destination
            # state durable while its enter() checkpoint was not.  Re-entering
            # only when the request identity is absent is idempotent and
            # rebuilds the deterministic idempotency key for this sequence.
            if (
                self.ctx.workflow_state
                in {
                    "ZHONGSHU_ANALYST",
                    "ZHONGSHU_SOLVER",
                    "ZHONGSHU_CRITIC",
                    "MENXIA_ITEM_SOLVER",
                    "MENXIA_ITEM_ANALYST",
                    "MENXIA_ITEM_CRITIC",
                }
                and not self.ctx.active_request_id
            ):
                state.enter(self.ctx)
                self.store.save_state(self.ctx)
            if isinstance(pending_event, dict) and pending_event.get("name"):
                self._pending_parallel_event = Event(
                    str(pending_event["name"]),
                    copy.deepcopy(pending_event.get("payload") or {}),
                    str(pending_event.get("reason") or ""),
                )
                self.store.save_state(self.ctx)
                return
            self._dispatch_pending(self.ctx, state)
            self.store.save_state(self.ctx)

    def _refresh_task_lock(self) -> None:
        """Refresh the process lock while synchronous parallel fan-in runs."""
        lock = self._task_lock
        if lock is None:
            return
        now = time.monotonic()
        with self._lock_refresh_guard:
            if now - self._last_lock_refresh < 5.0:
                return
            lock.refresh()
            self._last_lock_refresh = now

    def _normalize_analyst_event(self, event: Event, state: str) -> Event:
        if state != "ZHONGSHU_ANALYST" or event.action not in {
            "EVIDENCE_PACKET_READY", "EVIDENCE_SUPPLEMENT_READY",
        }:
            return event
        revision = str(
            self.ctx.request_payload.get("plan_revision_id")
            or self.ctx.request_payload.get("zhongshu_analyst_revision_id")
            or f"revision-{self.ctx.sequence + 1}"
        )
        if event.action == "EVIDENCE_PACKET_READY":
            payload = merge_analyst_evidence(
                self.ctx.task_id,
                revision,
                [event.payload],
                canonical_requirements=self.ctx.request_payload.get("zhongshu_requirement_contract"),
            )
        elif event.action == "EVIDENCE_SUPPLEMENT_READY":
            plan = supplement_plan(
                self.ctx.request_payload.get("analyst_plan") or {},
                [event.payload],
            )
            payload = {
                "action": "READY_FOR_SOLVER",
                "plan": plan,
                "evidence_packet": copy.deepcopy(plan),
                "analyst_evidence": copy.deepcopy(plan),
                "evidence_supplement": True,
                "worker_results": [copy.deepcopy(event.payload)],
            }
        else:
            raise ValueError("ZHONGSHU_ANALYST_TASK_PROPOSALS_FORBIDDEN")
        return Event("AGENT_REPLY_ACCEPTED", payload)

    def _next_event(self) -> Event:
        state = self.ctx.workflow_state
        if getattr(self, "_pending_parallel_event", None) is not None:
            event, self._pending_parallel_event = self._pending_parallel_event, None
            if event.name == "AGENT_REPLY_ACCEPTED":
                if state == "ZHONGSHU_ANALYST" and event.action in {"EVIDENCE_PACKET_READY", "EVIDENCE_SUPPLEMENT_READY"}:
                    try:
                        event = self._normalize_analyst_event(event, state)
                    except AnalystEvidenceGap as error:
                        event = self._analyst_evidence_gap_event(state, error)
                    except ValueError as error:
                        return Event(
                            "AGENT_REPLY_REJECTED",
                            {
                                "reason": str(error),
                                "resume_state": state,
                                "max_retries": self.ctx.max_reply_retries,
                            },
                        )
                scope_error = ""
                if not (
                    state == "ZHONGSHU_CRITIC"
                    and event.payload.get("task_review_mode") is True
                ):
                    scope_error = _finding_scope_error(
                        event.payload,
                        state,
                        group_id=str(self.ctx.active_group_id or ""),
                        item_id=str(self.ctx.active_item_id or ""),
                    )
                if scope_error:
                    return Event(
                        "AGENT_REPLY_REJECTED",
                        {
                            "reason": scope_error,
                            "resume_state": state,
                            "max_retries": self.ctx.max_reply_retries,
                        },
                    )
                self.ctx.last_error = None
                self._consume_agent_payload(event.payload)
                self._decorate_progress_event(event)
                if event.action == "HUMAN_GATE":
                    gate = _human_gate_from_payload(event.payload, self.ctx.raw_request)
                    next_state = str(
                        gate.get("next_state")
                        or event.payload.get("next_state")
                        or _default_human_gate_resume_state(state)
                    )
                    self.ctx.resume_state = next_state
                    remaining_questions = gate.pop("remaining_questions", [])
                    self.ctx.request_payload["human_gate"] = copy.deepcopy(gate)
                    self.ctx.request_payload["human_gate_queue"] = copy.deepcopy(
                        remaining_questions
                    )
                    self.ctx.request_payload["human_gate_prompt"] = _human_gate_prompt(
                        gate,
                        self.ctx.raw_request,
                    )
                    self.ctx.request_payload["human_gate_next_state"] = next_state
                    self.ctx.request_payload["human_gate_source_state"] = state
            return event
        if state == "REQUEST_INTAKE":
            return Event("AGENT_REPLY_ACCEPTED", {"action": "START"})
        if state in {"ZHONGSHU_ANALYST", "ZHONGSHU_SOLVER", "ZHONGSHU_CRITIC",
                     "MENXIA_ITEM_SOLVER", "MENXIA_ITEM_ANALYST", "MENXIA_ITEM_CRITIC",
                     "MENXIA_GROUP_GATE"}:
            if self.ctx.dispatch_status == "error":
                error_reason = (
                    self.ctx.last_error.get("message", "dispatch failed")
                    if isinstance(self.ctx.last_error, dict)
                    else str(self.ctx.last_error or "dispatch failed")
                )
                return Event(
                    "MULTICA_ERROR",
                    {
                        "resume_state": state,
                        "max_retries": self.ctx.max_external_retries,
                        "reason": error_reason,
                    },
                )
            elapsed_seconds = self._state_elapsed_seconds()
            if elapsed_seconds >= self.timeout_seconds:
                release_lease = getattr(self.states[state], "release_active_lease", None)
                if release_lease:
                    release_lease(self.ctx)
                self.ctx.timeout_phase = self.ctx.current_phase
                self.ctx.timeout_role = self.ctx.current_role
                self.ctx.timeout_request_id = self.ctx.active_request_id
                logger.warning(
                    "AGENT_TIMEOUT_TRIGGERED task_id=%s state=%s role=%s request_id=%s elapsed_seconds=%.3f timeout_seconds=%s",
                    self.ctx.task_id,
                    state,
                    self.ctx.current_role,
                    self.ctx.active_request_id,
                    elapsed_seconds,
                    self.timeout_seconds,
                )
                return Event(
                    "AGENT_TIMEOUT",
                    {
                        "request_id": self.ctx.active_request_id,
                        "reason": "agent timeout",
                    },
                )
            logger.info(
                "POLL_START task_id=%s state=%s role=%s request_id=%s elapsed_seconds=%.3f timeout_seconds=%s",
                self.ctx.task_id,
                state,
                self.ctx.current_role,
                self.ctx.active_request_id,
                elapsed_seconds,
                self.timeout_seconds,
            )
            try:
                event = self.states[state].update(self.ctx)
            except (RuntimeError, ValueError) as error:
                self.ctx.resume_state = state
                self.ctx.last_error = {
                    "code": (
                        "AGENT_PROMPT_BUILD_FAILED"
                        if isinstance(error, ValueError)
                        else "MULTICA_ERROR"
                    ),
                    "message": str(error),
                    "state": state,
                    "request_id": self.ctx.active_request_id,
                }
                logger.warning(
                    "AGENT_PROMPT_BUILD_FAILED task_id=%s state=%s request_id=%s "
                    "error_type=%s message=%s",
                    self.ctx.task_id,
                    state,
                    self.ctx.active_request_id,
                    type(error).__name__,
                    str(error),
                )
                return Event("MULTICA_ERROR", {
                    "resume_state": state,
                    "max_retries": self.ctx.max_external_retries,
                }, str(error))
            if event.name == "AGENT_REPLY_ACCEPTED":
                # Direct (non-parallel) Analyst replies use the same explicit
                # unknown-to-human-gate path as the parallel fan-in.
                if state == "ZHONGSHU_ANALYST" and event.action in {"EVIDENCE_PACKET_READY", "EVIDENCE_SUPPLEMENT_READY"}:
                    try:
                        event = self._normalize_analyst_event(event, state)
                    except AnalystEvidenceGap as error:
                        event = self._analyst_evidence_gap_event(state, error)
                    except ValueError as error:
                        return Event(
                            "AGENT_REPLY_REJECTED",
                            {
                                "reason": str(error),
                                "resume_state": state,
                                "max_retries": self.ctx.max_reply_retries,
                            },
                        )
                scope_error = ""
                if not (
                    state == "ZHONGSHU_CRITIC"
                    and event.payload.get("task_review_mode") is True
                ):
                    scope_error = _finding_scope_error(
                        event.payload,
                        state,
                        group_id=str(self.ctx.active_group_id or ""),
                        item_id=str(self.ctx.active_item_id or ""),
                    )
                if scope_error:
                    return Event(
                        "AGENT_REPLY_REJECTED",
                        {
                            "reason": scope_error,
                            "resume_state": state,
                            "max_retries": self.ctx.max_reply_retries,
                        },
                    )
                self._consume_agent_payload(event.payload)
                self._decorate_progress_event(event)
                if event.action == "HUMAN_GATE":
                    gate = _human_gate_from_payload(event.payload, self.ctx.raw_request)
                    next_state = str(
                        gate.get("next_state")
                        or event.payload.get("next_state")
                        or _default_human_gate_resume_state(state)
                    )
                    self.ctx.resume_state = next_state
                    remaining_questions = gate.pop("remaining_questions", [])
                    self.ctx.request_payload["human_gate"] = copy.deepcopy(gate)
                    self.ctx.request_payload["human_gate_queue"] = copy.deepcopy(remaining_questions)
                    self.ctx.request_payload["human_gate_prompt"] = _human_gate_prompt(
                        gate,
                        self.ctx.raw_request,
                    )
                    self.ctx.request_payload["human_gate_next_state"] = next_state
                    self.ctx.request_payload["human_gate_source_state"] = state
                    if self.ctx.workflow_state == "MENXIA_ITEM_CRITIC":
                        self.ctx.request_payload["human_gate_purpose"] = "P2_RISK"
            elif event.name == "NOOP" and self._heartbeat_due():
                logger.info(
                    "HEARTBEAT_DUE task_id=%s state=%s request_id=%s elapsed_seconds=%.3f",
                    self.ctx.task_id,
                    state,
                    self.ctx.active_request_id,
                    self._state_elapsed_seconds(),
                )
                self.states[state].notify_heartbeat(
                    self.ctx,
                    int(self._state_elapsed_seconds()),
                )
                self.store.save_state(self.ctx)
            logger.info(
                "POLL_END task_id=%s state=%s request_id=%s event=%s action=%s",
                self.ctx.task_id,
                state,
                self.ctx.active_request_id,
                event.name,
                event.action,
            )
            return event
        if state == "ZHONGSHU_FREEZE_CHECK":
            return self._freeze_check_event()
        if state == "HUMAN_GATE":
            if self.ctx.last_error and self.ctx.last_error.get("code") == "FEISHU_DELIVERY_FAILED":
                return Event("HUMAN_GATE_DELIVERY_FAILED", {"reason": "feishu delivery failed"})
            try:
                event = self.states[state].update(self.ctx)
            except RuntimeError as error:
                return Event("FEISHU_REQUEST_FAILED", {"reason": str(error)})
            if event.name == "NOOP" and self._human_gate_expired():
                return Event("HUMAN_GATE_TIMEOUT", {"reason": "human decision timeout"})
            return event
        if state == "TIMEOUT":
            if self.ctx.timeout_retry_count >= self.ctx.max_timeout_retries:
                return Event("RETRY_LIMIT_REACHED", {"reason": "timeout retry limit reached"})
            if self.ctx.timeout_request_id:
                return Event("RESUME", {
                    "resume_state": self._resume_state_for_timeout(),
                })
            return Event("RESUME_CONTEXT_MISSING")
        if state == "HUMAN_GATE_TIMEOUT":
            return Event("NOOP")
        if state == "HUMAN_GATE_ERROR":
            return Event("RETRY")
        if state == "INVALID_AGENT_REPLY":
            return Event("RETRY", {"resume_state": self.ctx.resume_state or "ZHONGSHU_SOLVER"})
        if state == "MULTICA_ERROR":
            return Event("RETRY", {"resume_state": self.ctx.resume_state or "ZHONGSHU_SOLVER"})
        return Event("NOOP")

    def _promote_supplemented_zhongshu_findings(
        self,
        evidence_request: Any,
        supplement_payload: dict[str, Any],
    ) -> None:
        """Move completed evidence findings into the Solver revision queue."""
        review = self.ctx.request_payload.get("zhongshu_critic_review")
        if not isinstance(review, dict):
            return

        requested_ids: set[str] = set()

        def collect_ids(value: Any) -> None:
            if not isinstance(value, dict):
                return
            finding_id = str(value.get("finding_id") or value.get("id") or "").strip()
            if finding_id:
                requested_ids.add(finding_id)

        for source in (evidence_request, supplement_payload):
            if not isinstance(source, dict):
                continue
            for finding in source.get("findings") or []:
                collect_ids(finding)
            for request in source.get("evidence_requests") or []:
                collect_ids(request)
            for key in ("finding_ids", "analyst_finding_ids", "solver_unresolved_finding_ids"):
                for finding_id in source.get(key) or []:
                    finding_id = str(finding_id).strip()
                    if finding_id:
                        requested_ids.add(finding_id)
            for result in source.get("worker_results") or []:
                if not isinstance(result, dict):
                    continue
                for update in result.get("evidence_updates") or []:
                    collect_ids(update)

        if not requested_ids:
            logger.warning(
                "ZHONGSHU_EVIDENCE_SUPPLEMENT_FINDING_IDS_MISSING task_id=%s",
                self.ctx.task_id,
            )
            return

        promoted: list[str] = []
        updated_review = copy.deepcopy(review)
        for finding in updated_review.get("findings") or []:
            if not isinstance(finding, dict):
                continue
            finding_id = str(finding.get("finding_id") or "").strip()
            if not finding_id or finding_id not in requested_ids:
                continue
            owner = str(
                finding.get("owner_role") or finding.get("owner") or ""
            ).strip().lower()
            action = str(finding.get("next_action") or "").strip().upper()
            if owner in {"human", "user"} or action in {"HUMAN_GATE", "BLOCKED"}:
                continue
            if action in {"REQUEST_ANALYST_EVIDENCE", "NEEDS_MORE_EVIDENCE"}:
                finding["previous_next_action"] = action
                finding["next_action"] = "REQUEST_SOLVER_REVISION"
                finding["owner_role"] = "review-solver"
                finding["evidence_supplemented"] = True
                promoted.append(finding_id)

        if not promoted:
            return
        updated_review["action"] = "REQUEST_SOLVER_REVISION"
        updated_review["evidence_supplemented_finding_ids"] = sorted(promoted)
        self.ctx.request_payload["zhongshu_critic_review"] = updated_review
        logger.info(
            "ZHONGSHU_FINDINGS_REQUEUED_TO_SOLVER task_id=%s finding_ids=%s",
            self.ctx.task_id,
            sorted(promoted),
        )

    def _consume_agent_payload(self, payload: dict[str, Any]) -> None:
        self._save_artifact_for_state(payload)
        self.ctx.last_agent_payload = dict(payload)
        action = str(payload.get("action", ""))
        if self.ctx.workflow_state == "ZHONGSHU_ANALYST":
            plan = payload.get("plan")
            if action == "READY_FOR_SOLVER" and isinstance(plan, dict):
                self.ctx.request_payload["analyst_plan"] = copy.deepcopy(plan)
                self.ctx.request_payload["analyst_evidence"] = copy.deepcopy(
                    payload.get("evidence_packet") or payload.get("analyst_evidence") or plan
                )
                if payload.get("evidence_supplement"):
                    evidence_request = self.ctx.request_payload.pop(
                        "zhongshu_evidence_request",
                        None,
                    )
                    self.ctx.request_payload["zhongshu_evidence_request_completed"] = evidence_request
                    self._promote_supplemented_zhongshu_findings(
                        evidence_request,
                        payload,
                    )
            if payload.get("evidence_gap_context"):
                self.ctx.request_payload["zhongshu_evidence_gap"] = copy.deepcopy(payload["evidence_gap_context"])
        elif self.ctx.workflow_state == "ZHONGSHU_SOLVER":
            self.ctx.request_payload["zhongshu_solver_response"] = copy.deepcopy(payload)
            finding_batch = payload.get("finding_batch")
            if isinstance(finding_batch, dict):
                self.ctx.request_payload["zhongshu_solver_finding_batch"] = copy.deepcopy(finding_batch)
                logger.info(
                    "SOLVER_FINDING_BATCH task_id=%s selected=%s remaining=%s "
                    "next_action=%s progress=%s",
                    self.ctx.task_id,
                    finding_batch.get("selected_finding_ids") or [],
                    finding_batch.get("remaining_finding_ids") or [],
                    finding_batch.get("next_action") or "",
                    finding_batch.get("progress") or {},
                )
            pending_finding_ids = payload.get("pending_finding_ids")
            if isinstance(pending_finding_ids, list):
                self.ctx.request_payload["zhongshu_pending_finding_ids"] = [
                    str(item) for item in pending_finding_ids if str(item).strip()
                ]
            plan = copy.deepcopy(_plan_from_payload(payload))
            if action in {"READY_FOR_CRITIC", "REQUEST_ANALYST_EVIDENCE", "HUMAN_GATE"}:
                groups = plan.get("groups") or plan.get("formal_groups")
                if isinstance(groups, list):
                    canonical_plan, semantic_duplicates = canonicalize_task_graph(plan)
                    if not semantic_duplicates:
                        plan = canonical_plan
                        self.ctx.request_payload.pop("zhongshu_canonicalization_issue", None)
                    else:
                        self.ctx.request_payload["zhongshu_canonicalization_issue"] = {
                            "kind": "semantic_duplicate_tasks",
                            "item_groups": copy.deepcopy(semantic_duplicates),
                        }
                    plan["plan_revision_id"] = f"revision-{self.ctx.sequence + 1}"
                    self.ctx.request_payload["plan_revision_id"] = plan["plan_revision_id"]
                    self.ctx.request_payload["candidate_plan"] = plan
            if action in {"REQUEST_ANALYST_EVIDENCE", "NEEDS_MORE_EVIDENCE"}:
                self.ctx.request_payload["zhongshu_evidence_request"] = copy.deepcopy(payload)
                self.ctx.request_payload["zhongshu_analyst_revision_id"] = (
                    f"revision-{self.ctx.sequence + 1}"
                )
                critic_review = self.ctx.request_payload.get("zhongshu_critic_review")
                if isinstance(critic_review, dict):
                    critic_review = copy.deepcopy(critic_review)
                    critic_review["action"] = "REQUEST_ANALYST_EVIDENCE"
                    critic_review["solver_route_reason"] = str(
                        payload.get("route_reason")
                        or "Solver reported unresolved Critic findings"
                    )
                    critic_review["solver_unresolved_finding_ids"] = list(
                        payload.get("analyst_finding_ids") or []
                    )
                    self.ctx.request_payload["zhongshu_critic_review"] = critic_review
        elif self.ctx.workflow_state == "ZHONGSHU_CRITIC":
            self._update_findings(payload)
            self.ctx.request_payload["zhongshu_critic_review"] = copy.deepcopy(payload)
            if action == "REQUEST_ANALYST_EVIDENCE":
                self.ctx.request_payload["zhongshu_evidence_request"] = copy.deepcopy(payload)
                self.ctx.request_payload["zhongshu_analyst_revision_id"] = (
                    f"revision-{self.ctx.sequence + 1}"
                )
        elif self.ctx.workflow_state == "MENXIA_ITEM_SOLVER":
            signature = _semantic_reply_signature(payload)
            if signature == self.ctx.last_reply_fingerprint:
                self.ctx.no_progress_count += 1
            else:
                self.ctx.last_reply_fingerprint = signature
                self.ctx.no_progress_count = 0
            proposal = payload.get("implementation_proposal")
            proposal_value = (
                copy.deepcopy(proposal) if isinstance(proposal, dict) else copy.deepcopy(payload)
            )
            item_id = str(self.ctx.active_item_id or "")
            critic_review = _current_item_payload(
                self.ctx.request_payload,
                "item_critic_reviews",
                item_id,
            ) or {}
            critic_finding_ids = _critic_finding_ids(critic_review)
            response_ids = _solver_response_finding_ids(payload)
            proposals = self.ctx.request_payload.setdefault("item_implementation_proposals", {})
            if item_id:
                proposals[item_id] = copy.deepcopy(proposal_value)
            self.ctx.request_payload["implementation_proposal"] = copy.deepcopy(proposal_value)
            self.ctx.request_payload["notification_payload"] = copy.deepcopy(proposal_value)
            logger.info(
                "AGENT_REPLY_MERGE task_id=%s state=%s group_id=%s item_id=%s "
                "action=%s merged_fields=%s preserved_global_keys=%s",
                self.ctx.task_id,
                self.ctx.workflow_state,
                self.ctx.active_group_id or "",
                item_id,
                action,
                sorted(proposal_value.keys()),
                ["frozen_plan", "candidate_plan", "active_group", "active_item"],
            )
            logger.info(
                "SOLVER_CRITIC_RESPONSE_MERGE task_id=%s state=%s group_id=%s item_id=%s "
                "critic_finding_count=%s critic_finding_ids=%s response_field_present=%s "
                "responded_finding_ids=%s unresponded_finding_ids=%s",
                self.ctx.task_id,
                self.ctx.workflow_state,
                self.ctx.active_group_id or "",
                item_id,
                len(critic_finding_ids),
                critic_finding_ids,
                "responses_to_critic" in payload,
                response_ids,
                [finding_id for finding_id in critic_finding_ids if finding_id not in response_ids],
            )
        elif self.ctx.workflow_state == "MENXIA_ITEM_ANALYST":
            review = copy.deepcopy(payload)
            item_id = str(self.ctx.active_item_id or "")
            reviews = self.ctx.request_payload.setdefault("item_analyst_reviews", {})
            if item_id:
                reviews[item_id] = copy.deepcopy(review)
            self.ctx.request_payload["analyst_review"] = review
            self.ctx.request_payload["notification_payload"] = copy.deepcopy(review)
        elif self.ctx.workflow_state == "MENXIA_ITEM_CRITIC":
            self._update_findings(
                payload,
                group_id=str(self.ctx.active_group_id or ""),
                item_id=str(self.ctx.active_item_id or ""),
            )
            review = copy.deepcopy(payload)
            item_id = str(self.ctx.active_item_id or "")
            reviews = self.ctx.request_payload.setdefault("item_critic_reviews", {})
            if item_id:
                reviews[item_id] = copy.deepcopy(review)
            self.ctx.request_payload["critic_review"] = review
            self.ctx.request_payload["notification_payload"] = copy.deepcopy(review)
        # Commit the business merge only after its artifact/stage evidence is
        # durable.  If the process stops before this point, restart polls or
        # reuses the same correlated reply instead of observing a half-merged
        # context with no corresponding artifact.
        self.store.save_state(self.ctx)

    def _decorate_progress_event(self, event: Event) -> None:
        state = self.ctx.workflow_state
        if state.startswith("ZHONGSHU") and event.action == "BLOCKED" and event.payload.get("human_required"):
            event.payload["original_action"] = "BLOCKED"
            event.payload["action"] = "HUMAN_GATE"
            event.payload.setdefault("human_gate", {"question": str(event.payload.get("unblock_condition") or event.payload.get("notification") or "请明确中书省阻塞项的处理方式。")})
        if state in {"ZHONGSHU_SOLVER", "ZHONGSHU_CRITIC"} and event.action in {"REQUEST_ANALYST_EVIDENCE", "NEEDS_MORE_EVIDENCE", "REQUEST_SOLVER_REVISION", "REQUEST_REGROUP"}:
            signature = progress_signature({
                "plan": self.ctx.request_payload.get("candidate_plan"),
                "facts": (self.ctx.request_payload.get("analyst_plan") or {}).get("confirmed_facts"),
                "evidence": (self.ctx.request_payload.get("analyst_plan") or {}).get("evidence_updates"),
                "findings": [{key: value for key, value in item.items() if key in {"finding_id", "claim", "status", "severity", "evidence_ids"}} for item in (self.ctx.request_payload.get("zhongshu_critic_review") or {}).get("findings", []) if isinstance(item, dict)],
                "human_decision": self.ctx.request_payload.get("human_decision"),
            })
            progress = self.ctx.request_payload.setdefault("zhongshu_progress", {})
            previous = progress.get(state, {})
            unchanged = int(previous.get("unchanged", 0)) + 1 if previous.get("signature") == signature else 0
            progress[state] = {"signature": signature, "unchanged": unchanged}
            if unchanged >= 3:
                self.ctx.request_payload["zhongshu_stalled_reply"] = copy.deepcopy(event.payload)
                event.payload["action"] = "HUMAN_GATE"
                event.payload["human_required"] = True
                event.payload["human_gate"] = {"question": "中书省连续三轮没有新增有效证据、任务变化或问题解决，请明确剩余问题的处理方式。", "next_state": state}
                event.payload["remaining_work"] = copy.deepcopy(self.ctx.request_payload.get("zhongshu_evidence_request") or self.ctx.request_payload.get("zhongshu_critic_review") or {})
        if event.action == "APPROVE_ITEM" and self.ctx.workflow_state == "MENXIA_ITEM_CRITIC":
            groups = self.ctx.request_payload.get("frozen_plan", {}).get("groups", [])
            group = groups[self.ctx.group_index or 0]
            items = group.get("items", [])
            current_item = self.ctx.item_index or 0
            if current_item + 1 < len(items):
                self.ctx.item_index = current_item + 1
                self._set_active_group_item(group, current_item + 1)
                event.payload["next_state"] = "MENXIA_ITEM_SOLVER"
            else:
                self.ctx.item_index = None
                self.ctx.active_item_id = None
                self.ctx.request_payload["active_item"] = None
                event.payload["next_state"] = "MENXIA_GROUP_GATE"
        elif event.action == "REQUEST_GROUP_REVISION" and self.ctx.workflow_state == "MENXIA_GROUP_GATE":
            groups = self.ctx.request_payload.get("frozen_plan", {}).get("groups", [])
            current_group = self.ctx.group_index or 0
            group = groups[current_group] if current_group < len(groups) else {}
            items = group.get("items", []) if isinstance(group, dict) else []
            target_item_id = str(
                event.payload.get("item_id")
                or event.payload.get("revision_item_id")
                or ""
            )
            target_index = next(
                (
                    index
                    for index, item in enumerate(items)
                    if isinstance(item, dict)
                    and str(item.get("item_id") or "") == target_item_id
                ),
                0,
            )
            self.ctx.item_index = target_index
            if isinstance(group, dict):
                self._set_active_group_item(group, target_index)
            event.payload["next_state"] = "MENXIA_ITEM_SOLVER"
        elif event.action in {"APPROVE_GROUP", "APPROVE_FREEZE"} and self.ctx.workflow_state == "MENXIA_GROUP_GATE":
            groups = self.ctx.request_payload.get("frozen_plan", {}).get("groups", [])
            current_group = self.ctx.group_index or 0
            if current_group + 1 < len(groups):
                next_group = groups[current_group + 1]
                self.ctx.group_index = current_group + 1
                self.ctx.item_index = 0
                self._set_active_group_item(next_group, 0)
                event.payload["next_state"] = "MENXIA_ITEM_SOLVER"
            else:
                event.payload["next_state"] = "DONE"

    def _save_artifact_for_state(self, payload: dict[str, Any]) -> None:
        artifact_dir = self.root / "artifacts"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_id = f"artifact-{self.ctx.sequence + 1:06d}"
        envelope = {
            "artifact_id": artifact_id,
            "task_id": self.ctx.task_id,
            "workflow_state": self.ctx.workflow_state,
            "group_id": self.ctx.active_group_id,
            "item_id": self.ctx.active_item_id,
            "created_at": time.time(),
            "payload": payload,
        }
        atomic_json(artifact_dir / f"{artifact_id}.json", envelope)
        phase = "ZHONGSHU" if str(self.ctx.workflow_state).startswith("ZHONGSHU") else "MENXIA"
        revision = str(
            self.ctx.request_payload.get("plan_revision_id")
            or self.ctx.request_payload.get("frozen_plan", {}).get("plan_revision_id")
            or self.ctx.request_payload.get("candidate_plan", {}).get("plan_revision_id")
            or "current"
        )
        try:
            stage_path = write_stage_result(
                self.lifecycle,
                phase=phase,
                revision=revision,
                state=self.ctx.workflow_state,
                payload=payload,
                group_id=self.ctx.active_group_id or "",
                item_id=self.ctx.active_item_id or "",
            )
            logger.info(
                "LIFECYCLE_STAGE_RESULT_WRITTEN task_id=%s phase=%s revision=%s "
                "state=%s group_id=%s item_id=%s path=%s",
                self.ctx.task_id,
                phase,
                revision,
                self.ctx.workflow_state,
                self.ctx.active_group_id or "",
                self.ctx.active_item_id or "",
                stage_path,
            )
        except Exception:
            logger.exception(
                "LIFECYCLE_STAGE_RESULT_WRITE_FAILED task_id=%s state=%s",
                self.ctx.task_id,
                self.ctx.workflow_state,
            )
            raise
        self.ctx.last_artifact_id = artifact_id


    def _write_final_delivery_files(self) -> None:
        delivery = self.ctx.request_payload.get("final_delivery")
        if not isinstance(delivery, dict):
            logger.warning(
                "FINAL_DELIVERY_FILE_SKIPPED task_id=%s reason=missing_final_delivery",
                self.ctx.task_id,
            )
            return
        markdown_path = self.root / "final_delivery.md"
        rendered = build_agent_notification(
            "review-critic",
            "DONE",
            "AGENT_REPLY_ACCEPTED",
            self.ctx,
            {"final_delivery": delivery},
            limit=100000,
        )
        markdown_path.write_text(rendered, encoding="utf-8", newline="\n")
        logger.info(
            "FINAL_DELIVERY_FILE_WRITTEN task_id=%s markdown=%s "
            "markdown_chars=%s",
            self.ctx.task_id,
            markdown_path,
            len(rendered),
        )

    def _update_findings(
        self,
        payload: dict[str, Any],
        *,
        group_id: str = "",
        item_id: str = "",
    ) -> None:
        findings = payload.get("findings")
        if not isinstance(findings, list):
            return
        scope_group = str(group_id or "").strip()
        scope_item = str(item_id or "").strip()
        if scope_item and not scope_group:
            raise ValueError("finding item_id requires group_id")
        existing = {
            finding.identity_key(): finding
            for finding in self.ctx.finding_objects()
        }
        severity_rank = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
        updates: list[Finding] = []
        for item in findings:
            if not isinstance(item, dict):
                continue
            finding_id = str(item.get("finding_id") or item.get("id") or "").strip()
            if not finding_id:
                continue
            finding_group = str(item.get("group_id") or "").strip()
            finding_item = str(item.get("item_id") or "").strip()
            effective_group = scope_group or finding_group
            effective_item = scope_item or finding_item
            previous = existing.get((effective_group, effective_item, finding_id))
            normalized = (
                previous.to_dict()
                if previous is not None
                else {}
            )
            normalized.update(copy.deepcopy(item))
            if "decision" in item and "status" not in item:
                normalized.pop("status", None)
            if previous is not None:
                incoming = str(normalized.get("severity") or "").upper()
                if (
                    incoming not in severity_rank
                    or severity_rank[incoming] > severity_rank.get(previous.severity.upper(), 3)
                ):
                    normalized["severity"] = previous.severity
            if not normalized.get("severity"):
                continue
            normalized["finding_id"] = finding_id
            normalized["group_id"] = effective_group
            normalized["item_id"] = effective_item
            updates.append(Finding.from_dict(normalized))
        self.ctx.merge_findings(updates)

    def _freeze_check_event(self) -> Event:
        plan = self.ctx.request_payload.get("candidate_plan")
        if not isinstance(plan, dict):
            return Event("FREEZE_REJECTED", {"reason": "solver plan missing"})
        from .states import _validate_solver_plan
        validation_error = _validate_solver_plan({"action": "READY_FOR_CRITIC", "plan": plan}, self.ctx.request_payload.get("analyst_plan"))
        if validation_error:
            return Event("FREEZE_REJECTED", {"reason": validation_error})
        _, semantic_duplicates = canonicalize_task_graph(plan)
        if semantic_duplicates:
            issue = {
                "finding_id": "orchestrator:semantic-duplicate-task",
                "severity": "P1",
                "status": "open",
                "title": "Semantic duplicate task items require Solver decision",
                "claim": "The final task graph contains multiple items with the same objective and requirement provenance.",
                "affected_item_ids": semantic_duplicates,
                "owner_role": "review-solver",
                "next_action": "REQUEST_SOLVER_REVISION",
                "required_action": "Explicitly merge or distinguish the duplicate items, then send the resulting items through Critic review again.",
            }
            review = copy.deepcopy(self.ctx.request_payload.get("zhongshu_critic_review") or {})
            review["action"] = "REQUEST_SOLVER_REVISION"
            review["findings"] = [
                item for item in review.get("findings") or []
                if isinstance(item, dict) and item.get("finding_id") != issue["finding_id"]
            ] + [issue]
            review["affected_item_ids"] = sorted({
                item_id
                for group in semantic_duplicates
                for item_id in group
            })
            self.ctx.request_payload["zhongshu_critic_review"] = review
            return Event(
                "FREEZE_REJECTED",
                {
                    "reason": "semantic duplicate task items require Solver revision",
                    "duplicate_item_groups": semantic_duplicates,
                },
            )
        if blocking_unknowns(plan):
            return Event("FREEZE_REJECTED", {"reason": "unresolved blocking unknowns", "unknowns": blocking_unknowns(plan)})
        if self._parallel_enabled() and self.ctx.zhongshu_parallel.get("enabled"):
            review = self.ctx.request_payload.get("zhongshu_critic_review") or {}
            expected_revision = str(plan.get("plan_revision_id") or "")
            expected_hash = canonical_plan_hash(plan)
            if review.get("task_review_mode") is True:
                try:
                    total_tasks = int(review.get("total_task_count") or 0)
                    completed_tasks = int(review.get("completed_task_count") or 0)
                except (TypeError, ValueError):
                    total_tasks = completed_tasks = -1
                queue_counts = review.get("task_queue_counts")
                queue_total = -1
                if isinstance(queue_counts, dict):
                    try:
                        queue_total = sum(int(value or 0) for value in queue_counts.values())
                    except (TypeError, ValueError):
                        queue_total = -1
                queue_is_complete = (
                    isinstance(queue_counts, dict)
                    and total_tasks > 0
                    and completed_tasks == total_tasks
                    and queue_counts.get("COMPLETED") == total_tasks
                    and queue_total == total_tasks
                )
                if (
                    review.get("action") != "APPROVE_FREEZE"
                    or review.get("task_review_complete") is not True
                    or not queue_is_complete
                    or review.get("revision_id") != expected_revision
                    or review.get("reviewed_plan_hash") != expected_hash
                    or review.get("active_p0_p1_finding_ids")
                ):
                    return Event(
                        "FREEZE_REJECTED",
                        {"reason": "task review queue lacks complete blocker-free coverage"},
                    )
            elif (
                review.get("action") != "APPROVE_FREEZE"
                or len(set(review.get("valid_worker_ids") or [])) < 2
                or int(review.get("approval_count") or 0) < 2
                or int(review.get("distinct_review_fingerprint_count") or 0) < 2
                or review_identity_error(review, expected_revision, expected_hash)
            ):
                return Event("FREEZE_REJECTED", {"reason": "current plan lacks validated Critic approval quorum"})
        groups = plan.get("groups") or plan.get("formal_groups")
        if not isinstance(groups, list) or not groups:
            return Event("FREEZE_REJECTED", {"reason": "formal groups missing"})
        for group in groups:
            if not isinstance(group, dict):
                return Event("FREEZE_REJECTED", {"reason": "group is not object"})
            items = group.get("items")
            if not isinstance(items, list) or not items:
                return Event("FREEZE_REJECTED", {
                    "reason": "group has no items",
                    "group_id": group.get("group_id"),
                })
        self.ctx.request_payload["frozen_plan"] = copy.deepcopy(plan)
        first_group = groups[0]
        self._set_active_group_item(first_group, 0)
        self.ctx.group_index = 0
        self.ctx.item_index = 0
        return Event("LOCAL_VALIDATION_PASSED", {"action": "FREEZE_OK"})

    def _set_active_group_item(self, group: dict[str, Any], item_index: int) -> None:
        items = group.get("items") if isinstance(group.get("items"), list) else []
        item = items[item_index] if item_index < len(items) and isinstance(items[item_index], dict) else {}
        self.ctx.active_group_id = str(
            group.get("group_id") or f"group-{(self.ctx.group_index or 0) + 1:06d}"
        )
        self.ctx.active_item_id = (
            str(item.get("item_id") or f"item-{item_index + 1:06d}") if item else None
        )
        # Revision budget is scoped to the active item, not the whole task.
        self.ctx.item_revision_round = 0
        self.ctx.request_payload["active_group"] = copy.deepcopy(group)
        self.ctx.request_payload["active_item"] = copy.deepcopy(item) if item else None

    def _state_elapsed_seconds(self) -> float:
        try:
            entered = datetime.fromisoformat(str(self.ctx.entered_at))
            if entered.tzinfo is None:
                entered = entered.replace(tzinfo=timezone.utc)
            return max(0.0, time.time() - entered.timestamp())
        except (TypeError, ValueError):
            return max(0.0, time.time() - self.started_at)

    def _timed_out(self) -> bool:
        if not self.ctx.active_request_id:
            return False
        return self._state_elapsed_seconds() >= self.timeout_seconds

    def _heartbeat_due(self) -> bool:
        if not self.ctx.active_request_id:
            return False
        return time.time() - self.ctx.last_heartbeat_epoch >= self.heartbeat_interval

    def _human_gate_expired(self) -> bool:
        return bool(
            self.ctx.active_decision_id
            and self._state_elapsed_seconds() >= self.timeout_seconds
        )

    def _resume_state_for_timeout(self) -> str:
        role = (self.ctx.timeout_role or "").split("-")[-1]
        mapping = {
            ("ZHONGSHU", "analyst"): "ZHONGSHU_ANALYST",
            ("ZHONGSHU", "solver"): "ZHONGSHU_SOLVER",
            ("ZHONGSHU", "critic"): "ZHONGSHU_CRITIC",
            ("MENXIA", "solver"): "MENXIA_ITEM_SOLVER",
            ("MENXIA", "analyst"): "MENXIA_ITEM_ANALYST",
            ("MENXIA", "critic"): "MENXIA_ITEM_CRITIC",
        }
        return mapping.get((self.ctx.timeout_phase or "", role), "STATE_CORRUPTED")

    def _record_event_context(self, event: Event) -> None:
        logger.info(
            "STATE_TRANSITION task_id=%s sequence=%s state=%s event=%s group=%s item=%s request_id=%s",
            self.ctx.task_id,
            self.ctx.sequence,
            self.ctx.workflow_state,
            event.name,
            self.ctx.active_group_id,
            self.ctx.active_item_id,
            self.ctx.active_request_id,
        )


def _plan_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("plan")
    if isinstance(value, dict):
        return value
    return payload


def _normalize_human_gate_question(value: dict[str, Any]) -> dict[str, Any]:
    gate = copy.deepcopy(value)
    gate["question_id"] = str(gate.get("question_id") or gate.get("id") or "")
    gate["question"] = str(gate.get("question") or "")
    options = gate.get("options")
    normalized_options: list[dict[str, Any]] = []
    if isinstance(options, list):
        for option in options:
            if not isinstance(option, dict):
                continue
            normalized = copy.deepcopy(option)
            if not normalized.get("description") and normalized.get("impact"):
                normalized["description"] = normalized.get("impact")
            normalized_options.append(normalized)
    gate["options"] = normalized_options
    return gate


def _human_gate_fallback_question(payload: dict[str, Any], fallback: str) -> str:
    """Render a useful decision subject when the producer omitted human_gate."""
    findings = payload.get("findings")
    active_findings = []
    if isinstance(findings, list):
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            status = str(finding.get("status") or finding.get("decision") or "OPEN").upper()
            if status in {"RESOLVED", "WONT_FIX", "DEFERRED"}:
                continue
            severity = str(finding.get("severity") or "P2").upper()
            if severity in {"P0", "P1"}:
                active_findings.append(finding)

    queue_counts = payload.get("task_queue_counts")
    completed = total = None
    if isinstance(queue_counts, dict):
        completed = queue_counts.get("COMPLETED")
        total = sum(
            int(value)
            for value in queue_counts.values()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        )

    if not active_findings and not payload.get("missing_evidence") and not payload.get("required_change"):
        return fallback

    lines = ["中书省审查发现未解决的问题，需要确认后继续。"]
    if completed is not None and total:
        lines.append(f"当前任务审查进度：{completed}/{total} 已完成。")
    if active_findings:
        lines.append("阻塞项：")
        for finding in active_findings[:3]:
            target = str(finding.get("target") or "未指定任务")
            claim = " ".join(str(finding.get("claim") or "").split())
            if len(claim) > 240:
                claim = claim[:237] + "..."
            lines.append(f"- {target}：{claim}")

    missing_evidence = payload.get("missing_evidence")
    if isinstance(missing_evidence, list) and missing_evidence:
        lines.append("缺失证据：")
        for item in missing_evidence[:3]:
            text = " ".join(str(item).split())
            if len(text) > 180:
                text = text[:177] + "..."
            lines.append(f"- {text}")

    required_change = payload.get("required_change")
    if isinstance(required_change, list) and required_change:
        lines.append("需要修改：")
        for item in required_change[:3]:
            text = " ".join(str(item).split())
            if len(text) > 180:
                text = text[:177] + "..."
            lines.append(f"- {text}")

    lines.append("请确认是否返回 Solver 补齐上述问题后继续。")
    return "\n".join(lines)


def _human_gate_from_payload(payload: dict[str, Any], fallback: str) -> dict[str, Any]:
    gate: dict[str, Any] = {}
    direct = payload.get("human_gate")
    if isinstance(direct, dict):
        gate = _normalize_human_gate_question(direct)
    if not gate:
        next_actions = payload.get("next_actions")
        if isinstance(next_actions, list):
            for candidate in next_actions:
                if (
                    isinstance(candidate, dict)
                    and str(candidate.get("action", "")).upper() == "HUMAN_GATE"
                ):
                    gate = _normalize_human_gate_question(candidate)
                    break
    if not gate and payload.get("question"):
        gate = _normalize_human_gate_question({
            "question_id": payload.get("question_id") or payload.get("id"),
            "question": payload.get("question"),
            "options": payload.get("options") or [],
        })

    questions = payload.get("questions_for_user")
    structured_questions = [
        _normalize_human_gate_question(question)
        for question in questions
        if isinstance(question, dict) and question.get("question")
    ] if isinstance(questions, list) else []
    if not gate and structured_questions:
        gate = structured_questions.pop(0)
    if structured_questions:
        gate["remaining_questions"] = structured_questions

    if not gate.get("question") and isinstance(questions, list) and questions:
        gate["question"] = str(questions[0])
    gate.setdefault("question", _human_gate_fallback_question(payload, fallback))
    gate.setdefault("options", [])
    total = 1 + len(gate.get("remaining_questions", []))
    gate["question_index"] = 1
    gate["question_total"] = total
    for index, remaining in enumerate(gate.get("remaining_questions", []), start=2):
        remaining["question_index"] = index
        remaining["question_total"] = total
    return gate


def _default_human_gate_resume_state(state: str) -> str:
    return {
        "ZHONGSHU_CRITIC": "ZHONGSHU_SOLVER",
        "MENXIA_ITEM_CRITIC": "MENXIA_ITEM_SOLVER",
        "MENXIA_GROUP_GATE": "MENXIA_ITEM_SOLVER",
    }.get(state, state)


def _human_gate_prompt(gate: dict[str, Any], fallback: str) -> str:
    lines = ["【需要你做决定】"]
    index = int(gate.get("question_index") or 1)
    total = int(gate.get("question_total") or 1)
    if total > 1:
        lines.append(f"问题 {index}/{total}")
    question_id = str(gate.get("question_id") or "")
    if question_id:
        lines.append(f"编号：{question_id}")
    lines.append(str(gate.get("question") or fallback))
    lines.append("")
    options = gate.get("options") if isinstance(gate.get("options"), list) else []
    for option_index, option in enumerate(options):
        if not isinstance(option, dict):
            continue
        option_id = str(option.get("id") or chr(ord("A") + option_index))
        letter = chr(ord("A") + option_index)
        label = str(option.get("label") or option.get("description") or option_id)
        description = str(option.get("description") or option.get("impact") or "")
        lines.append(f"{letter}. {label}")
        if description and description != label:
            lines.append(f"   影响：{description}")
    lines.append("")
    if options:
        lines.append("直接回复本消息，输入 A、B、C 等选项字母即可。")
    else:
        lines.append("直接回复本消息，输入你的决定即可。")
    lines.append("直接回复时无需携带决策编号。")
    return "\n".join(lines)


def build_context(args: argparse.Namespace, issue_id: str, raw_request: str, task_id: str) -> StateContext:
    return StateContext(
        task_id=task_id,
        issue_id=issue_id,
        raw_request=raw_request,
        project_type=args.project_type,
        task_type=args.task_type,
    )


def main(argv: list[str] | None = None) -> int:
    configure_logging(retention_hours=72)
    parser = argparse.ArgumentParser(description="Review Orchestrator FSM v3.1")
    parser.add_argument("--new")
    parser.add_argument("--issue")
    parser.add_argument("--resume")
    parser.add_argument("--cancel")
    parser.add_argument("--inspect-task")
    parser.add_argument("--project", default="")
    parser.add_argument("--allow-duplicate", action="store_true")
    parser.add_argument("--project-type", default="unknown")
    parser.add_argument("--task-type", default="review")
    parser.add_argument("--runs-root", default=os.environ.get("ORCHESTRATOR_RUNS_ROOT", "runs"))
    parser.add_argument("--poll-interval", type=float, default=float(os.environ.get("POLL_INTERVAL_SEC", "8")))
    parser.add_argument("--timeout", type=int, default=int(os.environ.get("PHASE_TIMEOUT_SEC", "900")))
    args = parser.parse_args(argv)

    multica = MulticaCliAdapter(Path(args.runs_root) / "transport")
    try:
        if args.inspect_task:
            ctx = StateContext(task_id=args.inspect_task)
            app = OrchestratorApp(ctx, args.runs_root, multica=multica)
            print(json.dumps(app.inspect(), ensure_ascii=False, indent=2))
            return 0
        if args.cancel:
            ctx = StateContext(task_id=args.cancel)
            ok = OrchestratorApp(ctx, args.runs_root, multica=multica).cancel()
            return 0 if ok else 1

        if args.resume:
            task_id = args.resume
            store = JsonStateStore(_resolve_task_root(args.runs_root, task_id))
            ctx = store.load()
        else:
            task_id = f"task-{time.strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}"
            try:
                raw_request = normalize_task_request(args.new or "")
            except ValueError as error:
                parser.error(str(error))
            issue_id = args.issue or multica.create_issue(
                "Review: " + raw_request[:60],
                raw_request,
                args.project,
                allow_duplicate=args.allow_duplicate,
            )
            ctx = build_context(args, issue_id, raw_request, task_id)
    except ValueError as error:
        parser.error(str(error))
    app = OrchestratorApp(
        ctx,
        args.runs_root,
        multica=multica,
        poll_interval=args.poll_interval,
        timeout_seconds=args.timeout,
    )
    return 0 if app.run() else 1
