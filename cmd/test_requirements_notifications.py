"""Failure notices use committed effects, never model calls or real test sends."""
import json
import tempfile
import unittest
from unittest.mock import Mock, patch

from orchestrator.app import OrchestratorApp
from orchestrator.requirements_port import FeishuRequirementsPort, RequirementsPrerequisiteError
from test_requirements_workflow import InvalidScript
from test_requirements_workflow_e2e import context, pump


class RequirementsNotificationTests(unittest.TestCase):
    def app(self, directory):
        return OrchestratorApp(context(),directory,multica=InvalidScript(),poll_interval=0,timeout_seconds=2)

    def port(self, app, http):
        return FeishuRequirementsPort(app.root,app.requirements_runtime.store,
                                     chat_id='test-chat',http=http,publisher=Mock())

    def test_error_commits_notice_and_restart_does_not_resend(self):
        with tempfile.TemporaryDirectory() as directory:
            app = self.app(directory)
            pump(app,'REQ_ENGINE_ERROR')
            pending = app.repository.pending_effects(app.ctx.identity.task_id)
            self.assertEqual(len(pending),1)
            self.assertEqual(pending[0].request.effect_type,'requirements_notice')
            # Restart before executing the committed notification.
            restarted = OrchestratorApp(app.ctx,directory,multica=InvalidScript(),poll_interval=0,timeout_seconds=2)
            http = Mock(base_url='https://example.invalid')
            http._request.return_value = {'code':0,'data':{'message_id':'test-message'}}
            port = self.port(restarted,http)  # No decision owners: a text notice still works.
            restarted.requirements_runtime.effects.port = port
            with patch('orchestrator.adapters.feishu_notifications_enabled',return_value=True):
                self.assertTrue(restarted.step())
                self.assertFalse(restarted.step())
            self.assertEqual(http._request.call_count,1)
            body = http._request.call_args.args[2]
            self.assertEqual(body['msg_type'],'text')
            text = json.loads(body['content'])['text']
            self.assertIn('REQ_ENGINE_ERROR',text)
            self.assertIn(app.ctx.identity.task_id,text)
            self.assertEqual(len(body['uuid']),32)
            self.assertEqual(restarted.ctx.progression.state,'REQ_ENGINE_ERROR')
            self.assertTrue(app.repository.list_effect_results(app.ctx.identity.task_id)[-1][0].event_payload['delivered'])
            after_delivery = OrchestratorApp(restarted.ctx,directory,multica=InvalidScript(),poll_interval=0,timeout_seconds=2)
            after_delivery.requirements_runtime.effects.port = port
            with patch('orchestrator.adapters.feishu_notifications_enabled',return_value=True):
                self.assertFalse(after_delivery.step())
            self.assertEqual(http._request.call_count,1)

    def test_send_failure_is_durable_and_does_not_generate_another_error(self):
        with tempfile.TemporaryDirectory() as directory:
            app = self.app(directory)
            pump(app,'REQ_ENGINE_ERROR')
            http = Mock(base_url='https://example.invalid')
            http._request.return_value = {'code':999,'data':{}}
            app.requirements_runtime.effects.port = self.port(app,http)
            sequence = app.ctx.progression.sequence
            with patch('orchestrator.adapters.feishu_notifications_enabled',return_value=True):
                results = app.effects.execute_pending(app.ctx.identity.task_id)
            self.assertEqual(results[0].status,'FAILED')
            self.assertIsNone(results[0].event_name)
            self.assertIn('NOTICE_SEND_NOT_DELIVERED',results[0].event_payload['reason'])
            self.assertFalse(app.step())
            self.assertEqual(app.ctx.progression.sequence,sequence)
            self.assertEqual(http._request.call_count,1)

    def test_test_mode_writes_notice_without_contacting_feishu(self):
        with tempfile.TemporaryDirectory() as directory:
            app = self.app(directory)
            pump(app,'REQ_ENGINE_ERROR')
            http = Mock(base_url='https://example.invalid')
            app.requirements_runtime.effects.port = self.port(app,http)
            with patch.dict('os.environ',{'NEXUS_TEST_NO_EXTERNAL_NOTIFICATIONS':'1'}):
                self.assertTrue(app.step())
            http._request.assert_not_called()
            http._token.assert_not_called()
            self.assertEqual(len(list((app.root/'requirements'/'notices').glob('*.txt'))),1)

    def test_blocked_notice_and_specific_missing_owner_diagnostic(self):
        from orchestrator.domain.events import DomainEvent
        with tempfile.TemporaryDirectory() as directory:
            app = self.app(directory)
            pump(app,'REQ_ANALYZE')
            app.engine.dispatch(DomainEvent('PREREQUISITE_MISSING',app.ctx.identity.task_id,
                app.ctx.progression.sequence,{'reason':'missing decision owner'},
                occurred_at='2026-10-07T03:25:42+00:00',event_id='blocked-test'))
            self.assertEqual(app.ctx.progression.state,'REQ_BLOCKED')
            app.effects.execute_pending(app.ctx.identity.task_id)
            paths = list((app.root/'requirements'/'notices').glob('*.txt'))
            self.assertEqual(len(paths),1)
            self.assertIn('missing decision owner',paths[0].read_text(encoding='utf-8'))
            port = self.port(app,Mock())
            with self.assertRaisesRegex(RequirementsPrerequisiteError,'decision_actor_ids'):
                port.preflight()

    def test_gateway_not_ready_midrun_is_transient_not_blocking(self):
        from types import SimpleNamespace
        from pathlib import Path
        from orchestrator.domain.decisions import EffectRequest
        from orchestrator.requirements_gateway import GatewayStore
        from orchestrator.requirements_port import RequirementsTransientError
        from orchestrator.runtime.requirements_effects import RequirementsEffects
        from requirements_test_support import make_state
        with tempfile.TemporaryDirectory() as directory:
            port = FeishuRequirementsPort(Path(directory), GatewayStore(Path(directory)/'gateway.sqlite'),
                                          actor_ids=('ou_operator',), chat_id='oc_chat',
                                          http=Mock(base_url='https://example.invalid'), publisher=Mock())
            with patch.dict('os.environ', {'FEISHU_GATE_APP_ID': 'cli_test', 'FEISHU_GATE_APP_SECRET': 's'}):
                port.preflight(include_gateway=False)
                with self.assertRaisesRegex(RequirementsPrerequisiteError, 'ready card gateway'):
                    port.preflight()
                with self.assertRaises(RequirementsTransientError) as caught:
                    port.publish_clarification('task-x', SimpleNamespace(rounds=(), questions=()), 'key-1')
            self.assertIsInstance(caught.exception, ConnectionError)
            request = EffectRequest('key-1', 'requirements_clarification', 'task-x', 'key-1',
                                    payload={'state': make_state().to_dict()})
            outcome = RequirementsEffects(directory, port).run_once(request)
            self.assertEqual(outcome.event_name, 'FAIL')
            self.assertEqual(outcome.event_payload['error_code'], 'COMMUNICATION_TRANSIENT')
            self.assertTrue(outcome.event_payload['retryable'])


if __name__ == '__main__':
    unittest.main()
