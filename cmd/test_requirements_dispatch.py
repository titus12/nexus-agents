import unittest
from dataclasses import replace
from orchestrator.domain.requirements.dispatch import build_dispatch
from orchestrator.domain.requirements.model import CORE_SECTIONS
from orchestrator.domain.requirements.review import merge_review
from requirements_test_support import make_state,complete_draft,review_payload
from test_requirements_workflow_e2e import context


class DispatchTests(unittest.TestCase):
    def test_body_is_supplied_once_and_scoped_review_reuses_coverage(self):
        state=make_state()
        state=replace(state,draft=complete_draft(state))
        state=merge_review(state,review_payload())
        updated,effect=build_dispatch(context(),state,'REQ_REVIEW')
        payload=effect.payload['dispatch_context']
        self.assertEqual(payload['required_review_axes'],['consistency'])
        self.assertEqual(len(payload['reusable_coverage']),6)
        self.assertEqual(len(payload['sections']),len(CORE_SECTIONS))
        self.assertTrue(all('markdown' not in s for s in payload['sections']))
        self.assertNotIn('plan',payload)
        self.assertEqual(updated.budget.calls,1)
