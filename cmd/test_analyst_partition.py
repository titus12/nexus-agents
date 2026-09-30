"""The Analyst fan-out splits the Critic's open findings instead of repeating them."""

from __future__ import annotations

import unittest
from dataclasses import replace

from orchestrator.domain.context import (
    ParallelState,
    ProgressState,
    RequestState,
    ReviewState,
    TaskIdentity,
    WorkflowContext,
    ZhongshuParallelLimits,
)
from orchestrator.domain.findings import Finding
from orchestrator.domain.policies.analyst_partition import (
    expected_analyst_lenses,
    partition_active_findings,
)
from orchestrator.domain.policies.prompts import analyst_evidence_task
from orchestrator.domain.states import ZhongshuAnalystState

REVISION = "rev-1"
CANONICAL = (
    {
        "requirement_id": "req-000001",
        "statement": "canonical statement",
        "priority": "must",
        "scope": "in",
        "kind": "task",
    },
)


def _finding(number: int, **overrides: object) -> Finding:
    fields = {
        "finding_id": f"finding-{number:06d}",
        "severity": "P1",
        "group_id": "group-000001",
        "item_id": "item-000001",
        "claim": f"claim {number}",
        "evidence_targets": ({"path": f"src/mod_{number}.py", "symbol": ""},),
    }
    fields.update(overrides)
    return Finding(**fields)


def _review(findings: tuple[Finding, ...]) -> ReviewState:
    return ReviewState(
        revision_id=REVISION, requirements=CANONICAL, findings=findings
    )


def _context(
    findings: tuple[Finding, ...],
    workers: int = 3,
    requester: str = "ZHONGSHU_CRITIC",
) -> WorkflowContext:
    review = replace(_review(findings), evidence_requester=requester)
    return WorkflowContext(
        identity=TaskIdentity("task-1", "issue-1", "", "request-1"),
        progression=ProgressState("ZHONGSHU_CRITIC", 4, "2026-09-30T00:00:00Z"),
        request=RequestState(raw_request="review"),
        parallel=ParallelState(
            zhongshu=ZhongshuParallelLimits(
                enabled=True, analyst_default_workers=workers
            )
        ),
        review=review,
    )


def _dispatch(context: WorkflowContext):
    return ZhongshuAnalystState._dispatch_effect(
        context,
        "ZHONGSHU_ANALYST",
        revision_id=REVISION,
        plan_hash="plan-hash",
        canonical_requirements=CANONICAL,
    )


def _ids(findings: tuple[Finding, ...]) -> list[str]:
    return [finding.finding_id for finding in findings]


class PartitionPolicyTests(unittest.TestCase):
    def test_round_robin_over_sorted_finding_ids(self) -> None:
        findings = tuple(_finding(number) for number in (5, 1, 4, 2, 3))

        slices = partition_active_findings(_review(findings), 2)

        self.assertEqual(
            {index: _ids(rows) for index, rows in slices.items()},
            {
                1: ["finding-000001", "finding-000003", "finding-000005"],
                2: ["finding-000002", "finding-000004"],
            },
        )

    def test_every_open_finding_lands_in_exactly_one_slice(self) -> None:
        findings = tuple(_finding(number) for number in range(1, 8))

        slices = partition_active_findings(_review(findings), 3)

        assigned = [fid for rows in slices.values() for fid in _ids(rows)]
        self.assertEqual(sorted(assigned), sorted(_ids(findings)))

    def test_workers_without_a_slice_are_left_out(self) -> None:
        slices = partition_active_findings(_review((_finding(1), _finding(2))), 3)

        self.assertEqual(sorted(slices), [1, 2])

    def test_resolved_findings_are_not_partitioned(self) -> None:
        resolved = _finding(1, status="RESOLVED")

        slices = partition_active_findings(_review((resolved, _finding(2))), 3)

        self.assertEqual({i: _ids(r) for i, r in slices.items()}, {1: ["finding-000002"]})

    def test_nothing_to_split(self) -> None:
        self.assertEqual(partition_active_findings(None, 3), {})
        self.assertEqual(partition_active_findings(_review(()), 3), {})
        self.assertEqual(partition_active_findings(_review((_finding(1),)), 1), {})

    def test_partition_is_stable_across_calls(self) -> None:
        review = _review(tuple(_finding(number) for number in range(1, 6)))

        self.assertEqual(
            partition_active_findings(review, 3), partition_active_findings(review, 3)
        )

    def test_expected_lenses_follow_the_dispatched_width(self) -> None:
        self.assertEqual(expected_analyst_lenses(3, iter(())), 3)
        self.assertEqual(expected_analyst_lenses(3, iter((1,))), 1)
        self.assertEqual(expected_analyst_lenses(3, iter((1, 2, 3, 4))), 3)
        self.assertEqual(expected_analyst_lenses(1, iter((1, 2))), 1)


