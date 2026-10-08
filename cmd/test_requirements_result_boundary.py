import copy
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock

from orchestrator.contracts import contract_for_state
from orchestrator.runtime.effects import EffectOutcome
from orchestrator.runtime.requirements_agent import RequirementsAgentRunner
from orchestrator.domain.errors import CONTRACT_REJECTED_EVENT
from orchestrator.domain.requirements.result_validation import result_errors
from orchestrator.app import OrchestratorApp
from test_requirements_workflow_e2e import Scripted, context, pump


FIXTURE = Path(__file__).parent/'fixtures'/'requirements'/'result-boundary-20261007.json'


def fixture_reply(directory):
    fixture = json.loads(FIXTURE.read_text(encoding='utf-8'))
    payload = contract_for_state('REQ_ANALYZE').example_payload()
    payload.update(task_id='t',request_id='r',analysis=fixture['analysis'],questions=fixture['questions'])
    for index, fact in enumerate(payload['analysis']['facts']):
        path = Path(directory)/fact['source']
        path.write_text(fixture['source_texts'][index],encoding='utf-8')
        fact['source'] = str(path)
    return payload, {'sources':fixture['sources']}


def run_reply(payload, dispatch, *, rejected=False, root=None, raw_path=None):
    inner = Mock()
    inner.run_once.return_value = EffectOutcome('SUCCEEDED',CONTRACT_REJECTED_EVENT if rejected else 'ANALYSIS_READY',
        {'raw_reply_path':str(raw_path),'contract_rejection':'bad revision'} if raw_path else
        {'raw_reply':json.dumps(payload),'raw_reply_truncated':False,'contract_rejection':'bad revision'} if rejected else payload)
    request = SimpleNamespace(task_id='t',idempotency_key='r',payload={
        'target_state':'REQ_ANALYZE','request_id':'r','role':'review-analyst','dispatch_context':dispatch})
    return RequirementsAgentRunner(inner,allowed_root=root).run_once(request)


class MixedScript(Scripted):
    def __init__(self,path,repair=True):
        super().__init__()
        self.path,self.repair,self.attempts=path,repair,0

    def dispatch(self, request):
        receipt=super().dispatch(request)
        if request.target_state=='REQ_ANALYZE':
            self.attempts+=1
            if not self.repair or self.attempts==1:
                payload=self.replies[request.request_id][0].payload
                payload['analysis']['facts']=[{'local_key':'shared','observation':'fact',
                    'source':str(self.path),'quote':'invented quote'}]
                payload['questions']=[{'local_key':'shared','topic_key':'scope','question':'q',
                    'reason':'r','impact':'i','revision':0,'answer_kind':'text'}]
        return receipt


