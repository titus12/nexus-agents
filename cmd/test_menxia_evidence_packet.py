"""Menxia evidence-packet round-trip tests.

Covers the convergence repair: the item Analyst's evidence reaches the shared
evidence packet (fan-in contribution + reducer merge), the item Solver's
proposal persists on the pipeline row for the item Critic, and the dispatch
contexts project both instead of re-demanding finished work.
"""

from __future__ import annotations

import unittest

from orchestrator.domain.context import (
    MenxiaItemState,
    ReviewState,
    ReviewTaskGroup,
    ReviewTaskItem,
)
from orchestrator.domain.policies.menxia import (
    apply_menxia_item_results,
    menxia_evidence_contribution,
    merge_menxia_evidence,
)
from orchestrator.domain.states import (
    _menxia_evidence_packet_update,
    _menxia_item_dispatch_context,
)
from orchestrator.runtime.agent_effects import AgentNodeJoiner
from orchestrator.runtime.nodes import WorkerResult

TASK = "task-menxia-evidence"


def make_review(**overrides) -> ReviewState:
    items = (
        ReviewTaskItem("item-001", "group-001", order=0, title="T1", objective="O1"),
        ReviewTaskItem("item-002", "group-001", order=1, title="T2", objective="O2"),
    )
    groups = (ReviewTaskGroup("group-001", ("item-001", "item-002"), order=0),)
    base = dict(
        revision_id="rev-1",
        task_items=items,
        task_groups=groups,
        max_item_revision_rounds=5,
    )
    base.update(overrides)
    return ReviewState(**base)


class TestMenxiaEvidenceContribution(unittest.TestCase):
    """menxia_evidence_contribution: payload -> packet-shaped records."""

    def test_maps_evidence_and_facts(self):
        payload = {
            "evidence": [
                {"evidence_id": "ev-1", "statement": "claim one", "source": "a.py:1"},
            ],
            "confirmed_facts": [
                {"statement": "fact two", "source": "b.py:2"},
                "plain string fact",
            ],
        }
        row = menxia_evidence_contribution("item-001", payload, "worker-01")
        self.assertIsNotNone(row)
        self.assertEqual(row["item_id"], "item-001")
        self.assertEqual(row["worker_ids"], ["worker-01"])
        conclusions = [r["conclusion"] for r in row["records"]]
        self.assertEqual(conclusions, ["claim one", "fact two", "plain string fact"])
        self.assertTrue(all(r["item_id"] == "item-001" for r in row["records"]))

    def test_forces_item_attribution_over_payload_tag(self):
        payload = {
            "evidence": [
                {"evidence_id": "ev-x", "statement": "s", "item_id": "item-999"},
            ],
        }
        row = menxia_evidence_contribution("item-001", payload, "w")
        self.assertEqual(row["records"][0]["item_id"], "item-001")

    def test_dedups_and_caps(self):
        payload = {
            "confirmed_facts": [
                {"evidence_id": f"ev-{i}", "statement": f"s{i}"} for i in range(30)
            ]
            + [{"statement": "s0"}],
        }
        row = menxia_evidence_contribution("item-001", payload, "w")
        self.assertEqual(len(row["records"]), 16)

    def test_empty_payload_returns_none(self):
        self.assertIsNone(menxia_evidence_contribution("item-001", {}, "w"))
        self.assertIsNone(menxia_evidence_contribution("item-001", None, "w"))
        self.assertIsNone(menxia_evidence_contribution("", {"evidence": []}, "w"))


