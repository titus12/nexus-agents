from __future__ import annotations

from datetime import datetime, timezone
import uuid
import json
import logging
import copy
from typing import Any

from .adapters import Clock, FeishuAdapter, MulticaAdapter, SystemClock
from .context import StateContext
from .events import Event
from .models import AgentRequest, DeliveryReceipt, HumanGate
from .models import AgentBinding, ExternalMessage
from .transitions import TransitionPolicy
from .validators import RejectedReply, validate_agent_reply
from .notifications import (
    build_agent_notification,
    build_agent_notification_parts,
    should_emit_notification,
)


logger = logging.getLogger("review_orchestrator_fsm")


ROLE_BY_STATE = {
    "ZHONGSHU_ANALYST": ("ZHONGSHU", "review-analyst"),
    "ZHONGSHU_SOLVER": ("ZHONGSHU", "review-solver"),
    "ZHONGSHU_CRITIC": ("ZHONGSHU", "review-critic"),
    "MENXIA_ITEM_SOLVER": ("MENXIA", "review-solver"),
    "MENXIA_ITEM_ANALYST": ("MENXIA", "review-analyst"),
    "MENXIA_ITEM_CRITIC": ("MENXIA", "review-critic"),
    "MENXIA_GROUP_GATE": ("MENXIA", "review-critic"),
}


