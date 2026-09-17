"""Code-level isolation of the Zhongshu Solver/Critic channels.

The Solver's two input channels (formalize from the Analyst, revise from the
Critic) must be separate logic paths, decided by the FSM edge and by the agent's
declared mode -- not inferred from payload shape.
"""

from __future__ import annotations

from dataclasses import replace
import unittest

from orchestrator.domain.context import (
    ProgressState,
    ReviewState,
    ReviewTaskRecord,
    TaskIdentity,
    WorkflowContext,
)
from orchestrator.domain.events import DomainEvent
from orchestrator.domain.findings import Finding
from orchestrator.domain.zhongshu import (
    SolverStage,
    evaluate_gate,
    fold_round,
    resolve_dispatch_stage,
    resolve_reply_stage,
    resolve_solver_stage,
    solver_contract_fragments,
)
from orchestrator.domain.zhongshu.solver import (
    FormalizeSolverLogic,
    ReviseSolverLogic,
    SolverBatch,
    build_solver_dispatch,
    process_solver_reply,
)


def _finding(index: int, severity: str = "P2", item_id: str = "") -> Finding:
    return Finding(
        finding_id=f"finding-{index:02d}",
        severity=severity,
        status="OPEN",
        group_id="group-000001",
        item_id=item_id or f"item-{index:03d}",
        claim=f"claim {index}",
        required_action=f"resolve {index}",
    )


def _plan(item_count: int = 7) -> dict:
    return {
        "requirements": [],
        "items": [{"item_id": f"item-{i:03d}"} for i in range(1, item_count + 1)],
        "groups": [
            {
                "group_id": "group-000001",
                "item_ids": [f"item-{i:03d}" for i in range(1, item_count + 1)],
            }
        ],
        "dependencies": [],
        "scope": {},
        "unknowns": [],
        "risks": [],
    }


def _context(findings=(), *, plan=None, state: str = "ZHONGSHU_SOLVER") -> WorkflowContext:
    return WorkflowContext(
        identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
        progression=ProgressState(state, 11, "2026-09-16T00:00:00Z"),
        review=ReviewState(
            revision_id="task-1:ZHONGSHU_ANALYST:2",
            plan=plan,
            plan_hash="plan-hash",
            findings=tuple(findings),
        ),
    )


class StageResolutionTests(unittest.TestCase):
    """The stage follows the FSM edge and the agent's declared mode."""

    def test_dispatch_stage_follows_the_transition_edge(self) -> None:
        self.assertEqual(
            resolve_dispatch_stage("ZHONGSHU_CRITIC"), SolverStage.REVISE
        )
        self.assertEqual(
            resolve_dispatch_stage("ZHONGSHU_FREEZE_CHECK"), SolverStage.REVISE
        )
        self.assertEqual(
            resolve_dispatch_stage("ZHONGSHU_ANALYST"), SolverStage.FORMALIZE
        )
        self.assertEqual(
            resolve_dispatch_stage("REQUEST_INTAKE"), SolverStage.FORMALIZE
        )

    def test_reply_stage_follows_the_declared_mode(self) -> None:
        review = _context(plan=_plan()).review
        self.assertIs(
            resolve_reply_stage({"mode": "TASK_GRAPH_FORMALIZATION_READ_ONLY_RESUME"}, review),
            SolverStage.REVISE,
        )
        self.assertIs(
            resolve_reply_stage({"mode": "TASK_GRAPH_FORMALIZATION_READ_ONLY"}, review),
            SolverStage.FORMALIZE,
        )
        # Without a declared mode the review snapshot decides.
        self.assertIs(resolve_reply_stage({}, review), SolverStage.REVISE)
        self.assertIs(resolve_reply_stage({}, _context(plan=None).review), SolverStage.FORMALIZE)

    def test_contract_stage_reads_the_explicit_key_with_legacy_fallback(self) -> None:
        self.assertIs(
            resolve_solver_stage({"solver_stage": "revise"}), SolverStage.REVISE
        )
        self.assertIs(
            resolve_solver_stage({"solver_stage": "formalize"}), SolverStage.FORMALIZE
        )
        self.assertIs(resolve_solver_stage({"has_current_plan": True}), SolverStage.REVISE)
        self.assertIs(resolve_solver_stage({}), SolverStage.FORMALIZE)


