from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from orchestrator.lifecycle import LifecyclePaths, write_stage_result


class LifecycleResultIsolationTests(unittest.TestCase):
    def test_parallel_workers_have_isolated_result_files(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = LifecyclePaths(Path(directory) / "task-1")
            paths.ensure()
            for worker_id in ("analyst-1", "analyst-2", "analyst-3"):
                write_stage_result(
                    paths,
                    phase="ZHONGSHU",
                    revision="revision-1",
                    state="ZHONGSHU_ANALYST",
                    worker_id=worker_id,
                    payload={"worker_id": worker_id},
                )
            files = sorted(
                (paths.zhongshu / "revision-1" / "workers").glob("*/result.json")
            )
            self.assertEqual([item.parent.name for item in files], ["analyst-1", "analyst-2", "analyst-3"])


if __name__ == "__main__":
    unittest.main()
