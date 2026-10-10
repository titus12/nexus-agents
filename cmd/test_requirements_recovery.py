from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from orchestrator.domain.requirements.model import RequirementsFailure,RequirementsRecovery,text_digest
from orchestrator.runtime.requirements_recovery import verified_recovery
from orchestrator.requirements_port import card_binding_present
from requirements_test_support import make_state


class RecoveryTests(unittest.TestCase):
    def test_changed_evidence_must_match_recorded_source_and_fingerprint(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'source.txt'
            path.write_text('fixed',encoding='utf-8')
            failure=RequirementsFailure('SOURCE_UNAVAILABLE','REQ_EVIDENCE','analyst','source unavailable',
                str(path),text_digest('old'))
            state=replace(make_state(),recovery=RequirementsRecovery('REQ_EVIDENCE','REQ_EVIDENCE','dispatch','',failure))
            note={'kind':'prerequisite_changed','source_ref':str(path),
                'expected_old_fingerprint':text_digest('old'),'new_fingerprint':text_digest('fixed')}
            verified=verified_recovery(SimpleNamespace(),state,note)
            self.assertTrue(verified['_runtime_verified'])
            note['expected_old_fingerprint']='0'*64
            with self.assertRaisesRegex(ValueError,'RECORDED_FAILURE'):
                verified_recovery(SimpleNamespace(),state,note)
            note['expected_old_fingerprint']=text_digest('old')
            note['new_fingerprint']='0'*64
            with self.assertRaisesRegex(ValueError,'VERIFIABLY_CHANGED'):
                verified_recovery(SimpleNamespace(),state,note)

    def test_card_receipt_binds_exact_page_and_gate_not_token_substring(self):
        import json
        gate={'gate_id':'gate','token':'opaque','verification_page_id':'page-2'}
        card={'value':{'gate_id':'gate','token':'opaque','page_id':'page-2'}}
        self.assertTrue(card_binding_present(json.dumps(card),gate))
        card['value']['page_id']='page-1'
        self.assertFalse(card_binding_present(json.dumps(card),gate))
        self.assertFalse(card_binding_present('opaque',gate))
