from __future__ import annotations

import unittest
from dataclasses import replace

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
from orchestrator.domain.states import ZhongshuCriticState
from orchestrator.runtime.reducer import LinearContextReducer
from orchestrator.runtime.repository import WorkflowSnapshot
from orchestrator.zhongshu_review import (
    _blocking_findings,
    consolidate_finding_observations,
    finding_semantic_key,
)


def _observation(**overrides) -> dict:
    base = {
        "finding_id": "finding-000001",
        "scope": "item",
        "group_id": "group-000001",
        "item_id": "item-000001",
        "category": "acceptance",
        "target": "item-000001",
        "severity": "P1",
        "status": "OPEN",
        "claim": "the acceptance signal does not name the measured window",
        "required_action": "name the measurement window",
    }
    base.update(overrides)
    return base


class StableFindingIdentityTests(unittest.TestCase):
    """Re-wording the same issue must not mint a new finding identity."""

    def test_reworded_claim_keeps_the_identity(self) -> None:
        first = _observation()
        second = _observation(
            claim="the acceptance criterion never states which window is measured",
            required_action="state the window explicitly",
        )

        self.assertEqual(finding_semantic_key(first), finding_semantic_key(second))

    def test_worker_local_id_does_not_change_the_identity(self) -> None:
        first = _observation(finding_id="finding-w02-000001")
        second = _observation(finding_id="finding-000042")

        self.assertEqual(finding_semantic_key(first), finding_semantic_key(second))

    def test_different_item_is_a_different_identity(self) -> None:
        first = _observation(item_id="item-000001")
        second = _observation(item_id="item-000002", target="item-000002")

        self.assertNotEqual(finding_semantic_key(first), finding_semantic_key(second))

    def test_reworded_category_keeps_the_identity(self) -> None:
        # The category is the critic's re-wordable label for the same complaint
        # ("general" -> "boundary"); a re-picked label must not mint a second
        # blocker for one issue (live run task-20260918-30ec74 churned the
        # blocker count this way until the per-item ratchet fired).
        first = _observation()
        second = _observation(category="boundary")

        self.assertEqual(finding_semantic_key(first), finding_semantic_key(second))

    def test_anchored_and_unanchored_findings_do_not_collapse(self) -> None:
        anchored = _observation()
        anonymous = _observation(
            finding_id="finding-anon",
            scope="global",
            group_id="",
            item_id="",
            category="",
            target="",
        )

        self.assertNotEqual(
            finding_semantic_key(anchored), finding_semantic_key(anonymous)
        )

    def test_anonymous_findings_keep_their_own_identity(self) -> None:
        left = _observation(
            finding_id="finding-a", scope="global", group_id="", item_id="",
            category="", target="",
        )
        right = _observation(
            finding_id="finding-b", scope="global", group_id="", item_id="",
            category="", target="",
        )

        self.assertNotEqual(finding_semantic_key(left), finding_semantic_key(right))


class SeverityFollowsCurrentRoundTests(unittest.TestCase):
    """Severity follows this round's judgment instead of the historical worst."""

    def test_downgraded_severity_stops_blocking(self) -> None:
        # One worker's P1 used to pin the finding blocking forever, even after
        # the critic re-raised the same issue as P2 in every later round.
        first = _observation(severity="P1")
        previous = {
            "finding-000001": {
                **first,
                "canonical_key": finding_semantic_key(first),
            }
        }
        downgraded = _observation(severity="P2", claim="re-raised milder")
        observations = {finding_semantic_key(downgraded): [downgraded]}

        ledger, _ = consolidate_finding_observations(
            observations, previous, quorum=1
        )

        (finding,) = ledger.values()
        self.assertEqual(finding["severity"], "P2")
        self.assertEqual(_blocking_findings([finding]), [])

    def test_worst_severity_within_one_round_still_wins(self) -> None:
        first = _observation(severity="P1", worker_id="w-1")
        second = _observation(severity="P2", worker_id="w-2")
        key = finding_semantic_key(first)

        ledger, _ = consolidate_finding_observations(
            {key: [first, second]}, {}, quorum=1
        )

        (finding,) = ledger.values()
        self.assertEqual(finding["severity"], "P1")


