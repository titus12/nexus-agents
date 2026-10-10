"""Deterministic presentation, never an implementation-plan renderer."""
import re
from dataclasses import replace
from .model import (CORE_SECTIONS, DraftSection, DraftRevision, ReviewIssue,
                    decode, text_digest)

# Eight-chapter template (spec 2026-10-10-zhongshu-scout-draft-first-modification.md D1-D3):
# constraints record externally imposed limits, scope records the boundary we
# draw ourselves, non_goals record adjacent capabilities the whole effort skips.
KIND_TITLES = {
    'background': '背景', 'goals': '目标', 'problem': '当前问题', 'constraints': '约束',
    'behavior': '核心功能清单', 'scope': '责任边界', 'acceptance': '验收标准',
    'non_goals': '非目标', 'risks': '风险',
}
CANONICAL_ORDER = ('background', 'goals', 'problem', 'constraints', 'behavior', 'scope',
                   'acceptance', 'non_goals', 'risks')
PENDING_MARKER_RE = re.compile(r'【待确认[^】]*】')


def render_markdown(draft):
    blocks = ['# ' + draft.title]
    blocks.extend('## ' + section.title + '\n\n' + section.markdown for section in draft.sections)
    return '\n\n'.join(blocks) + '\n'


def new_skeleton(raw_request, consensus_digest=''):
    sections = tuple(DraftSection('S_' + kind, kind, KIND_TITLES[kind],
                                  '> ' + raw_request.replace('\n', '\n> ') if kind == 'background' else '')
                     for kind in CORE_SECTIONS)
    draft = DraftRevision(1, '需求草案', sections, consensus_digest, '0'*64)
    return replace(draft, document_digest=text_digest(render_markdown(draft)))


def commit_draft(current, payload, consensus):
    if payload.get('base_document_digest', current.document_digest if current else '') != (current.document_digest if current else ''):
        raise ValueError('BASE_REVISION_STALE')
    body = payload.get('document', payload)
    sections = tuple(decode(DraftSection, row) for row in body['sections'])
    if len({s.section_id for s in sections}) != len(sections):
        raise ValueError('SECTION_ID_DUPLICATE')
    if not set(CORE_SECTIONS) <= {s.kind for s in sections}:
        raise ValueError('SECTION_REQUIRED')
    draft = DraftRevision((current.version+1) if current else 1, body['title'], sections,
                          consensus.digest, '0'*64, payload.get('change_summary', ''), current.document_digest if current else '')
    rendered = render_markdown(draft)
    if '\r' in rendered or rendered.startswith('\ufeff'):
        raise ValueError('DOCUMENT_ENCODING_INVALID')
    return replace(draft, document_digest=text_digest(rendered))


def validate_draft(draft, consensus, decisions=()):
    issues = []
    def issue(rule, section_ids=(), req_ids=(), owner='solver'):
        issues.append(ReviewIssue(f'mechanical-{rule}-{len(issues)+1}', rule, tuple(section_ids), tuple(req_ids),
                                  rule, '正文无法交付', 'blocking', owner, '补齐该语义与对应来源'))
    if not draft or not draft.title.strip():
        issue('DRAFT_EMPTY')
        return tuple(issues)
    if text_digest(render_markdown(draft)) != draft.document_digest:
        raise ValueError('DOCUMENT_HASH_MISMATCH')
    known_req = {c.requirement_id for c in consensus.claims}
    known_dec = set(consensus.active_decision_ids)
    known_fact = {f.fact_id for f in consensus.facts}
    known_sec = {s.section_id for s in draft.sections}
    for kind in CORE_SECTIONS:
        matches = [s for s in draft.sections if s.kind == kind]
        if len(matches) != 1 or not matches[0].markdown.strip():
            issue('SECTION_REQUIRED', [s.section_id for s in matches])
    for s in draft.sections:
        if not set(s.requirement_ids) <= known_req or not set(s.decision_ids) <= known_dec or not set(s.fact_ids) <= known_fact or not set(s.related_section_ids) <= known_sec:
            issue('REFERENCE_UNKNOWN', (s.section_id,))
        if PENDING_MARKER_RE.search(s.markdown):
            # A pending marker is a question awaiting a human answer; a draft
            # can only be validated or published once it is replaced (or
            # downgraded to a plain 待定 without the bracket marker).
            issue('PENDING_MARKER', (s.section_id,))
        if any(f.fact_id in s.fact_ids and (f.status != 'observed' or not f.source_fingerprint) for f in consensus.facts):
            issue('FACT_UNAVAILABLE',(s.section_id,),owner='analyst')
    covered = {i for s in draft.sections for i in s.requirement_ids}
    accepted = {i for s in draft.sections if s.kind == 'acceptance' for i in s.requirement_ids}
    mapped_decisions = {i for s in draft.sections for i in s.decision_ids}
    for c in consensus.claims:
        if c.status != 'active' or c.origin == 'proposal':
            continue
        if c.requirement_id not in covered:
            issue('REQUIREMENT_UNMAPPED', req_ids=(c.requirement_id,))
        if c.kind in ('goal', 'behavior') and c.requirement_id not in accepted:
            issue('ACCEPTANCE_UNMAPPED', req_ids=(c.requirement_id,))
    for identity in known_dec - mapped_decisions:
        issue('DECISION_UNMAPPED')
    return tuple(issues)
