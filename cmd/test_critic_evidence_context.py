from __future__ import annotations

import unittest

from orchestrator.domain.context import (
    context_from_dto,
    context_to_dto,
    ParallelState,
    ProgressState,
    ReviewState,
    ReviewTaskGroup,
    ReviewTaskItem,
    TaskIdentity,
    WorkflowContext,
)
from orchestrator.domain.findings import Finding
from orchestrator.domain.states import (
    _item_evidence_context,
    _ConcreteWorkflowState,
    _task_review_bindings,
)


def _packet(
    *,
    workers: int = 2,
    extra_updates: tuple[dict, ...] = (),
) -> dict[str, object]:
    updates = [
        {
            "evidence_id": "ev-001",
            "requirement_id": "req-000001",
            "item_id": "item-000001",
            "decision_relevance": "acceptance",
            "conclusion": "item-000001 acceptance is verifiable from code",
            "source": "cmd/orchestrator/domain/states.py",
        },
        {
            "evidence_id": "ev-002",
            "requirement_id": "req-000001",
            "item_id": "item-000002",
            "decision_relevance": "coverage",
            "conclusion": "item-000002 has no runtime consumer",
            "source": "cmd/orchestrator/domain/context.py",
        },
        {
            "evidence_id": "ev-003",
            "requirement_id": "req-000001",
            "decision_relevance": "boundary",
            "conclusion": "requirement-level fact without an item scope",
            "source": "docs/design.md",
        },
        {
            "evidence_id": "ev-004",
            "requirement_id": "req-000009",
            "decision_relevance": "risk",
            "conclusion": "unrelated requirement, must not match",
            "source": "docs/other.md",
        },
        *extra_updates,
    ]
    return {
        "evidence_updates": updates,
        "worker_evidence": {
            f"zhongshu_analyst-worker-{index:02d}": {"evidence_updates": []}
            for index in range(1, workers + 1)
        },
    }


def _review(evidence_packet: dict[str, object] | None) -> ReviewState:
    return ReviewState(
        revision_id="task-1:2",
        findings=(
            Finding(
                finding_id="finding-000001",
                severity="P1",
                group_id="group-000001",
                item_id="item-000001",
                claim="claim",
            ),
        ),
        task_items=(
            ReviewTaskItem(
                "item-000001",
                "group-000001",
                source_requirement_ids=("req-000001",),
            ),
            ReviewTaskItem(
                "item-000002",
                "group-000001",
                source_requirement_ids=("req-000001",),
            ),
        ),
        task_groups=(
            ReviewTaskGroup("group-000001", ("item-000001", "item-000002")),
        ),
        evidence_packet=evidence_packet,
    )


def _context(review: ReviewState) -> WorkflowContext:
    return WorkflowContext(
        identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
        progression=ProgressState("ZHONGSHU_CRITIC", 6, "2026-09-17T00:00:00Z"),
        parallel=ParallelState(),
        review=review,
    )


