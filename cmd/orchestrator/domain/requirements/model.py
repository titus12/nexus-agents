"""Immutable, strictly decoded records for requirements-v1. No runtime imports."""
from __future__ import annotations

import hashlib
import json
import types
import unicodedata
from dataclasses import asdict, dataclass, fields, is_dataclass
from enum import Enum
from functools import lru_cache
from typing import get_args, get_origin, get_type_hints


# Shared by domain validation and the model-facing result contracts.
RECORD_ENUMS = {
    'RequirementClaim': {
        'kind': ('goal', 'behavior', 'constraint', 'non_goal'),
        'origin': ('explicit', 'choice', 'derived', 'proposal'),
        'status': ('active', 'proposed', 'superseded', 'rejected'),
    },
    'FactRecord': {'status': ('observed', 'unavailable')},
    'AssumptionRecord': {'disposition': ('proposed', 'accepted', 'deferred', 'rejected')},
    'Question': {'answer_kind': ('single', 'multi', 'text')},
    'DraftSection': {'kind': ('background', 'goals', 'scope', 'constraints', 'behavior',
                              'rules', 'acceptance', 'non_goals', 'topic')},
    'ReviewIssue': {
        'owner': ('solver', 'analyst', 'user', 'engine', 'menxia'),
        'severity': ('blocking', 'advisory'),
        'status': ('open', 'resolved', 'superseded'),
    },
}

RECORD_MINIMUMS = {'Question': {'revision': 1}}


class Outcome(str, Enum):
    IN_PROGRESS = 'IN_PROGRESS'
    WAITING_CLARIFICATION = 'WAITING_CLARIFICATION'
    WAITING_CONFIRMATION = 'WAITING_CONFIRMATION'
    PREREQUISITE_BLOCKED = 'PREREQUISITE_BLOCKED'
    ENGINE_ERROR = 'ENGINE_ERROR'
    CANCELLED = 'CANCELLED'
    REQUIREMENTS_READY = 'REQUIREMENTS_READY'


