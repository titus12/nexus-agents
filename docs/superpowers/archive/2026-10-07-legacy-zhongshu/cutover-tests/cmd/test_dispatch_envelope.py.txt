from __future__ import annotations

import tempfile
import unittest

from orchestrator.dispatch_envelope import (
    build_envelope,
    validate_envelope,
)
from orchestrator.domain.context import (
    ProgressState,
    ReviewState,
    ReviewTaskRecord,
    TaskIdentity,
    WorkflowContext,
)
from orchestrator.domain.findings import Finding
from orchestrator.domain.policies.prompts import build_prompt
from orchestrator.domain.zhongshu import build_solver_dispatch
from orchestrator.domain.zhongshu.solver import batch_item_scope, revision_scope
from orchestrator.prompt_bundle import PromptBundleBuilder, PromptBundleError


def _finding(finding_id: str, item_id: str, *, severity: str = "P1") -> Finding:
    return Finding(
        finding_id=finding_id,
        severity=severity,
        group_id="group-000001",
        item_id=item_id,
        claim="claim",
    )


def _review() -> ReviewState:
    return ReviewState(
        revision_id="task-1:2",
        plan={"items": [], "groups": []},
        plan_hash="hash-1",
        findings=(
            _finding("finding-000001", "item-000001"),
            _finding("finding-000002", "item-000001"),
            _finding("finding-000003", "item-000002"),
            _finding("finding-000004", "item-000003"),
        ),
        task_review_ledger=(
            ReviewTaskRecord(
                item_id="item-000003",
                status="APPROVED",
                task_hash="h",
                dependency_hash="d",
            ),
        ),
    )


def _context(review: ReviewState, *, state: str = "ZHONGSHU_CRITIC") -> WorkflowContext:
    # The stage is decided by the transition edge: a dispatch leaving
    # ZHONGSHU_CRITIC towards the Solver is a revision.
    return WorkflowContext(
        identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
        progression=ProgressState(state, 8, "2026-09-17T00:00:00Z"),
        review=review,
    )


class EnvelopeValidationTests(unittest.TestCase):
    """A malformed envelope is a domain bug and must fail fast."""

    def test_valid_envelope_normalizes_and_keeps_optional_slice(self) -> None:
        envelope = build_envelope(
            ingredients=[
                {"key": "review.plan", "source": "context.json", "lifetime": "persisted",
                 "slice": "current_formal_plan"},
                {"key": "retry_feedback", "source": "prompt.txt", "lifetime": "transient"},
            ],
            tools={"editable": "plan.full", "contract": "c"},
            product={"type": "solver_result"},
        )

        self.assertEqual(len(envelope["ingredients"]), 2)
        self.assertEqual(envelope["ingredients"][0]["slice"], "current_formal_plan")
        self.assertNotIn("slice", envelope["ingredients"][1])

    def test_unknown_source_lifetime_and_missing_fields_raise(self) -> None:
        with self.assertRaises(ValueError):
            validate_envelope({
                "ingredients": [{"key": "x", "source": "elsewhere", "lifetime": "persisted"}],
                "tools": {"editable": "none"},
                "product": {"type": "t"},
            })
        with self.assertRaises(ValueError):
            validate_envelope({
                "ingredients": [{"key": "x", "source": "prompt.txt", "lifetime": "forever"}],
                "tools": {"editable": "none"},
                "product": {"type": "t"},
            })
        with self.assertRaises(ValueError):
            validate_envelope({
                "ingredients": [{"key": "", "source": "prompt.txt", "lifetime": "persisted"}],
                "tools": {"editable": "none"},
                "product": {"type": "t"},
            })
        with self.assertRaises(ValueError):
            validate_envelope({
                "ingredients": [{"key": "x", "source": "prompt.txt", "lifetime": "persisted"}],
                "tools": {},
                "product": {"type": "t"},
            })
        with self.assertRaises(ValueError):
            validate_envelope({
                "ingredients": [{"key": "x", "source": "prompt.txt", "lifetime": "persisted"}],
                "tools": {"editable": "none"},
                "product": {},
            })


