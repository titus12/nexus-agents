"""Machine contract for the Menxia item Solver state."""
from __future__ import annotations

from .common import array, make_contract, object_schema, string


_ACTIONS = ("FEASIBLE", "READY_FOR_ANALYST", "READY_FOR_CRITIC", "HUMAN_GATE", "BLOCKED")
_MODES = ("ITEM_IMPLEMENTATION_PROPOSAL",)
_PROPOSAL = object_schema({"objective": string(), "approach": string(), "files": array(), "changes": array(), "tests": array(), "verification": object_schema(nullable=True), "rollback": object_schema(nullable=True)}, required=("objective", "approach", "files", "changes", "tests"))
FIELDS = {
    "summary": string(), "implementation_proposal": object_schema(_PROPOSAL["properties"], required=_PROPOSAL["required"], nullable=True),
    "responses_to_critic": array(), "files": array(), "changes": array(), "tests": array(), "verification": object_schema(nullable=True), "rollback": object_schema(nullable=True),
    "next_actions": array(), "unknowns": array(), "risks": array(), "questions_for_user": array(),
}
CONTRACT = make_contract(
    contract_id="nexus.menxia.item_solver.v1", state="MENXIA_ITEM_SOLVER", phase="MENXIA", role="review-solver",
    modes=_MODES, actions=_ACTIONS, fields=FIELDS,
    prompt_rules=("Propose implementation for the current item only.", "Keep files, changes, tests, verification, and rollback explicit."),
    example_overrides={"action": "READY_FOR_CRITIC", "mode": "ITEM_IMPLEMENTATION_PROPOSAL", "implementation_proposal": {"objective": "objective", "approach": "approach", "files": [], "changes": [], "tests": []}},
)
