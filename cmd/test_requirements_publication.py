from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from orchestrator.domain.decisions import EffectRequest
from orchestrator.domain.requirements.model import ApprovalRecord, FactRecord, text_digest
from orchestrator.domain.requirements.document import render_markdown
from orchestrator.domain.requirements.review import merge_review
from orchestrator.runtime.requirements_effects import materialize, RequirementsEffects
from orchestrator.feishu_review_document import semantic_blocks
from requirements_test_support import make_state, complete_draft, review_payload


def reviewed():
    state=make_state()
    return merge_review(replace(state,draft=complete_draft(state)),review_payload())


def approved():
    state=reviewed()
    approval=ApprovalRecord('approval-000001',state.draft.document_digest,state.consensus.digest,
        state.review_stamp.input_digest,'local-operator','approval-event','now','local:'+state.draft.document_digest)
    return replace(state,approval=approval)


class PublicationTests(unittest.TestCase):
    def test_unapproved_draft_cannot_enter_approved_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                materialize(directory,reviewed(),True)
            self.assertFalse((Path(directory)/'requirements'/'approved').exists())

    def test_approved_bundle_is_replayable_and_corrupted_bytes_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            state=approved()
            folder=materialize(directory,state,True)
            self.assertEqual(materialize(directory,state,True),folder)
            self.assertEqual((folder/'requirements.md').read_bytes(),render_markdown(state.draft).encode('utf-8'))
            self.assertTrue((folder/'approval.json').is_file())
            self.assertTrue((folder/'manifest.json').is_file())
            (folder/'requirements.md').write_text('changed',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'IMMUTABLE'):
                materialize(directory,state,True)

    def test_strict_readback_preserves_structure_numbers_operators_and_code(self):
        original='# 标题\n\n不得自动重试 2 次。\n| 对象 | 数值 |\n|---|---|\n| 会话 | >= 2 |\n```python\nif a != 2:\n    connect()\n```'
        for changed in [original.replace('不得','可以'),original.replace('>=','>'),original.replace('!=','=='),
                        original.replace('# 标题','## 标题'),original.replace('    connect()','connect()')]:
            self.assertNotEqual(semantic_blocks(original),semantic_blocks(changed))
        self.assertEqual(semantic_blocks('# **标题**'),semantic_blocks('# 标题'))

    def test_fact_source_change_reopens_evidence_before_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'source.py'
            path.write_text('connections = 1',encoding='utf-8')
            state=make_state()
            fact=FactRecord('fact-000001','只建立一次连接',str(path),'connections = 1',text_digest('connections = 1'))
            state=replace(state,consensus=replace(state.consensus,facts=(fact,)))
            draft=complete_draft(state)
            sections=tuple(replace(s,fact_ids=(fact.fact_id,)) if s.kind=='behavior' else s for s in draft.sections)
            draft=replace(draft,sections=sections)
            draft=replace(draft,document_digest=text_digest(render_markdown(draft)))
            state=merge_review(replace(state,draft=draft),review_payload())
            path.write_text('connections = 2',encoding='utf-8')
            request=EffectRequest('check','requirements_approval_verify','task','check',payload={'state':state.to_dict()})
            outcome=RequirementsEffects(directory,object()).run_once(request)
            self.assertEqual(outcome.event_name,'FACTS_CHANGED')
            self.assertEqual(outcome.event_payload['changes'][0]['fact_id'],fact.fact_id)

    def test_tampered_approval_and_body_fail_on_restore(self):
        state=approved()
        from orchestrator.domain.requirements.model import RequirementsState
        dto=state.to_dict()
        dto['approval']['document_digest']='0'*64
        with self.assertRaises(ValueError):
            RequirementsState.from_dict(dto)
        dto=state.to_dict()
        dto['draft']['sections'][0]['markdown']='tampered'
        with self.assertRaises(ValueError):
            RequirementsState.from_dict(dto)

    def test_cloud_change_blocks_with_verifiable_source_basis(self):
        from unittest.mock import Mock,patch
        from orchestrator.requirements_gateway import GatewayStore
        from orchestrator.requirements_port import FeishuRequirementsPort
        from orchestrator.feishu_review_document import ReviewDocumentError
        with tempfile.TemporaryDirectory() as directory:
            publisher=Mock()
            fingerprint=text_digest('edited cloud text')
            publisher.verify.side_effect=ReviewDocumentError('PLAN_REVIEW_DOCUMENT_CHANGED',
                source_ref='cloud:2:doc',prerequisite_fingerprint=fingerprint)
            port=FeishuRequirementsPort(directory,GatewayStore(Path(directory)/'gateway.sqlite'),
                actor_ids=('owner',),chat_id='isolated',http=Mock(),publisher=publisher)
            request=EffectRequest('check','requirements_approval_verify','task','check',payload={'state':reviewed().to_dict()})
            with patch.object(port,'preflight'):
                outcome=RequirementsEffects(directory,port).run_once(request)
            self.assertEqual(outcome.event_name,'FAIL')
            self.assertEqual(outcome.event_payload['error_code'],'PREREQUISITE_MISSING')
            self.assertEqual(outcome.event_payload['prerequisite_fingerprint'],fingerprint)
            self.assertEqual(outcome.event_payload['recovery_source_ref'],'cloud:2:doc')

    def test_transient_document_cli_failure_is_retryable_not_blocked(self):
        from unittest.mock import Mock,patch
        from orchestrator.requirements_gateway import GatewayStore
        from orchestrator.requirements_port import FeishuRequirementsPort
        from orchestrator.feishu_review_document import ReviewDocumentError
        with tempfile.TemporaryDirectory() as directory:
            publisher=Mock()
            publisher.publish.side_effect=ReviewDocumentError(
                'FEISHU_DOCUMENT_CLI_FAILED exit=1: lark-cli-wrapper: broker token failed: '
                '{"error":{"type":"token_expired","message":"Access token expired and no refresh token is available"}}')
            port=FeishuRequirementsPort(directory,GatewayStore(Path(directory)/'gateway.sqlite'),
                actor_ids=('owner',),chat_id='isolated',http=Mock(),publisher=publisher)
            request=EffectRequest('check','requirements_review_document','task','check',
                payload={'state':reviewed().to_dict()})
            with patch.object(port,'preflight'):
                outcome=RequirementsEffects(directory,port).run_once(request)
            self.assertEqual(outcome.event_name,'FAIL')
            self.assertEqual(outcome.event_payload['error_code'],'COMMUNICATION_TRANSIENT')
            self.assertTrue(outcome.event_payload['retryable'])

    def test_document_cli_transient_markers_are_classified(self):
        from orchestrator.feishu_review_document import ReviewDocumentError
        transient=ReviewDocumentError('FEISHU_DOCUMENT_CLI_FAILED exit=1: broker token failed: token_expired')
        self.assertTrue(transient.transient)
        config=ReviewDocumentError('FEISHU_DOCUMENT_CLI_FAILED exit=1: missing scope docx:document')
        self.assertFalse(config.transient)
        self.assertFalse(ReviewDocumentError('PLAN_REVIEW_DOCUMENT_CHANGED').transient)


if __name__ == '__main__':
    unittest.main()
