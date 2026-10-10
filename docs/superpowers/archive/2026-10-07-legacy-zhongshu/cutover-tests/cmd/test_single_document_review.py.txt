from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from orchestrator.feishu_review_document import FeishuReviewDocumentPublisher, ReviewDocumentError, digest
from orchestrator.runtime.notification_effects import FeishuNotificationPort
from orchestrator.transport.external import DeliveryReceipt
from orchestrator.transport.external import HumanReply
from orchestrator.notifications import plan_review_notification_request
from test_human_document_loop import _gated_context, _drive, _HumanRevisionMultica
from test_plan_review_gate import _context, _freeze_event, _ReviewPort
from orchestrator.app import OrchestratorApp
from orchestrator.domain.states import StateRegistry


class CLI:
    def __init__(self, directory):
        self.directory = Path(directory) / 'requirements/feishu'
        self.calls = []
        self.content = ''
        self.unknown = False
        self.fetch_fail = False
        self.recovered = False

    def __call__(self, argv):
        self.calls.append(argv)
        if '+create' in argv:
            self.content = (self.directory / argv[-1][3:]).read_text(encoding='utf-8')
            if self.unknown:
                raise TimeoutError('created but response lost')
            return {'ok': True, 'data': {'document': {'document_id': 'doc1', 'url': 'https://example.feishu.cn/docx/doc1'}}}
        if '+search' in argv:
            title = argv[argv.index('--query') + 1]
            return {'data': {'results': [{'title': title, 'token': 'doc1', 'url': 'https://example.feishu.cn/docx/doc1'}]
                             if self.recovered else [], 'has_more': False}}
        if argv[0] == 'doc-task':
            if self.fetch_fail:
                raise RuntimeError('readback temporary failure')
            return {'steps': [{'result': {'data': {'document': {'document_id': 'doc1', 'content': self.content}}}}]}
        raise AssertionError(argv)


class Adapter:
    chat_id = 'chat-1'

    def __init__(self):
        self.messages = []
        self.fail = False
        self.replies = []

    def send_gate(self, gate):
        self.messages.append(gate.prompt)
        return DeliveryReceipt('feishu', '' if self.fail else 'om_one', not self.fail)

    def poll_reply(self, gate):
        return self.replies


