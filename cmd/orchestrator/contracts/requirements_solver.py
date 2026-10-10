from .common import array, object_schema, string
from .requirements_common import requirement_contract, SECTION, QUESTION

CONTRACT = requirement_contract('REQ_DRAFT', 'review-solver',
    ('DRAFT_READY', 'NEEDS_CLARIFICATION', 'NEEDS_EVIDENCE', 'NEEDS_ANALYSIS', 'PREREQUISITE_MISSING'),
    {'document': object_schema({'title': string(), 'sections': array(SECTION)}, required=('title','sections')),
     'issue_responses': array(object_schema({'issue_id':string(), 'response':string()}, required=('issue_id','response'))),
     'questions': array(QUESTION), 'evidence_requests': array(string()),
     'analysis_requests': array(object_schema({'source_ids':array(string()), 'reason':string()}, required=('source_ids','reason'))),
     'change_summary': string(), 'reason': string()},
    ('You alone author the complete readable requirements document. Return all sections.',
     'Use stable semantic anchors, flexible titles and relevant topics. Do not invent content.',
     'Preserve user choices; new tradeoffs require questions. Implementation recipes belong to Menxia.',
     'Address all actionable issues together; keep unrelated valid text.',
     'Use the injected skeleton section kinds and the eight-chapter functions in the skill. '
     'Unconfirmed items are written inline as 【待确认：<question_id>】 and every such marker needs a matching question; '
     'unconfirmed numbers are written as 待定 (no brackets). Never cite an unavailable fact in fact_ids.'))
