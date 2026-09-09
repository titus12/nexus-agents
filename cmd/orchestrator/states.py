from __future__ import annotations

from datetime import datetime, timezone
import uuid
import json
import hashlib
import logging
import copy
from pathlib import Path
from typing import Any

from .adapters import Clock, FeishuAdapter, MulticaAdapter, SystemClock
from .context import StateContext
from .zhongshu_review import CRITIC_ACTIONS, merge_evidence_updates, records
from .events import Event
from .models import AgentRequest, DeliveryReceipt, HumanGate
from .models import AgentBinding, ExternalMessage
from .models import normalize_finding_status
from .structured_output import (
    build_structured_output_spec,
    role_result_template,
    stable_role_fields,
    state_actions,
    validate_role_result_shape,
)
from .contracts import contract_for_state
from .transitions import TransitionPolicy
from .validators import RejectedReply, validate_agent_reply
from .notifications import (
    build_agent_notification,
    build_agent_notification_parts,
    should_emit_notification,
)
from .concurrency import ConcurrencyAdmission, DuplicateLeaseError
from .zhongshu_solver_contract import (
    ZHONGSHU_SOLVER_FORBIDDEN_FIELDS,
    ZHONGSHU_SOLVER_MAX_FINDINGS_PER_ROUND,
    ZHONGSHU_SOLVER_REQUIRED_ITEM_FIELDS,
    zhongshu_solver_runtime_rules,
)
from .zhongshu_parallel import (
    ANALYST_MAX_EVIDENCE_REQUESTS,
    ANALYST_MAX_EVIDENCE_UPDATES_MERGED,
    build_solver_evidence_context,
    canonical_plan_hash,
    requirement_requires_task,
    validate_zhongshu_evidence_packet,
)
from .zhongshu_review_queue import (
    ReviewJob,
    canonical_json,
    dependency_refs_for_item,
)


logger = logging.getLogger("review_orchestrator_fsm")


def _remember_rejected_reply(ctx: StateContext, payload: dict[str, Any], reason: str) -> None:
    """Keep repair input request-scoped; never reuse a naked global payload."""
    entry = {
        "request_id": ctx.active_request_id,
        "state": ctx.workflow_state,
        "phase": ctx.current_phase,
        "role": ctx.current_role,
        "reason": reason,
        "payload": copy.deepcopy(payload),
    }
    history = ctx.reply_history if isinstance(ctx.reply_history, list) else []
    history.append(entry)
    ctx.reply_history = history[-8:]


ACTIVE_SKILL_BY_STATE = {
    "ZHONGSHU_ANALYST": "zhongshu-analyst",
    "ZHONGSHU_SOLVER": "zhongshu-solver",
    "ZHONGSHU_CRITIC": "zhongshu-critic",
    "MENXIA_ITEM_SOLVER": "menxia-solver",
    "MENXIA_ITEM_ANALYST": "menxia-analyst",
    "MENXIA_ITEM_CRITIC": "menxia-critic",
    "MENXIA_GROUP_GATE": "menxia-critic",
}


def _active_skill_directive(state_name: str) -> str:
    skill = ACTIVE_SKILL_BY_STATE.get(state_name)
    if not skill:
        return ""
    phase = "ZHONGSHU" if state_name.startswith("ZHONGSHU") else "MENXIA"
    return (
        f"ACTIVE_RUNTIME_SKILL: {skill}\n"
        f"ACTIVE_RUNTIME_PHASE: {phase}\n"
        f"ACTIVE_RUNTIME_STATE: {state_name}\n"
        "Only the active runtime Skill above is authoritative for this request. "
        "Other Skills may be attached to this Agent, but they are inactive for this turn. "
        "Do not use another phase's role, output schema, action enum, or workflow instructions.\n"
        "Write exactly one role-protocol JSON object to the request result file. "
        "The issue comment must contain only the compact result pointer; do not return "
        "Markdown, a report, code fences, or a second business result."
    )


def _runtime_skill_lock(state_name: str) -> dict[str, Any]:
    """Bind a dispatch to the exact repository Skill document used by its role."""
    skill_name = ACTIVE_SKILL_BY_STATE.get(state_name, "")
    if not skill_name:
        return {}
    skill_path = (
        Path(__file__).resolve().parents[2]
        / "docs"
        / "multi"
        / f"{skill_name}-skill.md"
    )
    if state_name.startswith("ZHONGSHU"):
        skill_path = skill_path.parent / "runtime" / skill_path.name
    try:
        content = skill_path.read_bytes()
    except OSError:
        return {
            "name": skill_name,
            "status": "missing",
            "source": str(skill_path),
        }
    digest = hashlib.sha256(content).hexdigest()
    return {
        "name": skill_name,
        "status": "locked",
        "version": f"sha256-{digest[:12]}",
        "source": str(skill_path),
        "sha256": digest,
        "bytes": len(content),
        "encoding": "utf-8",
        "bom": content.startswith(b"\xef\xbb\xbf"),
    }


def _canonical_prompt_response_contract(
    existing: Any,
    *,
    phase: str,
    role: str,
    state: str,
    role_mode: str,
    schema_hash: str,
) -> dict[str, Any]:
    """Make the prompt's structural contract agree with the transport spec.

    Role-specific business guidance may stay in the prompt, but structural
    requirements must have one source of truth.  This prevents mode-specific
    legacy snippets such as ``required=["action"]`` from competing with the
    fixed role protocol appended by ``BaseState.request``.
    """
    contract = copy.deepcopy(existing) if isinstance(existing, dict) else {}
    transport_fields = [
        "contract_id", "task_id", "request_id", "phase", "state", "role", "mode",
        "structured_output_protocol", "structured_output_schema_hash",
    ]
    required = [
        "action",
        *transport_fields,
        *stable_role_fields(phase, role),
    ]
    allowed_actions = state_actions(state)
    contract.update({
        "format": "result_file_json_plus_compact_pointer_no_markdown_no_code_fence",
        "allowed_actions": allowed_actions,
        "required": required,
        "required_by_action": {
            action: list(required) for action in allowed_actions
        },
        "instruction": (
            "Write exactly one complete result using the fixed role field set to "
            "the result_path in prompt_ref. Use [] or null for fields unused by "
            "this mode, include the supplied protocol and schema hash, then "
            "reply only with the compact result pointer."
        ),
        "stable_role_protocol": {
            "phase": phase,
            "role": role,
            "state": state,
            "mode": role_mode,
            "schema_hash": schema_hash,
            "fields": stable_role_fields(phase, role),
        },
    })
    try:
        contract["contract_id"] = contract_for_state(state).contract_id
    except KeyError:
        contract["contract_id"] = ""
    return contract


ROLE_BY_STATE = {
    "ZHONGSHU_ANALYST": ("ZHONGSHU", "review-analyst"),
    "ZHONGSHU_SOLVER": ("ZHONGSHU", "review-solver"),
    "ZHONGSHU_CRITIC": ("ZHONGSHU", "review-critic"),
    "MENXIA_ITEM_SOLVER": ("MENXIA", "review-solver"),
    "MENXIA_ITEM_ANALYST": ("MENXIA", "review-analyst"),
    "MENXIA_ITEM_CRITIC": ("MENXIA", "review-critic"),
    "MENXIA_GROUP_GATE": ("MENXIA", "review-critic"),
}


def _text_diagnostics(text: str) -> dict[str, int]:
    """Return encoding clues without logging the reply body."""
    return {
        "question_marks": text.count("?"),
        "non_ascii": sum(1 for char in text if ord(char) > 127),
        "replacement_chars": text.count("\ufffd"),
    }


