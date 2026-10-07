"""Mixed defects, pure evidence, clarification and bounded repair through the app."""
from collections import Counter
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import unittest
from orchestrator.app import OrchestratorApp
from orchestrator.transport.external import ExternalMessage
from requirements_test_support import question,review_payload
from test_requirements_workflow_e2e import Scripted,context,pump


class DefectScript(Scripted):
    def __init__(self,source,mixed=False):
        super().__init__()
        self.source,self.mixed,self.reviews=source,mixed,0

    def dispatch(self,request):
        receipt=super().dispatch(request)
        body=self.replies[request.request_id][0].payload
        if request.target_state=='REQ_REVIEW':
            self.reviews+=1
            if self.reviews==1:
                rows=[{'rule':'SOURCE_GAP','claim':'缺少当前入口的事实证据','impact':'无法确认现状',
                    'owner':'analyst','section_ids':['S_background'],'acceptance':'提供逐字引文及来源指纹'}]
                if self.mixed:
                    rows.append({'rule':'CALLBACK_RULE','claim':'完成通知规则不清','impact':'调用方无法验收',
                        'owner':'solver','section_ids':['S_acceptance'],'acceptance':'每个调用方最多一次完成通知'})
                for row in rows:
                    row.update(severity='blocking',requirement_ids=['req-000001'])
                body.update(review_payload(rows))
            else:
                body.update(review_payload(closures=[{'issue_id':i['issue_id'],'status':'resolved',
                    'evidence':'已对照补证和当前正文验收条款'} for i in request.context['open_issues']]))
        elif request.target_state=='REQ_EVIDENCE':
            identity=request.context['open_issues'][0]['issue_id']
            body['fact_updates']=[{'local_key':'entry-fact','observation':'入口为共享会话',
                'source':str(self.source),'quote':'shared session','requirement_ids':['req-000001']}]
            body['issue_answers']=[{'issue_id':identity,'answer':'来源已核对','fact_ids':['entry-fact'],
                'needs_draft_change':self.mixed}]
        elif request.target_state=='REQ_DRAFT' and request.context['open_issues']:
            for section in body['document']['sections']:
                if section['kind']=='acceptance':
                    section['markdown']+=' 每个调用方最多收到一次完成通知。'
            body['issue_responses']=[{'issue_id':i['issue_id'],'response':'补充一次完成通知的验收条款'}
                for i in request.context['open_issues'] if i['owner']=='solver']
        self.replies[request.request_id][0]=ExternalMessage(request.agent_id,json.loads(json.dumps(body)),request.request_id)
        return receipt


class ClarificationScript(Scripted):
    def __init__(self):
        super().__init__()
        self.analyses=0

    def dispatch(self,request):
        receipt=super().dispatch(request)
        body=self.replies[request.request_id][0].payload
        if request.target_state=='REQ_ANALYZE':
            self.analyses+=1
            if self.analyses==1:
                body['questions']=[asdict(question())]
            elif self.analyses==2:
                body['analysis']['claims']=[]
                second=asdict(question('q-000002',('q-000001',)))
                second.update(topic_key='failure-notification',question='失败是否通知所有等待调用方？')
                body['questions']=[second]
            else:
                body['analysis']['claims']=[]
        elif request.target_state=='REQ_DRAFT':
            consensus=request.context['consensus']
            for section in body['document']['sections']:
                if section['kind'] in ('behavior','acceptance'):
                    section['requirement_ids']=[c['requirement_id'] for c in consensus['claims'] if c['status']=='active']
                    section['markdown']+=' '.join(c['statement'] for c in consensus['claims'] if c['status']=='active')
                if section['kind']=='behavior':
                    section['decision_ids']=consensus['active_decision_ids']
        self.replies[request.request_id][0]=ExternalMessage(request.agent_id,json.loads(json.dumps(body)),request.request_id)
        return receipt


class InvalidScript(Scripted):
    def dispatch(self,request):
        receipt=super().dispatch(request)
        self.replies[request.request_id][0].payload['invented_business_field']=True
        return receipt


