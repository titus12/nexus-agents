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
        "Suggestions must be code-level actionable so Solver can translate them one-to-one into changes.",
        "added_suggestion_ids must list exactly the ids you appended.",
        "Settle prior suggestions by id: still-open means you re-affirm it, absorbed means the body change satisfies it.",
    ),
    example_overrides={"action": "EVIDENCE_SUFFICIENT", "mode": "GROUP_PLAN_REVIEW", "reviewed_version": 1, "reviewed_hash": "hash", "doc_markdown": "# doc [v1]"},
)
