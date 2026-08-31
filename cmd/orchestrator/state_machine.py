from __future__ import annotations

from typing import Any, Callable, Protocol

from .context import StateContext
from .events import Event
from .transitions import Transition, TransitionPolicy


class State(Protocol):
    name: str

    def enter(self, ctx: StateContext) -> None: ...
    def update(self, ctx: StateContext) -> Event: ...
    def exit(self, ctx: StateContext, event: Event) -> None: ...

    def dispatch_pending(self, ctx: StateContext) -> None: ...


def _normalize_human_answer(answer: str) -> str:
    value = answer.strip()
    while value.startswith("@"):
        parts = value.split(None, 1)
        if len(parts) == 1:
            return ""
        value = parts[1].strip()
    return value


def _resolve_human_gate_option(gate: dict[str, Any], answer: str) -> dict[str, Any] | None:
    normalized = answer.strip().upper()
    options = gate.get("options") if isinstance(gate.get("options"), list) else []
    for index, option in enumerate(options):
        if not isinstance(option, dict):
            continue
        letter = chr(ord("A") + index)
        option_id = str(option.get("id") or letter)
        label = str(option.get("label") or "")
        if (
            normalized == letter
            or normalized == option_id.upper()
            or (label and normalized == label.upper())
            or normalized.startswith(letter + ".")
        ):
            selected = dict(option)
            selected["letter"] = letter
            selected.setdefault("id", option_id)
            return selected
    return None


