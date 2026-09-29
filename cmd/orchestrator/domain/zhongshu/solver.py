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

from dataclasses import dataclass, replace
import logging
from typing import Any, Iterable, Mapping, Sequence

from ..context import ReviewState, WorkflowContext, ZhongshuGroupState
from ..decisions import EffectRequest
from ..findings import resolve_finding_item_id
from ..zhongshu_doc import project_acceptance_signals
from ..policies.solver_plan import (
    carry_forward_group_docs,
    materialize_solver_reply,
    normalize_solver_finding_ids,
    solver_batch_coverage_error,
    solver_resolution_coverage_error,
    solver_revision_response_error,
    structural_integrity_errors,
)
from ..policies.zhongshu import approved_item_ids, select_solver_batch
from ...zhongshu_parallel import canonical_plan_hash
from ...zhongshu_solver_contract import ZHONGSHU_SOLVER_MAX_FINDINGS_PER_ROUND
from ...dispatch_envelope import build_envelope
from .stages import (
    SolverStage,
    resolve_dispatch_stage,
    resolve_reply_stage,
    review_has_active_findings,
)

logger = logging.getLogger("review_orchestrator_fsm")

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


def _plan_item_ids(review: object) -> set[str] | None:
    """Item ids the live plan owns, or ``None`` when no plan projection exists."""

    plan = getattr(review, "plan", None)
    if not isinstance(plan, Mapping):
        return None
    raw_items = plan.get("items") or plan.get("candidate_items") or []
    return {
        str(item.get("item_id") or "").strip()
        for item in raw_items
        if isinstance(item, Mapping) and str(item.get("item_id") or "").strip()
    }


def batch_item_scope(review: object, batch: SolverBatch) -> list[str]:
    """Items the dictated batch may change, as declared in the dispatch envelope.

    Mirrors :func:`revision_scope` (which recomputes the same boundary from the
    reply's echoed batch), so the declared knife boundary and the enforced one
    come from the same derivation.
    """

    selected = set(batch.selected_finding_ids)
    known = _plan_item_ids(review)
    scope: set[str] = set()
    for finding in getattr(review, "findings", ()) or ():
        finding_id = str(getattr(finding, "finding_id", "") or "")
        if finding_id not in selected:
            continue
        item_id = resolve_finding_item_id(finding, known)
        if item_id:
            scope.add(item_id)
    if known and scope - known:
        logger.warning(
            "SOLVER_SCOPE_ITEM_UNKNOWN items=%s source=batch_item_scope",
            sorted(scope - known),
        )
    scope.difference_update(
        approved_item_ids(getattr(review, "task_review_ledger", ()) or ())
    )
    return sorted(scope)


def _seeded_group_rows(review: object) -> tuple[object, ...]:
    """The authoritative ZhongshuGroupState rows, seeded for any new group."""

    seed = getattr(review, "seed_zhongshu_groups", None)
    return tuple(seed()) if callable(seed) else ()


def _plan_group_ids(plan: object) -> tuple[str, ...]:
    """The sorted group ids a materialized plan declares."""

    groups = plan.get("groups") if isinstance(plan, Mapping) else None
    if not isinstance(groups, (list, tuple)):
        return ()
    return tuple(sorted({
        str(group.get("group_id") or "").strip()
        for group in groups
        if isinstance(group, Mapping) and str(group.get("group_id") or "").strip()
    }))


