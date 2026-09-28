"""Machine contract for the Zhongshu Critic state."""
from __future__ import annotations

from .common import array, make_contract, object_schema, string


_ACTIONS = (
    "APPROVE_FREEZE",
    "REQUEST_ANALYST_EVIDENCE",
    "REQUEST_SOLVER_REVISION",
    "REQUEST_REGROUP",
    "HUMAN_GATE",
    "BLOCKED",
    "TASK_APPROVED",
    "TASK_CHANGES_REQUIRED",
    "REQUEST_TASK_DISCARD",
    "APPROVE_GROUP",
    "REVISE_GROUP",
)
_MODES = ("REVIEW_CURRENT_TASK_GRAPH", "REVIEW_ONE_TASK", "REVIEW_GROUP")

# The group review wave (2026-09-26 group pipeline) emits exactly one group
# verdict per job; item-level verdicts are gone and findings keep their item
# coordinates only for revision targeting and evidence routing.
GROUP_MODE_ACTIONS = (
    "APPROVE_GROUP",
    "REVISE_GROUP",
    "REQUEST_ANALYST_EVIDENCE",
    "HUMAN_GATE",
    "BLOCKED",
)

# The action enum is the union of every review mode's vocabulary, so a group
# reviewer may legally answer with a legacy whole-plan name (live run
# task-20260927-862584 answered REVISE_GROUP as REQUEST_SOLVER_REVISION and
# the wave was rejected).  Map the synonyms onto the closed group vocabulary
# instead of punishing vocabulary drift.
_GROUP_ACTION_SYNONYMS = {
    "APPROVE_GROUP": "APPROVE_GROUP",
    "APPROVE_CRITIC": "APPROVE_GROUP",
    "APPROVE_FREEZE": "APPROVE_GROUP",
    "TASK_APPROVED": "APPROVE_GROUP",
    "REVISE_GROUP": "REVISE_GROUP",
    "REQUEST_SOLVER_REVISION": "REVISE_GROUP",
    "TASK_CHANGES_REQUIRED": "REVISE_GROUP",
    "REQUEST_ANALYST_EVIDENCE": "REQUEST_ANALYST_EVIDENCE",
    "HUMAN_GATE": "HUMAN_GATE",
    "BLOCKED": "BLOCKED",
}


def normalize_group_action(action: object) -> str:
    """Map a worker's group verdict onto the closed REVIEW_GROUP vocabulary.

    Empty means the action is not a group verdict under any known name.
    """

    return _GROUP_ACTION_SYNONYMS.get(str(action or "").strip().upper(), "")


def allowed_actions_for_mode(mode: str) -> tuple[str, ...]:
    """Action allowlist for one review mode (group verdicts are closed)."""

    if str(mode or "").strip() == "REVIEW_GROUP":
        return GROUP_MODE_ACTIONS
    return _ACTIONS


def group_finding_scope(finding: object, known_item_ids: object) -> tuple[str, str]:
    """Resolve the owning item of one group-review finding.

    Returns ``(item_id, error)``: the explicit ``item_id`` wins, a free-text
    ``target`` is normalized through the shared resolver (the incident shape
    `group-000003/item-000004.acceptance_signals and task_review_ledger`), and
    a finding that names no member item is rejected as
    ``GROUP_FINDING_UNSCOPED:<finding_id>`` instead of drifting out of the
    revision scope.
    """

    from ..domain.findings import resolve_finding_item_id

    known = {
        str(value).strip()
        for value in (known_item_ids or ())
        if str(value).strip()
    }
    item_id = resolve_finding_item_id(finding, known or None)
    if not item_id or (known and item_id not in known):
        finding_id = ""
        if isinstance(finding, dict):
            finding_id = str(finding.get("finding_id") or "")
        else:
            finding_id = str(getattr(finding, "finding_id", "") or "")
        return "", f"GROUP_FINDING_UNSCOPED:{finding_id}"
    return item_id, ""
_FINDING = object_schema(
    {"finding_id": string(), "category": string(), "target": string(), "claim": string(), "decision": string(), "severity": string(), "evidence_strength": string(), "evidence_ids": array(string()), "required_action": string(), "evidence_targets": array(object_schema({"path": string(), "symbol": string()}, required=("path",))), "item_id": string(), "group_id": string()},
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
        "REVIEW_GROUP mode: review the whole group capsule and return exactly "
        "one group action (APPROVE_GROUP / REVISE_GROUP / "
        "REQUEST_ANALYST_EVIDENCE / HUMAN_GATE / BLOCKED). Every finding must "
        "carry the owning item_id of a member task; item_id is the coordinate "
        "for revision targeting, not a separate verdict.",
        "Do not echo revision ids, plan hashes or task/dependency hashes: "
        "the orchestrator stamps those fields from the dispatch record. "
        "The envelope field structured_output_schema_hash is the one exception: "
        "copy it verbatim from the injected contract.",
        "Every finding must be traceable to an evidence-backed claim.",
        "Every finding must name its owning task: set item_id to the exact "
        "plan item id (and group_id when the item belongs to one). target is "
        "display text, never the identity source.",
        "Review on four axes only: (1) requirement decidability -- every "
        "demand is checkable as written; (2) scenario coverage -- the "
        "acceptance assertions cover the requirement; (3) boundary clarity -- "
        "scope, non-goals and ownership leave no ambiguity; (4) metric "
        "consistency -- units and scopes agree with the §4 glossary. "
        "Missing measurement recipes are NOT findings (recipes belong to the "
        "Menxia execution phase); only a comparative numeric claim that is "
        "neither measured-with-source nor marked 待实测 is a finding.",
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
        "Evidence demand rule: a finding whose required_action demands "
        "source-level file:line evidence MUST carry evidence_targets: one "
        "entry per workspace file (path, plus symbol when applicable) the "
        "evidence must cite. Paths are relative to the mounted review "
        "workspace; runtime facts that no file can prove stay out of "
        "evidence_targets and close as WONT_VERIFY instead.",
        "Answered-finding rule: finding_responses from the analyst or solver "
        "arrive inside the evidence slice. A response backed by file:line "
        "evidence that covers the demanded targets MUST be explicitly "
        "dispositioned: accept it (close the finding with decision=RESOLVED, "
        "naming the response) or rebut it by naming the concrete defect with "
        "file:line. Re-requesting evidence the response already supplies is a "
        "protocol violation.",
    ),
    example_overrides={"action": "APPROVE_FREEZE", "mode": "REVIEW_CURRENT_TASK_GRAPH", "review_checks": {"requirement_coverage": [], "boundary": [], "dependencies": [], "acceptance": [], "risks": []}},
)