class SolverEnvelopeTests(unittest.TestCase):
    """The Solver dispatch declares its ingredients and knife boundary."""

    def test_revise_envelope_declares_batch_scope_and_excludes_approved(self) -> None:
        context = _context(_review())
        prompt = build_prompt(context, target_state="ZHONGSHU_SOLVER")

        effect = build_solver_dispatch(
            context, request_id="req-1", prompt=prompt
        )

        envelope = effect.payload["dispatch_context"]["envelope"]
        keys = [item["key"] for item in envelope["ingredients"]]
        self.assertIn("review.plan", keys)
        self.assertIn("acceptance_standards", keys)
        self.assertEqual(envelope["tools"]["editable"], "items:item-000001,item-000002")
        self.assertEqual(envelope["product"]["type"], "solver_result")

    def test_formalize_envelope_declares_full_plan_editable(self) -> None:
        review = _review()
        context = _context(review, state="ZHONGSHU_SOLVER")
        prompt = build_prompt(context, target_state="ZHONGSHU_SOLVER")

        effect = build_solver_dispatch(
            context,
            request_id="req-1",
            prompt=prompt,
        )

        envelope = effect.payload["dispatch_context"]["envelope"]
        self.assertEqual(envelope["tools"]["editable"], "plan.full")

    def test_declared_boundary_matches_the_enforced_revision_scope(self) -> None:
        review = _review()
        context = _context(review)
        prompt = build_prompt(context, target_state="ZHONGSHU_SOLVER")
        effect = build_solver_dispatch(context, request_id="req-1", prompt=prompt)
        batch = effect.payload["dispatch_context"]["solver_batch"]
        selected = batch["selected_finding_ids"]

        declared = set(
            effect.payload["dispatch_context"]["envelope"]["tools"]["editable"]
            .removeprefix("items:")
            .split(",")
        )
        enforced = revision_scope(
            review,
            {
                "finding_batch": {
                    "selected_finding_ids": list(selected),
                    "remaining_finding_ids": [],
                }
            },
        )

        self.assertEqual(declared, enforced)
        self.assertEqual(declared, set(batch_item_scope(review, type("B", (), {
            "selected_finding_ids": tuple(selected),
        })())))


class TaskReviewEnvelopeTests(unittest.TestCase):
    def test_critic_binding_declares_read_only_boundary(self) -> None:
        from orchestrator.domain.states import _task_review_bindings
        from orchestrator.domain.context import (
            ReviewTaskGroup,
            ReviewTaskItem,
            ParallelState,
        )

        review = ReviewState(
            revision_id="task-1:2",
            findings=(_finding("finding-000001", "item-000001"),),
            task_items=(
                ReviewTaskItem("item-000001", "group-000001"),
                ReviewTaskItem("item-000002", "group-000001"),
            ),
            task_groups=(
                ReviewTaskGroup("group-000001", ("item-000001", "item-000002")),
            ),
        )
        context = WorkflowContext(
            identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
            progression=ProgressState("ZHONGSHU_CRITIC", 6, "2026-09-17T00:00:00Z"),
            parallel=ParallelState(),
            review=review,
        )

        bindings = _task_review_bindings(context, "task-1:2", "hash")

        envelope = bindings[0]["dispatch_context"]["envelope"]
        self.assertEqual(envelope["tools"]["editable"], "none")
        slices = [
            item.get("slice") for item in envelope["ingredients"] if item.get("slice")
        ]
        self.assertIn("item:item-000001", slices)
        self.assertEqual(envelope["product"]["type"], "task_review_verdict")


class ManifestEnvelopeTests(unittest.TestCase):
    """The bundle manifest is the machine-readable record of the declaration."""

    def test_manifest_carries_a_valid_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            builder = PromptBundleBuilder(directory)
            envelope = build_envelope(
                ingredients=[
                    {"key": "review.plan", "source": "context.json",
                     "lifetime": "persisted"},
                ],
                tools={"editable": "plan.full", "contract": "c"},
                product={"type": "solver_result"},
            )
            bundle = builder.build(
                task_id="t",
                request_id="r",
                phase="ZHONGSHU",
                role="review-solver",
                prompt="do it",
                context={"envelope": envelope},
            )

            import json

            manifest = json.loads(bundle.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["envelope"]["tools"]["editable"], "plan.full")

    def test_malformed_envelope_fails_the_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            builder = PromptBundleBuilder(directory)
            with self.assertRaises(PromptBundleError):
                builder.build(
                    task_id="t",
                    request_id="r",
                    phase="ZHONGSHU",
                    role="review-solver",
                    prompt="do it",
                    context={"envelope": {"ingredients": []}},
                )

    def test_absent_envelope_writes_an_empty_section(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            builder = PromptBundleBuilder(directory)
            bundle = builder.build(
                task_id="t",
                request_id="r",
                phase="ZHONGSHU",
                role="review-solver",
                prompt="do it",
                context=None,
            )

            import json

            manifest = json.loads(bundle.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["envelope"], {})


if __name__ == "__main__":
    unittest.main()
