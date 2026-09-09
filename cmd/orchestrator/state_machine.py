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
        dispatch_pending: Callable[[StateContext, State], None] | None = None,
    ) -> None:
        self.ctx = ctx
        self.states = states
        self.persist = persist or (lambda _ctx, _event, _transition: None)
        self.dispatch_pending = dispatch_pending

    def start(self) -> None:
        state = self.states[self.ctx.workflow_state]
        state.enter(self.ctx)
        self.persist(self.ctx, None, None)
        self._dispatch_pending(state)
        self.persist(self.ctx, None, None)

    def dispatch(self, event: Event) -> Transition:
        current = self.states[self.ctx.workflow_state]
        if event.name == "AGENT_REPLY_REJECTED":
            event.payload.setdefault("resume_state", self.ctx.workflow_state)
            self.ctx.resume_state = str(event.payload["resume_state"])
        if event.name == "HUMAN_DECISION_RECEIVED":
            self._prepare_human_decision(event)
        transition = TransitionPolicy.resolve(self.ctx, event)
        TransitionPolicy.validate_target(self.ctx, event, transition, self.states)
        pending_event = self.ctx.request_payload.get("pending_fsm_event")
        pending_payload = (
            pending_event.get("payload")
            if isinstance(pending_event, dict)
            and isinstance(pending_event.get("payload"), dict)
            else {}
        )
        if (
            isinstance(pending_event, dict)
            and str(pending_event.get("name") or "") == event.name
            and (
                not pending_payload.get("reason")
                or pending_payload.get("reason") == event.payload.get("reason")
            )
        ):
            # Clear the durable intent only after the event has been validated.
            # If the following state commit is interrupted, the old state file
            # still contains the intent and startup will replay it once.
            self.ctx.request_payload.pop("pending_fsm_event", None)
        if transition.to_state == "HUMAN_GATE":
            gate = event.payload.get("human_gate")
            if isinstance(gate, dict):
                self.ctx.resume_state = str(
                    gate.get("next_state")
                    or event.payload.get("resume_state")
                    or self.ctx.workflow_state
                )
                self.ctx.request_payload["human_gate"] = dict(gate)
                self.ctx.request_payload["human_gate_next_state"] = self.ctx.resume_state
                self.ctx.request_payload["human_gate_source_state"] = self.ctx.workflow_state
                lines = [str(gate.get("question") or "请确认是否继续。")]
                for option in gate.get("options", []):
                    if isinstance(option, dict):
                        lines.append(f"{option.get('id', '')}. {option.get('label', '')}")
                self.ctx.request_payload["human_gate_prompt"] = "\n".join(lines)
            else:
                self.ctx.resume_state = str(
                    event.payload.get("resume_state") or self.ctx.workflow_state
                )
        current.exit(self.ctx, event)
        self._apply_counters(event, transition)
        self.ctx.workflow_state = transition.to_state
        if transition.to_state == "BLOCKED":
            reason = (
                str(event.payload.get("reason") or "").strip()
                if isinstance(event.payload, dict)
                else ""
            )
            if not reason:
                reason = str(event.reason or transition.reason or "").strip()
            if not reason and isinstance(event.payload, dict):
                reason = str(
                    event.payload.get("message")
                    or event.payload.get("notification")
                    or "workflow blocked"
                ).strip()
            self.ctx.blocked_reason = reason or "workflow blocked"
        elif self.ctx.workflow_state != "BLOCKED":
            self.ctx.blocked_reason = None
        self.ctx.sequence += 1
        next_state = self.states[transition.to_state]
        # Prepare the destination state before writing the transition record.
        # A process can be interrupted between any two filesystem writes.  If
        # the event is durable while the destination state's request is not,
        # restart used to load a state with no active request and become stuck.
        # The event record now describes the already-prepared destination.
        next_state.enter(self.ctx)
        self.persist(self.ctx, event, transition)
        self._dispatch_pending(next_state)
        self.persist(self.ctx, None, None)
        return transition

    def _dispatch_pending(self, state: State) -> None:
        if self.dispatch_pending is not None:
            self.dispatch_pending(self.ctx, state)
            return
        pending = getattr(state, "dispatch_pending", None)
        if pending:
            pending(self.ctx)

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
        if selected_option is not None:
            accepted = (
                str(selected_option.get("letter") or "").upper() == "B"
                or str(selected_option.get("id") or "").upper() == "B"
            )
        else:
            accepted = answer in {
                "B",
                "ACCEPT",
                "ACCEPTED_RISK",
                "ACCEPT_RISK",
                "接受",
                "接受风险",
            }
        if accepted:
            findings = self.ctx.finding_objects_for(
                self.ctx.active_group_id,
                self.ctx.active_item_id,
            )
            for finding in findings:
                if finding.active and str(finding.severity).upper() == "P2":
                    finding.status = "DEFERRED"
                    finding.resolution = "human accepted residual risk"
            self.ctx.merge_findings(findings)
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
