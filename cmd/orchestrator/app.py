"""Composition root for the immutable linear workflow runtime."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import time
import uuid

from .adapters import MulticaCliAdapter
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
    NodeEffectRunner,
    FeishuNotificationPort,
    NotificationEffectRunner,
    NullNotificationPort,
    RuntimeConcurrencyAdmission,
    TaskLockAdapter,
    WorkflowSnapshot,
    EffectRecord,
    WorkflowEngine,
)
from .runtime.migration import legacy_dto_to_snapshot


logger = logging.getLogger("review_orchestrator_fsm")


def _resolve_task_root(runs_root: str | Path, task_id: str) -> Path:
    root = Path(runs_root).expanduser().resolve()
    candidate = (root / str(task_id)).resolve()
    if candidate == root or root not in candidate.parents:
        raise ValueError("task id must resolve within runs root")
    return candidate


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
        self.admission = RuntimeConcurrencyAdmission(
            ConcurrencyLimits(
                global_max=int(os.environ.get("GLOBAL_MAX_WORKERS", "6")),
                per_task_max=int(os.environ.get("PER_TASK_MAX_WORKERS", "6")),
                analyst_max=int(os.environ.get("ANALYST_MAX_WORKERS", "3")),
                critic_max=int(os.environ.get("CRITIC_MAX_WORKERS", "6")),
                lease_ttl_seconds=int(os.environ.get("LEASE_TTL_SECONDS", "1020")),
            )
        )
        self.runner = AgentWorkerRunner(
            self.transport,
            admission=self.admission,
            artifacts=self.artifacts,
            poll_interval=self.poll_interval,
            max_polls=max(1, int(os.environ.get("AGENT_MAX_POLLS", "30"))),
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
        node_worker = AgentNodeWorkerRunner(self.runner)
        node_max_workers = max(1, int(os.environ.get("NODE_MAX_WORKERS", "6")))
        self.node_effects = NodeEffectRunner(
            executor_factory=lambda node, node_context: ConcurrentNodeExecutor(
                node_worker,
                AgentNodeJoiner(
                    node.node_run_id,
                    task_id=node_context.task_id,
                    state=node_context.state,
                    sequence=node_context.sequence,
                    revision_id=node_context.revision_id,
                    plan_hash=node_context.plan_hash,
                ),
                max_workers=min(
                    node_max_workers,
                    self.runner.parallel_width(
                        node_context.state,
                        node.bindings,
                        node_context.issue_id,
                    ),
                ),
            )
        )
        self.effects = EffectManager(
            self.repository,
            {
                "agent_dispatch": self.runner,
                "node_dispatch": self.node_effects,
                "notification": NotificationEffectRunner(
                    (
                        FeishuNotificationPort()
                        if os.environ.get("ENABLE_FEISHU_NOTIFICATIONS", "").lower()
                        in {"1", "true", "yes", "on"}
                        and os.environ.get("NEXUS_TEST_NO_EXTERNAL_NOTIFICATIONS", "").lower()
                        not in {"1", "true", "yes", "on"}
                        else NullNotificationPort()
                    )
                ),
            },
        )
        self.inbox = JsonDomainEventInbox(self.repository)
        from .domain.states import StateRegistry

        self.engine = WorkflowEngine(
            self.repository,
            StateRegistry.default(),
            TaskLockAdapter(context.identity.task_id, TaskLock(self.root / "task.lock")),
            LinearContextReducer(),
        )
        self._max_idle_seconds = max(1.0, self.timeout_seconds)

    @property
    def ctx(self) -> WorkflowContext:
        """Read-only spelling retained for diagnostics during cutover."""

        return self.repository.load(self.context.identity.task_id).context

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
    args = parser.parse_args(argv)
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
