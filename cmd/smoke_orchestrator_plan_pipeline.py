"""Offline smoke check of the sole requirements workflow and Menxia handoff."""
import os
os.environ['NEXUS_TEST_NO_EXTERNAL_NOTIFICATIONS']='1'
import tempfile, json
from orchestrator.app import OrchestratorApp
from test_requirements_workflow_e2e import context, Scripted, pump
def main():
    with tempfile.TemporaryDirectory() as root:
        app=OrchestratorApp(context(),root,multica=Scripted(),poll_interval=0,timeout_seconds=2)
        pump(app,'REQ_CONFIRM_WAIT')
        req=app.ctx.requirements
        app.requirements_runtime.submit('REQUIREMENTS_APPROVED',{'actor_id':'local-operator','gate_id':req.review_gate_id,'document_digest':req.draft.document_digest},'smoke-approval')
        pump(app,'DONE')
        print(json.dumps({'workflow':app.ctx.request.workflow,'state':app.ctx.progression.state,'items':len(app.ctx.review.task_items)},ensure_ascii=False))
    return 0
if __name__=='__main__':raise SystemExit(main())
