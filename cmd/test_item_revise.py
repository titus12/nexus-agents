from __future__ import annotations

import copy
import unittest

from orchestrator.domain.context import (
    ItemWorkflow,
    ParallelState,
    ProgressState,
    ReviewState,
    ReviewTaskItem,
    ReviewTaskRecord,
    TaskIdentity,
    WorkflowContext,
)
from orchestrator.domain.events import DomainEvent
from orchestrator.domain.findings import Finding
from orchestrator.domain.policies.item_revise import (
    contested_item_ids,
    materialize_item_patch,
    merge_item_patches,
    plan_item,
)
from orchestrator.domain.states import (
    ZhongshuSolverState,
    _ConcreteWorkflowState,
    _item_revise_bindings,
)
from orchestrator.runtime.agent_effects import AgentNodeJoiner
from orchestrator.runtime.nodes import WorkerResult


def _ledger(*pairs: tuple[str, str]) -> tuple[ReviewTaskRecord, ...]:
    return tuple(
        ReviewTaskRecord(item_id=item_id, status=status, task_hash="h", dependency_hash="d")
        for item_id, status in pairs
    )


def _plan() -> dict:
    return {
        "requirements": [],
        "items": [
            {
                "item_id": "item-000001",
                "group_id": "group-000001",
                "title": "first",
                "objective": "first objective",
                "dependencies": [],
                "source_requirement_ids": ["req-000001"],
                "acceptance_signals": ["old signal without unit"],
            },
            {
                "item_id": "item-000002",
                "group_id": "group-000001",
                "title": "second",
                "objective": "second objective",
                "dependencies": [],
                "source_requirement_ids": ["req-000001"],
                "acceptance_signals": ["old signal without unit"],
            },
        ],
        "groups": [{"group_id": "group-000001", "item_ids": ["item-000001", "item-000002"]}],
    }


def _patch_payload(item_id: str, *, signal: str = "new signal; measured in UTF-8 bytes") -> dict:
    return {
        "action": "READY_FOR_CRITIC",
        "item_id": item_id,
        "group_id": "group-000001",
        "item": {
            "item_id": item_id,
            "group_id": "group-000001",
            "title": "first" if item_id == "item-000001" else "second",
            "objective": "revised objective",
            "dependencies": [],
            "source_requirement_ids": ["req-000001"],
            "acceptance_signals": [signal],
        },
        "finding_resolutions": [
            {"finding_id": "finding-000001", "response": "acceptance rewritten"}
        ],
    }


class ItemRevisePolicyTests(unittest.TestCase):
    def test_contested_items_are_the_unapproved_ones_in_order(self) -> None:
        contested = contested_item_ids(
            _ledger(("item-000002", "APPROVED"), ("item-000001", "CHANGES_REQUIRED"))
        )

        self.assertEqual(contested, ("item-000001",))

    def test_materialize_rejects_identity_and_field_violations(self) -> None:
        payload = _patch_payload("item-000001")
        payload["item_id"] = "item-000002"

        _, error = materialize_item_patch(payload, "item-000001")
        self.assertTrue(error.startswith("ITEM_PATCH_IDENTITY"))

        payload = _patch_payload("item-000001")
        payload["item"]["benefit"] = "extra field is fine"
        payload["item"]["mystery_field"] = "not allowed"

        _, error = materialize_item_patch(payload, "item-000001")
        self.assertTrue(error.startswith("ITEM_PATCH_FIELD_FORBIDDEN"))

    def test_merge_is_non_mutating_and_rejects_unknown_items(self) -> None:
        plan = _plan()
        frozen = copy.deepcopy(plan)
        patch, error = materialize_item_patch(_patch_payload("item-000002"), "item-000002")
        self.assertFalse(error)

        merged, merge_error = merge_item_patches(plan, [patch])

        self.assertFalse(merge_error)
        self.assertEqual(plan, frozen)
        self.assertEqual(merged["items"][1]["acceptance_signals"], [
            "new signal; measured in UTF-8 bytes"
        ])
        self.assertEqual(merged["items"][0]["acceptance_signals"], ["old signal without unit"])

        stranger, _ = materialize_item_patch(_patch_payload("item-000009"), "item-000009")
        _, merge_error = merge_item_patches(plan, [stranger])
        self.assertTrue(merge_error.startswith("ITEM_MERGE_UNKNOWN_ITEM"))


class JoinItemReviseTests(unittest.TestCase):
    """The joiner merges per-item patches and re-hashes the plan."""

    def _results(self, *payloads: dict) -> tuple[WorkerResult, ...]:
        return tuple(
            WorkerResult(
                worker_id=f"worker-{index:02d}",
                status="SUCCEEDED",
                payload_ref=None,
                result_payload=payload,
            )
            for index, payload in enumerate(payloads, start=1)
        )

    def test_two_patches_merge_into_one_revised_plan(self) -> None:
        joiner = AgentNodeJoiner(
            "node-1",
            task_id="task-1",
            state="ZHONGSHU_SOLVER",
            dispatch_mode="item_revise",
            base_plan=_plan(),
        )

        result = joiner.join(
            self._results(
                _patch_payload("item-000001"),
                _patch_payload("item-000002", signal="other signal; P95 latency"),
            )
        )

        self.assertEqual(result.status, "SUCCEEDED")
        self.assertEqual(result.aggregate["action"], "READY_FOR_CRITIC")
        self.assertTrue(result.aggregate["plan_hash"])
        merged = result.aggregate["plan"]
        self.assertEqual(
            merged["items"][0]["acceptance_signals"],
            ["new signal; measured in UTF-8 bytes"],
        )
        self.assertEqual(
            merged["items"][1]["acceptance_signals"],
            ["other signal; P95 latency"],
        )

    def test_invalid_patch_fails_retryable(self) -> None:
        joiner = AgentNodeJoiner(
            "node-1",
            task_id="task-1",
            state="ZHONGSHU_SOLVER",
            dispatch_mode="item_revise",
            base_plan=_plan(),
        )
        bad = _patch_payload("item-000001")
        bad["item"]["item_id"] = "item-000002"

        result = joiner.join(self._results(bad))

        self.assertEqual(result.status, "FAILED")
        self.assertEqual(result.failure.error_code, "NODE_ITEM_REVISION_INVALID")
        self.assertTrue(result.failure.retryable)


