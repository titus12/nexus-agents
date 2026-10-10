"""Zero-token validation through the real app, repository, effects and Menxia."""
import argparse
import json
from dataclasses import asdict, replace
from pathlib import Path
import tempfile
import unittest
from orchestrator.app import OrchestratorApp, build_context
from orchestrator.adapters import FakeMulticaAdapter
from orchestrator.contracts import contract_for_state
from orchestrator.domain.context import context_from_dto, context_to_dto
from orchestrator.domain.requirements.model import CORE_SECTIONS
from orchestrator.transport.external import ExternalMessage
from menxia_test_support import GroupWaveScripting, _six_part_body
from requirements_test_support import make_state, complete_draft, review_payload


def context():
    return build_context(argparse.Namespace(project='',project_type='go',task_type='review',
        workflow='requirements-v1',communication='local'), 'issue-requirements',make_state().sources[0].text,'task-requirements')


def implementation_plan(count=8):
    statement = make_state().sources[0].text
    items = [{'item_id':f'I{i}','group_id':'G1','title':f'修改入口 {i}','objective':'按需求限制重复连接',
        'dependencies':[f'I{i-1}'] if i else [],'source_requirement_ids':['req-000001'],
        'acceptance_signals':['同一会话只建立一次连接。'],'unknowns':[],'risks':[],'parallelizable':False} for i in range(count)]
    return {'requirements':[{'requirement_id':'req-000001','description':statement}],
        'items':items,'groups':[{'group_id':'G1','item_ids':[i['item_id'] for i in items]}]}


class Scripted(GroupWaveScripting,FakeMulticaAdapter):
    critic_demands_revision = False
    def dispatch(self, request):
        receipt = FakeMulticaAdapter.dispatch(self,request)
        target = request.target_state
        if target in ('MENXIA_GROUP_SOLVER','MENXIA_GROUP_ANALYST','MENXIA_GROUP_CRITIC'):
            self._reply_group_wave(request)
            return receipt
        if target == 'MENXIA_GROUP_GATE':
            self._reply(request,{'action':'APPROVE_GROUP'})
            return receipt
        contract = contract_for_state(target,workflow='requirements-v1')
        body = contract.example_payload()
        body.update(task_id=request.task_id,request_id=request.request_id,
            structured_output_schema_hash=request.structured_output['schema_hash'])
        if target == 'REQ_ANALYZE':
            body['analysis']['claims'] = [{'topic_key':'duplicate-login','statement':make_state().sources[0].text,
                'source_ids':['src-000001'],'quote':make_state().sources[0].text}]
        elif target == 'REQ_DRAFT':
            draft = complete_draft(make_state())
            body['document'] = {'title':draft.title,'sections':[asdict(s) for s in draft.sections]}
        elif target == 'REQ_REVIEW':
            body.update(review_payload())
        elif target == 'MENXIA_REQUIREMENTS_INTAKE':
            body['plan'] = implementation_plan()
        self.queue_reply(request.request_id,ExternalMessage(request.agent_id,json.loads(json.dumps(body)),request.request_id))
        return receipt

    def _solver_reply(self, request, group_id, doc):
        result = super()._solver_reply(request,group_id,doc)
        from orchestrator.domain.menxia_doc import MenxiaGroupDoc
        updated = MenxiaGroupDoc.parse(result['doc_markdown'])
        body = _six_part_body(group_id,tuple(request.context['approved_acceptance_markdown'].splitlines()))
        result['doc_markdown'] = replace(updated,body_markdown=body).render()
        return result


def pump(app, stop, limit=100):
    for _ in range(limit):
        if app.ctx.progression.state == stop:
            return
        if not app.step():
            raise AssertionError(app.inspect())
    raise AssertionError('workflow did not reach '+stop+': '+app.ctx.progression.state)


class RequirementsWorkflowTests(unittest.TestCase):
    def test_real_app_review_approval_handoff_and_menxia_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter = Scripted()
            app = OrchestratorApp(context(),directory,multica=adapter,poll_interval=0,timeout_seconds=2)
            pump(app,'REQ_CONFIRM_WAIT')
            self.assertIsNone(app.ctx.review)
            self.assertIsNone(app.ctx.requirements.approval)
            self.assertEqual(app.ctx.requirements.budget.calls,3)
            req = app.ctx.requirements
            app.requirements_runtime.submit('REQUIREMENTS_APPROVED',{'actor_id':'local-operator',
                'gate_id':req.review_gate_id,'document_digest':req.draft.document_digest},'approval-1')
            pump(app,'DONE')
            current = app.ctx
            self.assertEqual(context_from_dto(context_to_dto(current)),current)
            self.assertTrue(Path(current.requirements.published_ref,'manifest.json').is_file())
            self.assertEqual(len(current.review.task_items),8)
            self.assertEqual(current.review.task_items[-1].dependencies,('I6',))
            self.assertFalse(current.review.zhongshu_groups)
            self.assertEqual(current.review.approved_requirements_input.document_digest,current.requirements.draft.document_digest)
            self.assertTrue(all(r.context.get('workflow') == 'requirements-v1' for r in adapter.dispatched))

    def test_feedback_at_menxia_intake_preserves_approved_input(self):
        with tempfile.TemporaryDirectory() as directory:
            app=OrchestratorApp(context(),directory,multica=Scripted(),poll_interval=0,timeout_seconds=2)
            pump(app,'REQ_CONFIRM_WAIT')
            req=app.ctx.requirements
            app.requirements_runtime.submit('REQUIREMENTS_APPROVED',{'actor_id':'local-operator',
                'gate_id':req.review_gate_id,'document_digest':req.draft.document_digest},'approval')
            pump(app,'MENXIA_REQUIREMENTS_INTAKE')
            approval=app.ctx.requirements.approval
            app.requirements_runtime.submit('REQUIREMENTS_FEEDBACK',{'actor_id':'local-operator','text':'后续再考虑重试。'},'followup')
            app.step()
            self.assertEqual(app.ctx.requirements.approval,approval)
            self.assertEqual(app.ctx.requirements.sources[-1].text,'后续再考虑重试。')
            self.assertEqual(app.ctx.progression.state,'MENXIA_REQUIREMENTS_INTAKE')

    def test_stale_approval_cannot_bypass_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            app = OrchestratorApp(context(),directory,multica=Scripted(),poll_interval=0,timeout_seconds=2)
            pump(app,'REQ_CONFIRM_WAIT')
            app.requirements_runtime.submit('REQUIREMENTS_APPROVED',{'actor_id':'local-operator',
                'gate_id':app.ctx.requirements.review_gate_id,'document_digest':'0'*64},'stale')
            self.assertTrue(app.step())
            self.assertEqual(app.ctx.progression.state,'REQ_CONFIRM_WAIT')
            self.assertIsNone(app.ctx.requirements.approval)

    def test_cancel_discards_pending_dispatch_and_preserves_draft(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter = Scripted()
            app = OrchestratorApp(context(),directory,multica=adapter,poll_interval=0,timeout_seconds=2)
            app.step()
            self.assertEqual(app.ctx.progression.state,'REQ_ANALYZE')
            self.assertTrue(app.cancel())
            self.assertEqual(app.ctx.progression.state,'REQ_CANCELLED')
            self.assertFalse(adapter.dispatched)
            self.assertIsNotNone(app.ctx.requirements.draft)


if __name__ == '__main__':
    unittest.main()
