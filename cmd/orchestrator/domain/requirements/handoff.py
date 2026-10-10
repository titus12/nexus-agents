"""Immutable approved input; implementation planning cannot rewrite it."""
from dataclasses import dataclass
from .model import ApprovalRecord, ReviewStamp, RequirementClaim, DecisionRecord, decode, digest, text_digest
from .document import render_markdown
from .review import review_input_digest


@dataclass(frozen=True)
class ApprovedRequirementsInput:
    task_id: str
    document_ref: str
    document_digest: str
    markdown: str
    acceptance_markdown: str
    claims: tuple[RequirementClaim, ...]
    decisions: tuple[DecisionRecord, ...]
    review: ReviewStamp
    approval: ApprovalRecord

    def __post_init__(self):
        if text_digest(self.markdown) != self.document_digest:
            raise ValueError('APPROVED_BODY_HASH_MISMATCH')
        if not self.acceptance_markdown.strip() or self.acceptance_markdown not in self.markdown:
            raise ValueError('APPROVED_ACCEPTANCE_MISSING')
        if not self.review.passed or self.review.document_digest != self.document_digest:
            raise ValueError('APPROVED_REVIEW_MISMATCH')
        if self.approval.document_digest != self.document_digest or self.approval.review_input_digest != self.review.input_digest:
            raise ValueError('APPROVAL_BINDING_MISMATCH')

    @property
    def input_digest(self):
        return digest(self)


def make_approved_input(task_id, state):
    if not state.approval or not state.review_stamp or not state.published_ref:
        raise ValueError('APPROVED_PUBLISHED_REQUIREMENTS_REQUIRED')
    if state.review_stamp.input_digest != review_input_digest(state.draft, state.consensus):
        raise ValueError('CURRENT_REVIEW_REQUIRED')
    return ApprovedRequirementsInput(task_id, state.published_ref + '/requirements.md', state.draft.document_digest,
        render_markdown(state.draft), next(s.markdown for s in state.draft.sections if s.kind == 'acceptance'),
        tuple(c for c in state.consensus.claims if c.status == 'active'),
        tuple(d for d in state.decisions if d.decision_id in state.consensus.active_decision_ids), state.review_stamp, state.approval)


def from_dict(value):
    return decode(ApprovedRequirementsInput, value)
