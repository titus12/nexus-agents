import unittest
from dataclasses import FrozenInstanceError, replace

from orchestrator.domain.context import (WorkflowContext, TaskIdentity, ProgressState,
                                         context_to_dto, context_from_dto)
from orchestrator.domain.requirements.model import RequirementsState, SourceRecord


class RequirementsModelTests(unittest.TestCase):
    def test_old_snapshot_remains_legacy(self):
        context = WorkflowContext(TaskIdentity('model', '', '', 'request-model'),
                                  ProgressState('REQUEST_INTAKE', 0, 'now'))
        dto = context_to_dto(context)
        dto['request'].pop('workflow', None)
        dto.pop('requirements', None)
        dto['schema_version'] = '4.0'
        restored = context_from_dto(dto)
        self.assertEqual(restored.request.workflow, 'legacy-v1')
        self.assertIsNone(restored.requirements)

    def test_requirements_round_trip_is_immutable(self):
        state = RequirementsState.empty(SourceRecord('src-000001', 'request', '登录去重'))
        self.assertEqual(RequirementsState.from_dict(state.to_dict()), state)
        with self.assertRaises(FrozenInstanceError):
            state.reason = 'changed'
        dto = state.to_dict()
        dto['sources'][0]['text'] = 'changed'
        self.assertEqual(state.sources[0].text, '登录去重')

    def test_strict_dto_rejects_unknown_and_bad_types(self):
        state = RequirementsState.empty(SourceRecord('src-000001', 'request', 'request'))
        dto = state.to_dict()
        dto['budget']['calls'] = True
        with self.assertRaises(ValueError):
            RequirementsState.from_dict(dto)
        dto = state.to_dict()
        dto['budget']['made_up'] = 1
        with self.assertRaises(ValueError):
            RequirementsState.from_dict(dto)
        with self.assertRaises(ValueError):
            replace(state, sources=state.sources + state.sources)

    def test_future_schema_and_unknown_workflow_rejected(self):
        context = WorkflowContext(TaskIdentity('model', '', '', 'request-model'),
                                  ProgressState('REQUEST_INTAKE', 0, 'now'))
        dto = context_to_dto(context)
        dto['schema_version'] = '99.0'
        with self.assertRaises(ValueError):
            context_from_dto(dto)
        dto['schema_version'] = '5.0'
        dto['request']['workflow'] = 'imaginary'
        with self.assertRaises(ValueError):
            context_from_dto(dto)

    def test_missing_persisted_budget_cannot_silently_reset_work(self):
        state=RequirementsState.empty(SourceRecord('src-000001','request','request'))
        dto=state.to_dict()
        dto['budget'].pop('calls')
        with self.assertRaisesRegex(ValueError,'missing persisted'):
            RequirementsState.from_dict(dto)
        dto=state.to_dict()
        dto['budget'].pop('policy_version')
        with self.assertRaisesRegex(ValueError,'missing persisted'):
            RequirementsState.from_dict(dto)