class SolverChannelIsolationTests(unittest.TestCase):
    """Each channel validates and materializes through its own logic."""

    def test_formalize_channel_ignores_the_finding_batch(self) -> None:
        logic = FormalizeSolverLogic()
        payload = {
            "action": "READY_FOR_CRITIC",
            "plan": _plan(),
            "finding_batch": {
                "selected_finding_ids": ["finding-01"],
                "remaining_finding_ids": [],
            },
        }

        self.assertEqual(logic.validate(payload, None), "")
        materialized, error = logic.materialize(payload, None)
        self.assertEqual(error, "")
        self.assertEqual(materialized, _plan())

    def test_revise_channel_enforces_the_dictated_batch(self) -> None:
        findings = tuple(_finding(i) for i in range(1, 8))
        review = _context(findings, plan=_plan()).review
        batch = SolverBatch.for_findings(findings)
        logic = ReviseSolverLogic(batch)

        # The dictated batch is echoed -> accepted, and scoped to batch items.
        echo = {
            "action": "READY_FOR_CRITIC",
            "changes": [],
            "finding_batch": batch.as_reply_template(),
        }
        self.assertEqual(logic.validate(echo, review), "")
        self.assertEqual(
            logic.editable_item_ids(echo, review),
            {f"item-{i:03d}" for i in range(1, 7)},
        )

        # Re-planning the partition is a reply-shape error, not a policy one.
        replanned = {
            "action": "READY_FOR_CRITIC",
            "changes": [],
            "finding_batch": {
                "selected_finding_ids": ["finding-01"],
                "remaining_finding_ids": [f"finding-{i:02d}" for i in range(2, 8)],
            },
        }
        self.assertTrue(
            logic.validate(replanned, review).startswith("SOLVER_BATCH_MISMATCH")
        )

    def test_process_reply_routes_by_declared_mode(self) -> None:
        findings = tuple(_finding(i) for i in range(1, 8))
        review = _context(findings, plan=_plan()).review
        batch = SolverBatch.for_findings(findings)

        revise_reply = process_solver_reply(
            {
                "mode": "TASK_GRAPH_FORMALIZATION_READ_ONLY_RESUME",
                "action": "READY_FOR_CRITIC",
                "changes": [],
                "finding_batch": batch.as_reply_template(),
            },
            review,
        )
        self.assertIs(revise_reply.stage, SolverStage.REVISE)
        self.assertEqual(revise_reply.error, "")

        # A formalization-mode reply on the same review skips the batch checks.
        formalize_reply = process_solver_reply(
            {
                "mode": "TASK_GRAPH_FORMALIZATION_READ_ONLY",
                "action": "READY_FOR_CRITIC",
                "plan": _plan(),
            },
            review,
        )
        self.assertIs(formalize_reply.stage, SolverStage.FORMALIZE)
        self.assertEqual(formalize_reply.error, "")


class SolverDispatchIsolationTests(unittest.TestCase):
    """Each stage dispatches a different context; the revise one is trimmed."""

    def _dispatch(self, findings, *, plan, state: str):
        context = _context(findings, plan=plan, state=state)
        prompt = type("Prompt", (), {"content": "prompt", "references": {}})()
        effect = build_solver_dispatch(
            context,
            request_id="task-1:ZHONGSHU_SOLVER:12",
            prompt=prompt,
            revision_id="task-1:ZHONGSHU_ANALYST:2",
            plan_hash="plan-hash",
        )
        return effect.payload["dispatch_context"]

    def test_revise_context_carries_the_batch_and_only_its_findings(self) -> None:
        findings = tuple(_finding(i) for i in range(1, 8))
        context = self._dispatch(findings, plan=_plan(), state="ZHONGSHU_CRITIC")

        self.assertEqual(context["solver_stage"], "revise")
        self.assertEqual(context["zhongshu_dispatch_mode"], "solver_revision")
        batch = context["solver_batch"]
        self.assertEqual(len(batch["selected_finding_ids"]), 6)
        self.assertEqual(batch["remaining_finding_ids"], ["finding-07"])
        # Trimming: only the batch's findings are shipped, not the whole history.
        shipped = {f["finding_id"] for f in context["active_findings"]}
        self.assertEqual(shipped, set(batch["selected_finding_ids"]))
        self.assertEqual(context["focus_finding_ids"], list(batch["selected_finding_ids"]))

    def test_formalize_context_carries_no_critic_feedback(self) -> None:
        findings = tuple(_finding(i) for i in range(1, 8))
        context = self._dispatch(findings, plan=None, state="ZHONGSHU_ANALYST")

        self.assertEqual(context["solver_stage"], "formalize")
        self.assertEqual(context["zhongshu_dispatch_mode"], "solver_formalize")
        self.assertNotIn("solver_batch", context)
        self.assertEqual(context["active_findings"], [])
        self.assertEqual(context["focus_finding_ids"], [])

    def test_trimmed_revise_context_is_materially_smaller(self) -> None:
        import json

        findings = tuple(_finding(i) for i in range(1, 21))
        full = self._dispatch(findings, plan=_plan(20), state="ZHONGSHU_CRITIC")
        trimmed = len(json.dumps(full["active_findings"], ensure_ascii=False))
        self.assertLess(trimmed, len(json.dumps(findings, default=lambda o: o.__dict__, ensure_ascii=False)) / 3)


