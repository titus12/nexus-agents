"""Machine contract for the Menxia group Solver state."""
from __future__ import annotations

from .common import array, make_contract, string


_ACTIONS = ("FEASIBLE", "READY_FOR_ANALYST", "READY_FOR_CRITIC", "HUMAN_GATE", "BLOCKED")
_MODES = ("GROUP_PLAN_DOCUMENT",)
_DOC_FIELDS = {
    "doc_version": {"type": "integer"},
    "doc_hash": string(),
    "doc_markdown": string(),
}
FIELDS = {
    "summary": string(),
    **_DOC_FIELDS,
    "absorbed_ids": array(string()),
    "rejected_ids": array(string()),
    "touched_scope": array(string()),
    "responses_to_reviewers": array(),
    "unknowns": array(),
    "risks": array(),
    "questions_for_user": array(),
}
CONTRACT = make_contract(
    contract_id="nexus.menxia.group_solver.v1", state="MENXIA_GROUP_SOLVER", phase="MENXIA", role="review-solver",
    modes=_MODES, actions=_ACTIONS, fields=FIELDS,
    prompt_rules=(
        "Own the plan body of the group document; every round produces exactly one new version.",
        "doc_markdown must be the complete new document: update the version in the title and the plan body, keep the suggestion, ledger, and sign-off sections intact.",
        "Absorb a suggestion only by actually changing the body, then list its id in absorbed_ids; the ledger records it.",
        "Decline a suggestion by replying inline under it, then list its id in rejected_ids; P0/P1 rejections stay visible until their author confirms.",
        "Never delete or edit an existing suggestion line, ledger line, or sign-off line.",
        "List every body section you changed in touched_scope; undeclared changes are rejected mechanically.",
    ),
    example_overrides={"action": "READY_FOR_ANALYST", "mode": "GROUP_PLAN_DOCUMENT", "doc_version": 2, "doc_hash": "hash", "doc_markdown": "# doc [v2]"},
)
