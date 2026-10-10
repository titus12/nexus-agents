from __future__ import annotations

import unittest

from orchestrator.domain.context import (
    ProgressState,
    RecoveryState,
    ReviewState,
    ReviewTaskItem,
    ReviewTaskRecord,
    TaskIdentity,
    WorkflowContext,
)
from orchestrator.domain.events import DomainEvent
from orchestrator.domain.findings import Finding
from orchestrator.domain.policies.zhongshu import blocker_fingerprint
from orchestrator.domain.states import StateRegistry
from orchestrator.runtime.reducer import LinearContextReducer
from orchestrator.runtime.repository import WorkflowSnapshot


def _finding(
    finding_id: str,
    *,
    severity: str = "P1",
    status: str = "OPEN",
    stuck_rounds: int = 0,
    canonical_key: str = "deadbeef",
    claim: str = "blocking claim",
) -> Finding:
    return Finding(
        finding_id=finding_id,
        severity=severity,
        status=status,
        group_id="group-000002",
        item_id="item-000006",
        claim=claim,
        required_action="fix it",
        stuck_rounds=stuck_rounds,
        canonical_key=canonical_key,
    )


def _items() -> tuple[ReviewTaskItem, ...]:
    return tuple(
        ReviewTaskItem(
            item_id=f"item-{index:06d}",
            group_id=f"group-{(index + 2) // 3:06d}",
            source_requirement_ids=("req-000001",),
        )
        for index in range(1, 8)
    )


def _ledger(item_rounds: int) -> tuple[ReviewTaskRecord, ...]:
    rows = [
        ReviewTaskRecord(item_id=f"item-{index:06d}", status="APPROVED")
        for index in range(1, 8)
        if index != 6
    ]
    rows.append(
        ReviewTaskRecord(
            item_id="item-000006",
            status="CHANGES_REQUIRED",
            changes_rounds=item_rounds,
        )
    )
    return tuple(rows)


class StuckFindingTreadmillTests(unittest.TestCase):
    """Reproduce the live task-menxia-t1 treadmill: one stable P1 finding the
    Critic re-raises every round while every human-gate resume re-runs the
    wave.  With a stable canonical key the no-progress fuse must age and
    FREEZE_WITH_FOLLOWUPS must replace the endless stuck-gate loop."""

    def setUp(self) -> None:
        self.registry = StateRegistry.default()
        self.reducer = LinearContextReducer()
        self.finding = _finding("finding-78e64e8b", stuck_rounds=6)
        self.snapshot = WorkflowSnapshot("task-1", self._context(), 0)

    def _context(self, *, no_progress_count: int = 0) -> WorkflowContext:
        return WorkflowContext(
            identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
            progression=ProgressState("ZHONGSHU_CRITIC", 10, "2026-09-22T00:00:00Z"),
            recovery=RecoveryState(
                no_progress_count=no_progress_count,
                max_no_progress=3,
                max_stuck_finding_rounds=3,
            ),
            review=ReviewState(
                revision_id="task-1:ZHONGSHU_ANALYST:2",
                findings=(self.finding,),
                zhongshu_revision_round=3,
                max_zhongshu_revision_rounds=8,
                max_item_revision_rounds=5,
                plan_hash="plan-hash",
                task_items=_items(),
                task_review_ledger=_ledger(item_rounds=3),
                last_reply_fingerprint=blocker_fingerprint((self.finding,)),
            ),
        )

    def _critic_event(self, seq: int) -> DomainEvent:
        return DomainEvent(
            "NODE_COMPLETED",
            "task-1",
            seq,
            {
                "action": "TASK_CHANGES_REQUIRED",
                "findings": [self.finding.to_dict()],
            },
            "2026-09-22T00:00:00Z",
        )

    def _resume(self) -> None:
        human = self.registry.get("HUMAN_GATE")
        decision = human.handle(
            self.snapshot.context,
            DomainEvent(
                "RESUME",
                "task-1",
                self.snapshot.context.progression.sequence + 1,
                {},
                "2026-09-22T00:00:01Z",
            ),
        )
        self.snapshot = self.reducer.apply(self.snapshot, decision)

    def _step(self) -> tuple[str, str, int]:
        context = self.snapshot.context
        critic = self.registry.get("ZHONGSHU_CRITIC")
        decision = critic.handle(
            context, self._critic_event(context.progression.sequence + 1)
        )
        self.snapshot = self.reducer.apply(self.snapshot, decision)
        if decision.transition.action == "HUMAN_GATE":
            self._resume()
        return (
            decision.transition.action,
            decision.transition.reason_code,
            self.snapshot.context.recovery.no_progress_count,
        )

    def test_freeze_with_followups_replaces_the_treadmill(self) -> None:
        observed: list[tuple[str, str, int]] = []
        for _ in range(6):
            step = self._step()
            observed.append(step)
            if step[1] == "ZHONGSHU_FREEZE_WITH_FOLLOWUPS":
                break

        sequence = [(action, reason) for action, reason, _ in observed]
        self.assertIn(
            "ZHONGSHU_FREEZE_WITH_FOLLOWUPS",
            [reason for _, reason in sequence],
            f"no freeze within {len(observed)} rounds; sequence={sequence} "
            f"final_no_progress={observed[-1][2] if observed else None}",
        )
        # The bounded exit must arrive on the third no-progress round, not
        # after an unbounded number of gate/resume cycles.
        self.assertEqual(len(observed), 3)
        self.assertEqual(
            [reason for _, reason, _ in observed[:2]], ["ZHONGSHU_STUCK_FINDING"] * 2
        )

    def test_no_progress_counter_persists_across_resume(self) -> None:
        _, _, count_one = self._step()
        self.assertEqual(count_one, 1)
        _, _, count_two = self._step()
        self.assertEqual(count_two, 2)
        action, reason, _ = self._step()

        self.assertEqual(action, "APPROVE_CRITIC")
        self.assertEqual(reason, "ZHONGSHU_FREEZE_WITH_FOLLOWUPS")


if __name__ == "__main__":
    unittest.main()
