from __future__ import annotations

import unittest

from orchestrator.domain.states import _resolve_plan_hash


class PlanHashResolutionTests(unittest.TestCase):
    def test_explicit_hash_wins(self) -> None:
        self.assertEqual(
            _resolve_plan_hash({"plan_hash": "abc", "plan": {"items": [{"item_id": "i"}]}}),
            "abc",
        )
        self.assertEqual(_resolve_plan_hash({"reviewed_plan_hash": "def"}), "def")

    def test_hash_computed_from_plan_items(self) -> None:
        plan = {
            "items": [{"item_id": "item-000001", "title": "t"}],
            "groups": [{"group_id": "group-000001", "item_ids": ["item-000001"]}],
        }
        resolved = _resolve_plan_hash({"plan": plan})
        self.assertIsNotNone(resolved)
        self.assertEqual(len(str(resolved)), 64)

    def test_missing_plan_returns_none(self) -> None:
        self.assertIsNone(_resolve_plan_hash({}))
        self.assertIsNone(_resolve_plan_hash({"plan": {"items": []}}))
        self.assertIsNone(_resolve_plan_hash({"plan": "not-a-dict"}))


if __name__ == "__main__":
    unittest.main()
