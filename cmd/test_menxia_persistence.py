from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
from orchestrator.context import StateContext
from orchestrator.persistence import JsonStateStore, PersistenceConflict

class MenxiaPersistenceTests(unittest.TestCase):
    def test_state_version_increments_and_expected_version_is_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            store = JsonStateStore(Path(directory))
            ctx = StateContext(task_id='task-1')
            store.save_state(ctx)
            self.assertEqual(ctx.state_version, 1)
            store.save_state(ctx, expected_version=1)
            self.assertEqual(ctx.state_version, 2)
            with self.assertRaises(PersistenceConflict):
                store.save_state(ctx, expected_version=1)

    def test_menxia_parallel_round_trips(self):
        with tempfile.TemporaryDirectory() as directory:
            store = JsonStateStore(Path(directory))
            ctx = StateContext(task_id='task-1', menxia_parallel={'runtimes': [{'item_id': 'i1'}]})
            store.save_state(ctx)
            loaded = store.load()
            self.assertEqual(loaded.menxia_parallel, {'runtimes': [{'item_id': 'i1'}]})
            self.assertEqual(loaded.state_version, 1)

    def test_zhongshu_parallel_defaults_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            store = JsonStateStore(Path(directory))
            ctx = StateContext(task_id='task-zhongshu')
            self.assertEqual(ctx.zhongshu_parallel['analyst_max_workers'], 3)
            self.assertEqual(ctx.zhongshu_parallel['critic_default_workers'], 6)
            store.save_state(ctx)
            loaded = store.load()
            self.assertEqual(loaded.zhongshu_parallel['global_max_workers'], 6)

if __name__ == '__main__':
    unittest.main()
