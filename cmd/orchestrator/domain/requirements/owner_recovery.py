"""Narrow operator repair, never an owner reassignment or approval."""


def validate_owner_recovery(context, actor_ids):
    state = context.requirements
    failure = state.recovery.failure if state and state.recovery else None
    if (context.request.communication != 'feishu' or context.request.decision_actor_ids
        or context.progression.state != 'REQ_BLOCKED' or not failure
        or failure.code != 'PREREQUISITE_MISSING' or failure.artifact_ref != 'communication'
        or 'decision_actor_ids' not in failure.message
        or context.progression.resume_state != 'REQ_CLARIFY_PUBLISH'
        or state.answers or state.approval or state.pending_approval or state.review_gate_id
        or any(r.status != 'preparing' or r.actor_ids for r in state.rounds)):
        raise ValueError('OWNER_REPAIR_REQUIRES_EMPTY_UNPUBLISHED_BLOCKED_TASK')
    if (not actor_ids or len(set(actor_ids)) != len(actor_ids)
        or any(not isinstance(a,str) or not a.startswith('ou_') or not a[3:]
               or not a[3:].isascii() or not a[3:].isalnum() for a in actor_ids)):
        raise ValueError('VALID_UNIQUE_DECISION_OWNERS_REQUIRED')
