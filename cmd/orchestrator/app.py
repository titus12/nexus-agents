"""Composition root for the immutable linear workflow runtime."""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import time
import threading
import uuid

from .adapters import MulticaCliAdapter, feishu_notifications_enabled
from .notifications import ROLE_NAMES
from .domain.context import (
    ProgressState,
    RequestState,
    TaskIdentity,
    WorkflowContext,
    context_to_dto,
)
from .domain.events import DomainEvent
from .feishu_command_parser import normalize_task_request
from .logging_setup import configure_logging
from .locks import TaskLock, TaskLockError
from .runtime.agent_effects import (
    COMPLETED_RESULT_GRACE_SECONDS,
    resolve_dispatch_role_timeouts,
)
from .runtime import AgentNodeJoiner, AgentNodeWorkerRunner, AgentWorkerRunner, ConcurrentNodeExecutor, ConcurrencyLimits, EffectManager, FileArtifactStore, JsonDomainEventInbox, JsonWorkflowRepository, LinearContextReducer, MulticaTransportAdapter, NodeContext, NodeEffectRunner, NodeExecutor, NodeResult, NotificationRequest, ReviewNode, WorkerBinding, WorkerProgressCallback, WorkerResult, FeishuNotificationPort, NullNotificationPort, RuntimeConcurrencyAdmission, TaskLockAdapter, WorkflowSnapshot, WorkflowEngine
from .runtime.plan_effects import PlanArtifactRunner


logger = logging.getLogger("review_orchestrator_fsm")


def _resolve_task_root(runs_root: str | Path, task_id: str) -> Path:
    root = Path(runs_root).expanduser().resolve()
    candidate = (root / str(task_id)).resolve()
    if candidate == root or root not in candidate.parents:
        raise ValueError("task id must resolve within runs root")
    return candidate


DEFAULT_ANALYST_LENSES = (
    "需求与验收覆盖：聚焦需求契约、验收信号与范围边界是否完整可信",
    "实现与集成证据：聚焦现有代码/配置/接口现状、依赖与集成约束的实证",
    "风险与未知：聚焦性能/兼容/回归风险、冲突证据与未决问题",
)




class _FanoutNodeExecutor:
    """Run a node after giving each worker its own child issue.

    Multica serializes tasks that share one ``(issue_id, agent_id)``
    conversation, so a single identity cannot fan out on one issue.  Each worker
    is moved onto a child issue before execution, which makes every worker a
    distinct conversation and lets them run concurrently.
    """

    def __init__(self, inner: NodeExecutor, prepare, finalize) -> None:
        self._inner = inner
        self._prepare = prepare
        self._finalize = finalize

    def execute(self, node: ReviewNode, context: NodeContext) -> NodeResult:
        prepared = node
        try:
            prepared = self._prepare(node, context)
        except Exception:
            logger.exception(
                "FANOUT_PREPARE_FAILED task_id=%s node_run_id=%s",
                context.task_id,
                context.node_run_id,
            )
        result: NodeResult | None = None
        try:
            result = self._inner.execute(prepared, context)
        finally:
            if result is not None and self._finalize is not None:
                try:
                    self._finalize(result)
                except Exception:
                    logger.exception(
                        "FANOUT_FINALIZE_FAILED task_id=%s node_run_id=%s",
                        context.task_id,
                        context.node_run_id,
                    )
        return result


_TRUTHY = frozenset({"1", "true", "yes", "on"})
_FALSY = frozenset({"0", "false", "no", "off"})

# Plan review has one approval word; all other nonempty answers are feedback.
# Other gates retain their existing reply-to-continue semantics.






def _env_flag(name: str) -> bool | None:
    """Read an opt-in boolean env flag; None means "not set" (keep as-is)."""
    raw = os.environ.get(name, "").strip().lower()
    if raw in _TRUTHY:
        return True
    if raw in _FALSY:
        return False
    return None


def _with_parallel_flag_overrides(context):
    flag = _env_flag('NEXUS_MENXIA_ENABLED')
    parallel = replace(context.parallel, menxia=replace(context.parallel.menxia, enabled=True if flag is None else flag))
    slicing = _env_flag('NEXUS_PROMPT_SLICING')
    if slicing is None:
        slicing = _env_flag('ZHONGSHU_PROMPT_SLICING')
    if slicing is not None:
        parallel = replace(parallel, prompt_slicing=slicing)
    return replace(context, parallel=parallel)


