"""Human authority and evolving interpretations, with append-only provenance."""
from dataclasses import replace
from .model import (SourceRecord, RequirementClaim, DecisionRecord, FactRecord,
                    AssumptionRecord, ConsensusRevision, decode, next_id,
                    normalized_quote)
from .result_validation import claim_quote_matches


def active_decisions(state):
    superseded = {old for d in state.decisions for old in d.supersedes_ids}
    return tuple(d for d in state.decisions if d.decision_id not in superseded)


def revise_consensus(state, **changes):
    old = state.consensus or ConsensusRevision()
    candidate = replace(old, **changes)
    if candidate == old:
        return state
    candidate = replace(candidate, version=old.version + 1, parent_digest=old.digest)
    return replace(state, consensus=candidate, pending_approval=None, approval=None)


def add_feedback(state, text, event_id, actor_id='local-operator', created_at=''):
    if any(s.external_id == event_id for s in state.sources):
        return state
    if state.sources and state.sources[-1].kind == 'feedback' and state.sources[-1].author_id == actor_id and state.sources[-1].text.strip() == text.strip():
        return state
    source = SourceRecord(next_id(state.sources, 'source_id', 'src'), 'feedback', text,
                          actor_id, event_id, created_at)
    state = replace(state, sources=state.sources + (source,),
                    pending_approval=None, approval=None)
    return revise_consensus(state, pending_source_ids=state.consensus.pending_source_ids + (source.source_id,))