class BaseState:
    name = ""

    def __init__(
        self,
        multica: MulticaAdapter | None = None,
        feishu: FeishuAdapter | None = None,
        clock: Clock | None = None,
        agent_ids: dict[str, str] | None = None,
    ) -> None:
        self.multica = multica
        self.feishu = feishu
        self.clock = clock or SystemClock()
        self.agent_ids = agent_ids or {}

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
        message = replies[0]
        try:
            payload_size = len(json.dumps(message.payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        except (TypeError, ValueError):
            payload_size = len(message.raw_content.encode("utf-8")) if message.raw_content else 0
        logger.info(
            "AGENT_REPLY_RECEIVED task_id=%s state=%s request_id=%s external_id=%s "
            "payload_bytes=%s payload_keys=%s raw_chars=%s",
            ctx.task_id,
            self.name,
            ctx.active_request_id,
            message.external_id,
            payload_size,
            sorted(message.payload.keys()),
            len(message.raw_content or ""),
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
            logger.warning(
                "AGENT_REPLY_UNSTRUCTURED task_id=%s state=%s request_id=%s external_id=%s",
                ctx.task_id,
                self.name,
                ctx.active_request_id,
                message.external_id,
            )
            raw_reply = str(message.payload.get("raw_reply") or "")
            launch_failed = (
                "start opencode" in raw_reply.lower()
                and "too long" in raw_reply.lower()
            )
            ctx.last_error = {
                "code": "AGENT_LAUNCH_FAILED" if launch_failed else "AGENT_REPLY_UNSTRUCTURED",
                "reason": "OPENCODE_COMMAND_LINE_TOO_LONG" if launch_failed else "REPLY_BODY_NOT_STRUCTURED",
                "message": raw_reply[:500],
                "external_id": message.external_id,
            }
            ctx.request_payload["last_rejected_reply"] = copy.deepcopy(
                message.payload
            )
            return Event("AGENT_REPLY_REJECTED", {
                "reason": "REPLY_BODY_NOT_STRUCTURED",
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
            ctx.request_payload["last_rejected_reply"] = copy.deepcopy(result.payload)
            return Event("AGENT_REPLY_REJECTED", {
                "reason": result.reason,
                "resume_state": self.name,
                "max_retries": ctx.max_reply_retries,
            })
        validated_payload = result.payload
        if self.name == "ZHONGSHU_SOLVER":
            validated_payload, normalization_notes = _normalize_solver_payload(result.payload)
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
            ctx.request_payload["last_rejected_reply"] = copy.deepcopy(validated_payload)
            return Event("AGENT_REPLY_REJECTED", {
                "reason": contract_error,
                "resume_state": self.name,
                "max_retries": ctx.max_reply_retries,
            })
        logger.info(
            "AGENT_REPLY_ACCEPTED task_id=%s state=%s request_id=%s external_id=%s action=%s",
            ctx.task_id,
            self.name,
            ctx.active_request_id,
            message.external_id,
            validated_payload.get("action"),
        )
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
        human_decision = ctx.request_payload.get("human_decision")
        if self.name == "ZHONGSHU_ANALYST":
            prompt = _zhongshu_analyst_prompt(ctx)
        elif self.name == "ZHONGSHU_SOLVER":
            if _is_multica_resume(ctx):
                prompt = _zhongshu_solver_resume_prompt(ctx)
            else:
                prompt = _zhongshu_solver_prompt(ctx)
        elif self.name == "MENXIA_ITEM_SOLVER":
            prompt = _menxia_item_solver_prompt(ctx)
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
        )

    def _dispatch_once(self, ctx: StateContext) -> None:
        if self.multica is None or not ctx.active_request_id or ctx.dispatch_status == "confirmed":
            return
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
        try:
            existing = self.multica.find_existing_request(request.idempotency_key, request.issue_id)
            if existing is not None:
                ctx.dispatch_operation_id = existing.operation_id
                ctx.dispatch_external_message_id = existing.external_message_id
                ctx.dispatch_status = "confirmed"
                return
            ctx.dispatch_status = "executing"
            ctx.dispatch_attempt += 1
            ctx.last_sent_at = self._now()
            receipt = self.multica.dispatch(request)
            ctx.dispatch_operation_id = receipt.operation_id
            ctx.dispatch_external_message_id = receipt.external_message_id
            ctx.dispatch_status = "confirmed" if receipt.confirmed else "pending"
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
    "candidate_verification_questions": list,
    "questions_for_user": list,
}


def _attach_repair_feedback(prompt: str, ctx: StateContext) -> str:
    last_error = ctx.last_error if isinstance(ctx.last_error, dict) else {}
    code = str(last_error.get("code", ""))
    reason = str(last_error.get("reason") or last_error.get("message") or "")
    repairable = code in {
        "AGENT_REPLY_CONTRACT_REJECTED",
        "AGENT_REPLY_UNSTRUCTURED",
    } or (
        code == "AGENT_REPLY_REJECTED"
        and reason == "INVALID_ACTION"
    )
    if not repairable:
        return prompt
    feedback = {
        "validation_error": reason[:500],
        "original_reply": copy.deepcopy(
            ctx.request_payload.get("last_rejected_reply")
        ),
        "instruction": (
            "This is a bounded structure-repair turn. Preserve confirmed facts, "
            "requirements, items, evidence, scope, and decisions. Only repair "
            "the reported protocol error; do not restart investigation, invent "
            "facts, or change the workflow decision. For an unstructured reply, "
            "convert only the original reply into the one JSON contract required "
            "by the current phase; do not re-analyze or change decisions. Return "
            "one complete JSON."
        ),
    }
    if ctx.workflow_state == "ZHONGSHU_SOLVER":
        feedback["instruction"] += (
            " For SOLVER_GROUP_ITEMS_MISSING, every plan.groups entry must contain "
            "a non-empty items array of item objects with item_id; do not use item_ids "
            "or string-only references. For SOLVER_PLAN_MISSING, return the complete "
            "plan object, not only a notification or finding_resolution."
        )
    try:
        value = json.loads(prompt)
    except (TypeError, json.JSONDecodeError):
        value = {"task": ctx.raw_request}
    if not isinstance(value, dict):
        value = {"task": ctx.raw_request}
    value["repair_feedback"] = feedback
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))



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
            "item": item,
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
            "required": ["action", "implementation_proposal"],
            "allowed_actions": ["FEASIBLE", "READY_FOR_CRITIC", "HUMAN_GATE", "BLOCKED"],
            "instruction": "return one JSON object only; do not output Markdown or launcher logs",
        },
    }


def _menxia_item_solver_prompt(ctx: StateContext) -> str:
    capsule = _menxia_item_solver_capsule(ctx)
    serialized = json.dumps(capsule, ensure_ascii=False, separators=(",", ":"))
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
    return serialized