def _agent_pool_by_state() -> dict[str, str]:
    """Canonical agent identity pool per dispatch state.

    Values are comma-separated multica agent UUIDs from the environment.
    Every state that emits an agent/node dispatch must appear here: when a
    state is missing, worker binding falls back to the payload's worker name
    (e.g. "menxia-solver-01"), which the multica CLI rejects as a non-canonical
    assignee.
    """

    return {
        'REQ_ANALYZE':os.environ.get('AGENT_ANALYST_ID',''),
        'REQ_EVIDENCE':os.environ.get('AGENT_ANALYST_ID',''),
        'REQ_DRAFT':os.environ.get('AGENT_SOLVER_ID',''),
        'REQ_REVIEW':os.environ.get('AGENT_CRITIC_ID',''),
        'MENXIA_REQUIREMENTS_INTAKE':os.environ.get('AGENT_SOLVER_ID',''),
        "MENXIA_ITEM_SOLVER": os.environ.get("AGENT_SOLVER_ID", ""),
        "MENXIA_ITEM_ANALYST": os.environ.get("AGENT_ANALYST_ID", ""),
        "MENXIA_ITEM_CRITIC": os.environ.get("AGENT_CRITIC_ID", ""),
        "MENXIA_GROUP_SOLVER": os.environ.get("AGENT_SOLVER_ID", ""),
        "MENXIA_GROUP_ANALYST": os.environ.get("AGENT_ANALYST_ID", ""),
        "MENXIA_GROUP_CRITIC": os.environ.get("AGENT_CRITIC_ID", ""),
        "MENXIA_GROUP_GATE": os.environ.get("AGENT_CRITIC_ID", ""),
    }