def apply_analysis(state, payload, *, aliases=None, preserve_pending=False):
    analysis = payload.get('analysis', payload)
    if 'decisions' in analysis or 'document' in analysis:
        raise ValueError('ANALYST_AUTHORITY_VIOLATION')
    sources = {s.source_id: s for s in state.sources}
    claims = {c.requirement_id: c for c in state.consensus.claims}
    decisions = list(state.decisions)
    aliases = {} if aliases is None else aliases
    def bind_alias(key, identity):
        if key:
            if key in aliases:
                raise ValueError('local_key %r: LOCAL_KEY_COLLISION' % key)
            aliases[key] = identity
    for raw in analysis.get('claims', ()):
        row = dict(raw)
        key = row.pop('local_key', '')
        quote = row.pop('quote', '')
        identity = row.get('requirement_id') or next_id(tuple(claims.values()), 'requirement_id', 'req')
        row['requirement_id'] = identity
        claim = decode(RequirementClaim, row)
        label = 'claims[%s]' % (key or identity)
        if not set(claim.source_ids) <= sources.keys():
            raise ValueError('%s: CLAIM_SOURCE_UNKNOWN: %s' % (
                label, sorted(set(claim.source_ids) - sources.keys())))
        previous = claims.get(identity)
        if previous and previous.origin in ('explicit','choice') and claim.origin not in ('explicit','choice'):
            raise ValueError('%s: USER_DECISION_PROTECTED: existing %s claim cannot be downgraded to %s' % (
                label, previous.origin, claim.origin))
        if previous and previous.origin in ('explicit','choice') and not any(sid in state.consensus.pending_source_ids for sid in claim.source_ids):
            if (claim.kind,claim.topic_key,claim.origin,claim.status) != (previous.kind,previous.topic_key,previous.origin,previous.status):
                raise ValueError('%s: USER_DECISION_PROTECTED: explicit claim changed kind/topic/origin/status without a new human source' % label)
            claim = replace(claim,source_ids=previous.source_ids,decision_ids=previous.decision_ids)
        if previous and previous.origin in ('explicit', 'choice') and claim.statement != previous.statement:
            if not any(sid in state.consensus.pending_source_ids for sid in claim.source_ids):
                raise ValueError('%s: USER_DECISION_PROTECTED: statement changed without a new human source; keep existing statement: %r' % (
                    label, previous.statement))
            if not quote or not claim_quote_matches(quote, claim.statement):
                raise ValueError('%s: CHANGED_CHOICE_REQUIRES_EXPLICIT_SOURCE' % label)
        if claim.origin in ('explicit', 'choice'):
            quote = quote or claim.statement
            human = next((sources[sid] for sid in claim.source_ids
                          if sources[sid].kind in ('request', 'answer', 'feedback')
                          and claim_quote_matches(quote, sources[sid].text)), None)
            if not human:
                human_ids = [sid for sid in claim.source_ids
                             if sid in sources and sources[sid].kind in ('request', 'answer', 'feedback')]
                cited = human_ids[0] if human_ids else 'the cited human source'
                excerpt = ''
                if human_ids:
                    excerpt = ' Full source text: ' + sources[human_ids[0]].text[:300]
                raise ValueError('%s: EXPLICIT_CLAIM_REQUIRES_HUMAN_QUOTE: quote must be an exact contiguous '
                                 'excerpt of %s (copy the source text verbatim, keep path separators and case; '
                                 'statement may paraphrase but quote may not).%s' % (label, cited, excerpt))
            old_choices = tuple(d for d in active_decisions(replace(state, decisions=tuple(decisions)))
                                if d.topic_key == claim.topic_key)

            def same_conclusion(conclusion: str, candidate: str) -> bool:
                a, b = normalized_quote(conclusion), normalized_quote(candidate)
                return bool(a) and bool(b) and (a in b or b in a)

            conflicting = next((d for d in old_choices
                                if human.source_id == d.source_id
                                and not same_conclusion(d.conclusion, quote)), None)
            if conflicting:
                raise ValueError('%s: USER_DECISION_PROTECTED: topic %r already concluded %r from the same human source (claims %s); one quote per topic per source - reuse that quote or use a different topic' % (
                    label, claim.topic_key, conflicting.conclusion,
                    ','.join(conflicting.requirement_ids) or conflicting.decision_id))
            existing = next((d for d in old_choices
                             if d.source_id == human.source_id and same_conclusion(d.conclusion, quote)), None)
            if existing is None:
                existing = DecisionRecord(next_id(decisions, 'decision_id', 'decision'), claim.topic_key,
                                          quote, human.source_id, tuple(d.decision_id for d in old_choices), (identity,))
                decisions.append(existing)
            claim = replace(claim, decision_ids=(existing.decision_id,))
        bind_alias(key, identity)
        claims[identity] = claim
    facts = {f.fact_id: f for f in state.consensus.facts}
    for raw in analysis.get('facts', analysis.get('fact_updates', ())):
        row = dict(raw)
        key = row.pop('local_key', '')
        row['fact_id'] = row.get('fact_id') or next_id(tuple(facts.values()), 'fact_id', 'fact')
        row['requirement_ids'] = [aliases.get(i, i) for i in row.get('requirement_ids', ())]
        fact = decode(FactRecord, row)
        facts[fact.fact_id] = fact
        bind_alias(key, fact.fact_id)
    assumptions = {a.assumption_id: a for a in state.consensus.assumptions}
    for raw in analysis.get('assumptions', ()):
        row = dict(raw)
        key = row.pop('local_key', '')
        row['assumption_id'] = row.get('assumption_id') or next_id(tuple(assumptions.values()), 'assumption_id', 'assumption')
        assumption = decode(AssumptionRecord, row)
        previous = assumptions.get(assumption.assumption_id)
        if previous and previous.disposition == 'accepted' and assumption != previous:
            raise ValueError('USER_ACCEPTED_ASSUMPTION_PROTECTED')
        if previous and previous.disposition == 'rejected' and assumption != previous:
            human_rejection = any(q.default_assumption_id == previous.assumption_id
                and q.topic_key == 'assumption:' + previous.assumption_id
                and any(a.question_id == q.question_id and a.question_revision == q.revision
                        and a.option_ids == ('other',) for a in state.answers) for q in state.questions)
            if human_rejection:
                raise ValueError('USER_REJECTED_ASSUMPTION_PROTECTED')
        if assumption.disposition == 'accepted' and not (previous and previous.disposition == 'accepted'):
            raise ValueError('MODEL_CANNOT_ACCEPT_ASSUMPTION')
        assumptions[assumption.assumption_id] = assumption
        bind_alias(key, assumption.assumption_id)
    state = replace(state, decisions=tuple(decisions))
    current = {d.decision_id for d in active_decisions(state)}
    final_claims = tuple(replace(c, status='superseded')
                         if c.decision_ids and not set(c.decision_ids) <= current else c for c in claims.values())
    return revise_consensus(state, claims=final_claims,
                            active_decision_ids=tuple(d.decision_id for d in active_decisions(state)),
                            facts=tuple(facts.values()), assumptions=tuple(assumptions.values()),
                            pending_source_ids=state.consensus.pending_source_ids if preserve_pending else ())
