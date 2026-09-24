"""Deterministic prompt projections with no transport or file I/O."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
from types import MappingProxyType

from ..context import WorkflowContext
from ..errors import is_reply_failure
from ...acceptance_standards import (
    acceptance_standard_applies_to,
    acceptance_standard_block,
)
from ...zhongshu_solver_contract import ZHONGSHU_SOLVER_MAX_FINDINGS_PER_ROUND
from .zhongshu import select_solver_batch


@dataclass(frozen=True)
class PromptSpec:
    state: str
    phase: str
    role: str
    content: str
    references: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "references", MappingProxyType(dict(self.references)))


def retry_feedback(context: WorkflowContext, state: str) -> str:
    """Restate the validator's rejection so a re-ask is not a blind re-sample.

    A reply budget only helps if the agent learns what was wrong: without this
    the model re-emits the same document, burns another full round, and the
    orchestrator reports the same rejection again.  Only the failure of the state
    being dispatched is restated, so a critic slip is never fed to the solver.
    """

    failure = getattr(context, "recovery", None)
    failure = getattr(failure, "last_failure", None)
    if failure is None or not is_reply_failure(getattr(failure, "error_code", "")):
        return ""
    if str(getattr(failure, "state", "") or "") != str(state):
        return ""
    reason = " ".join(str(getattr(failure, "message", "") or "").split())
    if not reason:
        return ""
    return (
        "\n[Retry feedback] The orchestrator rejected your previous reply: "
        f"{reason[:400]}\n"
        "Correct exactly that defect and return one complete structured result again.\n"
    )


def solver_revision_task(context: WorkflowContext) -> str:
    """Render this round's Solver revision task as explicit instructions.

    The review findings used to live only in the 58 KB ``context.json`` (the
    prompt merely said "finding count: 20"), and the agent had to invent the
    batch partition itself.  Every mechanical slip -- a blocker deferred, the
    same id in both lists, an omitted id -- discarded the whole reply.  The
    orchestrator already knows what should be worked on, so it states the batch
    and the exact ``finding_batch`` to return.
    """

    review = context.review
    if review is None or not isinstance(review.plan, Mapping):
        return ""
    active = tuple(finding for finding in review.findings if finding.active)
    if not active:
        return ""
    selected, remaining = select_solver_batch(
        active, ZHONGSHU_SOLVER_MAX_FINDINGS_PER_ROUND
    )
    if not selected:
        return ""
    by_id = {finding.finding_id: finding for finding in active}
    lines = [
        "",
        "[Revision task] Resolve only this round's batch of "
        f"{len(selected)} of {len(active)} open findings. For each listed finding "
        "return one finding_resolution (response, changed_fields, owner_role, "
        "next_action) and adjust the plan only as far as that finding requires:",
    ]
    for finding_id in selected:
        finding = by_id.get(finding_id)
        severity = str(getattr(finding, "severity", "") or "?").strip().upper()
        item_id = str(getattr(finding, "item_id", "") or "-").strip()
        demand = getattr(finding, "required_action", None) or getattr(
            finding, "claim", ""
        )
        demand = " ".join(str(demand or "").split())
        lines.append(f"- {finding_id} [{severity}, item {item_id}] {demand[:220]}")
    lines.append(
        "Copy this finding_batch into your result unchanged; do not re-plan the "
        "partition and do not add or drop ids:"
    )
    lines.append(
        json.dumps(
            {
                "selected_finding_ids": list(selected),
                "remaining_finding_ids": list(remaining),
            },
            ensure_ascii=False,
        )
    )
    lines.append(
        "The orchestrator carries the remaining findings into the next round."
    )
    return "\n".join(lines) + "\n"


def analyst_evidence_task(context: WorkflowContext) -> str:
    """Render the Critic's active findings as the Analyst's directed brief.

    The Analyst used to see only ``Current review finding count: N`` -- the
    demand stayed in the 58 KB context.json while the Critic kept rejecting
    the undirected evidence dump for not addressing it.  The Solver already
    receives its batch verbatim (``solver_revision_task``); the Analyst gets
    the same treatment, plus each finding's structured ``evidence_targets``
    so the fulfillment gate and the answer share one demand definition.
    """

    review = context.review
    if review is None:
        return ""
    active = tuple(finding for finding in review.findings if finding.active)
    if not active:
        return ""
    lines = [
        "",
        "[Evidence task] The Critic holds "
        f"{len(active)} open finding(s). Answer each one in finding_responses "
        "(finding_id, answer, evidence_ids, suggested_disposition) with "
        "file:line evidence; keep evidence_updates scoped to these demands:",
    ]
    for finding in active:
        severity = str(getattr(finding, "severity", "") or "?").strip().upper()
        item_id = str(getattr(finding, "item_id", "") or "-").strip()
        demand = (
            getattr(finding, "required_action", None)
            or getattr(finding, "claim", "")
        )
        demand = " ".join(str(demand or "").split())
        lines.append(
            f"- {finding.finding_id} [{severity}, item {item_id}] {demand[:220]}"
        )
        targets = [
            {"path": str(target.get("path") or ""), "symbol": str(target.get("symbol") or "")}
            for target in (getattr(finding, "evidence_targets", ()) or ())
            if isinstance(target, Mapping) and str(target.get("path") or "").strip()
        ]
        if targets:
            lines.append(
                "  evidence_targets: "
                + json.dumps(targets, ensure_ascii=False, sort_keys=True)
            )
    return "\n".join(lines) + "\n"


def build_prompt(context: WorkflowContext, *, target_state: str | None = None) -> PromptSpec:
    """Project immutable context into a bounded role prompt.

    ``target_state`` is the state the dispatched agent must act as.  Dispatching
    passes the transition target so the prompt never names the state the
    orchestrator is transitioning *from* (which previously told the Solver it
    was in ``ZHONGSHU_CRITIC`` and the Analyst it was in ``REQUEST_INTAKE``).
    """

    state = target_state or context.progression.state
    phase = "ZHONGSHU" if state.startswith("ZHONGSHU") else "MENXIA"
    role = {
        "ANALYST": "review-analyst",
        "SOLVER": "review-solver",
        "CRITIC": "review-critic",
        "FREEZE_CHECK": "review-critic",
        "ITEM_SOLVER": "review-solver",
        "ITEM_ANALYST": "review-analyst",
        "ITEM_CRITIC": "review-critic",
        "GROUP_GATE": "review-critic",
    }.get(state.removeprefix("ZHONGSHU_").removeprefix("MENXIA_"), "")
    findings = len(context.review.findings) if context.review else 0
    revision_task = (
        solver_revision_task(context) if state == "ZHONGSHU_SOLVER" else ""
    )
    evidence_task = (
        analyst_evidence_task(context) if state == "ZHONGSHU_ANALYST" else ""
    )
    # The acceptance-quality standard rides with every producing dispatch, so
    # acceptance signals are written observably the first time instead of
    # being negotiated inside the Critic revision loop.
    acceptance_block = (
        "\n" + acceptance_standard_block()
        if acceptance_standard_applies_to(state)
        else ""
    )
    content = (
        f"Task {context.identity.task_id}\n"
        f"State: {state}\n"
        f"Request:\n{context.request.raw_request}\n"
        f"Current review finding count: {findings}\n"
        "Return exactly one complete structured result for the bound state."
        f"{revision_task}"
        f"{evidence_task}"
        f"{acceptance_block}"
        f"{retry_feedback(context, state)}"
    )
    return PromptSpec(
        state=state,
        phase=phase,
        role=role,
        content=content,
        references={
            "request_payload": context.request.payload_ref or "",
            "last_result": context.delivery.last_result_ref or "",
            "revision_id": context.review.revision_id if context.review else "",
        },
    )


__all__ = ["PromptSpec", "build_prompt"]
