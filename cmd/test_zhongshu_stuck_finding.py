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
    blocker_fingerprint,
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
        attempted_finding_ids: tuple[str, ...] | None = None,
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
                attempted_finding_ids=attempted_finding_ids,
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

    def test_finding_outside_the_batch_does_not_age(self) -> None:
        # The batch dictated to the Solver never picked f-1, so the Critic
        # re-raising it must not push it towards the stuck escalation.
        context = self._context(
            (_finding("f-1", stuck_rounds=2),),
            attempted_finding_ids=("f-other",),
        )
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
        self.assertEqual(decision.update.review.findings[0].stuck_rounds, 2)

    def test_batched_finding_still_ages_to_the_gate(self) -> None:
        context = self._context(
            (_finding("f-1", stuck_rounds=2),),
            attempted_finding_ids=("f-1",),
        )
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


class ZhongshuAnalystEvidenceFuseTests(unittest.TestCase):
    """Evidence rounds must be bound by the same convergence fuses.

    The live run task-20260920-94c51b spun Critic -> Analyst -> Solver forever
    because REQUEST_ANALYST_EVIDENCE bypassed the gate: the stuck threshold
    was already crossed and the no-progress exit was unreachable.
    """

    def setUp(self) -> None:
        self.state = ZhongshuCriticState()
        self.reducer = LinearContextReducer()

    def _context(
        self,
        findings: tuple[Finding, ...],
        *,
        no_progress_count: int = 0,
        last_reply_fingerprint: str | None = None,
    ) -> WorkflowContext:
        return WorkflowContext(
            identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
            progression=ProgressState("ZHONGSHU_CRITIC", 6, "2026-09-13T00:00:00Z"),
            recovery=RecoveryState(
                no_progress_count=no_progress_count,
                max_no_progress=3,
                max_stuck_finding_rounds=3,
            ),
            review=ReviewState(
                revision_id="task-1:ZHONGSHU_ANALYST:2",
                findings=findings,
                zhongshu_revision_round=2,
                max_zhongshu_revision_rounds=8,
                plan_hash="plan-hash",
                last_reply_fingerprint=last_reply_fingerprint,
            ),
        )

    def _event(self, finding: Finding) -> DomainEvent:
        return DomainEvent(
            "NODE_COMPLETED",
            "task-1",
            6,
            {
                "action": "REQUEST_ANALYST_EVIDENCE",
                "findings": [_finding_payload(finding)],
            },
            "2026-09-13T00:00:00Z",
        )

    def test_analyst_round_hits_the_stuck_fuse(self) -> None:
        context = self._context((_finding("f-1", stuck_rounds=3),))

        decision = self.state.handle(context, self._event(_finding("f-1")))

        self.assertEqual(decision.transition.action, "HUMAN_GATE")
        self.assertEqual(decision.transition.reason_code, "ZHONGSHU_STUCK_FINDING")

        after = self.reducer.apply(WorkflowSnapshot("task-1", context, 0), decision)
        self.assertEqual(after.context.progression.state, "HUMAN_GATE")

    def test_repeated_analyst_rounds_accumulate_no_progress(self) -> None:
        # A P0 residual disables the P1-followup freeze, so the third round
        # with an unchanged fingerprint must hit the no-progress block.
        finding = _finding("f-1", severity="P0")
        context = self._context(
            (finding,),
            no_progress_count=2,
            last_reply_fingerprint=blocker_fingerprint((finding,)),
        )

        decision = self.state.handle(context, self._event(finding))

        self.assertEqual(decision.transition.action, "BLOCKED")
        self.assertEqual(decision.transition.reason_code, "ZHONGSHU_NO_PROGRESS")

    def test_fresh_findings_keep_the_analyst_loop_going(self) -> None:
        finding = _finding("f-1")
        context = self._context(
            (finding,),
            no_progress_count=2,
            last_reply_fingerprint="different",
        )

        decision = self.state.handle(context, self._event(finding))

        self.assertEqual(decision.transition.action, "REQUEST_ANALYST_EVIDENCE")
        self.assertEqual(decision.update.recovery.no_progress_count, 0)
        self.assertEqual(
            decision.update.review.last_reply_fingerprint,
            blocker_fingerprint((finding,)),
        )


if __name__ == "__main__":
    unittest.main()
