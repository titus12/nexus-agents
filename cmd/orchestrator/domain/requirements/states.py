"""Pure requirements state handlers. All I/O is a committed effect intent."""
from dataclasses import asdict, replace
from ..context import ProgressUpdate
from ..decisions import ContextUpdate, EffectRequest, StateDecision
from ..events import TransitionRequest
from .model import (Outcome, SourceRecord, RequirementsState, PendingApproval, ApprovalRecord,
                    RequirementsRecovery, RequirementsFailure, Question, WorkItem, digest, next_id)
from .model import EvidenceAnswer, IssueResponse, ReviewIssue, decode
from .consensus import apply_analysis, add_feedback
from .consensus import revise_consensus
from .questions import merge_questions, eligible_questions, open_round, accept_answers, cover_blocking_assumptions
from .questions import propose_answer_changes, clarify_round_cap_reached
from .document import new_skeleton, commit_draft, validate_draft
from .lint import lint_draft
from .review import merge_review, next_repair_route, review_input_digest
from .budget import (BudgetExceeded, record_progress, relevant_facts_digest, new_work_epoch, charge_publication)
from .dispatch import build_dispatch, ROLES
from .transitions import RequirementsTransitionRegistry, REQ_BUSINESS, SYSTEM, TERMINAL

EFFECT_STATES = {'REQ_VALIDATE':'requirements_step','REQ_CLARIFY_PUBLISH':'requirements_clarification',
    'REQ_REVIEW_PUBLISH':'requirements_review_document','REQ_APPROVAL_VERIFY':'requirements_approval_verify',
    'REQ_PUBLISH':'requirements_publish','REQ_HANDOFF':'requirements_handoff','REQ_RETRY_WAIT':'requirements_retry'}