class BaseState:
    name = ""

    def __init__(
        self,
        multica: MulticaAdapter | None = None,
        feishu: FeishuAdapter | None = None,
        clock: Clock | None = None,
        agent_ids: dict[str, str] | None = None,
        admission: ConcurrencyAdmission | None = None,
    ) -> None:
        self.multica = multica
        self.feishu = feishu
        self.clock = clock or SystemClock()
        self.agent_ids = agent_ids or {}
        self.admission = admission
        self._leases: dict[str, str] = {}

    def enter(self, ctx: StateContext) -> None:
        ctx.entered_at = self._now()
        ctx.updated_at = ctx.entered_at
        phase_role = ROLE_BY_STATE.get(self.name)
        if phase_role:
            ctx.current_phase, ctx.current_role = phase_role
            ctx.timeout_phase = None
            ctx.timeout_role = None
            ctx.timeout_request_id = None
            if ctx.last_heartbeat_epoch <= 0:
                ctx.last_heartbeat_epoch = self.clock.now().timestamp()
            # Agent binding follows the current FSM role. Never retain the
            # previous state's agent when transitioning Analyst -> Solver ->
            # Critic.
            ctx.expected_agent_id = self.agent_ids.get(ctx.current_role, ctx.current_role)
            if not ctx.active_request_id:
                ctx.active_request_id = f"{ctx.task_id}:{self.name}:{ctx.sequence}:{uuid.uuid4().hex[:8]}"
            ctx.dispatch_idempotency_key = ctx.dispatch_idempotency_key or f"{ctx.task_id}:{self.name}:{ctx.sequence}"
            if ctx.dispatch_status == "none":
                ctx.dispatch_status = "pending"
            notify = getattr(self.feishu, "notify", None)
            if should_emit_notification("STATE_ENTER"):
                self._notify_once(
                    ctx,
                    f"{ctx.task_id}:{ctx.sequence}:{self.name}:enter",
                    notify,
                    build_agent_notification(ctx.current_role, self.name, "STATE_ENTER", ctx),
                    ctx.current_role.split("-")[-1],
                    "STATE_ENTER",
                )

    def dispatch_pending(self, ctx: StateContext) -> None:
        self._dispatch_once(ctx)

    def release_active_lease(self, ctx: StateContext) -> None:
        lease_id = self._leases.pop(ctx.active_request_id, None)
        if lease_id and self.admission:
            self.admission.release(lease_id)

    def update(self, ctx: StateContext) -> Event:
        ctx.updated_at = self._now()
        if self.multica is None or not self.active_request(ctx):
            logger.info(
                "AGENT_POLL_SKIPPED task_id=%s state=%s request_id=%s multica_available=%s",
                ctx.task_id,
                self.name,
                ctx.active_request_id,
                self.multica is not None,
            )
            return Event("NOOP")
        request = self.request(ctx)
        replies = self.multica.poll(request)
        if not replies:
            logger.info(
                "AGENT_REPLY_PENDING task_id=%s state=%s role=%s request_id=%s",
                ctx.task_id,
                self.name,
                ctx.current_role,
                ctx.active_request_id,
            )
            return Event("NOOP")
        # The adapter returns fresh replies in chronological order; consume the
        # newest one so a batch cannot re-process an older reply first.
        message = replies[-1]
        raw_content = message.raw_content or ""
        serialized_payload = ""
        try:
            serialized_payload = json.dumps(
                message.payload, ensure_ascii=False, separators=(",", ":")
            )
        except (TypeError, ValueError):
            pass
        payload_size = (
            len(serialized_payload.encode("utf-8"))
            if serialized_payload
            else len(raw_content.encode("utf-8"))
        )
        raw_diagnostics = _text_diagnostics(raw_content)
        payload_diagnostics = _text_diagnostics(serialized_payload)
        logger.info(
            "AGENT_REPLY_RECEIVED task_id=%s state=%s request_id=%s external_id=%s "
            "payload_bytes=%s payload_keys=%s raw_chars=%s",
            ctx.task_id,
            self.name,
            ctx.active_request_id,
            message.external_id,
            payload_size,
            sorted(message.payload.keys()),
            len(raw_content),
        )
        logger.info(
            "AGENT_REPLY_TEXT_DIAGNOSTICS task_id=%s state=%s request_id=%s external_id=%s "
            "raw_question_marks=%s raw_non_ascii=%s raw_replacement_chars=%s "
            "payload_question_marks=%s payload_non_ascii=%s payload_replacement_chars=%s",
            ctx.task_id,
            self.name,
            ctx.active_request_id,
            message.external_id,
            raw_diagnostics["question_marks"],
            raw_diagnostics["non_ascii"],
            raw_diagnostics["replacement_chars"],
            payload_diagnostics["question_marks"],
            payload_diagnostics["non_ascii"],
            payload_diagnostics["replacement_chars"],
        )
        allowed = _allowed_actions(self.name)
        normalized_payload = _normalize_agent_reply_action(
            self.name,
            message.payload,
        )
        if normalized_payload is not message.payload:
            message = ExternalMessage(
                message.author_id,
                normalized_payload,
                message.external_id,
                message.raw_content,
            )
        if message.payload.get("action") == "__UNSTRUCTURED_REPLY__":
            raw_reply = str(message.payload.get("raw_reply") or "")
            launch_failed = bool(message.payload.get("launch_failed")) or (
                "start opencode" in raw_reply.lower()
                and "too long" in raw_reply.lower()
            )
            failure_code = "AGENT_LAUNCH_FAILED" if launch_failed else "AGENT_REPLY_SCHEMA_INVALID"
            failure_event = "AGENT_LAUNCH_FAILED" if launch_failed else "AGENT_REPLY_SCHEMA_INVALID"
            logger.warning(
                "%s task_id=%s state=%s request_id=%s external_id=%s",
                failure_event,
                ctx.task_id,
                self.name,
                ctx.active_request_id,
                message.external_id,
            )
            ctx.last_error = {
                "code": failure_code,
                "reason": "OPENCODE_COMMAND_LINE_TOO_LONG" if launch_failed else "REPLY_BODY_NOT_STRUCTURED",
                "message": raw_reply[:500],
                "external_id": message.external_id,
                "response_source": message.payload.get("response_source", "comment"),
                "structured_output_schema_hash": message.payload.get(
                    "structured_output_schema_hash", ""
                ),
                "json_status": message.payload.get("json_status", "invalid"),
                "json_error": message.payload.get("json_error", ""),
                "json_error_position": message.payload.get("json_error_position"),
                "raw_reply_chars": message.payload.get("raw_reply_chars"),
                "raw_reply_bytes": message.payload.get("raw_reply_bytes"),
                "raw_reply_sha256": message.payload.get("raw_reply_sha256", ""),
                "raw_reply_first_char": message.payload.get("raw_reply_first_char", ""),
                "raw_reply_last_char": message.payload.get("raw_reply_last_char", ""),
            }
            _remember_rejected_reply(ctx, message.payload, "REPLY_BODY_NOT_STRUCTURED")
            self.release_active_lease(ctx)
            return Event("AGENT_REPLY_REJECTED", {
                "reason": "REPLY_BODY_NOT_STRUCTURED",
                "failure_code": failure_code,
                "resume_state": self.name,
                "max_retries": ctx.max_reply_retries,
            })
        result = validate_agent_reply(
            message,
            AgentBinding(
                author_id=ctx.expected_agent_id,
                task_id=ctx.task_id,
                request_id=ctx.active_request_id,
                role=ctx.current_role,
                phase=ctx.current_phase,
                target_state=self.name,
                target_role=ctx.current_role,
            ),
            allowed,
        )
        if isinstance(result, RejectedReply):
            logger.warning(
                "AGENT_REPLY_REJECTED task_id=%s state=%s request_id=%s external_id=%s reason=%s",
                ctx.task_id,
                self.name,
                ctx.active_request_id,
                result.external_id,
                result.reason,
            )
            ctx.last_error = {"code": "AGENT_REPLY_REJECTED", "reason": result.reason, "external_id": result.external_id}
            _remember_rejected_reply(ctx, result.payload, result.reason)
            self.release_active_lease(ctx)
            return Event("AGENT_REPLY_REJECTED", {
                "reason": result.reason,
                "resume_state": self.name,
                "max_retries": ctx.max_reply_retries,
            })
        validated_payload = result.payload
        if isinstance(request.structured_output, dict):
            protocol_error = validate_role_result_shape(
                validated_payload,
                phase=request.phase,
                role=request.role,
                state=request.target_state or self.name,
                role_mode=str(
                    request.context.get("structured_output_role_mode")
                    or request.structured_output.get("role_mode")
                    or ""
                ),
                expected_schema_hash=str(
                    request.structured_output.get("schema_hash") or ""
                ),
            )
            if protocol_error:
                logger.warning(
                    "AGENT_REPLY_PROTOCOL_REJECTED task_id=%s state=%s "
                    "request_id=%s external_id=%s reason=%s",
                    ctx.task_id,
                    self.name,
                    ctx.active_request_id,
                    message.external_id,
                    protocol_error,
                )
                ctx.last_error = {
                    "code": "AGENT_REPLY_PROTOCOL_REJECTED",
                    "reason": protocol_error,
                    "external_id": message.external_id,
                    "contract_id": contract_for_state(self.name).contract_id,
                }
                _remember_rejected_reply(ctx, validated_payload, protocol_error)
                self.release_active_lease(ctx)
                return Event("AGENT_REPLY_REJECTED", {
                    "reason": protocol_error,
                    "resume_state": self.name,
                    "max_retries": ctx.max_reply_retries,
                })
        if self.name == "ZHONGSHU_SOLVER":
            validated_payload, normalization_notes = _normalize_solver_payload(
                result.payload,
                ctx.request_payload.get("analyst_plan"),
                current_plan=ctx.request_payload.get("candidate_plan"),
                revision_requirements=_solver_revision_requirements(
                    ctx.request_payload.get("zhongshu_critic_review")
                ),
            )
            if normalization_notes:
                logger.info(
                    "AGENT_REPLY_NORMALIZED task_id=%s state=%s request_id=%s changes=%s",
                    ctx.task_id,
                    self.name,
                    ctx.active_request_id,
                    "|".join(normalization_notes),
                )
        contract_error = _validate_state_payload(self.name, validated_payload, ctx)
        if contract_error:
            logger.warning(
                "AGENT_REPLY_CONTRACT_REJECTED task_id=%s state=%s request_id=%s external_id=%s reason=%s",
                ctx.task_id,
                self.name,
                ctx.active_request_id,
                message.external_id,
                contract_error,
            )
            ctx.last_error = {
                "code": "AGENT_REPLY_CONTRACT_REJECTED",
                "reason": contract_error,
                "external_id": message.external_id,
            }
            _remember_rejected_reply(ctx, validated_payload, contract_error)
            self.release_active_lease(ctx)
            return Event("AGENT_REPLY_REJECTED", {
                "reason": contract_error,
                "resume_state": self.name,
                "max_retries": ctx.max_reply_retries,
            })
        previous_error = ctx.last_error
        ctx.last_error = None
        if previous_error:
            logger.info(
                "AGENT_ERROR_CLEARED task_id=%s state=%s request_id=%s "
                "previous_code=%s previous_reason=%s",
                ctx.task_id,
                self.name,
                ctx.active_request_id,
                previous_error.get("code", ""),
                previous_error.get("reason", ""),
            )
        logger.info(
            "AGENT_REPLY_ACCEPTED task_id=%s state=%s request_id=%s external_id=%s action=%s",
            ctx.task_id,
            self.name,
            ctx.active_request_id,
            message.external_id,
            validated_payload.get("action"),
        )
        self.release_active_lease(ctx)
        return Event("AGENT_REPLY_ACCEPTED", validated_payload)

    def notify_heartbeat(self, ctx: StateContext, elapsed_seconds: int) -> None:
        notify = getattr(self.feishu, "notify", None)
        ctx.updated_at = self._now()
        ctx.heartbeat_count += 1
        logger.info(
            "FEISHU_NOTIFY_SUPPRESSED task_id=%s state=%s role=%s event=HEARTBEAT",
            ctx.task_id,
            self.name,
            ctx.current_role,
        )
        if not notify or not ctx.current_role or not should_emit_notification("HEARTBEAT"):
            return
        ctx.last_heartbeat_epoch = self.clock.now().timestamp()
        text = build_agent_notification(
            ctx.current_role,
            self.name,
            "HEARTBEAT",
            ctx,
            {
                "action": "WAITING_FOR_AGENT",
                "elapsed_seconds": elapsed_seconds,
                "expected_agent_id": ctx.expected_agent_id,
            },
        )
        self._notify_once(
            ctx,
            f"{ctx.task_id}:{ctx.sequence}:heartbeat:{ctx.heartbeat_count}",
            notify,
            text,
            ctx.current_role.split("-")[-1],
            "HEARTBEAT",
        )

    def exit(self, ctx: StateContext, event: Event) -> None:
        ctx.updated_at = self._now()
        # Transitions can be triggered by timeout, cancellation, or recovery
        # paths that did not pass through update().  Release any in-process
        # admission lease before clearing the request identity.
        self.release_active_lease(ctx)
        if event.name in {"AGENT_REPLY_ACCEPTED", "LOCAL_VALIDATION_PASSED"}:
            ctx.last_artifact_id = f"artifact-{ctx.sequence + 1:06d}"
        notify = getattr(self.feishu, "notify", None)
        if event.name != "NOOP" and ctx.current_role and should_emit_notification(event.name):
            self._notify_once(
                ctx,
                f"{ctx.task_id}:{ctx.sequence + 1}:{self.name}:exit:{event.name}",
                notify,
                build_agent_notification(
                    ctx.current_role,
                    self.name,
                    event.name,
                    ctx,
                    event.payload if event.name == "AGENT_REPLY_ACCEPTED" else None,
                ),
                ctx.current_role.split("-")[-1] if ctx.current_role else "gate",
                event.name,
            )
        ctx.active_request_id = ""
        ctx.dispatch_status = "none"
        ctx.dispatch_operation_id = None
        ctx.dispatch_external_message_id = None
        ctx.dispatch_idempotency_key = None

    @staticmethod
    def _notify_once(
        ctx: StateContext,
        key: str,
        notify: Any,
        text: str,
        role: str,
        event_name: str,
    ) -> None:
        if not notify or key in ctx.sent_notification_keys:
            return
        logger.info(
            "FEISHU_NOTIFY_START task_id=%s sequence=%s state=%s role=%s event=%s "
            "notification_key=%s text_chars=%s",
            ctx.task_id,
            ctx.sequence,
            ctx.workflow_state or "",
            role,
            event_name,
            key,
            len(text),
        )
        # Preserve the existing idempotency behavior: a notification key is
        # consumed before the external call, so a repeated state entry cannot
        # duplicate a notification even if the external adapter fails.
        ctx.sent_notification_keys.append(key)
        try:
            message_id = notify(text, role)
            if message_id:
                logger.info(
                    "FEISHU_NOTIFY_SUCCESS task_id=%s sequence=%s state=%s role=%s "
                    "event=%s notification_key=%s message_id=%s",
                    ctx.task_id,
                    ctx.sequence,
                    ctx.workflow_state or "",
                    role,
                    event_name,
                    key,
                    message_id,
                )
            else:
                logger.warning(
                    "FEISHU_NOTIFY_FAILED task_id=%s sequence=%s state=%s role=%s "
                    "event=%s notification_key=%s reason=empty_message_id",
                    ctx.task_id,
                    ctx.sequence,
                    ctx.workflow_state or "",
                    role,
                    event_name,
                    key,
                )
        except Exception as error:
            logger.exception(
                "FEISHU_NOTIFY_FAILED task_id=%s sequence=%s state=%s role=%s "
                "event=%s notification_key=%s error_type=%s",
                ctx.task_id,
                ctx.sequence,
                ctx.workflow_state or "",
                role,
                event_name,
                key,
                type(error).__name__,
            )
            ctx.last_error = {
                "code": "FEISHU_NOTIFY_FAILED",
                "message": str(error),
                "notification_key": key,
            }

    def active_request(self, ctx: StateContext) -> bool:
        return bool(ctx.active_request_id)

    def request(self, ctx: StateContext) -> AgentRequest:
        prompt = ctx.raw_request
        request_context: dict[str, Any] = {}
        human_decision = ctx.request_payload.get("human_decision")
        if self.name == "ZHONGSHU_ANALYST":
            prompt = _zhongshu_analyst_prompt(ctx)
        elif self.name == "ZHONGSHU_SOLVER":
            if _is_multica_resume(ctx):
                prompt = _zhongshu_solver_resume_prompt(ctx)
            else:
                prompt = _zhongshu_solver_prompt(ctx)
        elif self.name == "ZHONGSHU_CRITIC":
            prompt = _zhongshu_critic_prompt(ctx)
        elif self.name == "MENXIA_ITEM_SOLVER":
            request_context = _menxia_item_solver_capsule(ctx)
            prompt = _menxia_item_solver_prompt(ctx, externalize_context=True)
        elif ctx.current_phase == "MENXIA":
            prompt = json.dumps({
                "task": ctx.raw_request,
                "group_id": ctx.active_group_id,
                "item_id": ctx.active_item_id,
                "context": ctx.request_payload,
            }, ensure_ascii=False)
        elif isinstance(human_decision, dict):
            prompt = json.dumps({
                "task": ctx.raw_request,
                "human_decision": human_decision,
                "instruction": "Apply the user's human-gate decision to the existing issue history and produce the next structured protocol response.",
            }, ensure_ascii=False)
        request_context["active_runtime_skill"] = ACTIVE_SKILL_BY_STATE.get(self.name, "")
        request_context["active_runtime_skill_directive"] = _active_skill_directive(self.name)
        request_context["active_runtime_skill_lock"] = _runtime_skill_lock(self.name)
        request_context["active_runtime_phase"] = ctx.current_phase
        request_context["active_runtime_state"] = self.name
        request_context["target_state"] = self.name
        request_context["target_role"] = ctx.current_role
        request_context["reply_correlation_id"] = ctx.active_request_id
        request_context["structured_output_state"] = self.name
        if self.name == "ZHONGSHU_ANALYST":
            request_context["zhongshu_dispatch_mode"] = (
                "evidence_supplement"
                if ctx.request_payload.get("zhongshu_evidence_request")
                else "evidence_collection"
            )
        if self.name == "ZHONGSHU_SOLVER":
            candidate_plan = ctx.request_payload.get("candidate_plan")
            if isinstance(candidate_plan, dict) and isinstance(candidate_plan.get("plan"), dict):
                candidate_plan = candidate_plan["plan"]
            request_context["has_current_plan"] = bool(
                isinstance(candidate_plan, dict)
                and isinstance(candidate_plan.get("items"), list)
                and isinstance(candidate_plan.get("groups"), list)
            )
            request_context["solver_revision_mode"] = request_context["has_current_plan"]
            request_context["solver_resume_mode"] = _is_multica_resume(ctx)
        if ctx.active_group_id:
            request_context["group_id"] = ctx.active_group_id
        if ctx.active_item_id:
            request_context["item_id"] = ctx.active_item_id
        structured_spec = build_structured_output_spec(
            ctx.current_phase,
            ctx.current_role,
            {**ctx.request_payload, **request_context},
        )
        structured_output = structured_spec.to_dict() if structured_spec else None
        if structured_output:
            request_context["structured_output"] = copy.deepcopy(structured_output)
            request_context["structured_output_role_mode"] = structured_spec.role_mode
            try:
                prompt_value = json.loads(prompt)
            except (TypeError, json.JSONDecodeError):
                prompt_value = None
            if isinstance(prompt_value, dict):
                prompt_value["stable_role_response"] = role_result_template(
                    ctx.current_phase,
                    ctx.current_role,
                    task_id=ctx.task_id,
                    request_id=ctx.active_request_id,
                    state=self.name,
                    role_mode=structured_spec.role_mode,
                    schema_hash=structured_spec.schema_hash,
                )
                prompt_value["response_contract"] = _canonical_prompt_response_contract(
                    prompt_value.get("response_contract"),
                    phase=ctx.current_phase,
                    role=ctx.current_role,
                    state=self.name,
                    role_mode=structured_spec.role_mode,
                    schema_hash=structured_spec.schema_hash,
                )
                prompt = json.dumps(
                    prompt_value,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
        if self.name.startswith("ZHONGSHU"):
            prompt_value = json.loads(prompt)
            prompt_value["human_decision"] = copy.deepcopy(human_decision)
            prompt_value["evidence_request"] = copy.deepcopy(ctx.request_payload.get("zhongshu_evidence_request"))
            prompt_value["evidence_gap_context"] = copy.deepcopy(ctx.request_payload.get("zhongshu_evidence_gap"))
            prompt = json.dumps(prompt_value, ensure_ascii=False, separators=(",", ":"))
        prompt = _attach_repair_feedback(prompt, ctx)
        return AgentRequest(
            task_id=ctx.task_id,
            request_id=ctx.active_request_id,
            agent_id=ctx.expected_agent_id,
            role=ctx.current_role,
            phase=ctx.current_phase,
            prompt=prompt,
            idempotency_key=ctx.dispatch_idempotency_key or "",
            issue_id=ctx.issue_id,
            sent_after=ctx.last_sent_at,
            dispatch_external_message_id=ctx.dispatch_external_message_id or "",
            context=request_context,
            target_state=self.name,
            target_role=ctx.current_role,
            structured_output=structured_output,
        )

    def _dispatch_once(self, ctx: StateContext) -> None:
        if self.multica is None or not ctx.active_request_id:
            return
        if ctx.dispatch_status == "confirmed" and ctx.dispatch_external_message_id:
            return
        if ctx.dispatch_status == "confirmed" and not ctx.dispatch_external_message_id:
            # Older state files could mark a dispatch confirmed even though
            # the trigger comment id was lost.  Reconcile by idempotency key
            # before considering a new dispatch; never blindly duplicate it.
            ctx.dispatch_status = "pending"
        request = self.request(ctx)
        if self.name == "MENXIA_ITEM_SOLVER":
            _log_menxia_item_solver_prompt_request(ctx, request.prompt)
        logger.info(
            "DISPATCH_START task_id=%s state=%s role=%s request_id=%s attempt=%s",
            ctx.task_id,
            self.name,
            ctx.current_role,
            ctx.active_request_id,
            ctx.dispatch_attempt + 1,
        )
        if self.admission:
            try:
                lease = self.admission.try_acquire(
                    request.task_id,
                    request.phase,
                    request.role,
                    request.request_id,
                )
            except DuplicateLeaseError:
                logger.info(
                    "CONCURRENCY_DISPATCH_WAIT task_id=%s state=%s request_id=%s "
                    "reason=duplicate_active_lease",
                    ctx.task_id,
                    self.name,
                    ctx.active_request_id,
                )
                return
            if lease is None:
                logger.info(
                    "CONCURRENCY_DISPATCH_WAIT task_id=%s state=%s request_id=%s "
                    "reason=admission_limit",
                    ctx.task_id,
                    self.name,
                    ctx.active_request_id,
                )
                return
            self._leases[request.request_id] = lease.lease_id
            logger.info(
                "CONCURRENCY_DISPATCH_ADMITTED task_id=%s state=%s request_id=%s lease_id=%s",
                ctx.task_id,
                self.name,
                request.request_id,
                lease.lease_id,
            )
        try:
            existing = self.multica.find_existing_request(request.idempotency_key, request.issue_id)
            if existing is not None:
                ctx.dispatch_operation_id = existing.operation_id
                ctx.dispatch_external_message_id = existing.external_message_id
                ctx.dispatch_status = (
                    "confirmed" if existing.external_message_id else "pending"
                )
                if not existing.external_message_id:
                    ctx.last_error = {
                        "code": "DISPATCH_CORRELATION_UNAVAILABLE",
                        "message": "existing external request has no trigger message id; polling by request binding",
                        "request_id": request.request_id,
                    }
                    logger.warning(
                        "DISPATCH_CORRELATION_UNAVAILABLE task_id=%s state=%s request_id=%s source=existing_request",
                        ctx.task_id,
                        self.name,
                        request.request_id,
                    )
                return
            ctx.dispatch_status = "executing"
            ctx.dispatch_attempt += 1
            ctx.last_sent_at = self._now()
            receipt = self.multica.dispatch(request)
            ctx.dispatch_operation_id = receipt.operation_id
            ctx.dispatch_external_message_id = receipt.external_message_id
            ctx.dispatch_status = (
                "confirmed"
                if receipt.confirmed and receipt.external_message_id
                else "pending"
            )
            if receipt.confirmed and not receipt.external_message_id:
                ctx.last_error = {
                    "code": "DISPATCH_CORRELATION_UNAVAILABLE",
                    "message": "dispatch confirmed without trigger message id; polling by request binding",
                    "request_id": request.request_id,
                }
                logger.warning(
                    "DISPATCH_CORRELATION_UNAVAILABLE task_id=%s state=%s request_id=%s source=new_dispatch",
                    ctx.task_id,
                    self.name,
                    request.request_id,
                )
            logger.info(
                "DISPATCH_END task_id=%s state=%s request_id=%s status=%s operation_id=%s external_message_id=%s",
                ctx.task_id,
                self.name,
                ctx.active_request_id,
                ctx.dispatch_status,
                ctx.dispatch_operation_id,
                ctx.dispatch_external_message_id,
            )
        except Exception as error:
            self.release_active_lease(ctx)
            logger.exception(
                "DISPATCH_FAILED task_id=%s state=%s request_id=%s error_type=%s",
                ctx.task_id,
                self.name,
                ctx.active_request_id,
                type(error).__name__,
            )
            ctx.dispatch_status = "error"
            ctx.last_error = {
                "code": "MULTICA_DISPATCH_FAILED",
                "message": str(error),
                "request_id": request.request_id,
            }

    def _now(self) -> str:
        return self.clock.now().isoformat()


ANALYST_PLAN_REQUIRED_FIELDS = {
    "plan_id": str,
    "version": (int, str),
    "phase": str,
    "problem_interpretation": str,
    "objective": str,
    "success_definition": str,
    "requirements": list,
    "goals": list,
    "non_goals": list,
    "project_context": dict,
    "confirmed_facts": list,
    "conflicts": list,
    "candidate_directions": list,
    "selected_direction": dict,
    "alternatives": list,
    "comparison": list,
    "recommendation": dict,
    "candidate_items": list,
    "candidate_groups": list,
    "dependencies": list,
    "constraints": list,
    "scope": dict,
    "assumptions": list,
    "unknowns": list,
    "risks": list,
    "questions_for_solver": list,
    "evidence_requests": list,
    "candidate_verification_questions": list,
    "questions_for_user": list,
}


def _attach_repair_feedback(prompt: str, ctx: StateContext) -> str:
    """Attach only bounded contract repair paths to a retry prompt."""
    error = ctx.last_error if isinstance(ctx.last_error, dict) else {}
    state = str(ctx.resume_state or ctx.workflow_state or "")
    try:
        contract = contract_for_state(state)
    except KeyError:
        return prompt

    raw_reasons: list[str] = []
    reason = str(error.get("reason") or "")
    if reason:
        raw_reasons.append(reason)
    parallel = error.get("parallel")
    if isinstance(parallel, dict):
        for worker in (*parallel.get("failed_workers", ()), *parallel.get("rejected_workers", ())):
            if isinstance(worker, dict):
                raw_reasons.append(str(worker.get("error") or worker.get("reason") or ""))

    contract_errors: list[str] = []
    repair_hints: list[str] = []
    for raw_reason in raw_reasons:
        marker = "STRUCTURED_ROLE_CONTRACT_INVALID:"
        if marker in raw_reason:
            contract_errors.extend(
                item.strip()
                for item in raw_reason.split(marker, 1)[1].split(";")
                if item.strip()
            )
        elif raw_reason.startswith("STRUCTURED_ROLE_FIELDS_MISSING:"):
            contract_errors.extend(
                f"{item.strip()}: required"
                for item in raw_reason.split(":", 1)[1].split(",")
                if item.strip()
            )
        elif raw_reason.startswith("SOLVER_DEPENDENCY_CYCLE:"):
            contract_errors.append(raw_reason)
            repair_hints.append(
                "Repair the dependency edge or edges that create the cycle; dependencies must be one-way execution prerequisites and form a DAG, then verify a topological order before READY_FOR_CRITIC."
            )
        elif raw_reason.startswith("SOLVER_DEPENDENCY_UNKNOWN:"):
            contract_errors.append(raw_reason)
            repair_hints.append(
                "Every dependency must reference an existing item_id; remove or correct the invalid reference."
            )
        elif raw_reason.startswith("SOLVER_ITEM_DEPENDENCIES_INVALID:"):
            contract_errors.append(raw_reason)
            repair_hints.append("Dependencies must be a list of non-empty item_id strings.")
        elif raw_reason.startswith("SOLVER_GROUP_ITEM_MISMATCH:"):
            contract_errors.append(raw_reason)
            repair_hints.append(
                "The Solver transport format is canonical: keep complete task objects only in plan.items and emit groups[*].item_ids references; never duplicate task objects in groups[*].items."
            )
    if not contract_errors:
        return prompt

    if any(
        "plan.groups[" in error and ".items" in error
        for error in contract_errors
    ):
        repair_hints.append(
            "The Solver transport format is canonical: keep complete task objects only in plan.items and emit groups[*].item_ids references; never duplicate task objects in groups[*].items."
        )

    repair = {
        "contract_id": contract.contract_id,
        "errors": list(dict.fromkeys(contract_errors))[:20],
        "instruction": [*contract.repair_instructions(contract_errors), *repair_hints],
    }
    try:
        prompt_value = json.loads(prompt)
    except (TypeError, json.JSONDecodeError):
        return prompt
    if not isinstance(prompt_value, dict):
        return prompt
    prompt_value["contract_repair"] = repair
    logger.info(
        "SOLVER_REPAIR_FEEDBACK_ATTACHED task_id=%s state=%s errors=%s",
        ctx.task_id,
        state,
        repair["errors"],
    )
    return json.dumps(prompt_value, ensure_ascii=False, separators=(",", ":"))


def _latest_rejected_reply(ctx: StateContext) -> dict[str, Any] | None:
    history = ctx.reply_history if isinstance(ctx.reply_history, list) else []
    for entry in reversed(history):
        if not isinstance(entry, dict):
            continue
        if str(entry.get("state") or "") != ctx.workflow_state:
            continue
        payload = entry.get("payload")
        if isinstance(payload, dict):
            return copy.deepcopy(payload)
    legacy = ctx.request_payload.get("last_rejected_reply")
    return copy.deepcopy(legacy) if isinstance(legacy, dict) else None



def _pick_dict(value: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {key: copy.deepcopy(value[key]) for key in keys if key in value}


def _current_item_payload(
    request_payload: dict[str, Any],
    collection_key: str,
    item_id: str,
) -> dict[str, Any] | None:
    collection = request_payload.get(collection_key)
    if not isinstance(collection, dict) or not item_id:
        return None
    value = collection.get(item_id)
    return copy.deepcopy(value) if isinstance(value, dict) else None


def _item_evidence(request_payload: dict[str, Any], item: dict[str, Any]) -> list[dict[str, Any]]:
    evidence_ids: list[str] = []
    for value in item.get("evidence_basis", []):
        evidence_id = str(value or "")
        if evidence_id and evidence_id not in evidence_ids:
            evidence_ids.append(evidence_id)
    if not evidence_ids:
        return []
    index: dict[str, dict[str, Any]] = {}
    analyst_plan = request_payload.get("analyst_plan")
    if isinstance(analyst_plan, dict):
        facts = analyst_plan.get("confirmed_facts")
        if isinstance(facts, list):
            for fact in facts:
                if isinstance(fact, dict) and fact.get("evidence_id"):
                    index[str(fact["evidence_id"])] = copy.deepcopy(fact)
    return [index[evidence_id] for evidence_id in evidence_ids if evidence_id in index]


def _critic_finding_ids(review: object) -> list[str]:
    if not isinstance(review, dict):
        return []
    findings = review.get("findings")
    if not isinstance(findings, list):
        return []
    result: list[str] = []
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        finding_id = str(finding.get("finding_id") or finding.get("id") or "").strip()
        if finding_id and finding_id not in result:
            result.append(finding_id)
    return result


def _solver_response_finding_ids(payload: object) -> list[str]:
    if not isinstance(payload, dict):
        return []
    responses = payload.get("responses_to_critic")
    if not isinstance(responses, list):
        return []
    result: list[str] = []
    for response in responses:
        if not isinstance(response, dict):
            continue
        finding_id = str(response.get("finding_id") or response.get("id") or "").strip()
        if finding_id and finding_id not in result:
            result.append(finding_id)
    return result


def _log_menxia_item_solver_prompt_request(ctx: StateContext, prompt: str) -> None:
    item_id = str(ctx.active_item_id or "")
    critic_review = _current_item_payload(
        ctx.request_payload,
        "item_critic_reviews",
        item_id,
    ) or {}
    finding_ids = _critic_finding_ids(critic_review)
    analyst_review = _current_item_payload(
        ctx.request_payload,
        "item_analyst_reviews",
        item_id,
    )
    previous_proposal = _current_item_payload(
        ctx.request_payload,
        "item_implementation_proposals",
        item_id,
    )
    logger.info(
        "MENXIA_ITEM_SOLVER_PROMPT_READY task_id=%s state=%s group_id=%s item_id=%s "
        "request_id=%s attempt=%s prompt_chars=%s prompt_bytes=%s "
        "critic_finding_count=%s critic_finding_ids=%s analyst_review=%s "
        "critic_review=%s previous_proposal=%s repair_feedback=%s",
        ctx.task_id,
        ctx.workflow_state,
        ctx.active_group_id or "",
        item_id,
        ctx.active_request_id,
        ctx.dispatch_attempt + 1,
        len(prompt),
        len(prompt.encode("utf-8")),
        len(finding_ids),
        finding_ids,
        bool(analyst_review),
        bool(critic_review),
        bool(previous_proposal),
        bool(ctx.last_error),
    )


def _menxia_item_solver_capsule(ctx: StateContext) -> dict[str, Any]:
    request_payload = ctx.request_payload if isinstance(ctx.request_payload, dict) else {}
    item = request_payload.get("active_item")
    group = request_payload.get("active_group")
    item = copy.deepcopy(item) if isinstance(item, dict) else {}
    group = copy.deepcopy(group) if isinstance(group, dict) else {}
    item_id = str(ctx.active_item_id or item.get("item_id") or "")
    previous = _current_item_payload(request_payload, "item_implementation_proposals", item_id)
    analyst_review = _current_item_payload(request_payload, "item_analyst_reviews", item_id)
    critic_review = _current_item_payload(request_payload, "item_critic_reviews", item_id)
    return {
        "task": ctx.raw_request,
        "stage": {
            "phase": "MENXIA",
            "role": "review-solver",
            "purpose": "build a feasible and verifiable proposal for the current item",
        },
        "scope": {"group_id": ctx.active_group_id, "item_id": item_id},
        "inputs": {
            "group": _pick_dict(group, ("group_id", "title", "objective", "dependencies", "suggested_order", "shared_acceptance")),
            "item": _pick_dict(item, ("item_id", "title", "objective", "requirements", "dependencies", "acceptance", "suggested_order")),
            "evidence": _item_evidence(request_payload, item),
            "analyst_review": analyst_review,
            "critic_review": critic_review,
            "previous_item_proposal": previous,
        },
        "constraints": {
            "must_preserve": ["current item id, evidence basis, scope, and risk boundaries", "evidence ids from supplied evidence only"],
            "must_not_change": ["other items or groups", "FSM state or transport identity", "global plan without Orchestrator validation"],
        },
        "response_contract": {
            "instruction": "write one complete fixed Solver role-protocol JSON object to result_path using a real serializer; return only the result pointer and do not output Markdown or launcher logs",
        },
    }


def _menxia_item_solver_prompt(
    ctx: StateContext,
    *,
    externalize_context: bool = False,
) -> str:
    capsule = _menxia_item_solver_capsule(ctx)
    serialized = json.dumps(capsule, ensure_ascii=False, separators=(",", ":"))
    original_bytes = len(serialized.encode("utf-8"))
    if original_bytes > 24 * 1024:
        compact_capsule = _compact_prompt_value(capsule)
        serialized = json.dumps(
            compact_capsule,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        compact_bytes = len(serialized.encode("utf-8"))
        logger.warning(
            "AGENT_PROMPT_BUDGET_FALLBACK task_id=%s state=%s group_id=%s "
            "item_id=%s original_bytes=%s compact_bytes=%s limit=%s mode=compact_capsule",
            ctx.task_id,
            ctx.workflow_state,
            ctx.active_group_id or "",
            ctx.active_item_id or "",
            original_bytes,
            compact_bytes,
            24 * 1024,
        )
        if compact_bytes > 24 * 1024:
            minimal_capsule = {
                "task": ctx.raw_request,
                "stage": capsule["stage"],
                "scope": capsule["scope"],
                "inputs": {
                    "group": capsule["inputs"]["group"],
                    "item": capsule["inputs"]["item"],
                    "evidence": [
                        {
                            "evidence_id": value.get("evidence_id", ""),
                            "statement": value.get("statement", "")[:500],
                        }
                        for value in capsule["inputs"]["evidence"]
                        if isinstance(value, dict)
                    ][:12],
                    "analyst_review": {"status": "omitted_due_to_prompt_budget"},
                    "critic_review": {"status": "omitted_due_to_prompt_budget"},
                    "previous_item_proposal": {"status": "omitted_due_to_prompt_budget"},
                },
                "constraints": capsule["constraints"],
                "response_contract": capsule["response_contract"],
            }
            serialized = json.dumps(
                minimal_capsule,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            logger.warning(
                "AGENT_PROMPT_BUDGET_MINIMAL_FALLBACK task_id=%s state=%s "
                "group_id=%s item_id=%s compact_bytes=%s minimal_bytes=%s limit=%s",
                ctx.task_id,
                ctx.workflow_state,
                ctx.active_group_id or "",
                ctx.active_item_id or "",
                compact_bytes,
                len(serialized.encode("utf-8")),
                24 * 1024,
            )
    selected_source_keys = {
        "active_group",
        "active_item",
        "analyst_plan",
        "item_implementation_proposals",
        "item_analyst_reviews",
        "item_critic_reviews",
    }
    omitted_keys = sorted(
        key for key in ctx.request_payload
        if key not in selected_source_keys
    )
    logger.info(
        "AGENT_PROMPT_BUILT task_id=%s state=%s group_id=%s item_id=%s "
        "prompt_chars=%s prompt_bytes=%s input_keys=%s evidence_count=%s "
        "omitted_keys=%s analyst_review=%s critic_review=%s previous_proposal=%s",
        ctx.task_id,
        ctx.workflow_state,
        ctx.active_group_id or "",
        ctx.active_item_id or "",
        len(serialized),
        len(serialized.encode("utf-8")),
        sorted(capsule["inputs"].keys()),
        len(capsule["inputs"]["evidence"]),
        omitted_keys,
        bool(capsule["inputs"]["analyst_review"]),
        bool(capsule["inputs"]["critic_review"]),
        bool(capsule["inputs"]["previous_item_proposal"]),
    )
    if externalize_context:
        logger.info(
            "AGENT_PROMPT_CONTEXT_EXTERNALIZED task_id=%s state=%s group_id=%s "
            "item_id=%s context_bytes=%s prompt_bytes=%s",
            ctx.task_id,
            ctx.workflow_state,
            ctx.active_group_id or "",
            ctx.active_item_id or "",
            len(serialized.encode("utf-8")),
            len(
                "Read context.json from the prompt bundle as UTF-8. "
                "Use it as the complete current-item context, then write the fixed role-protocol result to result_path and return only its result pointer."
                .encode("utf-8")
            ),
        )
        return (
            "Read context.json from the prompt bundle as UTF-8. "
            "Use it as the complete current-item context, then write the fixed role-protocol result to result_path and return only its result pointer."
        )
    return serialized


def _compact_prompt_value(value: Any, *, depth: int = 0) -> Any:
    """Bound historical prompt context without changing current item identity."""
    if depth >= 4:
        if isinstance(value, (dict, list)):
            return "[omitted:prompt_depth_limit]"
        return str(value)[:800]
    if isinstance(value, str):
        return value if len(value) <= 1800 else value[:1800] + "…[truncated]"
    if isinstance(value, list):
        limit = 12 if depth < 2 else 6
        compacted = [
            _compact_prompt_value(item, depth=depth + 1)
            for item in value[:limit]
        ]
        if len(value) > limit:
            compacted.append(f"[omitted:{len(value) - limit} items]")
        return compacted
    if isinstance(value, dict):
        compacted: dict[str, Any] = {}
        for key, item in value.items():
            compacted[str(key)] = _compact_prompt_value(item, depth=depth + 1)
        return compacted
    return value

def _zhongshu_analyst_prompt(ctx: StateContext) -> str:
    """Build the bounded Zhongshu requirement-to-task discovery contract."""
    supplement_mode = bool(ctx.request_payload.get("zhongshu_evidence_request"))
    dispatch_mode = "evidence_supplement" if supplement_mode else "evidence_collection"
    role_mode = "EVIDENCE_SUPPLEMENT" if supplement_mode else "EVIDENCE_COLLECTION_READ_ONLY"
    success_action = "EVIDENCE_SUPPLEMENT_READY" if supplement_mode else "EVIDENCE_PACKET_READY"
    analyst_structured_spec = build_structured_output_spec(
        "ZHONGSHU",
        "review-analyst",
        {
            "active_runtime_state": "ZHONGSHU_ANALYST",
            "zhongshu_dispatch_mode": dispatch_mode,
        },
    )
    value: dict[str, Any] = {
        "task": ctx.raw_request,
        "role": "ZHONGSHU_ANALYST",
        "mode": role_mode,
        "role_objective": (
            "Establish a traceable evidence packet for Solver. Analyst collects requirements, "
            "facts, sources, constraints, risks, and unknowns; Solver alone creates and groups tasks."
        ),
        "limits": {
            "max_evidence_updates": 6,
            "max_evidence_requests": 2,
            "max_questions_for_solver": 0,
            "no_exhaustive_repository_scan": True,
        },
        "working_rules": [
            "Separate user requirements, confirmed facts, assumptions, unknowns, and risks.",
            "Copy every requirement object from requirement_contract verbatim and in the same order, including requirement_id, statement, source, priority, scope, kind, and acceptance_signal; never paraphrase, omit, invent, reorder, or redefine these immutable fields. The orchestrator owns this contract; you only add evidence. Distinguish facts, assumptions, unknowns, risks, and conflicts.",
            "Record only evidence that can change requirement coverage, task boundaries, dependencies, acceptance, scope, or risk. One evidence update contains one concise conclusion.",
            "Prefer one canonical evidence update per fact; do not restate the same evidence as multiple conclusions.",
            "Record evidence sources and conclusions; an unverified claim must remain unknown or conflicted.",
            "Do not create, name, group, split, merge, or prioritize implementation tasks.",
            "Do not emit implementation_proposal, file_changes, code_changes, or function-level design.",
            "Return task_proposals, candidate_items, and candidate_groups as empty arrays.",
            "questions_for_solver must be [] unless a specific unresolved fact blocks task decomposition; never ask Solver to choose an implementation technology or optimization strategy.",
            "Use evidence_requests only for a specific blocking fact. Each request must name item_id or requirement_id, and include one question and one reason; do not use it for open-ended design advice.",
            "Stop when the evidence packet is complete enough for Solver; write one complete Analyst role-protocol JSON object to result_path and return only its result pointer.",
        ],
        "response_contract": {
            "success_action": success_action,
            "other_actions": ["HUMAN_GATE", "BLOCKED"],
            "format": "result_file_json_plus_compact_pointer_no_markdown_no_code_fence",
        },
    }
    value["stable_role_response"] = role_result_template(
        "ZHONGSHU",
        "review-analyst",
        task_id=ctx.task_id,
        request_id=ctx.active_request_id,
        state="ZHONGSHU_ANALYST",
        role_mode=role_mode,
        schema_hash=(analyst_structured_spec.schema_hash if analyst_structured_spec else ""),
        action=success_action,
    )
    critic_review = ctx.request_payload.get("zhongshu_critic_review")
    if isinstance(critic_review, dict) and critic_review.get("action") == "REQUEST_ANALYST_EVIDENCE":
        value["critic_feedback"] = {
            "action": "REQUEST_ANALYST_EVIDENCE",
            "findings": copy.deepcopy(critic_review.get("findings") or []),
            "missing_evidence": copy.deepcopy(critic_review.get("missing_evidence") or []),
            "questions_for_analyst": copy.deepcopy(
                critic_review.get("questions_for_analyst")
                or critic_review.get("missing_evidence")
                or []
            ),
            "remaining_blockers": copy.deepcopy(critic_review.get("remaining_blockers") or []),
        }
        value["repair_instruction"] = (
            "只针对 Critic 指定的证据缺口补证；不要重新开始无关分析，"
            "无法确认的内容标记为 unknown。"
        )
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _compact_critic_finding(value: Any) -> Any:
    if not isinstance(value, dict):
        return copy.deepcopy(value)
    compact = {
        key: copy.deepcopy(item)
        for key, item in value.items()
        if key != "observations"
    }
    if isinstance(value.get("observations"), list):
        compact["observation_count"] = len(value["observations"])
    return compact


def _compact_critic_review_for_prompt(value: Any) -> dict[str, Any] | None:
    """Project the durable review ledger without replaying worker payloads."""
    if not isinstance(value, dict):
        return None
    compact: dict[str, Any] = {}
    for key, item in value.items():
        if key in {"worker_reviews", "pending_actions", "rejected_reviews", "worker_results"}:
            continue
        if key == "findings" and isinstance(item, list):
            compact[key] = [_compact_critic_finding(finding) for finding in item]
        elif key == "conflicts" and isinstance(item, list):
            compact[key] = []
            for conflict in item:
                if not isinstance(conflict, dict):
                    compact[key].append(copy.deepcopy(conflict))
                    continue
                entry = {
                    field: copy.deepcopy(field_value)
                    for field, field_value in conflict.items()
                    if field != "observations"
                }
                if isinstance(conflict.get("observations"), list):
                    entry["observation_count"] = len(conflict["observations"])
                compact[key].append(entry)
        else:
            compact[key] = copy.deepcopy(item)

    summaries: list[dict[str, Any]] = []
    for review in value.get("worker_reviews") or []:
        if not isinstance(review, dict):
            continue
        summary: dict[str, Any] = {}
        for field in (
            "worker_id", "worker_lens", "action", "revision_id",
            "plan_revision_id", "plan_hash", "reviewed_plan_hash",
        ):
            if review.get(field) not in (None, "", []):
                summary[field] = copy.deepcopy(review[field])
        findings = review.get("findings") or []
        if isinstance(review.get("finding_ids"), list) and not findings:
            summary["finding_ids"] = copy.deepcopy(review["finding_ids"])
        else:
            summary["finding_ids"] = sorted({
                str(finding.get("finding_id") or finding.get("id"))
                for finding in findings
                if isinstance(finding, dict)
                and (finding.get("finding_id") or finding.get("id"))
            })
        if isinstance(review.get("review_summary"), str) and review["review_summary"]:
            summary["review_summary"] = review["review_summary"][:1200]
        summaries.append(summary)
    if summaries:
        compact["worker_summaries"] = summaries
    else:
        pending: list[dict[str, Any]] = []
        for item in value.get("pending_actions") or []:
            if not isinstance(item, dict):
                continue
            pending.append({
                field: copy.deepcopy(item[field])
                for field in ("worker_id", "worker_lens", "action", "finding_ids")
                if field in item
            })
        if pending:
            compact["worker_summaries"] = pending
    rejected = []
    for item in value.get("rejected_reviews") or []:
        if isinstance(item, dict):
            rejected.append({
                field: copy.deepcopy(item[field])
                for field in ("worker_id", "reason")
                if field in item
            })
    if rejected:
        compact["rejected_workers"] = rejected
    return compact


def _compact_solver_response_for_critic(value: Any) -> dict[str, Any] | None:
    """Keep Solver decisions while removing the plan already in review_target."""
    if not isinstance(value, dict):
        return None
    return {
        key: copy.deepcopy(item)
        for key, item in value.items()
        if key not in {"plan", "current_formal_plan", "worker_results", "worker_reviews"}
    }


def _compact_critic_plan(value: dict[str, Any]) -> dict[str, Any]:
    """Keep one canonical graph view; remove only prompt-only repetitions."""
    plan = copy.deepcopy(value)
    plan["evidence_updates"] = merge_evidence_updates(plan.get("evidence_updates"))
    for item in plan.get("items") or []:
        if isinstance(item, dict):
            item["evidence_updates"] = merge_evidence_updates(item.get("evidence_updates"))
    for group in plan.get("groups") or []:
        if not isinstance(group, dict):
            continue
        group_items = group.get("items")
        if isinstance(group_items, list):
            group["item_ids"] = [
                str(item.get("item_id"))
                for item in group_items
                if isinstance(item, dict) and item.get("item_id")
            ]
            group.pop("items", None)
    plan.pop("worker_evidence", None)
    return plan


def _compact_critic_analyst_evidence(value: Any) -> dict[str, Any]:
    """Provide traceability inputs without replaying the candidate task graph."""
    if not isinstance(value, dict):
        return {}
    fields = (
        "requirements", "dependencies", "scope", "protected_paths",
        "constraints", "confirmed_facts", "unknowns", "risks", "conflicts",
        "questions_for_solver", "evidence_requests", "unknown_requirements", "unknown_requirement_ids",
        "unknown_resolutions",
    )
    compact = {
        key: copy.deepcopy(value[key])
        for key in fields
        if key in value
    }
    compact["candidate_item_index"] = [
        {
            key: copy.deepcopy(item[key])
            for key in (
                "item_id", "title", "objective", "source_requirement_ids",
                "dependencies", "acceptance_signals", "evidence_ids",
                "basis_evidence", "unknowns", "risks", "risk_signals",
                "parallelizable", "group_key",
            )
            if key in item
        }
        for item in value.get("candidate_items") or []
        if isinstance(item, dict) and item.get("item_id")
    ]
    compact["candidate_group_index"] = [
        {
            key: copy.deepcopy(group[key])
            for key in (
                "candidate_group_id", "title", "objective", "related_items",
                "basis_evidence", "dependencies", "suggested_order",
            )
            if key in group
        }
        for group in value.get("candidate_groups") or []
        if isinstance(group, dict)
        and (group.get("candidate_group_id") or group.get("group_id"))
    ]
    updates = list(records(value.get("evidence_updates")))
    for item in value.get("candidate_items") or []:
        if isinstance(item, dict):
            updates.extend(records(item.get("evidence_updates")))
    compact["evidence_updates"] = merge_evidence_updates(updates)
    worker_evidence = value.get("worker_evidence")
    if isinstance(worker_evidence, dict):
        compact["worker_evidence_workers"] = sorted(str(key) for key in worker_evidence)
    return compact


def _zhongshu_critic_prompt(ctx: StateContext) -> str:
    # Request construction must remain total during FSM recovery.  The critic
    # validator still rejects an empty graph; prompt construction must not
    # crash the orchestrator before it can emit a structured reply.
    plan = ctx.request_payload.get("candidate_plan")
    if not isinstance(plan, dict):
        plan = {}
    plan_hash = canonical_plan_hash(plan)
    previous = ctx.request_payload.get("zhongshu_critic_review")
    return json.dumps({
        "task": ctx.raw_request,
        "role": "ZHONGSHU_CRITIC",
        "mode": "REVIEW_CURRENT_TASK_GRAPH",
        "review_target": {
            "plan_hash": plan_hash,
            "plan": _compact_critic_plan(plan),
            "projection": {
                "groups_use_item_ids": True,
                "omitted_from_prompt": ["worker_evidence", "duplicated_group_items", "duplicate_evidence_updates"],
                "hash_basis": "the persisted full candidate_plan, not this prompt projection",
            },
        },
        "previous_review": _compact_critic_review_for_prompt(previous),
        "solver_response": _compact_solver_response_for_critic(
            ctx.request_payload.get("zhongshu_solver_response")
        ),
        "analyst_evidence": _compact_critic_analyst_evidence(
            ctx.request_payload.get("analyst_plan") or {}
        ),
        "instruction": (
            "Review exactly review_target.plan as a requirement-to-task graph. "
            "Check requirement coverage, task boundaries, dependencies, grouping, "
            "parallelizability, acceptance signals, unknowns, and risks. Echo "
            "reviewed_plan_hash equal to review_target.plan_hash; use the supplied hash "
            "verbatim because the prompt contains a bounded semantic projection. Do not design "
            "implementation solutions or emit Menxia implementation details. Agreement is valid "
            "only after an independent review through the assigned worker lens. "
            "Review prior findings explicitly when possible; omission never closes them. Resolve with status=RESOLVED, "
            "resolution or response, and evidence_ids. Preserve finding identity; include owner_role and next_action "
            "for unresolved work. Use worker-specific IDs for new findings. Judge the task graph, not completion "
            "of future implementation or measurement tasks."
        ),
        "response_contract": {
            "instruction": "Use the injected Zhongshu Critic contract for the complete result shape and repair only reported paths.",
        },
    }, ensure_ascii=False, separators=(",", ":"))


def _zhongshu_task_review_capsule(
    ctx: StateContext,
    plan: dict[str, Any],
    job: ReviewJob,
) -> dict[str, Any]:
    """Build the only plan projection a task-scoped Critic may review."""
    groups = plan.get("groups") if isinstance(plan.get("groups"), list) else []
    if not groups and isinstance(plan.get("formal_groups"), list):
        groups = plan["formal_groups"]
    items = plan.get("items") if isinstance(plan.get("items"), list) else []
    if not items and isinstance(plan.get("candidate_items"), list):
        items = plan["candidate_items"]
    item_by_id = {
        str(item.get("item_id")): copy.deepcopy(item)
        for item in items
        if isinstance(item, dict) and item.get("item_id")
    }
    # Compact Solver plans may keep the complete item objects only inside
    # groups. Materialize the same lookup used by the queue so prompt scope
    # cannot fail merely because the plan chose the compact representation.
    for index, group in enumerate(groups):
        if not isinstance(group, dict):
            continue
        group_id = str(
            group.get("group_id")
            or group.get("candidate_group_id")
            or f"group-{index + 1:06d}"
        )
        for raw_item in group.get("items") or []:
            if not isinstance(raw_item, dict) or not raw_item.get("item_id"):
                continue
            item = copy.deepcopy(raw_item)
            item.setdefault("group_id", group_id)
            item_by_id.setdefault(str(item["item_id"]), item)
    selected_group: dict[str, Any] = {}
    selected_item: dict[str, Any] | None = item_by_id.get(job.item_id)
    for index, group in enumerate(groups):
        if not isinstance(group, dict):
            continue
        group_id = str(
            group.get("group_id")
            or group.get("candidate_group_id")
            or f"group-{index + 1:06d}"
        )
        if group_id != job.group_id:
            continue
        selected_group = _pick_dict(
            group,
            (
                "group_id", "candidate_group_id", "title", "objective",
                "dependencies", "shared_acceptance", "suggested_order",
            ),
        )
        selected_group["group_id"] = job.group_id
        group_items = group.get("items")
        if isinstance(group_items, list):
            for item in group_items:
                if isinstance(item, dict) and str(item.get("item_id")) == job.item_id:
                    selected_item = item
                    break
        break
    if selected_item is None:
        raise ValueError(f"ZHONGSHU_TASK_REVIEW_ITEM_NOT_FOUND:{job.group_id}/{job.item_id}")
    selected_item = copy.deepcopy(selected_item)
    selected_item.setdefault("group_id", job.group_id)

    requirement_by_id = {
        str(requirement.get("requirement_id")): requirement
        for requirement in plan.get("requirements") or []
        if isinstance(requirement, dict) and requirement.get("requirement_id")
    }
    requirement_refs = [
        copy.deepcopy(requirement_by_id[str(raw_requirement_id)])
        for raw_requirement_id in selected_item.get("source_requirement_ids") or []
        if str(raw_requirement_id) in requirement_by_id
    ]
    dependency_refs: list[Any] = []
    for dependency in dependency_refs_for_item(selected_item, item_by_id):
        if isinstance(dependency, dict):
            dependency_id = str(
                dependency.get("item_id")
                or dependency.get("to")
                or dependency.get("target")
                or ""
            )
            dependency_item = item_by_id.get(dependency_id, {})
            dependency_refs.append({
                **copy.deepcopy(dependency),
                "item_id": dependency_id,
                "group_id": str(
                    dependency.get("dependency_group_id")
                    or dependency_item.get("group_id")
                    or ""
                ),
                "title": str(dependency_item.get("title") or ""),
                "objective": str(dependency_item.get("objective") or ""),
            })
        else:
            dependency_refs.append(copy.deepcopy(dependency))
    previous = ctx.request_payload.get("zhongshu_critic_review")
    historical_findings = [
        copy.deepcopy(finding)
        for finding in (previous.get("findings") if isinstance(previous, dict) else []) or []
        if isinstance(finding, dict)
        and str(finding.get("group_id") or "") == job.group_id
        and str(finding.get("item_id") or "") == job.item_id
    ]
    return {
        "task": copy.deepcopy(selected_item),
        "group_context": selected_group,
        "requirement_refs": requirement_refs,
        "dependency_refs": dependency_refs,
        "historical_findings": historical_findings,
        "review_scope": {
            "scope_type": "item",
            "revision_id": job.revision_id,
            "group_id": job.group_id,
            "item_id": job.item_id,
            "plan_hash": canonical_plan_hash(plan),
            "task_hash": job.task_hash,
            "dependency_hash": job.dependency_hash,
        },
    }


def _zhongshu_task_critic_prompt(
    ctx: StateContext,
    plan: dict[str, Any],
    job: ReviewJob,
    worker_id: str,
) -> str:
    capsule = _zhongshu_task_review_capsule(ctx, plan, job)
    value = {
        "user_request": ctx.raw_request,
        "role": "ZHONGSHU_CRITIC",
        "mode": "REVIEW_ONE_TASK",
        "worker_id": worker_id,
        **capsule,
        "instruction": (
            "Review only review_scope.group_id and review_scope.item_id. "
            "Do not review or create findings for any other task. Check requirement "
            "coverage, task boundary, dependencies, acceptance signals, unknowns, "
            "and risks. Do not design implementation solutions. Every Finding must "
            "include the assigned group_id and item_id; a dependency issue must use "
            "the assigned item as its single primary owner and list related_item_ids. "
            "Return TASK_APPROVED only when all checks pass and no active P0/P1 remains. "
            "Return TASK_CHANGES_REQUIRED only with an actionable Finding. Echo all "
            "revision and hash fields exactly from review_scope. Return one structured "
            "JSON result-file response and no Markdown."
        ),
        "response_contract": {
            "instruction": "Use the injected task-review contract for the complete result shape and repair only reported paths.",
        },
    }
    return canonical_json(value)


def _validate_analyst_plan(payload: dict[str, Any]) -> str:
    if payload.get("action") == "EVIDENCE_PACKET_READY":
        return validate_zhongshu_evidence_packet(
            payload,
            max_evidence_updates=ANALYST_MAX_EVIDENCE_UPDATES_MERGED,
            max_evidence_requests=ANALYST_MAX_EVIDENCE_REQUESTS,
            require_decision_relevance=True,
        )
    if payload.get("action") != "READY_FOR_SOLVER":
        return ""
    plan = payload.get("plan")
    if not isinstance(plan, dict):
        return "ANALYST_PLAN_MISSING"
    # Analyst can only pass an evidence packet to Solver. A READY_FOR_SOLVER
    # payload is an internal fan-in envelope, never a legacy task graph.
    if plan.get("evidence_packet_version") != 1:
        return "ZHONGSHU_ANALYST_TASK_GRAPH_FORBIDDEN"
    if plan.get("evidence_packet_version") == 1:
        return validate_zhongshu_evidence_packet(
            {**copy.deepcopy(plan), "action": "EVIDENCE_PACKET_READY"},
            max_evidence_updates=ANALYST_MAX_EVIDENCE_UPDATES_MERGED,
            max_evidence_requests=ANALYST_MAX_EVIDENCE_REQUESTS,
            require_decision_relevance=True,
        )


def validate_zhongshu_task_graph(payload: dict[str, Any]) -> str:
    """Validate either a compact Analyst proposal or the canonical task graph."""
    action = str(payload.get("action") or "")
    forbidden = {
        "implementation_proposal",
        "file_changes",
        "code_changes",
        "function_changes",
        "implementation_steps",
    }

    def contains_forbidden(value: Any) -> bool:
        if isinstance(value, dict):
            if forbidden.intersection(value):
                return True
            return any(contains_forbidden(item) for item in value.values())
        if isinstance(value, list):
            return any(contains_forbidden(item) for item in value)
        return False

    if contains_forbidden(payload):
        return "ZHONGSHU_TASK_IMPLEMENTATION_DETAIL_FORBIDDEN"
    if action == "TASK_PROPOSALS_READY":
        return "ZHONGSHU_ANALYST_TASK_PROPOSALS_FORBIDDEN"
    if action == "READY_FOR_SOLVER":
        reason = _validate_analyst_plan(payload)
        if reason:
            return reason
        plan = payload.get("plan")
        if not isinstance(plan, dict) or plan.get("task_graph_version") != 1:
            return ""
        items = plan.get("candidate_items")
        requirements = plan.get("requirements")
        if not isinstance(items, list) or not isinstance(requirements, list):
            return "ZHONGSHU_TASK_GRAPH_FIELDS_MISSING"
        requirement_ids = {
            str(item.get("requirement_id"))
            for item in requirements
            if isinstance(item, dict) and item.get("requirement_id")
        }
        item_ids = {
            str(item.get("item_id"))
            for item in items
            if isinstance(item, dict) and item.get("item_id")
        }
        covered_ids: set[str] = set()
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                return f"ZHONGSHU_TASK_GRAPH_ITEM_INVALID:{index}"
            source_ids = item.get("source_requirement_ids")
            if not isinstance(source_ids, list):
                return f"ZHONGSHU_TASK_GRAPH_PROVENANCE_INVALID:{index}"
            covered_ids.update(str(value) for value in source_ids)
            if not isinstance(item.get("acceptance_signals"), list) or not item.get("acceptance_signals"):
                return f"ZHONGSHU_TASK_GRAPH_ACCEPTANCE_MISSING:{index}"
            dependencies = item.get("dependencies")
            if not isinstance(dependencies, list):
                return f"ZHONGSHU_TASK_GRAPH_DEPENDENCIES_INVALID:{index}"
            unknown = sorted(str(value) for value in dependencies if str(value) not in item_ids)
            if unknown:
                return f"ZHONGSHU_TASK_GRAPH_UNKNOWN_DEPENDENCY:{index}:{','.join(unknown)}"
        unknown_requirements = sorted(covered_ids - requirement_ids)
        if unknown_requirements:
            return "ZHONGSHU_TASK_GRAPH_UNKNOWN_REQUIREMENT:" + ",".join(unknown_requirements)
        must_ids = {
            str(item.get("requirement_id"))
            for item in requirements
            if isinstance(item, dict)
            and item.get("requirement_id")
            and str(item.get("priority") or "").lower() == "must"
            and requirement_requires_task(item)
        }
        missing = sorted(must_ids - covered_ids)
        if missing:
            raw_unknowns = payload.get("unknown_requirement_ids") or payload.get(
                "unknown_requirements"
            )
            values = raw_unknowns if isinstance(raw_unknowns, list) else [raw_unknowns]
            explicit_unknowns = {
                str(
                    item.get("requirement_id")
                    or item.get("source_requirement_id")
                    or item.get("id")
                    if isinstance(item, dict)
                    else item
                ).strip()
                for item in values
                if item is not None and str(item).strip()
            }
            unresolved = sorted(set(missing) - explicit_unknowns)
            if unresolved:
                return "ZHONGSHU_TASK_GRAPH_REQUIREMENT_COVERAGE_MISSING:" + ",".join(unresolved)
        return ""
    return ""


def _validate_state_payload(
    state: str,
    payload: dict[str, Any],
    ctx: StateContext | None = None,
) -> str:
    if state == "ZHONGSHU_ANALYST":
        if payload.get("action") == "EVIDENCE_SUPPLEMENT_READY":
            if not isinstance(payload.get("evidence_updates"), list):
                return "ZHONGSHU_EVIDENCE_UPDATES_MISSING"
            return validate_zhongshu_evidence_packet(
                payload,
                max_evidence_updates=ANALYST_MAX_EVIDENCE_UPDATES_MERGED,
                max_evidence_requests=ANALYST_MAX_EVIDENCE_REQUESTS,
                require_decision_relevance=True,
            )
        if payload.get("action") == "EVIDENCE_PACKET_READY":
            return validate_zhongshu_evidence_packet(
                payload,
                canonical_requirements=(
                    ctx.request_payload.get("zhongshu_requirement_contract")
                    if ctx is not None
                    else None
                ),
                max_evidence_updates=ANALYST_MAX_EVIDENCE_UPDATES_MERGED,
                max_evidence_requests=ANALYST_MAX_EVIDENCE_REQUESTS,
                require_decision_relevance=True,
            )
        if payload.get("action") == "TASK_PROPOSALS_READY":
            return "ZHONGSHU_ANALYST_TASK_PROPOSALS_FORBIDDEN"
        return _validate_analyst_plan(payload)
    if state == "ZHONGSHU_SOLVER":
        analyst_plan = (
            ctx.request_payload.get("analyst_plan")
            if ctx is not None
            else None
        )
        critic_review = (
            ctx.request_payload.get("zhongshu_critic_review")
            if ctx is not None
            else None
        )
        allow_additional_requirements = (
            isinstance(critic_review, dict)
            and critic_review.get("action") in {
                "REQUEST_SOLVER_REVISION",
                "REQUEST_REGROUP",
            }
        )
        normalization_error = str(payload.get("_solver_normalization_error") or "")
        if normalization_error:
            return normalization_error
        evidence_request_error = _validate_solver_evidence_requests(
            payload,
            analyst_plan,
            ctx.request_payload.get("candidate_plan") if ctx is not None else None,
        )
        if evidence_request_error:
            return evidence_request_error
        revision_requirements = _solver_revision_requirements(critic_review)
        finding_resolution_error = (
            _solver_revision_resolutions_error(payload, revision_requirements)
            if revision_requirements
            and payload.get("action") not in {"HUMAN_GATE", "BLOCKED"}
            else ""
        )
        if finding_resolution_error:
            return finding_resolution_error
        return _validate_solver_plan(
            payload,
            analyst_plan,
            allow_additional_requirements=allow_additional_requirements,
        )
    if state == "ZHONGSHU_CRITIC":
        return _validate_zhongshu_critic_reply(payload, ctx)
    if state == "MENXIA_ITEM_SOLVER":
        return _validate_menxia_solver_reply(payload, ctx)
    if state == "MENXIA_ITEM_ANALYST":
        return _validate_menxia_analyst_reply(payload)
    if state == "MENXIA_ITEM_CRITIC":
        return _validate_menxia_critic_reply(payload)
    return ""


def _validate_zhongshu_critic_reply(
    payload: dict[str, Any],
    ctx: StateContext | None,
) -> str:
    action = payload.get("action")
    if action in {"HUMAN_GATE", "BLOCKED"} and "findings" not in payload:
        return ""
    finding_error = _validate_critic_findings(payload)
    if finding_error:
        return "ZHONGSHU_CRITIC_" + finding_error
    if ctx is None:
        return ""
    plan = ctx.request_payload.get("candidate_plan")
    if not isinstance(plan, dict):
        return "ZHONGSHU_CRITIC_PLAN_MISSING"
    actual_hash = str(
        payload.get("reviewed_plan_hash")
        or payload.get("plan_hash")
        or ""
    )
    if not actual_hash:
        return "ZHONGSHU_CRITIC_PLAN_HASH_MISSING"
    expected_hash = canonical_plan_hash(plan)
    if actual_hash != expected_hash:
        return "ZHONGSHU_CRITIC_PLAN_HASH_MISMATCH"
    # Omission is handled by the durable aggregate ledger, not per-worker rejection.
    if payload.get("plan_hash") and payload["plan_hash"] != expected_hash:
        return "ZHONGSHU_CRITIC_PLAN_HASH_MISMATCH"
    return ""


def validate_zhongshu_task_critic_reply(
    payload: dict[str, Any],
    job: ReviewJob,
) -> str:
    """Validate one task-scoped Critic result before it reaches fan-in."""
    action = str(payload.get("action") or "")
    allowed = {
        "TASK_APPROVED",
        "TASK_CHANGES_REQUIRED",
        "REQUEST_ANALYST_EVIDENCE",
        "HUMAN_GATE",
        "BLOCKED",
    }
    if action not in allowed:
        return f"ZHONGSHU_TASK_CRITIC_ACTION_INVALID:{action}"
    reported_job_id = str(payload.get("review_job_id") or "")
    if reported_job_id and reported_job_id != job.review_job_id:
        return "ZHONGSHU_TASK_CRITIC_JOB_ID_MISMATCH"
    if str(payload.get("revision_id") or "") != job.revision_id:
        return "ZHONGSHU_TASK_CRITIC_REVISION_MISMATCH"
    if str(payload.get("group_id") or "") != job.group_id:
        return "ZHONGSHU_TASK_CRITIC_GROUP_ID_MISMATCH"
    if str(payload.get("item_id") or "") != job.item_id:
        return "ZHONGSHU_TASK_CRITIC_ITEM_ID_MISMATCH"
    if str(payload.get("reviewed_task_hash") or "") != job.task_hash:
        return "ZHONGSHU_TASK_CRITIC_TASK_HASH_MISMATCH"
    if str(payload.get("reviewed_dependency_hash") or "") != job.dependency_hash:
        return "ZHONGSHU_TASK_CRITIC_DEPENDENCY_HASH_MISMATCH"
    checks = payload.get("review_checks")
    required_checks = {
        "requirement_coverage", "boundary", "dependencies", "acceptance", "risks",
    }
    if not isinstance(checks, dict) or not required_checks.issubset(checks):
        return "ZHONGSHU_TASK_CRITIC_REVIEW_CHECKS_MISSING"
    findings = payload.get("findings")
    if not isinstance(findings, list):
        return "ZHONGSHU_TASK_CRITIC_FINDINGS_MISSING"
    active_blockers = 0
    actionable = 0
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            return f"ZHONGSHU_TASK_CRITIC_FINDING_INVALID:{index}"
        if not str(finding.get("finding_id") or "").strip():
            return f"ZHONGSHU_TASK_CRITIC_FINDING_ID_MISSING:{index}"
        if str(finding.get("group_id") or "") != job.group_id:
            return f"ZHONGSHU_TASK_CRITIC_FINDING_GROUP_MISMATCH:{index}"
        if str(finding.get("item_id") or "") != job.item_id:
            return f"ZHONGSHU_TASK_CRITIC_FINDING_ITEM_MISMATCH:{index}"
        if str(finding.get("scope") or "item") not in {"item", "dependency"}:
            return f"ZHONGSHU_TASK_CRITIC_FINDING_SCOPE_INVALID:{index}"
        severity = str(finding.get("severity") or "P2").upper()
        if severity not in {"P0", "P1", "P2", "P3"}:
            return f"ZHONGSHU_TASK_CRITIC_FINDING_SEVERITY_INVALID:{index}"
        status = normalize_finding_status(
            finding.get("status"), finding.get("decision")
        )
        if status not in {"RESOLVED", "WONT_FIX", "DEFERRED"}:
            actionable += 1
            if severity in {"P0", "P1"}:
                active_blockers += 1
    if action == "TASK_APPROVED" and active_blockers:
        return "ZHONGSHU_TASK_CRITIC_APPROVAL_HAS_ACTIVE_BLOCKER"
    if action == "TASK_CHANGES_REQUIRED" and not actionable:
        return "ZHONGSHU_TASK_CRITIC_CHANGES_WITHOUT_ACTIONABLE_FINDING"
    return ""


def _validate_menxia_solver_reply(
    payload: dict[str, Any],
    ctx: StateContext | None,
) -> str:
    if ctx is None or payload.get("action") not in {"FEASIBLE", "READY_FOR_CRITIC"}:
        return ""
    if not isinstance(payload.get("implementation_proposal"), dict):
        return "MENXIA_SOLVER_PROPOSAL_MISSING"
    item_id = str(ctx.active_item_id or "")
    review = _current_item_payload(ctx.request_payload, "item_critic_reviews", item_id) or {}
    all_finding_ids = set(_critic_finding_ids(review))
    active_finding_ids = {
        str(finding.get("finding_id") or finding.get("id") or "")
        for finding in review.get("findings", [])
        if isinstance(finding, dict)
        and str(finding.get("finding_id") or finding.get("id") or "")
        and str(finding.get("status") or "OPEN").upper()
        not in {"RESOLVED", "WONT_FIX", "DEFERRED"}
    }
    if not active_finding_ids:
        return ""
    if "responses_to_critic" not in payload:
        return "SOLVER_CRITIC_RESPONSES_MISSING"
    response_ids = set(_solver_response_finding_ids(payload))
    unknown = response_ids - all_finding_ids
    if unknown:
        return "SOLVER_CRITIC_RESPONSE_UNKNOWN:" + ",".join(sorted(unknown))
    missing = active_finding_ids - response_ids
    if missing:
        return "SOLVER_CRITIC_RESPONSES_INCOMPLETE:" + ",".join(sorted(missing))
    signature = _semantic_reply_signature(payload)
    if signature == ctx.last_reply_fingerprint and ctx.no_progress_count >= ctx.max_no_progress:
        return "SOLVER_NO_PROGRESS"
    return ""


def _validate_menxia_analyst_reply(payload: dict[str, Any]) -> str:
    action = payload.get("action")
    if action in {"HUMAN_GATE", "BLOCKED"}:
        return ""
    if action not in {
        "EVIDENCE_SUFFICIENT",
        "NEEDS_MORE_EVIDENCE",
        "REQUEST_SOLVER_REVISION",
    }:
        return ""
    evidence_fields = {
        "assessment",
        "requirement_trace",
        "confirmed_facts",
        "evidence",
        "current_behavior",
        "existing_capabilities",
        "missing_evidence",
        "conflicts",
        "unknowns",
    }
    if not any(field in payload for field in evidence_fields):
        return "MENXIA_ANALYST_EVIDENCE_MISSING"
    return ""


def _validate_menxia_critic_reply(payload: dict[str, Any]) -> str:
    action = payload.get("action")
    if action in {"HUMAN_GATE", "BLOCKED"}:
        return ""
    if action not in {
        "APPROVE_ITEM",
        "REVISE_ITEM",
        "SPLIT_ITEM",
        "MERGE_ITEM",
        "REMOVE_ITEM",
        "REQUEST_SOLVER_REVISION",
        "APPROVE_GROUP",
        "APPROVE_FREEZE",
        "REQUEST_GROUP_REVISION",
        "REVISE_GROUP",
    }:
        return ""
    finding_error = _validate_critic_findings(payload)
    return "MENXIA_CRITIC_" + finding_error if finding_error else ""


def _validate_critic_findings(payload: dict[str, Any]) -> str:
    """Validate the durable finding identity and severity contract."""
    findings = payload.get("findings")
    if not isinstance(findings, list):
        return "FINDINGS_MISSING"
    seen_ids: set[str] = set()
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            return f"FINDING_INVALID:{index}"
        finding_id = str(finding.get("finding_id") or finding.get("id") or "").strip()
        if not finding_id:
            return f"FINDING_ID_MISSING:{index}"
        if finding_id in seen_ids:
            return f"FINDING_ID_DUPLICATE:{finding_id}"
        seen_ids.add(finding_id)
        severity = str(finding.get("severity") or "").strip().upper()
        if severity not in {"P0", "P1", "P2", "P3"}:
            return f"FINDING_SEVERITY_INVALID:{finding_id}"
        if not str(finding.get("claim") or finding.get("description") or "").strip():
            return f"FINDING_CLAIM_MISSING:{finding_id}"
        if not str(finding.get("decision") or finding.get("status") or "").strip():
            return f"FINDING_DECISION_MISSING:{finding_id}"
        if "confidence" in finding:
            confidence = finding.get("confidence")
            if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
                return f"FINDING_CONFIDENCE_INVALID:{finding_id}"
            if not 0 <= float(confidence) <= 1:
                return f"FINDING_CONFIDENCE_INVALID:{finding_id}"
    return ""


def _semantic_reply_signature(payload: dict[str, Any]) -> str:
    volatile = {
        "task_id",
        "request_id",
        "result_source",
        "result_path",
        "result_sha256",
        "result_utf8_bytes",
        "result_has_bom",
    }
    stable = {key: value for key, value in payload.items() if key not in volatile}
    return hashlib.sha256(
        json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _first_dependency_error(items: list[dict[str, Any]]) -> str:
    item_ids = {str(item.get("item_id") or "") for item in items}
    dependencies = {
        str(item["item_id"]): list(item.get("dependencies", []))
        for item in items
    }
    for item_id in sorted(dependencies):
        for reference in dependencies[item_id]:
            if reference not in item_ids:
                return f"SOLVER_DEPENDENCY_UNKNOWN:{reference}"
    visiting: dict[str, int] = {}
    visited: set[str] = set()
    stack: list[str] = []

    def visit(item_id: str) -> list[str] | None:
        if item_id in visiting:
            return [*stack[visiting[item_id] :], item_id]
        if item_id in visited:
            return None
        visiting[item_id] = len(stack)
        stack.append(item_id)
        for dependency in dependencies[item_id]:
            cycle = visit(dependency)
            if cycle:
                return cycle
        stack.pop()
        visiting.pop(item_id, None)
        visited.add(item_id)
        return None

    for item_id in sorted(item_ids):
        cycle = visit(item_id)
        if cycle:
            return f"SOLVER_DEPENDENCY_CYCLE:{cycle[0]}:cycle={'->'.join(cycle)}"
    return ""


def _validate_solver_evidence_requests(
    payload: dict[str, Any],
    analyst_plan: dict[str, Any] | None,
    current_plan: dict[str, Any] | None = None,
) -> str:
    """Require every Solver-to-Analyst request to have a deterministic route."""
    action = str(payload.get("action") or "")
    if action not in {"REQUEST_ANALYST_EVIDENCE", "NEEDS_MORE_EVIDENCE"}:
        return ""
    requests = payload.get("evidence_requests")
    if not isinstance(requests, list) or not requests:
        return "SOLVER_EVIDENCE_REQUESTS_MISSING"
    if len(requests) > 6:
        return "SOLVER_EVIDENCE_REQUESTS_TOO_MANY"
    known_items: set[str] = set()
    known_requirements: set[str] = set()
    scope_source = current_plan if isinstance(current_plan, dict) else analyst_plan
    if isinstance(scope_source, dict):
        known_items = {
            str(item.get("item_id"))
            for item in scope_source.get("items") or scope_source.get("formal_items") or []
            if isinstance(item, dict) and item.get("item_id")
        }
    if isinstance(analyst_plan, dict):
        known_requirements = {
            str(item.get("requirement_id"))
            for item in analyst_plan.get("requirements") or []
            if isinstance(item, dict) and item.get("requirement_id")
        }
    if isinstance(current_plan, dict):
        known_requirements.update(
            str(item.get("requirement_id"))
            for item in current_plan.get("requirements") or []
            if isinstance(item, dict) and item.get("requirement_id")
        )
    seen: set[tuple[str, str, str]] = set()
    for index, request in enumerate(requests):
        if not isinstance(request, dict):
            return f"SOLVER_EVIDENCE_REQUEST_INVALID:{index}"
        item_id = str(request.get("item_id") or "").strip()
        requirement_id = str(request.get("requirement_id") or "").strip()
        if not item_id and not requirement_id:
            return f"SOLVER_EVIDENCE_REQUEST_SCOPE_MISSING:{index}"
        if item_id and not known_items:
            return "SOLVER_EVIDENCE_REQUEST_ITEM_SCOPE_UNAVAILABLE"
        if item_id and item_id not in known_items:
            return f"SOLVER_EVIDENCE_REQUEST_ITEM_UNKNOWN:{item_id}"
        if requirement_id and not known_requirements:
            return "SOLVER_EVIDENCE_REQUEST_REQUIREMENT_SCOPE_UNAVAILABLE"
        if requirement_id and requirement_id not in known_requirements:
            return f"SOLVER_EVIDENCE_REQUEST_REQUIREMENT_UNKNOWN:{requirement_id}"
        if not str(request.get("question") or "").strip():
            return f"SOLVER_EVIDENCE_REQUEST_QUESTION_MISSING:{index}"
        if not str(request.get("reason") or "").strip():
            return f"SOLVER_EVIDENCE_REQUEST_REASON_MISSING:{index}"
        identity = (item_id, requirement_id, str(request.get("finding_id") or "").strip())
        if identity in seen:
            return f"SOLVER_EVIDENCE_REQUEST_DUPLICATE:{index}"
        seen.add(identity)
    return ""


def _validate_solver_plan(
    payload: dict[str, Any],
    analyst_plan: dict[str, Any] | None = None,
    *,
    allow_additional_requirements: bool = False,
) -> str:
    if payload.get("action") != "READY_FOR_CRITIC":
        return ""
    plan = payload.get("plan")
    if not isinstance(plan, dict):
        return "SOLVER_PLAN_MISSING"
    def contains_forbidden(value: Any) -> bool:
        if isinstance(value, dict):
            if ZHONGSHU_SOLVER_FORBIDDEN_FIELDS.intersection(value):
                return True
            return any(contains_forbidden(item) for item in value.values())
        if isinstance(value, list):
            return any(contains_forbidden(item) for item in value)
        return False

    if contains_forbidden(payload):
        return "SOLVER_IMPLEMENTATION_DETAIL_FORBIDDEN"
    requirements = plan.get("requirements")
    if not isinstance(requirements, list) or not requirements:
        return "SOLVER_REQUIREMENTS_MISSING"
    requirement_ids = [
        str(item.get("requirement_id") or "")
        for item in requirements
        if isinstance(item, dict)
    ]
    if len(requirement_ids) != len(requirements) or any(
        not requirement_id for requirement_id in requirement_ids
    ):
        return "SOLVER_REQUIREMENTS_INVALID"
    if len(set(requirement_ids)) != len(requirement_ids):
        return "SOLVER_REQUIREMENTS_DUPLICATE"
    if isinstance(analyst_plan, dict):
        analyst_requirements = analyst_plan.get("requirements")
        if isinstance(analyst_requirements, list):
            expected_ids = {
                str(item.get("requirement_id") or "")
                for item in analyst_requirements
                if isinstance(item, dict) and item.get("requirement_id")
            }
            actual_ids = set(requirement_ids)
            if not expected_ids.issubset(actual_ids):
                return "SOLVER_REQUIREMENTS_INCOMPLETE"
            if actual_ids != expected_ids:
                return "SOLVER_REQUIREMENTS_INCOMPLETE"
            actual_by_id = {
                str(item.get("requirement_id")): item
                for item in requirements
                if isinstance(item, dict)
            }
            for expected in analyst_requirements:
                if not isinstance(expected, dict) or not expected.get("requirement_id"):
                    continue
                requirement_id = str(expected["requirement_id"])
                actual = actual_by_id[requirement_id]
                for field in ("statement", "priority", "scope", "kind", "source", "acceptance_signal"):
                    if field in expected and actual.get(field) != expected.get(field):
                        return f"SOLVER_REQUIREMENT_CONTENT_MISMATCH:{requirement_id}"
    for index, requirement in enumerate(requirements):
        if not isinstance(requirement.get("statement"), str) or not requirement["statement"].strip():
            return f"SOLVER_REQUIREMENT_INVALID:{index}:statement"
    formal_items = plan.get("items")
    if not isinstance(formal_items, list) or not formal_items:
        return "SOLVER_ITEMS_MISSING"
    formal_item_ids = [
        str(item.get("item_id") or "")
        for item in formal_items
        if isinstance(item, dict)
    ]
    if len(formal_item_ids) != len(formal_items) or any(
        not item_id for item_id in formal_item_ids
    ):
        return "SOLVER_ITEMS_INVALID"
    if len(set(formal_item_ids)) != len(formal_item_ids):
        return "SOLVER_ITEMS_DUPLICATE"
    if isinstance(analyst_plan, dict):
        candidate_items = analyst_plan.get("candidate_items")
        if isinstance(candidate_items, list):
            candidate_item_ids_list = [
                str(item.get("item_id") or "")
                for item in candidate_items
                if isinstance(item, dict)
            ]
            if (
                len(candidate_item_ids_list) != len(candidate_items)
                or any(not item_id for item_id in candidate_item_ids_list)
            ):
                return "SOLVER_ANALYST_CANDIDATE_ITEMS_INVALID"
            if len(set(candidate_item_ids_list)) != len(candidate_item_ids_list):
                return "SOLVER_ANALYST_CANDIDATE_ITEMS_DUPLICATE"
    known_requirement_ids = set(requirement_ids)
    for index, item in enumerate(formal_items):
        if not isinstance(item, dict):
            return f"SOLVER_ITEM_INVALID:{index}"
        for field in ZHONGSHU_SOLVER_REQUIRED_ITEM_FIELDS:
            if field not in item:
                return f"SOLVER_ITEM_FIELD_MISSING:{index}:{field}"
        for field in ("title", "objective"):
            if not isinstance(item.get(field), str) or not item[field].strip():
                return f"SOLVER_ITEM_FIELD_INVALID:{index}:{field}"
        if not isinstance(item["source_requirement_ids"], list) or not item["source_requirement_ids"]:
            return f"SOLVER_ITEM_SOURCE_REQUIREMENTS_MISSING:{index}"
        for requirement_id in item["source_requirement_ids"]:
            if not isinstance(requirement_id, str) or requirement_id not in known_requirement_ids:
                return f"SOLVER_ITEM_REQUIREMENT_UNKNOWN:{requirement_id}"
        if not isinstance(item["dependencies"], list):
            return f"SOLVER_ITEM_DEPENDENCIES_INVALID:{index}"
        if not all(isinstance(value, str) and value.strip() for value in item["dependencies"]):
            return f"SOLVER_ITEM_DEPENDENCIES_INVALID:{index}"
        if not isinstance(item["acceptance_signals"], list) or not item["acceptance_signals"]:
            return f"SOLVER_ITEM_ACCEPTANCE_MISSING:{index}"
        if not all(isinstance(value, str) and value.strip() for value in item["acceptance_signals"]):
            return f"SOLVER_ITEM_ACCEPTANCE_INVALID:{index}"
        if not isinstance(item["unknowns"], list) or not isinstance(item["risks"], list):
            return f"SOLVER_ITEM_RISK_FIELDS_INVALID:{index}"
        if not isinstance(item["parallelizable"], bool):
            return f"SOLVER_ITEM_PARALLELIZABLE_INVALID:{index}"
    covered = {value for item in formal_items for value in item["source_requirement_ids"]}
    must_ids = {str(item["requirement_id"]) for item in requirements if str(item.get("priority") or "").lower() == "must" and requirement_requires_task(item)}
    if must_ids - covered:
        return "SOLVER_REQUIREMENT_COVERAGE_MISSING:" + ",".join(sorted(must_ids - covered))
    if isinstance(analyst_plan, dict):
        from .zhongshu_tasks import task_provenance_error
        provenance_error = task_provenance_error(plan, analyst_plan)
        if provenance_error:
            return provenance_error
    dependency_error = _first_dependency_error(formal_items)
    if dependency_error:
        return dependency_error
    groups = plan.get("groups")
    if not isinstance(groups, list) or not groups:
        return "SOLVER_GROUPS_MISSING"
    nested_item_ids: list[str] = []
    group_ids: set[str] = set()
    item_by_id = {str(item["item_id"]): item for item in formal_items}
    for index, group in enumerate(groups):
        if not isinstance(group, dict):
            return f"SOLVER_GROUP_INVALID:{index}"
        group_id = str(group.get("group_id") or "")
        if not group_id:
            return f"SOLVER_GROUP_INVALID:{index}"
        if group_id in group_ids:
            return f"SOLVER_GROUP_DUPLICATE:{group_id}"
        group_ids.add(group_id)
        items = group.get("items")
        if not isinstance(items, list) or not items:
            return f"SOLVER_GROUP_ITEMS_MISSING:{index}"
        for item_index, item in enumerate(items):
            if not isinstance(item, dict) or not item.get("item_id"):
                return f"SOLVER_GROUP_ITEM_INVALID:{index}:{item_index}"
            item_id = str(item["item_id"])
            nested_item_ids.append(item_id)
            if item_id not in item_by_id:
                continue
            if item != item_by_id[item_id]:
                return f"SOLVER_GROUP_ITEM_MISMATCH:{item_id}"
    if len(set(nested_item_ids)) != len(nested_item_ids):
        return "SOLVER_GROUP_ITEMS_DUPLICATE"
    if set(nested_item_ids) != set(formal_item_ids):
        return "SOLVER_ITEMS_INDEX_MISMATCH"
    return ""


def _solver_plan_snapshot(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    plan = value.get("plan") if isinstance(value.get("plan"), dict) else value
    if not isinstance(plan.get("items"), list) or not isinstance(plan.get("groups"), list):
        return None
    return copy.deepcopy(plan)


def _solver_revision_resolutions_error(
    payload: dict[str, Any],
    revision_requirements: list[dict[str, Any]],
) -> str:
    """Validate a complete, explicit Solver finding batch.

    A revision is allowed to make bounded progress.  ``finding_resolutions``
    therefore covers only the selected batch, while ``finding_batch`` must
    account for the exact remainder.  This preserves the old safety property
    (no silent omission) without forcing one LLM response to resolve every
    finding in one round.
    """
    if not revision_requirements:
        return ""
    resolutions = payload.get("finding_resolutions")
    if not isinstance(resolutions, list):
        return "SOLVER_FINDING_RESOLUTIONS_MISSING"
    required_ids = {
        str(item.get("finding_id"))
        for item in revision_requirements
        if item.get("finding_id")
    }
    seen_ids: set[str] = set()
    for index, resolution in enumerate(resolutions):
        if not isinstance(resolution, dict) or not resolution.get("finding_id"):
            return f"SOLVER_FINDING_RESOLUTION_INVALID:{index}"
        finding_id = str(resolution["finding_id"])
        if finding_id in seen_ids:
            return f"SOLVER_FINDING_RESOLUTION_DUPLICATE:{finding_id}"
        seen_ids.add(finding_id)
    unknown = sorted(seen_ids - required_ids)
    if unknown:
        return "SOLVER_FINDING_RESOLUTION_UNKNOWN:" + ",".join(unknown)

    batch = payload.get("finding_batch")
    if batch is None:
        # Compatibility is safe only for the old complete form.  A partial
        # response must use the new envelope so omission can never be treated
        # as progress.
        missing = sorted(required_ids - seen_ids)
        if missing:
            return "SOLVER_FINDING_BATCH_MISSING_FOR_PARTIAL:" + ",".join(missing)
        return ""
    if not isinstance(batch, dict):
        return "SOLVER_FINDING_BATCH_INVALID"

    def ids(field: str) -> tuple[list[str], str]:
        value = batch.get(field)
        if not isinstance(value, list):
            return [], f"SOLVER_FINDING_BATCH_{field.upper()}_INVALID"
        values = [str(item).strip() for item in value if str(item).strip()]
        if len(values) != len(value):
            return [], f"SOLVER_FINDING_BATCH_{field.upper()}_INVALID"
        if len(set(values)) != len(values):
            return [], f"SOLVER_FINDING_BATCH_{field.upper()}_DUPLICATE"
        return values, ""

    selected_ids, error = ids("selected_finding_ids")
    if error:
        return error
    remaining_ids, error = ids("remaining_finding_ids")
    if error:
        return error
    if len(selected_ids) > ZHONGSHU_SOLVER_MAX_FINDINGS_PER_ROUND:
        return (
            "SOLVER_FINDING_BATCH_TOO_LARGE:"
            f"selected={len(selected_ids)}:max={ZHONGSHU_SOLVER_MAX_FINDINGS_PER_ROUND}"
        )
    selected = set(selected_ids)
    remaining = set(remaining_ids)
    unknown_batch = sorted((selected | remaining) - required_ids)
    if unknown_batch:
        return "SOLVER_FINDING_BATCH_UNKNOWN:" + ",".join(unknown_batch)
    overlap = sorted(selected & remaining)
    if overlap:
        return "SOLVER_FINDING_BATCH_OVERLAP:" + ",".join(overlap)
    omitted = sorted(required_ids - selected - remaining)
    if omitted:
        return "SOLVER_FINDING_BATCH_COVERAGE_INCOMPLETE:" + ",".join(omitted)
    focus_ids = {
        str(item.get("finding_id") or "")
        for item in _solver_revision_prompt_context(revision_requirements)[0]
        if item.get("finding_id")
    }
    out_of_focus = sorted(selected - focus_ids)
    if out_of_focus:
        return "SOLVER_FINDING_BATCH_OUT_OF_FOCUS:" + ",".join(out_of_focus)
    resolution_mismatch = sorted(seen_ids ^ selected)
    if resolution_mismatch:
        return "SOLVER_FINDING_BATCH_RESOLUTION_MISMATCH:" + ",".join(resolution_mismatch)

    next_action = str(batch.get("next_action") or "").strip().upper()
    if next_action not in {
        "READY_FOR_CRITIC", "REQUEST_ANALYST_EVIDENCE", "NEEDS_MORE_EVIDENCE",
        "HUMAN_GATE", "BLOCKED",
    }:
        return "SOLVER_FINDING_BATCH_NEXT_ACTION_INVALID"
    progress = batch.get("progress")
    if not isinstance(progress, dict):
        return "SOLVER_FINDING_BATCH_PROGRESS_MISSING"
    for field in ("open_before", "resolved", "remaining", "open_after"):
        value = progress.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return f"SOLVER_FINDING_BATCH_PROGRESS_INVALID:{field}"
    resolved_count = sum(
        str(item.get("status") or "").strip().lower() == "resolved"
        for item in resolutions
        if isinstance(item, dict)
    )
    pending_count = len(remaining_ids) + sum(
        str(item.get("status") or "").strip().lower() != "resolved"
        for item in resolutions
        if isinstance(item, dict)
    )
    if progress["open_before"] != len(required_ids):
        return "SOLVER_FINDING_BATCH_PROGRESS_MISMATCH:open_before"
    if progress["resolved"] != resolved_count:
        return "SOLVER_FINDING_BATCH_PROGRESS_MISMATCH:resolved"
    if progress["remaining"] != len(remaining_ids):
        return "SOLVER_FINDING_BATCH_PROGRESS_MISMATCH:remaining"
    if progress["open_after"] != pending_count:
        return "SOLVER_FINDING_BATCH_PROGRESS_MISMATCH:open_after"
    return ""


def _solver_unresolved_finding_ids(
    payload: dict[str, Any],
    revision_requirements: list[dict[str, Any]],
) -> list[str]:
    """Return required findings that Solver explicitly did not resolve."""
    resolutions = payload.get("finding_resolutions")
    if not isinstance(resolutions, list):
        resolutions = []
    by_id = {
        str(item.get("finding_id")): item
        for item in resolutions
        if isinstance(item, dict) and item.get("finding_id")
    }
    batch = payload.get("finding_batch")
    explicit_remaining = {
        str(item).strip()
        for item in (batch.get("remaining_finding_ids") if isinstance(batch, dict) else [])
        if str(item).strip()
    }
    return [
        str(requirement["finding_id"])
        for requirement in revision_requirements
        if (
            str(requirement.get("finding_id") or "")
            and (
                str(requirement["finding_id"]) in explicit_remaining
                or str(
                    by_id.get(str(requirement["finding_id"]), {}).get("status") or ""
                ).strip().lower() != "resolved"
            )
        )
    ]


def _solver_pending_resolution_entries(
    payload: dict[str, Any],
    revision_requirements: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Materialize explicit remainder entries for ownership-based routing."""
    pending_ids = set(_solver_unresolved_finding_ids(payload, revision_requirements))
    responses = payload.get("finding_resolutions")
    by_id = {
        str(item.get("finding_id")): item
        for item in responses or []
        if isinstance(item, dict) and item.get("finding_id")
    }
    pending: list[dict[str, Any]] = []
    for requirement in revision_requirements:
        finding_id = str(requirement.get("finding_id") or "")
        if not finding_id or finding_id not in pending_ids:
            continue
        entry = copy.deepcopy(by_id.get(finding_id) or requirement)
        entry["finding_id"] = finding_id
        owner = str(
            entry.get("owner_role")
            or entry.get("owner")
            or requirement.get("owner_role")
            or requirement.get("owner")
            or ""
        ).strip().lower()
        next_action = str(
            entry.get("next_action")
            or requirement.get("next_action")
            or ""
        ).strip().upper()
        if not owner and next_action in {"REQUEST_SOLVER_REVISION", "REQUEST_REGROUP"}:
            owner = "review-solver"
        if not owner and next_action in {"REQUEST_ANALYST_EVIDENCE", "NEEDS_MORE_EVIDENCE"}:
            owner = "review-analyst"
        entry["owner_role"] = owner or "human"
        entry["next_action"] = next_action or "HUMAN_GATE"
        pending.append(entry)
    return pending


def _apply_solver_changes(
    plan: dict[str, Any],
    changes: Any,
) -> str:
    """Apply a small, typed Solver patch to the orchestrator-owned plan."""
    if not isinstance(changes, list):
        return "SOLVER_CHANGES_INVALID"
    item_list = plan.get("items")
    group_list = plan.get("groups")
    if not isinstance(item_list, list) or not isinstance(group_list, list):
        return "SOLVER_CURRENT_PLAN_INVALID"
    items = {
        str(item.get("item_id")): item
        for item in item_list
        if isinstance(item, dict) and item.get("item_id")
    }
    groups = {
        str(group.get("group_id")): group
        for group in group_list
        if isinstance(group, dict) and group.get("group_id")
    }
    item_fields = (set(ZHONGSHU_SOLVER_REQUIRED_ITEM_FIELDS) | {"source_candidate_ids", "basis_evidence", "evidence_updates"}) - {"item_id"}
    group_fields = {"title", "objective"}
    plan_fields = {"dependencies", "scope", "unknowns", "risks", "unknown_resolutions"}

    def sync_item(item_id: str) -> None:
        for group in group_list:
            if not isinstance(group, dict) or not isinstance(group.get("items"), list):
                continue
            group["items"] = [
                copy.deepcopy(items[item_id]) if isinstance(entry, dict) and str(entry.get("item_id")) == item_id else entry
                for entry in group["items"]
            ]

    for index, change in enumerate(changes):
        if not isinstance(change, dict):
            return f"SOLVER_CHANGE_INVALID:{index}"
        op = str(change.get("op") or "")
        if op == "replace_item_fields":
            item_id = str(change.get("item_id") or "")
            fields = change.get("fields")
            if item_id not in items:
                return f"SOLVER_CHANGE_ITEM_UNKNOWN:{item_id}"
            if not isinstance(fields, dict) or not fields:
                return f"SOLVER_CHANGE_FIELDS_INVALID:{index}"
            forbidden = set(fields) - item_fields
            if forbidden:
                return "SOLVER_CHANGE_ITEM_FIELD_FORBIDDEN:" + ",".join(sorted(forbidden))
            items[item_id].update(copy.deepcopy(fields))
            sync_item(item_id)
        elif op == "replace_group_items":
            group_id = str(change.get("group_id") or "")
            item_ids = change.get("item_ids")
            if group_id not in groups:
                return f"SOLVER_CHANGE_GROUP_UNKNOWN:{group_id}"
            if not isinstance(item_ids, list) or not item_ids:
                return f"SOLVER_CHANGE_GROUP_ITEMS_INVALID:{index}"
            normalized_ids = [str(item_id) for item_id in item_ids]
            if len(set(normalized_ids)) != len(normalized_ids):
                return f"SOLVER_CHANGE_GROUP_ITEMS_DUPLICATE:{group_id}"
            if any(item_id not in items for item_id in normalized_ids):
                unknown = sorted(item_id for item_id in normalized_ids if item_id not in items)
                return "SOLVER_CHANGE_ITEM_UNKNOWN:" + ",".join(unknown)
            groups[group_id]["items"] = [copy.deepcopy(items[item_id]) for item_id in normalized_ids]
        elif op == "replace_group_fields":
            group_id = str(change.get("group_id") or "")
            fields = change.get("fields")
            if group_id not in groups:
                return f"SOLVER_CHANGE_GROUP_UNKNOWN:{group_id}"
            if not isinstance(fields, dict) or not fields:
                return f"SOLVER_CHANGE_FIELDS_INVALID:{index}"
            forbidden = set(fields) - group_fields
            if forbidden:
                return "SOLVER_CHANGE_GROUP_FIELD_FORBIDDEN:" + ",".join(sorted(forbidden))
            groups[group_id].update(copy.deepcopy(fields))
        elif op == "replace_plan_fields":
            fields = change.get("fields")
            if not isinstance(fields, dict) or not fields:
                return f"SOLVER_CHANGE_FIELDS_INVALID:{index}"
            forbidden = set(fields) - plan_fields
            if forbidden:
                return "SOLVER_CHANGE_PLAN_FIELD_FORBIDDEN:" + ",".join(sorted(forbidden))
            plan.update(copy.deepcopy(fields))
        else:
            return f"SOLVER_CHANGE_OP_UNSUPPORTED:{op or index}"
    return ""


def _normalize_solver_payload(
    payload: dict[str, Any],
    analyst_plan: dict[str, Any] | None = None,
    *,
    current_plan: dict[str, Any] | None = None,
    revision_requirements: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    '''Normalize deterministic Solver group references before strict validation.'''
    normalized = copy.deepcopy(payload)
    if normalized.get("action") not in {
        "READY_FOR_CRITIC", "REQUEST_ANALYST_EVIDENCE", "NEEDS_MORE_EVIDENCE",
    }:
        return normalized, []
    revision_requirements = revision_requirements or []
    current_snapshot = _solver_plan_snapshot(current_plan)
    if isinstance(normalized.get("plan"), dict) and "changes" in normalized:
        # A revision may carry the complete plan together with the protocol's
        # required empty no-op changes array.  Only a non-empty/invalid
        # changes value is ambiguous with a complete plan.
        if normalized.get("changes") != []:
            normalized["_solver_normalization_error"] = "SOLVER_PLAN_AND_CHANGES_AMBIGUOUS"
            return normalized, []
    if current_snapshot is not None:
        resolution_error = _solver_revision_resolutions_error(
            normalized,
            revision_requirements,
        )
        if resolution_error:
            normalized["_solver_normalization_error"] = resolution_error
            return normalized, []
        unresolved_finding_ids = _solver_unresolved_finding_ids(
            normalized,
            revision_requirements,
        )
        if unresolved_finding_ids:
            from .zhongshu_review import repair_route
            unresolved_responses = _solver_pending_resolution_entries(
                normalized,
                revision_requirements,
            )
            normalized["action"] = repair_route(unresolved_responses)
            normalized["analyst_finding_ids"] = unresolved_finding_ids
            normalized["pending_finding_ids"] = unresolved_finding_ids
            normalized["pending_solver_finding_ids"] = [
                finding_id
                for finding_id in unresolved_finding_ids
                if next(
                    (
                        str(item.get("owner_role") or item.get("owner") or "").lower()
                        for item in unresolved_responses
                        if str(item.get("finding_id") or "") == finding_id
                    ),
                    "review-solver",
                ) in {"solver", "review-solver"}
            ]
            normalized["pending_resolutions"] = copy.deepcopy(unresolved_responses)
            normalized["route_reason"] = "Unresolved findings routed by explicit ownership, not by unresolved status alone."
            if normalized["action"] == "HUMAN_GATE":
                normalized["human_gate"] = {"question": "请明确尚未解决问题的处理归属或所需决策。", "next_state": "ZHONGSHU_SOLVER"}
            notes = ["action<-explicit_repair_owner"]
        else:
            normalized["pending_finding_ids"] = []
            normalized["pending_solver_finding_ids"] = []
            notes = []
    else:
        notes = []
    if not isinstance(normalized.get("plan"), dict) and current_snapshot is not None:
        if "changes" in normalized:
            change_error = _apply_solver_changes(current_snapshot, normalized.get("changes"))
            if change_error:
                normalized["_solver_normalization_error"] = change_error
                return normalized, []
            normalized["plan"] = current_snapshot
            notes = ["plan<-current_plan+changes"]
        else:
            resolution_error = _solver_revision_resolutions_error(
                normalized,
                revision_requirements,
            )
            if resolution_error:
                normalized["_solver_normalization_error"] = resolution_error
                return normalized, []
            normalized["plan"] = current_snapshot
            notes.append("plan<-current_plan")
    plan = normalized.get("plan")
    if not isinstance(plan, dict):
        return normalized, []
    if (
        (not isinstance(plan.get("requirements"), list) or not plan.get("requirements"))
        and isinstance(analyst_plan, dict)
        and isinstance(analyst_plan.get("requirements"), list)
        and analyst_plan.get("requirements")
    ):
        plan["requirements"] = copy.deepcopy(analyst_plan["requirements"])
        notes.append("requirements<-analyst_plan")
    formal_items = plan.get("items")
    groups = plan.get("groups")
    if isinstance(analyst_plan, dict) and isinstance(plan.get("requirements"), list):
        original_requirements = {str(item.get("requirement_id")): item for item in analyst_plan.get("requirements", []) if isinstance(item, dict)}
        for requirement in plan["requirements"]:
            if isinstance(requirement, dict):
                source = original_requirements.get(str(requirement.get("requirement_id")), {})
                for field, value in source.items():
                    requirement.setdefault(field, copy.deepcopy(value))
    if not isinstance(formal_items, list) or not formal_items:
        derived_items: list[dict[str, Any]] = []
        seen_item_ids: set[str] = set()
        if isinstance(groups, list):
            for group in groups:
                if not isinstance(group, dict) or not isinstance(group.get("items"), list):
                    continue
                for item in group["items"]:
                    if not isinstance(item, dict) or not item.get("item_id"):
                        continue
                    item_id = str(item["item_id"])
                    if item_id not in seen_item_ids:
                        derived_items.append(copy.deepcopy(item))
                        seen_item_ids.add(item_id)
        if derived_items:
            plan["items"] = derived_items
            formal_items = derived_items
            notes.append("items<-groups.items")
        else:
            return normalized, notes
    item_by_id = {
        str(item.get("item_id")): item
        for item in formal_items
        if isinstance(item, dict) and item.get("item_id")
    }
    if not isinstance(groups, list):
        return normalized, notes
    if isinstance(analyst_plan, dict):
        from .zhongshu_tasks import preserve_task_context
        preserve_task_context(plan, analyst_plan)
        for group in groups:
            if isinstance(group, dict) and isinstance(group.get("items"), list):
                preserve_task_context({"items": group["items"]}, analyst_plan)
    for group_index, group in enumerate(groups):
        if not isinstance(group, dict):
            continue
        references = None
        source_name = ""
        if isinstance(group.get("item_ids"), list):
            references = group.get("item_ids")
            source_name = "item_ids"
        if references is None:
            continue
        resolved = [
            copy.deepcopy(item_by_id[str(item_id)])
            for item_id in references
            if str(item_id) in item_by_id
        ]
        if len(resolved) != len(references):
            continue
        group["items"] = resolved
        group.pop("item_ids", None)
        notes.append(f"groups[{group_index}].{source_name}->items")
    from .zhongshu_tasks import blocking_unknowns
    unresolved_blockers = blocking_unknowns(plan)
    if normalized.get("action") == "READY_FOR_CRITIC" and unresolved_blockers:
        # An unknown discovered while planning is review input, not a user
        # decision. Critic must inspect it first; only an explicit HUMAN_GATE
        # action, a human-owned finding, or a later circuit breaker may enter
        # the human gate. Rewriting READY_FOR_CRITIC here skips Critic and
        # creates a gate with no actionable question.
        normalized["blocking_unknowns"] = unresolved_blockers
        notes.append("blocking_unknowns_preserved_for_critic")
    return normalized, notes


def _compact_solver_prompt_value(value: Any, string_limit: int = 220) -> Any:
    if isinstance(value, str):
        return value if len(value) <= string_limit else value[: string_limit - 1] + "…"
    if isinstance(value, list):
        return [
            _compact_solver_prompt_value(item, string_limit)
            for item in value[:8]
        ]
    if isinstance(value, dict):
        return {
            str(key): _compact_solver_prompt_value(item, string_limit)
            for key, item in list(value.items())[:24]
        }
    return value


def _worker_evidence_index(plan: dict[str, Any]) -> dict[str, Any] | None:
    """Build a provenance-only index without replaying worker evidence bodies.

    ``worker_evidence`` remains in the persisted Analyst packet for audit and
    recovery.  Solver already receives the canonical, deduplicated
    ``evidence_updates`` list, so sending every worker's raw copy again only
    inflates the prompt.  This index preserves enough provenance for Solver to
    understand worker coverage while keeping the evidence body in the
    canonical list.
    """
    worker_evidence = plan.get("worker_evidence")
    if not isinstance(worker_evidence, dict):
        return None

    workers: list[dict[str, Any]] = []
    all_evidence_ids: set[str] = set()
    for raw_worker_id, raw_payload in sorted(worker_evidence.items(), key=lambda item: str(item[0])):
        worker_id = str(raw_worker_id).strip()
        if not worker_id:
            continue
        evidence_ids: set[str] = set()
        if isinstance(raw_payload, dict):
            for field in ("evidence_updates", "confirmed_facts"):
                for record in raw_payload.get(field) or []:
                    if isinstance(record, dict):
                        evidence_id = str(record.get("evidence_id") or "").strip()
                        if evidence_id:
                            evidence_ids.add(evidence_id)
        all_evidence_ids.update(evidence_ids)
        workers.append({
            "worker_id": worker_id,
            "evidence_count": len(evidence_ids),
            "evidence_ids": sorted(evidence_ids),
        })

    return {
        "worker_count": len(workers),
        "evidence_count": len(all_evidence_ids),
        "workers": workers,
    }


def _solver_prompt_plan_projection(plan: dict[str, Any]) -> dict[str, Any]:
    """Return a Solver-safe plan view without raw per-worker evidence copies."""
    projection = copy.deepcopy(plan)
    projection.pop("worker_evidence", None)
    decision_context = build_solver_evidence_context(plan)
    for key in (
        "requirements", "candidate_items", "candidate_groups", "dependencies",
        "scope", "protected_paths", "constraints", "evidence_updates",
        "confirmed_facts", "confirmed_fact_ids", "unknowns", "risks",
        "conflicts", "unknown_requirements", "unknown_requirement_ids",
        "unknown_resolutions", "evidence_requests", "context_policy",
    ):
        if key in decision_context:
            projection[key] = decision_context[key]
    projection.pop("questions_for_solver", None)
    index = _worker_evidence_index(plan)
    if index is not None:
        projection["worker_evidence_index"] = index
    return projection


def _compact_analyst_plan_for_solver(plan: dict[str, Any]) -> dict[str, Any]:
    serialized_size = len(json.dumps(plan, ensure_ascii=False, separators=(",", ":")))
    if serialized_size <= 5000:
        return _solver_prompt_plan_projection(plan)
    keys = (
        "plan_id",
        "version",
        "phase",
        "problem_interpretation",
        "objective",
        "success_definition",
        "requirements",
        "goals",
        "non_goals",
        "confirmed_facts",
        "evidence_updates",
        "conflicts",
        "candidate_items",
        "candidate_groups",
        "dependencies",
        "constraints",
        "scope",
        "assumptions",
        "unknowns",
        "risks",
        "evidence_requests",
        "questions_for_solver",
        "candidate_verification_questions",
    )
    compact = {
        key: _compact_solver_prompt_value(plan[key])
        for key in keys
        if key in plan
    }
    index = _worker_evidence_index(plan)
    if index is not None:
        compact["worker_evidence_index"] = index
    if len(json.dumps(compact, ensure_ascii=False, separators=(",", ":"))) > 5000:
        compact["problem_interpretation"] = _compact_solver_prompt_value(plan.get("problem_interpretation", ""), 160)
        compact["objective"] = _compact_solver_prompt_value(plan.get("objective", ""), 180)
        compact["confirmed_facts"] = [
            {
                key: fact.get(key)
                for key in ("evidence_id", "statement", "source", "source_type", "confidence")
                if key in fact
            }
            for fact in plan.get("confirmed_facts", [])
            if isinstance(fact, dict)
        ][:6]
        compact["requirements"] = [
            {
                key: requirement.get(key)
                for key in ("requirement_id", "statement", "priority", "scope")
                if key in requirement
            }
            for requirement in plan.get("requirements", [])
            if isinstance(requirement, dict)
        ]
        compact["evidence_updates"] = [
            {
                key: update.get(key)
                for key in ("evidence_id", "finding_id", "item_id", "requirement_id", "source", "conclusion", "unknowns")
                if key in update
            }
            for update in plan.get("evidence_updates", [])
            if isinstance(update, dict)
        ][:24]
        compact["candidate_items"] = [
            {
                key: item.get(key)
                for key in ("item_id", "title", "objective", "basis_evidence", "dependencies")
                if key in item
            }
            for item in plan.get("candidate_items", [])
            if isinstance(item, dict)
        ]
        compact["candidate_groups"] = [
            {
                key: group.get(key)
                for key in ("candidate_group_id", "title", "objective", "related_items", "basis_evidence")
                if key in group
            }
            for group in plan.get("candidate_groups", [])
            if isinstance(group, dict)
        ]
    decision_context = build_solver_evidence_context(plan)
    for key in (
        "requirements", "candidate_items", "candidate_groups", "dependencies",
        "scope", "protected_paths", "constraints", "evidence_updates",
        "confirmed_facts", "confirmed_fact_ids", "unknowns", "risks",
        "conflicts", "unknown_requirements", "unknown_requirement_ids",
        "unknown_resolutions", "evidence_requests", "context_policy",
    ):
        if key in decision_context:
            compact[key] = decision_context[key]
    compact.pop("questions_for_solver", None)
    return compact



def _solver_revision_requirements(review: Any) -> list[dict[str, Any]]:
    if not isinstance(review, dict):
        return []
    findings = review.get("findings")
    if not isinstance(findings, list):
        return []
    result: list[dict[str, Any]] = []
    seen_finding_ids: set[str] = set()
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        status = normalize_finding_status(
            status=finding.get("status"),
            decision=finding.get("decision"),
        ).lower()
        next_action = str(finding.get("next_action") or "").upper()
        severity = str(finding.get("severity") or "").upper()
        # A finding that still asks Analyst for evidence is not a Solver
        # revision requirement.  It becomes eligible again only after the
        # completed supplement explicitly reassigns it to Solver.
        if not _solver_finding_is_solver_owned(finding):
            continue
        if status not in {
            "open", "reopened", "assigned_to_analyst", "assigned_to_solver", "in_review",
        } and not finding.get("blocking"):
            continue
        if next_action not in {"REQUEST_SOLVER_REVISION", "REQUEST_REGROUP"} and severity not in {"P0", "P1", "P2"} and not finding.get("blocking"):
            continue
        entry = {
            **copy.deepcopy(finding),
            "finding_id": str(finding.get("finding_id") or ""),
            "severity": severity,
            "title": str(finding.get("title") or finding.get("claim") or ""),
            "required_action": copy.deepcopy(finding.get("required_action") or finding.get("required_change") or ""),
            "next_action": next_action,
            "affected_item_ids": [
                str(item_id)
                for item_id in (
                    finding.get("affected_item_ids")
                    or finding.get("item_ids")
                    or ([finding.get("item_id")] if finding.get("item_id") else [])
                )
                if item_id
            ],
            "affected_requirement_ids": [
                str(requirement_id)
                for requirement_id in (
                    finding.get("affected_requirement_ids")
                    or finding.get("requirement_ids")
                    or ([finding.get("requirement_id")] if finding.get("requirement_id") else [])
                )
                if requirement_id
            ],
        }
        if entry["finding_id"]:
            if entry["finding_id"] in seen_finding_ids:
                continue
            seen_finding_ids.add(entry["finding_id"])
            result.append(entry)
    return result


def _solver_finding_is_solver_owned(finding: dict[str, Any]) -> bool:
    owner = str(finding.get("owner_role") or finding.get("owner") or "").strip().lower()
    next_action = str(finding.get("next_action") or "").strip().upper()
    if owner in {"analyst", "review-analyst", "human", "user"}:
        return False
    if owner in {"solver", "review-solver"}:
        return True
    if next_action in {"REQUEST_ANALYST_EVIDENCE", "NEEDS_MORE_EVIDENCE", "HUMAN_GATE", "BLOCKED"}:
        return False
    if next_action in {"REQUEST_SOLVER_REVISION", "REQUEST_REGROUP"}:
        return True
    # Historical Critic payloads may omit ownership and next_action for an
    # active P0-P2 finding. Preserve the previous Solver default for that
    # legacy shape, while refusing to guess for an explicitly named owner.
    if owner:
        return False
    severity = str(finding.get("severity") or "").strip().upper()
    return severity in {"P0", "P1", "P2"} or finding.get("blocking") is True


def _solver_revision_prompt_context(
    requirements: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return an adaptive detailed batch plus a compact all-finding index.

    Batch capacity is derived from a deterministic maximum count. Findings
    are atomic: each selected Finding is included in full. Findings explicitly
    owned by Analyst or human are kept in the index and remainder, not spent
    on the Solver's batch.
    """
    severity_rank = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    ordered = sorted(
        requirements,
        key=lambda item: (
            severity_rank.get(str(item.get("severity") or "P2").upper(), 2),
            0 if _solver_finding_is_solver_owned(item) else 1,
            str(item.get("finding_id") or ""),
        ),
    )
    solver_owned = [item for item in ordered if _solver_finding_is_solver_owned(item)]
    focus = copy.deepcopy(solver_owned[:ZHONGSHU_SOLVER_MAX_FINDINGS_PER_ROUND])
    index = []
    for item in ordered:
        index.append({
            key: copy.deepcopy(item[key])
            for key in (
                "finding_id", "severity", "title", "claim", "target", "owner_role",
                "next_action", "required_action",
            )
            if key in item and item[key] not in (None, "", [], {})
        })
        if isinstance(item.get("claim"), str):
            index[-1]["claim"] = item["claim"][:600]
    return focus, index


def _solver_task_graph_context(plan: dict[str, Any]) -> dict[str, Any]:
    """Keep only the decision-focused Analyst context needed by Solver."""
    context = build_solver_evidence_context(plan)
    index = _worker_evidence_index(plan)
    if index is not None:
        context["worker_evidence_index"] = index
    return context


def _solver_bounded_revision_plan(
    value: Any,
    revision_requirements: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Return the complete current formal graph for a Solver revision."""
    if not isinstance(value, dict):
        return None
    plan = value.get("plan") if isinstance(value.get("plan"), dict) else value
    items = plan.get("items")
    groups = plan.get("groups")
    if not isinstance(items, list) or not isinstance(groups, list):
        return None
    # Keep the complete snapshot.  A partial affected-item view is not a valid
    # READY_FOR_CRITIC plan and caused the model to treat the revision as a
    # notification-only acknowledgement instead of returning the graph.
    return copy.deepcopy(plan)


def _zhongshu_solver_prompt_v2(ctx: StateContext) -> str:
    analyst_plan = ctx.request_payload.get("analyst_plan")
    if not isinstance(analyst_plan, dict):
        raise RuntimeError("validated analyst_plan is missing from StateContext")
    task_graph = _solver_task_graph_context(analyst_plan)
    critic_review = ctx.request_payload.get("zhongshu_critic_review")
    revision_requirements = _solver_revision_requirements(critic_review)
    focus_findings, finding_index = _solver_revision_prompt_context(revision_requirements)
    current_plan = _solver_bounded_revision_plan(
        ctx.request_payload.get("candidate_plan"),
        revision_requirements,
    )
    solver_structured_spec = build_structured_output_spec(
        "ZHONGSHU",
        "review-solver",
        {
            "active_runtime_state": "ZHONGSHU_SOLVER",
            "solver_resume_mode": _is_multica_resume(ctx),
        },
    )
    if current_plan:
        task_graph["current_formal_plan"] = current_plan
    value = {
        "task": ctx.raw_request,
        "role": "ZHONGSHU_SOLVER",
        "mode": "TASK_GRAPH_FORMALIZATION_READ_ONLY",
        "role_objective": (
            "Validate and formalize one complete requirement-to-task graph for Critic. "
            "Do not design implementation solutions."
        ),
        "task_context": {
            "task_id": ctx.task_id,
            "phase": "ZHONGSHU",
            "scope": copy.deepcopy(task_graph.get("scope", {})),
            "protected_paths": copy.deepcopy(task_graph.get("protected_paths", [])),
            "canonicalization_issue": copy.deepcopy(
                ctx.request_payload.get("zhongshu_canonicalization_issue")
            ),
        },
        "upstream": {
            "task_graph": task_graph,
            "critic_findings": focus_findings,
            "active_finding_index": finding_index,
        },
        "skill_rules": list(zhongshu_solver_runtime_rules()),
        "required_output": {
            "field_notation": [
                "plan.requirements",
                "plan.items",
                "plan.groups[*].item_ids",
            ],
            "compact_group_encoding": (
                "For an initial or full-plan response, groups[*].item_ids MUST reference "
                "the complete objects in plan.items; the orchestrator expands IDs into "
                "complete group items before Critic sees the plan. Do not emit "
                "groups[*].items or duplicate task objects."
            ),
            "ready_plan_fields": [
                "requirements",
                "items",
                "groups",
                "dependencies",
                "scope",
                "unknowns",
                "risks",
            ],
            "formal_item_required_fields": list(ZHONGSHU_SOLVER_REQUIRED_ITEM_FIELDS),
            "formal_item_rule": (
                "Every plan.items entry MUST contain every formal_item_required_fields key, "
                "including source_requirement_ids, acceptance_signals, unknowns, risks, "
                "and parallelizable. Do not omit empty arrays. dependencies must be a list "
                "of existing item_id strings and the complete dependency graph must be a "
                "one-way DAG with no direct or transitive cycles."
            ),
            "evidence_request_rule": (
                "For REQUEST_ANALYST_EVIDENCE or NEEDS_MORE_EVIDENCE, return a non-empty "
                "evidence_requests array. Every request must be scoped by item_id or "
                "requirement_id and include question and reason; do not issue an "
                "unroutable global request."
            ),
            "revision_mode": bool(current_plan),
            "candidate_provenance": "Use source_candidate_ids on each merged, split or renamed task to account for the original Analyst candidates. Unchanged task IDs imply identity mapping. Preserve source requirements and evidence.",
            "revision_output": (
                "When current_formal_plan is present, return changes (an array; [] is a valid no-op), "
                "finding_resolutions for the selected batch, and finding_batch. finding_batch must "
                "partition every listed active finding into selected_finding_ids and "
                "remaining_finding_ids; the orchestrator applies changes to the complete canonical "
                "plan and carries the remainder to the next round. Return a full plan only when the "
                "requested topology change cannot be expressed by the allowed operations."
                if current_plan
                else "Initial run: return the complete plan; changes cannot replace the initial plan."
            ),
            "allowed_change_operations": [
                "replace_item_fields(item_id, fields)",
                "replace_group_items(group_id, item_ids)",
                "replace_group_fields(group_id, fields)",
                "replace_plan_fields(fields)",
            ],
            "item_fields": [
                "item_id",
                "title",
                "objective",
                "source_requirement_ids",
                "dependencies",
                "acceptance_signals",
                "unknowns",
                "risks",
                "parallelizable",
            ],
        },
        "quality_gates": [
            "Every Analyst requirement_id and statement is preserved exactly.",
            "Every executable must requirement is covered by at least one task; constraints remain preserved and are not tasks.",
            "Every task is independent, traceable, dependency-valid, and observable.",
            "Dependencies are one-way execution prerequisites only; related work is not a dependency, and the complete plan.items graph must pass topological validation with no direct or transitive cycle.",
            "Every group contains non-empty item_ids referencing plan.items; the orchestrator expands them before validation and Critic review, and every task must appear exactly once.",
            "Scope, protected paths, evidence boundaries, unknowns, and risks are preserved.",
        ],
        "response_contract": {
            "success": {
                "action": "READY_FOR_CRITIC",
                "initial_run": "Include plan as one complete formal task graph.",
                "revision_run": (
                    "Include changes (possibly []), one finding_resolution per selected finding, "
                    "and finding_batch covering selected plus remaining IDs; the orchestrator "
                    "materializes the complete plan from current_formal_plan."
                    if current_plan
                    else "Not applicable until a current formal plan exists."
                ),
                "plan": "Complete formal task graph on initial run, or when a revision needs unsupported topology changes.",
                "complete_plan_materialized_by_orchestrator": bool(current_plan),
            },
            "human_gate": {
                "action": "HUMAN_GATE",
                "question": "The unresolved scope or business decision.",
            },
            "blocked": {
                "action": "BLOCKED",
                "notification": "The missing evidence and unblock condition.",
            },
            "evidence_requests": {
                "required_actions": ["REQUEST_ANALYST_EVIDENCE", "NEEDS_MORE_EVIDENCE"],
                "fields": [
                    "item_id|null", "requirement_id|null", "finding_id|null",
                    "question", "reason", "blocking",
                ],
            },
            "formal_item_required_fields": list(ZHONGSHU_SOLVER_REQUIRED_ITEM_FIELDS),
            "formal_item_rule": (
                "Every plan.items item must contain all formal item fields, even when "
                "unknowns or risks are empty arrays. groups[*].item_ids must reference "
                "existing plan.items entries; do not copy task objects into groups."
            ),
        },
        "revision_protocol": {
            "required_for_ready_for_critic": bool(revision_requirements),
            "finding_requirements": focus_findings,
            "active_finding_ids": [
                str(item["finding_id"])
                for item in revision_requirements
                if item.get("finding_id")
            ],
            "focus_finding_ids": [
                str(item["finding_id"])
                for item in focus_findings
                if item.get("finding_id")
            ],
            "max_findings_per_round": ZHONGSHU_SOLVER_MAX_FINDINGS_PER_ROUND,
            "response_fields": [
                "finding_id",
                "status",
                "response",
                "owner_role",
                "next_action",
                "changed_fields",
                "verification",
            ],
            "batch_fields": [
                "selected_finding_ids",
                "remaining_finding_ids",
                "next_action",
                "progress",
            ],
            "instruction": (
                "Work on the orchestrator-provided focus_finding_ids. Each Finding is atomic and must be included in full; "
                f"select no more than {ZHONGSHU_SOLVER_MAX_FINDINGS_PER_ROUND} findings. Return exactly one finding_resolution "
                "for each selected_finding_id, and put every other listed finding in "
                "remaining_finding_ids. The two arrays must be disjoint and together "
                "cover every listed finding exactly once; never omit a finding. Include "
                "progress.open_before/resolved/remaining/open_after as non-negative integers. "
                "Use status=resolved only when the task graph and verification close the "
                "required action; otherwise use status=unresolved and explain the blocker. "
                + (
                    "Because current_formal_plan is present, return changes only (changes=[] "
                    "is valid when the plan is unchanged); do not copy the full graph. "
                    "The orchestrator will merge the changes and validate the complete plan. "
                    "Return a full plan only for unsupported topology changes. "
                    "finding_resolutions never replaces both plan and changes."
                    if current_plan
                    else "On this initial run, READY_FOR_CRITIC requires the complete plan; "
                    "changes cannot replace plan."
                )
            ),
        },
        "output_example": {
            "action": "READY_FOR_CRITIC",
            "plan": {
                "requirements": [{
                    "requirement_id": "REQ-001",
                    "statement": "one user requirement",
                    "source": "user",
                    "priority": "must",
                    "scope": "in",
                    "kind": "task",
                    "acceptance_signal": "observable result",
                }],
                "items": [{
                    "item_id": "TASK-001",
                    "title": "one task",
                    "objective": "one independently verifiable outcome",
                    "source_requirement_ids": ["REQ-001"],
                    "dependencies": [],
                    "acceptance_signals": ["observable result"],
                    "unknowns": [],
                    "risks": [],
                    "parallelizable": True,
                }],
                "groups": [{
                    "group_id": "GROUP-001",
                    "title": "one task group",
                    "item_ids": ["TASK-001"],
                }],
                "dependencies": [],
                "scope": {},
                "unknowns": [],
                "risks": [],
            },
        },
    }
    value["stable_role_response"] = role_result_template(
        "ZHONGSHU",
        "review-solver",
        task_id=ctx.task_id,
        request_id=ctx.active_request_id,
        state="ZHONGSHU_SOLVER",
        role_mode=(
            "TASK_GRAPH_FORMALIZATION_READ_ONLY_RESUME"
            if _is_multica_resume(ctx)
            else "TASK_GRAPH_FORMALIZATION_READ_ONLY"
        ),
        schema_hash=(solver_structured_spec.schema_hash if solver_structured_spec else ""),
    )
    if current_plan:
        value["output_example"]["changes"] = []
        value["output_example"]["finding_resolutions"] = [{
            "finding_id": "finding-001",
            "status": "resolved",
            "response": "bounded graph correction",
            "owner_role": "review-solver",
            "next_action": "READY_FOR_CRITIC",
            "changed_fields": ["items[TASK-001].acceptance_signals"],
            "verification": ["the corrected task remains requirement-traceable"],
        }]
        value["output_example"]["finding_batch"] = {
            "selected_finding_ids": ["finding-001"],
            "remaining_finding_ids": [],
            "next_action": "READY_FOR_CRITIC",
            "progress": {
                "open_before": 1,
                "resolved": 1,
                "remaining": 0,
                "open_after": 0,
            },
        }
    logger.info(
        "SOLVER_REVISION_PROMPT_BUILT task_id=%s state=%s finding_count=%s finding_ids=%s",
        ctx.task_id,
        ctx.workflow_state,
        len(revision_requirements),
        [item["finding_id"] for item in revision_requirements],
    )
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _legacy_zhongshu_solver_prompt(ctx: StateContext) -> str:
    analyst_plan = ctx.request_payload.get("analyst_plan")
    if not isinstance(analyst_plan, dict):
        raise RuntimeError("validated analyst_plan is missing from StateContext")
    analyst_plan_for_prompt = _compact_analyst_plan_for_solver(analyst_plan)
    critic_review = ctx.request_payload.get("zhongshu_critic_review")
    revision_requirements = _solver_revision_requirements(critic_review)
    focus_findings, finding_index = _solver_revision_prompt_context(revision_requirements)
    value = {
        "task": ctx.raw_request,
        "role": "ZHONGSHU_SOLVER",
        "mode": "PLAN_ONLY_READ_ONLY",
        "role_objective": (
            "基于完整 Analyst plan 形成或修订正式方案，把 candidate_items 和 "
            "candidate_groups 转为可审议的正式 items/groups，交给 Critic 审查。"
        ),
        "upstream": {
            "analyst_plan": analyst_plan_for_prompt,
            "critic_review": {
                "findings": focus_findings,
                "active_finding_index": finding_index,
            },
            "previous_plan": _compact_solver_previous_plan(ctx.request_payload.get("candidate_plan")),
            "human_decision": ctx.request_payload.get("human_decision"),
        },
        "working_rules": [
            "完整复用 analyst_plan，不得遗漏 requirements、scope、unknowns 或 risks。",
            "Analyst requirements 是强制基线；Critic 明确要求的新增 requirement 可以追加，但必须使用唯一的 requirement_id。",
            "READY_FOR_CRITIC requires every plan.groups entry to contain non-empty item_ids",
            "Each groups[*].item_ids entry must reference an existing plan.items item_id; never submit groups[*].items or duplicate task objects",
            "允许按证据需要使用只读工具，不设置固定工具调用次数。",
            "不得使用 write、edit、apply_patch 或其他修改文件的工具。",
            "不得修改业务代码、设计文件、文档或配置；本阶段只输出方案。",
            "证据已足够时应停止重复搜索并开始形成方案。",
            "工具结果返回后必须继续完成本轮推理，不能以过程说明作为最后回复。",
            "最终必须返回一个结构化 JSON；不要额外输出未绑定的 Markdown 报告。",
        ],
        "required_plan_content": {
            "requirements": "从 analyst_plan.requirements 正式化，不得丢失",
            "items": "在 plan.items 提供完整、唯一的正式 item 集合",
            "options": "结合 candidate_directions、alternatives 和 comparison",
            "recommendation": "结合 selected_direction 和 recommendation",
            "groups": "Each formal group must contain non-empty item_ids referencing plan.items",
            "group_items": "Use groups[*].item_ids to reference plan.items; do not emit groups[*].items or duplicate task objects",
            "dependencies": "保留并校验 analyst_plan.dependencies",
            "scope": "保留 in_scope、out_of_scope 和 protected_paths",
            "assumptions": "显式保留并审查",
            "unknowns": "保留 blocking 属性和下一步",
            "risk_signals": "吸收 analyst_plan.risks 和风险信号",
            "candidate_verification_questions": "转为 Critic 可执行的问题",
        },
        "output_example": {
            "action": "READY_FOR_CRITIC",
            "plan": {
                "requirements": [{
                    "requirement_id": "REQ-001",
                    "statement": "string",
                    "source": "user",
                    "priority": "must",
                    "scope": "in",
                    "kind": "task",
                    "acceptance_signal": "observable result",
                }],
                "items": [{
                    "item_id": "item-000001",
                    "title": "string",
                    "objective": "string",
                    "source_requirement_ids": ["REQ-001"],
                    "dependencies": [],
                    "acceptance_signals": ["observable result"],
                    "unknowns": [],
                    "risks": [],
                    "parallelizable": True,
                }],
                "groups": [{
                    "group_id": "group-000001",
                    "title": "string",
                    "item_ids": ["item-000001"],
                }],
                "dependencies": [],
                "scope": {},
                "unknowns": [],
                "risks": [],
            }
        },
        "revision_protocol": {
            "required_for_ready_for_critic": bool(revision_requirements),
            "finding_requirements": focus_findings,
            "active_finding_ids": [
                str(item["finding_id"])
                for item in revision_requirements
                if item.get("finding_id")
            ],
            "focus_finding_ids": [
                str(item["finding_id"])
                for item in focus_findings
                if item.get("finding_id")
            ],
            "max_findings_per_round": ZHONGSHU_SOLVER_MAX_FINDINGS_PER_ROUND,
            "response_fields": [
                "finding_id",
                "status",
                "response",
                "changed_fields",
                "verification",
                "rollback",
            ],
            "instruction": (
                "Work on the orchestrator-provided focus_finding_ids. Each Finding is atomic and must be included in full; "
                f"select no more than {ZHONGSHU_SOLVER_MAX_FINDINGS_PER_ROUND} findings. Return one finding_resolution for each "
                "selected_finding_id and put every other finding in remaining_finding_ids. "
                "The two arrays must be disjoint and together cover all listed findings; "
                "do not omit a finding. Include progress counts. Use status=resolved only "
                "when the plan and verification close the required_action; otherwise use "
                "status=unresolved and explain the blocker."
            ),
        },
        "response_contract": {
            "success": {
                "action": "READY_FOR_CRITIC",
                "notification": "面向人的方案摘要",
                "plan": "Complete formal plan; requires plan.requirements, non-empty plan.items, and non-empty plan.groups[*].item_ids covering every plan.items item exactly once",
            },
            "human_gate": {
                "action": "HUMAN_GATE",
                "next_actions": [{
                    "action": "HUMAN_GATE",
                    "question": "需要用户决定的问题",
                    "options": [{"id": "OPTION-A", "label": "选项名称", "description": "影响"}],
                }],
            },
            "blocked": {
                "action": "BLOCKED",
                "notification": "说明阻塞证据和解除条件",
            },
        },
    }
    value["mode"] = "TASK_GRAPH_FORMALIZATION_READ_ONLY"
    value["role_objective"] = (
        "Turn the canonical Analyst requirement-to-task graph into one formal task graph for Menxia. "
        "Do not design implementation solutions."
    )
    value["working_rules"] = [
        "Preserve every Analyst requirement exactly; do not invent or rename requirements.",
        "Formalize plan.items as independently actionable tasks and plan.groups as task groups.",
        "Every task must have one objective, explicit dependencies, and observable acceptance signals.",
        "Every plan.groups entry must contain non-empty item_ids referencing plan.items.",
        "Preserve task coverage, scope, unknowns, risks, and evidence boundaries from Analyst.",
        "Do not emit implementation_proposal, file_changes, code_changes, implementation_steps, or function-level design.",
            "Do not modify files. Write exactly one complete Solver role-protocol JSON object to result_path with action READY_FOR_CRITIC, then return only the result pointer.",
    ]
    value["required_plan_content"] = {
        "requirements": "Copy analyst_plan.requirements exactly.",
        "items": "Formalize every candidate item as one complete task object.",
        "groups": "Group every task exactly once using item_ids.",
        "group_items": "Use groups[*].item_ids to reference plan.items; do not duplicate task objects.",
        "dependencies": "Preserve task dependencies and do not invent implementation dependencies.",
        "acceptance_signals": "Keep observable task completion criteria.",
        "scope": "Preserve in_scope, out_of_scope, and protected_paths.",
    }
    value["output_example"] = {
        "action": "READY_FOR_CRITIC",
        "plan": {
            "requirements": [{
                "requirement_id": "REQ-001",
                "statement": "one user requirement",
                "source": "user",
                "priority": "must",
                "scope": "in",
                "kind": "task",
                "acceptance_signal": "observable result",
            }],
            "items": [{
                "item_id": "task-001",
                "title": "one task",
                "objective": "one independently verifiable outcome",
                "source_requirement_ids": ["REQ-001"],
                "dependencies": [],
                "acceptance_signals": ["observable result"],
                "unknowns": [],
                "risks": [],
                "parallelizable": True,
            }],
            "groups": [{
                "group_id": "group-001",
                "title": "one task group",
                "item_ids": ["task-001"],
            }],
            "dependencies": [],
            "scope": {},
            "unknowns": [],
            "risks": [],
        },
    }
    prompt_size = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    if prompt_size > 7000:
        value["upstream"].pop("previous_plan", None)
        value["working_rules"] = [
            "Write exactly one complete Solver role-protocol JSON object to result_path and return only its result pointer.",
            "Preserve requirements, evidence, scope, risks, and item coverage.",
            "Preserve every Analyst requirement exactly; never add or rename requirements.",
            "Use plan.items as the complete item set.",
            "Use plan.groups[*].item_ids as references to plan.items exactly once.",
            "Do not emit plan.groups[*].items or duplicate task objects.",
            "Do not modify files, invent facts, or emit implementation proposals.",
        ]
        value["required_plan_content"] = {
            "requirements": "Preserve and formalize analyst_plan.requirements.",
            "items": "Return complete plan.items.",
            "groups": "Every group must contain non-empty item_ids referencing plan.items.",
            "scope": "Preserve in_scope, out_of_scope, and protected_paths.",
            "evidence": "Preserve evidence IDs and confidence boundaries.",
        }
        value["output_example"] = {
            "action": "READY_FOR_CRITIC",
            "plan": {
                "requirements": [{
                    "requirement_id": "REQ-001",
                    "statement": "one user requirement",
                    "source": "user",
                    "priority": "must",
                    "scope": "in",
                    "kind": "task",
                    "acceptance_signal": "observable result",
                }],
                "items": [{"item_id": "task-000001", "title": "one task", "objective": "one independently verifiable outcome", "source_requirement_ids": ["REQ-001"], "dependencies": [], "acceptance_signals": ["observable result"], "unknowns": [], "risks": [], "parallelizable": True}],
                "groups": [{
                    "group_id": "group-000001",
                    "title": "string",
                    "item_ids": ["task-000001"]
                }],
                "dependencies": [],
                "scope": {},
                "unknowns": [],
                "risks": [],
            },
        }
        value["response_contract"] = {
            "instruction": "Use the injected Zhongshu Solver contract for the complete result shape and repair only reported paths.",
            "formal_item_required_fields": list(ZHONGSHU_SOLVER_REQUIRED_ITEM_FIELDS),
            "formal_item_rule": (
                "Every plan.items item must contain all formal item fields, including "
                "empty unknowns and risks arrays. groups[*].item_ids must reference "
                "existing item_id strings in one directed acyclic graph."
            ),
        }
        value["revision_protocol"] = {
            "required_for_ready_for_critic": bool(revision_requirements),
            "finding_requirements": focus_findings,
            "active_finding_ids": [
                str(item["finding_id"])
                for item in revision_requirements
                if item.get("finding_id")
            ],
            "response_fields": ["finding_id", "status", "response", "changed_fields", "verification", "rollback"],
            "instruction": "Return one finding_resolution for each selected finding and a finding_batch that explicitly lists every remaining finding; do not omit unresolved findings.",
        }
    logger.info(
        "SOLVER_REVISION_PROMPT_BUILT task_id=%s state=%s finding_count=%s finding_ids=%s",
        ctx.task_id,
        ctx.workflow_state,
        len(revision_requirements),
        [item["finding_id"] for item in revision_requirements],
    )
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


# The bounded prompt is the runtime hook; the legacy builder above is retained
# temporarily for compatibility with historical imports and fixtures.
_zhongshu_solver_prompt = _zhongshu_solver_prompt_v2


def _compact_solver_previous_plan(value: Any) -> dict[str, Any] | None:
    """Keep only revision-relevant structure from the prior Solver plan."""
    if not isinstance(value, dict):
        return None
    plan = value.get("plan") if isinstance(value.get("plan"), dict) else value

    def pick(source: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
        return {key: source[key] for key in keys if key in source}

    groups: list[dict[str, Any]] = []
    for group in plan.get("groups", []):
        if not isinstance(group, dict):
            continue
        compact_group = pick(
            group,
            ("group_id", "title", "objective", "requirements",
             "dependencies", "risk_level", "order", "parallelizable"),
        )
        raw_item_ids = group.get("item_ids")
        if not isinstance(raw_item_ids, list):
            raw_item_ids = [
                item.get("item_id")
                for item in group.get("items", [])
                if isinstance(item, dict) and item.get("item_id")
            ]
        compact_group["item_ids"] = [
            str(item_id) for item_id in raw_item_ids if str(item_id).strip()
        ]
        groups.append(compact_group)

    items: list[dict[str, Any]] = []
    for item in plan.get("items", []):
        if not isinstance(item, dict):
            continue
        items.append(
            pick(item, ("item_id", "title", "objective", "dependencies"))
        )

    compact = pick(
        plan,
        ("plan_id", "version", "phase", "selected_direction",
         "dependencies", "scope", "risk_signals", "plan_status"),
    )
    compact["groups"] = groups
    compact["items"] = items
    compact["revision_note"] = (
        "Compact prior-plan index only. Rebuild group membership from "
        "authoritative analyst_plan; never submit empty groups."
    )
    return compact


def _is_multica_resume(ctx: StateContext) -> bool:
    last_error = ctx.last_error if isinstance(ctx.last_error, dict) else {}
    return str(last_error.get("code", "")) in {
        "MULTICA_ERROR",
        "AGENT_LAUNCH_FAILED",
    }


def _zhongshu_solver_resume_prompt(ctx: StateContext) -> str:
    """Resume a completed/partially completed Solver without replaying the full plan.

    The previous Solver session already has the full Analyst plan. Replaying it
    on every external retry can make the Multica comment large enough to exceed
    the Windows process command-line limit before OpenCode starts.
    """
    last_error = ctx.last_error if isinstance(ctx.last_error, dict) else {}
    value = {
        "task": ctx.raw_request,
        "role": "ZHONGSHU_SOLVER",
        "mode": "TASK_GRAPH_FORMALIZATION_READ_ONLY_RESUME",
        "resume_reason": str(last_error.get("message", "previous Multica poll/dispatch failed"))[:500],
        "instruction": (
            "Resume the previous Solver turn using the task graph already present "
            "in this issue/session. Repair only the reported transport or structure "
            "problem, do not design implementation solutions, and return exactly "
            "one structured JSON response; do not repeat the full upstream plan."
        ),
        "skill_rules": list(zhongshu_solver_runtime_rules()),
        "response_contract": {
            "instruction": "Use the injected Zhongshu Solver contract for the complete result shape and repair only reported paths.",
        },
    }
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class RequestIntakeState(BaseState):
    name = "REQUEST_INTAKE"


class ZhongshuAnalystState(BaseState):
    name = "ZHONGSHU_ANALYST"


class ZhongshuSolverState(BaseState):
    name = "ZHONGSHU_SOLVER"


class ZhongshuCriticState(BaseState):
    name = "ZHONGSHU_CRITIC"


class ZhongshuFreezeCheckState(BaseState):
    name = "ZHONGSHU_FREEZE_CHECK"


class MenxiaItemSolverState(BaseState):
    name = "MENXIA_ITEM_SOLVER"


class MenxiaItemAnalystState(BaseState):
    name = "MENXIA_ITEM_ANALYST"


class MenxiaItemCriticState(BaseState):
    name = "MENXIA_ITEM_CRITIC"


class MenxiaGroupGateState(BaseState):
    name = "MENXIA_GROUP_GATE"


def _format_queued_human_gate_prompt(gate: dict[str, Any], fallback: str) -> str:
    lines = ["【需要你做决定】"]
    index = int(gate.get("question_index") or 1)
    total = int(gate.get("question_total") or 1)
    if total > 1:
        lines.append(f"问题 {index}/{total}")
    question_id = str(gate.get("question_id") or gate.get("id") or "")
    if question_id:
        lines.append(f"编号：{question_id}")
    lines.append(str(gate.get("question") or fallback))
    lines.append("")
    options = gate.get("options") if isinstance(gate.get("options"), list) else []
    for option_index, option in enumerate(options):
        if not isinstance(option, dict):
            continue
        letter = chr(ord("A") + option_index)
        label = str(option.get("label") or option.get("id") or letter)
        impact = str(option.get("description") or option.get("impact") or "")
        lines.append(f"{letter}. {label}")
        if impact and impact != label:
            lines.append(f"   影响：{impact}")
    lines.append("")
    lines.append("直接回复本消息，输入选项字母即可；无需携带决策编号。")
    return "\n".join(lines)


class HumanGateState(BaseState):
    name = "HUMAN_GATE"

    def enter(self, ctx: StateContext) -> None:
        ctx.entered_at = self._now()
        ctx.updated_at = ctx.entered_at
        ctx.active_decision_id = ctx.active_decision_id or f"decision-{uuid.uuid4().hex}"
        ctx.resume_state = (
            str(ctx.request_payload.get("human_gate_next_state") or "")
            or ctx.resume_state
            or ctx.timeout_phase
            or "ZHONGSHU_CRITIC"
        )
        base_prompt = str(ctx.request_payload.get("human_gate_prompt") or ctx.raw_request)
        prompt = (
            f"{base_prompt}\n\n"
            f"决策编号：{ctx.active_decision_id}\n"
            "直接回复本消息即可；若无法使用回复功能，可发送“决策编号 + 选项/答案”。"
        )
        ctx.request_payload["human_gate_prompt"] = prompt
        logger.info(
            "HUMAN_GATE_CREATED task_id=%s decision_id=%s resume_state=%s",
            ctx.task_id,
            ctx.active_decision_id,
            ctx.resume_state,
        )
        if self.feishu is None:
            ctx.last_error = {"code": "FEISHU_UNAVAILABLE"}
            return
        try:
            receipt = self.feishu.send_gate(
                HumanGate(
                    decision_id=ctx.active_decision_id,
                    task_id=ctx.task_id,
                    resume_state=ctx.resume_state,
                    prompt=prompt,
                )
            )
        except Exception as error:
            ctx.last_error = {
                "code": "FEISHU_REQUEST_FAILED",
                "message": str(error),
            }
            receipt = DeliveryReceipt("feishu", delivered=False)
        if not receipt.delivered:
            fallback = getattr(self.multica, "add_comment", None)
            if fallback and ctx.issue_id:
                try:
                    ctx.gate_message_id = str(fallback(ctx.issue_id, prompt))
                    ctx.request_payload["gate_delivery"] = "issue_fallback"
                    return
                except Exception as error:
                    ctx.last_error = {"code": "HUMAN_GATE_DELIVERY_FAILED", "message": str(error)}
            else:
                ctx.last_error = {"code": "FEISHU_DELIVERY_FAILED"}
            return
        ctx.gate_message_id = receipt.message_id

    def update(self, ctx: StateContext) -> Event:
        if self.feishu is None or not ctx.active_decision_id:
            return Event("NOOP")
        logger.info(
            "HUMAN_GATE_POLL_START task_id=%s decision_id=%s gate_message_id=%s",
            ctx.task_id,
            ctx.active_decision_id,
            ctx.gate_message_id,
        )
        replies = self.feishu.poll_reply(
            HumanGate(
                decision_id=ctx.active_decision_id,
                task_id=ctx.task_id,
                resume_state=ctx.resume_state or "ZHONGSHU_CRITIC",
                prompt=str(ctx.request_payload.get("human_gate_prompt") or ctx.raw_request),
                message_id=ctx.gate_message_id or "",
            )
        )
        if not replies:
            logger.info(
                "HUMAN_GATE_REPLY_PENDING task_id=%s decision_id=%s",
                ctx.task_id,
                ctx.active_decision_id,
            )
            return Event("NOOP")
        reply = replies[0]
        logger.info(
            "HUMAN_GATE_REPLY_RECEIVED task_id=%s decision_id=%s answer=%r author_id=%s",
            ctx.task_id,
            ctx.active_decision_id,
            reply.answer,
            reply.author_id,
        )
        remaining = ctx.request_payload.get("human_gate_queue")
        remaining = list(remaining) if isinstance(remaining, list) else []
        next_gate = remaining[0] if remaining and isinstance(remaining[0], dict) else None
        payload: dict[str, Any] = {
            "decision_id": reply.decision_id,
            "resume_state": (
                "HUMAN_GATE"
                if next_gate is not None
                else ctx.request_payload.get("human_gate_next_state")
                or ctx.resume_state
            ),
            "answer": reply.answer,
            "author_id": reply.author_id,
        }
        if next_gate is not None:
            payload["next_human_gate"] = next_gate
            payload["remaining_human_gates"] = remaining[1:]
            payload["next_human_gate_prompt"] = _format_queued_human_gate_prompt(
                next_gate,
                ctx.raw_request,
            )
        return Event("HUMAN_DECISION_RECEIVED", payload)

    def exit(self, ctx: StateContext, event: Event) -> None:
        super().exit(ctx, event)
        if event.name == "HUMAN_DECISION_RECEIVED":
            ctx.active_decision_id = None
            ctx.gate_message_id = None
            if not event.payload.get("next_human_gate"):
                ctx.request_payload.pop("human_gate_purpose", None)
                ctx.request_payload.pop("human_gate_next_state", None)
                ctx.request_payload.pop("human_gate_queue", None)


class TimeoutState(BaseState):
    name = "TIMEOUT"


class HumanGateTimeoutState(BaseState):
    name = "HUMAN_GATE_TIMEOUT"


class HumanGateErrorState(BaseState):
    name = "HUMAN_GATE_ERROR"


class InvalidAgentReplyState(BaseState):
    name = "INVALID_AGENT_REPLY"


class MulticaErrorState(BaseState):
    name = "MULTICA_ERROR"


class TerminalState(BaseState):
    def enter(self, ctx: StateContext) -> None:
        ctx.recoverable = False
        ctx.updated_at = self._now()


class BlockedState(TerminalState):
    name = "BLOCKED"


def _build_final_delivery(ctx: StateContext) -> dict[str, Any]:
    request_payload = getattr(ctx, "request_payload", {})
    plan = (
        request_payload.get("candidate_plan")
        if isinstance(request_payload, dict)
        else None
    )
    plan = plan if isinstance(plan, dict) else {}
    groups = plan.get("groups") or plan.get("formal_groups") or []
    items = plan.get("items") or []
    findings = getattr(ctx, "findings", [])
    active_findings = [
        finding for finding in findings
        if isinstance(finding, dict)
        and str(finding.get("status", "")).upper()
        not in {"RESOLVED", "WONT_FIX", "DEFERRED"}
    ]
    deferred_findings = [
        finding for finding in findings
        if isinstance(finding, dict)
        and str(finding.get("status", "")).upper() in {"DEFERRED", "WONT_FIX"}
    ]
    return {
        "groups": copy.deepcopy(groups),
        "items": copy.deepcopy(items),
        "review_results": {
            "status": "APPROVED",
            "active_finding_count": len(active_findings),
            "deferred_finding_count": len(deferred_findings),
        },
        "remaining_risks": copy.deepcopy(deferred_findings),
    }


class DoneState(TerminalState):
    name = "DONE"

    def enter(self, ctx: StateContext) -> None:
        super().enter(ctx)
        notify = getattr(self.feishu, "notify", None)
        if not notify:
            return
        payload = dict(getattr(ctx, "last_agent_payload", {}) or {})
        request_payload = getattr(ctx, "request_payload", {})
        if isinstance(request_payload, dict):
            final_delivery = request_payload.get("final_delivery")
            if not isinstance(final_delivery, dict):
                final_delivery = _build_final_delivery(ctx)
                request_payload["final_delivery"] = final_delivery
            payload["final_delivery"] = final_delivery
        messages = build_agent_notification_parts(
            "review-critic",
            "DONE",
            "AGENT_REPLY_ACCEPTED",
            ctx,
            payload,
        )
        for index, message in enumerate(messages):
            self._notify_once(
                ctx,
                (
                    f"{ctx.task_id}:{ctx.sequence}:DONE:enter"
                    if index == 0
                    else f"{ctx.task_id}:{ctx.sequence}:DONE:details:{index}"
                ),
                notify,
                message,
                "critic",
                "DONE",
            )


class CancelledState(TerminalState):
    name = "CANCELLED"


class StateCorruptedState(TerminalState):
    name = "STATE_CORRUPTED"


def build_state_registry(
    multica: MulticaAdapter | None = None,
    feishu: FeishuAdapter | None = None,
    clock: Clock | None = None,
    agent_ids: dict[str, str] | None = None,
    admission: ConcurrencyAdmission | None = None,
) -> dict[str, BaseState]:
    classes = [
        RequestIntakeState,
        ZhongshuAnalystState,
        ZhongshuSolverState,
        ZhongshuCriticState,
        ZhongshuFreezeCheckState,
        MenxiaItemSolverState,
        MenxiaItemAnalystState,
        MenxiaItemCriticState,
        MenxiaGroupGateState,
        HumanGateState,
        TimeoutState,
        HumanGateTimeoutState,
        HumanGateErrorState,
        InvalidAgentReplyState,
        MulticaErrorState,
        BlockedState,
        DoneState,
        CancelledState,
        StateCorruptedState,
    ]
    return {
        state_class().name: state_class(
            multica,
            feishu,
            clock,
            agent_ids,
            admission,
        )
        for state_class in classes
    }


def _normalize_agent_reply_action(
    state: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if state != "MENXIA_GROUP_GATE" or payload.get("action") != "REVISE_GROUP":
        return payload
    normalized = copy.deepcopy(payload)
    normalized["action"] = "REQUEST_GROUP_REVISION"
    normalized["original_action"] = "REVISE_GROUP"
    normalized["action_normalized"] = True
    return normalized


def _allowed_actions(state: str) -> set[str]:
    values = {
        "ZHONGSHU_ANALYST": {"READY_FOR_SOLVER", "EVIDENCE_PACKET_READY", "EVIDENCE_SUPPLEMENT_READY", "REQUIREMENT_CONTRACT_READY", "HUMAN_GATE", "BLOCKED"},
        "ZHONGSHU_SOLVER": {
            "READY_FOR_CRITIC",
            "REQUEST_ANALYST_EVIDENCE",
            "NEEDS_MORE_EVIDENCE",
            "HUMAN_GATE",
            "BLOCKED",
        },
        "ZHONGSHU_CRITIC": set(CRITIC_ACTIONS),
        "MENXIA_ITEM_SOLVER": {"FEASIBLE", "READY_FOR_ANALYST", "READY_FOR_CRITIC", "HUMAN_GATE", "BLOCKED"},
        "MENXIA_ITEM_ANALYST": {"EVIDENCE_SUFFICIENT", "NEEDS_MORE_EVIDENCE", "REQUEST_SOLVER_REVISION", "HUMAN_GATE", "BLOCKED"},
        "MENXIA_ITEM_CRITIC": {"APPROVE_ITEM", "REVISE_ITEM", "SPLIT_ITEM", "MERGE_ITEM", "REMOVE_ITEM", "REQUEST_SOLVER_REVISION", "HUMAN_GATE", "BLOCKED"},
        "MENXIA_GROUP_GATE": {
            "APPROVE_GROUP",
            "APPROVE_FREEZE",
            "REQUEST_GROUP_REVISION",
            "HUMAN_GATE",
            "BLOCKED",
        },
    }
    return values.get(state, set())