class ItemEvidenceContextTests(unittest.TestCase):
    """The task-review Critic must see the evidence the run already has."""

    def test_item_and_requirement_scoped_records_are_matched(self) -> None:
        review = _review(_packet())
        item = review.task_items[0]

        context = _item_evidence_context(review, item, expected_lenses=3)

        ids = [record["evidence_id"] for record in context["records"]]
        self.assertEqual(ids, ["ev-001", "ev-003"])
        self.assertEqual(context["lens_workers"], [
            "zhongshu_analyst-worker-01",
            "zhongshu_analyst-worker-02",
        ])
        self.assertEqual(context["lens_gaps"], 1)

    def test_records_for_other_items_and_requirements_are_excluded(self) -> None:
        review = _review(_packet())
        item = review.task_items[1]

        context = _item_evidence_context(review, item, expected_lenses=2)

        ids = [record["evidence_id"] for record in context["records"]]
        self.assertEqual(ids, ["ev-002", "ev-003"])
        self.assertEqual(context["lens_gaps"], 0)

    def test_record_count_is_bounded(self) -> None:
        extra = tuple(
            {
                "evidence_id": f"ev-{index:03d}",
                "requirement_id": "req-000001",
                "item_id": "item-000001",
                "conclusion": f"record {index}",
            }
            for index in range(100, 120)
        )
        review = _review(_packet(extra_updates=extra))
        item = review.task_items[0]

        context = _item_evidence_context(review, item, expected_lenses=3)

        self.assertEqual(len(context["records"]), 8)

    def test_long_text_is_trimmed(self) -> None:
        packet = _packet(
            extra_updates=(
                {
                    "evidence_id": "ev-long",
                    "requirement_id": "req-000001",
                    "item_id": "item-000001",
                    "conclusion": "x" * 900,
                    "source": "y" * 900,
                },
            )
        )
        review = _review(packet)
        item = review.task_items[0]

        context = _item_evidence_context(review, item, expected_lenses=3)

        record = next(
            record
            for record in context["records"]
            if record["evidence_id"] == "ev-long"
        )
        self.assertEqual(record["conclusion"], "x" * 400)
        self.assertEqual(record["source"], "y" * 200)

    def test_missing_packet_yields_empty_context(self) -> None:
        review = _review(None)

        context = _item_evidence_context(review, review.task_items[0], 3)

        self.assertEqual(context["records"], [])
        self.assertEqual(context["lens_workers"], [])
        self.assertEqual(context["lens_gaps"], 0)


class TaskReviewBindingEvidenceTests(unittest.TestCase):
    """Bindings carry the item evidence slice and the coverage marker."""

    def test_bindings_carry_item_evidence_and_gap_marker(self) -> None:
        context = _context(_review(_packet(workers=1)))

        bindings = _task_review_bindings(context, "task-1:2", "plan-hash")

        self.assertEqual(len(bindings), 2)
        first = bindings[0]["dispatch_context"]
        self.assertEqual(
            [record["evidence_id"] for record in first["item_evidence"]],
            ["ev-001", "ev-003"],
        )
        self.assertIn("evidence_gaps", first)
        self.assertIn("expected 3", first["evidence_gaps"])
        self.assertIn(
            "dispatch_context.item_evidence", bindings[0]["prompt_ref"]
        )

    def test_no_gap_marker_when_every_lens_delivered(self) -> None:
        context = _context(_review(_packet(workers=3)))

        bindings = _task_review_bindings(context, "task-1:2", "plan-hash")

        self.assertNotIn("evidence_gaps", bindings[0]["dispatch_context"])

    def test_without_packet_the_binding_still_dispatches(self) -> None:
        context = _context(_review(None))

        bindings = _task_review_bindings(context, "task-1:2", "plan-hash")

        self.assertEqual(len(bindings), 2)
        self.assertEqual(bindings[0]["dispatch_context"]["item_evidence"], [])
        self.assertNotIn("evidence_gaps", bindings[0]["dispatch_context"])
        self.assertNotIn("dispatch_context.item_evidence", bindings[0]["prompt_ref"])


class EvidencePacketPersistenceTests(unittest.TestCase):
    """The packet folds into the review aggregate and survives a snapshot."""

    def test_analyst_payload_stores_the_packet(self) -> None:
        context = _context(_review(None))
        packet = _packet()

        update = _ConcreteWorkflowState._review_update(
            context,
            {"plan": dict(packet), "evidence_packet": dict(packet)},
        )

        self.assertIsNotNone(update.evidence_packet)
        self.assertEqual(
            update.evidence_packet["evidence_updates"], packet["evidence_updates"]
        )

    def test_critic_payload_keeps_the_stored_packet(self) -> None:
        context = _context(_review(_packet()))

        update = _ConcreteWorkflowState._review_update(
            context,
            {
                "task_reviews": [
                    {"item_id": "item-000001", "action": "TASK_CHANGES_REQUIRED"}
                ],
                "findings": [context.review.findings[0].to_dict()],
            },
        )

        self.assertIsNone(update.evidence_packet)

    def test_packet_survives_a_dto_round_trip(self) -> None:
        context = _context(_review(_packet()))

        restored = context_from_dto(context_to_dto(context))

        self.assertEqual(
            restored.review.evidence_packet["evidence_updates"],
            context.review.evidence_packet["evidence_updates"],
        )


if __name__ == "__main__":
    unittest.main()
