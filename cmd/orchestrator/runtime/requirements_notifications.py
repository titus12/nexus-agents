"""Add durable progress intents to committed requirements/Menxia transitions."""
from dataclasses import replace
from ..domain.decisions import EffectRequest
from .reducer import LinearContextReducer
from .repository import WorkflowSnapshot


STAGES = {
    'REQ_ANALYZE': '正在分析需求',
    'REQ_DRAFT': '正在编写需求草案',
    'REQ_EVIDENCE': '正在补充核验事实证据',
    'REQ_REVIEW': '正在进行需求评审',
    'REQ_CLARIFY_WAIT': '等待澄清问题回答',
    'REQ_CONFIRM_WAIT': '等待需求确认',
    'REQ_RETRY_WAIT': '正在重试',
    'REQ_APPROVAL_VERIFY': '正在验证需求批准',
    'MENXIA_REQUIREMENTS_INTAKE': '需求已批准，开始实施任务拆分',
    'MENXIA_GROUP_SOLVER': '正在编写实施方案',
    'MENXIA_GROUP_ANALYST': '正在核验实现证据',
    'MENXIA_GROUP_CRITIC': '正在进行实施审查',
    'MENXIA_GROUP_GATE': '正在汇总实施审查结果',
    'RETRY_WAIT': '实施阶段正在重试',
    'HUMAN_GATE': '需要人工决策',
    'BLOCKED': '实施流程被阻塞',
    'PERSISTENCE_DEGRADED': '运行记录写入异常，等待恢复',
    'DONE': '任务已完成',
    'FAILED': '任务执行失败',
    'REQ_CANCELLED': '任务已取消',
}


def progress_text(before, event, after):
    target = after.progression.state
    title = STAGES.get(target)
    if title is None:
        return ''  # Requirements failure/block notices and cards have their own effects.
    if event.name == 'START':
        title = '任务已启动｜' + title
    elif event.name == 'RESUME' or before.progression.state in ('REQ_RETRY_WAIT','RETRY_WAIT'):
        title = '重试后继续｜' + title
    lines = ['【' + title + '】', '任务：' + after.identity.task_id]
    req = after.requirements
    if event.name == 'START':
        lines.append('需求：' + after.request.raw_request[:400])
    if target in ('REQ_DRAFT','REQ_REVIEW','REQ_EVIDENCE'):
        lines.append(f'已核验事实：{len(req.consensus.facts)}；需求条目：{len(req.consensus.claims)}')
    if target == 'REQ_CLARIFY_WAIT':
        if req.rounds:
            round_ = req.rounds[-1]
            revisions = {q.question_id:q.revision for q in req.questions}
            answered = len({a.question_id for a in req.answers if a.question_id in round_.question_ids
                            and a.question_revision == revisions[a.question_id]})
            lines.append(f'本批共 {len(round_.question_ids)} 题，已回答 {answered} 题。')
        lines.append('请查看已发送的澄清卡片。')
    if target == 'REQ_CONFIRM_WAIT':
        lines.append('需求评审已通过，请查看需求文档和确认卡片。')
    if target == 'REQ_RETRY_WAIT' and req.recovery:
        failure = req.recovery.failure
        lines.append('原因：' + failure.code + '；' + failure.message[:500])
        charge = next((c for c in req.budget.dispatch_charges
                       if c.logical_dispatch_id == req.recovery.logical_dispatch_id),None)
        if charge:
            # This transition schedules the repair; charge is applied on redispatch.
            format_count = charge.format_repairs + (req.recovery.repair_fingerprint == 'format')
            external_count = charge.external_retries + (req.recovery.repair_fingerprint == 'external')
            lines.append(f'格式修复：{format_count}/2；外部重试：{external_count}/2')
    if target in ('RETRY_WAIT','HUMAN_GATE','BLOCKED','PERSISTENCE_DEGRADED','FAILED'):
        failure = after.recovery.last_failure
        reason = after.recovery.blocked_reason or (failure.message if failure else '')
        if reason:
            lines.append('原因：' + reason[:500])
        question = event.payload_mapping.get('question')
        if isinstance(question,str) and question:
            lines.append('待决策：' + question[:500])
    if target == 'REQ_CANCELLED':
        lines.append('任务已停止，待执行的业务操作已取消。')
    return '\n'.join(lines)


class NotifyingRequirementsState:
    """Project a pure decision and prepend its journaled notification intent."""
    def __init__(self, inner, transitions):
        self.inner, self.name = inner, inner.name
        self.reducer = LinearContextReducer(transitions)

    def handle(self, context, event):
        decision = self.inner.handle(context,event)
        partial = (event.name == 'ANSWERS_SUBMITTED' and self.name == 'REQ_CLARIFY_WAIT'
                   and decision.update.requirements is not None
                   and decision.update.requirements != context.requirements)
        if decision.transition is None and not partial:
            return decision
        after = self.reducer.apply(WorkflowSnapshot(context.identity.task_id,context,0),decision).context
        body = progress_text(context,event,after)
        if not body:
            return decision
        key = f'{context.identity.task_id}:progress:{after.progression.sequence}:{after.progression.state}'
        effect = EffectRequest(key,'requirements_progress',context.identity.task_id,key,
                               payload={'body':body,'notification_key':key,
                                        'state_name':after.progression.state,
                                        'sequence':after.progression.sequence})
        return replace(decision,effects=(effect,*decision.effects))