class BoundaryTests(unittest.TestCase):
    def test_real_model_fixture_reports_all_known_errors_on_first_response(self):
        with tempfile.TemporaryDirectory() as directory:
            payload,dispatch=fixture_reply(directory)
            original=copy.deepcopy(payload)
            outcome=run_reply(payload,dispatch)
            self.assertEqual(outcome.event_name,'FAIL')
            self.assertEqual(outcome.event_payload['error_code'],'AGENT_REPLY_VALIDATION_FAILED')
            feedback=outcome.event_payload['reason']
            self.assertIn('LOCAL_KEY_COLLISION',feedback)
            for index in range(5):
                self.assertIn(f'questions[{index}].revision: must be >= 1',feedback)
            self.assertEqual(len(outcome.event_payload['evidence_errors']),5)
            self.assertIn('FACT_QUOTE_NOT_IN_SOURCE',feedback)
            self.assertEqual(payload,original)

    def test_real_fixture_repaired_together_passes_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            payload,dispatch=fixture_reply(directory)
            for index,q in enumerate(payload['questions']):
                q['revision']=1
                q['local_key']=f'question-{index}'
            for fact in payload['analysis']['facts']:
                fact['quote']=Path(fact['source']).read_text(encoding='utf-8')
            outcome=run_reply(payload,dispatch)
            self.assertEqual(outcome.event_name,'ANALYSIS_READY')
            self.assertTrue(all(len(f['source_fingerprint'])==64 for f in outcome.event_payload['analysis']['facts']))

    def test_early_transport_rejection_also_gets_complete_feedback(self):
        with tempfile.TemporaryDirectory() as directory:
            payload,dispatch=fixture_reply(directory)
            path=Path(directory)/'raw.json'
            path.write_text(json.dumps(payload),encoding='utf-8')
            for raw_path in (None,path):
                outcome=run_reply(payload,dispatch,rejected=True,root=directory,raw_path=raw_path)
                self.assertEqual(outcome.event_name,'FAIL')
                self.assertIn('LOCAL_KEY_COLLISION',outcome.event_payload['reason'])
                self.assertEqual(len(outcome.event_payload['evidence_errors']),5)

    def test_rejected_json_cannot_be_accepted_or_cross_task_inspected(self):
        payload=contract_for_state('REQ_ANALYZE').example_payload()
        payload.update(task_id='t',request_id='r')
        outcome=run_reply(payload,{},rejected=True)
        self.assertEqual(outcome.event_name,'FAIL')
        payload['task_id']='other'
        outcome=run_reply(payload,{},rejected=True)
        self.assertEqual(outcome.event_name,CONTRACT_REJECTED_EVENT)

    def test_raw_diagnostic_path_must_stay_within_allowed_root(self):
        with tempfile.TemporaryDirectory() as directory:
            payload=contract_for_state('REQ_ANALYZE').example_payload()
            payload.update(task_id='t',request_id='r')
            path=Path(directory)/'raw.json'
            path.write_text(json.dumps(payload),encoding='utf-8')
            outcome=run_reply(payload,{},rejected=True,root=Path(directory)/'allowed',raw_path=path)
            self.assertEqual(outcome.event_name,CONTRACT_REJECTED_EVENT)

    def test_cross_collection_alias_and_duplicate_ids_are_collected(self):
        payload={'analysis':{'assumptions':[{'local_key':'same','statement':'a'},
            {'local_key':'same','statement':'b'}]},'questions':[
            {'question_id':'q','topic_key':'t','question':'q','reason':'r','impact':'i'},
            {'question_id':'q','topic_key':'t','question':'q','reason':'r','impact':'i'}]}
        feedback='\n'.join(result_errors(payload,{}))
        self.assertIn('LOCAL_KEY_COLLISION',feedback)
        self.assertIn('DUPLICATE_RECORD_ID',feedback)
        self.assertIn('omit',feedback)

    def test_paraphrased_explicit_claim_names_the_cited_human_source(self):
        payload={'analysis':{'claims':[{'requirement_id':'req-1','topic_key':'t','statement':'paraphrased goal',
            'kind':'goal','origin':'choice','source_ids':['src-000002']}]}}
        dispatch={'sources':[{'source_id':'src-000001','kind':'request','text':'request text'},
            {'source_id':'src-000002','kind':'answer','text':'建议保留原句逐字引用'}]}
        feedback='\n'.join(result_errors(payload,dispatch))
        self.assertIn('EXPLICIT_CLAIM_REQUIRES_HUMAN_QUOTE',feedback)
        self.assertIn('src-000002',feedback)
        self.assertIn('verbatim',feedback)
        self.assertIn('Full source text: 建议保留原句逐字引用',feedback)

    def test_separator_and_case_variants_still_count_as_verbatim(self):
        source='RTC-SDK 项目，Packages\\com.sdk\\Runtime中的网络层tlv做性能优化，GC优化'
        payload={'analysis':{'claims':[{'requirement_id':'req-1','topic_key':'t',
            'statement':'Packages/com.sdk/Runtime中的网络层TLV做性能优化，GC优化',
            'kind':'behavior','origin':'explicit','source_ids':['src-000001']}]}}
        dispatch={'sources':[{'source_id':'src-000001','kind':'request','text':source}]}
        self.assertEqual(result_errors(payload,dispatch),[])

    def test_numeric_bounds_and_multiple_reference_errors_are_reported(self):
        from orchestrator.contracts.common import validate_schema
        errors=[]
        validate_schema(0,{'type':'integer','minimum':1},'revision',errors)
        validate_schema(3,{'type':'integer','maximum':2},'limit',errors)
        self.assertEqual(len(errors),2)
        payload={'questions':[{'question_id':'q','topic_key':'t','question':'q','reason':'r','impact':'i',
            'default_assumption_id':'missing-a','related_requirement_ids':['missing-r'],
            'depends_on_question_ids':['q','missing-q']}]}
        feedback='\n'.join(result_errors(payload,{}))
        for field in ('default_assumption_id','related_requirement_ids','depends_on_question_ids','QUESTION_DEPENDENCY_CYCLE'):
            self.assertIn(field,feedback)

    def test_real_app_repairs_simultaneous_errors_in_one_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'source.cs'
            path.write_text('exact source',encoding='utf-8')
            adapter=MixedScript(path)
            app=OrchestratorApp(context(),directory,multica=adapter,poll_interval=0,timeout_seconds=2)
            pump(app,'REQ_CONFIRM_WAIT')
            self.assertEqual(adapter.attempts,2)
            feedback=adapter.dispatched[1].context['repair']['failure']['message']
            for code in ('LOCAL_KEY_COLLISION','FACT_QUOTE_NOT_IN_SOURCE','questions[0].revision'):
                self.assertIn(code,feedback)
            self.assertEqual(app.ctx.requirements.budget.dispatch_charges[0].format_repairs,1)
            self.assertIsNone(app.ctx.requirements.approval)

    def test_persistent_invalid_reply_still_stops_without_committing(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'source.cs'
            path.write_text('exact source',encoding='utf-8')
            adapter=MixedScript(path,repair=False)
            app=OrchestratorApp(context(),directory,multica=adapter,poll_interval=0,timeout_seconds=2)
            pump(app,'REQ_ENGINE_ERROR')
            self.assertEqual(adapter.attempts,3)
            self.assertEqual(app.ctx.requirements.consensus.facts,())
            self.assertEqual(app.ctx.requirements.questions,())

    def test_verified_recovery_reanalyzes_original_source_with_fresh_request(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'source.cs'
            path.write_text('exact source',encoding='utf-8')
            adapter=MixedScript(path,repair=False)
            app=OrchestratorApp(context(),directory,multica=adapter,poll_interval=0,timeout_seconds=2)
            pump(app,'REQ_ENGINE_ERROR')
            self.assertTrue(app.ctx.requirements.consensus.pending_source_ids)
            old_ids={request.request_id for request in adapter.dispatched}
            adapter.repair=True
            app.requirements_runtime.submit('RECOVERY_INPUT',{'_runtime_verified':True,
                'new_fingerprint':'verified-runtime-repair'},'recovery-test')
            pump(app,'REQ_CONFIRM_WAIT')
            recovered=adapter.dispatched[3]
            self.assertEqual(recovered.target_state,'REQ_ANALYZE')
            self.assertNotIn(recovered.request_id,old_ids)
            self.assertIn('repair',recovered.context)
            self.assertEqual(app.ctx.requirements.budget.work_epoch,2)
            self.assertIsNone(app.ctx.requirements.approval)


if __name__=='__main__':
    unittest.main()
