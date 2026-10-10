"""Lifecycle notification acceptance through the actual journal and app."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from orchestrator.app import OrchestratorApp
from orchestrator.requirements_port import LocalRequirementsPort, FeishuRequirementsPort
from orchestrator.runtime.notification_effects import FeishuNotificationPort, NullNotificationPort
from test_requirements_workflow_e2e import Scripted, context, pump
from test_requirements_run_output import SummaryThenJson


class RecordingProgress(LocalRequirementsPort):
    def __init__(self, app, order=None):
        super().__init__(app.root,app.requirements_runtime.store)
        self.requests=[]
        self.order=order if order is not None else []

    def publish_progress(self, task_id, key, body):
        self.requests.append((key,body))
        self.order.append(('notify',body))
        return {'channel':'recording','message_id':'message-'+str(len(self.requests)),'delivered':True}


class OrderedScript(Scripted):
    def __init__(self, order):
        super().__init__()
        self.order=order

    def dispatch(self, request):
        self.order.append(('dispatch',request.target_state))
        return super().dispatch(request)


class ProgressNotificationTests(unittest.TestCase):
    def test_start_is_committed_and_sent_before_worker_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            order=[]
            app=OrchestratorApp(context(),directory,multica=OrderedScript(order),poll_interval=0,timeout_seconds=2)
            port=RecordingProgress(app,order)
            app.requirements_runtime.effects.port=port
            app.step()
            pending=app.repository.pending_effects(app.ctx.identity.task_id)
            self.assertEqual([e.request.effect_type for e in pending],['requirements_progress','agent_dispatch'])
            self.assertFalse(order)
            app.effects.execute_pending(app.ctx.identity.task_id)
            self.assertEqual(order[0][0],'notify')
            self.assertIn('任务已启动',order[0][1])
            self.assertEqual(order[1],('dispatch','REQ_ANALYZE'))

    def test_full_scripted_workflow_notifies_stages_wait_handoff_menxia_and_done(self):
        with tempfile.TemporaryDirectory() as directory:
            app=OrchestratorApp(context(),directory,multica=Scripted(),poll_interval=0,timeout_seconds=2)
            port=RecordingProgress(app)
            app.requirements_runtime.effects.port=port
            pump(app,'REQ_CONFIRM_WAIT')
            app.effects.execute_pending(app.ctx.identity.task_id)
            req=app.ctx.requirements
            app.requirements_runtime.submit('REQUIREMENTS_APPROVED',{'actor_id':'local-operator',
                'gate_id':req.review_gate_id,'document_digest':req.draft.document_digest},'approve-notification-test')
            pump(app,'DONE')
            app.effects.execute_pending(app.ctx.identity.task_id)
            text='\n'.join(body for _,body in port.requests)
            for expected in ('任务已启动','需求草案','需求评审','等待需求确认','验证需求批准',
                             '实施任务拆分','实施方案','实现证据','实施审查','任务已完成'):
                self.assertIn(expected,text)
            keys=[key for key,_ in port.requests]
            self.assertEqual(len(keys),len(set(keys)))
            results=[r for r,e in app.repository.list_effect_results(app.ctx.identity.task_id)
                     if e.request.effect_type=='requirements_progress']
            self.assertEqual(len(results),len(port.requests))
            self.assertTrue(all(r.event_name is None for r in results))
            self.assertTrue(all(r.event_payload['delivered'] for r in results))

    def test_retry_is_visible_with_reason_and_actual_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            app=OrchestratorApp(context(),directory,multica=SummaryThenJson(),poll_interval=0,timeout_seconds=2)
            port=RecordingProgress(app)
            app.requirements_runtime.effects.port=port
            pump(app,'REQ_CONFIRM_WAIT')
            text='\n'.join(body for _,body in port.requests)
            self.assertIn('正在重试',text)
            self.assertIn('AGENT_REPLY_UNSTRUCTURED',text)
            self.assertIn('格式修复：1/2',text)
            self.assertIn('重试后继续',text)

    def test_cancel_drains_committed_notices_and_restart_does_not_resend(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter=Scripted()
            app=OrchestratorApp(context(),directory,multica=adapter,poll_interval=0,timeout_seconds=2)
            port=RecordingProgress(app)
            app.requirements_runtime.effects.port=port
            app.step()
            self.assertTrue(app.cancel())
            self.assertEqual(app.ctx.progression.state,'REQ_CANCELLED')
            self.assertFalse(adapter.dispatched)
            self.assertEqual(len(port.requests),2)
            self.assertIn('任务已取消',port.requests[-1][1])
            restarted=OrchestratorApp(app.ctx,directory,multica=adapter,requirements_port=port,poll_interval=0,timeout_seconds=2)
            self.assertFalse(restarted.step())
            self.assertEqual(len(port.requests),2)

    def test_production_worker_port_is_restored_but_local_and_test_modes_stay_offline(self):
        with tempfile.TemporaryDirectory() as directory:
            ctx=context()
            ctx=replace(ctx,request=replace(ctx.request,communication='feishu'))
            with patch('orchestrator.app.feishu_notifications_enabled',return_value=True):
                app=OrchestratorApp(ctx,directory,multica=Scripted())
            self.assertIsInstance(app.notification_port,FeishuNotificationPort)
        with tempfile.TemporaryDirectory() as directory:
            with patch('orchestrator.app.feishu_notifications_enabled',return_value=True):
                self.assertIsInstance(OrchestratorApp(context(),directory,multica=Scripted()).notification_port,NullNotificationPort)
        with tempfile.TemporaryDirectory() as directory:
            with patch('orchestrator.app.feishu_notifications_enabled',return_value=False):
                self.assertIsInstance(OrchestratorApp(ctx,directory,multica=Scripted()).notification_port,NullNotificationPort)

    def test_feishu_progress_is_owner_independent_and_receipt_and_uuid_are_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            app=OrchestratorApp(context(),directory,multica=Scripted(),poll_interval=0,timeout_seconds=2)
            http=Mock(base_url='https://example.invalid')
            http._request.return_value={'code':0,'data':{'message_id':'test-message'}}
            port=FeishuRequirementsPort(app.root,app.requirements_runtime.store,chat_id='test-chat',http=http,publisher=Mock())
            app.requirements_runtime.effects.port=port
            app.step()
            with patch('orchestrator.adapters.feishu_notifications_enabled',return_value=True):
                app.effects.execute_pending(app.ctx.identity.task_id)
            http._request.assert_called_once()
            body=http._request.call_args.args[2]
            self.assertEqual(body['receive_id'],'test-chat')
            self.assertEqual(len(body['uuid']),32)
            result=next(r for r,e in app.repository.list_effect_results(app.ctx.identity.task_id)
                        if e.request.effect_type=='requirements_progress')
            self.assertEqual(result.event_payload['message_id'],'test-message')
            self.assertTrue(result.event_payload['delivered'])

    def test_delivery_failure_is_durable_and_does_not_restart_model_work(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter=Scripted()
            app=OrchestratorApp(context(),directory,multica=adapter,poll_interval=0,timeout_seconds=2)
            port=RecordingProgress(app)
            port.publish_progress=Mock(side_effect=ConnectionError('notification unavailable'))
            app.requirements_runtime.effects.port=port
            pump(app,'REQ_CONFIRM_WAIT')
            app.effects.execute_pending(app.ctx.identity.task_id)
            failures=[r for r,e in app.repository.list_effect_results(app.ctx.identity.task_id)
                      if e.request.effect_type=='requirements_progress']
            self.assertTrue(failures)
            self.assertTrue(all(r.status=='FAILED' and r.event_name is None for r in failures))
            self.assertEqual(len(adapter.dispatched),3)
            self.assertEqual(app.ctx.requirements.budget.dispatch_charges[0].format_repairs,0)

    def test_test_mode_persists_progress_artifact_without_http_or_tokens(self):
        with tempfile.TemporaryDirectory() as directory:
            app=OrchestratorApp(context(),directory,multica=Scripted(),poll_interval=0,timeout_seconds=2)
            http=Mock(base_url='https://example.invalid')
            app.requirements_runtime.effects.port=FeishuRequirementsPort(app.root,app.requirements_runtime.store,
                chat_id='test-chat',http=http,publisher=Mock())
            app.step()
            with patch.dict('os.environ',{'NEXUS_TEST_NO_EXTERNAL_NOTIFICATIONS':'1'}):
                app.effects.execute_pending(app.ctx.identity.task_id)
            http._request.assert_not_called()
            http._token.assert_not_called()
            files=list((app.root/'requirements'/'progress').glob('*.txt'))
            self.assertEqual(len(files),1)
            self.assertIn('任务已启动',files[0].read_text(encoding='utf-8'))
            result=next(r for r,e in app.repository.list_effect_results(app.ctx.identity.task_id)
                        if e.request.effect_type=='requirements_progress')
            self.assertFalse(result.event_payload['delivered'])

    def test_crash_after_send_before_result_commit_reuses_same_feishu_uuid(self):
        with tempfile.TemporaryDirectory() as directory:
            app=OrchestratorApp(context(),directory,multica=Scripted(),poll_interval=0,timeout_seconds=2)
            http=Mock(base_url='https://example.invalid')
            http._request.return_value={'code':0,'data':{'message_id':'test-message'}}
            port=FeishuRequirementsPort(app.root,app.requirements_runtime.store,chat_id='test-chat',http=http,publisher=Mock())
            app.requirements_runtime.effects.port=port
            app.step()
            with patch('orchestrator.adapters.feishu_notifications_enabled',return_value=True), \
                 patch.object(app.repository,'commit_effect_result',side_effect=OSError('simulated crash')):
                with self.assertRaises(OSError):
                    app.effects.execute_pending(app.ctx.identity.task_id)
            first_uuid=http._request.call_args.args[2]['uuid']
            restarted=OrchestratorApp(app.ctx,directory,multica=Scripted(),requirements_port=port,poll_interval=0,timeout_seconds=2)
            with patch('orchestrator.adapters.feishu_notifications_enabled',return_value=True):
                restarted.effects.execute_pending(app.ctx.identity.task_id)
            self.assertEqual(http._request.call_args.args[2]['uuid'],first_uuid)
            self.assertEqual(http._request.call_count,1)
            restarted.effects.execute_pending(app.ctx.identity.task_id)
            self.assertEqual(http._request.call_count,1)