def _zhongshu_analyst_prompt(ctx: StateContext) -> str:
    value = {
        "task": ctx.raw_request,
        "role": "ZHONGSHU_ANALYST",
        "mode": "EVIDENCE_AND_DECOMPOSITION_READ_ONLY",
        "role_objective": (
            "澄清需求、收集可追溯证据、识别冲突和未知项、先拆分候选任务，"
            "再按任务组织候选组，形成唯一新版 Analyst plan 交给 Solver。"
        ),
        "working_rules": [
            "本阶段只分析和拆解，不修改项目文件，不实现业务代码。",
            "事实、推断、假设和未知项必须分开表达。",
            "每条 confirmed_fact 必须包含 evidence_id、statement、source 和 confidence。",
            "每个 candidate_item 必须独立存在，不能只嵌套在 group 中。",
            "每个 candidate_group 只能通过 related_items 引用候选任务。",
            "不得使用旧版 evidence_packet、analyst_draft 或顶层 candidate_groups 协议。",
            "最终必须返回一个 JSON，action 为 READY_FOR_SOLVER、HUMAN_GATE 或 BLOCKED。",
        ],
        "required_plan_schema": {
            "plan_id": "string",
            "version": "number|string",
            "phase": "ZHONGSHU",
            "problem_interpretation": "string",
            "objective": "string",
            "success_definition": "string",
            "requirements": [{
                "requirement_id": "REQ-001",
                "statement": "string",
                "source": "user|derived",
                "priority": "must|should|could",
                "scope": "in|out|conditional",
                "acceptance_signal": "string",
            }],
            "goals": ["string"],
            "non_goals": ["string"],
            "project_context": {},
            "confirmed_facts": [{
                "evidence_id": "ev-000001",
                "statement": "string",
                "source_type": "code|document|knowledge_base|user",
                "source": "path:line or source description",
                "confidence": 0.0,
                "relevance": "string",
            }],
            "conflicts": [],
            "candidate_directions": [],
            "selected_direction": {},
            "alternatives": [],
            "comparison": [],
            "recommendation": {},
            "candidate_items": [{
                "item_id": "item-000001",
                "title": "string",
                "problem_addressed": "string",
                "objective": "string",
                "basis_evidence": ["ev-000001"],
                "why_needed": "string",
                "dependencies": [],
                "acceptance_signals": ["string"],
                "risk_signals": [],
            }],
            "candidate_groups": [{
                "candidate_group_id": "candidate-group-000001",
                "title": "string",
                "objective": "string",
                "reason": "string",
                "related_items": ["item-000001"],
                "basis_evidence": ["ev-000001"],
                "dependencies": [],
                "suggested_order": 1,
            }],
            "dependencies": [],
            "constraints": [],
            "scope": {
                "in_scope": [],
                "out_of_scope": [],
                "protected_paths": [],
            },
            "assumptions": [],
            "unknowns": [],
            "risks": [],
            "questions_for_solver": [],
            "candidate_verification_questions": [],
            "questions_for_user": [],
        },
        "response_contract": {
            "action": "READY_FOR_SOLVER",
            "notification": "面向人的分析摘要",
            "plan": "Plan object must match required_plan_schema; never flatten plan fields to the top level",
        },
    }
    critic_review = ctx.request_payload.get("zhongshu_critic_review")
    if (
        isinstance(critic_review, dict)
        and critic_review.get("action") == "REQUEST_ANALYST_EVIDENCE"
    ):
        value["critic_feedback"] = {
            "action": "REQUEST_ANALYST_EVIDENCE",
            "findings": copy.deepcopy(critic_review.get("findings") or []),
            "missing_evidence": copy.deepcopy(
                critic_review.get("missing_evidence")
                or critic_review.get("questions_for_analyst")
                or []
            ),
            "questions_for_analyst": copy.deepcopy(
                critic_review.get("questions_for_analyst") or []
            ),
            "remaining_blockers": copy.deepcopy(
                critic_review.get("remaining_blockers") or []
            ),
        }
        value["repair_instruction"] = (
            "只针对 Critic 指定的证据缺口补证，不重新开始无关分析；"
            "逐条回应 questions_for_analyst，保留原有有效事实，"
            "无法确认的内容标记为 unknown。"
        )
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _validate_analyst_plan(payload: dict[str, Any]) -> str:
    if payload.get("action") != "READY_FOR_SOLVER":
        return ""
    plan = payload.get("plan")
    if not isinstance(plan, dict):
        return "ANALYST_PLAN_MISSING"
    missing = [name for name in ANALYST_PLAN_REQUIRED_FIELDS if name not in plan]
    if missing:
        return "ANALYST_PLAN_MISSING_FIELDS:" + ",".join(sorted(missing))
    invalid = [
        name
        for name, expected in ANALYST_PLAN_REQUIRED_FIELDS.items()
        if not isinstance(plan.get(name), expected)
    ]
    if invalid:
        return "ANALYST_PLAN_INVALID_TYPES:" + ",".join(sorted(invalid))
    if plan.get("phase") != "ZHONGSHU":
        return "ANALYST_PLAN_PHASE_MISMATCH"

    item_ids: set[str] = set()
    for index, item in enumerate(plan["candidate_items"]):
        if not isinstance(item, dict):
            return f"ANALYST_ITEM_INVALID:{index}"
        required = {
            "item_id",
            "title",
            "problem_addressed",
            "objective",
            "basis_evidence",
            "why_needed",
            "dependencies",
            "acceptance_signals",
            "risk_signals",
        }
        absent = required - set(item)
        if absent:
            return f"ANALYST_ITEM_MISSING_FIELDS:{index}:" + ",".join(sorted(absent))
        item_id = str(item.get("item_id") or "")
        if not item_id or item_id in item_ids:
            return f"ANALYST_ITEM_ID_INVALID:{index}"
        item_ids.add(item_id)

    group_ids: set[str] = set()
    for index, group in enumerate(plan["candidate_groups"]):
        if not isinstance(group, dict):
            return f"ANALYST_GROUP_INVALID:{index}"
        required = {
            "candidate_group_id",
            "title",
            "objective",
            "reason",
            "related_items",
            "basis_evidence",
            "dependencies",
            "suggested_order",
        }
        absent = required - set(group)
        if absent:
            return f"ANALYST_GROUP_MISSING_FIELDS:{index}:" + ",".join(sorted(absent))
        group_id = str(group.get("candidate_group_id") or "")
        if not group_id or group_id in group_ids:
            return f"ANALYST_GROUP_ID_INVALID:{index}"
        group_ids.add(group_id)
        unknown_items = set(str(value) for value in group.get("related_items", [])) - item_ids
        if unknown_items:
            return f"ANALYST_GROUP_UNKNOWN_ITEMS:{group_id}:" + ",".join(sorted(unknown_items))
    return ""