class SingleDocumentTests(unittest.TestCase):
    def test_publish_retry_restart_and_notification_reuse(self):
        with tempfile.TemporaryDirectory() as directory:
            cli, adapter = CLI(directory), Adapter()
            publisher = FeishuReviewDocumentPublisher(directory, cli=cli)
            source = _context()
            decision = StateRegistry.default().get('ZHONGSHU_FREEZE_CHECK').handle(source, _freeze_event())
            context = replace(source, review=replace(source.review, plan_review=decision.update.review.plan_review),
                              progression=replace(source.progression, state='HUMAN_GATE', sequence=4),
                              human_gate=decision.update.human_gate)
            request = plan_review_notification_request(context)
            port = FeishuNotificationPort(adapter, publisher=publisher)
            adapter.fail = True
            self.assertFalse(port.send(request).delivered)
            adapter.fail = False
            restarted = FeishuNotificationPort(adapter, publisher=FeishuReviewDocumentPublisher(directory, cli=cli))
            self.assertTrue(restarted.send(request).delivered)
            self.assertTrue(restarted.send(request).delivered)
            self.assertEqual(sum('+create' in call for call in cli.calls), 1)
            self.assertIn('--title', next(call for call in cli.calls if '+create' in call))
            self.assertEqual(len(adapter.messages), 2)  # Failed first attempt, one successful delivery.
            self.assertIn('https://example.feishu.cn/docx/doc1', adapter.messages[-1])
            self.assertNotIn('分段', adapter.messages[-1])
            self.assertNotIn('分组需求正文', adapter.messages[-1])
            restarted.verify_plan_review_document(context)
            cli.content += '\n线上直接修改'
            with self.assertRaisesRegex(ReviewDocumentError, 'DOCUMENT_CHANGED'):
                restarted.verify_plan_review_document(context)

    def test_unknown_create_never_recreates_and_recovers_existing(self):
        with tempfile.TemporaryDirectory() as directory:
            cli = CLI(directory)
            cli.unknown = True
            publisher = FeishuReviewDocumentPublisher(directory, cli=cli)
            with self.assertRaises(TimeoutError):
                publisher.publish('task-1', 1, '# 正文\n完整内容', digest('# 正文\n完整内容'))
            with self.assertRaisesRegex(ReviewDocumentError, 'CREATE_OUTCOME_UNKNOWN'):
                publisher.publish('task-1', 1, '# 正文\n完整内容', digest('# 正文\n完整内容'))
            cli.recovered = True
            self.assertEqual(publisher.publish('task-1', 1, '# 正文\n完整内容', digest('# 正文\n完整内容'))['document_id'], 'doc1')
            self.assertEqual(sum('+create' in call for call in cli.calls), 1)

    def test_readback_failure_reuses_created_document(self):
        with tempfile.TemporaryDirectory() as directory:
            cli = CLI(directory)
            cli.fetch_fail = True
            publisher = FeishuReviewDocumentPublisher(directory, cli=cli)
            with self.assertRaises(RuntimeError):
                publisher.publish('task-1', 1, '正文', digest('正文'))
            cli.fetch_fail = False
            self.assertEqual(publisher.publish('task-1', 1, '正文', digest('正文'))['status'], 'VERIFIED')
            self.assertEqual(sum('+create' in call for call in cli.calls), 1)
            with self.assertRaisesRegex(ReviewDocumentError, 'VERSION_COLLISION'):
                publisher.publish('task-1', 1, '另一份正文', digest('另一份正文'))

    def test_incomplete_readback_prevents_notification(self):
        with tempfile.TemporaryDirectory() as directory:
            cli = CLI(directory)
            publisher = FeishuReviewDocumentPublisher(directory, cli=cli)
            cli.fetch_fail = True
            with self.assertRaises(RuntimeError):
                publisher.publish('task-1', 1, '开头\n重要中间段\n末尾', digest('开头\n重要中间段\n末尾'))
            cli.fetch_fail = False
            cli.content = cli.content.replace('重要中间段', '')
            with self.assertRaisesRegex(ReviewDocumentError, 'READBACK_MISMATCH'):
                publisher.publish('task-1', 1, '开头\n重要中间段\n末尾', digest('开头\n重要中间段\n末尾'))

    def test_real_cli_disabled_by_test_runner(self):
        with tempfile.TemporaryDirectory() as directory, patch('subprocess.run') as run:
            with self.assertRaisesRegex(ReviewDocumentError, 'EXTERNAL_DISABLED_FOR_TESTS'):
                FeishuReviewDocumentPublisher(directory)._run(['doc-task'])
            run.assert_not_called()

    def test_readback_does_not_drop_code_operators(self):
        with tempfile.TemporaryDirectory() as directory:
            cli = CLI(directory)
            publisher = FeishuReviewDocumentPublisher(directory, cli=cli)
            markdown = '# 条件\n`a ** b` 与 `x || y`\n```python\na ** b\n```'
            cli.fetch_fail = True
            with self.assertRaises(RuntimeError):
                publisher.publish('task-1', 1, markdown, digest(markdown))
            cli.fetch_fail = False
            cli.content = cli.content.replace('**', '').replace('||', '')
            with self.assertRaisesRegex(ReviewDocumentError, 'READBACK_MISMATCH'):
                publisher.publish('task-1', 1, markdown, digest(markdown))

    def test_all_feedback_rounds_offer_one_full_snapshot_and_no_critic(self):
        with tempfile.TemporaryDirectory() as directory:
            port, adapter = _ReviewPort(), _HumanRevisionMultica()
            app = OrchestratorApp(_gated_context(), root=directory, multica=adapter, notification_port=port,
                                  poll_interval=0, timeout_seconds=5)
            _drive(app)
            for answer in ['补充边界', '增加任务，补充 C 的核验']:
                count = len(adapter.dispatched)
                self.assertTrue(app.resume(answer))
                _drive(app)
                self.assertEqual([r.target_state for r in adapter.dispatched[count:]], ['ZHONGSHU_SOLVER'])
            context = app.repository.load(app.context.identity.task_id).context
            offers = [request for request in port.requests if request.document_markdown]
            self.assertEqual(len(offers), 3)
            self.assertEqual([r.document_version for r in offers], [1, 2, 3])
            self.assertEqual(offers[-1].document_markdown, context.review.plan_review['markdown'])
            self.assertTrue(all(len(r.body) < 1000 for r in offers))

    def test_failed_publication_and_cloud_edit_hold_pass_but_allow_feedback(self):
        class Port(_ReviewPort):
            ensure_ok = False
            changed = True

            def ensure_plan_review_delivery(self, context):
                return self.ensure_ok

            def verify_plan_review_document(self, context):
                if self.changed:
                    raise ReviewDocumentError('PLAN_REVIEW_DOCUMENT_CHANGED')

        with tempfile.TemporaryDirectory() as directory:
            port, adapter = Port(), _HumanRevisionMultica()
            app = OrchestratorApp(_gated_context(), root=directory, multica=adapter, notification_port=port,
                                  poll_interval=0, timeout_seconds=5)
            context = _drive(app)
            port.replies = [HumanReply(context.human_gate.decision_id, '通过')]
            app._poll_human_gate_reply(context.identity.task_id)
            self.assertIsNone(app.inbox.next(context.identity.task_id))
            port.ensure_ok = True
            app._last_gate_poll = 0
            app._poll_human_gate_reply(context.identity.task_id)
            self.assertIsNone(app.inbox.next(context.identity.task_id))
            self.assertFalse(app.resume('通过'))
            self.assertTrue(app.resume('请补充边界'))
            before = len(adapter.dispatched)
            port.replies = []
            _drive(app)
            self.assertEqual([r.target_state for r in adapter.dispatched[before:]], ['ZHONGSHU_SOLVER'])


if __name__ == '__main__':
    unittest.main()
