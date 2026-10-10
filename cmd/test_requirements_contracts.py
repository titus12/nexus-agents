import json
from pathlib import Path
import tempfile
import unittest
from orchestrator.contracts import contract_for_state
import hashlib
from orchestrator.agent_result_file import write_agent_result_file, AgentResultFileError


class ContractTests(unittest.TestCase):
    def test_record_enums_are_exposed_and_strict(self):
        from orchestrator.contracts.requirements_common import record_schema, strict_schema
        from orchestrator.domain.requirements import model
        for name, enum_fields in model.RECORD_ENUMS.items():
            schema = strict_schema(record_schema(getattr(model,name)))
            for field, allowed in enum_fields.items():
                with self.subTest(record=name,field=field):
                    field_schema = schema['properties'][field]
                    self.assertEqual(field_schema['enum'],list(allowed))
                    self.assertTrue(field_schema['strictEnum'])

    def test_reported_invalid_values_are_rejected_at_result_boundary(self):
        contract = contract_for_state('REQ_ANALYZE')
        cases = [('claims','kind','functional'),('claims','origin','user'),
                 ('claims','status','draft'),('facts','status','verified'),
                 ('assumptions','disposition','probable')]
        rows = {'claims':{'topic_key':'t','statement':'s'},
                'facts':{'observation':'o','source':'file','quote':'q'},
                'assumptions':{'statement':'s'}}
        for collection, field, invalid in cases:
            with self.subTest(collection=collection,field=field):
                payload = contract.example_payload()
                payload['analysis'][collection] = [{**rows[collection],field:invalid}]
                errors = contract.validate(payload)
                self.assertTrue(errors)
                self.assertTrue(any('expected one of' in error and invalid in error for error in errors))
        payload = contract.example_payload()
        payload['questions'] = [{'topic_key':'t','question':'q','reason':'r','impact':'i',
                                  'answer_kind':'single_choice'}]
        self.assertTrue(contract.validate(payload))

    def test_result_file_does_not_coerce_requirement_source_identity(self):
        contract = contract_for_state('REQ_ANALYZE')
        payload = contract.example_payload()
        payload['analysis']['claims'] = [{'topic_key':'t','statement':'s',
            'source_ids':[1], 'quote':'s'}]
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(AgentResultFileError):
            write_agent_result_file(payload, target_path=Path(directory)/'result.json',
                task_id='t', request_id='r', phase='ZHONGSHU', role='review-analyst',
                expected_state='REQ_ANALYZE', expected_schema_hash='a'*64,
                expected_role_mode='REQUIREMENTS_DOCUMENT', allowed_root=directory)

    def test_exact_contracts_reject_authority_fields_and_coercion(self):
        for state in ('REQ_ANALYZE','REQ_EVIDENCE','REQ_DRAFT','REQ_REVIEW','MENXIA_REQUIREMENTS_INTAKE'):
            with self.subTest(state=state):
                contract=contract_for_state(state,workflow='requirements-v1')
                payload=contract.example_payload()
                self.assertEqual(contract.validate(payload),())
                payload['approval']={}
                self.assertTrue(contract.validate(payload))
        contract=contract_for_state('REQ_ANALYZE',workflow='requirements-v1')
        payload=contract.example_payload()
        payload['analysis']['assumptions']=[{'statement':'automatic retry','blocking':'false'}]
        self.assertTrue(contract.validate(payload))

    def test_real_result_file_uses_workflow_specific_menxia_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            state='MENXIA_GROUP_SOLVER'
            contract=contract_for_state(state,workflow='requirements-v1')
            payload=contract.example_payload()
            payload['action']='REQUIREMENTS_CHANGE_REQUESTED'
            payload['structured_output_schema_hash']=hashlib.sha256(json.dumps(contract.schema,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
            result=write_agent_result_file(payload,target_path=Path(directory)/'result.json',task_id='t',request_id='r',
                phase='MENXIA',role='review-solver',allowed_root=directory,
                expected_state=state,expected_role_mode=payload['mode'],expected_schema_hash=payload['structured_output_schema_hash'],
                workflow='requirements-v1')
            self.assertEqual(json.loads(result.path.read_text())['action'],'REQUIREMENTS_CHANGE_REQUESTED')
            with self.assertRaises(AgentResultFileError):
                write_agent_result_file(payload,target_path=Path(directory)/'legacy.json',task_id='t',request_id='r',
                    phase='MENXIA',role='review-solver',allowed_root=directory,expected_state=state,
                    expected_role_mode=payload['mode'],expected_schema_hash=payload['structured_output_schema_hash'],workflow='legacy')
