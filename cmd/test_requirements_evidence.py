from dataclasses import replace
import hashlib
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock

from orchestrator.contracts import contract_for_state
from orchestrator.runtime.effects import EffectOutcome
from orchestrator.runtime.requirements_agent import RequirementsAgentRunner
from orchestrator.app import OrchestratorApp
from test_requirements_workflow_e2e import Scripted,context,pump


class EvidenceScript(Scripted):
    def __init__(self,path,repair=True):
        super().__init__()
        self.path,self.repair,self.attempts=path,repair,0

    def dispatch(self,request):
        receipt=super().dispatch(request)
        if request.target_state=='REQ_ANALYZE':
            self.attempts+=1
            body=self.replies[request.request_id][0].payload
            body['analysis']['facts']=[{'local_key':'proof','observation':'verified fact',
                'source':str(self.path),'quote':'exact source' if self.repair and self.attempts>1 else 'invented quote'}]
        return receipt


class EvidenceTests(unittest.TestCase):
    def run_reply(self,facts,root=''):
        payload=contract_for_state('REQ_ANALYZE').example_payload()
        payload['analysis']['facts']=facts
        inner=Mock()
        inner.run_once.return_value=EffectOutcome('SUCCEEDED','ANALYSIS_READY',payload)
        outcome=RequirementsAgentRunner(inner).run_once(SimpleNamespace(payload={
            'target_state':'REQ_ANALYZE','dispatch_context':{'project_root':root}}))
        return outcome,payload

    def test_fingerprint_is_runtime_owned_and_relative_source_is_canonical(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'source.cs'
            data=b'exact source\r\nsecond line\r\n'
            path.write_bytes(data)
            fact={'observation':'fact','source':'source.cs:1-2','quote':'exact source'}
            outcome,original=self.run_reply([fact],directory)
            self.assertEqual(outcome.event_name,'ANALYSIS_READY')
            verified=outcome.event_payload['analysis']['facts'][0]
            self.assertEqual(verified['source_fingerprint'],hashlib.sha256(data).hexdigest())
            self.assertEqual(verified['source'],str(path.resolve())+':1-2')
            self.assertNotIn('source_fingerprint',original['analysis']['facts'][0])

    def test_model_line_numbers_in_fingerprint_are_rejected_by_contract(self):
        outcome,_=self.run_reply([{'observation':'fact','source':'source.cs','quote':'source',
                                  'source_fingerprint':'line 160'}])
        self.assertEqual(outcome.event_payload['error_code'],'AGENT_REPLY_CONTRACT_REJECTED')
        self.assertTrue(outcome.event_payload['retryable'])

    def test_crlf_lf_equivalence_preserves_raw_byte_hash_and_indentation(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'source.cs'
            data=b'first line\r\n  second line\r\n'
            path.write_bytes(data)
            outcome,_=self.run_reply([{'observation':'a','source':str(path),'quote':'first line\n  second line'}])
            self.assertEqual(outcome.event_name,'ANALYSIS_READY')
            self.assertEqual(outcome.event_payload['analysis']['facts'][0]['source_fingerprint'],hashlib.sha256(data).hexdigest())
            # T7 alignment heals whitespace-only drift and stores the raw slice.
            outcome,_=self.run_reply([{'observation':'a','source':str(path),'quote':'first line\nsecond line'}])
            self.assertEqual(outcome.event_name,'ANALYSIS_READY')
            self.assertEqual(outcome.event_payload['analysis']['facts'][0]['quote'],'first line\r\n  second line')
            # A genuinely fabricated quote still fails.
            outcome,_=self.run_reply([{'observation':'a','source':str(path),'quote':'invented quote text'}])
            self.assertEqual(outcome.event_name,'FAIL')

    def test_all_bad_quotes_and_paths_return_scoped_feedback(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'source.cs'
            path.write_text('exact source',encoding='utf-8')
            outcome,_=self.run_reply([
                {'local_key':'bad-quote','observation':'a','source':str(path),'quote':'exact...source'},
                {'local_key':'missing-file','observation':'b','source':str(path.parent/'missing'),'quote':'b'}])
            self.assertEqual(outcome.event_payload['error_code'],'FACT_EVIDENCE_INVALID')
            self.assertTrue(outcome.event_payload['retryable'])
            errors=outcome.event_payload['evidence_errors']
            self.assertEqual([e['fact_id'] for e in errors],['bad-quote','missing-file'])

    def test_relative_paths_require_explicit_root_and_unavailable_does_not_read(self):
        outcome,_=self.run_reply([{'observation':'a','source':'source.cs','quote':'a'}])
        self.assertIn('PROJECT_ROOT',outcome.event_payload['reason'])
        outcome,_=self.run_reply([{'observation':'unknown','source':'missing','quote':'','status':'unavailable'}])
        self.assertEqual(outcome.event_name,'ANALYSIS_READY')

    def test_real_app_repairs_quotes_without_resetting_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'source.cs'
            path.write_text('exact source',encoding='utf-8')
            adapter=EvidenceScript(path)
            app=OrchestratorApp(context(),directory,multica=adapter,poll_interval=0,timeout_seconds=2)
            pump(app,'REQ_CONFIRM_WAIT')
            self.assertEqual(adapter.attempts,2)
            repair=adapter.dispatched[1].context['repair']
            self.assertIn('FACT_QUOTE_NOT_IN_SOURCE',repair['failure']['message'])
            self.assertEqual(app.ctx.requirements.budget.dispatch_charges[0].format_repairs,1)

    def test_bad_evidence_stops_after_two_repairs(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'source.cs'
            path.write_text('exact source',encoding='utf-8')
            adapter=EvidenceScript(path,repair=False)
            app=OrchestratorApp(context(),directory,multica=adapter,poll_interval=0,timeout_seconds=2)
            pump(app,'REQ_ENGINE_ERROR')
            self.assertEqual(adapter.attempts,3)
            self.assertEqual(app.ctx.requirements.budget.dispatch_charges[0].format_repairs,2)
            self.assertEqual(app.ctx.requirements.consensus.facts,())

    def test_project_root_survives_dispatch_and_dto_roundtrip(self):
        from orchestrator.domain.context import context_from_dto,context_to_dto
        from orchestrator.domain.requirements.dispatch import build_dispatch
        current=context()
        current=replace(current,request=replace(current.request,project_root=str(Path.cwd())))
        _,effect=build_dispatch(current,current.requirements,'REQ_ANALYZE')
        self.assertEqual(effect.payload['dispatch_context']['project_root'],str(Path.cwd()))
        self.assertEqual(context_from_dto(context_to_dto(current)),current)