class ItemReviseBindingTests(unittest.TestCase):
    """One binding per contested item, with the knife boundary declared."""

    def _context(self, *, enabled: bool, state: str = "ZHONGSHU_CRITIC") -> WorkflowContext:
        return WorkflowContext(
            identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
            progression=ProgressState(state, 8, "2026-09-17T00:00:00Z"),
            parallel=ParallelState(
                zhongshu=__import__(
                    "orchestrator.domain.context", fromlist=["ZhongshuParallelLimits"]
                ).ZhongshuParallelLimits(item_workflow_enabled=enabled),
            ),
            review=ReviewState(
                revision_id="task-1:2",
                plan=_plan(),
                plan_hash="hash-1",
                findings=(
                    Finding(
                        finding_id="finding-000001",
                        severity="P1",
                        group_id="group-000001",
                        item_id="item-000001",
                        claim="acceptance not observable",
                        required_action="rewrite acceptance",
                    ),
                ),
                task_review_ledger=_ledger(
                    ("item-000001", "CHANGES_REQUIRED"),
                    ("item-000002", "APPROVED"),
                ),
                task_items=(
                    ReviewTaskItem("item-000001", "group-000001"),
                    ReviewTaskItem("item-000002", "group-000001"),
                ),
            ),
        )

    def test_bindings_cover_only_contested_items(self) -> None:
        context = self._context(enabled=True)

        bindings = _item_revise_bindings(context, "task-1:2", "hash-1")

        self.assertEqual(len(bindings), 1)
        binding = bindings[0]
        self.assertEqual(binding["item_id"], "item-000001")
        self.assertEqual(binding["role"], "review-solver")
        envelope = binding["dispatch_context"]["envelope"]
        self.assertEqual(envelope["tools"]["editable"], "items:item-000001")
        self.assertIn("finding-000001", binding["prompt_ref"])
        self.assertIn("Rewrite only this item", binding["prompt_ref"])

    def test_binding_builder_ignores_the_flag_by_design(self) -> None:
        # The flag gates the dispatch site, not the pure binding builder; the
        # disabled-path behaviour is asserted via _dispatch_effect below.
        context = self._context(enabled=False)

        bindings = _item_revise_bindings(context, "task-1:2", "hash-1")

        self.assertEqual(len(bindings), 1)

    def test_enabled_flag_routes_the_revision_edge_to_the_node(self) -> None:
        context = self._context(enabled=True)

        effect = _ConcreteWorkflowState._dispatch_effect(
            context,
            "ZHONGSHU_SOLVER",
            revision_id="task-1:2",
            plan_hash="hash-1",
        )

        self.assertEqual(effect.effect_type, "node_dispatch")
        self.assertEqual(effect.payload["dispatch_mode"], "item_revise")
        self.assertEqual(len(effect.payload["bindings"]), 1)
        self.assertEqual(effect.payload["current_formal_plan"], _plan())

    def test_disabled_flag_keeps_the_single_writer_dispatch(self) -> None:
        context = self._context(enabled=False)

        effect = _ConcreteWorkflowState._dispatch_effect(
            context,
            "ZHONGSHU_SOLVER",
            revision_id="task-1:2",
            plan_hash="hash-1",
        )

        self.assertEqual(effect.effect_type, "agent_dispatch")


class SolverStateNodePathTests(unittest.TestCase):
    """A NODE_COMPLETED aggregate skips the reply validator and writes the plan."""

    def test_node_aggregate_is_persisted_without_reply_validation(self) -> None:
        context = WorkflowContext(
            identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
            progression=ProgressState("ZHONGSHU_SOLVER", 10, "2026-09-17T00:00:00Z"),
            review=ReviewState(
                revision_id="task-1:2",
                item_workflows=(ItemWorkflow(item_id="item-000001"),),
            ),
        )
        state = ZhongshuSolverState()
        event = DomainEvent(
            "NODE_COMPLETED",
            "task-1",
            10,
            {
                "action": "READY_FOR_CRITIC",
                "plan": _plan(),
                "plan_hash": "new-hash",
                "revision_id": "task-1:2",
                "summary": "item-scoped revision merged for item-000001",
            },
            "2026-09-17T00:00:00Z",
        )

        decision = state.handle(context, event)

        self.assertEqual(decision.transition.action, "READY_FOR_CRITIC")
        self.assertEqual(decision.update.review.plan, _plan())
        self.assertEqual(decision.update.review.plan_hash, "new-hash")
        # The plan artifact effect rides along with the transition.
        self.assertTrue(
            any(effect.effect_type == "plan_artifact" for effect in decision.effects)
        )


if __name__ == "__main__":
    unittest.main()