class OrchestratorApp:
    """Run one workflow using only the New immutable FSM."""

    def __init__(
        self,
        context: WorkflowContext,
        root: str | Path = "runs",
        multica: object | None = None,
        *,
        poll_interval: float = 8.0,
        timeout_seconds: int = 900,
        notification_port: object | None = None,
        requirements_port: object | None = None,
    ) -> None:
        if not isinstance(context, WorkflowContext):
            raise TypeError("OrchestratorApp requires WorkflowContext")
        from .runtime.requirements_cutover import require_current_workflow
        require_current_workflow(context)
        self.root = _resolve_task_root(root, context.identity.task_id)
        self.root.mkdir(parents=True, exist_ok=True)
        self.repository = JsonWorkflowRepository(self.root)
        self.artifacts = FileArtifactStore(self.root / "artifacts")
        if self.repository.state_path.exists():
            snapshot = self.repository.load(context.identity.task_id)
        else:
            snapshot = WorkflowSnapshot(context.identity.task_id, context, 0)
            self.repository.initialize(snapshot)
        require_current_workflow(snapshot.context)
        # Explicit env flags win over persisted parallel limits for both new
        # and resumed tasks.  The repository is the source of truth and every
        # step reloads from it, so a correction must be persisted, not just
        # held in memory.
        overridden = _with_parallel_flag_overrides(snapshot.context)
        if overridden != snapshot.context:
            logger.info(
                "PARALLEL_FLAG_OVERRIDE task_id=%s menxia_enabled=%s prompt_slicing=%s",
                snapshot.task_id,
                overridden.parallel.menxia.enabled,
                overridden.parallel.prompt_slicing,
            )
            self.repository.patch_context(snapshot.task_id, overridden)
            snapshot = replace(snapshot, context=overridden)
        require_current_workflow(snapshot.context)
        self.context = snapshot.context
        self.multica = multica or MulticaCliAdapter(self.root / "transport")
        self.transport = (
            self.multica
            if all(
                callable(getattr(self.multica, name, None))
                for name in ("dispatch", "poll", "status", "lookup")
            )
            else MulticaTransportAdapter(self.multica)
        )
        self.poll_interval = max(0.0, poll_interval)
        self.timeout_seconds = max(0.0, float(timeout_seconds))
        self._gate_poll_interval = max(
            2.0, float(os.environ.get("GATE_POLL_INTERVAL_SEC", "10"))
        )
        self._last_gate_poll = 0.0
        self.notification_port = notification_port or (
            FeishuNotificationPort(task_root=self.root)
            if context.request.communication == 'feishu' and feishu_notifications_enabled()
            else NullNotificationPort())
        self.admission = RuntimeConcurrencyAdmission(
            ConcurrencyLimits(
                global_max=int(os.environ.get("GLOBAL_MAX_WORKERS", "6")),
                per_task_max=int(os.environ.get("PER_TASK_MAX_WORKERS", "6")),
                analyst_max=int(os.environ.get("ANALYST_MAX_WORKERS", "3")),
                critic_max=int(os.environ.get("CRITIC_MAX_WORKERS", "4")),
                lease_ttl_seconds=int(os.environ.get("LEASE_TTL_SECONDS", "1020")),
            )
        )
        max_polls_env = os.environ.get("AGENT_MAX_POLLS", "").strip()
        if max_polls_env:
            max_polls = max(1, int(max_polls_env))
        else:
            interval = self.poll_interval if self.poll_interval > 0 else 15.0
            max_polls = max(30, int(self.timeout_seconds / interval) + 2)
        # Per-role timeout differentiation: the default table applies to the
        # default global configuration only (an explicit non-default
        # ``timeout_seconds`` means the operator owns the dispatch deadlines
        # outright, and the scripted-adapter test suites rely on their own
        # small caps), but an explicit ZHONGSHU_DISPATCH_TIMEOUT_OVERRIDES is
        # always honoured instead of being silently dropped with it.
        include_default_role_timeouts = self.timeout_seconds == 900.0
        role_timeouts: dict[str, float] = {}
        if (
            include_default_role_timeouts
            or os.environ.get("ZHONGSHU_DISPATCH_TIMEOUT_OVERRIDES", "").strip()
        ):
            role_timeouts = resolve_dispatch_role_timeouts(
                include_defaults=include_default_role_timeouts
            )
        if not include_default_role_timeouts:
            logger.info(
                "DISPATCH_ROLE_TIMEOUT_DEFAULTS_SKIPPED timeout_seconds=%s role_timeouts=%s",
                self.timeout_seconds,
                role_timeouts,
            )
        self.runner = AgentWorkerRunner(
            self.transport,
            admission=self.admission,
            artifacts=self.artifacts,
            poll_interval=self.poll_interval,
            max_polls=max_polls,
            timeout_seconds=self.timeout_seconds,
            role_timeout_seconds=role_timeouts,
            agent_ids=_agent_pool_by_state(),
            completed_result_grace_seconds=float(
                os.environ.get(
                    "AGENT_COMPLETED_RESULT_GRACE_SEC",
                    COMPLETED_RESULT_GRACE_SECONDS,
                )
            ),
        )
        self._node_max_workers = max(1, int(os.environ.get("NODE_MAX_WORKERS", "6")))
        self._critic_max_workers = max(
            1, int(os.environ.get("CRITIC_MAX_WORKERS", "4"))
        )
        logger.info(
            "AGENT_POOL_CONFIG node_max_workers=%s pools=%s",
            self._node_max_workers,
            self.runner.agent_pool_summary(),
        )
        self.node_effects = NodeEffectRunner(executor_factory=self._build_node_executor)
        self.plan_effects = PlanArtifactRunner(self.artifacts, task_root=self.root)
        self.inbox = JsonDomainEventInbox(self.repository)
        from .runtime.requirements_workflow import RequirementsRuntime
        from .runtime.requirements_agent import RequirementsAgentRunner
        from .runtime.requirements_scouts import ScoutFanoutRunner
        from .domain.requirements.states import EFFECT_STATES
        runtime = RequirementsRuntime(self.repository, self.root, self.context, requirements_port)
        self.requirements_runtime = runtime
        scout = ScoutFanoutRunner(self.runner, report_dir=self.root.parent / 'scout-reports',
                                  close_child=self._close_fanout_child)
        runners = {'agent_dispatch':RequirementsAgentRunner(scout, allowed_root=self.root.parent),
                   'node_dispatch':self.node_effects, 'plan_artifact':self.plan_effects}
        runners.update({kind:runtime.effects for kind in {*EFFECT_STATES.values(),
            'requirements_cleanup','requirements_clarification_refresh','requirements_notice','requirements_progress'}})
        self.effects = EffectManager(self.repository, runners,
            result_event_sink=runtime.coordinator.accept_result, effect_guard=runtime.guard)
        self.engine = WorkflowEngine(self.repository, runtime.states,
            TaskLockAdapter(context.identity.task_id, TaskLock(self.root/'task.lock')),
            LinearContextReducer(runtime.transitions), None)
        self._max_idle_seconds = max(1.0, self.timeout_seconds)

    @property
    def ctx(self) -> WorkflowContext:
        """Read-only spelling retained for diagnostics during cutover."""

        return self.repository.load(self.context.identity.task_id).context

    def _build_node_executor(self, node: ReviewNode, node_context: NodeContext) -> NodeExecutor:
        node_worker = AgentNodeWorkerRunner(self.runner)
        started_at = time.monotonic()
        total = len(node.bindings)
        pool_size = len(self.runner.resolve_agent_pool(node_context.state))
        child_supported = callable(
            getattr(self.transport, "ensure_child_issue", None)
        )
        fanout = (node_context.state.startswith('MENXIA_') and total > 1
                  and pool_size == 1 and child_supported)
        if fanout:
            cap = total
            max_workers = min(self._node_max_workers, cap, total)
        else:
            max_workers = min(
                self._node_max_workers,
                self.runner.parallel_width(
                    node_context.state,
                    node.bindings,
                    node_context.issue_id,
                ),
            )
        logger.info(
            "NODE_EXECUTOR_PLAN task_id=%s state=%s dispatch_mode=%s bindings=%s "
            "max_workers=%s serialized=%s fanout=%s pool=%s",
            node_context.task_id,
            node_context.state,
            node_context.dispatch_mode or "whole_plan",
            total,
            max_workers,
            total > max_workers,
            fanout,
            pool_size or 1,
        )
        if total > max_workers and not fanout:
            logger.warning(
                "NODE_EXECUTOR_SERIALIZED task_id=%s state=%s bindings=%s "
                "max_workers=%s hint=configure_multiple_agent_ids",
                node_context.task_id,
                node_context.state,
                total,
                max_workers,
            )
        on_worker_complete = self._make_worker_progress_callback(
            node_context, total, started_at
        )
        executor: NodeExecutor = ConcurrentNodeExecutor(
            node_worker,
            AgentNodeJoiner(
                node.node_run_id,
                task_id=node_context.task_id,
                state=node_context.state,
                sequence=node_context.sequence,
                revision_id=node_context.revision_id,
                plan_hash=node_context.plan_hash,
                task_review_queue=node_context.review_queue,
                canonical_requirements=node_context.canonical_requirements,
                dispatch_mode=node_context.dispatch_mode,
                base_plan=node_context.base_plan,
                previous_review=node_context.previous_review,
                salvaged_worker_payloads=node_context.salvaged_worker_payloads,
                evidence_requester=node_context.evidence_requester,
                evidence_requires_solver_revision=node_context.evidence_requires_solver_revision,
                menxia_stage_census=node_context.menxia_stage_census,
                binding_contexts={
                    binding.worker_id: dict(binding.dispatch_context)
                    for binding in node.bindings
                },
            ),
            max_workers=max_workers,
            on_worker_complete=on_worker_complete,
        )
        if fanout:
            executor = _FanoutNodeExecutor(
                executor,
                self._prepare_fanout_node,
                self._finalize_fanout_children,
            )
        return executor

    def _prepare_fanout_node(self, node, context):
            if not context.issue_id:
                return node
            bindings = tuple(replace(binding, issue_id='', fanout_parent_id=context.issue_id,
                fanout_title=f'Menxia {binding.role} {binding.item_id or binding.group_id or binding.worker_id} · {context.task_id} · seq{context.sequence}')
                for binding in node.bindings)
            return replace(node, bindings=bindings)

    def _finalize_fanout_children(self, result: NodeResult) -> None:
        close = getattr(self.transport, "close_issue", None)
        if not callable(close):
            return
        for worker in getattr(result, "worker_results", ()) or ():
            child_id = str(getattr(worker, "external_issue_id", "") or "")
            if child_id:
                status = (
                    "done"
                    if getattr(worker, "status", "") == "SUCCEEDED"
                    else "cancelled"
                )
                close(child_id, status)

    def _close_fanout_child(self, child_id: str, status: str) -> None:
        """Close one scout probe child issue (mirrors _finalize_fanout_children)."""
        close = getattr(self.transport, "close_issue", None)
        if not callable(close) or not child_id:
            return
        close(str(child_id), status)

    def _make_worker_progress_callback(
        self,
        node_context: NodeContext,
        total: int,
        started_at: float,
    ) -> WorkerProgressCallback:
        def report(
            binding: WorkerBinding,
            result: WorkerResult,
            completed: int,
            _count: int,
            _context: NodeContext,
        ) -> None:
            self._notify_worker_progress(
                node_context,
                binding,
                result,
                completed,
                total,
                started_at,
            )

        return report

    def _notify_worker_progress(
        self,
        node_context: NodeContext,
        binding: WorkerBinding,
        result: WorkerResult,
        completed: int,
        total: int,
        started_at: float,
    ) -> None:
        """Best-effort human progress ping for one finished node worker."""

        worker_id = binding.worker_id
        role = ROLE_NAMES.get(node_context.state, node_context.state or "任务")
        outcome = "完成" if result.status == "SUCCEEDED" else "失败"
        elapsed = max(0.0, time.monotonic() - started_at)
        body = "\n".join(
            [
                f"【{role}｜进度 {completed}/{total}】",
                f"任务：{node_context.task_id}",
                f"{worker_id} 已{outcome}",
                f"本阶段已用：{_format_elapsed(elapsed)}",
            ]
        )
        key = (
            f"{node_context.task_id}:{node_context.state}:"
            f"{node_context.sequence}:worker-progress:{worker_id}"
        )
        try:
            receipt = self.notification_port.send(
                NotificationRequest(node_context.task_id, key, body)
            )
            logger.info(
                "WORKER_PROGRESS_NOTIFIED task_id=%s state=%s worker_id=%s "
                "completed=%s/%s delivered=%s",
                node_context.task_id,
                node_context.state,
                worker_id,
                completed,
                total,
                getattr(receipt, "delivered", False),
            )
        except Exception:
            logger.exception(
                "WORKER_PROGRESS_NOTIFY_FAILED task_id=%s state=%s worker_id=%s",
                node_context.task_id,
                node_context.state,
                worker_id,
            )

    def run(self) -> bool:
        lease = TaskLock(self.root/'requirements-run.lock')
        lease.acquire()
        stopped = threading.Event()
        self._requirements_lease_errors = []
        def refresh():
            while not stopped.wait(10):
                try:
                    lease.refresh()
                except Exception as error:
                    self._requirements_lease_errors.append(error)
                    return
        thread = threading.Thread(target=refresh,daemon=True)
        thread.start()
        try:
            return self._run_loop()
        finally:
            stopped.set()
            thread.join(timeout=1)
            lease.release()

    def _run_loop(self):
            from .requirements_observability import record, answer_progress
            task_id = self.context.identity.task_id
            self._ensure_start_event()
            next_health = 0
            while True:
                if getattr(self, '_requirements_lease_errors', ()):
                    raise self._requirements_lease_errors[0]
                snapshot = self.repository.load(task_id)
                state = snapshot.context.progression.state
                if time.monotonic() >= next_health:
                    aggregate = snapshot.context.requirements
                    round_id = aggregate.rounds[-1].round_id if aggregate and aggregate.rounds else ''
                    record('WORKFLOW_HEALTH', task_id=task_id, pid=os.getpid(), state=state,
                           sequence=snapshot.context.progression.sequence,
                           **answer_progress(aggregate, round_id))
                    next_health = time.monotonic() + 60
                if state in self.requirements_runtime.terminal_states:
                    self.effects.execute_pending(task_id)
                    return state == 'DONE'
                progressed = self.step()
                if not progressed and state == 'RETRY_WAIT':
                    self.resume()
                if self.poll_interval:
                    time.sleep(min(self.poll_interval, 0.25))

    def cancel(self):
        snapshot = self.repository.load(self.context.identity.task_id)
        if snapshot.context.progression.state in self.requirements_runtime.terminal_states:
            return False
        self.requirements_runtime.submit('CANCEL', {'reason':'operator cancellation'}, 'cancel:'+uuid.uuid4().hex)
        lease = TaskLock(self.root/'requirements-run.lock')
        try:
            lease.acquire()
        except TaskLockError:
            return True  # The active runner will consume the durable cancellation.
        try:
            # Drain committed events under the run lease, never dispatch business
            # effects while trying to cancel. Pending early callbacks cannot
            # prevent the local cancellation from being selected by the bridge.
            while self.ctx.progression.state not in self.requirements_runtime.terminal_states:
                self.requirements_runtime.coordinator.flush_one(snapshot.task_id)
                event = self.inbox.next(snapshot.task_id)
                if event is None:
                    raise RuntimeError('QUEUED_CANCELLATION_NOT_BRIDGED')
                self.engine.dispatch(event)
                self.inbox.ack(event)
                lease.refresh()
            self.effects.execute_pending(snapshot.task_id)
            return self.ctx.progression.state == 'REQ_CANCELLED'
        finally:
            lease.release()


    def resume(self, answer='', *, feedback_messages=None):
            snapshot = self.repository.load(self.context.identity.task_id)
            if snapshot.context.progression.state not in ('HUMAN_GATE','BLOCKED','RETRY_WAIT','PERSISTENCE_DEGRADED'):
                return False
            if not str(snapshot.context.progression.resume_state or '').startswith('MENXIA_'):
                return False
            self.inbox.publish(DomainEvent(name='RESUME', task_id=snapshot.task_id,
                sequence=snapshot.context.progression.sequence, payload={'answer':answer},
                occurred_at=_now(), event_id='menxia-resume:'+uuid.uuid4().hex))
            return True

    def inspect(self) -> dict[str, object]:
        snapshot = self.repository.load(self.context.identity.task_id)
        return {
            "task_id": snapshot.task_id,
            "state_version": snapshot.state_version,
            "context": context_to_dto(snapshot.context),
            "pending_effects": [effect.effect_id for effect in self.repository.pending_effects(snapshot.task_id)],
            "pending_events": [event.name for event in self.repository.pending_domain_events(snapshot.task_id)],
        }

    def _ensure_start_event(self) -> None:
        snapshot = self.repository.load(self.context.identity.task_id)
        if (
            snapshot.context.progression.state == 'REQ_INTAKE'
            and not self.repository.pending_domain_events(snapshot.task_id)
        ):
            self.inbox.publish(
                DomainEvent(
                    name="START",
                    task_id=snapshot.task_id,
                    sequence=snapshot.context.progression.sequence,
                    payload={"raw_request": snapshot.context.request.raw_request},
                    occurred_at=_now(),
                    event_id=f"start:{snapshot.task_id}",
                )
            )

    def step(self) -> bool:
        """Advance one committed event/effect batch; deterministic scripted tests use this path."""
        task_id = self.context.identity.task_id
        self._ensure_start_event()
        self.requirements_runtime.coordinator.flush_one(task_id)
        event = self.inbox.next(task_id)
        if event is not None:
            self.engine.dispatch(event)
            self.inbox.ack(event)
            return True
        if self.requirements_runtime.store.next(task_id):
            return False  # A verified early callback holds the model result until receipt reconciliation.
        if self.repository.pending_effects(task_id):
            self.effects.execute_pending(task_id)
            return True
        return False