class RequirementsWorkflowState:
    def __init__(self, name, transitions):
        self.name, self.transitions = name, transitions
        self.failure_fingerprint = ''

    def enter(self, context):
        return StateDecision()

    def on_timeout(self, context):
        return StateDecision()

    def on_resume(self, context):
        raise ValueError('explicit requirements recovery input required')

    def goto(self, context, state, action, *, kind='initial', retry=False):
        resume = context.progression.resume_state if action == 'RESUME' else None
        target = self.transitions.target_for(self.name, action, resume)
        effects = ()
        if target in ROLES:
            state, effect = build_dispatch(context, state, target, kind, retry)
            effects = (effect,)
        elif target in EFFECT_STATES:
            key = (state.recovery.logical_dispatch_id if retry and state.recovery else '') or f'{context.identity.task_id}:{target}:{context.progression.sequence+1}'
            identity = key + f':retry-{context.progression.sequence+1}' if retry else key
            effects = (EffectRequest('requirements:'+identity, EFFECT_STATES[target], context.identity.task_id, key,
                payload={'target_state':target,'state':state.to_dict(),'actor_ids':list(context.request.decision_actor_ids),
                         'sequence':context.progression.sequence,'logical_key':key}),)
        if target in ('REQ_BLOCKED','REQ_ENGINE_ERROR'):
            state = replace(state, outcome=Outcome.PREREQUISITE_BLOCKED if target == 'REQ_BLOCKED' else Outcome.ENGINE_ERROR)
            key = f'{context.identity.task_id}:notice:{target}:{context.progression.sequence+1}'
            effects += (EffectRequest(key, 'requirements_notice', context.identity.task_id, key,
                payload={'target_state': target, 'state': state.to_dict()}),)
        elif target == 'REQ_CONFIRM_WAIT':
            state = replace(state, outcome=Outcome.WAITING_CONFIRMATION)
        elif target not in ('REQ_CLARIFY_WAIT','REQ_CLARIFY_PUBLISH','REQ_CANCELLED','MENXIA_REQUIREMENTS_INTAKE'):
            state = replace(state, outcome=Outcome.IN_PROGRESS)
        progression = ProgressUpdate(resume_state=self.name) if target in ('REQ_BLOCKED','REQ_ENGINE_ERROR','REQ_RETRY_WAIT') else None
        return StateDecision(TransitionRequest(action, action), ContextUpdate(progression=progression, requirements=state), effects)

    def fail(self, context, state, code, message, *, shape=False, external=False, artifact='', publication_key='', prerequisite_fingerprint='', recovery_source_ref=''):
        failure = RequirementsFailure(code, self.name, 'requirements', message, recovery_source_ref or 'runtime-code',
                                      prerequisite_fingerprint=prerequisite_fingerprint or self.failure_fingerprint,
                                      unblock_condition='提供已变化且可核验的输入或修复依据')
        logical = (state.active_dispatch.logical_dispatch_id if self.name in ROLES and state.active_dispatch
                   else getattr(state.recovery,'logical_dispatch_id','') if self.name == 'REQ_RETRY_WAIT'
                   else publication_key or f'{context.identity.task_id}:{self.name}:publication')
        recovery = RequirementsRecovery(self.name, self.name, logical, artifact, failure)
        state = replace(state, reason=message, recovery=recovery, pending_approval=None, approval=None)
        if code in ('PREREQUISITE_MISSING','ACCESS_DENIED','SOURCE_UNAVAILABLE'):
            return self.goto(context, state, 'PREREQUISITE_MISSING')
        if external and self.name in EFFECT_STATES:
            try:
                state = replace(state,budget=charge_publication(state.budget,logical),
                                recovery=replace(recovery,repair_fingerprint='publication'))
                return self.goto(context,state,'RETRYABLE_FAILURE')
            except BudgetExceeded:
                pass
        if (shape or external) and logical:
            charge = next((r for r in state.budget.dispatch_charges if r.logical_dispatch_id == logical), None)
            count = charge.format_repairs if shape and charge else charge.external_retries if charge else 2
            if count < 2 and state.budget.calls < 12:
                state = replace(state, recovery=replace(recovery, repair_fingerprint='format' if shape else 'external'))
                return self.goto(context, state, 'RETRYABLE_FAILURE')
        return self.goto(context, state, 'ENGINE_FAILURE')

    def handle(self, context, event):
        decision = self._handle_event(context,event)
        if decision.transition:
            progression = decision.update.progression or ProgressUpdate()
            return replace(decision,update=replace(decision.update,
                progression=replace(progression,entered_at=event.occurred_at or context.progression.entered_at)))
        return decision

    def _handle_event(self, context, event):
        if context.progression.state != self.name or event.task_id != context.identity.task_id:
            raise ValueError('requirements event binding mismatch')
        state = context.requirements
        if state is None:
            raise ValueError('requirements aggregate missing')
        payload = dict(event.payload_mapping)
        action = event.name
        if self.name in TERMINAL:
            return StateDecision(update=ContextUpdate(requirements=state))
        if action == 'DECISION_OWNERS_REPAIRED':
            from .owner_recovery import validate_owner_recovery
            actors = tuple(payload.get('actor_ids',()))
            try:
                validate_owner_recovery(context,actors)
                if (not payload.get('_runtime_verified') or not payload.get('new_fingerprint')
                    or payload.get('expected_sequence') != context.progression.sequence
                    or payload.get('expected_failure_fingerprint') != state.recovery.failure.prerequisite_fingerprint):
                    raise ValueError('OWNER_REPAIR_BASIS_CHANGED')
            except ValueError as error:
                return StateDecision(update=ContextUpdate(requirements=replace(state,reason=str(error))))
            state = replace(state,rounds=tuple(replace(r,actor_ids=actors) for r in state.rounds),reason='')
            projected = replace(context,request=replace(context.request,decision_actor_ids=actors))
            decision = self.goto(projected,state,'RESUME')
            return replace(decision,update=replace(decision.update,repair_decision_actor_ids=actors))
        if action == 'CANCEL':
            state = replace(state, outcome=Outcome.CANCELLED, pending_approval=None,
                            rounds=tuple(replace(r, status='cancelled') if r.status not in ('answered','superseded') else r for r in state.rounds))
            decision = self.goto(context, state, 'CANCEL')
            key = f'cancel:{context.identity.task_id}:{context.progression.sequence+1}'
            cleanup = EffectRequest(key, 'requirements_cleanup', context.identity.task_id, key, payload={'state':state.to_dict()})
            return replace(decision, effects=(cleanup,))
        if action == 'STALE_RESULT':
            return StateDecision(update=ContextUpdate(requirements=state))
        if action == 'REQUIREMENTS_FEEDBACK':
            if self.name in ('REQ_PUBLISH','REQ_HANDOFF','MENXIA_REQUIREMENTS_INTAKE'):
                source = SourceRecord(next_id(state.sources,'source_id','src'),'feedback',payload['text'],
                    payload.get('actor_id','local-operator'),event.event_id,event.occurred_at)
                return StateDecision(update=ContextUpdate(requirements=replace(state,sources=state.sources+(source,),
                    reason='已批准；补充登记为后续需求变更：'+payload['text'])))
            from .chapter_lock import release_locks
            from .document import KIND_TITLES
            text = payload['text']
            requested = tuple(payload.get('section_kinds') or ())
            valid = tuple(kind for kind in requested if kind in KIND_TITLES)
            ignored = tuple(kind for kind in requested if kind not in KIND_TITLES)
            if valid:
                text = '【针对章节：' + '、'.join(KIND_TITLES[kind] for kind in valid) + '】' + text
            updated = add_feedback(state, text, event.event_id, payload.get('actor_id','local-operator'), event.occurred_at)
            if updated == state:
                return StateDecision()
            updated = release_locks(updated, valid if requested else None)
            if ignored:
                updated = replace(updated, reason='未识别的章节，已忽略：' + '、'.join(ignored))
            if self.name in ('REQ_BLOCKED','REQ_ENGINE_ERROR','REQ_RETRY_WAIT'):
                return StateDecision(update=ContextUpdate(requirements=updated))
            updated = replace(updated, budget=new_work_epoch(updated.budget, digest((payload['text'], 'feedback'))))
            return self.goto(context, updated, 'FEEDBACK_TO_DRAFT', kind='initial')
        try:
            if action == 'ANSWERS_SUBMITTED':
                if self.name=='MENXIA_REQUIREMENTS_INTAKE':
                    return StateDecision(update=ContextUpdate(requirements=replace(state,reason='需求已交接，旧回答不能变更批准输入。')))
                if any(not isinstance(row.get('free_text',''),str) for row in payload.get('answers',())):
                    raise ValueError('INVALID_ANSWER_CONTENT')
                submitted_round = next((r for r in state.rounds if r.round_id == payload.get('round_id')),None)
                conflict = self.name != 'REQ_CLARIFY_WAIT' or not submitted_round or submitted_round.status not in ('preparing','open','partial')
                if not conflict and submitted_round:
                    conflict = any(any(a.question_id == row.get('question_id') and a.question_revision == row.get('question_revision')
                        and (a.option_ids,a.free_text.strip(),a.disposition) != (tuple(row.get('option_ids',())),row.get('free_text','').strip(),row.get('disposition','answered'))
                        for a in state.answers) for row in payload.get('answers',()))
                if conflict:
                    updated = propose_answer_changes(state,payload,event.event_id,event.occurred_at)
                    if updated == state:
                        return StateDecision()
                    if self.name not in ('REQ_PUBLISH','REQ_HANDOFF','REQ_BLOCKED','REQ_ENGINE_ERROR','REQ_RETRY_WAIT'):
                        updated = open_round(updated,'REQ_DRAFT',context.request.decision_actor_ids)
                        return self.goto(context,updated,'ANSWER_CONFLICT')
                    if self.name in ('REQ_BLOCKED','REQ_ENGINE_ERROR','REQ_RETRY_WAIT'):
                        return StateDecision(update=ContextUpdate(requirements=replace(updated,reason='决定变更已记录；解除前提后进入确认。')))
                    return StateDecision(update=ContextUpdate(requirements=replace(state,reason='答案变更已收到，请使用当前需求变更入口。')))
            return self._handle(context, state, event, payload)
        except BudgetExceeded as error:
            return self.fail(context, state, str(error), str(error))
        except (ValueError, KeyError, TypeError) as error:
            if action in ('ANSWERS_SUBMITTED','REQUIREMENTS_APPROVED','REQUIREMENTS_SECTION_CONFIRMED','RECOVERY_INPUT'):
                return StateDecision(update=ContextUpdate(requirements=replace(state, reason=str(error))))
            return self.fail(context, state, 'REQUIREMENTS_RESULT_INVALID', str(error), shape=self.name in ROLES,
                             artifact=str(payload.get('result_artifact_id') or payload.get('raw_reply_path') or ''))

    def _handle(self, context, state, event, payload):
        action = event.name
        if action == 'FACTS_CHANGED':
            changes = {row['fact_id']:row['source_fingerprint'] for row in payload['changes']}
            facts = tuple(replace(f,status='unavailable',source_fingerprint=changes[f.fact_id])
                          if f.fact_id in changes else f for f in state.consensus.facts)
            state = revise_consensus(state,facts=facts)
            from .chapter_lock import release_locks
            state = release_locks(state, fact_ids=tuple(changes))
            findings = tuple(ReviewIssue('source-changed-'+identity,'SOURCE_VERSION_CHANGED',
                tuple(s.section_id for s in state.draft.sections if identity in s.fact_ids),(),
                '被正文引用的事实来源已变化','原来的审查依据不再有效','blocking','analyst','核对当前来源并提供对应引文') for identity in changes)
            state = replace(state,issues=tuple(i for i in state.issues if i.issue_id not in {x.issue_id for x in findings})+findings,
                            budget=new_work_epoch(state.budget,digest(changes)))
            return self.goto(context,state,action,kind='evidence')
        if action == 'FAIL':
            code = str(payload.get('error_code') or payload.get('failure',{}).get('error_code') or 'ENGINE_FAILURE')
            shape = code == 'FACT_EVIDENCE_INVALID' or any(s in code for s in ('REPLY','CONTRACT','UNSTRUCTURED','FORMAT'))
            external = bool(payload.get('retryable') or payload.get('failure',{}).get('retryable')) and not shape
            return self.fail(context, state, code, str(payload.get('reason') or code), shape=shape, external=external,
                             artifact=str(payload.get('raw_reply_path') or payload.get('result_artifact_id') or ''),
                             publication_key=payload.get('publication_logical_key',''),
                             prerequisite_fingerprint=payload.get('prerequisite_fingerprint',''),
                             recovery_source_ref=payload.get('recovery_source_ref',''))
        if action == 'PREREQUISITE_MISSING':
            return self.fail(context, state, action, payload.get('reason') or '缺少必要输入、权限或事实来源')
        if self.name == 'REQ_INTAKE' and action == 'START':
            state = replace(state, draft=new_skeleton(context.request.raw_request, state.consensus.digest))
            return self.goto(context, state, 'START')
        if self.name == 'REQ_ANALYZE' and action == 'ANALYSIS_READY':
            aliases = {}
            state = apply_analysis(state, payload, aliases=aliases)
            state = replace(state,work_queue=tuple(replace(w,status='done') if w.kind=='analysis' and w.status=='pending' else w
                                                  for w in state.work_queue))
            state = merge_questions(state, payload.get('questions', ()), aliases)
            # Draft-first (2026-10-10 D6): blocking assumptions are no longer
            # converted to questions here.  They are covered after the Solver's
            # DRAFT_READY so the user always answers against a written draft.
            if eligible_questions(state):
                state = open_round(state, 'REQ_ANALYZE', context.request.decision_actor_ids)
                return self.goto(context, state, 'ANALYSIS_NEEDS_USER')
            return self.goto(context, state, 'ANALYSIS_ACCEPTED')
        if self.name == 'REQ_DRAFT':
            if action == 'DRAFT_READY':
                draft = commit_draft(state.draft, payload, state.consensus)
                state = replace(state, draft=draft, approval=None, pending_approval=None)
                state = merge_questions(state,payload.get('questions',()))
                # Draft-first (2026-10-10 D6): blocking assumptions become
                # questions only after the draft exists, so they can never be
                # silently dropped.
                state = cover_blocking_assumptions(state)
                responses = tuple(decode(IssueResponse,r) for r in payload.get('issue_responses',()))
                if any(r.issue_id not in {i.issue_id for i in state.issues} or not r.response.strip() for r in responses):
                    raise ValueError('SOLVER_RESPONSE_BINDING_INVALID')
                state = replace(state,solver_responses=tuple(r for r in state.solver_responses if r.issue_id not in {a.issue_id for a in responses})+responses)
                if eligible_questions(state) and not clarify_round_cap_reached(state):
                    state = open_round(state,'REQ_DRAFT',context.request.decision_actor_ids)
                    return self.goto(context,state,'NEEDS_CLARIFICATION')
                return self.goto(context, state, 'NEEDS_ANALYSIS' if state.consensus.pending_source_ids else 'DRAFT_READY')
            if action == 'NEEDS_CLARIFICATION':
                state = merge_questions(state, payload.get('questions', ()))
                state = open_round(state, 'REQ_DRAFT', context.request.decision_actor_ids)
                return self.goto(context, state, action)
            if action in ('NEEDS_EVIDENCE','NEEDS_ANALYSIS'):
                if not payload.get('evidence_requests' if action == 'NEEDS_EVIDENCE' else 'analysis_requests'):
                    raise ValueError('DIRECTED_REQUEST_REQUIRED')
                requests = payload.get('evidence_requests' if action == 'NEEDS_EVIDENCE' else 'analysis_requests')
                details = '\n'.join(requests) if action == 'NEEDS_EVIDENCE' else '\n'.join(r['reason'] for r in requests)
                sources = () if action == 'NEEDS_EVIDENCE' else tuple(sid for r in requests for sid in r['source_ids'])
                if not set(sources) <= {s.source_id for s in state.sources}:
                    raise ValueError('DIRECTED_SOURCE_UNKNOWN')
                work = WorkItem(next_id(state.work_queue,'work_id','work'),'evidence' if action == 'NEEDS_EVIDENCE' else 'analysis',
                                details=details,source_ids=sources)
                state = replace(state,work_queue=state.work_queue+(work,))
                return self.goto(context, state, action, kind='evidence' if action == 'NEEDS_EVIDENCE' else 'initial')
        if self.name == 'REQ_VALIDATE' and action in ('VALID','REPAIR_CONTENT'):
            mechanical = validate_draft(state.draft, state.consensus, state.decisions, state.chapter_locks)
            lint = lint_draft(state.draft, state.consensus, state.sources)
            issues = tuple(i for i in state.issues
                           if not i.issue_id.startswith(('mechanical-','lint-'))) + mechanical + lint
            state = replace(state, issues=issues)
            if any(i.owner == 'analyst' for i in mechanical):
                return self.goto(context,state,'NEEDS_EVIDENCE',kind='evidence')
            return self.goto(context, state, 'REPAIR_CONTENT' if mechanical else 'VALID', kind='semantic' if mechanical else 'initial')
        if self.name == 'REQ_REVIEW' and action == 'REVIEW_READY':
            state = merge_review(state, payload)
            state = merge_questions(state, payload.get('questions', ()))
            route = next_repair_route(state)
            if route in ('REVIEW_NEEDS_DRAFT','REVIEW_NEEDS_EVIDENCE','REVIEW_NEEDS_USER'):
                owner = {'REVIEW_NEEDS_DRAFT':'solver','REVIEW_NEEDS_EVIDENCE':'analyst','REVIEW_NEEDS_USER':'user'}[route]
                ids = tuple(i.issue_id for i in state.issues if i.status == 'open' and i.owner == owner)
                if not any(w.issue_ids == ids and w.status == 'pending' for w in state.work_queue):
                    work = WorkItem(next_id(state.work_queue,'work_id','work'),owner,issue_ids=ids)
                    state = replace(state,work_queue=state.work_queue+(work,))
            if route == 'ENGINE_FAILURE':
                return self.fail(context, state, 'REVIEW_ENGINE_ISSUE', '审查发现编排故障')
            if route != 'REVIEW_PASSED':
                state = replace(state, budget=record_progress(state.budget, state.draft.document_digest,
                    relevant_facts_digest(state.consensus.facts), digest(state.consensus.active_decision_ids)))
            if route == 'REVIEW_NEEDS_USER':
                if not eligible_questions(state):
                    rows = []
                    for issue in state.issues:
                        if issue.owner == 'user' and issue.status == 'open':
                            identity = next_id(state.questions + tuple(rows), 'question_id', 'q')
                            rows.append(Question(identity, issue.rule, issue.claim+'，希望采用什么行为？', issue.claim,
                                                 issue.impact, 'text', related_requirement_ids=issue.requirement_ids, related_issue_ids=(issue.issue_id,)))
                    state = replace(state, questions=state.questions+tuple(rows))
                state = open_round(state, 'REQ_DRAFT', context.request.decision_actor_ids)
            return self.goto(context, state, route, kind='semantic' if route == 'REVIEW_NEEDS_DRAFT' else 'evidence' if route == 'REVIEW_NEEDS_EVIDENCE' else 'initial')
        if self.name == 'REQ_EVIDENCE' and action == 'EVIDENCE_READY':
            aliases = {}
            state = apply_analysis(state, {'fact_updates':payload.get('fact_updates', ())}, aliases=aliases, preserve_pending=True)
            answers = tuple(decode(EvidenceAnswer,{**row,'fact_ids':[aliases.get(i,i) for i in row.get('fact_ids',())]})
                            for row in payload.get('issue_answers',()))
            known_issues = {i.issue_id for i in state.issues}
            known_facts = {f.fact_id for f in state.consensus.facts}
            if any(a.issue_id not in known_issues or not set(a.fact_ids) <= known_facts or not a.answer.strip() for a in answers):
                raise ValueError('EVIDENCE_ANSWER_BINDING_INVALID')
            state = replace(state,evidence_answers=tuple(a for a in state.evidence_answers if a.issue_id not in {r.issue_id for r in answers})+answers)
            state = replace(state,work_queue=tuple(replace(w,status='done') if w.kind=='evidence' and not w.issue_ids and w.status=='pending' else w
                                                  for w in state.work_queue))
            needs_change = any(r.get('needs_draft_change') for r in payload.get('issue_answers', ()))
            return self.goto(context, state, 'EVIDENCE_TO_DRAFT' if needs_change else 'EVIDENCE_TO_REVIEW', kind='semantic' if needs_change else 'initial')
        if self.name == 'REQ_CLARIFY_PUBLISH' and action == 'CARD_DELIVERED':
            state = replace(state, rounds=tuple(replace(r, status='open') if r == state.rounds[-1] and r.status == 'preparing' else r for r in state.rounds))
            return self.goto(context, state, action)
        if self.name == 'REQ_CLARIFY_WAIT' and action == 'ANSWERS_SUBMITTED':
            updated = accept_answers(state, {**payload, 'event_id':event.event_id})
            if updated == state:
                return StateDecision()
            updated = replace(updated, budget=new_work_epoch(updated.budget, digest(updated.consensus.active_decision_ids)))
            if updated.rounds[-1].status != 'answered':
                key = f'{context.identity.task_id}:answers:{context.progression.sequence+1}'
                effect = EffectRequest(key,'requirements_clarification_refresh',context.identity.task_id,key,
                    payload={'target_state':self.name,'state':updated.to_dict()})
                return StateDecision(update=ContextUpdate(requirements=updated),effects=(effect,))
            target = updated.rounds[-1].return_to
            return self.goto(context, updated, 'ANSWERS_TO_ANALYZE' if target == 'REQ_ANALYZE' else 'ANSWERS_TO_DRAFT')
        if self.name == 'REQ_REVIEW_PUBLISH' and action == 'REVIEW_DOCUMENT_DELIVERED':
            state = replace(state, review_document_ref=payload['document_ref'], review_gate_id=payload['gate_id'],
                            review_cloud_ref=payload.get('cloud_revision_ref',''))
            return self.goto(context, state, action)
        if action == 'REQUIREMENTS_SECTION_CONFIRMED':
            if self.name != 'REQ_CONFIRM_WAIT':
                return StateDecision(update=ContextUpdate(requirements=replace(state, reason='当前不能确认章节：' + self.name)))
            actor = payload.get('actor_id','')
            from .authorization import authorized_actor
            from .chapter_lock import confirm_sections
            if not authorized_actor(actor,context.request.decision_actor_ids,payload):
                raise ValueError('UNAUTHORIZED_SECTION_CONFIRMATION')
            if payload.get('document_digest') != state.draft.document_digest or payload.get('gate_id') != state.review_gate_id:
                raise ValueError('STALE_SECTION_CONFIRMATION')
            if not state.review_stamp or not state.review_stamp.passed or state.review_stamp.input_digest != review_input_digest(state.draft, state.consensus):
                raise ValueError('VALID_REVIEW_REQUIRED')
            updated = confirm_sections(state, tuple(payload.get('section_kinds', ())), actor_id=actor,
                                       event_id=event.event_id, occurred_at=event.occurred_at or '')
            return StateDecision(update=ContextUpdate(requirements=updated))
        if self.name == 'REQ_CONFIRM_WAIT' and action == 'REQUIREMENTS_APPROVED':
            actor = payload.get('actor_id','')
            from .authorization import authorized_actor
            if not authorized_actor(actor,context.request.decision_actor_ids,payload):
                raise ValueError('UNAUTHORIZED_APPROVAL')
            if payload.get('document_digest') != state.draft.document_digest or payload.get('gate_id') != state.review_gate_id:
                raise ValueError('STALE_APPROVAL')
            if not state.review_stamp or not state.review_stamp.passed or state.review_stamp.input_digest != review_input_digest(state.draft, state.consensus):
                raise ValueError('VALID_REVIEW_REQUIRED')
            pending = PendingApproval(event.event_id, state.review_gate_id, state.draft.document_digest,
                                      state.review_stamp.input_digest, actor, event.event_id, event.occurred_at)
            return self.goto(context, replace(state, pending_approval=pending), action)
        if self.name == 'REQ_APPROVAL_VERIFY' and action == 'APPROVAL_VERIFIED':
            if not state.pending_approval or state.pending_approval.document_digest != state.draft.document_digest:
                raise ValueError('STALE_APPROVAL')
            if payload.get('review_input_digest') != review_input_digest(state.draft, state.consensus):
                raise ValueError('STALE_REVIEW')
            approval = ApprovalRecord(f'approval-{context.progression.sequence+1:06d}',
                state.draft.document_digest, state.consensus.digest, state.review_stamp.input_digest,
                state.pending_approval.actor_id, state.pending_approval.event_id, event.occurred_at,
                payload.get('cloud_revision_ref',''))
            return self.goto(context, replace(state, approval=approval, pending_approval=None), action)
        if self.name == 'REQ_PUBLISH' and action == 'REQUIREMENTS_PUBLISHED':
            return self.goto(context, replace(state, published_ref=payload['published_ref']), action)
        if self.name == 'REQ_HANDOFF' and action == 'HANDOFF_PERSISTED':
            decision = self.goto(context, replace(state, handoff_ref=payload['handoff_ref'], outcome=Outcome.REQUIREMENTS_READY), action)
            return replace(decision,update=replace(decision.update,clear_review=True))
        if self.name == 'REQ_RETRY_WAIT' and action == 'RETRY_READY':
            kind = state.recovery.repair_fingerprint
            return self.goto(context, state, 'RESUME', kind=kind, retry=True)
        if self.name in ('REQ_BLOCKED','REQ_ENGINE_ERROR') and action == 'RECOVERY_INPUT':
            if not payload.get('_runtime_verified') or not payload.get('new_fingerprint') or payload['new_fingerprint'] == state.recovery.repair_fingerprint:
                raise ValueError('VERIFIED_CHANGED_PREREQUISITE_REQUIRED')
            state = replace(state, budget=new_work_epoch(state.budget, payload['new_fingerprint']))
            if state.consensus.pending_source_ids and state.recovery.failed_state != 'REQ_ANALYZE':
                return self.goto(context,state,'FEEDBACK_TO_DRAFT')
            return self.goto(context, state, 'RESUME', kind='format' if state.recovery.failed_state == 'REQ_ANALYZE' else 'initial')
        if action == 'ENGINE_FAILURE':
            return self.fail(context, state, action, payload.get('reason', action))
        if action == 'CARD_REFRESH_FAILED':
            return StateDecision(update=ContextUpdate(requirements=replace(state,reason=payload['reason'])))
        raise ValueError(f'UNSUPPORTED_REQUIREMENTS_ACTION:{self.name}:{action}')


def requirements_states(transitions):
    return {name: RequirementsWorkflowState(name, transitions) for name in REQ_BUSINESS + SYSTEM + TERMINAL if name.startswith('REQ_')}
