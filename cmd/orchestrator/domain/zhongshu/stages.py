"""Explicit stage boundaries for the Zhongshu loop.

The Solver used to be a single code path whose behaviour was inferred from data
("does a plan happen to exist?") and whose dispatch label claimed ``revision``
even for the first formalization.  Both roles now carry an explicit stage that
the FSM edge decides, so the two input channels are isolated in code, not just
in payload shape.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping

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
    """

    mode = str((payload or {}).get("mode") or "").strip().upper()
    if mode.endswith("_RESUME"):
        return SolverStage.REVISE
    if mode.endswith("_READ_ONLY"):
        return SolverStage.FORMALIZE
    plan = getattr(review, "plan", None)
    if isinstance(plan, Mapping):
        return SolverStage.REVISE
    return SolverStage.FORMALIZE


__all__ = ["SolverStage", "resolve_dispatch_stage", "resolve_reply_stage"]
