"""Machine contract for the item-scoped Zhongshu Solver worker (A2/P3)."""
from __future__ import annotations

from .common import array, make_contract, object_schema, string

_ACTIONS = ("READY_FOR_CRITIC", "BLOCKED")
_MODES = ("TASK_ITEM_REVISION",)
_FINDING_RESOLUTION = object_schema(
    {
        "finding_id": string(),
        "response": string(),
        "changed_fields": array(string()),
        "evidence": array(string()),
    },
    required=("finding_id", "response"),
)
FIELDS = {
    "summary": string(),
    "item_id": string(),
    "group_id": string(),
    "item": object_schema(
        {
            "item_id": string(),
            "group_id": string(),
            "title": string(),
            "objective": string(),
            "dependencies": array(string()),
            "source_requirement_ids": array(string()),
            "acceptance_signals": array(string()),
            "unknowns": array(),
            "risks": array(),
        },
        required=("item_id", "group_id", "title", "objective", "acceptance_signals"),
    ),
    "finding_resolutions": array(_FINDING_RESOLUTION),
    "evidence_ids": array(string()),
    "unknowns": array(),
    "risks": array(),
}
CONTRACT = make_contract(
    contract_id="nexus.zhongshu.item_solver.v1",
    state="ZHONGSHU_SOLVER",
    phase="ZHONGSHU",
    role="review-solver",
    modes=_MODES,
    actions=_ACTIONS,
    fields=FIELDS,
    prompt_rules=(
        "Revise exactly the assigned item; every finding listed for it must get "
        "one finding_resolution.",
        "Rewrite acceptance signals observably: per-item verifiable, explicit "
        "measurement units, cited evidence sources, UNKNOWN for runtime facts.",
    ),
    example_overrides={
        "action": "READY_FOR_CRITIC",
        "mode": "TASK_ITEM_REVISION",
        "item": {
            "item_id": "item-000001",
            "group_id": "group-000001",
            "title": "unchanged",
            "objective": "revised objective",
            "acceptance_signals": ["observable signal with unit and source"],
        },
    },
)
