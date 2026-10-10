"""Regression for late interactive prerequisite failure and audited owner repair."""
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from orchestrator.app import main, OrchestratorApp
from orchestrator.domain.requirements.model import RequirementsFailure, RequirementsRecovery
from orchestrator.domain.requirements.questions import open_round
from orchestrator.domain.requirements.owner_recovery import validate_owner_recovery
from orchestrator.runtime.requirements_recovery import verified_owner_recovery
from orchestrator.requirements_port import FeishuRequirementsPort
from test_requirements_workflow_e2e import context, Scripted, pump
from requirements_test_support import make_state, question


def blocked_context():
    ctx = context()
    req = open_round(replace(make_state(),questions=(question(),)),'REQ_ANALYZE',())
    failure = RequirementsFailure('PREREQUISITE_MISSING','REQ_CLARIFY_PUBLISH','requirements',
        'DECISION_OWNER_CHAT_OR_READY_CARD_GATEWAY_MISSING: decision_actor_ids',
        'communication','old-fingerprint')
    req = replace(req,recovery=RequirementsRecovery('REQ_CLARIFY_PUBLISH','REQ_CLARIFY_PUBLISH','publish','',failure))
    return replace(ctx,request=replace(ctx.request,communication='feishu',decision_actor_ids=()),
                   progression=replace(ctx.progression,state='REQ_BLOCKED',resume_state='REQ_CLARIFY_PUBLISH'),requirements=req)