def canonical(value) -> str:
    return json.dumps(asdict(value) if is_dataclass(value) else value,
                      ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=asdict)


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def text_digest(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def normalized_quote(text: str) -> str:
    """Meaning-preserving normalization for verbatim-quote matching.

    Only forms that cannot change the user's words collapse: unicode
    compatibility forms, Windows/POSIX path separators, letter case and
    whitespace runs. Every visible word survives unchanged.
    """
    return ' '.join(unicodedata.normalize('NFKC', str(text or '')).replace('\\', '/').casefold().split())


@lru_cache(maxsize=None)
def record_types(cls):
    return get_type_hints(cls)


def decode(cls, value):
    """Reject shape/type coercion and unknown fields at every nesting level."""
    origin, args = get_origin(cls), get_args(cls)
    if origin is types.UnionType:
        for member in args:
            try:
                return decode(member, value)
            except (ValueError, TypeError):
                pass
        raise ValueError(f'invalid union value for {cls}')
    if cls is type(None):
        if value is not None:
            raise ValueError('expected null')
        return None
    if origin is tuple:
        if not isinstance(value, (list, tuple)):
            raise ValueError('expected array')
        return tuple(decode(args[0], item) for item in value)
    if isinstance(cls, type) and issubclass(cls, Enum):
        return cls(value)
    if is_dataclass(cls):
        if not isinstance(value, dict):
            raise ValueError(f'expected {cls.__name__} object')
        hints = record_types(cls)
        if set(value) - set(hints):
            raise ValueError(f'unknown {cls.__name__} fields: {sorted(set(value)-set(hints))}')
        return cls(**{key: decode(hints[key], item) for key, item in value.items()})
    if type(value) is not cls:
        raise ValueError(f'expected {cls.__name__}, got {type(value).__name__}')
    return value


def enum_check(value, allowed, field):
    if value not in allowed:
        raise ValueError(f'invalid {field}: {value}')


@dataclass(frozen=True)
class SourceRecord:
    source_id: str
    kind: str
    text: str
    author_id: str = 'local-operator'
    external_id: str = ''
    created_at: str = ''
    supersedes_ids: tuple[str, ...] = ()

    def __post_init__(self):
        if not self.source_id or not self.text.strip():
            raise ValueError('source requires identity and original text')
        enum_check(self.kind, ('request', 'answer', 'feedback', 'fact'), 'source kind')


@dataclass(frozen=True)
class RequirementClaim:
    requirement_id: str
    topic_key: str
    statement: str
    kind: str = 'behavior'
    origin: str = 'explicit'
    source_ids: tuple[str, ...] = ()
    decision_ids: tuple[str, ...] = ()
    status: str = 'active'

    def __post_init__(self):
        enum_check(self.kind, RECORD_ENUMS['RequirementClaim']['kind'], 'claim kind')
        enum_check(self.origin, RECORD_ENUMS['RequirementClaim']['origin'], 'claim origin')
        enum_check(self.status, RECORD_ENUMS['RequirementClaim']['status'], 'claim status')
        if not self.requirement_id or not self.topic_key or not self.statement.strip():
            raise ValueError('incomplete claim')


@dataclass(frozen=True)
class DecisionRecord:
    decision_id: str
    topic_key: str
    conclusion: str
    source_id: str
    supersedes_ids: tuple[str, ...] = ()
    requirement_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class FactRecord:
    fact_id: str
    observation: str
    source: str
    quote: str
    source_fingerprint: str = ''
    status: str = 'observed'
    requirement_ids: tuple[str, ...] = ()

    def __post_init__(self):
        enum_check(self.status, RECORD_ENUMS['FactRecord']['status'], 'fact status')
        if self.status == 'observed' and (not self.source or not self.quote):
            raise ValueError('observed fact requires source and quote')


@dataclass(frozen=True)
class AssumptionRecord:
    assumption_id: str
    statement: str
    source_ids: tuple[str, ...] = ()
    blocking: bool = False
    disposition: str = 'proposed'

    def __post_init__(self):
        enum_check(self.disposition, RECORD_ENUMS['AssumptionRecord']['disposition'], 'assumption')


@dataclass(frozen=True)
class ConsensusRevision:
    version: int = 1
    parent_digest: str = ''
    claims: tuple[RequirementClaim, ...] = ()
    active_decision_ids: tuple[str, ...] = ()
    facts: tuple[FactRecord, ...] = ()
    assumptions: tuple[AssumptionRecord, ...] = ()
    pending_source_ids: tuple[str, ...] = ()

    @property
    def digest(self):
        return digest(self)


@dataclass(frozen=True)
class QuestionOption:
    option_id: str
    label: str
    consequence: str = ''


@dataclass(frozen=True)
class Question:
    question_id: str
    topic_key: str
    question: str
    reason: str
    impact: str
    answer_kind: str = 'single'
    options: tuple[QuestionOption, ...] = ()
    recommendation: str = ''
    default_assumption_id: str = ''
    blocking: bool = True
    depends_on_question_ids: tuple[str, ...] = ()
    related_requirement_ids: tuple[str, ...] = ()
    related_issue_ids: tuple[str, ...] = ()
    revision: int = 1

    def __post_init__(self):
        enum_check(self.answer_kind, RECORD_ENUMS['Question']['answer_kind'], 'answer kind')
        if not all((self.question_id, self.topic_key, self.question, self.reason, self.impact)):
            raise ValueError('question must explain its reason and impact')
        if self.revision < RECORD_MINIMUMS['Question']['revision'] or len({o.option_id for o in self.options}) != len(self.options):
            raise ValueError('invalid question revision/options')


@dataclass(frozen=True)
class ClarificationRound:
    round_id: str
    consensus_digest: str
    question_ids: tuple[str, ...]
    page_ids: tuple[str, ...] = ()
    status: str = 'preparing'
    return_to: str = 'REQ_ANALYZE'
    actor_ids: tuple[str, ...] = ()

    def __post_init__(self):
        enum_check(self.status, ('preparing', 'open', 'partial', 'answered', 'superseded', 'cancelled'), 'round')
        enum_check(self.return_to, ('REQ_ANALYZE', 'REQ_DRAFT'), 'question return')


@dataclass(frozen=True)
class AnswerRecord:
    answer_id: str
    question_id: str
    question_revision: int
    round_id: str
    option_ids: tuple[str, ...]
    free_text: str
    disposition: str
    source_id: str
    event_id: str


CORE_SECTIONS = ('background', 'goals', 'scope', 'constraints', 'behavior', 'rules', 'acceptance', 'non_goals')


@dataclass(frozen=True)
class DraftSection:
    section_id: str
    kind: str
    title: str
    markdown: str
    requirement_ids: tuple[str, ...] = ()
    decision_ids: tuple[str, ...] = ()
    fact_ids: tuple[str, ...] = ()
    related_section_ids: tuple[str, ...] = ()

    def __post_init__(self):
        enum_check(self.kind, RECORD_ENUMS['DraftSection']['kind'], 'section kind')


@dataclass(frozen=True)
class DraftRevision:
    version: int
    title: str
    sections: tuple[DraftSection, ...]
    consensus_digest: str
    document_digest: str
    change_summary: str = ''
    parent_digest: str = ''


@dataclass(frozen=True)
class ReviewIssue:
    issue_id: str
    rule: str
    section_ids: tuple[str, ...]
    requirement_ids: tuple[str, ...]
    claim: str
    impact: str
    severity: str
    owner: str
    acceptance: str
    basis_source_ids: tuple[str, ...] = ()
    status: str = 'open'
    previous_issue_id: str = ''

    def __post_init__(self):
        enum_check(self.owner, RECORD_ENUMS['ReviewIssue']['owner'], 'issue owner')
        enum_check(self.severity, RECORD_ENUMS['ReviewIssue']['severity'], 'issue severity')
        enum_check(self.status, RECORD_ENUMS['ReviewIssue']['status'], 'issue status')
        if self.owner == 'menxia' and self.severity == 'blocking':
            raise ValueError('unresolved business requirement cannot be delegated')


@dataclass(frozen=True)
class ReviewCoverage:
    axis: str
    section_ids: tuple[str, ...]
    conclusion: str
    basis_digest: str = ''


@dataclass(frozen=True)
class ReviewStamp:
    input_digest: str
    document_digest: str
    consensus_digest: str
    facts_digest: str
    issue_ids: tuple[str, ...]
    affected_section_ids: tuple[str, ...]
    coverage: tuple[ReviewCoverage, ...]
    passed: bool
    policy_version: str = 'requirements-policy-v1'


@dataclass(frozen=True)
class ApprovalRecord:
    approval_id: str
    document_digest: str
    consensus_digest: str
    review_input_digest: str
    actor_id: str
    external_event_id: str
    approved_at: str
    cloud_revision_ref: str = ''


@dataclass(frozen=True)
class PendingApproval:
    request_id: str
    gate_id: str
    document_digest: str
    review_input_digest: str
    actor_id: str
    event_id: str
    received_at: str
    close_barrier_id: str = ''


@dataclass(frozen=True)
class DispatchCharge:
    logical_dispatch_id: str
    kind: str
    attempts: int = 1
    format_repairs: int = 0
    external_retries: int = 0


@dataclass(frozen=True)
class EffectCharge:
    effect_id: str
    count: int = 0


@dataclass(frozen=True)
class BudgetLedger:
    policy_version: str = 'requirements-policy-v1'
    work_epoch: int = 1
    epoch_input_digest: str = ''
    calls: int = 0
    total_calls: int = 0
    semantic_repairs: int = 0
    evidence_repairs: int = 0
    dispatch_charges: tuple[DispatchCharge, ...] = ()
    publication_retries: tuple[EffectCharge, ...] = ()
    last_progress_digest: str = ''
    stalled_count: int = 0

    def __post_init__(self):
        for name in ('work_epoch', 'calls', 'total_calls', 'semantic_repairs', 'evidence_repairs', 'stalled_count'):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f'invalid budget {name}')
        if self.work_epoch < 1 or self.policy_version != 'requirements-policy-v1':
            raise ValueError('unsupported requirements budget policy')