class CriticGateTests(unittest.TestCase):
    """The gate decision table, including the stuck-finding escalation order."""

    def _round(self, findings, *, ledger=(), expected=("item-001",)):
        return fold_round(
            ledger=ledger,
            findings=findings,
            expected_item_ids=expected,
            max_item_rounds=5,
        )

    def test_stuck_finding_escalates_even_while_making_progress(self) -> None:
        # Regression: the stuck check runs in both progress branches.
        stuck = replace(_finding(1, "P1"), stuck_rounds=3)
        verdict = evaluate_gate(
            self._round((stuck,)),
            revision_allowed=True,
            previous_fingerprint=None,
            no_progress_count=0,
            max_no_progress=3,
            max_stuck_rounds=3,
        )
        self.assertEqual(verdict.action, "HUMAN_GATE")
        self.assertEqual(verdict.reason_code, "ZHONGSHU_STUCK_FINDING")

    def test_followup_freeze_requires_every_item_reviewed(self) -> None:
        p1 = _finding(1, "P1")
        ledger = (ReviewTaskRecord(item_id="item-001", task_hash="h", dependency_hash="d", status="CHANGES_REQUIRED"),)
        verdict = evaluate_gate(
            self._round((p1,), ledger=ledger),
            revision_allowed=True,
            previous_fingerprint="blockers=1",
            no_progress_count=2,
            max_no_progress=3,
            max_stuck_rounds=3,
        )
        self.assertEqual(verdict.action, "FREEZE_WITH_FOLLOWUPS")
        self.assertEqual(verdict.followup_finding_ids, ("finding-01",))

        # One expected task never reviewed -> the freeze is refused.
        verdict = evaluate_gate(
            self._round((p1,), ledger=ledger, expected=("item-001", "item-002")),
            revision_allowed=True,
            previous_fingerprint="blockers=1",
            no_progress_count=2,
            max_no_progress=3,
            max_stuck_rounds=3,
        )
        self.assertNotEqual(verdict.action, "FREEZE_WITH_FOLLOWUPS")
        self.assertEqual(verdict.deferred_followup_unreviewed, ("item-002",))

    def test_budget_exhaustion_blocks_before_anything_else(self) -> None:
        verdict = evaluate_gate(
            self._round((_finding(1, "P1"),)),
            revision_allowed=False,
            previous_fingerprint=None,
            no_progress_count=0,
            max_no_progress=3,
            max_stuck_rounds=3,
        )
        self.assertEqual(verdict.action, "BLOCKED")
        self.assertEqual(verdict.reason_code, "ZHONGSHU_REVISION_BUDGET_EXHAUSTED")


class SolverStageContractTests(unittest.TestCase):
    """The reply contract differs per channel."""

    def test_revise_contract_has_the_batch_rule_and_loose_required_fields(self) -> None:
        fragments = solver_contract_fragments(SolverStage.REVISE)
        self.assertIn("finding_batch_rule", fragments)
        self.assertEqual(fragments["required_by_action"], {"READY_FOR_CRITIC": ["action"]})

    def test_formalize_contract_requires_the_plan_and_has_no_batch_rule(self) -> None:
        fragments = solver_contract_fragments(SolverStage.FORMALIZE)
        self.assertNotIn("finding_batch_rule", fragments)
        self.assertEqual(
            fragments["required_by_action"], {"READY_FOR_CRITIC": ["action", "plan"]}
        )


if __name__ == "__main__":
    unittest.main()
