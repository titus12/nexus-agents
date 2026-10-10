"""Adversarial regression at operating-system and external-message boundaries."""
from dataclasses import replace
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from orchestrator.locks import TaskLock
from orchestrator.app import OrchestratorApp
from orchestrator.requirements_gateway import GatewayStore
from orchestrator.requirements_port import FeishuRequirementsPort
from orchestrator.domain.events import DomainEvent
from orchestrator.domain.requirements.states import RequirementsWorkflowState
from orchestrator.domain.requirements.transitions import RequirementsTransitionRegistry
from test_requirements_startup import blocked_context
from test_requirements_workflow_e2e import context, Scripted


class SelfReviewTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt','Windows process semantics')
    def test_real_windows_child_survives_expired_lease_probe(self):
        child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],
                               stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                               creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            with tempfile.TemporaryDirectory() as directory:
                lock=TaskLock(Path(directory)/'task.lock')
                for _ in range(3):
                    self.assertFalse(lock._can_reclaim({'pid':child.pid,'lease_until':time.time()-1}))
                    self.assertIsNone(child.poll())
                child.terminate()
                child.wait(timeout=5)
                self.assertTrue(lock._can_reclaim({'pid':child.pid,'lease_until':time.time()-1}))
        finally:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=5)

    @unittest.skipUnless(os.name == 'nt','Windows process semantics')
    def test_windows_liveness_probe_never_calls_os_kill(self):
        with tempfile.TemporaryDirectory() as directory:
            lock=TaskLock(Path(directory)/'task.lock')
            with patch('orchestrator.locks.os.kill') as kill:
                self.assertFalse(lock._can_reclaim({'pid':os.getpid(),'lease_until':time.time()-1}))
                kill.assert_not_called()

    def test_stale_owner_repair_after_cancel_is_ignored(self):
        ctx=blocked_context()
        ctx=replace(ctx,progression=replace(ctx.progression,state='REQ_CANCELLED',resume_state=None))
        handler=RequirementsWorkflowState('REQ_CANCELLED',RequirementsTransitionRegistry())
        event=DomainEvent('DECISION_OWNERS_REPAIRED',ctx.identity.task_id,ctx.progression.sequence,
                          {'actor_ids':['ou_owner'],'_runtime_verified':True,'expected_sequence':0},'')
        decision=handler.handle(ctx,event)
        self.assertIsNone(decision.transition)
        self.assertIsNone(decision.update.repair_decision_actor_ids)

    def test_stale_owner_repair_basis_does_not_crash_active_runtime(self):
        ctx=blocked_context()
        handler=RequirementsWorkflowState('REQ_BLOCKED',RequirementsTransitionRegistry())
        event=DomainEvent('DECISION_OWNERS_REPAIRED',ctx.identity.task_id,ctx.progression.sequence,
                          {'actor_ids':['ou_owner'],'_runtime_verified':True,'new_fingerprint':'changed',
                           'expected_sequence':ctx.progression.sequence-1,'expected_failure_fingerprint':'old-fingerprint'},'')
        decision=handler.handle(ctx,event)
        self.assertIsNone(decision.transition)
        self.assertIsNone(decision.update.repair_decision_actor_ids)

    def test_message_recovery_requires_exact_live_card_receipt(self):
        import json
        with tempfile.TemporaryDirectory() as directory,patch.dict('os.environ',{'FEISHU_GATE_APP_ID':'cli_gate'}):
            http=Mock(base_url='https://mock.invalid')
            port=FeishuRequirementsPort(directory,GatewayStore(Path(directory)/'gateway.sqlite'),chat_id='oc_chat',http=http)
            gate={'gate_id':'gate','token':'token','verification_page_id':'page'}
            content=json.dumps({'value':{'gate_id':'gate','token':'token','page_id':'page'}})
            good={'message_id':'om_wanted','chat_id':'oc_chat','sender':{'id':'cli_gate','sender_type':'app'},
                  'msg_type':'interactive','deleted':False,'body':{'content':content}}
            for extra in ({'message_id':'om_other'},{'deleted':True},{'msg_type':'text'},{'sender':{'id':'cli_gate','sender_type':'user'}}):
                http._request.return_value={'code':0,'data':{'items':[{**good,**extra}]}}
                self.assertFalse(port.verify_message('om_wanted',gate),extra)
            http._request.return_value={'code':0,'data':{'items':[good]}}
            self.assertTrue(port.verify_message('om_wanted',gate))

    def test_idle_cancel_with_prior_feedback_never_dispatches_model(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter=Scripted()
            app=OrchestratorApp(context(),directory,multica=adapter,poll_interval=0)
            app.step()  # Commit START but do not execute its model effect.
            app.requirements_runtime.submit('REQUIREMENTS_FEEDBACK',{'text':'additional scope','actor_id':'local-operator'},'prior-feedback')
            self.assertTrue(app.cancel())
            self.assertEqual(app.ctx.progression.state,'REQ_CANCELLED')
            self.assertFalse(adapter.dispatched)

    def test_local_cancel_bypasses_early_callback_receipt_barrier(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter=Scripted()
            app=OrchestratorApp(context(),directory,multica=adapter,poll_interval=0)
            store=app.requirements_runtime.store
            desc={'actor_ids':['ou_owner'],'chat_id':'oc_test','kind':'clarification','round_id':'round',
                  'pages':{'page':{'send_key':'key','questions':{}}}}
            token=store.register('early-gate',app.ctx.identity.task_id,desc)
            store.send_intent('early-gate','page','key')
            body={'header':{'app_id':'cli_test','event_type':'card.action.trigger','event_id':'early'},
                  'event':{'operator':{'open_id':'ou_owner'},'context':{'open_chat_id':'oc_test','open_message_id':'om_early'},
                           'action':{'value':{'gate_id':'early-gate','token':token,'page_id':'page','kind':'clarification'},'form_value':{'q0_text':'answer'}}}}
            store.callback(body,'cli_test')
            self.assertTrue(app.cancel())
            self.assertEqual(app.ctx.progression.state,'REQ_CANCELLED')
            self.assertEqual(store.gate('early-gate')['status'],'CANCELLED')
            self.assertFalse(adapter.dispatched)

    def test_two_owner_repairs_queued_before_commit_do_not_crash_or_reassign(self):
        from orchestrator.runtime.requirements_recovery import verified_owner_recovery
        with tempfile.TemporaryDirectory() as directory:
            ctx=blocked_context()
            app=OrchestratorApp(ctx,directory,multica=Scripted(),poll_interval=0)
            runtime=app.requirements_runtime
            runtime.store.heartbeat(runtime.port.app_id,True)
            for actor,key in (('ou_first','first'),('ou_second','second')):
                runtime.submit('DECISION_OWNERS_REPAIRED',verified_owner_recovery(runtime,ctx,(actor,)),key)
            app.step()
            app.step()
            self.assertEqual(app.ctx.progression.state,'REQ_CLARIFY_PUBLISH')
            self.assertEqual(app.ctx.request.decision_actor_ids,('ou_first',))