class StateMachine:
    def __init__(
        self,
        ctx: StateContext,
        states: dict[str, State],
        persist: Callable[[StateContext, Event | None, Transition | None], None] | None = None,
    ) -> None:
        self.ctx = ctx
        self.states = states
        self.persist = persist or (lambda _ctx, _event, _transition: None)

    def start(self) -> None:
        state = self.states[self.ctx.workflow_state]
        state.enter(self.ctx)
        self.persist(self.ctx, None, None)
        dispatch_pending = getattr(state, "dispatch_pending", None)
        if dispatch_pending:
            dispatch_pending(self.ctx)
        self.persist(self.ctx, None, None)

    def dispatch(self, event: Event) -> Transition:
        current = self.states[self.ctx.workflow_state]
        if event.name == "AGENT_REPLY_REJECTED":
            event.payload.setdefault("resume_state", self.ctx.workflow_state)
            self.ctx.resume_state = str(event.payload["resume_state"])
        if event.name == "HUMAN_DECISION_RECEIVED":
            self._prepare_human_decision(event)
        transition = TransitionPolicy.resolve(self.ctx, event)
        if transition.to_state == "HUMAN_GATE":
            self.ctx.resume_state = self.ctx.workflow_state
            gate = event.payload.get("human_gate")
            if isinstance(gate, dict):
                lines = [str(gate.get("question") or "请确认是否继续。")]
                for option in gate.get("options", []):
                    if isinstance(option, dict):
                        lines.append(f"{option.get('id', '')}. {option.get('label', '')}")
                self.ctx.request_payload["human_gate_prompt"] = "\n".join(lines)
        current.exit(self.ctx, event)
        self._apply_counters(event, transition)
        self.ctx.workflow_state = transition.to_state
        self.ctx.sequence += 1
        self.persist(self.ctx, event, transition)
        next_state = self.states[transition.to_state]
        next_state.enter(self.ctx)
        self.persist(self.ctx, None, None)
        dispatch_pending = getattr(next_state, "dispatch_pending", None)
        if dispatch_pending:
            dispatch_pending(self.ctx)
        self.persist(self.ctx, None, None)
        return transition

    def _prepare_human_decision(self, event: Event) -> None:
        answer_raw = _normalize_human_answer(str(event.payload.get("answer") or ""))
        event.payload["answer"] = answer_raw
        answer = answer_raw.upper()
        gate = self.ctx.request_payload.get("human_gate")
        selected_option = _resolve_human_gate_option(
            gate if isinstance(gate, dict) else {},
            answer_raw,
        )
        decision = {
            "decision_id": event.payload.get("decision_id"),
            "answer": answer_raw,
            "selected_option": selected_option,
            "question_id": gate.get("question_id") if isinstance(gate, dict) else None,
            "question": (
                gate.get("question")
                if isinstance(gate, dict)
                else self.ctx.request_payload.get("human_gate_prompt")
            ),
            "source_state": self.ctx.request_payload.get("human_gate_source_state"),
            "resume_state": (
                self.ctx.request_payload.get("human_gate_next_state")
                or self.ctx.resume_state
            ),
            "author_id": event.payload.get("author_id", ""),
        }
        self.ctx.request_payload["human_decision"] = decision
        decisions = self.ctx.request_payload.get("human_decisions")
        decisions = list(decisions) if isinstance(decisions, list) else []
        decisions.append(decision)
        self.ctx.request_payload["human_decisions"] = decisions

        next_gate = event.payload.get("next_human_gate")
        if isinstance(next_gate, dict):
            self.ctx.request_payload["human_gate"] = next_gate
            self.ctx.request_payload["human_gate_queue"] = list(
                event.payload.get("remaining_human_gates") or []
            )
            self.ctx.request_payload["human_gate_prompt"] = str(
                event.payload.get("next_human_gate_prompt") or next_gate.get("question") or ""
            )
            event.payload["resume_state"] = "HUMAN_GATE"
        else:
            event.payload["resume_state"] = (
                self.ctx.request_payload.get("human_gate_next_state")
                or self.ctx.resume_state
            )
        if self.ctx.request_payload.get("human_gate_purpose") != "P2_RISK":
            return
        accepted = answer.startswith("B") or "鎺ュ彈" in answer or "ACCEPT" in answer
        if accepted:
            findings = self.ctx.finding_objects()
            for finding in findings:
                if finding.active and str(finding.severity).upper() == "P2":
                    finding.status = "DEFERRED"
                    finding.resolution = "human accepted residual risk"
            self.ctx.replace_findings(findings)
            event.payload["resume_state"] = (
                self.ctx.request_payload.get("human_gate_next_state")
                or self.ctx.resume_state
            )
        else:
            self.ctx.item_revision_round += 1
            event.payload["resume_state"] = "MENXIA_ITEM_SOLVER"

    def snapshot(self) -> dict[str, Any]:
        return self.ctx.to_dict()

    def _apply_counters(self, event: Event, transition: Transition) -> None:
        source_state = self.ctx.workflow_state
        reply_retry_transition = (
            source_state == "INVALID_AGENT_REPLY"
            or transition.to_state == "INVALID_AGENT_REPLY"
        )
        if (
            not reply_retry_transition
            and transition.to_state != source_state
            and self.ctx.reply_retry_count
        ):
            self.ctx.reply_retry_count = 0
        if event.name == "FREEZE_REJECTED" or event.action in {"FREEZE_REJECTED", "FREEZE_RETRY"}:
            self.ctx.freeze_check_attempt += 1
            self.ctx.zhongshu_revision_round += 1
        if self.ctx.workflow_state == "MENXIA_ITEM_CRITIC" and transition.to_state == "MENXIA_ITEM_SOLVER":
            self.ctx.item_revision_round += 1
        if self.ctx.workflow_state == "ZHONGSHU_CRITIC" and transition.to_state == "ZHONGSHU_SOLVER":
            self.ctx.zhongshu_revision_round += 1
        if self.ctx.workflow_state == "INVALID_AGENT_REPLY" and event.name == "RETRY":
            self.ctx.reply_retry_count += 1
        if self.ctx.workflow_state in {"MULTICA_ERROR", "FEISHU_REQUEST_FAILED"} and event.name == "RETRY":
            self.ctx.external_retry_count += 1
        if self.ctx.workflow_state == "TIMEOUT" and event.name == "RESUME":
            self.ctx.timeout_retry_count += 1
