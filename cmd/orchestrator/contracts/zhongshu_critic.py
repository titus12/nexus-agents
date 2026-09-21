"""Machine contract for the Zhongshu Critic state."""
from __future__ import annotations

from .common import array, make_contract, object_schema, string


_ACTIONS = ("APPROVE_FREEZE", "REQUEST_ANALYST_EVIDENCE", "REQUEST_SOLVER_REVISION", "REQUEST_REGROUP", "HUMAN_GATE", "BLOCKED", "TASK_APPROVED", "TASK_CHANGES_REQUIRED", "REQUEST_TASK_DISCARD")
_MODES = ("REVIEW_CURRENT_TASK_GRAPH", "REVIEW_ONE_TASK")
_FINDING = object_schema(
    {"finding_id": string(), "category": string(), "target": string(), "claim": string(), "decision": string(), "severity": string(), "evidence_strength": string(), "evidence_ids": array(string()), "required_action": string()},
    required=("finding_id", "category", "target", "claim", "decision", "severity", "evidence_strength"),
)
_REVIEW_CHECKS = object_schema(
    {"requirement_coverage": array(), "boundary": array(), "dependencies": array(), "acceptance": array(), "risks": array()},
    required=("requirement_coverage", "boundary", "dependencies", "acceptance", "risks"),
)
_FINDING_RESPONSE = object_schema(
    {"finding_id": string(), "response": string(), "note": string()},
    required=("finding_id", "response"),
)
FIELDS = {
    "summary": string(), "review_summary": string(), "worker_lens": string(), "findings": array(_FINDING),
    "finding_responses": array(_FINDING_RESPONSE),
    "requirement_coverage": array(), "evidence_alignment": array(), "missing_evidence": array(), "required_change": array(),
    "remaining_blockers": array(), "questions_for_solver": array(), "questions_for_analyst": array(), "questions_for_user": array(),
    "group_id": string(), "item_id": string(),
    "review_checks": _REVIEW_CHECKS, "evidence_ids": array(string()), "unknowns": array(),
}
CONTRACT = make_contract(
    contract_id="nexus.zhongshu.critic.v1", state="ZHONGSHU_CRITIC", phase="ZHONGSHU", role="review-critic",
    modes=_MODES, actions=_ACTIONS, fields=FIELDS,
    prompt_rules=(
        "Review only the supplied graph or task.",
        "Do not echo revision ids, plan hashes or task/dependency hashes: "
        "the orchestrator stamps those fields from the dispatch record.",
        "Every finding must be traceable to an evidence-backed claim.",
        "A signal claiming a measured outcome must embed「验证方法：」with all five "
        "recipe elements (target with file:line, exact steps, metric+unit, baseline "
        "source, expected observation); a recipe missing elements is rejected with "
        "the missing elements named.",
        "A claim that cannot be verified within this run closes with "
        "decision=WONT_VERIFY and the verification recipe in the resolution text; "
        "do not keep it blocking for more rounds.",
        "「不可验证」是 WONT_VERIFY 理由，不是丢弃理由： REQUEST_TASK_DISCARD 只能基于 "
        "「该任务对本次审查问题不重要」，并论证其服务的 requirement。",
        "Delta disposition rule: dispatch_context.active_findings lists the "
        "canonical findings already recorded for this task. Every active P0 "
        "finding MUST be explicitly dispositioned: re-raise it in findings "
        "with its canonical finding_id (STILL_OPEN), or answer it in "
        "finding_responses with response=RESOLVED (name what changed, with "
        "evidence) or response=ACCEPT (accept the risk and let the run "
        "proceed; put the follow-up plan in note). A P0 left silent is a "
        "contract violation and the reply is rejected. P1 findings follow the "
        "same rule but silence on a P1 is treated as resolved-by-approval.",
        "Language gate: write every finding claim in ONE language (Chinese or "
        "English) and keep it identical to the canonical claim's language for "
        "restated findings. Mixing Chinese and English across rounds is a "
        "protocol violation.",
        "Direction rule: the review exists to steer the draft toward a correct "
        "task direction, not only to reject it. Every finding on a "
        "TASK_CHANGES_REQUIRED verdict MUST carry required_action: one concrete, "
        "minimal modification suggestion naming the item field to change "
        "(objective, acceptance_signals, dependencies, or source_requirement_ids) "
        "and the direction to move it. State the correction you would accept as "
        "RESOLVED; do not restate the complaint as the action.",
    ),
    example_overrides={"action": "APPROVE_FREEZE", "mode": "REVIEW_CURRENT_TASK_GRAPH", "review_checks": {"requirement_coverage": [], "boundary": [], "dependencies": [], "acceptance": [], "risks": []}},
)
