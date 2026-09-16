"""Deterministic prompt projections with no transport or file I/O."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from ..context import WorkflowContext


@dataclass(frozen=True)
class PromptSpec:
    state: str
    phase: str
    role: str
    content: str
    references: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "references", MappingProxyType(dict(self.references)))


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
    content = (
        f"Task {context.identity.task_id}\n"
        f"State: {state}\n"
        f"Request:\n{context.request.raw_request}\n"
        f"Current review finding count: {findings}\n"
        "Return exactly one complete structured result for the bound state."
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