def _validate_state_payload(
    state: str,
    payload: dict[str, Any],
    ctx: StateContext | None = None,
) -> str:
    if state == "ZHONGSHU_ANALYST":
        return _validate_analyst_plan(payload)
    if state == "ZHONGSHU_SOLVER":
        analyst_plan = (
            ctx.request_payload.get("analyst_plan")
            if ctx is not None
            else None
        )
        return _validate_solver_plan(payload, analyst_plan)
    return ""


def _validate_solver_plan(
    payload: dict[str, Any],
    analyst_plan: dict[str, Any] | None = None,
) -> str:
    if payload.get("action") != "READY_FOR_CRITIC":
        return ""
    plan = payload.get("plan")
    if not isinstance(plan, dict):
        return "SOLVER_PLAN_MISSING"
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
            if set(requirement_ids) != expected_ids:
                return "SOLVER_REQUIREMENTS_INCOMPLETE"
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
    groups = plan.get("groups")
    if not isinstance(groups, list) or not groups:
        return "SOLVER_GROUPS_MISSING"
    nested_item_ids: list[str] = []
    for index, group in enumerate(groups):
        if not isinstance(group, dict):
            return f"SOLVER_GROUP_INVALID:{index}"
        items = group.get("items")
        if not isinstance(items, list) or not items:
            return f"SOLVER_GROUP_ITEMS_MISSING:{index}"
        for item_index, item in enumerate(items):
            if not isinstance(item, dict) or not item.get("item_id"):
                return f"SOLVER_GROUP_ITEM_INVALID:{index}:{item_index}"
            nested_item_ids.append(str(item["item_id"]))
    if len(set(nested_item_ids)) != len(nested_item_ids):
        return "SOLVER_GROUP_ITEMS_DUPLICATE"
    if set(nested_item_ids) != set(formal_item_ids):
        return "SOLVER_ITEMS_INDEX_MISMATCH"
    return ""


