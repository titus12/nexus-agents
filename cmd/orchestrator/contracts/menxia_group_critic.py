"""Machine contract for the Menxia group Critic state."""
from __future__ import annotations

from .common import array, make_contract, string


_ACTIONS = ("APPROVE_GROUP", "REQUEST_SOLVER_REVISION", "REQUEST_EVIDENCE", "HUMAN_GATE", "BLOCKED")
_MODES = ("GROUP_PLAN_REVIEW",)
FIELDS = {
    "summary": string(),
    "reviewed_version": {"type": "integer"},
    "reviewed_hash": string(),
    "doc_markdown": string(),
    "added_suggestion_ids": array(string()),
    "required_changes": array(),
    "verification_plan": array(),
    "remaining_risks": array(),
    "questions_for_user": array(),
}
CONTRACT = make_contract(
    contract_id="nexus.menxia.group_critic.v1", state="MENXIA_GROUP_CRITIC", phase="MENXIA", role="review-critic",
    modes=_MODES, actions=_ACTIONS, fields=FIELDS,
    prompt_rules=(
        "Attack the group document's body hunks at their anchors: call sites, error paths, convention conflicts.",
        "doc_markdown must be the reviewed version with your suggestions appended to the suggestion section only; never touch the body, ledger, or sign-off sections.",
        "Every suggestion must use the canonical line format: [S-NNN][critic][vN base][P0-P3][open] followed by the actionable body.",
        "Route P0/P1 gaps to REQUEST_SOLVER_REVISION; route missing-evidence gaps to REQUEST_EVIDENCE so the Analyst re-investigates first.",
        "Approve only when the suggestion section holds no open P0/P1 of your own and prior rounds are settled by id.",
        "State a falsification plan: which test would expose the plan if wrong; an unfalsifiable plan is a P1 suggestion.",
    ),
    example_overrides={"action": "APPROVE_GROUP", "mode": "GROUP_PLAN_REVIEW", "reviewed_version": 1, "reviewed_hash": "hash", "doc_markdown": "# doc [v1]"},
)
