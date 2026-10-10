"""Closed graph for requirements, with an explicit Menxia continuation boundary."""
from ..transitions import TransitionRegistry

REQ_BUSINESS = ('REQ_INTAKE', 'REQ_ANALYZE', 'REQ_DRAFT', 'REQ_VALIDATE', 'REQ_REVIEW', 'REQ_EVIDENCE',
    'REQ_CLARIFY_PUBLISH', 'REQ_CLARIFY_WAIT', 'REQ_REVIEW_PUBLISH', 'REQ_CONFIRM_WAIT',
    'REQ_APPROVAL_VERIFY', 'REQ_PUBLISH', 'REQ_HANDOFF', 'MENXIA_REQUIREMENTS_INTAKE')
MENXIA = ('MENXIA_GROUP_SOLVER','MENXIA_GROUP_ANALYST','MENXIA_GROUP_CRITIC','MENXIA_GROUP_GATE')
SYSTEM = ('REQ_BLOCKED','REQ_ENGINE_ERROR','REQ_RETRY_WAIT','HUMAN_GATE','BLOCKED','RETRY_WAIT','PERSISTENCE_DEGRADED')
TERMINAL = ('REQ_CANCELLED','DONE','FAILED')
WAIT = ('REQ_CLARIFY_WAIT','REQ_CONFIRM_WAIT','REQ_BLOCKED','REQ_ENGINE_ERROR','HUMAN_GATE','BLOCKED','PERSISTENCE_DEGRADED')

EDGES = (
    ('REQ_INTAKE','START','REQ_ANALYZE'),
    ('REQ_ANALYZE','ANALYSIS_ACCEPTED','REQ_DRAFT'), ('REQ_ANALYZE','ANALYSIS_NEEDS_USER','REQ_CLARIFY_PUBLISH'),
    ('REQ_DRAFT','DRAFT_READY','REQ_VALIDATE'), ('REQ_DRAFT','NEEDS_CLARIFICATION','REQ_CLARIFY_PUBLISH'),
    ('REQ_DRAFT','NEEDS_EVIDENCE','REQ_EVIDENCE'), ('REQ_DRAFT','NEEDS_ANALYSIS','REQ_ANALYZE'),
    ('REQ_VALIDATE','VALID','REQ_REVIEW'), ('REQ_VALIDATE','REPAIR_CONTENT','REQ_DRAFT'),
    ('REQ_VALIDATE','NEEDS_EVIDENCE','REQ_EVIDENCE'),
    ('REQ_VALIDATE','FACTS_CHANGED','REQ_EVIDENCE'),
    ('REQ_REVIEW_PUBLISH','FACTS_CHANGED','REQ_EVIDENCE'),
    ('REQ_APPROVAL_VERIFY','FACTS_CHANGED','REQ_EVIDENCE'),
    ('REQ_REVIEW','REVIEW_PASSED','REQ_REVIEW_PUBLISH'), ('REQ_REVIEW','REVIEW_NEEDS_USER','REQ_CLARIFY_PUBLISH'),
    ('REQ_REVIEW','REVIEW_NEEDS_DRAFT','REQ_DRAFT'), ('REQ_REVIEW','REVIEW_NEEDS_EVIDENCE','REQ_EVIDENCE'),
    ('REQ_EVIDENCE','EVIDENCE_TO_DRAFT','REQ_DRAFT'), ('REQ_EVIDENCE','EVIDENCE_TO_REVIEW','REQ_REVIEW'),
    ('REQ_CLARIFY_PUBLISH','CARD_DELIVERED','REQ_CLARIFY_WAIT'),
    ('REQ_CLARIFY_WAIT','ANSWERS_TO_ANALYZE','REQ_ANALYZE'), ('REQ_CLARIFY_WAIT','ANSWERS_TO_DRAFT','REQ_DRAFT'),
    ('REQ_REVIEW_PUBLISH','REVIEW_DOCUMENT_DELIVERED','REQ_CONFIRM_WAIT'),
    ('REQ_CONFIRM_WAIT','REQUIREMENTS_APPROVED','REQ_APPROVAL_VERIFY'),
    ('REQ_APPROVAL_VERIFY','APPROVAL_VERIFIED','REQ_PUBLISH'),
    ('REQ_PUBLISH','REQUIREMENTS_PUBLISHED','REQ_HANDOFF'),
    ('REQ_HANDOFF','HANDOFF_PERSISTED','MENXIA_REQUIREMENTS_INTAKE'),
    ('MENXIA_REQUIREMENTS_INTAKE','IMPLEMENTATION_SCOPE_READY','MENXIA_GROUP_SOLVER'),
    ('MENXIA_REQUIREMENTS_INTAKE','REQUIREMENTS_CHANGE_REQUESTED','REQ_CLARIFY_PUBLISH'),
)


class RequirementsTransitionRegistry(TransitionRegistry):
    def __init__(self):
        edges = {(s,a):t for s,a,t in EDGES}
        menxia = TransitionRegistry.default()
        continuation = set(MENXIA + ('HUMAN_GATE','BLOCKED','RETRY_WAIT','PERSISTENCE_DEGRADED','DONE','FAILED'))
        edges.update({(s,a):t for (s,a),t in menxia.transitions.items() if s in continuation and t in continuation})
        for state in REQ_BUSINESS + SYSTEM + MENXIA:
            edges[(state,'CANCEL')] = 'REQ_CANCELLED'
        for state in REQ_BUSINESS:
            edges[(state,'PREREQUISITE_MISSING')] = 'REQ_BLOCKED'
            edges[(state,'ENGINE_FAILURE')] = 'REQ_ENGINE_ERROR'
            edges[(state,'RETRYABLE_FAILURE')] = 'REQ_RETRY_WAIT'
            if state not in ('REQ_PUBLISH','REQ_HANDOFF'):
                edges[(state,'FEEDBACK_TO_DRAFT')] = 'REQ_DRAFT'
                edges[(state,'ANSWER_CONFLICT')] = 'REQ_CLARIFY_PUBLISH'
        for state in MENXIA:
            edges[(state,'REQUIREMENTS_CHANGE_REQUESTED')] = 'REQ_CLARIFY_PUBLISH'
        for state in ('REQ_BLOCKED','REQ_ENGINE_ERROR'):
            edges[(state,'FEEDBACK_TO_DRAFT')] = 'REQ_DRAFT'
        super().__init__(edges, SYSTEM, allowed_states=REQ_BUSINESS+SYSTEM+MENXIA+TERMINAL,
                         system_states=SYSTEM, terminal_states=TERMINAL,
                         resumable_states=REQ_BUSINESS+MENXIA, required_mainline_edges=EDGES)

    def validate_resume_state(self, source, target):
        super().validate_resume_state(source, target)
        if source in ('HUMAN_GATE','BLOCKED','RETRY_WAIT','PERSISTENCE_DEGRADED') and target not in MENXIA:
            raise ValueError('Menxia system state cannot resume Zhongshu')
        if source.startswith('REQ_') and target not in REQ_BUSINESS:
            raise ValueError('requirements recovery cannot enter old workflow')