def batch_group_scope(review: object, batch: SolverBatch) -> list[str]:
    """Groups the dictated batch may rewrite requirement documents for.

    The group-level counterpart of :func:`batch_item_scope`: the owner groups
    of the batch's findings, minus groups whose items are all approved, minus
    frozen groups.  A frozen group is never editable even when the batch names
    one of its findings — that path can only arise from a stale batch and must
    fail closed.
    """

    from ..policies.zhongshu_group import group_of_item

    selected = set(batch.selected_finding_ids)
    scope: set[str] = set()
    for finding in getattr(review, "findings", ()) or ():
        finding_id = str(getattr(finding, "finding_id", "") or "")
        if finding_id not in selected:
            continue
        group_id = str(getattr(finding, "group_id", "") or "").strip()
        if not group_id:
            item_id = str(getattr(finding, "item_id", "") or "").strip()
            group_id = group_of_item(review, item_id) if item_id else ""
        if group_id:
            scope.add(group_id)
    approved = set(approved_item_ids(getattr(review, "task_review_ledger", ()) or ()))
    for group in getattr(review, "task_groups", ()) or ():
        members = set(group.item_ids)
        if members and members.issubset(approved):
            scope.discard(group.group_id)
    scope.difference_update(
        row.group_id
        for row in _seeded_group_rows(review)
        if row.stage == "FROZEN"
    )
    return sorted(scope)


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
    known = _plan_item_ids(review)
    scope: set[str] = set()
    for finding in getattr(review, "findings", ()) or ():
        finding_id = str(getattr(finding, "finding_id", "") or "")
        if finding_id not in selected_ids:
            continue
        item_id = resolve_finding_item_id(finding, known)
        if item_id:
            scope.add(item_id)
    if not scope:
        return None
    if known and scope - known:
        # Keep the unresolved ids in the scope: materialize_solver_reply turns
        # them into a retryable SOLVER_SCOPE_ITEM_UNKNOWN instead of silently
        # carrying the reviewed plan over the solver's fixes.
        logger.warning(
            "SOLVER_SCOPE_ITEM_UNKNOWN items=%s source=revision_scope",
            sorted(scope - known),
        )
    scope.difference_update(
        approved_item_ids(getattr(review, "task_review_ledger", ()) or ())
    )
    return scope


class FormalizeSolverLogic:
    """Analyst channel: turn the analyst contract into the first formal graph.

    There is no Critic feedback to account for, so there is no batch to
    validate; the structured-output schema owns the field-level contract and
    the structural gate owns graph validity.  Every group must ship its
    requirement document with the initial graph (plan Task 5): a FORMALIZE
    reply that omits any group's document is rejected as
    ``SOLVER_GROUP_DOC_MISSING`` and each submitted document must pass the
    mechanical form checks before it folds.
    """

    stage = SolverStage.FORMALIZE

    def __init__(self) -> None:
        self.folded_group_docs: dict[str, dict[str, object]] = {}

    def validate(self, payload: Mapping[str, Any], review: object) -> str:
        return ""

    def editable_item_ids(
        self, payload: Mapping[str, Any], review: object
    ) -> set[str] | None:
        return None

    def materialize(
        self, payload: Mapping[str, Any], review: object
    ) -> tuple[dict[str, object] | None, str]:
        materialized, error = materialize_solver_reply(payload, None)
        if not error and review is not None:
            # Before the first plan folds there are no seeded rows to derive
            # the group set from, so the plan the reply itself declares is the
            # authority on which groups must ship a document.
            required = _plan_group_ids(materialized)
            folded, doc_error = carry_forward_group_docs(
                payload.get("group_docs"),
                _seeded_group_rows(review),
                editable_group_ids=required,
                required_group_ids=required,
                review=review,
                plan=materialized,
            )
            if doc_error:
                return None, doc_error
            self.folded_group_docs = folded
        return materialized, error


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
        self.folded_group_docs: dict[str, dict[str, object]] = {}

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
        if not error:
            error = solver_resolution_coverage_error(
                self._batch.selected_finding_ids, payload
            )
        return error

    def editable_item_ids(
        self, payload: Mapping[str, Any], review: object
    ) -> set[str] | None:
        return revision_scope(review, payload)

    def editable_group_ids(self, review: object) -> list[str]:
        return batch_group_scope(review, self._batch)

    def materialize(
        self, payload: Mapping[str, Any], review: object
    ) -> tuple[dict[str, object] | None, str]:
        materialized, error = materialize_solver_reply(
            payload,
            getattr(review, "plan", None),
            editable_item_ids=self.editable_item_ids(payload, review),
        )
        if not error and review is not None:
            editable_groups = self.editable_group_ids(review)
            folded, doc_error = carry_forward_group_docs(
                payload.get("group_docs"),
                _seeded_group_rows(review),
                editable_group_ids=editable_groups,
                required_group_ids=editable_groups,
                review=review,
                plan=materialized,
            )
            if doc_error:
                return None, doc_error
            self.folded_group_docs = folded
        return materialized, error