class AcceptorRiskIsNotBlockingTests(unittest.TestCase):
    """A P1 the Critic explicitly accepted must stop blocking the freeze."""

    def test_open_p1_blocks(self) -> None:
        self.assertEqual(len(_blocking_findings([_observation()])), 1)

    def test_resolved_p1_does_not_block(self) -> None:
        self.assertEqual(
            _blocking_findings([_observation(status="RESOLVED")]), []
        )

    def test_accepted_risk_does_not_block(self) -> None:
        finding = _observation(decision="ACCEPTED_RISK")

        self.assertEqual(_blocking_findings([finding]), [])

    def test_wont_fix_does_not_block(self) -> None:
        self.assertEqual(
            _blocking_findings([_observation(status="WONT_FIX")]), []
        )

    def test_open_p2_does_not_block(self) -> None:
        self.assertEqual(
            _blocking_findings([_observation(severity="P2")]), []
        )


def _review_event(action: str, records: tuple[dict, ...]) -> DomainEvent:
    return DomainEvent(
        "NODE_COMPLETED",
        "task-1",
        6,
        {"action": action, "task_reviews": list(records)},
        "2026-09-15T00:00:00Z",
    )


def _record(item_id: str, action: str, *, task_hash: str = "th") -> dict:
    return {
        "review_job_id": f"zhongshu:rev:group-000001:{item_id}",
        "group_id": "group-000001",
        "item_id": item_id,
        "action": action,
        "reviewed_task_hash": task_hash,
        "reviewed_dependency_hash": "dh",
    }


class PerTaskStallEscalationTests(unittest.TestCase):
    """One endlessly rejected task cannot hide behind the graph budget."""

    def setUp(self) -> None:
        self.state = ZhongshuCriticState()
        self.reducer = LinearContextReducer()

    def _context(self, *records: ReviewTaskRecord, max_item_rounds: int = 3) -> WorkflowContext:
        return WorkflowContext(
            identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
            progression=ProgressState("ZHONGSHU_CRITIC", 6, "2026-09-15T00:00:00Z"),
            review=ReviewState(
                revision_id="rev-1",
                plan={"items": [{"item_id": "item-000001"}, {"item_id": "item-000002"}]},
                task_items=(
                    ReviewTaskItem("item-000001", "group-000001", order=0),
                    ReviewTaskItem("item-000002", "group-000001", order=1),
                ),
                findings=(
                    Finding(
                        finding_id="f-1",
                        severity="P1",
                        status="OPEN",
                        group_id="group-000001",
                        item_id="item-000001",
                        claim="blocking claim",
                        required_action="fix it",
                    ),
                ),
                task_review_ledger=records,
                zhongshu_revision_round=1,
                max_zhongshu_revision_rounds=8,
                item_revision_round=0,
                max_item_revision_rounds=max_item_rounds,
                plan_hash="plan-hash",
            ),
        )

    def _snapshot(self, context: WorkflowContext) -> WorkflowSnapshot:
        return WorkflowSnapshot("task-1", context, 0)

    def test_repeated_rejection_counts_up(self) -> None:
        context = self._context(
            ReviewTaskRecord(
                item_id="item-000001",
                status="CHANGES_REQUIRED",
                task_hash="th",
                changes_rounds=1,
            )
        )
        decision = self.state.handle(
            context, _review_event("REQUEST_SOLVER_REVISION", (_record("item-000001", "TASK_CHANGES_REQUIRED"),))
        )

        after = self.reducer.apply(self._snapshot(context), decision)
        record = {
            r.item_id: r for r in after.context.review.task_review_ledger
        }["item-000001"]
        self.assertEqual(record.changes_rounds, 2)

    def test_approval_resets_the_rejection_counter(self) -> None:
        context = self._context(
            ReviewTaskRecord(
                item_id="item-000001",
                status="CHANGES_REQUIRED",
                task_hash="th",
                changes_rounds=2,
            )
        )
        decision = self.state.handle(
            context, _review_event("TASK_APPROVED", (_record("item-000001", "TASK_APPROVED"),))
        )

        after = self.reducer.apply(self._snapshot(context), decision)
        record = {
            r.item_id: r for r in after.context.review.task_review_ledger
        }["item-000001"]
        self.assertEqual(record.status, "APPROVED")
        self.assertEqual(record.changes_rounds, 0)

    def test_stalled_task_escalates_to_a_human(self) -> None:
        context = self._context(
            ReviewTaskRecord(
                item_id="item-000001",
                status="CHANGES_REQUIRED",
                task_hash="th",
                changes_rounds=3,
            )
        )
        decision = self.state.handle(
            context, _review_event("REQUEST_SOLVER_REVISION", ())
        )

        self.assertEqual(decision.transition.action, "HUMAN_GATE")
        self.assertEqual(decision.transition.reason_code, "ZHONGSHU_ITEM_STALLED")
        after = self.reducer.apply(self._snapshot(context), decision)
        self.assertEqual(after.context.progression.state, "HUMAN_GATE")

    def test_stall_escalation_can_be_disabled(self) -> None:
        context = self._context(
            ReviewTaskRecord(
                item_id="item-000001",
                status="CHANGES_REQUIRED",
                task_hash="th",
                changes_rounds=9,
            ),
            max_item_rounds=0,
        )
        decision = self.state.handle(
            context, _review_event("REQUEST_SOLVER_REVISION", ())
        )

        self.assertEqual(decision.transition.action, "REQUEST_SOLVER_REVISION")


