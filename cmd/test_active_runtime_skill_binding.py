from __future__ import annotations

import unittest

from orchestrator.runtime.compat_effects import _skill_lock


class ActiveRuntimeSkillBindingTests(unittest.TestCase):
    def test_zhongshu_analyst_skill_lock_resolves_from_repository_root(self) -> None:
        lock = _skill_lock("ZHONGSHU_ANALYST")

        self.assertEqual(lock.get("name"), "zhongshu-analyst")
        self.assertEqual(lock.get("status"), "locked")
        self.assertTrue(lock.get("source", "").endswith(
            "docs\\multi\\runtime\\zhongshu-analyst-skill.md"
        ))
        self.assertTrue(lock.get("sha256"))
        self.assertGreater(lock.get("bytes", 0), 0)


if __name__ == "__main__":
    unittest.main()
