from __future__ import annotations

import argparse
import copy
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .adapters import FeishuHttpAdapter, MulticaCliAdapter, SystemClock
from .context import StateContext
from .events import Event
from .locks import TaskLock, TaskLockError
from .logging_setup import configure_logging
from .models import HumanGate
from .persistence import JsonStateStore, PersistenceError
from .recovery import RecoveryManager
from .state_machine import StateMachine
from .states import build_state_registry
from .transitions import TransitionError

logger = logging.getLogger("review_orchestrator_fsm")


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
        self.root = Path(root) / ctx.task_id
        self.store = JsonStateStore(self.root)
        self.multica = multica or MulticaCliAdapter(self.root / "transport")
        self.feishu = feishu or FeishuHttpAdapter()
        self.poll_interval = poll_interval
        self.timeout_seconds = timeout_seconds
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
        )
        self.machine = StateMachine(ctx, self.states, self.store.persist)

    def run(self) -> bool:
        lock = TaskLock(self.root / "task.lock")
        outcome = "not_started"
        try:
            lock.acquire()
        except TaskLockError:
            logger.error("TASK_LOCK_BUSY task_id=%s", self.ctx.task_id)
            return False
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
            while self.ctx.workflow_state not in {"DONE", "BLOCKED", "CANCELLED", "STATE_CORRUPTED"}:
                lock.refresh()
                event = self._next_event()
                if event.name == "NOOP":
                    time.sleep(self.poll_interval)
                    continue
                self.machine.dispatch(event)
                self._record_event_context(event)
            outcome = self.ctx.workflow_state.lower()
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
        try:
            lock.acquire()
            self._ensure_loaded()
            if self.ctx.workflow_state in {"DONE", "BLOCKED", "CANCELLED", "STATE_CORRUPTED"}:
                return False
            self.machine.dispatch(Event("CANCEL_REQUESTED", {"reason": "operator cancellation"}))
            return True
        finally:
            lock.release()

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
            dispatch_pending = getattr(self.states[self.ctx.workflow_state], "dispatch_pending", None)
            if dispatch_pending:
                dispatch_pending(self.ctx)
                self.store.save_state(self.ctx)

    def _next_event(self) -> Event:
        state = self.ctx.workflow_state
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
            except RuntimeError as error:
                self.ctx.resume_state = state
                self.ctx.last_error = {"code": "MULTICA_ERROR", "message": str(error)}
                return Event("MULTICA_ERROR", {
                    "resume_state": state,
                    "max_retries": self.ctx.max_external_retries,
                }, str(error))
            if event.name == "AGENT_REPLY_ACCEPTED":
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

    def _consume_agent_payload(self, payload: dict[str, Any]) -> None:
        self.store.save_state(self.ctx)
        self._save_artifact_for_state(payload)
        self.ctx.last_agent_payload = dict(payload)
        action = str(payload.get("action", ""))
        if self.ctx.workflow_state == "ZHONGSHU_ANALYST":
            plan = payload.get("plan")
            if action == "READY_FOR_SOLVER" and isinstance(plan, dict):
                self.ctx.request_payload["analyst_plan"] = copy.deepcopy(plan)
        elif self.ctx.workflow_state == "ZHONGSHU_SOLVER":
            plan = _plan_from_payload(payload)
            if action == "READY_FOR_CRITIC":
                groups = plan.get("groups") or plan.get("formal_groups")
                if isinstance(groups, list):
                    self.ctx.request_payload["candidate_plan"] = plan
        elif self.ctx.workflow_state == "ZHONGSHU_CRITIC":
            self._update_findings(payload)
            self.ctx.request_payload["zhongshu_critic_review"] = copy.deepcopy(payload)
        elif self.ctx.workflow_state == "MENXIA_ITEM_SOLVER":
            proposal = payload.get("implementation_proposal")
            self.ctx.request_payload["implementation_proposal"] = (
                copy.deepcopy(proposal) if isinstance(proposal, dict) else copy.deepcopy(payload)
            )
            self.ctx.request_payload["notification_payload"] = self.ctx.request_payload["implementation_proposal"]
        elif self.ctx.workflow_state == "MENXIA_ITEM_ANALYST":
            self.ctx.request_payload["analyst_review"] = copy.deepcopy(payload)
            self.ctx.request_payload["notification_payload"] = self.ctx.request_payload["analyst_review"]
        elif self.ctx.workflow_state == "MENXIA_ITEM_CRITIC":
            self._update_findings(payload)
            self.ctx.request_payload["critic_review"] = copy.deepcopy(payload)
            self.ctx.request_payload["notification_payload"] = self.ctx.request_payload["critic_review"]

    def _decorate_progress_event(self, event: Event) -> None:
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
        (artifact_dir / f"{artifact_id}.json").write_text(
            json.dumps(envelope, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.ctx.last_artifact_id = artifact_id

    def _update_findings(self, payload: dict[str, Any]) -> None:
        findings = payload.get("findings")
        if not isinstance(findings, list):
            return
        from .models import Finding

        updates = [
            Finding.from_dict(item)
            for item in findings
            if isinstance(item, dict) and item.get("finding_id")
        ]
        self.ctx.merge_findings(updates)

    def _freeze_check_event(self) -> Event:
        plan = self.ctx.request_payload.get("candidate_plan")
        if not isinstance(plan, dict):
            return Event("FREEZE_REJECTED", {"reason": "solver plan missing"})
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
    gate.setdefault("question", fallback)
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
        store = JsonStateStore(Path(args.runs_root) / task_id)
        ctx = store.load()
    else:
        task_id = f"task-{time.strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}"
        raw_request = args.new or ""
        issue_id = args.issue or multica.create_issue(
            "Review: " + raw_request[:60],
            raw_request,
            args.project,
            allow_duplicate=args.allow_duplicate,
        )
        ctx = build_context(args, issue_id, raw_request, task_id)
    app = OrchestratorApp(
        ctx,
        args.runs_root,
        multica=multica,
        poll_interval=args.poll_interval,
        timeout_seconds=args.timeout,
    )
    return 0 if app.run() else 1

