"""Per-stage response contracts for the Zhongshu Solver.

The stage is written into the dispatch context by ``build_solver_dispatch``,
so the transport can build the reply contract from it instead of inferring the
channel from payload shape (the old ``has_current_plan`` guess, which mislabelled
the first formalization whenever an Analyst-produced plan already existed).
"""

from __future__ import annotations

from typing import Any, Mapping

from ...zhongshu_solver_contract import ZHONGSHU_SOLVER_BATCH_RULE
from .stages import SolverStage


def resolve_solver_stage(context: Mapping[str, Any] | None) -> SolverStage:
    """Stage of a dispatched Solver request.

    The dispatcher writes ``solver_stage`` explicitly; legacy payloads predate
    that key and fall back to the previous ``has_current_plan`` inference.
    """

    stage = str((context or {}).get("solver_stage") or "").strip().lower()
    if stage == SolverStage.REVISE.value:
        return SolverStage.REVISE
    if stage == SolverStage.FORMALIZE.value:
        return SolverStage.FORMALIZE
    legacy = context or {}
    if legacy.get("solver_revision_mode") or legacy.get("has_current_plan"):
        return SolverStage.REVISE
    return SolverStage.FORMALIZE


def solver_contract_fragments(stage: SolverStage) -> dict[str, Any]:
    """The stage-specific reply-contract fragments.

    A revision may answer with bounded changes or, when the requested topology
    cannot be represented by a patch, with a full plan; state validation
    enforces that one of them materializes.  A formalization must carry the
    complete plan.  The finding-batch rule only exists on the revise channel:
    there is no batch to partition on the formalize channel.
    """

    if stage is SolverStage.REVISE:
        return {
            "finding_batch_rule": ZHONGSHU_SOLVER_BATCH_RULE,
            "required_by_action": {"READY_FOR_CRITIC": ["action"]},
        }
    return {
        "required_by_action": {"READY_FOR_CRITIC": ["action", "plan"]},
    }


__all__ = ["resolve_solver_stage", "solver_contract_fragments"]