class TestMergeMenxiaEvidence(unittest.TestCase):
    """merge_menxia_evidence: extend the packet, never discard zhongshu fields."""

    def _contribution(self, item_id: str, evidence_id: str) -> dict:
        return {
            "item_id": item_id,
            "worker_ids": ["worker-01"],
            "records": [
                {"evidence_id": evidence_id, "conclusion": "c", "item_id": item_id}
            ],
        }

    def test_merges_into_existing_packet(self):
        packet = {
            "phase": "ZHONGSHU",
            "objective": "keep me",
            "evidence_updates": [{"evidence_id": "old", "item_id": "item-001"}],
        }
        merged = merge_menxia_evidence(packet, [self._contribution("item-001", "new")])
        self.assertIsNotNone(merged)
        self.assertEqual(merged["phase"], "ZHONGSHU")
        self.assertEqual(merged["objective"], "keep me")
        ids = [u["evidence_id"] for u in merged["evidence_updates"]]
        self.assertEqual(ids, ["old", "new"])
        self.assertIn("worker-01", merged["worker_evidence"])

    def test_returns_none_when_nothing_new(self):
        packet = {
            "evidence_updates": [
                {"evidence_id": "ev-1", "item_id": "item-001", "conclusion": "c"}
            ]
        }
        self.assertIsNone(merge_menxia_evidence(packet, [self._contribution("item-001", "ev-1")]))

    def test_creates_skeleton_without_existing(self):
        merged = merge_menxia_evidence(None, [self._contribution("item-002", "ev-9")])
        self.assertEqual(merged["phase"], "MENXIA")
        self.assertEqual(len(merged["evidence_updates"]), 1)

    def test_empty_contributions_return_none(self):
        self.assertIsNone(merge_menxia_evidence(None, []))
        self.assertIsNone(merge_menxia_evidence(None, [{"item_id": ""}]))

    def test_same_evidence_id_rederived_dedups(self):
        packet = {"evidence_updates": []}
        rows = [
            {
                "item_id": "item-001",
                "records": [
                    {"evidence_id": "ev-1", "conclusion": "first wording"}
                ],
            },
            {
                "item_id": "item-001",
                "records": [
                    {"evidence_id": "ev-1", "conclusion": "rederived wording"}
                ],
            },
        ]
        merged = merge_menxia_evidence(packet, rows)
        ids = [u["evidence_id"] for u in merged["evidence_updates"]]
        self.assertEqual(ids, ["ev-1"])


class TestDemandFirstEvidenceSlice(unittest.TestCase):
    """_item_evidence_context: findings-named evidence outranks packet order."""

    from orchestrator.domain.states import _item_evidence_context
    from orchestrator.domain.findings import Finding

    def _review(self, packet, findings=()):
        return ReviewState(
            revision_id="rev-1",
            task_items=(
                ReviewTaskItem(
                    "item-001", "group-001", source_requirement_ids=("req-001",)
                ),
            ),
            evidence_packet=packet,
            findings=tuple(findings),
        )

    def _slice_ids(self, review):
        from orchestrator.domain.states import _item_evidence_context

        out = _item_evidence_context(review, review.task_items[0], 0)
        return [r.get("evidence_id") for r in out["records"]]

    def test_demanded_evidence_beats_cap(self):
        packet = {
            "evidence_updates": [
                {
                    "evidence_id": f"ev-fill-{i}", "conclusion": "c",
                    "requirement_id": "req-001",
                }
                for i in range(10)
            ]
            + [{"evidence_id": "ev-demanded", "conclusion": "c", "requirement_id": "req-001"}],
        }
        finding = self.Finding.from_dict(
            {
                "finding_id": "f-1",
                "severity": "P1",
                "item_id": "item-001",
                "group_id": "group-001",
                "claim": "item_evidence lacks ev-demanded",
            }
        )
        review = self._review(packet, (finding,))
        ids = self._slice_ids(review)
        self.assertEqual(len(ids), 8)
        self.assertEqual(ids[0], "ev-demanded")
        self.assertNotIn("ev-fill-9", ids)

    def test_item_addressed_outranks_requirement_level(self):
        packet = {
            "evidence_updates": [
                {"evidence_id": "ev-req", "conclusion": "c", "requirement_id": "req-001"},
                {"evidence_id": "ev-item", "conclusion": "c", "item_id": "item-001"},
            ]
        }
        ids = self._slice_ids(self._review(packet))
        self.assertEqual(ids, ["ev-item", "ev-req"])

    def test_resolved_findings_do_not_demote_packet_order(self):
        packet = {
            "evidence_updates": [
                {"evidence_id": "ev-first", "conclusion": "c", "requirement_id": "req-001"},
                {"evidence_id": "ev-second", "conclusion": "c", "requirement_id": "req-001"},
            ]
        }
        finding = self.Finding.from_dict(
            {
                "finding_id": "f-1",
                "severity": "P2",
                "item_id": "item-001",
                "group_id": "group-001",
                "status": "RESOLVED",
                "claim": "mentions ev-second",
            }
        )
        ids = self._slice_ids(self._review(packet, (finding,)))
        self.assertEqual(ids, ["ev-first", "ev-second"])


