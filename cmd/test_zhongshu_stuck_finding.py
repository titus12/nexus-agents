from __future__ import annotations

import unittest

from orchestrator.domain.context import (
    ProgressState,
    RecoveryState,
    ReviewState,
    TaskIdentity,
    WorkflowContext,
)
from orchestrator.domain.events import DomainEvent
from orchestrator.domain.findings import Finding
from orchestrator.domain.policies.zhongshu import (
    age_unresolved_findings,
    stuck_blockers,
)
from orchestrator.domain.states import ZhongshuCriticState
from orchestrator.runtime.reducer import LinearContextReducer
from orchestrator.runtime.repository import WorkflowSnapshot


def _finding(
    finding_id: str,
    *,
    severity: str = "P1",
    status: str = "OPEN",
    stuck_rounds: int = 0,
) -> Finding:
    return Finding(
        finding_id=finding_id,
        severity=severity,
        status=status,
        group_id="group-001",
        item_id="item-000001",
        claim="blocking claim",
        required_action="fix it",
        stuck_rounds=stuck_rounds,
    )


def _finding_payload(finding: Finding) -> dict[str, object]:
    return finding.to_dict()


class FindingAgeTests(unittest.TestCase):
    def test_repeated_finding_ages(self) -> None:
        prior = (_finding("f-1", stuck_rounds=1),)
        merged = (_finding("f-1"),)

        aged = age_unresolved_findings(prior, merged, merged)

        self.assertEqual(aged[0].stuck_rounds, 2)

    def test_resolved_finding_resets_age(self) -> None:
        prior = (_finding("f-1", stuck_rounds=5),)
        merged = (_finding("f-1", status="RESOLVED"),)

        aged = age_unresolved_findings(prior, merged, merged)

        self.assertEqual(aged[0].stuck_rounds, 0)

    def test_first_observation_starts_at_zero(self) -> None:
        merged = (_finding("f-1"),)

        aged = age_unresolved_findings((), merged, merged)

        self.assertEqual(aged[0].stuck_rounds, 0)

    def test_unobserved_finding_keeps_its_age(self) -> None:
        prior = (_finding("f-1", stuck_rounds=5),)
        merged = (_finding("f-1", stuck_rounds=5),)

        aged = age_unresolved_findings(prior, merged, ())

        self.assertEqual(aged[0].stuck_rounds, 5)

    def test_stuck_blockers_selects_only_exhausted_blockers(self) -> None:
        findings = (
            _finding("f-1", stuck_rounds=3),
            _finding("f-2", stuck_rounds=1),
            _finding("f-3", status="RESOLVED", stuck_rounds=9),
            _finding("f-4", severity="P2", stuck_rounds=9),
        )

        self.assertEqual(
            [finding.finding_id for finding in stuck_blockers(findings, 3)], ["f-1"]
        )
        self.assertEqual(stuck_blockers(findings, 0), ())


class ZhongshuStuckFindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = ZhongshuCriticState()
        self.reducer = LinearContextReducer()

    def _context(
        self,
        findings: tuple[Finding, ...],
        *,
        max_stuck_finding_rounds: int = 3,
    ) -> WorkflowContext:
        return WorkflowContext(
            identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
            progression=ProgressState("ZHONGSHU_CRITIC", 6, "2026-09-13T00:00:00Z"),
            recovery=RecoveryState(
                max_stuck_finding_rounds=max_stuck_finding_rounds
            ),
            review=ReviewState(
                revision_id="task-1:ZHONGSHU_ANALYST:2",
                findings=findings,
                zhongshu_revision_round=2,
                max_zhongshu_revision_rounds=8,
                plan_hash="plan-hash",
            ),
        )

    def _snapshot(self, context: WorkflowContext) -> WorkflowSnapshot:
        return WorkflowSnapshot("task-1", context, 0)

    def test_repeated_finding_escalates_to_human_gate(self) -> None:
        context = self._context((_finding("f-1", stuck_rounds=3),))
        decision = self.state.handle(
            context, DomainEvent("NODE_COMPLETED", "task-1", 6, {"action": "REQUEST_SOLVER_REVISION"}, "2026-09-13T00:00:00Z")
        )

        self.assertEqual(decision.transition.action, "HUMAN_GATE")
        self.assertEqual(decision.transition.reason_code, "ZHONGSHU_STUCK_FINDING")
        self.assertEqual(
            decision.update.human_gate.reason_code, "ZHONGSHU_STUCK_FINDING"
        )
        self.assertFalse(decision.effects)

        after = self.reducer.apply(self._snapshot(context), decision)
        self.assertEqual(after.context.progression.state, "HUMAN_GATE")
        self.assertEqual(after.context.progression.resume_state, "ZHONGSHU_CRITIC")

    def test_finding_inside_the_budget_keeps_revising(self) -> None:
        context = self._context((_finding("f-1"),))
        event = DomainEvent(
            "NODE_COMPLETED",
            "task-1",
            6,
            {
                "action": "REQUEST_SOLVER_REVISION",
                "findings": [_finding_payload(_finding("f-1"))],
            },
            "2026-09-13T00:00:00Z",
        )

        decision = self.state.handle(context, event)

        self.assertEqual(decision.transition.action, "REQUEST_SOLVER_REVISION")
        self.assertEqual(decision.update.review.findings[0].stuck_rounds, 1)

    def test_aging_from_payload_reaches_the_budget_and_escalates(self) -> None:
        context = self._context((_finding("f-1", stuck_rounds=2),))
        event = DomainEvent(
            "NODE_COMPLETED",
            "task-1",
            6,
            {
                "action": "REQUEST_SOLVER_REVISION",
                "findings": [_finding_payload(_finding("f-1"))],
            },
            "2026-09-13T00:00:00Z",
        )

        decision = self.state.handle(context, event)

        self.assertEqual(decision.transition.action, "HUMAN_GATE")
        self.assertEqual(decision.transition.reason_code, "ZHONGSHU_STUCK_FINDING")

    def test_escalation_can_be_disabled(self) -> None:
        context = self._context(
            (_finding("f-1", stuck_rounds=9),), max_stuck_finding_rounds=0
        )
        decision = self.state.handle(
            context, DomainEvent("NODE_COMPLETED", "task-1", 6, {"action": "REQUEST_SOLVER_REVISION"}, "2026-09-13T00:00:00Z")
        )

        self.assertEqual(decision.transition.action, "REQUEST_SOLVER_REVISION")


if __name__ == "__main__":
    unittest.main()