class AnApprovedTaskIsNeverStalledTests(unittest.TestCase):
    def test_approved_records_are_ignored(self) -> None:
        from orchestrator.domain.policies.zhongshu import stalled_item_ids

        ledger = (
            ReviewTaskRecord(
                item_id="item-000001",
                status="APPROVED",
                task_hash="th",
                changes_rounds=5,
            ),
        )

        self.assertEqual(stalled_item_ids(ledger, 3), ())


def _dispatch_context(index: int) -> dict:
    return {
        "zhongshu_dispatch_mode": "task_review",
        "review_job_id": f"zhongshu:rev-1:group-{index:02d}:item-{index:02d}",
        "revision_id": "rev-1",
        "group_id": f"group-{index:02d}",
        "item_id": f"item-{index:02d}",
        "task_hash": f"task-hash-{index:02d}",
        "dependency_hash": f"dep-hash-{index:02d}",
    }


def _worker_result(index: int, action: str, findings: list) -> "WorkerResult":
    from orchestrator.runtime.nodes import WorkerResult

    return WorkerResult(
        f"zhongshu_critic-worker-{index:02d}",
        "SUCCEEDED",
        None,
        result_payload={
            "action": action,
            "revision_id": "rev-1",
            "plan_hash": "plan-hash",
            "reviewed_plan_hash": "plan-hash",
            "group_id": f"group-{index:02d}",
            "item_id": f"item-{index:02d}",
            "reviewed_task_hash": f"task-hash-{index:02d}",
            "reviewed_dependency_hash": f"dep-hash-{index:02d}",
            "worker_id": f"zhongshu_critic-worker-{index:02d}",
            "findings": findings,
            "review_checks": {},
        },
    )


def _join(*results: "WorkerResult"):
    from orchestrator.runtime.agent_effects import AgentNodeJoiner
    from orchestrator.zhongshu_review_queue import queue_from_dispatch_contexts

    queue = queue_from_dispatch_contexts(
        [_dispatch_context(i) for i in (1, 2, 3)], "rev-1", "plan-hash"
    )
    joiner = AgentNodeJoiner(
        "node:1",
        task_id="task-1",
        state="ZHONGSHU_CRITIC",
        revision_id="rev-1",
        plan_hash="plan-hash",
        task_review_queue=queue,
    )
    return joiner.join(tuple(results))


class AcceptedRiskUnblocksTheFreezeTests(unittest.TestCase):
    """An explicitly accepted P1 must let the graph freeze."""

    def test_accepted_risk_finding_does_not_block_the_freeze(self) -> None:
        joined = _join(
            _worker_result(1, "TASK_APPROVED", []),
            _worker_result(
                2,
                "TASK_APPROVED",
                [
                    {
                        "finding_id": "f-1",
                        "severity": "P1",
                        "decision": "ACCEPTED_RISK",
                        "category": "acceptance",
                        "target": "item-02",
                        "claim": "follow-up measurement",
                        "evidence_strength": "inference",
                    }
                ],
            ),
            _worker_result(3, "TASK_APPROVED", []),
        )

        self.assertEqual(joined.aggregate["action"], "APPROVE_CRITIC")
        self.assertEqual(joined.aggregate["active_p0_p1_finding_ids"], [])
        (finding,) = joined.aggregate["findings"]
        self.assertEqual(finding["status"], "DEFERRED")

    def test_open_p1_finding_still_blocks_the_freeze(self) -> None:
        joined = _join(
            _worker_result(1, "TASK_APPROVED", []),
            _worker_result(
                2,
                "TASK_CHANGES_REQUIRED",
                [
                    {
                        "finding_id": "f-1",
                        "severity": "P1",
                        "status": "OPEN",
                        "category": "acceptance",
                        "target": "item-02",
                        "claim": "follow-up measurement",
                        "evidence_strength": "inference",
                    }
                ],
            ),
            _worker_result(3, "TASK_APPROVED", []),
        )

        self.assertEqual(joined.aggregate["action"], "REQUEST_SOLVER_REVISION")
        self.assertEqual(joined.aggregate["active_p0_p1_finding_ids"], ["f-1"])


