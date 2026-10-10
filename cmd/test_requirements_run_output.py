"""Regression for Multica keeping JSON in result.output and posting a summary."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from orchestrator.adapters import MulticaCliAdapter
from orchestrator.contracts import contract_for_state
from orchestrator.domain.errors import CONTRACT_REJECTED_EVENT, UNSTRUCTURED_REPLY_EVENT
from orchestrator.runtime.effects import EffectOutcome
from orchestrator.runtime.requirements_agent import RequirementsAgentRunner
from orchestrator.structured_output import build_structured_output_spec
from orchestrator.transport.external import AgentRequest
from orchestrator.app import OrchestratorApp
from test_requirements_workflow_e2e import Scripted, context, pump


class RunOutputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.adapter = MulticaCliAdapter(Path(self.temp.name)/'transport')
        spec = build_structured_output_spec('ZHONGSHU','review-analyst',
            {'workflow':'requirements-v1','active_runtime_state':'REQ_ANALYZE'}).to_dict()
        self.request = AgentRequest('task-1','req-1','analyst','review-analyst','ZHONGSHU','prompt','key-1',
            issue_id='issue-1',dispatch_external_message_id='trigger-1',target_state='REQ_ANALYZE',structured_output=spec)
        self.payload = contract_for_state('REQ_ANALYZE').example_payload()
        self.payload.update(task_id='task-1',request_id='req-1',structured_output_schema_hash=spec['schema_hash'])
        self.run = {'id':'run-1','agent_id':'analyst','status':'completed','trigger_comment_id':'trigger-1',
                    'result':{'output':json.dumps(self.payload)}}
        self.summary = {'id':'summary-1','author_id':'analyst','parent_id':'trigger-1',
                        'content':'REQ_ANALYZE 已完成，已整理事实和待决策问题。'}

    def poll(self, runs=None, comments=None):
        def read(*args):
            return [self.run] if args[:2]==('issue','runs') and runs is None else (
                runs if args[:2]==('issue','runs') else [self.summary] if comments is None else comments)
        with patch.object(self.adapter,'_run_with_read_retry',side_effect=read):
            return self.adapter.poll(self.request)

    def test_run_json_is_recovered_before_summary_failure_and_materialized(self):
        messages = self.poll()
        self.assertEqual(messages[0].payload['action'],'ANALYSIS_READY')
        self.assertEqual(messages[0].external_id,'run:run-1')
        path = self.adapter.prompt_bundle_builder.result_path('task-1','req-1')
        self.assertEqual(json.loads(path.read_text(encoding='utf-8')),self.payload)
        inner = Mock()
        inner.run_once.return_value = EffectOutcome('SUCCEEDED','ANALYSIS_READY',messages[0].payload)
        outcome = RequirementsAgentRunner(inner).run_once(SimpleNamespace(payload={'target_state':'REQ_ANALYZE'}))
        self.assertEqual(outcome.event_name,'ANALYSIS_READY')
        self.assertNotIn('error_code',outcome.event_payload)

    def test_fenced_run_output_uses_existing_json_parser(self):
        self.run['result']['output']='返回结果：\n```json\n'+json.dumps(self.payload)+'\n```'
        self.assertEqual(self.poll()[0].payload['action'],'ANALYSIS_READY')

    def test_explicit_task_and_request_binding_required(self):
        for field,value in [('request_id','older-request'),('task_id','other-task'),('request_id',None),('task_id',None)]:
            with self.subTest(field=field,value=value):
                payload = copy.deepcopy(self.payload)
                if value is None:
                    del payload[field]
                else:
                    payload[field]=value
                self.run['result']['output']=json.dumps(payload)
                with patch.object(self.adapter,'_matching_runs',return_value=[self.run]):
                    self.assertIsNone(self.adapter._recover_run_output(self.request,[]))
                self.assertFalse(self.adapter.prompt_bundle_builder.result_path('task-1','req-1').exists())

    def test_wrong_agent_and_noncompleted_runs_are_not_recovered(self):
        for field,value in [('agent_id','other-agent'),('status','running'),('status','failed')]:
            with self.subTest(field=field,value=value):
                run = {**self.run,field:value}
                with patch.object(self.adapter,'_matching_runs',return_value=[run]):
                    self.assertIsNone(self.adapter._recover_run_output(self.request,[]))

    def test_multiple_fenced_objects_are_not_guessed(self):
        body='```json\n'+json.dumps(self.payload)+'\n```'
        self.run['result']['output']='output\n'+body+'\n'+body
        self.assertEqual(self.poll()[0].payload['action'],UNSTRUCTURED_REPLY_EVENT)

    def test_business_contract_rejection_takes_priority_over_summary(self):
        del self.payload['analysis']
        self.run['result']['output']=json.dumps(self.payload)
        reply = self.poll()[0]
        self.assertEqual(reply.payload['action'],CONTRACT_REJECTED_EVENT)
        self.assertIn('analysis',reply.payload['contract_rejection'])
        self.assertTrue(Path(reply.payload['raw_reply_path']).is_file())

    def test_valid_comment_has_priority_and_needs_no_execution_lookup(self):
        self.run['result']['output']='bad result'
        comment = {**self.summary,'content':json.dumps(self.payload)}
        def read(*args):
            self.assertNotEqual(args[:2],('issue','runs'))
            return [comment]
        with patch.object(self.adapter,'_run_with_read_retry',side_effect=read):
            self.assertEqual(self.adapter.poll(self.request)[0].payload['action'],'ANALYSIS_READY')

    def test_sentinels_pass_through_role_wrapper_unchanged(self):
        for action in (CONTRACT_REJECTED_EVENT,UNSTRUCTURED_REPLY_EVENT):
            with self.subTest(action=action):
                inner = Mock()
                original = EffectOutcome('SUCCEEDED',action,{'action':action,'raw_reply':'summary',
                    'contract_rejection':'analysis: required field missing'})
                inner.run_once.return_value = original
                outcome = RequirementsAgentRunner(inner).run_once(SimpleNamespace(payload={'target_state':'REQ_ANALYZE'}))
                self.assertIs(outcome,original)


class SummaryThenJson(Scripted):
    def __init__(self):
        super().__init__()
        self.analyze_attempts=0

    def dispatch(self,request):
        receipt=super().dispatch(request)
        if request.target_state=='REQ_ANALYZE':
            self.analyze_attempts+=1
            if self.analyze_attempts==1:
                self.replies[request.request_id][0].payload.clear()
                self.replies[request.request_id][0].payload.update(action=UNSTRUCTURED_REPLY_EVENT,raw_reply='分析已完成')
        return receipt


class RealAppReplyRepairTests(unittest.TestCase):
    def test_unstructured_failure_gets_specific_repair_feedback_then_converges(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter=SummaryThenJson()
            app=OrchestratorApp(context(),directory,multica=adapter,poll_interval=0,timeout_seconds=2)
            pump(app,'REQ_CONFIRM_WAIT')
            self.assertEqual(adapter.analyze_attempts,2)
            failure=adapter.dispatched[1].context['repair']['failure']
            self.assertEqual(failure['code'],'AGENT_REPLY_UNSTRUCTURED')
            self.assertIn('one complete JSON object',failure['message'])
            self.assertNotIn('unknown field',failure['message'])
            artifact=adapter.dispatched[1].context['repair']['failed_reply_ref']
            self.assertTrue(Path(artifact).is_file())
            self.assertEqual(json.loads(Path(artifact).read_text(encoding='utf-8'))['raw_reply'],'分析已完成')
            charge=app.ctx.requirements.budget.dispatch_charges[0]
            self.assertEqual(charge.format_repairs,1)
            self.assertEqual(charge.external_retries,0)
