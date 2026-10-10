"""Real-model artifact sample, isolated from notifications and human approval.

One invocation stops at clarification/confirmation/failure. Continue the same
sample with a fixture-backed answer file; never auto-approve a model document.
"""
import argparse
import json
import os
from pathlib import Path
import time
import uuid
from orchestrator.app import OrchestratorApp,build_context
from orchestrator.adapters import MulticaCliAdapter
from orchestrator.runtime.repository import JsonWorkflowRepository
from orchestrator.domain.requirements.document import render_markdown
from orchestrator.locks import TaskLock


def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('--case',required=True,choices=('singleflight','vague-goal','evolving-scope'))
    parser.add_argument('--sample',required=True,type=int,choices=(1,2))
    parser.add_argument('--results',default='runs/requirements-quality-20261007')
    parser.add_argument('--project',default='')
    parser.add_argument('--answer-file')
    parser.add_argument('--timeout',type=int,default=900)
    args=parser.parse_args(argv)
    os.environ['NEXUS_TEST_NO_EXTERNAL_NOTIFICATIONS']='1'
    os.environ['NEXUS_REQUIREMENTS_CONTENT_SAMPLE']='1'
    fixture=Path(__file__).parent/'fixtures'/'requirements'/args.case
    case=json.loads((fixture/'case.json').read_text(encoding='utf-8'))
    root=Path(args.results).resolve()/args.case/f'run-{args.sample}'
    root.mkdir(parents=True,exist_ok=True)
    adapter=MulticaCliAdapter(root/'transport')
    existing=list(root.glob('*/workflow-state.json'))
    if len(existing)>1:
        raise ValueError('sample has multiple tasks; choose a new results root')
    repository=JsonWorkflowRepository(existing[0].parent if existing else root)
    if existing:
        metadata=json.loads(repository.state_path.read_text(encoding='utf-8'))
        context=repository.load(metadata['task_id']).context
    else:
        text=case.get('input') or (fixture/case['input_file']).read_text(encoding='utf-8-sig')
        task_id=f'quality-{args.case}-{args.sample}-{uuid.uuid4().hex[:8]}'
        issue=adapter.create_issue('Requirements quality sample: '+task_id,text,project_id=args.project,allow_duplicate=True)
        context=build_context(argparse.Namespace(workflow='requirements-v1',communication='local',
            project=args.project,project_type='unknown',task_type='review'),issue,text,task_id)
    app=OrchestratorApp(context,root,multica=adapter,poll_interval=2,timeout_seconds=args.timeout)
    if args.answer_file:
        answer=json.loads(Path(args.answer_file).read_text(encoding='utf-8-sig'))
        answer['actor_id']='local-operator'
        app.requirements_runtime.submit('ANSWERS_SUBMITTED',answer,'fixture-answer:'+uuid.uuid4().hex)
        app.step()
    feedback_id='fixture-feedback:'+args.case+':'+str(args.sample)
    with TaskLock(app.root/'requirements-run.lock'):
        while True:
            state=app.ctx.progression.state
            if state=='REQ_CONFIRM_WAIT' and case.get('feedback_after_first_review'):
                if not any(s.external_id.endswith(feedback_id) or s.text==case['feedback_after_first_review']
                           for s in app.ctx.requirements.sources):
                    app.requirements_runtime.submit('REQUIREMENTS_FEEDBACK',{'actor_id':'local-operator',
                        'text':case['feedback_after_first_review']},feedback_id)
                    app.step()
                    continue
            if state in ('REQ_CLARIFY_WAIT','REQ_CONFIRM_WAIT','REQ_ENGINE_ERROR','REQ_BLOCKED','REQ_CANCELLED'):
                req=app.ctx.requirements
                if req.draft:
                    (app.root/'review-body.md').write_text(render_markdown(req.draft),encoding='utf-8')
                print(json.dumps({'sample_root':str(app.root),'task_id':context.identity.task_id,'state':state,
                    'calls':req.budget.total_calls,'reason':req.reason,
                    'round':req.to_dict()['rounds'][-1] if req.rounds else None,
                    'questions':[q for q in req.to_dict()['questions'] if req.rounds and q['question_id'] in req.rounds[-1].question_ids]},
                    ensure_ascii=False,indent=2),flush=True)
                return 0 if state=='REQ_CONFIRM_WAIT' else 2 if state=='REQ_CLARIFY_WAIT' else 1
            progressed=app.step()
            if progressed:
                print('sample progress: '+app.ctx.progression.state,flush=True)
            else:
                time.sleep(0.25)


if __name__=='__main__':
    raise SystemExit(main())
