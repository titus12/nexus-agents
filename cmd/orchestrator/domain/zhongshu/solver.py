"""Solver role logic, isolated per input channel.

``FORMALIZE`` (Analyst contract -> first formal graph) and ``REVISE`` (Critic
feedback -> bounded, scoped revision) are separate logic classes with their own
validation and materialization.  The FSM state only routes by stage; it no
longer infers the channel from payload shape.

The dispatch side is isolated the same way: :func:`build_solver_dispatch`
builds a different context per stage, and the REVISE context carries only what
this round's batch needs (the dictated batch and its findings), not the whole
history.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from ..context import ReviewState, WorkflowContext
from ..decisions import EffectRequest
from ..policies.solver_plan import (
    materialize_solver_reply,
    normalize_solver_finding_ids,
    solver_batch_coverage_error,
    solver_revision_response_error,
    structural_integrity_errors,
)
from ..policies.zhongshu import approved_item_ids, select_solver_batch
from ...zhongshu_parallel import canonical_plan_hash
from ...zhongshu_solver_contract import ZHONGSHU_SOLVER_MAX_FINDINGS_PER_ROUND
from .stages import SolverStage, resolve_dispatch_stage, resolve_reply_stage

# Fields a revising Solver needs per finding.  The full finding record carries
# round history and verification traces that only bloat the revision context
# (findings were 58% of the dispatch payload); the orchestrator keeps the full
# record in its own state.
_REVISION_FINDING_FIELDS = (
    "finding_id",
    "severity",
    "status",
    "owner_role",
    "item_id",
    "group_id",
    "scope",
    "claim",
    "required_action",
    "related_item_ids",
    "remaining_risk",
)


@dataclass(frozen=True)
class SolverBatch:
    """The round's finding batch, chosen by the orchestrator."""

    selected_finding_ids: tuple[str, ...]
    remaining_finding_ids: tuple[str, ...]
    max_findings: int = ZHONGSHU_SOLVER_MAX_FINDINGS_PER_ROUND

    @classmethod
    def for_findings(
        cls,
        findings: Iterable[object],
        max_findings: int = ZHONGSHU_SOLVER_MAX_FINDINGS_PER_ROUND,
    ) -> "SolverBatch":
        selected, remaining = select_solver_batch(findings, max_findings)
        return cls(selected, remaining, max_findings)

    def as_dispatch_context(self) -> dict[str, object]:
        return {
            "selected_finding_ids": list(self.selected_finding_ids),
            "remaining_finding_ids": list(self.remaining_finding_ids),
            "max_findings": self.max_findings,
        }

    def as_reply_template(self) -> dict[str, object]:
        """The exact ``finding_batch`` the reply must echo."""
        return {
            "selected_finding_ids": list(self.selected_finding_ids),
            "remaining_finding_ids": list(self.remaining_finding_ids),
        }

    def as_expected(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        return (self.selected_finding_ids, self.remaining_finding_ids)


def revision_finding_payload(finding: object) -> dict[str, object]:
    """Compact finding record for the revision context."""

    to_dict = getattr(finding, "to_dict", None)
    record: dict[str, object] = dict(to_dict()) if callable(to_dict) else dict(
        finding  # type: ignore[arg-type]
    ) if isinstance(finding, Mapping) else {}
    return {key: record[key] for key in _REVISION_FINDING_FIELDS if key in record}


def revision_scope(
    review: object,
    payload: Mapping[str, Any],
) -> set[str] | None:
    """Items a scoped revision may change: owners of its selected findings.

    Tasks the Critic already approved are subtracted so a later revision cannot
    silently rewrite accepted work: rewriting it changes the review surface,
    drops the approval, and forces the Critic to re-review (and re-find) work
    that had already passed.

    Returning ``None`` means the reply is not a scoped revision (initial run,
    no active plan, or no declared batch), which keeps the full-plan
    materialization path.  An empty set is a *valid* scope meaning no task may
    change; it is not the same as ``None``.
    """

    if review is None or not isinstance(getattr(review, "plan", None), Mapping):
        return None
    batch = payload.get("finding_batch")
    if not isinstance(batch, Mapping):
        return None
    selected = batch.get("selected_finding_ids")
    if not isinstance(selected, list):
        return None
    selected_ids = {str(value).strip() for value in selected if str(value).strip()}
    if not selected_ids:
        return None
    scope: set[str] = set()
    for finding in getattr(review, "findings", ()) or ():
        finding_id = str(getattr(finding, "finding_id", "") or "")
        if finding_id not in selected_ids:
            continue
        item_id = str(getattr(finding, "item_id", "") or "").strip()
        if not item_id:
            item_id = str(getattr(finding, "target", "") or "").strip().split("/")[-1]
        if item_id:
            scope.add(item_id)
    if not scope:
        return None
    scope.difference_update(
        approved_item_ids(getattr(review, "task_review_ledger", ()) or ())
    )
    return scope


class FormalizeSolverLogic:
    """Analyst channel: turn the analyst contract into the first formal graph.

    There is no Critic feedback to account for, so there is no batch to
    validate; the structured-output schema owns the field-level contract and
    the structural gate owns graph validity.
    """

    stage = SolverStage.FORMALIZE

    def validate(self, payload: Mapping[str, Any], review: object) -> str:
        return ""

    def editable_item_ids(
        self, payload: Mapping[str, Any], review: object
    ) -> set[str] | None:
        return None

    def materialize(
        self, payload: Mapping[str, Any], review: object
    ) -> tuple[dict[str, object] | None, str]:
        return materialize_solver_reply(payload, None)


class ReviseSolverLogic:
    """Critic channel: apply the dictated batch to the reviewed plan.

    Validation is the batch contract only: the orchestrator chose the batch, so
    the reply must resolve the selected findings and echo the partition.  A
    reply that re-plans the partition is a shape slip (``SOLVER_BATCH_MISMATCH``)
    charged to the reply budget, not a policy disagreement.
    """

    stage = SolverStage.REVISE

    def __init__(self, batch: SolverBatch) -> None:
        self._batch = batch

    def active_finding_ids(self, review: object) -> list[str]:
        return [
            str(getattr(finding, "finding_id", "") or "")
            for finding in getattr(review, "findings", ()) or ()
            if getattr(finding, "active", False)
        ]

    def validate(self, payload: Mapping[str, Any], review: object) -> str:
        active_ids = self.active_finding_ids(review)
        error = solver_revision_response_error(
            active_ids,
            isinstance(getattr(review, "plan", None), Mapping),
            payload,
        )
        if not error:
            error = solver_batch_coverage_error(
                active_ids,
                payload,
                expected_batch=self._batch.as_expected(),
            )
        return error

    def editable_item_ids(
        self, payload: Mapping[str, Any], review: object
    ) -> set[str] | None:
        return revision_scope(review, payload)

    def materialize(
        self, payload: Mapping[str, Any], review: object
    ) -> tuple[dict[str, object] | None, str]:
        return materialize_solver_reply(
            payload,
            getattr(review, "plan", None),
            editable_item_ids=self.editable_item_ids(payload, review),
        )


@dataclass(frozen=True)
class SolverReplyOutcome:
    """Result of processing one Solver reply, per its declared stage."""

    stage: SolverStage
    payload: Mapping[str, Any]
    materialized: Mapping[str, Any] | None
    error: str


def process_solver_reply(
    payload: Mapping[str, Any],
    review: ReviewState | None,
) -> SolverReplyOutcome:
    """Validate and materialize one Solver reply through its stage's logic."""

    stage = resolve_reply_stage(payload, review)
    active_ids = [
        str(getattr(finding, "finding_id", "") or "")
        for finding in getattr(review, "findings", ()) or ()
        if getattr(finding, "active", False)
    ]
    # Canonicalize abbreviated finding ids once, before any policy reads them.
    normalized = normalize_solver_finding_ids(dict(payload), active_ids)
    if stage is SolverStage.REVISE:
        logic = ReviseSolverLogic(SolverBatch.for_findings(
            getattr(review, "findings", ()) or (),
            ZHONGSHU_SOLVER_MAX_FINDINGS_PER_ROUND,
        ))
    else:
        logic = FormalizeSolverLogic()

    error = logic.validate(normalized, review)
    materialized: Mapping[str, Any] | None = None
    if not error:
        materialized, error = logic.materialize(normalized, review)
    if not error and materialized is not None:
        integrity = structural_integrity_errors(materialized)
        if integrity:
            error = "SOLVER_PLAN_STRUCTURE_INVALID:" + ";".join(integrity[:10])
    return SolverReplyOutcome(stage, normalized, materialized, error)


def build_solver_dispatch(
    context: WorkflowContext,
    *,
    request_id: str,
    prompt: Any,
    revision_id: str = "",
    plan_hash: str = "",
    next_item: object | None = None,
) -> EffectRequest:
    """Build the Solver dispatch for the stage implied by the current edge.

    The stage is decided by the transition edge (the state the dispatch leaves),
    never by payload shape.  The REVISE context carries only this round's batch
    and its findings; the FORMALIZE context carries no Critic feedback at all.
    """

    stage = resolve_dispatch_stage(context.progression.state)
    review = context.review
    has_plan = review is not None and isinstance(review.plan, Mapping)
    active = tuple(
        finding for finding in getattr(review, "findings", ()) or () if finding.active
    ) if review is not None else ()
    payload: dict[str, object] = {
        "issue_id": context.identity.issue_id,
        "request_id": request_id,
        "agent_id": "zhongshu-solver",
        "role": "review-solver",
        "phase": "ZHONGSHU",
        "target_state": "ZHONGSHU_SOLVER",
        "prompt_ref": prompt.content,
        "request_payload_ref": context.request.payload_ref or "",
        "references": dict(prompt.references),
        "revision_id": revision_id,
        "plan_hash": plan_hash,
        "sequence": context.progression.sequence,
        "group_id": next_item.group_id if next_item else None,
        "item_id": next_item.item_id if next_item else None,
    }
    if stage is SolverStage.REVISE:
        batch = SolverBatch.for_findings(active, ZHONGSHU_SOLVER_MAX_FINDINGS_PER_ROUND)
        selected_ids = set(batch.selected_finding_ids)
        # Context trimming: the revising agent sees only this round's batch.
        # The orchestrator keeps the full findings and the full plan in its own
        # state; the reply is materialized against that, so nothing is lost.
        payload["dispatch_context"] = {
            "solver_stage": stage.value,
            "zhongshu_dispatch_mode": "solver_revision",
            "solver_revision_mode": has_plan,
            "has_current_plan": has_plan,
            "solver_revision_round": review.zhongshu_revision_round if review else 0,
            "focus_finding_ids": list(batch.selected_finding_ids),
            "solver_batch": batch.as_dispatch_context(),
            "active_findings": [
                revision_finding_payload(finding)
                for finding in active
                if str(getattr(finding, "finding_id", "")) in selected_ids
            ],
            "current_formal_plan": dict(review.plan) if has_plan else None,
            "current_plan_ref": review.plan_ref or "" if review else "",
            "current_plan_hash": review.plan_hash or "" if review else "",
        }
    else:
        payload["dispatch_context"] = {
            "solver_stage": stage.value,
            "zhongshu_dispatch_mode": "solver_formalize",
            "solver_revision_mode": False,
            "has_current_plan": has_plan,
            "solver_revision_round": review.zhongshu_revision_round if review else 0,
            "focus_finding_ids": [],
            "active_findings": [],
            "current_formal_plan": dict(review.plan) if has_plan else None,
            "current_plan_ref": review.plan_ref or "" if review else "",
            "current_plan_hash": review.plan_hash or "" if review else "",
        }
    return EffectRequest(
        effect_id=f"dispatch:{request_id}",
        effect_type="agent_dispatch",
        task_id=context.identity.task_id,
        idempotency_key=request_id,
        payload_ref=context.request.payload_ref,
        payload=payload,
    )


def plan_artifact_effect(
    task_id: str,
    sequence: int,
    materialized: Mapping[str, Any],
    revision_id: str,
) -> EffectRequest:
    """The orchestrator-owned plan artifact for a materialized Solver reply."""

    return EffectRequest(
        effect_id=f"plan:{task_id}:{sequence + 1}",
        effect_type="plan_artifact",
        task_id=task_id,
        idempotency_key=f"{task_id}:policy-plan:{sequence + 1}",
        payload={
            "plan": materialized,
            "plan_hash": canonical_plan_hash(materialized),
            "revision_id": revision_id,
            "sequence": sequence,
        },
    )


__all__ = [
    "FormalizeSolverLogic",
    "ReviseSolverLogic",
    "SolverBatch",
    "SolverReplyOutcome",
    "build_solver_dispatch",
    "plan_artifact_effect",
    "process_solver_reply",
    "revision_finding_payload",
    "revision_scope",
]