class PartitionDispatchTests(unittest.TestCase):
    def test_each_worker_gets_only_its_slice(self) -> None:
        context = _context(tuple(_finding(number) for number in range(1, 5)))

        effect = _dispatch(context)

        bindings = effect.payload["bindings"]
        by_worker = {
            binding["worker_id"]: [
                row["finding_id"]
                for row in binding["dispatch_context"]["evidence_demand"][
                    "active_findings"
                ]
            ]
            for binding in bindings
        }
        self.assertEqual(
            by_worker,
            {
                "zhongshu_analyst-worker-01": ["finding-000001", "finding-000004"],
                "zhongshu_analyst-worker-02": ["finding-000002"],
                "zhongshu_analyst-worker-03": ["finding-000003"],
            },
        )

    def test_demand_targets_are_limited_to_the_slice(self) -> None:
        context = _context(tuple(_finding(number) for number in range(1, 4)))

        effect = _dispatch(context)

        second = effect.payload["bindings"][1]["dispatch_context"]["evidence_demand"]
        self.assertEqual(second["evidence_targets"], [{"path": "src/mod_2.py", "symbol": ""}])

    def test_prompt_lists_only_the_slice(self) -> None:
        context = _context(tuple(_finding(number) for number in range(1, 4)))

        effect = _dispatch(context)

        prompt = effect.payload["bindings"][1]["prompt_ref"]
        self.assertIn("finding-000002", prompt)
        self.assertNotIn("finding-000001", prompt)
        self.assertNotIn("finding-000003", prompt)
        self.assertIn("your slice is 1 of them", prompt)

    def test_fewer_findings_than_workers_skips_the_idle_workers(self) -> None:
        context = _context((_finding(1),))

        effect = _dispatch(context)

        self.assertEqual(
            [binding["worker_id"] for binding in effect.payload["bindings"]],
            ["zhongshu_analyst-worker-01"],
        )

    def test_non_critic_evidence_round_keeps_every_lens(self) -> None:
        findings = tuple(_finding(number) for number in range(1, 3))
        effect = _dispatch(_context(findings, requester="ZHONGSHU_SOLVER"))

        bindings = effect.payload["bindings"]
        self.assertEqual(len(bindings), 3)
        for binding in bindings:
            demand = binding["dispatch_context"].get("evidence_demand")
            self.assertEqual(len(demand["active_findings"]), 2)

    def test_without_findings_every_worker_is_a_full_lens(self) -> None:
        effect = _dispatch(_context(()))

        self.assertEqual(len(effect.payload["bindings"]), 3)
        for binding in effect.payload["bindings"]:
            self.assertNotIn(
                "evidence_demand", binding["dispatch_context"]
            )

    def test_salvage_outside_the_wave_is_ignored(self) -> None:
        # Only worker-01 is dispatched for one finding; a salvage row of
        # worker-03 cannot stand in for anyone.
        context = _context((_finding(1),))
        context = WorkflowContext(
            identity=context.identity,
            progression=context.progression,
            request=context.request,
            parallel=context.parallel,
            review=ReviewState(
                revision_id=REVISION,
                requirements=CANONICAL,
                findings=context.review.findings,
                evidence_requester="ZHONGSHU_CRITIC",
                salvaged_worker_payloads=(
                    {
                        "worker_id": "zhongshu_analyst-worker-03",
                        "salvage_revision_id": REVISION,
                        "action": "EVIDENCE_PACKET_READY",
                    },
                ),
            ),
        )

        effect = _dispatch(context)

        self.assertEqual(len(effect.payload["bindings"]), 1)
        self.assertNotIn("salvaged_worker_payloads", effect.payload)


class EvidenceTaskPromptTests(unittest.TestCase):
    def test_unsliced_prompt_lists_every_open_finding(self) -> None:
        context = _context(tuple(_finding(number) for number in range(1, 4)))

        text = analyst_evidence_task(context)

        self.assertIn("3 open finding(s)", text)
        for number in (1, 2, 3):
            self.assertIn(f"finding-{number:06d}", text)
        self.assertNotIn("your slice", text)


if __name__ == "__main__":
    unittest.main()
