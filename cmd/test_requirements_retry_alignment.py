import copy,json,tempfile,unittest
from dataclasses import asdict,replace
from pathlib import Path
from requirements_test_support import make_state
from test_requirements_result_boundary import run_reply,MixedScript
from test_requirements_workflow_e2e import context,pump
from orchestrator.app import OrchestratorApp
from orchestrator.contracts import contract_for_state
from orchestrator.domain.requirements.model import AssumptionRecord
from orchestrator.domain.requirements.result_validation import result_errors


FIXTURE=Path(__file__).parent/'fixtures/requirements/retry-alignment-20261007.json'


class RetryAlignmentTests(unittest.TestCase):
    def test_real_failure_reports_all_ten_quotes_and_accepted_assumption_together(self):
        fixture=json.loads(FIXTURE.read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory() as directory:
            for name,text in fixture['source_texts'].items():
                (Path(directory)/name).write_text(text,encoding='utf-8')
            payload=contract_for_state('REQ_ANALYZE').example_payload()
            payload.update(task_id='t',request_id='r',analysis=fixture['analysis'],questions=fixture['questions'])
            original=copy.deepcopy(payload)
            dispatch={'sources':fixture['sources'],'project_root':directory,'consensus':{'assumptions':[]}}
            outcome=run_reply(payload,dispatch)
            self.assertEqual(outcome.event_name,'FAIL')
            # Fact 9 is a real two-field excerpt flattened onto one line: T7
            # alignment heals it, the other nine stitched quotes still fail.
            self.assertEqual(len(outcome.event_payload['evidence_errors']),9)
            healed=outcome.event_payload['analysis']['facts'][9]['quote']
            self.assertIn(healed.replace('\r\n','\n'),fixture['source_texts'][fixture['analysis']['facts'][9]['source']])
            feedback=outcome.event_payload['reason']
            self.assertIn('analysis.assumptions[0].disposition: MODEL_CANNOT_ACCEPT_ASSUMPTION',feedback)
            self.assertIn('set disposition="proposed"',feedback)
            self.assertIn('changing assumption_id',feedback)
            self.assertEqual(payload,original)
            # Correct all reported fields in the same original payload; no identity substitution.
            payload['analysis']['assumptions'][0]['disposition']='proposed'
            for fact in payload['analysis']['facts']:
                fact['quote']=fixture['source_texts'][fact['source']]
            repaired=run_reply(payload,dispatch)
            self.assertEqual(repaired.event_name,'ANALYSIS_READY')

    def test_existing_accepted_assumption_echo_is_allowed_but_modifications_are_not(self):
        existing=asdict(AssumptionRecord('accepted-id','human decision',disposition='accepted'))
        dispatch={'consensus':{'assumptions':[existing]}}
        payload={'analysis':{'assumptions':[{**existing,'local_key':'echo'}]}}
        self.assertEqual(result_errors(payload,dispatch),[])
        for field,value in (('statement','model rewrite'),('disposition','proposed'),('assumption_id','new-id')):
            changed=copy.deepcopy(payload)
            changed['analysis']['assumptions'][0][field]=value
            feedback='\n'.join(result_errors(changed,dispatch))
            self.assertIn('PROTECTED' if field!='assumption_id' else 'MODEL_CANNOT_ACCEPT_ASSUMPTION',feedback)
        self.assertEqual(result_errors({'analysis':{'assumptions':[]}},dispatch),[])

    def test_multiple_forbidden_assumptions_are_all_indexed(self):
        payload={'analysis':{'assumptions':[{'statement':'a','disposition':'accepted'},
            {'statement':'b','disposition':'accepted','assumption_id':'b'}]}}
        feedback='\n'.join(result_errors(payload,{}))
        for index in range(2):
            self.assertIn(f'analysis.assumptions[{index}].disposition',feedback)

    def test_actual_dispatched_schema_explains_conditional_write_permissions(self):
        from types import SimpleNamespace
        from orchestrator.adapters import _response_contract_for
        from orchestrator.runtime.compat_effects import _skill_lock
        contract=contract_for_state('REQ_ANALYZE')
        field=contract.schema['properties']['analysis']['properties']['assumptions']['items']['properties']['disposition']
        self.assertIn('accepted',field['enum'])
        self.assertIn('unchanged in every field',field['description'])
        self.assertIn('New assumptions must remain proposed',str(contract.prompt_rules))
        envelope=_response_contract_for(SimpleNamespace(target_state='REQ_ANALYZE'))
        self.assertIn('New assumptions must remain proposed',envelope['instruction'])
        lock=_skill_lock('REQ_ANALYZE')
        self.assertEqual(lock['status'],'locked')
        self.assertIn('Assumption write permissions',Path(lock['source']).read_text(encoding='utf-8'))

    def test_real_app_repairs_quote_and_permission_together_in_one_retry(self):
        class MixedPermissionScript(MixedScript):
            def dispatch(self,request):
                receipt=super().dispatch(request)
                if request.target_state=='REQ_ANALYZE' and self.attempts==1:
                    self.replies[request.request_id][0].payload['analysis']['assumptions']=[
                        {'statement':'proposal','disposition':'accepted'}]
                return receipt
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'source.cs'
            path.write_text('exact source',encoding='utf-8')
            adapter=MixedPermissionScript(path)
            app=OrchestratorApp(context(),directory,multica=adapter,poll_interval=0,timeout_seconds=2)
            pump(app,'REQ_CONFIRM_WAIT')
            self.assertEqual(adapter.attempts,2)
            message=adapter.dispatched[1].context['repair']['failure']['message']
            self.assertIn('analysis.assumptions[0].disposition',message)
            self.assertIn('FACT_QUOTE_NOT_IN_SOURCE',message)
            self.assertIsNone(app.ctx.requirements.approval)
