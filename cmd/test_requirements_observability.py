import logging
from dataclasses import replace
import tempfile
import unittest
from unittest.mock import patch

import test_requirements_gateway as fixtures
from test_requirements_workflow_e2e import context, Scripted
from orchestrator.app import OrchestratorApp
from orchestrator.requirements_gateway import GatewayBridge
from orchestrator.requirements_observability import SafeSdkHandler, error_code, answer_progress, frame_metadata


class ObservabilityTests(unittest.TestCase):
    def test_frame_diagnostics_only_include_bounded_transport_identifiers(self):
        from types import SimpleNamespace
        frame = SimpleNamespace(headers=[SimpleNamespace(key='type', value='event'),
            SimpleNamespace(key='message_id', value='m' * 300),
            SimpleNamespace(key='token', value='PRIVATE TOKEN')], payload=b'PRIVATE ANSWERS')
        self.assertEqual(frame_metadata(frame), {'type': 'event', 'message_id': 'm' * 128})

    def test_partial_answer_progress_identifies_missing_question_and_ignores_stale_revision(self):
        from requirements_test_support import make_state, question
        from orchestrator.domain.requirements.questions import open_round, accept_answers
        state = open_round(replace(make_state(), questions=(question(), question('q-000002'))),
                           'REQ_ANALYZE', ('ou-owner',))
        state = accept_answers(state, {'round_id': 'round-000001', 'actor_id': 'ou-owner', 'event_id': 'answer',
                   'answers': [{'question_id': 'q-000001', 'question_revision': 1, 'option_ids': ['manual']}]})
        progress = answer_progress(state, 'round-000001')
        self.assertEqual(progress['round_status'], 'partial')
        self.assertEqual(progress['remaining_question_ids'], ['q-000002'])
        state = replace(state, questions=(replace(state.questions[0], revision=2), state.questions[1]))
        self.assertEqual(answer_progress(state, 'round-000001')['answered_count'], 0)

    def test_callback_accept_duplicate_reject_and_storage_failure_are_distinct_and_redacted(self):
        with tempfile.TemporaryDirectory() as directory:
            store, token = fixtures.GatewayTests().make(directory)
            body = fixtures.callback(token)
            body['event']['action']['form_value']['feedback'] = 'PRIVATE ANSWER BODY'
            with self.assertLogs('review_orchestrator_fsm', level='INFO') as logs:
                store.callback(body, 'cli_gate')
                store.callback(body, 'cli_gate')
                with self.assertRaises(ValueError):
                    store.callback(body, 'wrong-app')
                with patch.object(store, 'connect', side_effect=OSError('SECRET STORAGE DETAILS')):
                    with self.assertRaises(OSError):
                        store.callback(body, 'cli_gate')
            output = '\n'.join(logs.output)
            for marker in ('CARD_CALLBACK_RECEIVED', 'CARD_CALLBACK_COMMITTED',
                           'CARD_CALLBACK_DUPLICATE', 'CARD_CALLBACK_REJECTED',
                           'CARD_CALLBACK_NOT_ACKNOWLEDGED', 'CALLBACK_APP_BINDING_INVALID'):
                self.assertIn(marker, output)
            for secret in (token, 'PRIVATE ANSWER BODY', 'SECRET STORAGE DETAILS'):
                self.assertNotIn(secret, output)

    def test_bridge_reject_reason_and_receipt_wait_are_logged_without_poll_spam(self):
        with tempfile.TemporaryDirectory() as directory:
            store, token = fixtures.GatewayTests().make(directory)
            app = OrchestratorApp(context(), directory, multica=Scripted(), poll_interval=0, timeout_seconds=2)
            bridge = GatewayBridge(store, app.repository)
            body = fixtures.callback(token)
            body['event']['context']['open_message_id'] = 'om_wrong'
            store.callback(body, 'cli_gate')
            with self.assertLogs('review_orchestrator_fsm', level='INFO') as logs:
                bridge.poll('task-requirements')
            self.assertIn('CALLBACK_MESSAGE_MISMATCH', '\n'.join(logs.output))
            store.callback(fixtures.callback(token, 'second'), 'cli_gate')
            with store.connect() as db:
                db.execute("UPDATE messages SET message_id='' WHERE gate_id='gate-1'")
            with self.assertLogs('review_orchestrator_fsm', level='INFO') as logs:
                for _ in range(10):
                    bridge.poll('task-requirements')
            self.assertEqual(sum('CARD_BRIDGE_WAIT' in line for line in logs.output), 1)

    def test_sdk_failure_does_not_emit_raw_message_or_exception(self):
        handler = SafeSdkHandler()
        event = logging.LogRecord('Lark', logging.ERROR, __file__, 1,
                                  'secret=%s', ('PRIVATE SDK DATA',), None)
        with self.assertLogs('review_orchestrator_fsm', level='INFO') as logs:
            handler.emit(event)
        self.assertIn('GATEWAY_SDK_DIAGNOSTIC', logs.output[0])
        self.assertNotIn('PRIVATE SDK DATA', logs.output[0])
        self.assertEqual(error_code(ValueError('PRIVATE ANSWER BODY')), 'ValueError')


if __name__ == '__main__':
    unittest.main()