class WorkflowTests(unittest.TestCase):
    def test_transient_publication_retries_four_times_without_repeating_models(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter=Scripted()
            app=OrchestratorApp(context(),directory,multica=adapter,poll_interval=0,timeout_seconds=2)
            pump(app,'REQ_REVIEW_PUBLISH')
            from unittest.mock import patch
            app.requirements_runtime.effects.sleep=lambda seconds: None
            with patch.object(app.requirements_runtime.port,'publish_review',side_effect=TimeoutError('temporary')) as publish:
                pump(app,'REQ_ENGINE_ERROR')
                self.assertEqual(publish.call_count,5)
            self.assertEqual(len(adapter.dispatched),3)
            self.assertFalse(app.ctx.requirements.approval)
            self.assertEqual(app.ctx.requirements.budget.publication_retries[0].count,4)

    def test_pure_evidence_returns_to_critic_without_rewriting_body(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'source.txt'
            source.write_text('shared session',encoding='utf-8')
            adapter=DefectScript(source)
            app=OrchestratorApp(context(),directory,multica=adapter,poll_interval=0,timeout_seconds=2)
            pump(app,'REQ_EVIDENCE')
            before=app.ctx.requirements.draft.document_digest
            pump(app,'REQ_CONFIRM_WAIT')
            counts=Counter(r.target_state for r in adapter.dispatched)
            self.assertEqual(counts['REQ_DRAFT'],1)
            self.assertEqual(counts['REQ_REVIEW'],2)
            self.assertEqual(counts['REQ_EVIDENCE'],1)
            self.assertEqual(app.ctx.requirements.draft.document_digest,before)
            self.assertEqual(app.ctx.requirements.evidence_answers[0].fact_ids,('fact-000001',))
            self.assertTrue(all(i.status=='resolved' for i in app.ctx.requirements.issues))

    def test_mixed_defects_keep_all_findings_and_repair_in_one_writer_round(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'source.txt'
            source.write_text('shared session',encoding='utf-8')
            adapter=DefectScript(source,True)
            app=OrchestratorApp(context(),directory,multica=adapter,poll_interval=0,timeout_seconds=2)
            pump(app,'REQ_CONFIRM_WAIT')
            counts=Counter(r.target_state for r in adapter.dispatched)
            self.assertEqual(counts['REQ_DRAFT'],2)
            self.assertEqual(counts['REQ_EVIDENCE'],1)
            self.assertEqual(counts['REQ_REVIEW'],2)
            self.assertEqual(len(app.ctx.requirements.issues),2)
            self.assertEqual(app.ctx.requirements.budget.semantic_repairs,1)

    def test_two_understanding_rounds_keep_stable_question_mapping(self):
        with tempfile.TemporaryDirectory() as directory:
            app=OrchestratorApp(context(),directory,multica=ClarificationScript(),poll_interval=0,timeout_seconds=2)
            for index in (1,2):
                pump(app,'REQ_CLARIFY_WAIT')
                req=app.ctx.requirements
                self.assertEqual(req.rounds[-1].question_ids,(f'q-{index:06d}',))
                app.requirements_runtime.submit('ANSWERS_SUBMITTED',{'actor_id':'local-operator',
                    'round_id':req.rounds[-1].round_id,'answers':[{'question_id':f'q-{index:06d}',
                        'question_revision':1,'option_ids':['manual']}]},f'answer-{index}')
                app.step()
            pump(app,'REQ_CONFIRM_WAIT')
            self.assertEqual(len(app.ctx.requirements.answers),2)
            self.assertEqual(len(app.ctx.requirements.rounds),2)

    def test_format_repair_is_bounded_and_restarts_do_not_reset_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter=InvalidScript()
            app=OrchestratorApp(context(),directory,multica=adapter,poll_interval=0,timeout_seconds=2)
            pump(app,'REQ_ENGINE_ERROR')
            self.assertEqual(len(adapter.dispatched),3)
            self.assertEqual(app.ctx.requirements.budget.calls,3)
            restored=OrchestratorApp(context(),directory,multica=adapter,poll_interval=0,timeout_seconds=2)
            self.assertTrue(restored.step())  # Deliver the committed local failure notice.
            self.assertFalse(restored.step())
            self.assertEqual(len(adapter.dispatched),3)
            self.assertEqual(restored.ctx.requirements.budget.calls,3)
            self.assertFalse(restored.ctx.requirements.approval)
