from __future__ import annotations
import copy
from orchestrator.domain.context import ReviewState, ReviewTaskGroup, ReviewTaskItem, TaskIdentity, WorkflowContext, ProgressState, ZhongshuGroupState
from orchestrator.domain.findings import Finding
def incident_plan():
    dependencies = [[], [1], [1], [1], [1], [1], [1], [2, 3, 4, 5, 6, 7], [1], [8, 9]]
    return {'requirements': [{'requirement_id': 'REQ-001', 'text': 'Produce a review plan'}], 'items': [dict(item_id=f'ITEM-{n:03}', title=f'Task {n}', objective=f'Review {n}', dependencies=[f'ITEM-{d:03}' for d in deps], source_requirement_ids=['REQ-001'], acceptance_signals=[f'Result {n} is observable']) for n, deps in enumerate(dependencies, 1)], 'groups': [dict(group_id='GRP-001', item_ids=[f'ITEM-{n:03}' for n in (1, 2, 3, 4, 5, 6, 7, 9)]), dict(group_id='GRP-002', item_ids=['ITEM-008', 'ITEM-010'])]}

def doc(group, version=1):
    names = ('背景', '目标', '标识与范围', '状态与边界语义', '行为要求', '责任边界', '交叉不变量', '验收标准', '非目标')
    lines = [f"# {group['group_id']} 需求文档 [v{version}]"]
    for n, name in enumerate(names, 1):
        lines.append(f'## {n}. {name}')
        lines.append('\n'.join((f'### {iid}\nResult {int(iid[-3:])} is observable' for iid in group['item_ids'])) if n == 8 else '按已确认需求执行。')
    return '\n'.join(lines)

def review_for(plan):
    owners = {iid: g['group_id'] for g in plan['groups'] for iid in g['item_ids']}
    return ReviewState(revision_id='R1', plan=copy.deepcopy(plan), requirements=tuple(plan['requirements']), task_items=tuple((ReviewTaskItem(item_id=i['item_id'], group_id=owners[i['item_id']], order=n, title=i['title'], objective=i['objective'], dependencies=tuple(i['dependencies']), acceptance_signals=tuple(i['acceptance_signals'])) for n, i in enumerate(plan['items'], 1))), task_groups=tuple((ReviewTaskGroup(group_id=g['group_id'], item_ids=tuple(g['item_ids']), order=n) for n, g in enumerate(plan['groups'], 1))), zhongshu_groups=tuple((ZhongshuGroupState(group_id=g['group_id'], doc_markdown=doc(g), doc_version=1) for g in plan['groups'])), findings=(Finding(finding_id='f-1', severity='P1', status='OPEN', group_id='GRP-002', item_id='ITEM-010'),))

def context_for(review, state='ZHONGSHU_SOLVER'):
    return WorkflowContext(identity=TaskIdentity('task-structure', 'issue', '', 'request'), progression=ProgressState(state, 3, '2026-10-06T00:00:00Z'), review=review)

def repaired_reply():
    plan = incident_plan()
    plan['groups'][0]['item_ids'] = [f'ITEM-{n:03}' for n in range(1, 7)]
    plan['groups'].append(dict(group_id='GRP-003', item_ids=['ITEM-007', 'ITEM-009']))
    return dict(action='READY_FOR_CRITIC', mode='TASK_GRAPH_FORMALIZATION_READ_ONLY', plan=plan, group_docs=[dict(group_id=g['group_id'], markdown=doc(g, 2 if g['group_id'] == 'GRP-001' else 1)) for g in plan['groups'] if g['group_id'] != 'GRP-002'])
