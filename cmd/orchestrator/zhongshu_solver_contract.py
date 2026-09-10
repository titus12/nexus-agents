from __future__ import annotations


ZHONGSHU_SOLVER_REQUIRED_ITEM_FIELDS = (
    "item_id",
    "title",
    "objective",
    "source_requirement_ids",
    "dependencies",
    "acceptance_signals",
    "unknowns",
    "risks",
    "parallelizable",
    "rationale",
    "benefit",
)

ZHONGSHU_SOLVER_FORBIDDEN_FIELDS = frozenset(
    {
        "implementation_proposal",
        "file_changes",
        "code_changes",
        "function_changes",
        "implementation_steps",
        "interfaces",
        "data_flow",
        "control_flow",
        "migration",
        "rollback",
        "affected_modules",
    }
)

# Findings are atomic review conclusions.  Keep each revision bounded by a
# deterministic count; never truncate one Finding by character budget.
ZHONGSHU_SOLVER_MAX_FINDINGS_PER_ROUND = 6


def zhongshu_solver_runtime_rules() -> tuple[str, ...]:
    return (
        "Solver owns task-graph quality: coverage, task boundaries, dependencies, grouping, parallelism, acceptance, scope, unknowns, and risks.",
        "Dependencies are allowed, but they are directed prerequisites only: A dependency means the referenced task must finish before the current task. The complete task graph must be a DAG. Direct cycles, transitive cycles, and dependency edges that merely mean related-to are forbidden. Before READY_FOR_CRITIC, perform a topological preflight over every plan.items dependency.",
        "Preserve every Analyst requirement_id and statement exactly; do not invent, rename, delete, or silently reinterpret requirements.",
        "plan.requirements is an exact copy of the upstream Analyst requirement contract. Evidence, inferred opportunities, optimizations, and task decomposition belong in plan.items and plan.groups, never in new requirement objects.",
        "The current Analyst handoff is evidence-only, so candidate_items and candidate_groups are empty by design. Create plan.items and plan.groups from the evidence; if a legacy candidate is present, preserve its item_id or account for it with source_candidate_ids when merging or splitting.",
        "Every task must be independently actionable, traceable to known requirements, dependency-valid, and observable through acceptance_signals.",
        "Use only Analyst-cited evidence and the current graph for targeted revalidation; do not perform a repository-wide investigation.",
        "If a graph decision cannot be supported by the supplied evidence, return NEEDS_MORE_EVIDENCE or HUMAN_GATE instead of guessing.",
        "For REQUEST_ANALYST_EVIDENCE or NEEDS_MORE_EVIDENCE, emit a non-empty evidence_requests array. Every request must name item_id or requirement_id and include a concrete question and reason; an unscoped request is invalid.",
        "Return one formal task graph for Critic inside the fixed Solver role envelope; write it to result_path using a real JSON serializer. Do not emit implementation_proposal, file_changes, code_changes, interfaces, data_flow, migration, rollback, or function-level design.",
        "Do not modify files, claim implementation, claim tests passed, or return a final approval decision.",
        "On the initial READY_FOR_CRITIC run, put one complete formal plan in the fixed Solver envelope. When current_formal_plan is supplied for a revision, work on one bounded finding batch and return bounded changes (changes=[] is a valid no-op), one finding_resolution for each selected finding, and a finding_batch that explicitly partitions every active finding into selected_finding_ids and remaining_finding_ids. The orchestrator materializes and validates the complete plan and carries the declared remainder to the next round. Return a full plan only when an unsupported topology change requires it; finding_resolutions never replaces both plan and changes. Always emit all fixed Solver fields, including evidence_requests, using [] or null when unused.",
        "Allowed revision changes are replace_item_fields, replace_group_items, replace_group_fields, and replace_plan_fields. Do not add or remove tasks or groups through an untyped free-form patch.",
        "For a full plan, groups MUST use item_ids as compact references to complete objects in plan.items; the orchestrator expands and validates them. Do not emit groups[*].items or duplicate task objects.",
        "Keep explicit unknown_requirement_ids and unknowns visible in the plan; unresolved requirements must not be converted into invented tasks.",
        f"For every unresolved finding_resolution include owner_role (review-analyst, review-solver, human) and next_action. Request Analyst only for specific missing facts; grouping and topology are Solver work. Unknown ownership needs HUMAN_GATE, not repeated investigation. In finding_batch, select only from the orchestrator-provided focus_finding_ids (at most {ZHONGSHU_SOLVER_MAX_FINDINGS_PER_ROUND} complete findings); selected_finding_ids and remaining_finding_ids must be disjoint, contain only the listed active finding IDs, and together cover them exactly; never omit an ID silently.",
        "Read the exact finding_id AND original claim before responding; never infer finding identity from ordering. Return response, changed_fields and evidence for resolutions.",
        "Define measurable future tasks without claiming the measurement or implementation is already complete. A request to review optimization opportunities does not authorize implementation.",
    )