def build_context(args: argparse.Namespace, issue_id: str, raw_request: str, task_id: str) -> WorkflowContext:
    workflow = getattr(args,'workflow',None) or 'requirements-v1'
    if workflow != 'requirements-v1':
        raise ValueError('legacy Zhongshu workflow is retired; use requirements-v1')
    from .domain.requirements.model import RequirementsState, SourceRecord
    aggregate = RequirementsState.empty(SourceRecord('src-000001','request',raw_request))
    return WorkflowContext(
        identity=TaskIdentity(
            task_id=task_id,
            issue_id=issue_id,
            project=str(args.project or ""),
            request_id=f"request-{task_id}",
        ),
        progression=ProgressState(
            state='REQ_INTAKE',
            sequence=0,
            entered_at=_now(),
        ),
        request=RequestState(
            raw_request=raw_request,
            project_type=str(args.project_type),
            task_type=str(args.task_type),
            plan_schema='canonical-v1',
            workflow=workflow,decision_actor_ids=tuple(getattr(args,'decision_actor_id',None) or ()),
            decision_owner_policy='explicit' if getattr(args,'decision_actor_id',None) else 'chat-members',
            communication=getattr(args,'communication',None) or 'feishu',
            project_root=str(getattr(args,'project_root',None) or ''),
        ),
        requirements=aggregate,
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _format_elapsed(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, remainder = divmod(total, 60)
    if minutes:
        return f"{minutes} 分 {remainder} 秒"
    return f"{remainder} 秒"


def _resolve_section_kinds(parser, tokens):
    from .domain.requirements.document import KIND_TITLES
    kinds = []
    for token in tokens:
        kind = token if token in KIND_TITLES else next((k for k, t in KIND_TITLES.items() if t == token), None)
        if not kind:
            parser.error(f'unknown section kind or title: {token}')
        if kind not in kinds:
            kinds.append(kind)
    return kinds


def main(argv: list[str] | None = None) -> int:
    configure_logging(retention_hours=72)
    parser = argparse.ArgumentParser(description="Review Orchestrator immutable linear FSM")
    parser.add_argument("--new")
    parser.add_argument("--issue")
    parser.add_argument("--resume")
    parser.add_argument("--cancel")
    parser.add_argument("--inspect-task")
    parser.add_argument('--check-prerequisites', action='store_true', help='Verify communication readiness without creating an issue or running a model.')
    parser.add_argument("--migrate-task", help="Copy a historical task into the sole requirements workflow without dispatching workers.")
    parser.add_argument("--project", default="")
    parser.add_argument('--project-root', help='Absolute target repository directory used to resolve evidence paths; fixed when creating a task.')
    parser.add_argument("--allow-duplicate", action="store_true")
    parser.add_argument("--project-type", default="unknown")
    parser.add_argument('--workflow',choices=('requirements-v1',),default=None)
    parser.add_argument('--communication',choices=('feishu','local'),default=None)
    parser.add_argument('--decision-actor-id','--decision-owner',action='append',help='Authorized Feishu open_id; repeat for multiple owners.')
    parser.add_argument('--repair-decision-owner', action='append', help='Fill missing owners on a blocked task before any card or approval; use with --resume.')
    parser.add_argument('--answer-file','--requirements-answer',help='Structured answers JSON for a persisted requirements task.')
    parser.add_argument('--feedback-file','--requirements-feedback',help='UTF-8 requirements feedback for a persisted task.')
    parser.add_argument('--approve-requirements',metavar='DOCUMENT_SHA256')
    parser.add_argument('--confirm-sections', nargs='+', metavar=('DOCUMENT_SHA256','KIND'),
                        help='Confirm chapters of the current draft by digest; KIND accepts English kind or Chinese title.')
    parser.add_argument('--feedback-sections', nargs='+', metavar='KIND',
                        help='Modifier for --feedback-file: unlock the given chapters for this feedback.')
    parser.add_argument('--requirements-status', metavar='TASK_ID',
                        help='Read-only chapter lock/status view of a persisted requirements task.')
    parser.add_argument('--recovery-note-file','--recovery-note',help='Verified prerequisite_changed or bind_card_receipt JSON.')
    parser.add_argument("--task-type", default="review")
    parser.add_argument("--plan-schema", choices=("canonical-v1",), default=None,
                        help="Select plan authority for a new task; resumes use the persisted schema.")
    parser.add_argument("--runs-root", default=os.environ.get("ORCHESTRATOR_RUNS_ROOT", "runs"))
    parser.add_argument("--poll-interval", type=float, default=float(os.environ.get("POLL_INTERVAL_SEC", "8")))
    parser.add_argument("--timeout", type=int, default=int(os.environ.get("PHASE_TIMEOUT_SEC", "900")))
    parser.add_argument(
        "--no-external-notifications",
        action="store_true",
        help=(
            "Test mode: suppress every outbound Feishu/webhook message so runs "
            "can be exercised without notifying a real chat."
        ),
    )
    args = parser.parse_args(argv)
    actions = (args.new is not None, bool(args.resume), bool(args.cancel),
               bool(args.inspect_task), bool(args.migrate_task), args.check_prerequisites,
               bool(args.requirements_status))
    if sum(actions) != 1:
        parser.error('choose exactly one of --new, --resume, --cancel, --inspect-task, --migrate-task, --requirements-status')
    resuming = args.resume or args.cancel or args.inspect_task
    if resuming and any((args.workflow,args.communication,args.decision_actor_id,args.project_root)):
        parser.error('workflow, communication, decision owners and project root are fixed when creating a task')
    if args.project_root:
        project_root = Path(args.project_root)
        if not project_root.is_absolute() or not project_root.is_dir():
            parser.error('--project-root must be an existing absolute directory')
        args.project_root = str(project_root.resolve())
    for actor in (args.decision_actor_id or []) + (args.repair_decision_owner or []):
        if (not actor.startswith('ou_') or not actor[3:] or not actor[3:].isascii()
            or not actor[3:].isalnum()):
            parser.error('--decision-actor-id must be a real Feishu open_id (ou_ followed by letters/digits); '
                         'replace the example placeholder before starting a task')
    inputs = [args.answer_file,args.feedback_file,args.approve_requirements,args.confirm_sections,args.recovery_note_file,args.repair_decision_owner]
    if any(inputs) and (not args.resume or sum(bool(x) for x in inputs)!=1):
        parser.error('use exactly one requirements input with --resume TASK_ID')
    if args.feedback_sections and not args.feedback_file:
        parser.error('--feedback-sections is a modifier for --feedback-file')
    if args.plan_schema is not None and (args.resume or args.cancel or args.inspect_task):
        parser.error("--plan-schema is only valid when creating a new task")
    if args.no_external_notifications:
        # Must be set before the app wires its notification port.
        os.environ["NEXUS_TEST_NO_EXTERNAL_NOTIFICATIONS"] = "1"
    try:
        if args.check_prerequisites or args.new is not None:
            from .requirements_startup import check_startup
            readiness = check_startup(args.runs_root,args.communication or 'feishu',tuple(args.decision_actor_id or ()))
            if readiness.get('decision_actor_ids'):
                args.decision_actor_id=readiness['decision_actor_ids']
            if args.check_prerequisites:
                print(json.dumps(readiness,ensure_ascii=False,indent=2))
                return 0
        if args.migrate_task:
            from .runtime.requirements_cutover import migrate_historical_task
            result = migrate_historical_task(args.runs_root, args.migrate_task,
                communication=args.communication, actor_ids=args.decision_actor_id)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.inspect_task:
            from .runtime.requirements_cutover import inspect_task
            print(json.dumps(inspect_task(args.runs_root, args.inspect_task), ensure_ascii=False, indent=2))
            return 0
        if args.requirements_status:
            from .runtime.requirements_cutover import requirements_status
            print(json.dumps(requirements_status(args.runs_root, args.requirements_status), ensure_ascii=False, indent=2))
            return 0
        if args.resume or args.cancel:
            from .runtime.requirements_cutover import inspect_task
            if inspect_task(args.runs_root, args.resume or args.cancel)['historical_read_only']:
                parser.error('Historical task requires --migrate-task '+(args.resume or args.cancel))
        multica = MulticaCliAdapter(Path(args.runs_root) / "transport")
        if args.cancel:
            task_id = args.cancel
            root = _resolve_task_root(args.runs_root, task_id)
            repository = JsonWorkflowRepository(root)
            context = repository.load(task_id).context
            return 0 if OrchestratorApp(context, args.runs_root, multica=multica).cancel() else 1
        if args.resume:
            task_id = args.resume
            root = _resolve_task_root(args.runs_root, task_id)
            repository = JsonWorkflowRepository(root)
            if repository.state_path.exists():
                context = repository.load(task_id).context
            else:
                parser.error('Historical task requires --migrate-task '+task_id)
        else:
            task_id = f"task-{time.strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}"
            raw_request = normalize_task_request(args.new or "")
            if args.issue:
                issue_data = multica.get_issue(args.issue)
                current_assignee = str(issue_data.get("assignee_id") or "")
                if not current_assignee:
                    parser.error(
                        f"Issue {args.issue} has no assignee. "
                        "Assign the target agent before starting the review."
                    )
            # Leave the orchestration issue unassigned: assigning an agent here
            # fires an immediate assignment run on the parent issue, which is a
            # redundant fourth analyst run now that analyst work fans out to
            # child issues.  Phase dispatches assign the parent when they need
            # it (which also fires their trigger run exactly once).
            issue_id = args.issue or multica.create_issue(
                "Review: " + raw_request[:60],
                raw_request,
                args.project,
                allow_duplicate=args.allow_duplicate,
            )
            context = build_context(args, issue_id, raw_request, task_id)
    except (ValueError, OSError, json.JSONDecodeError) as error:
        parser.error(str(error))
    app = OrchestratorApp(
        context,
        args.runs_root,
        multica=multica,
        poll_interval=args.poll_interval,
        timeout_seconds=args.timeout,
    )
    if args.resume:
        if app.requirements_runtime:
            if args.answer_file:
                payload = json.loads(Path(args.answer_file).read_text(encoding='utf-8-sig'))
                payload['actor_id'] = 'local-operator'
                app.requirements_runtime.submit('ANSWERS_SUBMITTED',payload,'local-answer:'+uuid.uuid4().hex)
            elif args.feedback_file:
                text = Path(args.feedback_file).read_text(encoding='utf-8-sig').strip()
                if not text:
                    parser.error('feedback must not be empty')
                payload = {'text': text, 'actor_id': 'local-operator'}
                if args.feedback_sections:
                    payload['section_kinds'] = _resolve_section_kinds(parser, args.feedback_sections)
                app.requirements_runtime.submit('REQUIREMENTS_FEEDBACK', payload, 'local-feedback:'+uuid.uuid4().hex)
            elif args.approve_requirements:
                current = app.ctx.requirements
                app.requirements_runtime.submit('REQUIREMENTS_APPROVED',{'document_digest':args.approve_requirements,
                    'gate_id':current.review_gate_id,'actor_id':'local-operator'},'local-approval:'+uuid.uuid4().hex)
            elif args.confirm_sections:
                if len(args.confirm_sections) < 2:
                    parser.error('--confirm-sections needs DOCUMENT_SHA256 followed by at least one chapter kind')
                current = app.ctx.requirements
                app.requirements_runtime.submit('REQUIREMENTS_SECTION_CONFIRMED',
                    {'document_digest': args.confirm_sections[0], 'gate_id': current.review_gate_id,
                     'actor_id': 'local-operator',
                     'section_kinds': _resolve_section_kinds(parser, args.confirm_sections[1:])},
                    'local-section-confirm:'+uuid.uuid4().hex)
            elif args.recovery_note_file:
                from .runtime.requirements_recovery import verified_recovery
                evidence = json.loads(Path(args.recovery_note_file).read_text(encoding='utf-8-sig'))
                try:
                    verified = verified_recovery(app.requirements_runtime,app.ctx.requirements,evidence)
                except (ValueError,KeyError,OSError) as error:
                    parser.error(str(error))
                app.requirements_runtime.submit('RECOVERY_INPUT',verified,'local-recovery:'+uuid.uuid4().hex)
            elif args.repair_decision_owner:
                from .runtime.requirements_recovery import verified_owner_recovery
                try:
                    verified = verified_owner_recovery(app.requirements_runtime,app.ctx,tuple(args.repair_decision_owner))
                except ValueError as error:
                    parser.error(str(error))
                app.requirements_runtime.submit('DECISION_OWNERS_REPAIRED',verified,'local-owner-recovery:'+uuid.uuid4().hex)
        if not any(inputs):
            app.resume()
    if app.requirements_runtime and any(inputs) and TaskLock(app.root/'requirements-run.lock').has_live_owner():
        print('Input durably queued for the active requirements runtime.')
        return 0
    return 0 if app.run() else 1








__all__ = ["OrchestratorApp", "build_context", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