@dataclass(frozen=True)
class WorkItem:
    work_id: str
    kind: str
    issue_ids: tuple[str, ...] = ()
    question_ids: tuple[str, ...] = ()
    depends_on_work_ids: tuple[str, ...] = ()
    status: str = 'pending'
    details: str = ''
    source_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class RequirementsFailure:
    code: str
    stage: str
    component: str
    message: str
    artifact_ref: str = ''
    prerequisite_fingerprint: str = ''
    unblock_condition: str = ''


@dataclass(frozen=True)
class RequirementsRecovery:
    failed_state: str
    retry_return_to: str = ''
    logical_dispatch_id: str = ''
    failed_reply_ref: str = ''
    failure: RequirementsFailure | None = None
    repair_fingerprint: str = ''


@dataclass(frozen=True)
class RequirementsDispatch:
    logical_dispatch_id: str
    request_id: str
    state: str
    base_document_digest: str
    base_consensus_digest: str
    review_input_digest: str = ''
    status: str = 'active'


@dataclass(frozen=True)
class EvidenceAnswer:
    issue_id: str
    answer: str
    fact_ids: tuple[str, ...]
    needs_draft_change: bool


@dataclass(frozen=True)
class IssueResponse:
    issue_id: str
    response: str


@dataclass(frozen=True)
class RequirementsState:
    sources: tuple[SourceRecord, ...] = ()
    decisions: tuple[DecisionRecord, ...] = ()
    consensus: ConsensusRevision | None = None
    questions: tuple[Question, ...] = ()
    rounds: tuple[ClarificationRound, ...] = ()
    answers: tuple[AnswerRecord, ...] = ()
    draft: DraftRevision | None = None
    issues: tuple[ReviewIssue, ...] = ()
    review_stamp: ReviewStamp | None = None
    pending_approval: PendingApproval | None = None
    approval: ApprovalRecord | None = None
    budget: BudgetLedger = BudgetLedger()
    work_queue: tuple[WorkItem, ...] = ()
    active_dispatch: RequirementsDispatch | None = None
    recovery: RequirementsRecovery | None = None
    outcome: Outcome = Outcome.IN_PROGRESS
    reason: str = ''
    handoff_ref: str = ''
    review_document_ref: str = ''
    review_gate_id: str = ''
    review_cloud_ref: str = ''
    published_ref: str = ''
    evidence_answers: tuple[EvidenceAnswer, ...] = ()
    solver_responses: tuple[IssueResponse, ...] = ()

    def __post_init__(self):
        for records, field in ((self.sources, 'source_id'), (self.decisions, 'decision_id'),
                               (self.questions, 'question_id'), (self.rounds, 'round_id'),
                               (self.answers, 'answer_id'), (self.issues, 'issue_id')):
            identities = [getattr(row, field) for row in records]
            if any(not identity for identity in identities) or len(set(identities)) != len(identities):
                raise ValueError(f'duplicate/empty {field}')
        enum_check(self.outcome, tuple(Outcome), 'requirements outcome')
        source_ids = {s.source_id for s in self.sources}
        decision_ids = {d.decision_id for d in self.decisions}
        previous_decisions = set()
        for d in self.decisions:
            if d.source_id not in source_ids or not set(d.supersedes_ids) <= previous_decisions:
                raise ValueError('invalid decision source/supersedes')
            previous_decisions.add(d.decision_id)
        if self.consensus:
            ids = [c.requirement_id for c in self.consensus.claims]
            if len(ids) != len(set(ids)):
                raise ValueError('duplicate requirement_id')
            for c in self.consensus.claims:
                if not set(c.source_ids) <= source_ids or not set(c.decision_ids) <= decision_ids:
                    raise ValueError('unknown claim source/decision')
            if not set(self.consensus.active_decision_ids) <= decision_ids:
                raise ValueError('unknown active decision')
            if not set(self.consensus.pending_source_ids) <= source_ids:
                raise ValueError('unknown pending source')
        if self.draft and (self.draft.version < 1 or len(self.draft.document_digest) != 64):
            raise ValueError('invalid draft version/hash')
        if self.draft:
            from .document import render_markdown
            if text_digest(render_markdown(self.draft)) != self.draft.document_digest:
                raise ValueError('DOCUMENT_HASH_MISMATCH')
        if self.approval:
            from .review import review_input_digest, AXES
            if (not self.draft or not self.review_stamp or not self.review_stamp.passed
                or self.approval.document_digest != self.draft.document_digest
                or self.approval.consensus_digest != self.consensus.digest
                or self.review_stamp.input_digest != self.approval.review_input_digest
                or {c.axis for c in self.review_stamp.coverage} != set(AXES)
                or any(i.status == 'open' and i.severity == 'blocking' for i in self.issues)
                or self.consensus.pending_source_ids
                or self.approval.review_input_digest != review_input_digest(self.draft,self.consensus)):
                raise ValueError('APPROVAL_BINDING_INVALID')
        if self.outcome == Outcome.REQUIREMENTS_READY and (not self.approval or not self.published_ref or not self.handoff_ref):
            raise ValueError('REQUIREMENTS_READY_WITHOUT_APPROVED_HANDOFF')

    @classmethod
    def empty(cls, raw_source: SourceRecord):
        return cls(sources=(raw_source,), consensus=ConsensusRevision(pending_source_ids=(raw_source.source_id,)))

    def to_dict(self):
        return json.loads(canonical(self))

    @classmethod
    def from_dict(cls, value):
        def require_fields(record_type, raw):
            if raw is None:
                return
            origin,args = get_origin(record_type),get_args(record_type)
            if origin is types.UnionType:
                concrete = next((t for t in args if is_dataclass(t)),None)
                if concrete:
                    require_fields(concrete,raw)
            elif origin is tuple and isinstance(raw,(list,tuple)):
                for item in raw:
                    require_fields(args[0],item)
            elif is_dataclass(record_type) and isinstance(raw,dict):
                hints = record_types(record_type)
                if set(hints)-set(raw):
                    raise ValueError(f'missing persisted {record_type.__name__} fields: {sorted(set(hints)-set(raw))}')
                for key,typ in hints.items():
                    require_fields(typ,raw[key])
        require_fields(cls,value)
        state = decode(cls, value)
        if state.consensus and state.consensus.version < 1:
            raise ValueError('invalid consensus version')
        for records,attribute in ((state.consensus.facts,'fact_id'),(state.consensus.assumptions,'assumption_id')) if state.consensus else ():
            if len({getattr(r,attribute) for r in records}) != len(records):
                raise ValueError('duplicate persisted '+attribute)
        return state


def next_id(records, attribute, prefix):
    numbers = [int(getattr(row, attribute).split('-')[-1]) for row in records
               if getattr(row, attribute).startswith(prefix + '-') and getattr(row, attribute).split('-')[-1].isdigit()]
    return f'{prefix}-{max(numbers, default=0)+1:06d}'