class TestSolverProposalPersistence(unittest.TestCase):
    """apply_menxia_item_results: the proposal rides the pipeline row."""

    PROPOSAL = {
        "objective": "obj",
        "approach": "app",
        "files": [f"f{i}.py" for i in range(20)],
        "changes": [f"c{i}" for i in range(20)],
        "tests": [f"t{i}" for i in range(20)],
    }

    def test_solver_forward_action_persists_bounded_proposal(self):
        review = make_review(
            menxia_items=(MenxiaItemState("item-001", "group-001", "SOLVING"),)
        )
        rows = apply_menxia_item_results(
            review,
            [
                {
                    "item_id": "item-001",
                    "action": "FEASIBLE",
                    "implementation_proposal": self.PROPOSAL,
                }
            ],
            max_rounds=5,
        )
        row = rows[0]
        self.assertEqual(row.stage, "ANALYZING")
        self.assertIsNotNone(row.last_solver_proposal)
        self.assertEqual(len(row.last_solver_proposal["changes"]), 12)
        self.assertEqual(len(row.last_solver_proposal["files"]), 12)
        self.assertEqual(row.last_solver_proposal["objective"], "obj")

    def test_critic_action_keeps_previous_proposal(self):
        seeded = MenxiaItemState(
            "item-001", "group-001", "REVIEWING",
            last_solver_proposal={"objective": "old"},
        )
        review = make_review(menxia_items=(seeded,))
        rows = apply_menxia_item_results(
            review,
            [{"item_id": "item-001", "action": "REVISE_ITEM"}],
            max_rounds=5,
        )
        self.assertEqual(rows[0].stage, "SOLVING")
        self.assertEqual(rows[0].last_solver_proposal, {"objective": "old"})

    def test_non_solver_action_does_not_persist_proposal(self):
        review = make_review(
            menxia_items=(MenxiaItemState("item-001", "group-001", "SOLVING"),)
        )
        rows = apply_menxia_item_results(
            review,
            [
                {
                    "item_id": "item-001",
                    "action": "NEEDS_MORE_EVIDENCE",
                    "implementation_proposal": self.PROPOSAL,
                }
            ],
            max_rounds=5,
        )
        self.assertIsNone(rows[0].last_solver_proposal)

    def test_state_dict_round_trip(self):
        row = MenxiaItemState(
            "item-001", "group-001", "SOLVING",
            last_solver_proposal={"objective": "obj"},
        )
        restored = MenxiaItemState.from_dict(row.to_dict())
        self.assertEqual(restored.last_solver_proposal, {"objective": "obj"})
        legacy = MenxiaItemState.from_dict(
            {"item_id": "item-001", "group_id": "group-001", "stage": "SOLVING"}
        )
        self.assertIsNone(legacy.last_solver_proposal)


class TestEvidencePacketFold(unittest.TestCase):
    """_menxia_evidence_packet_update: direct packet wins; menxia merges."""

    class _Context:
        def __init__(self, review):
            self.review = review

    def test_direct_packet_wins(self):
        payload = {
            "evidence_packet": {"phase": "ZHONGSHU"},
            "menxia_evidence": [{"item_id": "item-001", "records": []}],
        }
        packet = _menxia_evidence_packet_update(self._Context(None), payload)
        self.assertEqual(packet, {"phase": "ZHONGSHU"})

    def test_menxia_evidence_merges_review_packet(self):
        review = make_review(evidence_packet={"phase": "ZHONGSHU", "evidence_updates": []})
        payload = {
            "menxia_evidence": [
                {
                    "item_id": "item-001",
                    "worker_ids": ["w"],
                    "records": [{"evidence_id": "e", "conclusion": "c"}],
                }
            ]
        }
        packet = _menxia_evidence_packet_update(self._Context(review), payload)
        self.assertEqual(packet["phase"], "ZHONGSHU")
        self.assertEqual(len(packet["evidence_updates"]), 1)

    def test_no_evidence_keys_keeps_current_packet(self):
        self.assertIsNone(_menxia_evidence_packet_update(self._Context(None), {}))


