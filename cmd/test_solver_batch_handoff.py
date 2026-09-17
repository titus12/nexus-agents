from __future__ import annotations

import json
import unittest

from orchestrator.domain.context import (
    ProgressState,
    ReviewState,
    ReviewTaskItem,
    ReviewTaskRecord,
    TaskIdentity,
    WorkflowContext,
)
from orchestrator.domain.events import DomainEvent
from orchestrator.domain.findings import Finding
from orchestrator.domain.policies.prompts import build_prompt
from orchestrator.domain.policies.zhongshu import select_solver_batch
from orchestrator.domain.states import ZhongshuCriticState


def _finding(index: int, severity: str = "P2") -> Finding:
    return Finding(
        finding_id=f"finding-{index:02d}",
        severity=severity,
        status="OPEN",
        group_id="group-000001",
        item_id=f"item-{index:03d}",
        claim=f"claim {index}",
        required_action=f"resolve {index}",
    )


def _plan(count: int = 7) -> dict:
    return {
        "requirements": [],
        "items": [{"item_id": f"item-{i:03d}"} for i in range(1, count + 1)],
        "groups": [
            {
                "group_id": "group-000001",
                "item_ids": [f"item-{i:03d}" for i in range(1, count + 1)],
            }
        ],
        "dependencies": [],
        "scope": {},
        "unknowns": [],
        "risks": [],
    }


def _context(
    findings: tuple[Finding, ...],
    *,
    has_plan: bool = True,
) -> WorkflowContext:
    return WorkflowContext(
        identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
        progression=ProgressState("ZHONGSHU_CRITIC", 6, "2026-09-16T00:00:00Z"),
        review=ReviewState(
            revision_id="task-1:ZHONGSHU_ANALYST:2",
            plan=_plan() if has_plan else None,
            plan_hash="plan-hash",
            findings=findings,
            task_items=tuple(
                ReviewTaskItem(f"item-{i:03d}", "group-000001", order=i - 1)
                for i in range(1, 8)
            ),
            task_review_ledger=tuple(
                ReviewTaskRecord(
                    item_id=f"item-{i:03d}",
                    task_hash="h",
                    dependency_hash="d",
                    status="CHANGES_REQUIRED",
                )
                for i in range(1, 8)
            ),
            zhongshu_revision_round=1,
        ),
    )


class SolverBatchSelectionTests(unittest.TestCase):
    """The Orchestrator, not the agent, owns the revision batch."""

    def test_batch_is_capped_disjoint_and_covers_every_active_finding(self) -> None:
        findings = tuple(
            _finding(index, "P1" if index == 1 else "P2") for index in range(1, 8)
        )

        selected, remaining = select_solver_batch(findings, 6)

        self.assertEqual(len(selected), 6)
        self.assertEqual(set(selected) & set(remaining), set())
        self.assertEqual(
            set(selected) | set(remaining),
            {finding.finding_id for finding in findings},
        )
        # One finding per item is taken first, so the last item is carried over.
        self.assertIn("finding-01", selected)
        self.assertEqual(remaining, ("finding-07",))

    def test_blockers_are_selected_before_follow_ups(self) -> None:
        findings = (
            _finding(1, "P2"),
            _finding(2, "P2"),
            _finding(3, "P0"),
        )

        selected, remaining = select_solver_batch(findings, 2)

        self.assertIn("finding-03", selected)
        self.assertEqual(len(selected), 2)
        self.assertEqual(len(remaining), 1)

    def test_no_active_findings_yields_an_empty_batch(self) -> None:
        selected, remaining = select_solver_batch((), 6)

        self.assertEqual((selected, remaining), ((), ()))


class SolverRevisionPromptTests(unittest.TestCase):
    """The revision prompt must state the task, not just the finding count."""

    def _prompt(self, findings: tuple[Finding, ...], *, has_plan: bool = True) -> str:
        return build_prompt(
            _context(findings, has_plan=has_plan), target_state="ZHONGSHU_SOLVER"
        ).content

    def test_prompt_lists_the_selected_findings_and_the_exact_batch(self) -> None:
        findings = tuple(
            _finding(index, "P1" if index == 1 else "P2") for index in range(1, 8)
        )

        content = self._prompt(findings)

        selected, remaining = select_solver_batch(findings, 6)
        self.assertIn("[Revision task]", content)
        for finding_id in selected:
            self.assertIn(finding_id, content)
        expected_batch = json.dumps(
            {
                "selected_finding_ids": list(selected),
                "remaining_finding_ids": list(remaining),
            },
            ensure_ascii=False,
        )
        self.assertIn(expected_batch, content)
        # The carried-over finding is not part of this round's work.
        self.assertNotIn("resolve 7", content)
        self.assertIn("resolve 1", content)

    def test_initial_run_has_no_revision_task(self) -> None:
        content = self._prompt((_finding(1),), has_plan=False)

        self.assertNotIn("[Revision task]", content)


class SolverDispatchContextTests(unittest.TestCase):
    """The dispatched context carries the same batch the prompt states."""

    def test_solver_dispatch_publishes_solver_batch(self) -> None:
        findings = tuple(
            _finding(index, "P1" if index == 1 else "P2") for index in range(1, 8)
        )
        context = _context(findings)
        event = DomainEvent(
            "NODE_COMPLETED",
            "task-1",
            6,
            {
                "action": "REQUEST_SOLVER_REVISION",
                "task_reviews": [
                    {
                        "item_id": f"item-{i:03d}",
                        "action": "TASK_CHANGES_REQUIRED",
                        "task_hash": "h",
                        "dependency_hash": "d",
                    }
                    for i in range(1, 8)
                ],
            },
            "2026-09-16T00:00:00Z",
        )

        decision = ZhongshuCriticState().handle(context, event)

        solver_effects = [
            effect
            for effect in decision.effects
            if effect.payload.get("target_state") == "ZHONGSHU_SOLVER"
        ]
        self.assertEqual(len(solver_effects), 1)
        batch = solver_effects[0].payload["dispatch_context"]["solver_batch"]
        expected_selected, expected_remaining = select_solver_batch(findings, 6)
        self.assertEqual(batch["selected_finding_ids"], list(expected_selected))
        self.assertEqual(batch["remaining_finding_ids"], list(expected_remaining))
        self.assertEqual(batch["max_findings"], 6)


if __name__ == "__main__":
    unittest.main()