def _normalize_solver_payload(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    '''Normalize deterministic Solver group references before strict validation.'''
    normalized = copy.deepcopy(payload)
    if normalized.get("action") != "READY_FOR_CRITIC":
        return normalized, []
    plan = normalized.get("plan")
    if not isinstance(plan, dict):
        return normalized, []
    formal_items = plan.get("items")
    if not isinstance(formal_items, list):
        return normalized, []
    item_by_id = {
        str(item.get("item_id")): item
        for item in formal_items
        if isinstance(item, dict) and item.get("item_id")
    }
    groups = plan.get("groups")
    if not isinstance(groups, list):
        return normalized, []

    notes: list[str] = []
    for group_index, group in enumerate(groups):
        if not isinstance(group, dict):
            continue
        references = None
        source_name = ""
        if isinstance(group.get("item_ids"), list):
            references = group.get("item_ids")
            source_name = "item_ids"
        elif (
            isinstance(group.get("items"), list)
            and group.get("items")
            and all(isinstance(item, str) for item in group.get("items", []))
        ):
            references = group.get("items")
            source_name = "items"
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


def _compact_analyst_plan_for_solver(plan: dict[str, Any]) -> dict[str, Any]:
    serialized_size = len(json.dumps(plan, ensure_ascii=False, separators=(",", ":")))
    if serialized_size <= 5000:
        return copy.deepcopy(plan)
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
        "conflicts",
        "candidate_items",
        "candidate_groups",
        "dependencies",
        "constraints",
        "scope",
        "assumptions",
        "unknowns",
        "risks",
        "questions_for_solver",
        "candidate_verification_questions",
    )
    compact = {
        key: _compact_solver_prompt_value(plan[key])
        for key in keys
        if key in plan
    }
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
    return compact



def _solver_revision_requirements(review: Any) -> list[dict[str, Any]]:
    if not isinstance(review, dict):
        return []
    findings = review.get("findings")
    if not isinstance(findings, list):
        return []
    result: list[dict[str, Any]] = []
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        status = str(finding.get("status") or "open").lower()
        next_action = str(finding.get("next_action") or "").upper()
        severity = str(finding.get("severity") or "").upper()
        if status not in {"open", "reopened"} and not finding.get("blocking"):
            continue
        if next_action not in {"REQUEST_SOLVER_REVISION", "REQUEST_REGROUP"} and severity not in {"P1", "P2"} and not finding.get("blocking"):
            continue
        entry = {
            "finding_id": str(finding.get("finding_id") or ""),
            "severity": severity,
            "title": str(finding.get("title") or "")[:240],
            "required_action": str(finding.get("required_action") or "")[:600],
            "next_action": next_action,
        }
        if entry["finding_id"]:
            result.append(entry)
    return result

