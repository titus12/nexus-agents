"""Machine contract for the Zhongshu Analyst state."""
from __future__ import annotations

from .common import array, contract_schema, make_contract, object_schema, string


_ACTIONS = (
    "READY_FOR_SOLVER", "EVIDENCE_PACKET_READY", "REQUIREMENT_CONTRACT_READY",
    "HUMAN_GATE", "BLOCKED",
)
_MODES = (
    "REQUIREMENT_CONTRACT_ONLY", "EVIDENCE_COLLECTION_READ_ONLY",
)

_REQUIREMENT = object_schema(
    {
        "requirement_id": string(), "statement": string(), "source": string(),
        "priority": string(enum=("must", "should", "could")),
        "scope": string(enum=("in", "out", "conditional")),
        "kind": string(enum=("task", "constraint")),
        "acceptance_signal": string(),
    },
    required=("requirement_id", "statement", "source", "priority", "scope", "kind", "acceptance_signal"),
)
# Models often describe evidence relevance with a risk-flavoured synonym or a
# typo instead of the exact enum.  Canonicalize rather than reject the whole
# worker reply (a rejection stalls the Analyst fan-out for the phase timeout).
_DECISION_RELEVANCE_ALIASES = {
    "performance": "risk",
    "compatibility": "risk",
    "regression": "risk",
    "safety": "risk",
    "security": "risk",
    "reliability": "risk",
    "stability": "risk",
    "robustness": "risk",
    "unknown": "risk",
    "unknowns": "risk",
    "uncertainty": "risk",
    "conflict": "risk",
    "conflicts": "risk",
    "depencency": "dependency",
    "dependancy": "dependency",
    "dependencies": "dependency",
    "scope": "boundary",
    "boundaries": "boundary",
    "completeness": "coverage",
    "verification": "acceptance",
    "verify": "acceptance",
}
_EVIDENCE_UPDATE = object_schema(
    {
        "evidence_id": string(), "requirement_id": string(),
        "decision_relevance": string(
            enum=("boundary", "coverage", "dependency", "acceptance", "risk"),
            aliases=_DECISION_RELEVANCE_ALIASES,
            fallback="coverage",
        ),
        "source": string(), "conclusion": string(), "unknowns": array(),
    },
    required=("evidence_id", "requirement_id", "decision_relevance", "source", "conclusion"),
    forbidden=("relevance",),
)
_EVIDENCE_REQUEST = object_schema(
    {
        "item_id": {"type": ["string", "null"]}, "requirement_id": string(),
        "question": string(), "reason": string(), "blocking": {"type": "boolean"},
    },
    required=("item_id", "requirement_id", "question", "reason", "blocking"),
)
# Answer channel: when the dispatch lists the Critic's active findings (the
# "[Evidence task]" block), the Analyst answers each finding with evidence and
# a suggested disposition instead of returning an undirected evidence dump.
_FINDING_RESPONSE = object_schema(
    {
        "finding_id": string(), "answer": string(), "evidence_ids": array(string()),
        "suggested_disposition": string(
            enum=("CLOSE", "REVISE", "DOWNGRADE", "NEEDS_RUNTIME_DATA"),
            fallback="REVISE",
        ),
        "note": string(),
    },
    required=("finding_id", "answer"),
)

FIELDS = {
    "summary": string(),
    "requirements": array(_REQUIREMENT),
    "task_proposals": array(max_items=0),
    "candidate_items": array(max_items=0),
    "candidate_groups": array(max_items=0),
    "evidence_updates": array(_EVIDENCE_UPDATE),
    "finding_responses": array(_FINDING_RESPONSE),
    "confirmed_facts": array(),
    "constraints": array(),
    "conflicts": array(),
    "unknowns": array(),
    "unknown_requirement_ids": array(),
    "unknown_resolutions": array(),
    "risks": array(),
    "scope": object_schema(nullable=True),
    "questions_for_solver": array(),
    "evidence_requests": array(_EVIDENCE_REQUEST),
    "questions_for_user": array(),
}

CONTRACT = make_contract(
    contract_id="nexus.zhongshu.analyst.v1",
    state="ZHONGSHU_ANALYST",
    phase="ZHONGSHU",
    role="review-analyst",
    modes=_MODES,
    actions=_ACTIONS,
    fields=FIELDS,
    prompt_rules=(
        "Collect evidence only; do not create or group implementation tasks.",
        "task_proposals, candidate_items, and candidate_groups must always be empty; Solver is the only task-graph producer.",
        "Every evidence update must include decision_relevance, exactly one of: "
        "boundary, coverage, dependency, acceptance, risk.",
        "Answer every finding listed in the [Evidence task] block: one "
        "finding_response per finding_id, whose answer cites file:line "
        "evidence for the demanded targets, references the evidence_ids that "
        "support it, and proposes suggested_disposition (CLOSE when the "
        "evidence resolves the claim, NEEDS_RUNTIME_DATA only when no file "
        "can prove it). The workspace is mounted read-only locally: locate "
        "files with local search, cite local paths, and never claim that git "
        "or remote access limits block the evidence.",
        "Return the complete envelope even when an array is empty.",
    ),
    example_overrides={
        "action": "EVIDENCE_PACKET_READY", "mode": "EVIDENCE_COLLECTION_READ_ONLY",
        "evidence_updates": [{
            "evidence_id": "ev-1", "requirement_id": "REQ-001",
            "decision_relevance": "coverage", "source": "a.py:1",
            "conclusion": "verified", "unknowns": [],
        }],
    },
)
