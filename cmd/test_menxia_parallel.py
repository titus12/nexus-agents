from __future__ import annotations
import unittest
from orchestrator.menxia_parallel import CoordinatorResult, MenxiaParallelConfig, MenxiaParallelCoordinator, RuntimeUpdate

class MenxiaParallelTests(unittest.TestCase):
    def plan(self):
        return {'plan_revision_id': 'rev-1', 'groups': [
            {'group_id': 'g1', 'items': [{'item_id': 'i1'}, {'item_id': 'i2'}]},
            {'group_id': 'g2', 'dependencies': ['g1'], 'items': [{'item_id': 'i3'}, {'item_id': 'i4'}]},
        ]}

    def test_global_item_limit_is_enforced(self):
        started = []
        c = MenxiaParallelCoordinator('t1', self.plan(), MenxiaParallelConfig(enabled=True, max_concurrent_groups=2, max_concurrent_items=2), dispatch=lambda r: started.append(r.item_id))
        result = c.tick()
        self.assertEqual(result, CoordinatorResult.WAITING)
        self.assertEqual(started, ['i1', 'i2'])
        self.assertLessEqual(sum(r.execution_status in {'RUNNING', 'WAITING_REPLY', 'RETRYING'} for r in c.runtimes.values()), 2)

    def test_group_dependency_blocks_dependent_group(self):
        c = MenxiaParallelCoordinator('t1', self.plan(), MenxiaParallelConfig(enabled=True, max_concurrent_groups=2, max_concurrent_items=4))
        c.tick()
        self.assertEqual(c.group_statuses['g1'], 'RUNNING')
        self.assertEqual(c.group_statuses['g2'], 'PENDING')

    def test_stale_update_does_not_change_runtime(self):
        c = MenxiaParallelCoordinator('t1', self.plan(), MenxiaParallelConfig(enabled=True, max_concurrent_groups=1, max_concurrent_items=1))
        c.tick()
        r = c.runtimes[('g1', 'i1')]
        before = r.execution_status
        c.merge_update(RuntimeUpdate('t1', 'g1', 'i1', 'rev-1', 'old-request', r.state, 'APPROVE_ITEM', {}))
        self.assertEqual(r.execution_status, before)
        self.assertIsNone(r.outcome)

    def test_approved_item_advances_to_next_item_without_duplicate_request(self):
        c = MenxiaParallelCoordinator('t1', self.plan(), MenxiaParallelConfig(enabled=True, max_concurrent_groups=1, max_concurrent_items=1))
        c.tick()
        r = c.runtimes[('g1', 'i1')]
        old_request = r.request_id
        c.merge_update(RuntimeUpdate('t1', 'g1', 'i1', 'rev-1', old_request, r.state, 'APPROVE_ITEM', {'proposal': {'ok': True}}))
        c.tick()
        self.assertEqual(r.outcome, 'APPROVED')
        self.assertEqual(c.runtimes[('g1', 'i2')].execution_status, 'WAITING_REPLY')
        self.assertNotEqual(c.runtimes[('g1', 'i2')].request_id, old_request)

if __name__ == '__main__':
    unittest.main()
