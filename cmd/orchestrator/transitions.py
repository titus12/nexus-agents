from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .context import StateContext
from .events import Event


@dataclass(frozen=True)
class Transition:
    from_state: str
    event: str
    to_state: str
    reason: str = ""


class TransitionError(RuntimeError):
    pass


class TransitionPolicy:
    """The single source of truth for legal state transitions."""

    TERMINAL = {"DONE", "BLOCKED", "CANCELLED", "STATE_CORRUPTED"}
    NON_TERMINAL = {
        "REQUEST_INTAKE",
        "ZHONGSHU_ANALYST",
        "ZHONGSHU_SOLVER",
        "ZHONGSHU_CRITIC",
        "ZHONGSHU_FREEZE_CHECK",
        "MENXIA_ITEM_SOLVER",
        "MENXIA_ITEM_ANALYST",
        "MENXIA_ITEM_CRITIC",
        "MENXIA_GROUP_GATE",
        "HUMAN_GATE",
        "TIMEOUT",
        "HUMAN_GATE_TIMEOUT",
        "HUMAN_GATE_ERROR",
        "INVALID_AGENT_REPLY",
        "MULTICA_ERROR",
    }

    RESUMABLE_STATES = NON_TERMINAL - {
        "REQUEST_INTAKE",
        "TIMEOUT",
        "HUMAN_GATE_TIMEOUT",
        "HUMAN_GATE_ERROR",
        "INVALID_AGENT_REPLY",
        "MULTICA_ERROR",
    }

    @classmethod
    def validate_target(
        cls,
        ctx: StateContext,
        event: Event,
        transition: Transition,
        known_states: dict[str, Any],
    ) -> None:
        """Reject untrusted dynamic targets before mutating or persisting FSM state."""
        target = str(transition.to_state or "")
        if target not in known_states:
            raise TransitionError(
                f"transition target is not registered: {target or '<empty>'}"
            )

        dynamic = event.payload.get("next_state") or event.payload.get("resume_state")
        if not dynamic:
            return

        allowed: set[str] | None = None
        if ctx.workflow_state == "MENXIA_ITEM_CRITIC" and event.action == "APPROVE_ITEM":
            allowed = {"MENXIA_ITEM_SOLVER", "MENXIA_GROUP_GATE", "HUMAN_GATE"}
        elif ctx.workflow_state == "MENXIA_GROUP_GATE" and event.action in {
            "APPROVE_GROUP",
            "APPROVE_FREEZE",
        }:
            allowed = {"MENXIA_ITEM_SOLVER", "DONE"}
        elif ctx.workflow_state == "ZHONGSHU_ANALYST":
            allowed = {
                "ZHONGSHU_SOLVER",
                "HUMAN_GATE",
                "INVALID_AGENT_REPLY",
                "BLOCKED",
            }
        elif ctx.workflow_state == "ZHONGSHU_SOLVER":
            allowed = {
                "ZHONGSHU_CRITIC",
                "ZHONGSHU_ANALYST",
                "HUMAN_GATE",
                "INVALID_AGENT_REPLY",
                "BLOCKED",
            }
        elif ctx.workflow_state == "ZHONGSHU_CRITIC":
            allowed = {
                "ZHONGSHU_SOLVER",
                "ZHONGSHU_ANALYST",
                "ZHONGSHU_FREEZE_CHECK",
                "HUMAN_GATE",
                "INVALID_AGENT_REPLY",
                "BLOCKED",
            }
        elif ctx.workflow_state == "HUMAN_GATE" and event.name == "HUMAN_DECISION_RECEIVED":
            allowed = cls.RESUMABLE_STATES
        elif ctx.workflow_state in {"INVALID_AGENT_REPLY", "MULTICA_ERROR"}:
            allowed = cls.RESUMABLE_STATES
        elif ctx.workflow_state == "TIMEOUT" and event.name == "RESUME":
            allowed = cls.RESUMABLE_STATES

        if allowed is not None and target not in allowed:
            raise TransitionError(
                f"transition target is not allowed: state={ctx.workflow_state}, "
                f"event={event.name}, target={target}"
            )

    @classmethod
    def resolve(cls, ctx: StateContext, event: Event) -> Transition:
        state = ctx.workflow_state
        name = event.name
        action = event.action

        if state in cls.TERMINAL:
            raise TransitionError(f"terminal state cannot transition: {state}")
        if state not in cls.NON_TERMINAL:
            raise TransitionError(f"unknown state: {state}")

        if name == "CANCEL_REQUESTED":
            return Transition(state, name, "CANCELLED", "operator cancellation")
        if name == "STATE_PERSISTENCE_FAILED":
            return Transition(state, name, "STATE_CORRUPTED", event.reason or "persistence failure")
        if name == "AGENT_TIMEOUT":
            return Transition(state, name, "TIMEOUT", event.reason or "agent timeout")
        if name == "AGENT_REPLY_REJECTED":
            if int(event.payload.get("max_retries", ctx.max_reply_retries)) <= ctx.reply_retry_count:
                return Transition(state, name, "BLOCKED", "invalid reply retry limit reached")
            return Transition(state, name, "INVALID_AGENT_REPLY", event.payload.get("reason", "invalid reply"))
        if name == "HUMAN_GATE_TIMEOUT" and state == "HUMAN_GATE":
            return Transition(state, name, "HUMAN_GATE_TIMEOUT", event.reason or "gate deadline reached")
        if name == "HUMAN_GATE_DELIVERY_FAILED":
            return Transition(state, name, "HUMAN_GATE_ERROR", event.reason or "gate delivery failed")
        if name in {"MULTICA_ERROR", "FEISHU_REQUEST_FAILED"}:
            if name == "FEISHU_REQUEST_FAILED" and state == "HUMAN_GATE":
                return Transition(state, name, "HUMAN_GATE_ERROR", event.reason or "feishu request failed")
            if event.payload.get("retry_exhausted") or event.payload.get("action") == "RETRY_LIMIT_REACHED":
                return Transition(state, name, "BLOCKED", "external retry limit reached")
            if state in {"MULTICA_ERROR", "FEISHU_REQUEST_FAILED"}:
                resume_state = _required_state(event, "resume_state")
                return Transition(state, name, resume_state, "external retry")
            return Transition(state, name, "MULTICA_ERROR", event.reason or "external service error")

        if state == "REQUEST_INTAKE" and action == "START":
            return Transition(state, name, "ZHONGSHU_ANALYST", "request accepted")

        if state == "ZHONGSHU_ANALYST":
            if action in {"READY_FOR_SOLVER", "EVIDENCE_PACKET_READY", "EVIDENCE_SUPPLEMENT_READY"}:
                return Transition(state, name, "ZHONGSHU_SOLVER")
            if action == "HUMAN_GATE":
                return Transition(state, name, "HUMAN_GATE")
            if action == "BLOCKED":
                return Transition(state, name, "HUMAN_GATE" if event.payload.get("human_required") else "BLOCKED")

        if state == "ZHONGSHU_SOLVER":
            if action == "READY_FOR_CRITIC":
                return Transition(state, name, "ZHONGSHU_CRITIC")
            if action in {"REQUEST_ANALYST_EVIDENCE", "NEEDS_MORE_EVIDENCE"}:
                return Transition(state, name, "ZHONGSHU_ANALYST", "Solver evidence is insufficient")
            if action == "HUMAN_GATE":
                return Transition(state, name, "HUMAN_GATE")
            if action == "BLOCKED":
                return Transition(state, name, "HUMAN_GATE" if event.payload.get("human_required") else "BLOCKED")

        if state == "ZHONGSHU_CRITIC":
            if action == "APPROVE_FREEZE":
                blocking = ctx.blocking_finding_ids
                if blocking and ctx.zhongshu_revision_round >= ctx.max_zhongshu_revision_rounds:
                    event.payload.setdefault("resume_state", "ZHONGSHU_SOLVER")
                    event.payload.setdefault("human_gate", {
                        "question": (
                            "中书省已达到最大修订轮次，但仍有活动的 P0/P1 finding。"
                            "请确认是否人工处理后继续。"
                        ),
                        "next_state": "ZHONGSHU_SOLVER",
                    })
                    return Transition(
                        state,
                        name,
                        "HUMAN_GATE",
                        "Zhongshu revision limit reached with active P0/P1 findings",
                    )
                target = "ZHONGSHU_SOLVER" if blocking else "ZHONGSHU_FREEZE_CHECK"
                return Transition(state, name, target, "active P0/P1 remain" if blocking else "")
            if action == "REQUEST_ANALYST_EVIDENCE":
                return Transition(state, name, "ZHONGSHU_ANALYST")
            if action in {"REQUEST_SOLVER_REVISION", "REQUEST_REGROUP"}:
                if ctx.zhongshu_revision_round >= ctx.max_zhongshu_revision_rounds:
                    event.payload.setdefault("resume_state", "ZHONGSHU_SOLVER")
                    event.payload.setdefault("human_gate", {
                        "question": "中书省已达到最大修订轮次，请确认剩余问题的处理方式。",
                        "next_state": "ZHONGSHU_SOLVER",
                    })
                    return Transition(
                        state,
                        name,
                        "HUMAN_GATE",
                        "Zhongshu revision limit reached",
                    )
                return Transition(state, name, "ZHONGSHU_SOLVER")
            if action == "HUMAN_GATE":
                return Transition(state, name, "HUMAN_GATE")
            if action == "BLOCKED":
                return Transition(state, name, "HUMAN_GATE" if event.payload.get("human_required") else "BLOCKED", "Critic reported an explicit blocker")

        if state == "ZHONGSHU_FREEZE_CHECK":
            if action in {"FREEZE_OK", "APPROVE_FREEZE"}:
                return Transition(state, name, "MENXIA_ITEM_SOLVER")
            if action in {"FREEZE_REJECTED", "FREEZE_RETRY"} or name == "FREEZE_REJECTED":
                if ctx.freeze_check_attempt < ctx.max_freeze_check_attempts:
                    return Transition(state, name, "ZHONGSHU_SOLVER", "freeze retry")
                return Transition(state, name, "BLOCKED", "freeze retry limit reached")

        if state == "MENXIA_ITEM_SOLVER":
            if action in {"FEASIBLE", "READY_FOR_ANALYST", "READY_FOR_CRITIC"}:
                return Transition(state, name, "MENXIA_ITEM_ANALYST")
            if action == "HUMAN_GATE":
                return Transition(state, name, "HUMAN_GATE")
            if action == "BLOCKED":
                return Transition(state, name, "HUMAN_GATE" if event.payload.get("human_required") else "BLOCKED")

        if state == "MENXIA_ITEM_ANALYST":
            if action in {"EVIDENCE_SUFFICIENT", "READY_FOR_CRITIC"}:
                return Transition(state, name, "MENXIA_ITEM_CRITIC")
            if action in {"NEEDS_MORE_EVIDENCE", "REQUEST_SOLVER_REVISION"}:
                return Transition(state, name, "MENXIA_ITEM_SOLVER")
            if action == "HUMAN_GATE":
                return Transition(state, name, "HUMAN_GATE")
            if action == "BLOCKED":
                return Transition(state, name, "HUMAN_GATE" if event.payload.get("human_required") else "BLOCKED")

        if state == "MENXIA_ITEM_CRITIC":
            if action == "APPROVE_ITEM":
                active_findings = ctx.finding_objects_for(
                    ctx.active_group_id,
                    ctx.active_item_id,
                )
                blocking = [finding for finding in active_findings if finding.active]
                if blocking:
                    severe = [
                        finding for finding in blocking
                        if str(finding.severity).upper() in {"P0", "P1"}
                    ]
                    if severe:
                        return Transition(
                            state,
                            name,
                            "MENXIA_ITEM_SOLVER",
                            "unresolved P0/P1 findings require solver revision",
                        )
                    event.payload.setdefault("human_gate", {
                        "question": "当前任务仍有未解决的 P2 问题，是否接受残余风险后继续？",
                        "options": [
                            {"id": "A", "label": "打回 Solver 修复 P2"},
                            {"id": "B", "label": "接受残余风险并继续"},
                        ],
                    })
                    return Transition(
                        state,
                        name,
                        "HUMAN_GATE",
                        "unresolved P2 findings require explicit risk decision",
                    )
                return Transition(state, name, str(event.payload.get("next_state") or "MENXIA_GROUP_GATE"))
            if action in {"REVISE_ITEM", "SPLIT_ITEM", "MERGE_ITEM", "REMOVE_ITEM", "REQUEST_SOLVER_REVISION"}:
                if ctx.item_revision_round < ctx.max_item_revision_rounds:
                    return Transition(state, name, "MENXIA_ITEM_SOLVER", "item revision")
                return Transition(state, name, "BLOCKED", "item revision limit reached")
            if action == "ITEM_REVISION_LIMIT":
                return Transition(state, name, "BLOCKED", "item revision limit reached")
            if action == "HUMAN_GATE":
                return Transition(state, name, "HUMAN_GATE")
            if action == "BLOCKED":
                return Transition(state, name, "BLOCKED")

        if state == "MENXIA_GROUP_GATE":
            if action == "REQUEST_GROUP_REVISION":
                return Transition(
                    state,
                    name,
                    "MENXIA_ITEM_SOLVER",
                    "group revision",
                )
            if action in {"APPROVE_GROUP", "APPROVE_FREEZE"}:
                if any(
                    finding.active
                    for finding in ctx.finding_objects_for(
                        ctx.active_group_id,
                        include_global=True,
                    )
                ):
                    return Transition(
                        state,
                        name,
                        "BLOCKED",
                        "group gate cannot approve while active findings remain",
                    )
                return Transition(state, name, str(event.payload.get("next_state") or "DONE"))
            if action == "HUMAN_GATE":
                return Transition(state, name, "HUMAN_GATE")
            if action == "BLOCKED":
                return Transition(state, name, "BLOCKED")

        if state == "HUMAN_GATE":
            if name == "HUMAN_DECISION_RECEIVED":
                decision_id = event.payload.get("decision_id")
                expected_id = ctx.active_decision_id
                if expected_id and decision_id != expected_id:
                    raise TransitionError("human decision_id mismatch")
                return Transition(state, name, _required_state(event, "resume_state", ctx.resume_state))
            if name == "HUMAN_DECISION_INVALID":
                return Transition(state, name, "HUMAN_GATE", "invalid decision")

        if state == "TIMEOUT":
            if name == "RESUME":
                target = _required_state(event, "resume_state", ctx.timeout_phase)
                if event.payload.get("retry_exhausted"):
                    return Transition(state, name, "BLOCKED", "timeout retry limit reached")
                return Transition(state, name, target, "timeout recovery")
            if name == "RESUME_CONTEXT_MISSING":
                return Transition(state, name, "STATE_CORRUPTED", "timeout context missing")
            if name == "RETRY_LIMIT_REACHED":
                return Transition(state, name, "BLOCKED", "timeout retry limit reached")

        if state == "HUMAN_GATE_TIMEOUT":
            if name in {"REOPEN_GATE", "HUMAN_GATE_REOPEN"}:
                return Transition(state, name, "HUMAN_GATE", "manual gate reopen")

        if state == "HUMAN_GATE_ERROR":
            if name == "RETRY":
                return Transition(state, name, "HUMAN_GATE", "manual delivery retry")

        if state == "INVALID_AGENT_REPLY":
            if name == "RETRY":
                if ctx.reply_retry_count < int(event.payload.get("max_retries", ctx.max_reply_retries)):
                    return Transition(state, name, _required_state(event, "resume_state"), "invalid reply retry")
                return Transition(state, name, "BLOCKED", "invalid reply retry limit reached")
            if name == "RETRY_LIMIT_REACHED":
                return Transition(state, name, "BLOCKED", "invalid reply retry limit reached")

        if state == "MULTICA_ERROR":
            if name == "RETRY":
                if ctx.external_retry_count < int(event.payload.get("max_retries", ctx.max_external_retries)):
                    return Transition(state, name, _required_state(event, "resume_state"), "external retry")
                return Transition(state, name, "BLOCKED", "external retry limit reached")
            if name == "RETRY_LIMIT_REACHED":
                return Transition(state, name, "BLOCKED", "external retry limit reached")

        raise TransitionError(f"illegal transition: state={state}, event={name}, action={action}")


def _required_state(event: Event, key: str, fallback: str | None = None) -> str:
    value = event.payload.get(key) or fallback
    if not value:
        raise TransitionError(f"event {event.name} requires {key}")
    return str(value)
