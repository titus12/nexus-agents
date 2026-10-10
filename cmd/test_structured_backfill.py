"""Only the current strict role protocol is accepted."""
import tempfile, unittest
from pathlib import Path
from orchestrator.contracts import contract_for_state
from orchestrator.agent_result_file import write_agent_result_file, AgentResultFileError
from orchestrator.structured_output import accepted_role_modes, fill_role_defaults
class StrictRoleResultTests(unittest.TestCase):
    def test_required_business_fields_are_not_backfilled(self):
        payload={'action':'DRAFT_READY'}
        result=fill_role_defaults(payload,phase='ZHONGSHU',role='review-solver',state='REQ_DRAFT')
        self.assertNotIn('document',result)
        self.assertTrue(contract_for_state('REQ_DRAFT').validate(result))
    def test_foreign_mode_is_rejected_before_writing(self):
        payload=contract_for_state('REQ_DRAFT').example_payload()
        payload['mode']='TASK_GRAPH_FORMALIZATION_READ_ONLY'
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(AgentResultFileError):
            write_agent_result_file(payload,target_path=Path(directory)/'result.json',task_id='t',request_id='r',phase='ZHONGSHU',role='review-solver',allowed_root=directory,expected_state='REQ_DRAFT',expected_role_mode='REQUIREMENTS_DOCUMENT')
    def test_declared_modes_are_exact(self):
        self.assertEqual(accepted_role_modes('REQUIREMENTS_DOCUMENT'),frozenset({'REQUIREMENTS_DOCUMENT'}))