class RewordedFindingStaysOneLedgerEntryTests(unittest.TestCase):
    """A re-worded re-raise must fold onto the existing ledger entry."""

    def _consolidate(self, observation: dict, previous: dict | None):
        from orchestrator.zhongshu_review import (
            consolidate_finding_observations,
            finding_semantic_key,
        )

        return consolidate_finding_observations(
            {finding_semantic_key(observation): [observation]},
            previous,
            quorum=1,
        )

    def test_reworded_observation_reuses_the_ledger_entry(self) -> None:
        first = _observation(
            finding_id="finding-000001",
            claim="the acceptance signal does not name the measured window",
        )
        ledger, _ = self._consolidate(first, None)
        (canonical_id,) = ledger.keys()

        second = _observation(
            finding_id="finding-w02-000001",
            claim="no measurement window is stated in the acceptance criterion",
        )
        merged, disagreements = self._consolidate(second, ledger)

        self.assertEqual(list(merged), [canonical_id])
        self.assertEqual(len(merged), 1)
        self.assertEqual(disagreements, [])

    def test_distinct_categories_on_one_item_stay_one_entry(self) -> None:
        # The category is a re-wordable label, not structural identity: the
        # per-task review reasons about (scope, item, affected refs).  Two
        # category labels for one item's complaint fold into one ledger entry
        # and both observations stay visible on it.
        first = _observation(category="acceptance")
        ledger, _ = self._consolidate(first, None)

        merged, _ = self._consolidate(
            _observation(finding_id="finding-000002", category="boundary"), ledger
        )

        self.assertEqual(len(merged), 1)
        (entry,) = merged.values()
        # The ledger entry keeps the original canonical id; the observations
        # field carries this round's snapshot (the latest observation wins).
        self.assertEqual(entry["finding_id"], "finding-000001")
        self.assertEqual(entry["observations"][0]["finding_id"], "finding-000002")


