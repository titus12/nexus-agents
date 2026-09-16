from __future__ import annotations

import unittest

from orchestrator.zhongshu_review_queue import build_review_jobs, review_surface


def _item(item_id: str, *, objective: str = "Do one", group_id: str = "group-1") -> dict:
    return {
        "item_id": item_id,
        "group_id": group_id,
        "title": item_id,
        "objective": objective,
        "source_requirement_ids": ["req-1"],
        "dependencies": [],
        "acceptance_signals": ["observable"],
        "unknowns": [],
        "risks": [],
        "parallelizable": True,
        "rationale": "initial rationale",
    }


def _plan(items: list[dict]) -> dict:
    return {
        "items": items,
        "groups": [{"group_id": "group-1", "item_ids": [i["item_id"] for i in items]}],
    }


def _task_hash(plan: dict, item_id: str) -> str:
    queue = build_review_jobs(plan, "rev-1", plan_hash="ph")
    return next(job.task_hash for job in queue.jobs if job.item_id == item_id)


class ReviewSurfaceHashTests(unittest.TestCase):
    def test_solver_prose_does_not_change_the_task_hash(self) -> None:
        base = _plan([_item("item-1")])
        revised = _plan([_item("item-1")])
        revised["items"][0]["rationale"] = "completely rewritten justification"
        revised["items"][0]["benefit"] = "new benefit"
        revised["items"][0]["tradeoffs"] = "new tradeoff"
        revised["items"][0]["risks"] = ["new risk"]

        self.assertEqual(_task_hash(base, "item-1"), _task_hash(revised, "item-1"))

    def test_reviewed_fields_change_the_task_hash(self) -> None:
        base = _plan([_item("item-1")])

        for field, value in (
            ("objective", "Do one differently"),
            ("title", "renamed"),
            ("acceptance_signals", ["observable", "second signal"]),
            ("dependencies", ["item-2"]),
            ("source_requirement_ids", ["req-2"]),
        ):
            revised = _plan([_item("item-1")])
            revised["items"][0][field] = value
            with self.subTest(field=field):
                self.assertNotEqual(
                    _task_hash(base, "item-1"), _task_hash(revised, "item-1")
                )

    def test_whitespace_only_edits_do_not_change_the_task_hash(self) -> None:
        base = _plan([_item("item-1", objective="Do   one")])
        revised = _plan([_item("item-1", objective=" Do one ")])

        self.assertEqual(_task_hash(base, "item-1"), _task_hash(revised, "item-1"))

    def test_group_membership_change_changes_the_task_hash(self) -> None:
        base = _plan([_item("item-1"), _item("item-2")])
        revised = _plan([_item("item-1"), _item("item-3")])

        self.assertNotEqual(_task_hash(base, "item-1"), _task_hash(revised, "item-1"))

    def test_review_surface_ignores_volatile_keys(self) -> None:
        item = _item("item-1")
        item["rationale"] = "noise"
        surface = review_surface(item, group_item_ids=["item-1"])

        self.assertNotIn("rationale", surface)
        self.assertEqual(surface["objective"], "Do one")


if __name__ == "__main__":
    unittest.main()
