import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from orchestrator.requirements_gateway import GatewayStore, GatewayBridge
from orchestrator.app import OrchestratorApp
from test_requirements_workflow_e2e import context, Scripted


def descriptor():
    return {'actor_ids':['ou_owner'],'chat_id':'oc_test','pages':{'review':{'send_key':'send-1','questions':{}}},
        'document_digest':'a'*64,'review_input_digest':'b'*64}


def callback(token, event_id='event-1',kind='feedback'):
    return {'header':{'app_id':'cli_gate','event_type':'card.action.trigger','event_id':event_id},
        'event':{'operator':{'open_id':'ou_owner'},'context':{'open_chat_id':'oc_test','open_message_id':'om_test'},
            'action':{'value':{'gate_id':'gate-1','token':token,'page_id':'review','kind':kind},
                      'form_value':{'feedback':'失败后不自动重试'}}}}


class GatewayTests(unittest.TestCase):
    def make(self,directory):
        store = GatewayStore(Path(directory)/'gateway.sqlite')
        token = store.register('gate-1','task-requirements',descriptor())
        store.send_intent('gate-1','review','send-1')
        store.delivered('gate-1','review','om_test')
        return store,token

    def test_callback_is_durable_before_success_and_duplicates_do_not_repeat(self):
        with tempfile.TemporaryDirectory() as directory:
            store,token = self.make(directory)
            toast = store.callback(callback(token),'cli_gate')
            self.assertEqual(toast['toast']['type'],'info')
            reopened = GatewayStore(store.path)
            self.assertEqual(reopened.next('task-requirements')['payload']['form_value']['feedback'],'失败后不自动重试')
            reopened.callback(callback(token),'cli_gate')
            with reopened.connect() as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM inbox').fetchone()[0],1)

    def test_wrong_app_actor_chat_and_token_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            store,token = self.make(directory)
            for path,value in [(('header','app_id'),'wrong'),(('event','operator','open_id'),'outsider'),
                (('event','context','open_chat_id'),'another-chat'),(('event','action','value','token'),'wrong')]:
                body = callback(token)
                obj = body
                for k in path[:-1]:
                    obj = obj[k]
                obj[path[-1]]=value
                with self.assertRaises(ValueError):
                    store.callback(body,'cli_gate')
            self.assertIsNone(store.next('task-requirements'))

    def test_storage_failure_does_not_acknowledge_success(self):
        with tempfile.TemporaryDirectory() as directory:
            store,token = self.make(directory)
            with patch.object(store,'connect',side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    store.callback(callback(token),'cli_gate')

    def test_barrier_holds_prior_feedback_and_preserves_later_feedback(self):
        with tempfile.TemporaryDirectory() as directory:
            store,token = self.make(directory)
            store.callback(callback(token),'cli_gate')
            self.assertFalse(store.close_for_approval('gate-1','approval'))
            row=store.next('task-requirements')
            store.acknowledge(row['inbox_id'],'DONE','feedback')
            self.assertTrue(store.close_for_approval('gate-1','approval'))
            store.callback(callback(token,'later'),'cli_gate')
            self.assertIsNone(store.next('task-requirements'))
            with store.connect() as db:
                self.assertEqual(db.execute("SELECT status FROM inbox WHERE event_id='later'").fetchone()[0],'FOLLOWUP')

    def test_bridge_recovers_commit_before_callback_ack_once(self):
        with tempfile.TemporaryDirectory() as directory:
            store,token=self.make(directory)
            app=OrchestratorApp(context(),directory,multica=Scripted(),poll_interval=0,timeout_seconds=2)
            bridge=GatewayBridge(store,app.repository)
            store.callback(callback(token),'cli_gate')
            self.assertTrue(bridge.poll('task-requirements'))
            self.assertTrue(bridge.poll('task-requirements'))
            events=app.repository.pending_domain_events('task-requirements')
            self.assertEqual(len(events),1)
            app.engine.dispatch(events[0])
            app.inbox.ack(events[0])
            self.assertFalse(bridge.poll('task-requirements'))
            self.assertIsNone(store.next('task-requirements'))

    def test_early_callback_survives_missing_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            store=GatewayStore(Path(directory)/'gateway.sqlite')
            token=store.register('gate-1','task-requirements',descriptor())
            store.send_intent('gate-1','review','send-1')
            store.callback(callback(token),'cli_gate')
            app=OrchestratorApp(context(),directory,multica=Scripted(),poll_interval=0,timeout_seconds=2)
            bridge=GatewayBridge(store,app.repository)
            self.assertTrue(bridge.poll('task-requirements'))
            self.assertFalse(app.repository.pending_domain_events('task-requirements'))
            self.assertEqual(store.next('task-requirements')['status'],'RECEIVED')
            store.delivered('gate-1','review','om_test')
            self.assertTrue(bridge.poll('task-requirements'))
            self.assertEqual(len(app.repository.pending_domain_events('task-requirements')),1)

    def test_unknown_send_outcome_never_blindly_sends_again(self):
        with tempfile.TemporaryDirectory() as directory:
            store=GatewayStore(Path(directory)/'gateway.sqlite')
            store.register('gate-1','task-requirements',descriptor())
            store.send_intent('gate-1','review','send-1')
            self.assertTrue(store.begin_send('gate-1','review'))
            restored=GatewayStore(store.path)
            self.assertFalse(restored.begin_send('gate-1','review'))
            restored.delivered('gate-1','review','om_test')
            self.assertEqual(restored.send_intent('gate-1','review','send-1')['message_id'],'om_test')

    def test_approval_barrier_includes_older_clarification_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            store,token=self.make(directory)
            old=descriptor()
            old['pages']['review']['send_key']='old-send'
            store.register('old-gate','task-requirements',old)
            store.local('task-requirements','ANSWERS_SUBMITTED',{'round_id':'old-round'},'old-answer')
            self.assertFalse(store.close_for_approval('gate-1','approval'))
            self.assertEqual(store.gate('old-gate')['status'],'CLOSED')


if __name__ == '__main__':
    unittest.main()