@dataclass(frozen=True)
class SolverReplyOutcome:
    """Result of processing one Solver reply, per its declared stage."""

    stage: SolverStage
    payload: Mapping[str, Any]
    materialized: Mapping[str, Any] | None
    error: str
    # Group rows carrying the folded requirement documents (Task 5 fold):
    # empty unless the reply passed validation and submitted documents.
    group_doc_rows: tuple[ZhongshuGroupState, ...] = ()


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
    materialized: Mapping[str, object] | None = None
    if not error:
        materialized, error = logic.materialize(normalized, review)
    group_doc_rows: tuple[ZhongshuGroupState, ...] = ()
    if not error and materialized is not None:
        rows = _rows_with_group_docs(review, logic.folded_group_docs)
        # §8 is the single author of acceptance_signals: the projection
        # overwrites whatever the solver wrote in the plan field.
        materialized, projection_error = project_acceptance_signals(
            materialized,
            {row.group_id: row.doc_markdown for row in rows},
        )
        if projection_error:
            error = projection_error
            materialized = None
        else:
            group_doc_rows = rows
    if not error and materialized is not None:
        integrity = structural_integrity_errors(materialized)
        if integrity:
            error = "SOLVER_PLAN_STRUCTURE_INVALID:" + ";".join(integrity[:10])
    if (
        not error
        and materialized is not None
        and stage is SolverStage.REVISE
        and isinstance(getattr(review, "plan", None), Mapping)
        and (isinstance(normalized.get("plan"), Mapping) or normalized.get("changes"))
        and canonical_plan_hash(materialized) == canonical_plan_hash(review.plan)
    ):
        # The reply claimed edits but the materialized plan is byte-identical
        # to the reviewed one: a scoped carry-forward silently discarded the
        # revision.  Loud, because the critic round that follows can only
        # re-reject the same stale capsule (live incident task-20260926-35833d).
        logger.warning(
            "SOLVER_REVISION_NOOP request_id=%s selected=%s",
            str(normalized.get("request_id") or ""),
            list((normalized.get("finding_batch") or {}).get("selected_finding_ids") or [])
            if isinstance(normalized.get("finding_batch"), Mapping)
            else [],
        )
    return SolverReplyOutcome(stage, normalized, materialized, error, group_doc_rows)


def fold_item_revise_group_docs(
    payload: Mapping[str, Any],
    review: ReviewState | None,
    materialized_plan: Mapping[str, Any] | None,
) -> tuple[str, tuple[ZhongshuGroupState, ...]]:
    """Fold an item-revise reply's group documents (the A2/P3 path).

    The item patch may rewrite acceptance signals, so the owning group's
    requirement document must be re-authored in the same reply: it supersedes
    the authoritative version and closes with the patched plan.  Only groups
    the patches actually touched are editable; a document naming another
    group must byte-match the authoritative markdown or the fold fails.
    """

    submitted = payload.get("group_docs")
    if not submitted:
        return "", ()
    patched = [
        str(group_id).strip()
        for group_id in (payload.get("patched_group_ids") or ())
        if str(group_id).strip()
    ]
    folded, error = carry_forward_group_docs(
        submitted,
        _seeded_group_rows(review),
        editable_group_ids=patched,
        required_group_ids=[],
        review=review,
        plan=materialized_plan,
    )
    if error:
        return error, ()
    return "", _rows_with_group_docs(review, folded)