class StartupTests(unittest.TestCase):
    def test_missing_gateway_refuses_new_task_before_transport_or_issue(self):
        with tempfile.TemporaryDirectory() as directory, patch('orchestrator.app.MulticaCliAdapter') as transport:
            with redirect_stderr(io.StringIO()) as output, self.assertRaises(SystemExit) as error:
                main(['--new','test','--runs-root',directory])
            self.assertEqual(error.exception.code,2)
            self.assertIn('ready card gateway',output.getvalue())
            transport.assert_not_called()
            self.assertFalse(list(Path(directory).glob('task-*')))

    def test_offline_gateway_refuses_before_transport(self):
        with tempfile.TemporaryDirectory() as directory, patch('orchestrator.app.MulticaCliAdapter') as transport:
            with redirect_stderr(io.StringIO()) as output, self.assertRaises(SystemExit):
                main(['--new','test','--decision-owner','ou_owner','--runs-root',directory])
            self.assertIn('ready card gateway',output.getvalue())
            transport.assert_not_called()

    def test_local_check_requires_no_credentials_or_transport(self):
        with tempfile.TemporaryDirectory() as directory, patch('orchestrator.app.MulticaCliAdapter') as transport:
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(['--check-prerequisites','--communication','local','--runs-root',directory]),0)
            self.assertIn('"ready": true',output.getvalue())
            transport.assert_not_called()

    def test_missing_secret_is_checked_without_exposing_value(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict('os.environ',{'FEISHU_GATE_APP_SECRET':''}):
            with redirect_stderr(io.StringIO()) as output,self.assertRaises(SystemExit):
                main(['--check-prerequisites','--decision-owner','ou_owner','--runs-root',directory])
            self.assertIn('FEISHU_GATE_APP_SECRET',output.getvalue())

    def test_owner_repair_commits_and_survives_restart_without_new_analysis(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter=Scripted()
            ctx=blocked_context()
            app=OrchestratorApp(ctx,directory,multica=adapter,poll_interval=0)
            runtime=app.requirements_runtime
            runtime.store.heartbeat(runtime.port.app_id,True)
            verified=verified_owner_recovery(runtime,ctx,('ou_owner',))
            self.assertEqual(runtime.port.actor_ids,())
            runtime.submit('DECISION_OWNERS_REPAIRED',verified,'repair-owners')
            app.step()
            self.assertEqual(app.ctx.progression.state,'REQ_CLARIFY_PUBLISH')
            self.assertEqual(app.ctx.request.decision_actor_ids,('ou_owner',))
            self.assertEqual(app.ctx.requirements.rounds[-1].actor_ids,('ou_owner',))
            self.assertEqual(app.ctx.requirements.budget,ctx.requirements.budget)
            self.assertEqual(app.ctx.requirements.consensus,ctx.requirements.consensus)
            restored=OrchestratorApp(ctx,directory,multica=adapter,poll_interval=0)
            self.assertEqual(restored.ctx.request.decision_actor_ids,('ou_owner',))
            self.assertEqual(restored.requirements_runtime.port.actor_ids,('ou_owner',))
            self.assertFalse(adapter.dispatched)
            # Real publication effect and authenticated callback after repair, with mock HTTP.
            http=Mock()
            http.base_url='https://mock.invalid'
            http._request.return_value={'code':0,'data':{'message_id':'om_test'}}
            restored.requirements_runtime.port.http=http
            with patch('orchestrator.adapters.feishu_notifications_enabled',return_value=True):
                pump(restored,'REQ_CLARIFY_WAIT')
            store=restored.requirements_runtime.store
            round_=restored.ctx.requirements.rounds[-1]
            gate_id=ctx.identity.task_id+':clarification:'+round_.round_id
            gate=store.gate(gate_id)
            page=round_.page_ids[0]
            callback={'header':{'app_id':restored.requirements_runtime.port.app_id,'event_type':'card.action.trigger','event_id':'answer-after-repair'},
                      'event':{'operator':{'open_id':'ou_owner'},'context':{'open_chat_id':restored.requirements_runtime.port.chat_id,'open_message_id':'om_test'},
                               'action':{'value':{'gate_id':gate_id,'token':gate['token'],'page_id':page,'kind':'clarification'},'form_value':{'q0':'manual'}}}}
            callback['event']['operator']['open_id']='ou_outsider'
            with self.assertRaisesRegex(ValueError,'GATE_BINDING'):
                store.callback(callback,restored.requirements_runtime.port.app_id)
            callback['event']['operator']['open_id']='ou_owner'
            store.callback(callback,restored.requirements_runtime.port.app_id)
            restored.step()
            self.assertEqual(restored.ctx.progression.state,'REQ_ANALYZE')
            self.assertEqual(restored.ctx.requirements.answers[-1].option_ids,('manual',))

    def test_cannot_change_existing_owner_or_published_round(self):
        ctx=blocked_context()
        for invalid in (replace(ctx,request=replace(ctx.request,decision_actor_ids=('ou_old',))),
                        replace(ctx,requirements=replace(ctx.requirements,rounds=tuple(replace(r,status='open') for r in ctx.requirements.rounds))),
                        replace(ctx,progression=replace(ctx.progression,state='REQ_CONFIRM_WAIT'))):
            with self.assertRaisesRegex(ValueError,'EMPTY_UNPUBLISHED'):
                validate_owner_recovery(invalid,('ou_owner',))

    def test_owner_repair_refuses_existing_gateway_registration(self):
        with tempfile.TemporaryDirectory() as directory:
            ctx=blocked_context()
            app=OrchestratorApp(ctx,directory,multica=Scripted())
            runtime=app.requirements_runtime
            runtime.store.register('old-gate',runtime.task_id,{'actor_ids':[]})
            with self.assertRaisesRegex(ValueError,'EXISTING_GATE'):
                verified_owner_recovery(runtime,ctx,('ou_owner',))

    def test_owner_repair_refuses_dead_gateway_without_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            ctx=blocked_context()
            app=OrchestratorApp(ctx,directory,multica=Scripted())
            with self.assertRaisesRegex(ValueError,'ready card gateway'):
                verified_owner_recovery(app.requirements_runtime,ctx,('ou_owner',))
            self.assertEqual(app.requirements_runtime.port.actor_ids,())
