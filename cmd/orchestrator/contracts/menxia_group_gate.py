"""Machine contract for the Menxia group gate state."""
from __future__ import annotations

from .common import array, make_contract, object_schema, string


_ACTIONS = ("APPROVE_GROUP", "APPROVE_FREEZE", "REQUEST_GROUP_REVISION", "HUMAN_GATE", "BLOCKED")
_MODES = ("ITEM_OR_GROUP_REVIEW",)
_ITEM_DECISION = object_schema({"item_id": string(), "action": string(), "summary": string(), "blockers": array()}, required=("item_id", "action", "summary", "blockers"))
FIELDS = {
    "summary": string(), "review_summary": string(), "decision": string(), "findings": array(), "score": object_schema(nullable=True), "remaining_risks": array(),
    "item_review": object_schema(nullable=True), "group_review": object_schema(nullable=True), "group_consistency": object_schema({"item_decisions": array(_ITEM_DECISION), "consistent": {"type": "boolean"}}, required=("item_decisions", "consistent"), nullable=True),
    "required_changes": array(), "verification_plan": array(), "rollback_plan": object_schema(nullable=True), "questions_for_user": array(), "next_actions": array(),
}
CONTRACT = make_contract(
    contract_id="nexus.menxia.group_gate.v1", state="MENXIA_GROUP_GATE", phase="MENXIA", role="review-critic",
    modes=_MODES, actions=_ACTIONS, fields=FIELDS,
    prompt_rules=("Gate the complete group only after item decisions are present.", "Keep consistency, blockers, and next actions explicit."),
    example_overrides={"action": "APPROVE_GROUP", "mode": "ITEM_OR_GROUP_REVIEW", "decision": "APPROVE"},
)
