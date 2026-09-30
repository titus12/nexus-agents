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
# Verbatim transcription of the lines an evidence entry cites.  The transport
# gate (orchestrator.quote_gate) demotes file-citing entries without one, or
# whose quoted text is not in the cited file, to unverified evidence so
# paraphrased or invented code behavior cannot pass as verified
# (task-20260929-c261a8 problem A).
_QUOTE = object_schema(
    {
        "path": string(),
        "line_start": {"type": ["integer", "null"]},
        "line_end": {"type": ["integer", "null"]},
        "text": string(),
    },
    required=("text",),
)
_QUOTE = {
    **_QUOTE,
    "description": (
        "One contiguous span of the cited lines, transcribed verbatim: at "
        "most 30 lines / 4096 chars, no ellipsis, no skipped lines. Evidence "
        "about separate spots needs separate entries; an oversized or "
        "non-verbatim quote marks its entry [UNVERIFIED-QUOTE]."
    ),
}
_EVIDENCE_UPDATE = object_schema(
    {
        "evidence_id": string(), "requirement_id": string(),
        "decision_relevance": string(
            enum=("boundary", "coverage", "dependency", "acceptance", "risk"),
            aliases=_DECISION_RELEVANCE_ALIASES,
            fallback="coverage",
        ),
        "source": string(), "quote": _QUOTE, "conclusion": string(), "unknowns": array(),
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
        "quote": _QUOTE,
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
        "When a finding demands a requirement's original text, quote the "
        "requirement statement, priority and scope verbatim inside the answer "
        "itself. When citing the contract by reference, use the exact anchor "
        "`requirement_contract[<requirement_id>]`: that array ships in every "
        "ZHONGSHU role's context.json, so the reader verifies it there. Never "
        "cite a file or field you did not actually open.",
        "Every evidence_update or finding_response whose source cites a file "
        "must carry a verbatim `quote` ({path, line_start, line_end, text}) "
        "transcribing the exact cited lines: transcribe first, then interpret, "
        "and never describe code behavior the quoted text does not show. The "
        "quote must be one contiguous span copied character-for-character: no "
        "ellipsis, no skipped or paraphrased lines, at most 30 lines and 4096 "
        "characters; when the evidence points at several separate spots, emit "
        "one entry per spot, each with its own small quote. The transport gate "
        "marks an entry [UNVERIFIED-QUOTE] and strips its CLOSE suggestion when "
        "it cites a file without a quote (QUOTE_REQUIRED), quotes text absent "
        "from the cited file (QUOTE_MISMATCH), or exceeds that limit "
        "(QUOTE_OVERSIZE); such entries count as unverified evidence.",
        "Evidence conclusions must state what the code or document contains, "
        "with file:line sources. Engineering recommendations ('X should be "
        "added') are not evidence and will be rejected as unsupported claims.",
        "Return the complete envelope even when an array is empty.",
    ),
    example_overrides={
        "action": "EVIDENCE_PACKET_READY", "mode": "EVIDENCE_COLLECTION_READ_ONLY",
        "evidence_updates": [{
            "evidence_id": "ev-1", "requirement_id": "REQ-001",
            "decision_relevance": "coverage", "source": "a.py:1",
            "quote": {"path": "a.py", "line_start": 1, "line_end": 2,
                      "text": "def handler():\n    return verify(x)"},
            "conclusion": "verified", "unknowns": [],
        }],
    },
)
