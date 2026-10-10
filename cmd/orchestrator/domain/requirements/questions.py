"""Question identities, dependency-aware rounds and atomic partial submissions."""
from dataclasses import replace
from .authorization import authorized_actor
from types import SimpleNamespace
from .model import (Question, ClarificationRound, SourceRecord, DecisionRecord,
                    AnswerRecord, RequirementClaim, QuestionOption, Outcome, decode, next_id, digest)
from .consensus import active_decisions, revise_consensus


def merge_questions(state, rows, aliases=None):
    aliases = {} if aliases is None else dict(aliases)
    rows = [dict(row) for row in rows]
    reserved = list(state.questions)
    for row in rows:
        key = row.pop('local_key', '')
        identity = row.get('question_id') or next_id(reserved, 'question_id', 'q')
        row['question_id'] = identity
        if key:
            if key in aliases:
                raise ValueError('LOCAL_KEY_COLLISION')
            aliases[key] = identity
        reserved.append(SimpleNamespace(question_id=identity))
    for row in rows:
        for field in ('depends_on_question_ids','related_requirement_ids','related_issue_ids'):
            row[field] = [aliases.get(i,i) for i in row.get(field,())]
        row['default_assumption_id'] = aliases.get(row.get('default_assumption_id',''),row.get('default_assumption_id',''))
    questions = list(state.questions)
    for raw in rows:
        row = dict(raw)
        row.pop('local_key', None)
        existing = next((q for q in questions if q.question_id == row.get('question_id')), None)
        row['question_id'] = row.get('question_id') or next_id(questions, 'question_id', 'q')
        question = decode(Question, row)
        if existing and question != existing:
            if any(a.question_id == existing.question_id for a in state.answers) and not state.consensus.pending_source_ids:
                raise ValueError('ANSWERED_QUESTION_REQUIRES_NEW_BASIS')
            question = replace(question, revision=existing.revision + 1)
            questions[questions.index(existing)] = question
        elif not existing:
            same = next((q for q in questions if q.topic_key == question.topic_key
                         and q.question == question.question and q.options == question.options
                         and q.depends_on_question_ids == question.depends_on_question_ids
                         and q.related_requirement_ids == question.related_requirement_ids), None)
            if not same:
                questions.append(question)
    known = {q.question_id for q in questions}
    for q in questions:
        if not set(q.depends_on_question_ids) <= known or q.question_id in q.depends_on_question_ids:
            raise ValueError('QUESTION_DEPENDENCY_UNKNOWN')
    visiting, visited = set(), set()
    def visit(identity):
        if identity in visiting:
            raise ValueError('QUESTION_DEPENDENCY_CYCLE')
        if identity in visited:
            return
        visiting.add(identity)
        for dep in next(q for q in questions if q.question_id == identity).depends_on_question_ids:
            visit(dep)
        visiting.remove(identity)
        visited.add(identity)
    for identity in known:
        visit(identity)
    return replace(state, questions=tuple(questions))


def eligible_questions(state):
    answered = {(a.question_id, a.question_revision) for a in state.answers}
    done_ids = {q.question_id for q in state.questions if (q.question_id, q.revision) in answered}
    return tuple(q for q in state.questions if (q.question_id, q.revision) not in answered
                 and set(q.depends_on_question_ids) <= done_ids)


def cover_blocking_assumptions(state):
    """Do not publish a partial first batch while known blocking choices remain hidden."""
    questions = list(state.questions)
    for assumption in state.consensus.assumptions:
        if not assumption.blocking or assumption.disposition != 'proposed':
            continue
        if any(q.default_assumption_id == assumption.assumption_id for q in questions):
            continue
        basis = '、'.join(assumption.source_ids) or assumption.assumption_id
        questions.append(Question(next_id(questions, 'question_id', 'q'),
            'assumption:' + assumption.assumption_id,
            '是否确认这项基本约束：' + assumption.statement,
            ('新增依据：' if state.rounds else '首轮基本约束：') + basis + '；该阻塞假设尚未确认。',
            '决定需求草案和验收基线，不能由代理代替用户选择。', 'single',
            (QuestionOption('confirm_assumption', assumption.statement, '明确确认该约束'),
             QuestionOption('other', '不采用，请填写替代约束', '按填写的替代约束继续分析')),
            default_assumption_id=assumption.assumption_id))
    return replace(state, questions=tuple(questions))


