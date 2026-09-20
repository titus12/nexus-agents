"""Machine contract for the Zhongshu freeze-check state."""
from __future__ import annotations

from .common import make_contract
from .zhongshu_critic import FIELDS

# The freeze check shares the critic fan-in (merge_critic_reviews), so its
# worker-facing action vocabulary is the subset of the critic's that the
# aggregation can emit and the FSM can route: approvals, evidence requests,
# revisions, and operator escalations.
_ACTIONS = (
    "APPROVE_FREEZE", "REQUEST_ANALYST_EVIDENCE", "REQUEST_SOLVER_REVISION",
    "HUMAN_GATE", "BLOCKED",
)
_MODES = ("REVIEW_CURRENT_TASK_GRAPH",)

CONTRACT = make_contract(
    contract_id="nexus.zhongshu.freeze_check.v1",
    state="ZHONGSHU_FREEZE_CHECK",
    phase="ZHONGSHU",
    role="review-critic",
    modes=_MODES,
    actions=_ACTIONS,
    fields=FIELDS,
    prompt_rules=(
        "Decide whether the reviewed plan is ready to freeze: every task "
        "approved, no open P0/P1 finding, evidence aligned with the plan.",
        "Return APPROVE_FREEZE only when no active P0/P1 finding applies.",
        "If the run lacks the investigation needed to judge the freeze, return "
        "REQUEST_ANALYST_EVIDENCE; if the plan itself needs edits before it can "
        "freeze, return REQUEST_SOLVER_REVISION.",
        "Do not echo revision ids, plan hashes or task/dependency hashes: "
        "the orchestrator stamps those fields from the dispatch record.",
    ),
    example_overrides={
        "action": "APPROVE_FREEZE", "mode": "REVIEW_CURRENT_TASK_GRAPH",
        "review_checks": {"requirement_coverage": [], "boundary": [], "dependencies": [], "acceptance": [], "risks": []},
    },
)