def _zhongshu_solver_prompt(ctx: StateContext) -> str:
    analyst_plan = ctx.request_payload.get("analyst_plan")
    if not isinstance(analyst_plan, dict):
        raise RuntimeError("validated analyst_plan is missing from StateContext")
    analyst_plan_for_prompt = _compact_analyst_plan_for_solver(analyst_plan)
    critic_review = ctx.request_payload.get("zhongshu_critic_review")
    revision_requirements = _solver_revision_requirements(critic_review)
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
            "critic_review": critic_review,
            "previous_plan": _compact_solver_previous_plan(ctx.request_payload.get("candidate_plan")),
            "human_decision": ctx.request_payload.get("human_decision"),
        },
        "working_rules": [
            "完整复用 analyst_plan，不得遗漏 requirements、scope、unknowns 或 risks。",
            "READY_FOR_CRITIC requires every plan.groups entry to contain a non-empty items array of item objects",
            "Each groups[*].items entry must be a complete item object with item_id; never submit item_ids or string references",
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
            "groups": "Each formal group must contain a non-empty items array of item objects",
            "group_items": "Use groups[*].items with complete item objects copied from plan.items; match by item_id; do not use item_ids or string-only references",
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
                "items": [{
                    "item_id": "item-000001",
                    "title": "string",
                    "objective": "string"
                }],
                "groups": [{
                    "group_id": "group-000001",
                    "title": "string",
                    "items": [{
                        "item_id": "item-000001",
                        "title": "string",
                        "objective": "string"
                    }]
                }]
            }
        },
        "revision_protocol": {
            "required_for_ready_for_critic": bool(revision_requirements),
            "finding_requirements": revision_requirements,
            "response_fields": [
                "finding_id",
                "status",
                "response",
                "changed_fields",
                "verification",
                "rollback",
            ],
            "instruction": (
                "For every listed finding, return exactly one finding_resolution. "
                "Use status=resolved only when the plan and verification close the "
                "required_action; otherwise use status=unresolved and explain the blocker. "
                "Do not omit a finding."
            ),
        },
        "response_contract": {
            "success": {
                "action": "READY_FOR_CRITIC",
                "notification": "面向人的方案摘要",
                "plan": "Complete formal plan; requires plan.requirements, non-empty plan.items, and non-empty plan.groups[*].items object arrays covering every plan.items item exactly once",
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
    prompt_size = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    if prompt_size > 7000:
        value["upstream"].pop("previous_plan", None)
        value["working_rules"] = [
            "Return exactly one JSON object.",
            "Preserve requirements, evidence, scope, risks, and item coverage.",
            "Use plan.items as the complete item set.",
            "Use plan.groups[*].items as complete item objects copied from plan.items exactly once.",
            "Do not use plan.groups[*].item_ids or string-only item references.",
            "Do not modify files or invent facts.",
        ]
        value["required_plan_content"] = {
            "requirements": "Preserve and formalize analyst_plan.requirements.",
            "items": "Return complete plan.items.",
            "groups": "Every group must contain a non-empty items array of item objects copied from plan.items.",
            "scope": "Preserve in_scope, out_of_scope, and protected_paths.",
            "evidence": "Preserve evidence IDs and confidence boundaries.",
        }
        value["output_example"] = {
            "action": "READY_FOR_CRITIC",
            "plan": {
                "requirements": [],
                "items": [{"item_id": "item-000001", "title": "string", "objective": "string"}],
                "groups": [{
                    "group_id": "group-000001",
                    "title": "string",
                    "items": [{"item_id": "item-000001", "title": "string", "objective": "string"}]
                }],
            },
        }
        value["response_contract"] = {
            "allowed_actions": ["READY_FOR_CRITIC", "HUMAN_GATE", "BLOCKED"],
            "required": ["action", "plan"],
        }
        value["revision_protocol"] = {
            "required_for_ready_for_critic": bool(revision_requirements),
            "finding_requirements": revision_requirements,
            "response_fields": ["finding_id", "status", "response", "changed_fields", "verification", "rollback"],
            "instruction": "Return one finding_resolution for every finding; do not omit unresolved findings.",
        }
    logger.info(
        "SOLVER_REVISION_PROMPT_BUILT task_id=%s state=%s finding_count=%s finding_ids=%s",
        ctx.task_id,
        ctx.workflow_state,
        len(revision_requirements),
        [item["finding_id"] for item in revision_requirements],
    )
    last_error = ctx.last_error if isinstance(ctx.last_error, dict) else {}
    if str(last_error.get("code", "")) == "AGENT_REPLY_CONTRACT_REJECTED":
        value["repair_feedback"] = {
            "validation_error": str(last_error.get("reason", ""))[:300],
            "instruction": (
                "This is a structure repair turn. Preserve all facts and items; "
                "only correct the reported contract error and return one complete JSON response. "
                "For SOLVER_GROUP_ITEMS_MISSING, every plan.groups entry must contain a "
                "non-empty items array of item objects with item_id; do not use item_ids "
                "or string-only references. For SOLVER_PLAN_MISSING, return the complete "
                "plan object, not only a notification or finding_resolution."
            ),
        }
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))



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
        compact_group["items"] = []
        for item in group.get("items", []):
            if not isinstance(item, dict):
                continue
            compact_group["items"].append(
                pick(item, ("item_id", "title", "objective", "dependencies"))
            )
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
        "Compact prior-plan index only. Rebuild missing group items from "
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
        "mode": "PLAN_ONLY_READ_ONLY_RESUME",
        "resume_reason": str(last_error.get("message", "previous Multica poll/dispatch failed"))[:500],
        "instruction": (
            "Resume the previous Solver turn. Reuse the Analyst plan and prior "
            "tool results already present in this issue/session. Return exactly "
            "one structured JSON response; do not repeat the full upstream plan."
        ),
        "response_contract": {
            "allowed_actions": ["READY_FOR_CRITIC", "HUMAN_GATE", "BLOCKED"],
            "required": ["action"],
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
    return {state_class().name: state_class(multica, feishu, clock, agent_ids) for state_class in classes}


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
        "ZHONGSHU_ANALYST": {"READY_FOR_SOLVER", "HUMAN_GATE", "BLOCKED"},
        "ZHONGSHU_SOLVER": {"READY_FOR_CRITIC", "HUMAN_GATE", "BLOCKED"},
        "ZHONGSHU_CRITIC": {"APPROVE_FREEZE", "REQUEST_ANALYST_EVIDENCE", "REQUEST_SOLVER_REVISION", "REQUEST_REGROUP", "HUMAN_GATE", "BLOCKED"},
        "MENXIA_ITEM_SOLVER": {"FEASIBLE", "READY_FOR_CRITIC", "HUMAN_GATE", "BLOCKED"},
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