def open_round(state, return_to, actor_ids=()):
    questions = eligible_questions(state)
    if not questions:
        raise ValueError('NO_ELIGIBLE_QUESTIONS')
    topics = {q.topic_key: index for index,q in reversed(tuple(enumerate(questions)))}
    questions = tuple(sorted(questions,key=lambda q:topics[q.topic_key]))
    identity = next_id(state.rounds, 'round_id', 'round')
    pages = tuple(f'{identity}-page-{i+1}' for i in range((len(questions)+4)//5))
    round_ = ClarificationRound(identity, state.consensus.digest,
                                tuple(q.question_id for q in questions), pages, 'preparing', return_to, tuple(actor_ids))
    old = tuple(replace(r, status='superseded') if r.status in ('preparing', 'open', 'partial') else r for r in state.rounds)
    return replace(state, rounds=old + (round_,), outcome=Outcome.WAITING_CLARIFICATION)


def accept_answers(state, submission):
    event_id = submission['event_id']
    if any(a.event_id == event_id for a in state.answers):
        return state
    round_ = next((r for r in state.rounds if r.round_id == submission.get('round_id')), None)
    if round_ is None or round_.status not in ('preparing', 'open', 'partial'):
        raise ValueError('STALE_QUESTION_ROUND')
    actor = submission.get('actor_id', '')
    if not authorized_actor(actor,round_.actor_ids,submission):
        raise ValueError('UNAUTHORIZED_ANSWER')
    rows = submission.get('answers', ())
    if not rows:
        raise ValueError('EMPTY_SUBMISSION')
    validated, seen = [], set()
    for row in rows:
        if row['question_id'] in seen:
            raise ValueError('DUPLICATE_ANSWER_FIELD')
        seen.add(row['question_id'])
        q = next((q for q in state.questions if q.question_id == row['question_id']), None)
        if not q or q.question_id not in round_.question_ids or row.get('question_revision') != q.revision:
            raise ValueError('STALE_QUESTION_REVISION')
        disposition = row.get('disposition', 'answered')
        options, text = tuple(row.get('option_ids', ())), row.get('free_text', '')
        if disposition not in ('answered', 'accept_default', 'defer'):
            raise ValueError('INVALID_ANSWER_DISPOSITION')
        if not isinstance(text, str) or len(text) > 1000 or len(set(options)) != len(options):
            raise ValueError('INVALID_ANSWER_CONTENT')
        if not set(options) <= {o.option_id for o in q.options}:
            raise ValueError('ANSWER_OPTION_UNKNOWN')
        if q.answer_kind == 'single' and len(options) > 1:
            raise ValueError('ANSWER_SINGLE_SELECTION')
        if disposition != 'answered' and (q.blocking or (disposition == 'accept_default' and not q.default_assumption_id)):
            raise ValueError('BLOCKING_QUESTION_UNANSWERED')
        if disposition == 'answered' and not options and not text.strip():
            raise ValueError('ANSWER_REQUIRED')
        if 'other' in options and not text.strip():
            raise ValueError('OTHER_REQUIRES_TEXT')
        previous = next((a for a in state.answers if a.question_id == q.question_id and a.question_revision == q.revision), None)
        if previous:
            if (previous.option_ids, previous.free_text.strip(), previous.disposition) == (options, text.strip(), disposition):
                continue
            raise ValueError('DECISION_CHANGE_REQUIRED')
        validated.append((q, options, text, disposition))
    if not validated:
        return state
    original_source_count = len(state.sources)
    sources, decisions, answers, claims = list(state.sources), list(state.decisions), list(state.answers), list(state.consensus.claims)
    assumptions = list(state.consensus.assumptions)
    for q, options, text, disposition in validated:
        selected = [o.label for o in q.options if o.option_id in options]
        conclusion = '\n'.join(selected + ([text] if text.strip() else []))
        if disposition == 'accept_default':
            assumption = next((a for a in assumptions if a.assumption_id == q.default_assumption_id), None)
            if not assumption:
                raise ValueError('DEFAULT_ASSUMPTION_UNKNOWN')
            conclusion = assumption.statement
            assumptions[assumptions.index(assumption)] = replace(assumption, disposition='accepted')
        elif disposition == 'defer':
            conclusion = '暂后处理：' + q.question
        elif q.default_assumption_id and q.topic_key == 'assumption:' + q.default_assumption_id:
            assumption = next((a for a in assumptions if a.assumption_id == q.default_assumption_id), None)
            if assumption and options in (('confirm_assumption',), ('other',)):
                assumptions[assumptions.index(assumption)] = replace(assumption,
                    disposition='accepted' if options == ('confirm_assumption',) else 'rejected')
        source = SourceRecord(next_id(sources, 'source_id', 'src'), 'answer', conclusion, actor, event_id)
        sources.append(source)
        answer = AnswerRecord(next_id(answers, 'answer_id', 'answer'), q.question_id, q.revision,
                              round_.round_id, options, text, disposition, source.source_id, event_id)
        answers.append(answer)
        if disposition != 'defer':
            old = tuple(d for d in active_decisions(replace(state, sources=tuple(sources), decisions=tuple(decisions))) if d.topic_key == q.topic_key)
            related = q.related_requirement_ids
            if not related:
                related = (next_id(claims, 'requirement_id', 'req'),)
            decision = DecisionRecord(next_id(decisions, 'decision_id', 'decision'), q.topic_key,
                                      conclusion, source.source_id, tuple(d.decision_id for d in old), related)
            decisions.append(decision)
            for identity in related:
                previous = next((c for c in claims if c.requirement_id == identity), None)
                claim = RequirementClaim(identity, q.topic_key, conclusion, previous.kind if previous else 'behavior',
                                         'choice', (source.source_id,), (decision.decision_id,), 'active')
                if previous:
                    claims[claims.index(previous)] = claim
                else:
                    claims.append(claim)
    state = replace(state, sources=tuple(sources), decisions=tuple(decisions), answers=tuple(answers))
    state = revise_consensus(state, claims=tuple(claims), assumptions=tuple(assumptions),
                             active_decision_ids=tuple(d.decision_id for d in active_decisions(state)),
                             pending_source_ids=state.consensus.pending_source_ids +
                                 (tuple(s.source_id for s in sources[original_source_count:]) if round_.return_to=='REQ_ANALYZE' else ()))
    complete = all(any(a.question_id == identity and a.question_revision == next(q.revision for q in state.questions if q.question_id == identity)
                       for a in state.answers) for identity in round_.question_ids)
    return replace(state, rounds=tuple(replace(r, status='answered' if complete else 'partial') if r.round_id == round_.round_id else r for r in state.rounds))


def conflicting_answers(state, submission):
    """A late different answer proposes a change; it cannot overwrite an active decision."""
    round_ = next((r for r in state.rounds if r.round_id == submission.get('round_id')),None)
    actor = submission.get('actor_id','')
    if not round_ or round_.status == 'cancelled' or not authorized_actor(actor,round_.actor_ids,submission):
        raise ValueError('STALE_OR_UNAUTHORIZED_ANSWER')
    changes = []
    for row in submission.get('answers',()):
        q = next((q for q in state.questions if q.question_id == row.get('question_id')),None)
        if not q or q.question_id not in round_.question_ids or row.get('question_revision') != q.revision:
            raise ValueError('STALE_QUESTION_REVISION')
        previous = next((a for a in reversed(state.answers) if a.question_id == q.question_id and a.question_revision == q.revision),None)
        if not previous:
            raise ValueError('USE_CURRENT_QUESTION_ROUND')
        options,text,disposition = tuple(row.get('option_ids',())),row.get('free_text',''),row.get('disposition','answered')
        if not isinstance(text,str):
            raise ValueError('INVALID_DECISION_CHANGE')
        if (options,text.strip(),disposition) == (previous.option_ids,previous.free_text.strip(),previous.disposition):
            continue
        if (not isinstance(text,str) or len(text)>1000 or disposition != 'answered'
            or not set(options) <= {o.option_id for o in q.options} or len(options)!=len(set(options))
            or (q.answer_kind=='single' and len(options)>1) or (not options and not text.strip())):
            raise ValueError('INVALID_DECISION_CHANGE')
        current = next(s.text for s in state.sources if s.source_id == previous.source_id)
        proposed = '\n'.join([o.label for o in q.options if o.option_id in options]+([text] if text.strip() else []))
        changes.append((q,current,proposed))
    return changes


def propose_answer_changes(state, submission, event_id, created_at):
    changes = conflicting_answers(state,submission)
    if not changes:
        return state
    from .consensus import add_feedback
    text = '\n\n'.join(q.question+'\n原决定：'+old+'\n本次回答：'+new for q,old,new in changes)
    state = add_feedback(state,text,event_id,submission['actor_id'],created_at)
    questions = list(state.questions)
    for q,old,new in changes:
        identity = next_id(questions,'question_id','q')
        questions.append(Question(identity,q.topic_key,'是否变更已确认的决定？\n原决定：'+old+'\n新回答：'+new,
            '旧卡片提交了不同答案，需要确认变更意图',q.impact,'single',
            (QuestionOption('keep',old,'维持已经确认的行为'),QuestionOption('change',new,'修改需求并重新审查')),
            related_requirement_ids=q.related_requirement_ids,related_issue_ids=q.related_issue_ids))
    return replace(state,questions=tuple(questions))