class CrossLanguageRestatementRebindTests(unittest.TestCase):
    """The ingest must unify a language-switched re-raise onto the stored id.

    Live run task-20260920-bbd659: the critic alternated Chinese and English
    across rounds and minted seven finding ids for three real issues, which
    reset the stuck counters and inflated the blocker count.  The orchestrator
    now stamps the identity mechanically instead of trusting the echo rule.
    """

    def _existing(self, **overrides) -> Finding:
        fields = dict(
            finding_id="finding-canonical",
            severity="P1",
            status="OPEN",
            group_id="group-000001",
            item_id="item-000001",
            claim=(
                "要求将 AGENT_SOLVER_ID 扩展为组并支持 round-robin 负载均衡，"
                "但未定义 JsonSchema 结构与 fallback 语义"
            ),
            required_action="补充 JsonSchema 契约与失效行为",
            stuck_rounds=2,
        )
        fields.update(overrides)
        return Finding(**fields)

    def _incoming(self, **overrides) -> Finding:
        fields = dict(
            finding_id="finding-minted",
            severity="P1",
            status="OPEN",
            group_id="group-000001",
            item_id="item-000001",
            claim=(
                "AGENT_SOLVER_ID group expansion lacks a JsonSchema contract "
                "and fallback semantics"
            ),
            required_action="define the schema contract",
        )
        fields.update(overrides)
        return Finding(**fields)

    def test_cross_language_restatement_rebinds_to_the_stored_id(self) -> None:
        from orchestrator.domain.policies.zhongshu import (
            rebind_restatement_findings,
        )

        existing = self._existing()
        incoming = self._incoming()

        rebound, rebinds = rebind_restatement_findings((existing,), (incoming,))

        self.assertEqual(rebound[0].finding_id, "finding-canonical")
        self.assertEqual(len(rebinds), 1)
        self.assertEqual(rebinds[0]["from"], "finding-minted")
        self.assertEqual(rebinds[0]["to"], "finding-canonical")
        self.assertEqual(rebinds[0]["shared_anchor"], "AGENT_SOLVER_ID")

    def test_cross_language_without_a_shared_anchor_stays_separate(self) -> None:
        from orchestrator.domain.policies.zhongshu import (
            rebind_restatement_findings,
        )

        existing = self._existing(
            claim="未规定并发观测的计量单位与基线来源，无法形成稳定判定",
        )
        incoming = self._incoming(
            claim="the concurrency signal lacks a measurement unit and baseline",
        )

        rebound, rebinds = rebind_restatement_findings((existing,), (incoming,))

        self.assertEqual(rebound[0].finding_id, "finding-minted")
        self.assertEqual(rebinds, [])

    def test_same_language_rewording_is_not_rebound(self) -> None:
        # Inside one language the similarity gate and the echo instruction
        # stay responsible; the rebind must not grab prose paraphrases.
        from orchestrator.domain.policies.zhongshu import (
            rebind_restatement_findings,
        )

        existing = self._existing()
        incoming = self._incoming(
            claim="要求将 AGENT_SOLVER_ID 扩展为组，但 JsonSchema 契约与 fallback 语义仍未定义",
        )

        rebound, rebinds = rebind_restatement_findings((existing,), (incoming,))

        self.assertEqual(rebound[0].finding_id, "finding-minted")
        self.assertEqual(rebinds, [])

    def test_resolved_finding_is_not_a_rebind_target(self) -> None:
        from orchestrator.domain.policies.zhongshu import (
            rebind_restatement_findings,
        )

        existing = self._existing(status="RESOLVED")

        rebound, rebinds = rebind_restatement_findings(
            (existing,), (self._incoming(),)
        )

        self.assertEqual(rebound[0].finding_id, "finding-minted")
        self.assertEqual(rebinds, [])

    def test_rebind_lets_the_stuck_counter_accrue_on_one_id(self) -> None:
        from orchestrator.domain.states import _merge_and_close_findings

        existing = self._existing()
        incoming = self._incoming()

        merged = _merge_and_close_findings((existing,), (incoming,), [])

        self.assertEqual(len(merged), 1)
        (finding,) = merged
        self.assertEqual(finding.finding_id, "finding-canonical")
        self.assertEqual(finding.stuck_rounds, 3)


class StuckFindingEscalationTests(unittest.TestCase):
    """A stable identity is what lets the stuck counter finally accumulate."""

    def test_re_raised_same_finding_escalates_to_a_human(self) -> None:
        state = ZhongshuCriticState()
        reducer = LinearContextReducer()
        finding = Finding(
            finding_id="f-1",
            severity="P1",
            status="OPEN",
            group_id="group-000001",
            item_id="item-000001",
            claim="boundary unclear",
            required_action="clarify the boundary",
            stuck_rounds=2,
        )
        context = WorkflowContext(
            identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
            progression=ProgressState("ZHONGSHU_CRITIC", 6, "2026-09-15T00:00:00Z"),
            review=ReviewState(
                revision_id="rev-1",
                findings=(finding,),
                zhongshu_revision_round=1,
                max_zhongshu_revision_rounds=8,
                plan_hash="plan-hash",
            ),
        )
        event = DomainEvent(
            "NODE_COMPLETED",
            "task-1",
            6,
            {
                "action": "REQUEST_SOLVER_REVISION",
                "findings": [finding.to_dict()],
            },
            "2026-09-15T00:00:00Z",
        )

        decision = state.handle(context, event)

        self.assertEqual(decision.transition.action, "HUMAN_GATE")
        self.assertEqual(decision.transition.reason_code, "ZHONGSHU_STUCK_FINDING")
        after = reducer.apply(WorkflowSnapshot("task-1", context, 0), decision)
        self.assertEqual(after.context.progression.state, "HUMAN_GATE")


if __name__ == "__main__":
    unittest.main()
