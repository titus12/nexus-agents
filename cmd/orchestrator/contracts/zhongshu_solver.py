"""Machine contract for the Zhongshu Solver state."""
from __future__ import annotations

from .common import array, make_contract, object_schema, string


_ACTIONS = ("READY_FOR_CRITIC", "REQUEST_ANALYST_EVIDENCE", "NEEDS_MORE_EVIDENCE", "HUMAN_GATE", "BLOCKED")
_MODES = ("TASK_GRAPH_FORMALIZATION_READ_ONLY", "TASK_GRAPH_FORMALIZATION_READ_ONLY_RESUME")
_TASK = object_schema(
    {
        "item_id": string(), "title": string(), "objective": string(),
        "source_requirement_ids": array(string()), "dependencies": array(string()),
        "acceptance_signals": array(string()), "unknowns": array(),
        "risks": array(), "parallelizable": {"type": "boolean"},
        "rationale": string(), "benefit": string(), "tradeoffs": string(),
    },
    required=("item_id", "title", "objective", "source_requirement_ids", "dependencies", "acceptance_signals", "unknowns", "risks", "parallelizable", "rationale", "benefit"),
)
_REQUIREMENT = object_schema(
    {
        "requirement_id": string(),
        "statement": string(),
        "source": string(),
        "priority": string(enum=("must", "should", "could")),
        "scope": string(enum=("in", "out", "conditional")),
        "kind": string(enum=("task", "constraint")),
        "acceptance_signal": string(),
    },
    required=("requirement_id", "statement", "source", "priority", "scope", "kind", "acceptance_signal"),
)
_GROUP = object_schema(
    {
        "group_id": string(),
        "title": string(),
        "objective": string(),
        "item_ids": array(string()),
    },
    required=("group_id", "item_ids"),
    forbidden=("items",),
)
_PLAN = object_schema(
    {
        "requirements": array(_REQUIREMENT),
        "items": array(_TASK),
        "groups": array(_GROUP),
        "dependencies": array(),
        "scope": object_schema(nullable=True),
        "unknown_requirement_ids": array(string()),
        "unknowns": array(),
        "risks": array(),
    },
    required=("requirements", "items", "groups", "dependencies", "scope", "unknowns", "risks"),
)
_FINDING_RESOLUTION = object_schema(
    {"finding_id": string(), "response": string(), "changed_fields": array(string()), "evidence": array(), "owner_role": string(), "next_action": string()},
    required=("finding_id", "response", "changed_fields", "evidence", "owner_role", "next_action"),
)
_FINDING_BATCH = object_schema(
    {"selected_finding_ids": array(string()), "remaining_finding_ids": array(string()), "next_action": string(), "progress": object_schema()},
    required=("selected_finding_ids", "remaining_finding_ids", "next_action", "progress"),
)
_EVIDENCE_REQUEST = object_schema(
    {"item_id": {"type": ["string", "null"]}, "requirement_id": string(), "question": string(), "reason": string()},
    required=("item_id", "requirement_id", "question", "reason"),
)

FIELDS = {
    "summary": string(), "plan": {**_PLAN, "type": ["object", "null"]},
    "changes": array(), "finding_resolutions": array(_FINDING_RESOLUTION), "finding_batch": object_schema(_FINDING_BATCH["properties"], required=_FINDING_BATCH["required"], nullable=True),
    "dependencies": array(), "scope": object_schema(nullable=True), "unknowns": array(), "risks": array(),
    "unknown_resolutions": array(), "questions_for_user": array(), "next_actions": array(), "evidence_requests": array(_EVIDENCE_REQUEST),
}

CONTRACT = make_contract(
    contract_id="nexus.zhongshu.solver.v1", state="ZHONGSHU_SOLVER", phase="ZHONGSHU", role="review-solver",
    modes=_MODES, actions=_ACTIONS, fields=FIELDS,
    prompt_rules=(
        "Own task graph quality, dependencies, grouping, acceptance, unknowns, and risks.",
        "plan.requirements is the immutable Analyst contract; use only the supplied requirement_id values and put evidence-derived work in plan.items.",
        "Analyst candidate_items and candidate_groups are empty in the current evidence-only handoff; Solver creates the formal task graph from evidence.",
        "Dependencies are one-way execution prerequisites only. The complete plan.items dependency graph must be a directed acyclic graph: direct and transitive cycles are invalid, and related-but-not-blocking work must not be encoded as depends_on.",
        "plan.items is the only complete task-object index. Every group must use item_ids to reference plan.items; never emit groups[*].items or duplicate task objects.",
        "Do not emit implementation-level design fields forbidden by the solver protocol.",
        "Every finding resolution must identify its owner and next action.",
    ),
    example_overrides={
        "action": "READY_FOR_CRITIC",
        "mode": "TASK_GRAPH_FORMALIZATION_READ_ONLY",
        "plan": {
            "requirements": [],
            "items": [],
            "groups": [],
            "dependencies": [],
            "scope": {},
            "unknowns": [],
            "risks": [],
        },
    },
)