class TestCriticDispatchContext(unittest.TestCase):
    """_menxia_item_dispatch_context: the Critic sees the item proposal."""

    def _context(self, row: MenxiaItemState):
        review = make_review(menxia_items=(row,))
        from orchestrator.domain.context import (
            ParallelState,
            MenxiaParallelLimits,
            ProgressState,
            TaskIdentity,
            WorkflowContext,
        )

        return WorkflowContext(
            identity=TaskIdentity(TASK, "issue-1", "proj", "req-1"),
            progression=ProgressState("MENXIA_ITEM_CRITIC", 3, "2026-09-22T00:00:00Z"),
            review=review,
            parallel=ParallelState(
                menxia=MenxiaParallelLimits(enabled=True, max_concurrent_groups=1, max_concurrent_items=1)
            ),
        )

    def test_critic_gets_persisted_proposal(self):
        row = MenxiaItemState(
            "item-001", "group-001", "REVIEWING",
            last_solver_proposal={"objective": "obj", "changes": []},
        )
        context = _menxia_item_dispatch_context(
            self._context(row), "MENXIA_ITEM_CRITIC",
            item_id="item-001", group_id="group-001",
            revision_id="rev-1", plan_hash="h", row=row,
        )
        self.assertEqual(context["item_solver_proposal"]["objective"], "obj")
        keys = [i["key"] for i in context["envelope"]["ingredients"]]
        self.assertIn("review.item_solver_proposal", keys)

    def test_critic_without_proposal_has_no_key(self):
        row = MenxiaItemState("item-001", "group-001", "REVIEWING")
        context = _menxia_item_dispatch_context(
            self._context(row), "MENXIA_ITEM_CRITIC",
            item_id="item-001", group_id="group-001",
            revision_id="rev-1", plan_hash="h", row=row,
        )
        self.assertNotIn("item_solver_proposal", context)
        keys = [i["key"] for i in context["envelope"]["ingredients"]]
        self.assertNotIn("review.item_solver_proposal", keys)

    def test_solver_context_never_carries_proposal(self):
        row = MenxiaItemState(
            "item-001", "group-001", "SOLVING",
            last_solver_proposal={"objective": "obj"},
        )
        context = _menxia_item_dispatch_context(
            self._context(row), "MENXIA_ITEM_SOLVER",
            item_id="item-001", group_id="group-001",
            revision_id="rev-1", plan_hash="h", row=row,
        )
        self.assertNotIn("item_solver_proposal", context)


class TestJoinerEvidenceCarry(unittest.TestCase):
    """AgentNodeJoiner._join_menxia: evidence and proposal ride the fold."""

    def _joiner(self, state: str, item_id: str) -> AgentNodeJoiner:
        return AgentNodeJoiner(
            "node:1", task_id=TASK, state=state, sequence=3,
            revision_id="rev-1", dispatch_mode="menxia_item_pipeline",
            menxia_stage_census={item_id: "ANALYZING" if state == "MENXIA_ITEM_ANALYST" else "SOLVING"},
            binding_contexts={"worker-01": {"item_id": item_id, "group_id": "group-001"}},
        )

    def test_analyst_wave_carries_evidence_contribution(self):
        joiner = self._joiner("MENXIA_ITEM_ANALYST", "item-001")
        result = joiner.join(
            (
                WorkerResult(
                    "worker-01", "SUCCEEDED", None,
                    result_payload={
                        "action": "EVIDENCE_SUFFICIENT",
                        "confirmed_facts": [{"statement": "s", "source": "a.py:1"}],
                    },
                ),
            )
        )
        contributions = result.aggregate["menxia_evidence"]
        self.assertEqual(len(contributions), 1)
        self.assertEqual(contributions[0]["item_id"], "item-001")
        self.assertEqual(contributions[0]["worker_ids"], ["worker-01"])

    def test_analyst_wave_without_evidence_omits_key(self):
        joiner = self._joiner("MENXIA_ITEM_ANALYST", "item-001")
        result = joiner.join(
            (WorkerResult("worker-01", "SUCCEEDED", None, result_payload={"action": "EVIDENCE_SUFFICIENT"}),)
        )
        self.assertNotIn("menxia_evidence", result.aggregate)

    def test_solver_wave_row_carries_proposal(self):
        joiner = self._joiner("MENXIA_ITEM_SOLVER", "item-001")
        result = joiner.join(
            (
                WorkerResult(
                    "worker-01", "SUCCEEDED", None,
                    result_payload={
                        "action": "FEASIBLE",
                        "implementation_proposal": {"objective": "obj", "changes": []},
                    },
                ),
            )
        )
        row = result.aggregate["menxia_item_results"][0]
        self.assertEqual(row["implementation_proposal"]["objective"], "obj")


if __name__ == "__main__":
    unittest.main()
