"""Collect independent result errors before an atomic domain commit; no I/O."""
from .model import (RequirementClaim, FactRecord, AssumptionRecord, Question, decode,
        normalized_quote)


def claim_quote_matches(quote: str, source_text: str) -> bool:
    if not isinstance(quote, str) or not quote:
        return False
    if quote in source_text:
        return True
    probe, haystack = normalized_quote(quote), normalized_quote(source_text)
    return bool(probe) and probe in haystack


def result_errors(payload, dispatch):
    errors, aliases, identities = [], {}, {}
    analysis = payload.get('analysis', {})
    analysis = analysis if isinstance(analysis, dict) else {}
    collections = [('analysis.'+name, analysis.get(name, []), cls, field) for name,cls,field in (
        ('claims',RequirementClaim,'requirement_id'), ('facts',FactRecord,'fact_id'),
        ('assumptions',AssumptionRecord,'assumption_id'))]
    collections += [('questions',payload.get('questions',[]),Question,'question_id'),
                    ('fact_updates',payload.get('fact_updates',[]),FactRecord,'fact_id')]
    for name, rows, cls, id_field in collections:
        if not isinstance(rows,list):
            continue  # Type errors belong to the schema validator.
        identities[name] = set()
        for index,raw in enumerate(rows):
            path = f'{name}[{index}]'
            if not isinstance(raw,dict):
                continue
            key = raw.get('local_key')
            if isinstance(key,str) and key:
                if key in aliases:
                    errors.append(f'{path}.local_key: LOCAL_KEY_COLLISION with {aliases[key]}')
                else:
                    aliases[key] = path
            identity = raw.get(id_field)
            if isinstance(identity,str) and identity:
                if identity in identities[name]:
                    errors.append(f'{path}.{id_field}: DUPLICATE_RECORD_ID; '
                                  f'give this row a fresh unique id or omit {id_field} for engine assignment')
                identities[name].add(identity)
            row = {k:v for k,v in raw.items() if k != 'local_key'}
            if cls is RequirementClaim:
                row.pop('quote', None)  # Transport provenance, not a claim field.
                row.pop('span_ids', None)  # Payload-only citation hint; not a claim field.
            row[id_field] = identity or f'validation-{index}'
            try:
                decode(cls,row)
            except (ValueError,TypeError,KeyError) as error:
                errors.append(f'{path}: {error}')

    sources = {s.get('source_id'):s for s in dispatch.get('sources',[]) if isinstance(s,dict)}

    def source_text(entry):
        # Span presentation (T7): '[src-000001#1] text' lines replace the raw
        # text; quote matching only needs whitespace-insensitive containment.
        if 'spans' in entry and isinstance(entry['spans'], list):
            return '\n'.join(str(line).split('] ', 1)[-1] for line in entry['spans'] if isinstance(line, str))
        return entry.get('text', '')

    claims = analysis.get('claims',[])
    if isinstance(claims,list):
        for index,row in enumerate(claims):
            if not isinstance(row,dict) or not isinstance(row.get('source_ids',[]),list):
                continue
            refs = row.get('source_ids',[])
            if any(not isinstance(ref,str) or ref not in sources for ref in refs):
                errors.append(f'analysis.claims[{index}].source_ids: CLAIM_SOURCE_UNKNOWN')
            if row.get('origin','explicit') in ('explicit','choice'):
                raw_quote = row.get('quote')
                if 'quote' in row and (raw_quote is None or
                        (isinstance(raw_quote,str) and raw_quote.strip().casefold() in ('none','null'))):
                    human_refs = [ref for ref in refs if isinstance(ref,str) and ref in sources
                        and sources[ref].get('kind') in ('request','answer','feedback')]
                    cited = human_refs[0] if human_refs else 'the cited human source'
                    excerpt = ''
                    if human_refs:
                        excerpt = ' Copy from it: ' + source_text(sources[human_refs[0]])[:300]
                    errors.append(f'analysis.claims[{index}].quote: QUOTE_IS_NULL_LITERAL; '
                                  f'the quote is null or the string "None" - that is not a quote. '
                                  f'Replace it with the verbatim text of {cited}: copy the exact '
                                  'characters, no placeholder, no null.' + excerpt)
                    continue
                quote = raw_quote or row.get('statement')
                if isinstance(quote,str) and not any(isinstance(ref,str) and ref in sources
                    and sources[ref].get('kind') in ('request','answer','feedback')
                    and claim_quote_matches(quote,source_text(sources[ref])) for ref in refs):
                    human_refs = [ref for ref in refs if isinstance(ref,str) and ref in sources
                        and sources[ref].get('kind') in ('request','answer','feedback')]
                    cited = human_refs[0] if human_refs else 'a cited human source'
                    excerpt = ''
                    if human_refs:
                        excerpt = ' Full source text: ' + source_text(sources[human_refs[0]])[:300]
                    errors.append(f'analysis.claims[{index}].quote: EXPLICIT_CLAIM_REQUIRES_HUMAN_QUOTE; '
                                  f'set quote to a verbatim contiguous excerpt of {cited} '
                                  '(copy the source text exactly, keep path separators and case; '
                                  'statement may paraphrase but quote may not; when unsure, copying '
                                  'the entire source text as the quote is acceptable).' + excerpt)
    consensus = dispatch.get('consensus', {})
    previous_assumptions = {row.get('assumption_id'):row for row in consensus.get('assumptions',[])
                            if isinstance(row,dict) and isinstance(row.get('assumption_id'),str)}
    assumptions = analysis.get('assumptions',[])
    if isinstance(assumptions,list):
        for index,row in enumerate(assumptions):
            if not isinstance(row,dict):
                continue
            previous = previous_assumptions.get(row.get('assumption_id')) if isinstance(row.get('assumption_id'),str) else None
            if row.get('disposition') == 'accepted' and not (previous and previous.get('disposition') == 'accepted'):
                errors.append(f'analysis.assumptions[{index}].disposition: MODEL_CANNOT_ACCEPT_ASSUMPTION; '
                              'set disposition="proposed". Only human input can accept a new or previously unaccepted assumption; '
                              'adding/changing assumption_id or local_key does not grant acceptance.')
            elif previous and previous.get('disposition') == 'accepted':
                try:
                    current = decode(AssumptionRecord,{key:value for key,value in row.items() if key != 'local_key'})
                    original = decode(AssumptionRecord,previous)
                except (ValueError,TypeError,KeyError):
                    continue  # Record decode errors are already collected above.
                if current != original:
                    errors.append(f'analysis.assumptions[{index}]: USER_ACCEPTED_ASSUMPTION_PROTECTED; '
                                  'omit this record or return the existing accepted record unchanged, including its assumption_id.')
    known = {}
    for name,id_field,rows in (
        ('requirements','requirement_id',analysis.get('claims',[])),
        ('assumptions','assumption_id',analysis.get('assumptions',[])),
        ('questions','question_id',payload.get('questions',[]))):
        old = dispatch.get('questions',[]) if name=='questions' else consensus.get('claims' if name=='requirements' else name,[])
        known[name] = {row.get(id_field) for row in old if isinstance(row,dict) and isinstance(row.get(id_field),str)}
        if isinstance(rows,list):
            known[name].update(row.get(id_field) for row in rows if isinstance(row,dict) and isinstance(row.get(id_field),str))
            known[name].update(row.get('local_key') for row in rows if isinstance(row,dict) and isinstance(row.get('local_key'),str))
    questions = payload.get('questions',[])
    if isinstance(questions,list):
        graph = {}
        question_keys = {}
        for index,row in enumerate(questions):
            if not isinstance(row,dict):
                continue
            identity = row.get('question_id') or row.get('local_key') or f'validation-{index}'
            if not isinstance(identity,str):
                continue
            question_keys[identity] = identity
            if isinstance(row.get('local_key'),str):
                question_keys[row['local_key']] = identity
            deps = row.get('depends_on_question_ids',[])
            graph[identity] = [dep for dep in deps if isinstance(dep,str)] if isinstance(deps,list) else []
            for field,target in (('depends_on_question_ids','questions'),('related_requirement_ids','requirements')):
                refs = row.get(field,[])
                if isinstance(refs,list) and any(not isinstance(ref,str) or ref not in known[target] for ref in refs):
                    errors.append(f'questions[{index}].{field}: UNKNOWN_REFERENCE')
            assumption = row.get('default_assumption_id','')
            if isinstance(assumption,str) and assumption and assumption not in known['assumptions']:
                errors.append(f'questions[{index}].default_assumption_id: DEFAULT_ASSUMPTION_UNKNOWN')
        visiting,visited=set(),set()
        def visit(identity):
            if identity in visiting:
                return True
            if identity in visited:
                return False
            visiting.add(identity)
            cycle=any(visit(question_keys.get(dep,dep)) for dep in graph.get(identity,[]) if question_keys.get(dep,dep) in graph)
            visiting.remove(identity)
            visited.add(identity)
            return cycle
        if any(visit(identity) for identity in graph):
            errors.append('questions.depends_on_question_ids: QUESTION_DEPENDENCY_CYCLE')
    return errors
