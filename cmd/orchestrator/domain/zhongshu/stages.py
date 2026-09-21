"""Explicit stage boundaries for the Zhongshu loop.

The Solver used to be a single code path whose behaviour was inferred from data
("does a plan happen to exist?") and whose dispatch label claimed ``revision``
even for the first formalization.  Both roles now carry an explicit stage that
the FSM edge decides, so the two input channels are isolated in code, not just
in payload shape.
"""

from __future__ import annotations

from enum import Enum
import logging
from typing import Any, Mapping

logger = logging.getLogger("review_orchestrator_fsm")

# Edges that enter the Solver carrying Critic feedback.  Every other edge into
# ``ZHONGSHU_SOLVER`` (intake, analyst) starts the formalization channel.
_REVISE_SOURCES = frozenset({"ZHONGSHU_CRITIC", "ZHONGSHU_FREEZE_CHECK"})


class SolverStage(Enum):
    """Which input channel the Solver is working on."""

    FORMALIZE = "formalize"  # analyst contract -> first formal task graph
    REVISE = "revise"        # critic findings -> bounded, scoped revision


def resolve_dispatch_stage(source_state: str) -> SolverStage:
    """Stage of the dispatch leaving ``source_state`` towards the Solver.

    The transition edge is authoritative: the Critic hands over feedback, so
    that edge is a revision regardless of whether a plan happens to exist in
    the snapshot (an Analyst-produced plan can predate the first Solver call).
    """

    if str(source_state or "") in _REVISE_SOURCES:
        return SolverStage.REVISE
    return SolverStage.FORMALIZE


def resolve_reply_stage(payload: Mapping[str, Any], review: object) -> SolverStage:
    """Stage of an arriving Solver reply.

    The agent declares the mode it answered in (``..._RESUME`` = revision,
    ``..._READ_ONLY`` = formalization); that is the primary signal.  Replies
    without a usable mode fall back to the review snapshot.

    A ``..._READ_ONLY`` self-report is not authoritative on its own: a live
    run (task-20260920-94c51b seq 10) answered a *revision* dispatch while
    still declaring the formalization mode, which silently bypassed the batch
    validation and the scoped carry-forward that keeps approved tasks frozen.
    A ``finding_batch`` echo is structural evidence of the revise channel --
    the formalize channel does not even define one -- so when the reply
    carries a non-empty batch against an existing plan, the structure wins
    and the contradiction is logged.
    """

    mode = str((payload or {}).get("mode") or "").strip().upper()
    if mode.endswith("_RESUME"):
        return SolverStage.REVISE
    if mode.endswith("_READ_ONLY"):
        if _reply_carries_revision_batch(payload) and isinstance(
            getattr(review, "plan", None), Mapping
        ):
            logger.warning(
                "SOLVER_STAGE_MODE_CONTRADICTION mode=%s resolved=REVISE "
                "reason=finding_batch_against_existing_plan",
                mode,
            )
            return SolverStage.REVISE
        return SolverStage.FORMALIZE
    plan = getattr(review, "plan", None)
    if isinstance(plan, Mapping):
        return SolverStage.REVISE
    return SolverStage.FORMALIZE


def _reply_carries_revision_batch(payload: Mapping[str, Any]) -> bool:
    batch = payload.get("finding_batch")
    if not isinstance(batch, Mapping):
        return False
    selected = batch.get("selected_finding_ids")
    return isinstance(selected, list) and any(
        str(value).strip() for value in selected
    )


__all__ = ["SolverStage", "resolve_dispatch_stage", "resolve_reply_stage"]
