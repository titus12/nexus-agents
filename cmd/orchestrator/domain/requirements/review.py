"""Stable defect ledger and reuse of unaffected review conclusions."""
from dataclasses import replace
from .model import ReviewIssue, ReviewCoverage, ReviewStamp, decode, digest, next_id
from .budget import relevant_facts_digest

AXES = ('intent', 'scenarios', 'constraints', 'consistency', 'acceptance', 'decisions', 'facts')


def review_input_digest(draft, consensus, policy_version='requirements-policy-v1'):
    return digest((draft.document_digest, consensus.digest, relevant_facts_digest(consensus.facts), policy_version))


def affected_sections(before, after, changed_claim_ids=(), changed_fact_ids=(), changed_decision_ids=()):
    old = {s.section_id: s for s in before.sections} if before else {}
    current = {s.section_id: s for s in after.sections}
    affected = {identity for identity in old.keys() | current.keys() if old.get(identity) != current.get(identity)}
    for s in after.sections:
        if set(s.requirement_ids) & set(changed_claim_ids) or set(s.fact_ids) & set(changed_fact_ids) or set(s.decision_ids) & set(changed_decision_ids):
            affected.add(s.section_id)
    while True:
        expanded = set(affected)
        for s in tuple(before.sections if before else ()) + after.sections:
            if s.section_id in affected:
                expanded.update(s.related_section_ids)
            if set(s.related_section_ids) & affected:
                expanded.add(s.section_id)
        if expanded == affected:
            return tuple(sorted(affected))
        affected = expanded


def coverage_basis(state, section_ids):
    sections = tuple(s for s in state.draft.sections if s.section_id in section_ids)
    reqs = {i for s in sections for i in s.requirement_ids}
    decs = {i for s in sections for i in s.decision_ids}
    facts = {i for s in sections for i in s.fact_ids}
    return digest((sections, tuple(c for c in state.consensus.claims if c.requirement_id in reqs),
                   tuple(d for d in state.decisions if d.decision_id in decs),
                   tuple(f for f in state.consensus.facts if f.fact_id in facts), state.budget.policy_version))


def issue_identity(issue):
    return issue.rule, tuple(sorted(issue.section_ids)), tuple(sorted(issue.requirement_ids))


def merge_review(state, payload):
    expected = review_input_digest(state.draft, state.consensus)
    if payload.get('review_input_digest', expected) != expected:
        raise ValueError('REVIEW_INPUT_STALE')
    if 'document' in payload or 'decisions' in payload:
        raise ValueError('CRITIC_AUTHORITY_VIOLATION')
    issues = list(state.issues)
    known_sections = {s.section_id for s in state.draft.sections}
    known_reqs = {c.requirement_id for c in state.consensus.claims}
    known_sources = {s.source_id for s in state.sources}
    for raw in payload.get('issues', ()):
        row = dict(raw)
        row.pop('local_key', None)
        row['issue_id'] = row.get('issue_id') or next_id(issues, 'issue_id', 'finding')
        issue = decode(ReviewIssue, row)
        if not issue.claim or not issue.impact or not issue.acceptance:
            raise ValueError('FINDING_REQUIRES_CLAIM_IMPACT_ACCEPTANCE')
        if not set(issue.section_ids) <= known_sections or not set(issue.requirement_ids) <= known_reqs or not set(issue.basis_source_ids) <= known_sources:
            raise ValueError('FINDING_REFERENCE_UNKNOWN')
        if not issue.section_ids and not issue.requirement_ids and not issue.basis_source_ids:
            raise ValueError('FINDING_BASIS_REQUIRED')
        old = next((i for i in issues if i.issue_id == issue.issue_id or issue_identity(i) == issue_identity(issue)), None)
        if old:
            if issue.acceptance != old.acceptance:
                raise ValueError(
                    'FINDING_ACCEPTANCE_CHANGED: finding %s already requires %r; '
                    'restate that acceptance text verbatim (changing it requires closing '
                    'the finding and opening a new one).' % (old.issue_id, old.acceptance))
            issues[issues.index(old)] = replace(old, status='open', basis_source_ids=tuple(sorted(set(old.basis_source_ids + issue.basis_source_ids))))
        else:
            issues.append(replace(issue, status='open'))
    for closure in payload.get('closures', ()):
        old = next((i for i in issues if i.issue_id == closure['issue_id']), None)
        if old is None:
            raise ValueError('CLOSURE_ISSUE_UNKNOWN')
        decision = closure['status']
        if decision not in ('resolved', 'still_open', 'reclassified'):
            raise ValueError('CLOSURE_STATUS_INVALID')
        if decision == 'resolved':
            if not closure.get('evidence'):
                raise ValueError('CLOSURE_EVIDENCE_REQUIRED')
            issues[issues.index(old)] = replace(old, status='resolved')
        elif decision == 'reclassified':
            if not closure.get('evidence') or closure.get('severity') != 'advisory':
                raise ValueError('RECLASSIFICATION_BASIS_REQUIRED')
            issues[issues.index(old)] = replace(old, severity='advisory')
    coverage = []
    if state.review_stamp:
        coverage.extend(c for c in state.review_stamp.coverage if c.axis != 'consistency'
                        and c.basis_digest == coverage_basis(state, c.section_ids))
    for raw in payload.get('coverage', ()):
        c = decode(ReviewCoverage, raw)
        if c.axis not in AXES or not c.conclusion.strip() or not c.section_ids or not set(c.section_ids) <= known_sections:
            raise ValueError('REVIEW_COVERAGE_INVALID')
        coverage = [old for old in coverage if old.axis != c.axis]
        coverage.append(replace(c, basis_digest=coverage_basis(state, c.section_ids)))
    if {c.axis for c in coverage} != set(AXES):
        raise ValueError('REVIEW_COVERAGE_INCOMPLETE')
    blocked = any(i.severity == 'blocking' and i.status == 'open' for i in issues)
    stamp = ReviewStamp(expected, state.draft.document_digest, state.consensus.digest,
                        relevant_facts_digest(state.consensus.facts), tuple(i.issue_id for i in issues),
                        tuple(payload.get('review_scope', known_sections)), tuple(coverage), not blocked)
    actionable = {i.issue_id for i in issues if i.status=='open' and i.severity=='blocking'}
    queue = tuple(replace(w,status='done') if w.issue_ids and not set(w.issue_ids)&actionable else w for w in state.work_queue)
    return replace(state, issues=tuple(issues), work_queue=queue, review_stamp=stamp, pending_approval=None, approval=None)


def next_repair_route(state):
    open_issues = tuple(i for i in state.issues if i.status == 'open' and i.severity == 'blocking')
    for owner, action in (('engine', 'ENGINE_FAILURE'), ('user', 'REVIEW_NEEDS_USER'),
                          ('analyst', 'REVIEW_NEEDS_EVIDENCE'), ('solver', 'REVIEW_NEEDS_DRAFT')):
        if any(i.owner == owner for i in open_issues):
            return action
    return 'REVIEW_PASSED'
