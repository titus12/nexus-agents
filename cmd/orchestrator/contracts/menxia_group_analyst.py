"""Machine contract for the Menxia group Analyst state."""
from __future__ import annotations

from .common import array, make_contract, string


_ACTIONS = ("EVIDENCE_SUFFICIENT", "READY_FOR_CRITIC", "NEEDS_MORE_EVIDENCE", "REQUEST_SOLVER_REVISION", "HUMAN_GATE", "BLOCKED")
_MODES = ("GROUP_PLAN_REVIEW",)
FIELDS = {
    "summary": string(),
    "reviewed_version": {"type": "integer"},
    "reviewed_hash": string(),
    "doc_markdown": string(),
    "added_suggestion_ids": array(string()),
    "confirmed_rejection_ids": array(string()),
    "reaffirmed_rejection_ids": array(string()),
    "confirmed_facts": array(),
    "missing_evidence": array(),
    "unknowns": array(),
    "questions_for_user": array(),
}
CONTRACT = make_contract(
    contract_id="nexus.menxia.group_analyst.v1", state="MENXIA_GROUP_ANALYST", phase="MENXIA", role="review-analyst",
    modes=_MODES, actions=_ACTIONS, fields=FIELDS,
    prompt_rules=(
        "Review the group document against the original requirement before judging the body.",
        "doc_markdown must be the reviewed version with your suggestions appended to the suggestion section only; never touch the body, ledger, or sign-off sections.",
        "Every suggestion must use the canonical line format: [S-NNN][analyst][vN base][P0-P3][open] followed by the actionable body.",
        "Suggestions must be scope-specific and actionable: code anchors for implementation designs, evidence or conclusion anchors for review-only requests. Do not expand a review request into implementation work.",
        "added_suggestion_ids must list exactly the ids you appended.",
        "Keep reviewed_version and reviewed_hash equal to the dispatched version/hash. The orchestrator owns sign-off.",
        "Use confirmed_rejection_ids to accept Solver's rejection of your own pending suggestions, or reaffirmed_rejection_ids to insist. Keep the document records unchanged; the orchestrator applies these decisions.",
        "Settle prior suggestions by id: still-open means you re-affirm it, absorbed means the body change satisfies it.",
    ),
    example_overrides={"action": "EVIDENCE_SUFFICIENT", "mode": "GROUP_PLAN_REVIEW", "reviewed_version": 1, "reviewed_hash": "hash", "doc_markdown": "# doc [v1]"},
)
