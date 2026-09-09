"""Machine contract for the Menxia item Critic state."""
from __future__ import annotations

from .common import array, make_contract, object_schema, string


_ACTIONS = ("APPROVE_ITEM", "REVISE_ITEM", "SPLIT_ITEM", "MERGE_ITEM", "REMOVE_ITEM", "REQUEST_SOLVER_REVISION", "HUMAN_GATE", "BLOCKED")
_MODES = ("ITEM_OR_GROUP_REVIEW",)
_FINDING = object_schema({"finding_id": string(), "claim": string(), "severity": string(), "evidence_ids": array(string())}, required=("finding_id", "claim", "severity"))
FIELDS = {
    "summary": string(), "review_summary": string(), "decision": string(), "findings": array(_FINDING), "score": object_schema(nullable=True), "remaining_risks": array(),
    "item_review": object_schema(nullable=True), "group_review": object_schema(nullable=True), "group_consistency": object_schema(nullable=True), "required_changes": array(), "verification_plan": array(), "rollback_plan": object_schema(nullable=True),
    "questions_for_user": array(), "next_actions": array(),
}
CONTRACT = make_contract(
    contract_id="nexus.menxia.item_critic.v1", state="MENXIA_ITEM_CRITIC", phase="MENXIA", role="review-critic",
    modes=_MODES, actions=_ACTIONS, fields=FIELDS,
    prompt_rules=("Review the current item or group only.", "Every required change must be observable through verification_plan."),
    example_overrides={"action": "APPROVE_ITEM", "mode": "ITEM_OR_GROUP_REVIEW", "decision": "APPROVE"},
)