def _rows_with_group_docs(
    review: ReviewState | None,
    folded: Mapping[str, Mapping[str, object]],
) -> tuple[ZhongshuGroupState, ...]:
    """Merge folded requirement documents into the authoritative group rows.

    The fold (plan Task 5) writes the orchestrator-recomputed document fields
    into the matching ``ZhongshuGroupState`` rows; rows for groups the review
    does not track yet (first formalization, before the plan folds) are
    seeded fresh.  Groups without a folded document keep their row untouched.
    """

    if not folded:
        return ()
    rows: dict[str, ZhongshuGroupState] = {
        row.group_id: row
        for row in getattr(review, "zhongshu_groups", ()) or ()
        if row.group_id
    }
    for group in getattr(review, "task_groups", ()) or ():
        rows.setdefault(group.group_id, ZhongshuGroupState(group_id=group.group_id))
    for group_id in folded:
        rows.setdefault(group_id, ZhongshuGroupState(group_id=group_id))
    merged = []
    for group_id, row in rows.items():
        doc = folded.get(group_id)
        if doc:
            row = replace(
                row,
                doc_markdown=str(doc.get("markdown") or ""),
                doc_version=int(doc.get("doc_version") or 0),
                doc_hash=str(doc.get("doc_hash") or ""),
                doc_source_hash=str(doc.get("doc_source_hash") or ""),
            )
        merged.append(row)
    return tuple(merged)


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

    review = context.review
    stage = resolve_dispatch_stage(
        context.progression.state,
        has_active_findings=review_has_active_findings(review),
    )
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
        scope_items = batch_item_scope(review, batch)
        envelope = build_envelope(
            ingredients=[
                {"key": "review.plan", "source": "context.json", "lifetime": "persisted",
                 "slice": "current_formal_plan"},
                {"key": "review.findings", "source": "context.json", "lifetime": "persisted",
                 "slice": "finding_batch.selected_finding_ids"},
                {"key": "acceptance_standards", "source": "prompt.txt", "lifetime": "persisted"},
                {"key": "retry_feedback", "source": "prompt.txt", "lifetime": "transient"},
            ],
            tools={
                "editable": "items:" + (",".join(scope_items) or "none"),
                "contract": "nexus.zhongshu.solver.v1",
            },
            product={"type": "solver_result", "contract": "nexus.zhongshu.solver.v1"},
        )
        # Context trimming: the revising agent sees only this round's batch.
        # The orchestrator keeps the full findings and the full plan in its own
        # state; the reply is materialized against that, so nothing is lost.
        payload["dispatch_context"] = {
            "solver_stage": stage.value,
            "zhongshu_dispatch_mode": "solver_revision",
            "solver_revision_mode": has_plan,
            "expected_reply_mode": "TASK_GRAPH_FORMALIZATION_READ_ONLY_RESUME",
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
            "group_docs_authoritative": {
                row.group_id: row.doc_markdown
                for row in (
                    review.seed_zhongshu_groups() if review is not None else ()
                )
            },
            # Same authoritative array the Critic's bundle carries: requirement
            # text is quoted from here, never re-derived from memory
            # (task-20260929-c261a8 problem B).
            "requirement_contract": [
                dict(entry)
                for entry in (getattr(review, "requirements", ()) or ())
            ],
            "envelope": envelope,
        }
    else:
        payload["dispatch_context"] = {
            "solver_stage": stage.value,
            "zhongshu_dispatch_mode": "solver_formalize",
            "solver_revision_mode": False,
            "expected_reply_mode": "TASK_GRAPH_FORMALIZATION_READ_ONLY",
            "has_current_plan": has_plan,
            "solver_revision_round": review.zhongshu_revision_round if review else 0,
            "focus_finding_ids": [],
            "active_findings": [],
            "current_formal_plan": dict(review.plan) if has_plan else None,
            "current_plan_ref": review.plan_ref or "" if review else "",
            "current_plan_hash": review.plan_hash or "" if review else "",
            "requirement_contract": [
                dict(entry)
                for entry in (getattr(review, "requirements", ()) or ())
            ],
            "envelope": build_envelope(
                ingredients=[
                    {"key": "review.plan", "source": "context.json", "lifetime": "persisted",
                     "slice": "current_formal_plan"},
                    {"key": "acceptance_standards", "source": "prompt.txt", "lifetime": "persisted"},
                    {"key": "retry_feedback", "source": "prompt.txt", "lifetime": "transient"},
                ],
                tools={"editable": "plan.full", "contract": "nexus.zhongshu.solver.v1"},
                product={"type": "solver_result", "contract": "nexus.zhongshu.solver.v1"},
            ),
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
    "batch_group_scope",
    "build_solver_dispatch",
    "plan_artifact_effect",
    "process_solver_reply",
    "revision_finding_payload",
    "revision_scope",
]
