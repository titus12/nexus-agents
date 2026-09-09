"""Machine contract for the Zhongshu Critic state."""
from __future__ import annotations

from .common import array, make_contract, object_schema, string


_ACTIONS = ("APPROVE_FREEZE", "REQUEST_ANALYST_EVIDENCE", "REQUEST_SOLVER_REVISION", "REQUEST_REGROUP", "HUMAN_GATE", "BLOCKED", "TASK_APPROVED", "TASK_CHANGES_REQUIRED")
_MODES = ("REVIEW_CURRENT_TASK_GRAPH", "REVIEW_ONE_TASK")
_FINDING = object_schema(
    {"finding_id": string(), "category": string(), "target": string(), "claim": string(), "decision": string(), "severity": string(), "evidence_strength": string(), "evidence_ids": array(string())},
    required=("finding_id", "category", "target", "claim", "decision", "severity", "evidence_strength"),
)
_REVIEW_CHECKS = object_schema(
    {"requirement_coverage": array(), "boundary": array(), "dependencies": array(), "acceptance": array(), "risks": array()},
    required=("requirement_coverage", "boundary", "dependencies", "acceptance", "risks"),
)
FIELDS = {
    "summary": string(), "review_summary": string(), "plan_hash": string(), "reviewed_plan_hash": string(),
    "revision_id": string(), "plan_revision_id": string(), "worker_lens": string(), "findings": array(_FINDING),
    "requirement_coverage": array(), "evidence_alignment": array(), "missing_evidence": array(), "required_change": array(),
    "remaining_blockers": array(), "questions_for_solver": array(), "questions_for_analyst": array(), "questions_for_user": array(),
    "group_id": string(), "item_id": string(), "reviewed_task_hash": string(), "reviewed_dependency_hash": string(),
    "review_checks": _REVIEW_CHECKS, "evidence_ids": array(string()), "unknowns": array(),
}
CONTRACT = make_contract(
    contract_id="nexus.zhongshu.critic.v1", state="ZHONGSHU_CRITIC", phase="ZHONGSHU", role="review-critic",
    modes=_MODES, actions=_ACTIONS, fields=FIELDS,
    prompt_rules=("Review only the supplied graph or task.", "Every finding must be traceable to an evidence-backed claim."),
    example_overrides={"action": "APPROVE_FREEZE", "mode": "REVIEW_CURRENT_TASK_GRAPH", "review_checks": {"requirement_coverage": [], "boundary": [], "dependencies": [], "acceptance": [], "risks": []}},
)
