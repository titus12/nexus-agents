from .common import array, object_schema, string
from .requirements_common import requirement_contract, CLAIM, FACT, ASSUMPTION, QUESTION

CONTRACT = requirement_contract('REQ_ANALYZE', 'review-analyst',
    ('ANALYSIS_READY', 'PREREQUISITE_MISSING'),
    {'analysis': object_schema({'claims': array(CLAIM), 'facts': array(FACT),
                               'assumptions': array(ASSUMPTION), 'gaps': array(string())}, required=('claims','facts','assumptions','gaps')),
     'questions': array(QUESTION), 'reason': string()},
    ('Explore intent and necessary facts. Preserve original user sources.',
     'Do not author the document or implementation graph. Never create human approvals.',
     'Explicit claims require an exact quote from a human source. Separate proposals from choices.',
     'Cite human wording with span_ids from dispatch_context.sources spans (e.g. src-000001#2); '
     'the runtime backfills the exact quote, so never copy the source text by hand.',
     'Use only enum values listed in the schema. A user quote has origin explicit; '
     'an observed repository fact has status observed; an unanswered choice uses answer_kind single, multi or text. '
     'Do not invent labels such as functional, user, draft, verified, probable or single_choice.',
     'Analyst 是只读探针：questions 必须是空数组；非 intent 探针的 claims 会被丢弃；找不到的内容写入 gaps。'))
EVIDENCE_CONTRACT = requirement_contract('REQ_EVIDENCE', 'review-analyst',
    ('EVIDENCE_READY', 'PREREQUISITE_MISSING'),
    {'fact_updates': array(FACT), 'issue_answers': array(object_schema(
        {'issue_id': string(), 'answer': string(), 'fact_ids': array(string()), 'needs_draft_change': {'type':'boolean'}},
        required=('issue_id','answer','fact_ids','needs_draft_change'))), 'reason': string()},
    ('Investigate only dispatched fact gaps. Cite exact readable sources.',
     'State whether the evidence changes authored behavior. Do not rewrite the document.'))
