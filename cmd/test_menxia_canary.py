from __future__ import annotations

import unittest
from orchestrator.menxia_parallel import CoordinatorResult, MenxiaParallelConfig, MenxiaParallelCoordinator, RuntimeUpdate


class CanaryTests(unittest.TestCase):
    def test_two_groups_three_items_with_bounded_concurrency(self):
        plan = {
            'plan_revision_id': 'canary-rev-1',
            'groups': [
                {'group_id': 'group-1', 'items': [
                    {'item_id': 'item-1'}, {'item_id': 'item-2'}, {'item_id': 'item-3'},
                ]},
                {'group_id': 'group-2', 'dependencies': ['group-1'], 'items': [
                    {'item_id': 'item-4'}, {'item_id': 'item-5'}, {'item_id': 'item-6'},
                ]},
            ],
        }
        dispatched = []
        active_samples = []
        coordinator = None

        def dispatch(runtime):
            dispatched.append((runtime.group_id, runtime.item_id, runtime.request_id))

        def poll(runtime):
            active_samples.append(sum(item.execution_status in {'RUNNING', 'WAITING_REPLY', 'RETRYING'} for item in coordinator.runtimes.values()))
            return RuntimeUpdate(
                task_id='canary-task',
                group_id=runtime.group_id,
                item_id=runtime.item_id,
                plan_revision_id=runtime.plan_revision_id,
                request_id=runtime.request_id,
                state=runtime.state,
                event='APPROVE_ITEM',
                payload={'proposal': {'item_id': runtime.item_id}},
                observed_at='2026-09-01T00:00:00Z',
            )

        coordinator = MenxiaParallelCoordinator(
            'canary-task',
            plan,
            MenxiaParallelConfig(enabled=True, max_concurrent_groups=1, max_concurrent_items=2),
            dispatch=dispatch,
            poll=poll,
        )
        for _ in range(10):
            result = coordinator.tick()
            if result == CoordinatorResult.FAN_IN_READY:
                break
        self.assertTrue(coordinator.is_complete())
        self.assertEqual(coordinator.group_statuses['group-1'], 'APPROVED')
        self.assertEqual(coordinator.group_statuses['group-2'], 'APPROVED')
        self.assertLessEqual(max(active_samples), 2)
        self.assertEqual([item[1] for item in dispatched], ['item-1', 'item-2', 'item-3', 'item-4', 'item-5', 'item-6'])
        self.assertEqual([item['item_id'] for item in coordinator.snapshot()['runtimes']], ['item-1', 'item-2', 'item-3', 'item-4', 'item-5', 'item-6'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
