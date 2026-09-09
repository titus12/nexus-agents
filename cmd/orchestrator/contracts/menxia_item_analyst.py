"""Machine contract for the Menxia item Analyst state."""
from __future__ import annotations

from .common import array, make_contract, object_schema, string


_ACTIONS = ("EVIDENCE_SUFFICIENT", "NEEDS_MORE_EVIDENCE", "REQUEST_SOLVER_REVISION", "HUMAN_GATE", "BLOCKED")
_MODES = ("ITEM_EVIDENCE_REVIEW",)
_EVIDENCE = object_schema({"evidence_id": string(), "statement": string(), "source": string()}, required=("evidence_id", "statement", "source"))
FIELDS = {
    "summary": string(), "assessment": string(), "requirement_trace": object_schema(nullable=True), "confirmed_facts": array(), "evidence": array(_EVIDENCE),
    "current_behavior": array(), "existing_capabilities": array(), "dependencies_verified": array(), "missing_evidence": array(), "conflicts": array(), "unknowns": array(),
    "questions_for_solver": array(), "questions_for_user": array(),
}
CONTRACT = make_contract(
    contract_id="nexus.menxia.item_analyst.v1", state="MENXIA_ITEM_ANALYST", phase="MENXIA", role="review-analyst",
    modes=_MODES, actions=_ACTIONS, fields=FIELDS,
    prompt_rules=("Verify evidence for the current item only.", "Separate verified facts, unknowns, and missing evidence."),
    example_overrides={"action": "EVIDENCE_SUFFICIENT", "mode": "ITEM_EVIDENCE_REVIEW"},
)
