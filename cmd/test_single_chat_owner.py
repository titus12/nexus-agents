"""Old commands use authenticated callbacks in the configured single-human chat."""
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
import io
import tempfile
import unittest
from unittest.mock import Mock,patch

from orchestrator.app import main,build_context,OrchestratorApp
from orchestrator.requirements_startup import check_startup
from orchestrator.requirements_gateway import GatewayStore,GatewayBridge
from orchestrator.requirements_port import FeishuRequirementsPort
from orchestrator.domain.requirements.authorization import authorized_actor
from test_requirements_startup import blocked_context
from test_requirements_workflow_e2e import Scripted,pump,context


class SingleOwnerTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict('os.environ',{'FEISHU_GATE_APP_ID':'cli_gate','FEISHU_GATE_APP_SECRET':'test-secret','HUMAN_GATE_CHAT_ID':'oc_test'})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_default_readiness_does_not_query_members_or_create_remote_work(self):
        with tempfile.TemporaryDirectory() as directory:
            GatewayStore(Path(directory)/'requirements-gateway.sqlite').heartbeat('cli_gate',True)
            with patch('orchestrator.adapters.FeishuHttpAdapter') as http,patch('orchestrator.app.MulticaCliAdapter') as adapter,redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(['--check-prerequisites','--runs-root',directory]),0)
            adapter.assert_not_called()
            http.return_value._token.assert_not_called()
            http.return_value._request.assert_not_called()
            self.assertIn('chat-members',output.getvalue())

    def test_legacy_new_command_persists_chat_policy_without_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            GatewayStore(Path(directory)/'requirements-gateway.sqlite').heartbeat('cli_gate',True)
            with patch('orchestrator.app.MulticaCliAdapter') as adapter,patch('orchestrator.app.OrchestratorApp') as app:
                adapter.return_value.create_issue.return_value='issue'
                app.return_value.run.return_value=True
                self.assertEqual(main(['--new','RTC review','--project','project','--runs-root',directory]),0)
            ctx=app.call_args.args[0]
            self.assertEqual(ctx.request.decision_actor_ids,())
            self.assertEqual(ctx.request.decision_owner_policy,'chat-members')
            self.assertEqual(ctx.request.raw_request,'RTC review')

    def test_explicit_owner_remains_optional_and_restrictive(self):
        self.assertFalse(authorized_actor('ou_other',('ou_owner',),{'_verified_chat_member':True}))
        self.assertTrue(authorized_actor('ou_owner',('ou_owner',),{}))
        self.assertFalse(authorized_actor('ou_owner',(),{}))
        self.assertFalse(authorized_actor('cli_bot',(),{'_verified_chat_member':True}))

    def test_default_card_callback_advances_through_real_bridge_and_handler(self):
        with tempfile.TemporaryDirectory() as directory:
            ctx=blocked_context()
            ctx=replace(ctx,request=replace(ctx.request,decision_owner_policy='chat-members'),
                        progression=replace(ctx.progression,state='REQ_CLARIFY_PUBLISH',resume_state=None))
            adapter=Scripted()
            app=OrchestratorApp(ctx,directory,multica=adapter,poll_interval=0)
            runtime=app.requirements_runtime
            runtime.store.heartbeat('cli_gate',True)
            http=Mock(base_url='https://mock.invalid')
            http._request.return_value={'code':0,'data':{'message_id':'om_card'}}
            runtime.port.http=http
            req=app.ctx.requirements
            with patch('orchestrator.adapters.feishu_notifications_enabled',return_value=True):
                runtime.port.publish_clarification(runtime.task_id,req,'card-key')
            # Publication succeeded; execute its ordinary domain transition.
            from orchestrator.domain.events import DomainEvent
            app.inbox.publish(DomainEvent('CARD_DELIVERED',runtime.task_id,app.ctx.progression.sequence,{},'2026-10-07T06:00:00+00:00', 'card-delivered'))
            app.step()
            gate_id=runtime.task_id+':clarification:'+req.rounds[-1].round_id
            gate=runtime.store.gate(gate_id)
            page=req.rounds[-1].page_ids[0]
            body={'header':{'app_id':'cli_gate','event_type':'card.action.trigger','event_id':'answer'},
                  'event':{'operator':{'open_id':'ou_me'},'context':{'open_chat_id':'oc_test','open_message_id':'om_card'},
                           'action':{'value':{'gate_id':gate_id,'token':gate['token'],'page_id':page,'kind':'clarification'},'form_value':{'q0':'manual'}}}}
            for field,value in (('open_chat_id','oc_other'),('open_message_id','om_other')):
                invalid=__import__('copy').deepcopy(body)
                invalid['event']['context'][field]=value
                if field=='open_chat_id':
                    with self.assertRaises(ValueError):runtime.store.callback(invalid,'cli_gate')
                else:
                    invalid['header']['event_id']='wrong-message'
                    runtime.store.callback(invalid,'cli_gate')
                    app.step()
                    self.assertEqual(app.ctx.progression.state,'REQ_CLARIFY_WAIT')
            runtime.store.callback(body,'cli_gate')
            app.step()
            self.assertEqual(app.ctx.progression.state,'REQ_ANALYZE')
            self.assertEqual(app.ctx.requirements.answers[-1].option_ids,('manual',))

    def test_policy_survives_snapshot_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            ctx=blocked_context()
            ctx=replace(ctx,request=replace(ctx.request,decision_owner_policy='chat-members'))
            app=OrchestratorApp(ctx,directory,multica=Scripted())
            restored=OrchestratorApp(ctx,directory,multica=Scripted())
            self.assertEqual(restored.ctx.request.decision_owner_policy,'chat-members')
            self.assertEqual(restored.requirements_runtime.port.owner_policy,'chat-members')

    def test_group_approval_callback_still_requires_current_document_and_gate(self):
        with tempfile.TemporaryDirectory() as source, tempfile.TemporaryDirectory() as target:
            fixture=OrchestratorApp(context(),source,multica=Scripted(),poll_interval=0)
            pump(fixture,'REQ_CONFIRM_WAIT')
            ctx=fixture.ctx
            ctx=replace(ctx,request=replace(ctx.request,communication='feishu',decision_owner_policy='chat-members',decision_actor_ids=()),
                        requirements=replace(ctx.requirements,review_gate_id='group-review'))
            app=OrchestratorApp(ctx,target,multica=Scripted(),poll_interval=0)
            store=app.requirements_runtime.store
            req=app.ctx.requirements
            token=store.register('group-review',ctx.identity.task_id,{'kind':'review','owner_policy':'chat-members','actor_ids':[],
                'chat_id':'oc_test','document_digest':req.draft.document_digest,'review_input_digest':req.review_stamp.input_digest,
                'pages':{'review':{'send_key':'review-key','questions':{}}}})
            store.send_intent('group-review','review','review-key')
            store.delivered('group-review','review','om_review')
            body={'header':{'app_id':'cli_gate','event_type':'card.action.trigger','event_id':'approval'},
                  'event':{'operator':{'open_id':'ou_me'},'context':{'open_chat_id':'oc_test','open_message_id':'om_review'},
                           'action':{'value':{'gate_id':'group-review','token':token,'page_id':'review','kind':'approve'}}}}
            store.callback(body,'cli_gate')
            app.step()
            self.assertEqual(app.ctx.progression.state,'REQ_APPROVAL_VERIFY')
            self.assertEqual(app.ctx.requirements.pending_approval.actor_id,'ou_me')
            self.assertIsNone(app.ctx.requirements.approval)  # Cloud readback is still required.
