"""Explicit two-message live acceptance; never dispatches a model or business work."""
import argparse
import hashlib
import json
from pathlib import Path
import uuid

from orchestrator.adapters import FakeMulticaAdapter, feishu_notifications_enabled
from orchestrator.app import OrchestratorApp, build_context
from orchestrator.requirements_port import FeishuRequirementsPort


LABEL = '【通知链路验收｜不涉及业务任务】\n'


class AcceptancePort(FeishuRequirementsPort):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.sent=[]

    def publish_progress(self, task_id, key, body):
        receipt=super().publish_progress(task_id,key,LABEL+body)
        self.sent.append((key,LABEL+body,receipt))
        return receipt


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--send',action='store_true',help='Send exactly two labelled start/cancel messages to the configured Feishu chat.')
    parser.add_argument('--runs-root',default='runs/maintenance/notification-acceptance')
    args=parser.parse_args()
    if not args.send:
        parser.error('Live notification acceptance requires explicit --send; automated tests use cmd/run_tests.py.')
    if not feishu_notifications_enabled():
        parser.error('Feishu notifications are disabled; do not bypass test suppression.')
    task_id='notification-acceptance-'+uuid.uuid4().hex[:12]
    ctx=build_context(argparse.Namespace(project='',project_type='unknown',task_type='review',
        workflow='requirements-v1',communication='feishu'),'',
        '验收通知发送、终态发送和重启去重；不运行模型，不修改业务项目。',task_id)
    adapter=FakeMulticaAdapter()
    app=OrchestratorApp(ctx,args.runs_root,multica=adapter,poll_interval=0,timeout_seconds=2)
    port=AcceptancePort(app.root,app.requirements_runtime.store)
    if not port.chat_id:
        parser.error('HUMAN_GATE_CHAT_ID is not configured.')
    app.requirements_runtime.effects.port=port
    app.step()  # Commit start intent, deliberately leave it pending.
    assert app.cancel()  # Drain committed start/cancel after reaching terminal state.
    assert app.ctx.progression.state=='REQ_CANCELLED'
    assert not adapter.dispatched
    assert len(port.sent)==2
    results=[r for r,e in app.repository.list_effect_results(task_id)
             if e.request.effect_type=='requirements_progress']
    if len(results)!=2 or any(r.status!='SUCCEEDED' or not r.event_payload.get('delivered') for r in results):
        raise RuntimeError('LIVE_NOTIFICATION_ACCEPTANCE_FAILED; inspect isolated task journal')
    restarted=OrchestratorApp(app.ctx,args.runs_root,multica=adapter,requirements_port=port,poll_interval=0,timeout_seconds=2)
    assert not restarted.step()
    assert len(port.sent)==2
    assert not adapter.dispatched
    report={'task_id':task_id,'state':app.ctx.progression.state,'model_calls':0,
            'messages_sent':2,'restart_resent':0,'message_readback':'pending',
            'receipts':[{'notification_key':key,**receipt,'expected_content':body,
                         'content_sha256':hashlib.sha256(body.encode('utf-8')).hexdigest()}
                        for key,body,receipt in port.sent]}
    output=Path(args.runs_root).resolve()/'latest-report.json'
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'report':str(output),'task_id':task_id,'messages_sent':2,
                      'model_calls':0,'restart_resent':0,
                      'message_ids':[r['message_id'] for r in report['receipts']]},ensure_ascii=False,indent=2))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
