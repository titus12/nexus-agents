"""The Analyst fan-out splits the Critic's open findings instead of repeating them."""
from __future__ import annotations
import unittest
from dataclasses import replace
from orchestrator.domain.context import ParallelState, ProgressState, RequestState, ReviewState, TaskIdentity, WorkflowContext, ZhongshuParallelLimits
from orchestrator.domain.findings import Finding
from orchestrator.domain.policies.analyst_partition import expected_analyst_lenses, partition_active_findings
REVISION = 'rev-1'
CANONICAL = ({'requirement_id': 'req-000001', 'statement': 'canonical statement', 'priority': 'must', 'scope': 'in', 'kind': 'task'},)

def _finding(number: int, **overrides: object) -> Finding:
    fields = {'finding_id': f'finding-{number:06d}', 'severity': 'P1', 'group_id': 'group-000001', 'item_id': 'item-000001', 'claim': f'claim {number}', 'evidence_targets': ({'path': f'src/mod_{number}.py', 'symbol': ''},)}
    fields.update(overrides)
    return Finding(**fields)

def _review(findings: tuple[Finding, ...]) -> ReviewState:
    return ReviewState(revision_id=REVISION, requirements=CANONICAL, findings=findings)

def _context(findings: tuple[Finding, ...], workers: int=3, requester: str='ZHONGSHU_CRITIC') -> WorkflowContext:
    review = replace(_review(findings), evidence_requester=requester)
    return WorkflowContext(identity=TaskIdentity('task-1', 'issue-1', '', 'request-1'), progression=ProgressState('ZHONGSHU_CRITIC', 4, '2026-09-30T00:00:00Z'), request=RequestState(raw_request='review'), parallel=ParallelState(zhongshu=ZhongshuParallelLimits(enabled=True, analyst_default_workers=workers)), review=review)

def _dispatch(context: WorkflowContext):
    return ZhongshuAnalystState._dispatch_effect(context, 'ZHONGSHU_ANALYST', revision_id=REVISION, plan_hash='plan-hash', canonical_requirements=CANONICAL)

def _ids(findings: tuple[Finding, ...]) -> list[str]:
    return [finding.finding_id for finding in findings]

class PartitionPolicyTests(unittest.TestCase):

    def test_round_robin_over_sorted_finding_ids(self) -> None:
        findings = tuple((_finding(number) for number in (5, 1, 4, 2, 3)))
        slices = partition_active_findings(_review(findings), 2)
        self.assertEqual({index: _ids(rows) for index, rows in slices.items()}, {1: ['finding-000001', 'finding-000003', 'finding-000005'], 2: ['finding-000002', 'finding-000004']})

    def test_every_open_finding_lands_in_exactly_one_slice(self) -> None:
        findings = tuple((_finding(number) for number in range(1, 8)))
        slices = partition_active_findings(_review(findings), 3)
        assigned = [fid for rows in slices.values() for fid in _ids(rows)]
        self.assertEqual(sorted(assigned), sorted(_ids(findings)))

    def test_workers_without_a_slice_are_left_out(self) -> None:
        slices = partition_active_findings(_review((_finding(1), _finding(2))), 3)
        self.assertEqual(sorted(slices), [1, 2])

    def test_resolved_findings_are_not_partitioned(self) -> None:
        resolved = _finding(1, status='RESOLVED')
        slices = partition_active_findings(_review((resolved, _finding(2))), 3)
        self.assertEqual({i: _ids(r) for i, r in slices.items()}, {1: ['finding-000002']})

    def test_nothing_to_split(self) -> None:
        self.assertEqual(partition_active_findings(None, 3), {})
        self.assertEqual(partition_active_findings(_review(()), 3), {})
        self.assertEqual(partition_active_findings(_review((_finding(1),)), 1), {})

    def test_partition_is_stable_across_calls(self) -> None:
        review = _review(tuple((_finding(number) for number in range(1, 6))))
        self.assertEqual(partition_active_findings(review, 3), partition_active_findings(review, 3))

    def test_expected_lenses_follow_the_dispatched_width(self) -> None:
        self.assertEqual(expected_analyst_lenses(3, iter(())), 3)
        self.assertEqual(expected_analyst_lenses(3, iter((1,))), 1)
        self.assertEqual(expected_analyst_lenses(3, iter((1, 2, 3, 4))), 3)
        self.assertEqual(expected_analyst_lenses(1, iter((1, 2))), 1)
if __name__ == '__main__':
    unittest.main()
