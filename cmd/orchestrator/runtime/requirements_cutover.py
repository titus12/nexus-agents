"""Historical data inspection and explicit import; no retired workflow execution."""
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import uuid

from ..domain.context import RequestState, TaskIdentity, ProgressState, WorkflowContext
from ..domain.requirements.model import RequirementsState, SourceRecord
from ..locks import TaskLock
from .repository import JsonWorkflowRepository, WorkflowSnapshot


def require_current_workflow(context):
    if context.request.workflow != 'requirements-v1' or context.requirements is None:
        raise ValueError('Historical Zhongshu execution is retired. Inspect the task or use '
                         '--migrate-task '+context.identity.task_id+' to create a new requirements review.')


def task_root(runs_root, task_id):
    base = Path(runs_root).resolve()
    if not task_id or Path(task_id).name != task_id or any(c in task_id for c in '/\\:'):
        raise ValueError('invalid task id')
    result = (base/task_id).resolve()
    if result.parent != base:
        raise ValueError('task root escapes runs root')
    return result


def inspect_task(runs_root, task_id):
    root = task_root(runs_root, task_id)
    path = root/'workflow-state.json'
    if not path.is_file():
        path = root/'state.json'
    # Read raw persisted data: no startup patch, journal repair or runner creation.
    value = json.loads(path.read_text(encoding='utf-8-sig'))
    context = value.get('context', value)
    return {'task_id':task_id, 'state_version':value.get('state_version',0),
            'context':context, 'historical_read_only':context.get('request',{}).get('workflow') != 'requirements-v1'}


def migrate_historical_task(runs_root, task_id, *, communication=None, actor_ids=None):
    root = task_root(runs_root, task_id)
    for name in ('task.lock','requirements-run.lock','run.lock'):
        if TaskLock(root/name).has_live_owner():
            raise ValueError('Historical task has an active owner; migration must wait for it to stop.')
    original = inspect_task(runs_root, task_id)
    old = original['context']
    request = old.get('request',{})
    if request.get('workflow') == 'requirements-v1':
        raise ValueError('Task already uses the sole requirements workflow')
    raw_request = str(request.get('raw_request') or old.get('raw_request') or old.get('original_request') or '').strip()
    if not raw_request:
        raise ValueError('Historical task has no recoverable original user request')
    identity = old.get('identity',{})
    new_id = 'task-'+datetime.now().strftime('%Y%m%d')+'-'+uuid.uuid4().hex[:8]
    new_root = task_root(runs_root,new_id)
    now = datetime.now(timezone.utc).isoformat()
    state = RequirementsState.empty(SourceRecord('src-000001','request',raw_request,
        external_id='historical:'+task_id,created_at=now))
    # Carry explicit human feedback only. Model plans remain historical attachments.
    packet = (old.get('review') or {}).get('plan_review') or {}
    feedback = packet.get('feedback_history') or []
    sources = list(state.sources)
    for entry in feedback:
        text = entry if isinstance(entry, str) else ''
        if isinstance(entry, dict):
            text = entry.get('text') or entry.get('answer') or entry.get('feedback') or ''
        if isinstance(text,str) and text.strip() and text.strip() != '通过':
            sources.append(SourceRecord('src-'+str(len(sources)+1).zfill(6),'feedback',text,
                author_id='historical-user',external_id='historical:'+task_id,created_at=now))
    state = replace(state,sources=tuple(sources))
    context = WorkflowContext(identity=TaskIdentity(task_id=new_id,issue_id=str(identity.get('issue_id') or old.get('issue_id') or ''),
        project=str(identity.get('project') or request.get('project') or ''),request_id='request-'+new_id),
        progression=ProgressState(state='REQ_INTAKE',sequence=0,entered_at=now),
        request=RequestState(raw_request=raw_request,workflow='requirements-v1',plan_schema='canonical-v1',
            communication=communication or request.get('communication') or 'feishu',
            decision_actor_ids=tuple(actor_ids or request.get('decision_actor_ids') or ())),requirements=state)
    new_root.mkdir(parents=True,exist_ok=False)
    data = json.dumps(original,ensure_ascii=False,sort_keys=True,indent=2).encode('utf-8')
    (new_root/'historical-task.json').write_bytes(data)
    provenance = {'source_task_id':task_id,'new_task_id':new_id,'source_snapshot_sha256':hashlib.sha256(data).hexdigest(),
                  'source_root':str(root),'approved':False,'created_at':now}
    (new_root/'migration.json').write_text(json.dumps(provenance,ensure_ascii=False,indent=2),encoding='utf-8')
    JsonWorkflowRepository(new_root).initialize(WorkflowSnapshot(new_id,context,0))
    return {**provenance,'resume_command':'rtk proxy python -X utf8 cmd/review_orchestrator_v2.py --resume '+new_id,
            'message':'Historical input imported; no worker dispatched and a new review and approval are required.'}
