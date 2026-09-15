"""Composition root for the immutable linear workflow runtime."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import time
import uuid

from .adapters import MulticaCliAdapter, feishu_notifications_enabled
from .notifications import DirectNotificationEmitter, ROLE_NAMES
from .domain.context import (
    ProgressState,
    RequestState,
    TaskIdentity,
    WorkflowContext,
    context_to_dto,
)
from .domain.decisions import EffectRequest
from .domain.events import DomainEvent
from .domain.transitions import TERMINAL_STATES
from .feishu_command_parser import normalize_task_request
from .logging_setup import configure_logging
from .locks import TaskLock
from .runtime import (
    AgentNodeJoiner,
    AgentNodeWorkerRunner,
    AgentWorkerRunner,
    ConcurrentNodeExecutor,
    ConcurrencyLimits,
    EffectManager,
    FileArtifactStore,
    JsonDomainEventInbox,
    JsonWorkflowRepository,
    LinearContextReducer,
    MulticaTransportAdapter,
    NodeContext,
    NodeEffectRunner,
    NodeExecutor,
    NodeResult,
    NotificationRequest,
    ReviewNode,
    WorkerBinding,
    WorkerProgressCallback,
    WorkerResult,
    FeishuNotificationPort,
    NullNotificationPort,
    RuntimeConcurrencyAdmission,
    TaskLockAdapter,
    WorkflowSnapshot,
    EffectRecord,
    WorkflowEngine,
)
from .runtime.migration import legacy_dto_to_snapshot
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


def _analyst_lenses() -> tuple[str, ...]:
    """Return the per-worker evidence lenses for a Zhongshu Analyst fan-out.

    Overridable with the ``ANALYST_LENSES`` environment variable (comma
    separated).  Lens text must not contain an ASCII comma.
    """

    raw = os.environ.get("ANALYST_LENSES", "").strip()
    if raw:
        lenses = tuple(item.strip() for item in raw.split(",") if item.strip())
        if lenses:
            return lenses
    return DEFAULT_ANALYST_LENSES


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
    ) -> None:
        if not isinstance(context, WorkflowContext):
            raise TypeError("OrchestratorApp requires WorkflowContext")
        self.root = _resolve_task_root(root, context.identity.task_id)
        self.root.mkdir(parents=True, exist_ok=True)
        self.repository = JsonWorkflowRepository(self.root)
        self.artifacts = FileArtifactStore(self.root / "artifacts")
        if self.repository.state_path.exists():
            snapshot = self.repository.load(context.identity.task_id)
        else:
            snapshot = WorkflowSnapshot(context.identity.task_id, context, 0)
            self.repository.initialize(snapshot)
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
        self.notification_port = notification_port or (
            FeishuNotificationPort()
            if feishu_notifications_enabled()
            else NullNotificationPort()
        )
        self.admission = RuntimeConcurrencyAdmission(
            ConcurrencyLimits(
                global_max=int(os.environ.get("GLOBAL_MAX_WORKERS", "6")),
                per_task_max=int(os.environ.get("PER_TASK_MAX_WORKERS", "6")),
                analyst_max=int(os.environ.get("ANALYST_MAX_WORKERS", "3")),
                critic_max=int(os.environ.get("CRITIC_MAX_WORKERS", "3")),
                lease_ttl_seconds=int(os.environ.get("LEASE_TTL_SECONDS", "1020")),
            )
        )
        max_polls_env = os.environ.get("AGENT_MAX_POLLS", "").strip()
        if max_polls_env:
            max_polls = max(1, int(max_polls_env))
        else:
            interval = self.poll_interval if self.poll_interval > 0 else 15.0
            max_polls = max(30, int(self.timeout_seconds / interval) + 2)
        self.runner = AgentWorkerRunner(
            self.transport,
            admission=self.admission,
            artifacts=self.artifacts,
            poll_interval=self.poll_interval,
            max_polls=max_polls,
            timeout_seconds=self.timeout_seconds,
            agent_ids={
                "ZHONGSHU_ANALYST": os.environ.get("AGENT_ANALYST_ID", ""),
                "ZHONGSHU_SOLVER": os.environ.get("AGENT_SOLVER_ID", ""),
                "ZHONGSHU_CRITIC": os.environ.get("AGENT_CRITIC_ID", ""),
                "ZHONGSHU_FREEZE_CHECK": os.environ.get("AGENT_CRITIC_ID", ""),
                "MENXIA_ITEM_SOLVER": os.environ.get("AGENT_SOLVER_ID", ""),
                "MENXIA_ITEM_ANALYST": os.environ.get("AGENT_ANALYST_ID", ""),
                "MENXIA_ITEM_CRITIC": os.environ.get("AGENT_CRITIC_ID", ""),
                "MENXIA_GROUP_GATE": os.environ.get("AGENT_CRITIC_ID", ""),
            },
        )
        self._node_max_workers = max(1, int(os.environ.get("NODE_MAX_WORKERS", "6")))
        self._critic_max_workers = max(
            1, int(os.environ.get("CRITIC_MAX_WORKERS", "3"))
        )
        logger.info(
            "AGENT_POOL_CONFIG node_max_workers=%s pools=%s",
            self._node_max_workers,
            self.runner.agent_pool_summary(),
        )
        self.node_effects = NodeEffectRunner(executor_factory=self._build_node_executor)
        self.plan_effects = PlanArtifactRunner(self.artifacts)
        self.effects = EffectManager(
            self.repository,
            {
                "agent_dispatch": self.runner,
                "node_dispatch": self.node_effects,
                "plan_artifact": self.plan_effects,
            },
        )
        self.inbox = JsonDomainEventInbox(self.repository)
        from .domain.states import StateRegistry

        self.engine = WorkflowEngine(
            self.repository,
            StateRegistry.default(),
            TaskLockAdapter(context.identity.task_id, TaskLock(self.root / "task.lock")),
            LinearContextReducer(),
            DirectNotificationEmitter(self.notification_port),
        )
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
        fanout_analyst = (
            node_context.state == "ZHONGSHU_ANALYST"
            and node_context.dispatch_mode != "task_review"
            and total > 1
            and pool_size == 1
            and child_supported
        )
        fanout_critic = (
            node_context.state == "ZHONGSHU_CRITIC"
            and node_context.dispatch_mode == "task_review"
            and total > 1
            and pool_size == 1
            and child_supported
        )
        fanout = fanout_analyst or fanout_critic
        if fanout:
            cap = total if fanout_analyst else self._critic_max_workers
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

    def _prepare_fanout_node(self, node: ReviewNode, context: NodeContext) -> ReviewNode:
        """Move each fan-out worker onto its own child issue.

        Applies to the whole-plan Analyst node (each worker is a distinct
        evidence lens) and to the per-task Critic node (each worker owns one
        mandatory task).  The child issue is created later, inside the transport
        dispatch, with the dispatch payload as its description; that keeps a
        single assignment run per worker (no placeholder run, no poisoned
        session).
        """

        if not context.issue_id:
            return node
        analyst = context.state == "ZHONGSHU_ANALYST"
        lenses = _analyst_lenses() if analyst else ()
        bindings: list[WorkerBinding] = []
        for position, binding in enumerate(node.bindings):
            suffix = binding.worker_id.rsplit("-", 1)[-1]
            if analyst:
                title = (
                    f"Analyst 维度 {suffix} · {context.task_id} "
                    f"· seq{context.sequence}"
                )
            else:
                task_label = binding.item_id or binding.group_id or suffix
                title = (
                    f"Critic 任务 {task_label} · {context.task_id} "
                    f"· seq{context.sequence}"
                )
            dispatch_context = dict(binding.dispatch_context or {})
            if lenses:
                dispatch_context["worker_lens"] = lenses[position % len(lenses)]
                dispatch_context.setdefault("worker_index", position + 1)
            bindings.append(
                replace(
                    binding,
                    issue_id="",
                    fanout_parent_id=context.issue_id,
                    fanout_title=title,
                    dispatch_context=dispatch_context,
                )
            )
        if lenses and len(lenses) != len(node.bindings):
            logger.warning(
                "FANOUT_LENS_COUNT_MISMATCH task_id=%s node_run_id=%s lenses=%s "
                "bindings=%s",
                context.task_id,
                context.node_run_id,
                len(lenses),
                len(node.bindings),
            )
        logger.info(
            "FANOUT_CHILDREN_READY task_id=%s node_run_id=%s state=%s parent=%s "
            "children=%s",
            context.task_id,
            context.node_run_id,
            context.state,
            context.issue_id,
            len(bindings),
        )
        return replace(node, bindings=tuple(bindings))

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
        task_id = self.context.identity.task_id
        self._ensure_start_event()
        idle_since = time.monotonic()
        while True:
            snapshot = self.repository.load(task_id)
            state = snapshot.context.progression.state
            if state in TERMINAL_STATES:
                logger.info(
                    "PROCESS_EXIT task_id=%s state=%s sequence=%s outcome=%s",
                    task_id,
                    state,
                    snapshot.context.progression.sequence,
                    state.lower(),
                )
                return state == "DONE"

            event = self.inbox.next(task_id)
            if event is not None:
                self.engine.dispatch(event)
                self.inbox.ack(event)
                self.effects.execute_pending(task_id)
                idle_since = time.monotonic()
                continue

            if self.repository.pending_effects(task_id):
                self.effects.execute_pending(task_id)
                idle_since = time.monotonic()
                continue

            if state == "RETRY_WAIT":
                # Retry wait is an internal bounded recovery state.  The
                # runner resumes it after the persisted failure has been
                # observed; the retry counter is enforced by the domain.
                self.resume()
                idle_since = time.monotonic()
                continue
            if state in {"HUMAN_GATE", "BLOCKED", "PERSISTENCE_DEGRADED"}:
                # These states require an explicit operator/human decision;
                # an idle clock must never turn waiting for a person into a
                # synthetic failure.
                if self.poll_interval:
                    time.sleep(min(self.poll_interval, 0.25))
                continue

            if time.monotonic() - idle_since >= self._max_idle_seconds:
                current = self.repository.load(task_id)
                self.inbox.publish(
                    DomainEvent(
                        name="TIMEOUT",
                        task_id=task_id,
                        sequence=current.context.progression.sequence,
                        payload={
                            "error_code": "EVENT_INPUT_TIMEOUT",
                            "reason": "no event or pending effect before deadline",
                        },
                        occurred_at=_now(),
                        event_id=f"timeout:{task_id}:{current.context.progression.sequence}",
                    )
                )
                idle_since = time.monotonic()
                continue
            if self.poll_interval:
                time.sleep(min(self.poll_interval, 0.25))

    def cancel(self) -> bool:
        snapshot = self.repository.load(self.context.identity.task_id)
        if snapshot.context.progression.state in TERMINAL_STATES:
            return False
        self.engine.dispatch(
            DomainEvent(
                name="CANCEL",
                task_id=snapshot.task_id,
                sequence=snapshot.context.progression.sequence,
                payload={"reason": "operator cancellation"},
                occurred_at=_now(),
                event_id=f"cancel:{snapshot.task_id}:{snapshot.context.progression.sequence}",
            )
        )
        return True

    def resume(self, answer: str = "") -> bool:
        """Apply one operator/human resume decision to a recoverable state."""

        snapshot = self.repository.load(self.context.identity.task_id)
        if snapshot.context.progression.state not in {
            "HUMAN_GATE",
            "RETRY_WAIT",
            "BLOCKED",
            "PERSISTENCE_DEGRADED",
        }:
            return False
        self.inbox.publish(
            DomainEvent(
                name="RESUME",
                task_id=snapshot.task_id,
                sequence=snapshot.context.progression.sequence,
                payload={"answer": answer} if answer else {},
                occurred_at=_now(),
                event_id=f"resume:{snapshot.task_id}:{snapshot.context.progression.sequence}",
            )
        )
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
            snapshot.context.progression.state == "REQUEST_INTAKE"
            and snapshot.context.progression.sequence == 0
            and not self.repository.pending_domain_events(snapshot.task_id)
        ):
            self.inbox.publish(
                DomainEvent(
                    name="START",
                    task_id=snapshot.task_id,
                    sequence=0,
                    payload={"raw_request": snapshot.context.request.raw_request},
                    occurred_at=_now(),
                    event_id=f"start:{snapshot.task_id}",
                )
            )


def build_context(args: argparse.Namespace, issue_id: str, raw_request: str, task_id: str) -> WorkflowContext:
    return WorkflowContext(
        identity=TaskIdentity(
            task_id=task_id,
            issue_id=issue_id,
            project=str(args.project or ""),
            request_id=f"request-{task_id}",
        ),
        progression=ProgressState(
            state="REQUEST_INTAKE",
            sequence=0,
            entered_at=_now(),
        ),
        request=RequestState(
            raw_request=raw_request,
            project_type=str(args.project_type),
            task_type=str(args.task_type),
        ),
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _format_elapsed(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, remainder = divmod(total, 60)
    if minutes:
        return f"{minutes} 分 {remainder} 秒"
    return f"{remainder} 秒"


def main(argv: list[str] | None = None) -> int:
    configure_logging(retention_hours=72)
    parser = argparse.ArgumentParser(description="Review Orchestrator immutable linear FSM")
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
    parser.add_argument(
        "--no-external-notifications",
        action="store_true",
        help=(
            "Test mode: suppress every outbound Feishu/webhook message so runs "
            "can be exercised without notifying a real chat."
        ),
    )
    args = parser.parse_args(argv)
    if args.no_external_notifications:
        # Must be set before the app wires its notification port.
        os.environ["NEXUS_TEST_NO_EXTERNAL_NOTIFICATIONS"] = "1"
    multica = MulticaCliAdapter(Path(args.runs_root) / "transport")

    try:
        if args.inspect_task:
            task_id = args.inspect_task
            root = _resolve_task_root(args.runs_root, task_id)
            repository = JsonWorkflowRepository(root)
            context = repository.load(task_id).context
            print(json.dumps(OrchestratorApp(context, args.runs_root, multica=multica).inspect(), ensure_ascii=False, indent=2))
            return 0
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
                legacy_path = root / "state.json"
                value = json.loads(legacy_path.read_text(encoding="utf-8"))
                migrated = legacy_dto_to_snapshot(
                    value,
                    artifact_port=FileArtifactStore(root / "artifacts"),
                    task_id=task_id,
                )
                repository.initialize(migrated)
                _seed_legacy_inflight_effect(repository, migrated)
                _migrate_legacy_pending_events(repository, root, task_id)
                context = migrated.context
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
        app.resume()
    return 0 if app.run() else 1


def _seed_legacy_inflight_effect(
    repository: JsonWorkflowRepository,
    snapshot: WorkflowSnapshot,
) -> None:
    """Turn a legacy in-flight dispatch into a durable New effect intent."""

    context = snapshot.context
    delivery = context.delivery
    if (
        context.progression.state in TERMINAL_STATES
        or delivery.status not in {"PENDING", "RUNNING"}
        or not delivery.active_request_id
    ):
        return
    request_id = delivery.active_request_id
    idempotency_key = delivery.idempotency_key or request_id
    request = EffectRequest(
        effect_id=f"dispatch:legacy:{request_id}",
        effect_type="agent_dispatch",
        task_id=snapshot.task_id,
        idempotency_key=idempotency_key,
        payload_ref=context.request.payload_ref,
        payload={
            "issue_id": context.identity.issue_id,
            "request_id": request_id,
            "agent_id": delivery.expected_agent_id,
            "role": delivery.role,
            "phase": delivery.phase,
            "target_state": context.progression.state,
            "prompt_ref": context.request.payload_ref or context.request.raw_request,
            "request_payload_ref": context.request.payload_ref or "",
            "revision_id": context.review.revision_id if context.review else "",
            "plan_hash": context.review.plan_hash if context.review else "",
            "sequence": context.progression.sequence,
        },
    )
    repository.seed_effect(
        EffectRecord(
            effect_id=request.effect_id,
            task_id=snapshot.task_id,
            request=request,
            status="PENDING",
            attempt=max(0, delivery.attempt),
            state=context.progression.state,
            sequence=context.progression.sequence,
        )
    )


def _migrate_legacy_pending_events(
    repository: JsonWorkflowRepository,
    root: Path,
    task_id: str,
) -> None:
    """Import explicitly pending legacy events; audit lines are not inboxes.

    The removed V2 persistence format wrote every consumed transition to
    ``events.jsonl`` after the state update.  Those lines are historical audit
    records and must never be replayed.  A legacy producer that really has an
    unconsumed input must mark it with ``pending: true`` (or an equivalent
    explicit unacknowledged marker) before this one-time bridge will import it.
    """

    legacy_path = root / "events.jsonl"
    if not legacy_path.exists():
        return
    current_sequence = repository.load(task_id).context.progression.sequence
    pending: list[DomainEvent] = []
    for index, line in enumerate(legacy_path.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid legacy event at line {index + 1}") from error
        if not isinstance(value, dict):
            raise ValueError(f"legacy event at line {index + 1} is not an object")
        event_value = value.get("event") if isinstance(value.get("event"), dict) else value
        if not _legacy_event_is_pending(value, event_value):
            # V2's normal event record is an audit record written after the
            # transition.  Treating it as an inbox item would replay every
            # historical transition on the first New-runtime restart.
            continue
        event_task_id = str(event_value.get("task_id") or task_id)
        if event_task_id != task_id:
            continue
        sequence = event_value.get("sequence")
        if type(sequence) is not int:
            raise ValueError(f"legacy event at line {index + 1} has invalid sequence")
        if sequence < current_sequence:
            continue
        if sequence > current_sequence:
            raise ValueError("legacy event sequence contains a gap")
        raw_name = (
            event_value.get("name")
            or event_value.get("event_name")
            or (event_value.get("event") if isinstance(event_value.get("event"), str) else "")
        )
        name = str(raw_name or "")
        if not name:
            raise ValueError(f"legacy event at line {index + 1} has no name")
        pending.append(
            DomainEvent(
                name=name,
                task_id=task_id,
                sequence=sequence,
                payload=event_value.get("payload", {}),
                occurred_at=str(event_value.get("occurred_at") or _now()),
                event_id=str(
                    event_value.get("event_id")
                    or f"legacy:{task_id}:{sequence}:{name}"
                ),
            )
        )
    if len(pending) > 1:
        raise ValueError("legacy inbox contains multiple pending events")
    for event in pending:
        repository.append_domain_event(event)


def _legacy_event_is_pending(
    record: Mapping[str, object],
    event_value: Mapping[str, object],
) -> bool:
    """Recognize only explicit pending/unacknowledged legacy envelopes."""

    for source in (record, event_value):
        if source.get("pending") is True:
            return True
        if source.get("acked") is False:
            return True
        if source.get("acknowledged") is False:
            return True
        if source.get("consumed") is False:
            return True
    return False


__all__ = ["OrchestratorApp", "build_context", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
