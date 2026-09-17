"""Business logic of the Zhongshu review loop, isolated per role and stage.

Modules:
- ``stages``  : which input channel a dispatch/reply belongs to;
- ``solver``  : the Solver's two channels (formalize / revise) and their dispatch;
- ``critic``  : verdict aggregation and the round gate.

The FSM states in ``domain.states`` are thin adapters: they resolve the stage,
call the logic here, and translate the result into decisions and effects.
"""

from .contracts import resolve_solver_stage, solver_contract_fragments
from .stages import SolverStage, resolve_dispatch_stage, resolve_reply_stage
from .solver import (
    FormalizeSolverLogic,
    ReviseSolverLogic,
    SolverBatch,
    SolverReplyOutcome,
    build_solver_dispatch,
    plan_artifact_effect,
    process_solver_reply,
    revision_finding_payload,
    revision_scope,
)
from .critic import (
    GateVerdict,
    ReviewRound,
    evaluate_gate,
    fold_round,
    mark_followups,
)

__all__ = [
    "FormalizeSolverLogic",
    "GateVerdict",
    "ReviewRound",
    "ReviseSolverLogic",
    "SolverBatch",
    "SolverReplyOutcome",
    "SolverStage",
    "build_solver_dispatch",
    "evaluate_gate",
    "fold_round",
    "mark_followups",
    "plan_artifact_effect",
    "process_solver_reply",
    "resolve_dispatch_stage",
    "resolve_reply_stage",
    "resolve_solver_stage",
    "revision_finding_payload",
    "revision_scope",
    "solver_contract_fragments",
]
