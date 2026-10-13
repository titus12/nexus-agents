from .common import array, object_schema, string
from .requirements_common import requirement_contract, ISSUE, COVERAGE, QUESTION

CONTRACT = requirement_contract('REQ_REVIEW', 'review-critic',
    ('REVIEW_READY', 'PREREQUISITE_MISSING'),
    {'issues': array(ISSUE), 'closures': array(object_schema(
        {'issue_id':string(), 'status':string(enum=('resolved','still_open','reclassified')),
         'evidence':string(), 'severity':string()}, required=('issue_id','status','evidence'))),
     'coverage': array(COVERAGE), 'review_scope':array(string()), 'questions': array(QUESTION), 'reason':string()},
    ('Review independently against original sources and human decisions, not merely Analyst summaries.',
     'First review covers intent/scenarios/constraints/consistency/acceptance/decisions/facts.',
     'Later review closes existing issues and actual affected sections; always scan overall consistency.',
     'Every blocker needs stable identity, basis, impact, owner and fixed acceptance. Optional improvements do not block.',
     'Never author content, approve on behalf of the user or demand implementation recipes.',
     'Do not repeat structural checks already reported as lint-* advisory issues; judge intent, scenarios and gaps. '
     'A pending marker 【待确认】 is a user-owned open question, not a Solver defect.',
     'Judge each chapter against the chapter contract in the skill. Report misplaced, duplicated or function-violating '
     'content with rule chapter-misplaced, chapter-duplicate or chapter-function-violation; mark blocking only when it '
     'changes how downstream readers understand scope, behavior or acceptance.',
     'Chapter problems already reported by a lint-* advisory issue must not be reported again.'))
