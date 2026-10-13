"""Minimal role inputs: one authored document, current consensus and provenance."""
import os
from dataclasses import asdict, replace
from ..decisions import EffectRequest
from .model import RequirementsDispatch
from .budget import charge_dispatch
from .document import render_markdown
from .review import review_input_digest, AXES, coverage_basis
from .spans import atomize, normalized_text

ROLES = {'REQ_ANALYZE':'review-analyst','REQ_EVIDENCE':'review-analyst','REQ_DRAFT':'review-solver','REQ_REVIEW':'review-critic',
         'MENXIA_REQUIREMENTS_INTAKE':'review-solver'}
SKILLS = {'REQ_ANALYZE':'requirements-analyst','REQ_EVIDENCE':'requirements-analyst','REQ_DRAFT':'requirements-solver',
          'REQ_REVIEW':'requirements-critic','MENXIA_REQUIREMENTS_INTAKE':'menxia-solver'}


def source_presentation(source_rows):
    """Span-numbered user sources (T7 6.2): raw text appears exactly once,
    as '[<span.id>] <text>' lines, so models cite span ids instead of copying."""
    rows = []
    for source in source_rows:
        text = normalized_text(source)
        spans = atomize(source)
        lines = [f'[{span.id}] {span.slice_of(text)}' for span in spans]
        if not lines:
            lines = [f'[{source.source_id}#1] {text.strip()}']
        rows.append({'source_id':source.source_id, 'kind':source.kind, 'author_id':source.author_id,
                     'spans':lines})
    return rows


def build_dispatch(context, state, target, kind='initial', retry=False):
    logical = (state.recovery.logical_dispatch_id if retry and state.recovery else '') or f'{context.identity.task_id}:e{state.budget.work_epoch}:{context.progression.sequence+1}:{target}'
    previous = next((r for r in state.budget.dispatch_charges if r.logical_dispatch_id == logical), None)
    attempt = previous.attempts + 1 if previous else 1
    ledger = charge_dispatch(state.budget, logical, kind, attempt)
    request_id = logical + f':attempt-{attempt}'
    stamp = review_input_digest(state.draft, state.consensus) if state.draft else ''
    binding = RequirementsDispatch(logical, request_id, target, state.draft.document_digest if state.draft else '', state.consensus.digest, stamp)
    state = replace(state, budget=ledger, active_dispatch=binding)
    role = ROLES[target]
    agent = os.environ.get({'review-analyst':'AGENT_ANALYST_ID','review-solver':'AGENT_SOLVER_ID','review-critic':'AGENT_CRITIC_ID'}[role]) or role
    open_issues = [asdict(i) for i in state.issues if i.status == 'open']
    dispatch = {'workflow':'requirements-v1', 'active_runtime_state':target,
                'project_root':context.request.project_root,
                'logical_dispatch_id':logical, 'base_document_digest':binding.base_document_digest,
                'base_consensus_digest':binding.base_consensus_digest, 'review_input_digest':stamp,
                'consensus':asdict(state.consensus), 'decisions':[asdict(d) for d in state.decisions],
                'sources':source_presentation([s for s in state.sources if s.kind != 'fact']),
                'open_issues':open_issues, 'change_summary':state.draft.change_summary if state.draft else '',
                'document':render_markdown(state.draft) if state.draft else '',
                'sections':[{k:v for k,v in asdict(s).items() if k != 'markdown'} for s in state.draft.sections] if state.draft else [],
                'previous_review':asdict(state.review_stamp) if state.review_stamp else None}
    dispatch['evidence_answers'] = [asdict(a) for a in state.evidence_answers]
    dispatch['questions'] = [asdict(q) for q in state.questions]
    dispatch['rounds'] = [asdict(r) for r in state.rounds]
    dispatch['answers'] = [asdict(a) for a in state.answers]
    dispatch['solver_responses'] = [asdict(a) for a in state.solver_responses]
    dispatch['work_queue'] = [asdict(w) for w in state.work_queue if w.status == 'pending']
    if target in ('REQ_DRAFT','REQ_REVIEW') and state.chapter_locks:
        from .chapter_lock import locked_payload
        locked = locked_payload(state)
        if locked:
            dispatch['locked_sections'] = locked
    if target == 'REQ_REVIEW':
        reusable = [c for c in state.review_stamp.coverage if c.axis != 'consistency'
                    and c.basis_digest == coverage_basis(state,c.section_ids)] if state.review_stamp else []
        dispatch['required_review_axes'] = [axis for axis in AXES if axis not in {c.axis for c in reusable}]
        dispatch['reusable_coverage'] = [asdict(c) for c in reusable]
        dispatch['affected_section_ids'] = sorted({s.section_id for s in state.draft.sections
            if any(s.section_id in c.section_ids and c.basis_digest != coverage_basis(state,c.section_ids)
                   for c in state.review_stamp.coverage)} if state.review_stamp else {s.section_id for s in state.draft.sections})
    if (retry or kind == 'format') and state.recovery:
        dispatch['repair'] = asdict(state.recovery)
    prompt = ('本任务产出一份完整的需求草案。以dispatch_context中的同一正文、共识和原始用户来源为依据。'
              '只履行当前角色职责；不要构建实施任务图、投票、重做已经有效的调查。'
              '如果缺少业务决定，说明原因和影响并提交问题；不要替用户决定。'
              '引用用户原话时填 span_ids（形如 src-000001#2 的片段编号，见 sources[*].spans），不要复制原文。')
    if target == 'REQ_DRAFT':
        prompt += (' 首轮直接完整成稿；未决处用【待确认：qid】标记并在本次回复的 questions 中给出对应问题'
                   '（含 reason/impact/recommendation）。')
    if os.environ.get('NEXUS_REQUIREMENTS_CONTENT_SAMPLE') == '1':
        dispatch['validation_mode'] = 'content-sample'
        prompt += ' 本任务是隔离产物验收；只读取输入与写入指定结果文件，不发飞书、邮件或其他通知，不提交或修改业务代码。'
    if target == 'REQ_EVIDENCE':
        dispatch['open_issues'] = [i for i in open_issues if i['owner'] == 'analyst']
        required_sources = {sid for work in state.work_queue if work.status == 'pending' for sid in work.source_ids}
        dispatch['sources'] = source_presentation([s for s in state.sources if s.source_id in required_sources])
        prompt = '只调查指定事实缺口；给出可核验来源与逐字引文，说明是否影响正文。'
    if target == 'MENXIA_REQUIREMENTS_INTAKE':
        from .handoff import make_approved_input
        dispatch['approved_requirements'] = asdict(make_approved_input(context.identity.task_id, state))
        prompt = '只根据已批准的需求正文构建实施任务 DAG。保持需求基线不变，统一使用一个实施审查组，保留任务依赖；不得替用户变更需求。'
    effect = EffectRequest('agent:'+request_id, 'agent_dispatch', context.identity.task_id, request_id,
                           payload={'issue_id':context.identity.issue_id,'request_id':request_id,'agent_id':agent,
                                    'role':role,'phase':'MENXIA' if target.startswith('MENXIA') else 'ZHONGSHU',
                                    'target_state':target,'sequence':context.progression.sequence,
                                    'prompt_ref':prompt,'dispatch_context':dispatch,
                                    'validation_state':state.to_dict()})
    return state, effect
