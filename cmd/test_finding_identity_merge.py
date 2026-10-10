from __future__ import annotations
import unittest
from orchestrator.domain.context import ProgressState, ReviewState, TaskIdentity, WorkflowContext
from orchestrator.domain.findings import Finding
from orchestrator.domain.states import _ConcreteWorkflowState
_SHORT_ID = 'finding-2bf907421ff768fa8d9e'
_FULL_ID = 'finding-2bf907421ff768fa8d9e4d066dec21c7af06b6b829a66471e922174dd36e03f4'

def _finding(finding_id: str, *, severity: str='P1', status: str='OPEN', stuck_rounds: int=0, canonical_key: str='', item_id: str='item-000001', group_id: str='group-000001') -> Finding:
    return Finding(finding_id=finding_id, severity=severity, status=status, group_id=group_id, item_id=item_id, claim='blocking claim', required_action='fix it', stuck_rounds=stuck_rounds, canonical_key=canonical_key)

class MergeFindingsIdentityTests(unittest.TestCase):
    """One opinion must occupy one ledger entry regardless of its id form."""

    def test_same_canonical_key_under_different_ids_collapses(self) -> None:
        stored = _finding(_FULL_ID, canonical_key='abc123', stuck_rounds=2)
        re_raised = _finding(_SHORT_ID, canonical_key='abc123', status='DEFERRED')
        merged = merge_findings((stored,), (re_raised,))
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].finding_id, _SHORT_ID)
        self.assertEqual(merged[0].status, 'DEFERRED')

    def test_re_raise_under_twin_id_does_not_duplicate_the_entry(self) -> None:
        stored = _finding(_SHORT_ID, canonical_key='abc123')
        twin = _finding(_FULL_ID, canonical_key='abc123')
        merged = merge_findings((stored, twin), ())
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].finding_id, _FULL_ID)

    def test_findings_without_canonical_key_keep_structural_identity(self) -> None:
        first = _finding('f-1', canonical_key='', item_id='item-000001')
        second = _finding('f-1', canonical_key='', item_id='item-000002')
        merged = merge_findings((first,), (second,))
        self.assertEqual(len(merged), 2)
if __name__ == '__main__':
    unittest.main()

from orchestrator.domain.policies.review_findings import merge_findings
