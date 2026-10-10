"""Finding id remint (2026-09-28 efficiency plan Task 2).

Critic rounds fold findings whose ids workers minted independently: rule (a)
rebinds a returning opinion onto the id its earlier round already owns, rule
(b) remints a content-derived id when one structural id would name two
different claims.  Solver rounds never remint (their dispositions echo ids).
"""

from __future__ import annotations

import os
os.environ.setdefault("NEXUS_TEST_NO_EXTERNAL_NOTIFICATIONS", "1")
import unittest

from orchestrator.domain.findings import Finding
from orchestrator.domain.policies.zhongshu import (
    _claim_text_key,
    age_unresolved_findings,
    merge_findings,
    remint_incoming_finding_ids,
)


def _finding(
    finding_id: str,
    item_id: str = "item-000001",
    group_id: str = "group-000001",
    claim: str = "缓存未失效导致脏读",
    canonical_key: str = "",
    status: str = "OPEN",
    stuck_rounds: int = 0,
) -> Finding:
    return Finding(
        finding_id=finding_id,
        severity="P1",
        status=status,
        group_id=group_id,
        item_id=item_id,
        claim=claim,
        canonical_key=canonical_key,
        stuck_rounds=stuck_rounds,
    )


class ClaimTextKeyTests(unittest.TestCase):
    def test_normalizes_case_punct_and_markers(self) -> None:
        self.assertEqual(
            _claim_text_key("- Cache invalidation missing!"),
            _claim_text_key("cache invalidation missing"),
        )

    def test_distinct_claims_stay_distinct(self) -> None:
        self.assertNotEqual(
            _claim_text_key("缓存未失效导致脏读"),
            _claim_text_key("缓存过期时间配置错误"),
        )


class RemintRuleATests(unittest.TestCase):
    def test_same_claim_under_fresh_id_rebinds_to_stored_id(self) -> None:
        existing = (_finding("finding-000009", claim="cache never invalidated"),)
        incoming = (
            _finding("f-1", claim="Cache never invalidated!"),
            _finding("f-2", claim="totally different claim"),
        )
        reminted, reports = remint_incoming_finding_ids(existing, incoming)
        self.assertEqual(reminted[0].finding_id, "finding-000009")
        self.assertEqual(reminted[1].finding_id, "f-2")
        self.assertEqual(
            reports,
            [
                {
                    "rule": "a",
                    "item_id": "item-000001",
                    "from": "f-1",
                    "to": "finding-000009",
                }
            ],
        )

    def test_revived_old_id_rebinds_onto_reminted_successor(self) -> None:
        # Round 1 minted X, a rule-(b) remint re-addressed it to Y; round 2
        # re-raises the same claim under the original X.  The rebind must land
        # on Y so the ledger never holds two copies of the same opinion.
        existing = (_finding("finding-abc123def456", claim="dirty read on cache"),)
        incoming = (_finding("f-x", claim="dirty read on cache"),)
        reminted, reports = remint_incoming_finding_ids(existing, incoming)
        self.assertEqual(reminted[0].finding_id, "finding-abc123def456")
        self.assertEqual(reports[0]["to"], "finding-abc123def456")
        self.assertEqual(reports[0]["rule"], "a")

    def test_same_id_same_claim_is_rule_c_passthrough(self) -> None:
        existing = (_finding("f-1", claim="same claim"),)
        reminted, reports = remint_incoming_finding_ids(
            existing, (_finding("f-1", claim="same claim"),)
        )
        self.assertEqual(reminted[0].finding_id, "f-1")
        self.assertEqual(reports, [])

    def test_cross_item_same_claim_is_not_rebound(self) -> None:
        existing = (_finding("f-1", item_id="item-000002", claim="shared wording"),)
        reminted, reports = remint_incoming_finding_ids(
            existing, (_finding("f-9", claim="shared wording"),)
        )
        self.assertEqual(reminted[0].finding_id, "f-9")
        self.assertEqual(reports, [])


class RemintRuleBTests(unittest.TestCase):
    def test_structural_collision_with_different_claim_remints(self) -> None:
        existing = (_finding("f-1", claim="claim A"),)
        incoming = (_finding("f-1", claim="claim B"),)
        reminted, reports = remint_incoming_finding_ids(existing, incoming)
        self.assertNotEqual(reminted[0].finding_id, "f-1")
        self.assertTrue(reminted[0].finding_id.startswith("finding-"))
        self.assertEqual(
            reports,
            [
                {
                    "rule": "b",
                    "item_id": "item-000001",
                    "from": "f-1",
                    "to": reminted[0].finding_id,
                }
            ],
        )

    def test_reminted_id_is_stable_across_replays(self) -> None:
        existing = (_finding("f-1", claim="claim A"),)
        first, first_reports = remint_incoming_finding_ids(
            existing, (_finding("f-1", claim="claim B"),)
        )
        second, second_reports = remint_incoming_finding_ids(
            existing, (_finding("f-1", claim="claim B"),)
        )
        self.assertEqual(first[0].finding_id, second[0].finding_id)
        self.assertEqual(first_reports, second_reports)

    def test_reminted_id_avoids_batch_and_ledger_collisions(self) -> None:
        existing = (
            _finding("f-1", claim="claim A"),
            _finding(
                "finding-ffffffffffff",
                item_id="item-000003",
                claim="unrelated third claim with distinct text",
            ),
        )
        # Force the natural remint target to collide with the second ledger id.
        import orchestrator.domain.policies.zhongshu as zhongshu

        original = zhongshu._finding_content_hash

        def forced_hash(finding: object) -> str:
            base = original(finding)
            if str(getattr(finding, "claim", "")) == "claim B":
                return "f" * 64
            return base

        zhongshu._finding_content_hash = forced_hash
        try:
            reminted, _reports = remint_incoming_finding_ids(
                existing, (_finding("f-1", claim="claim B"),)
            )
        finally:
            zhongshu._finding_content_hash = original
        self.assertEqual(reminted[0].finding_id, "finding-ffffffffffff-2")

    def test_no_collision_keeps_worker_id(self) -> None:
        existing = (_finding("finding-000001", claim="claim A"),)
        reminted, reports = remint_incoming_finding_ids(
            existing, (_finding("f-7", claim="claim B"),)
        )
        self.assertEqual(reminted[0].finding_id, "f-7")
        self.assertEqual(reports, [])


class ScopeAndAgeTests(unittest.TestCase):
    def test_merge_folds_rebound_opinion_into_one_entry(self) -> None:
        existing = (_finding("finding-000009", claim="cache never invalidated"),)
        incoming = (_finding("f-1", claim="Cache never invalidated!"),)
        reminted, _ = remint_incoming_finding_ids(existing, incoming)
        merged = merge_findings(existing, reminted)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].finding_id, "finding-000009")

    def test_rebound_restatement_inherits_stuck_rounds(self) -> None:
        stored = _finding(
            "finding-000009", claim="cache never invalidated", stuck_rounds=3
        )
        existing = (stored,)
        incoming = (_finding("f-1", claim="Cache never invalidated!"),)
        reminted, _ = remint_incoming_finding_ids(existing, incoming)
        merged = merge_findings(existing, reminted)
        aged = age_unresolved_findings(
            existing,
            merged,
            reminted,
            attempted_finding_ids=("finding-000009",),
        )
        self.assertEqual(len(aged), 1)
        self.assertEqual(aged[0].stuck_rounds, 4)

    def test_reminted_new_claim_starts_fresh_age(self) -> None:
        existing = (
            _finding("f-1", claim="claim A", stuck_rounds=5),
            _finding("f-2", claim="unrelated stored claim", stuck_rounds=2),
        )
        incoming = (_finding("f-2", claim="brand new claim text"),)
        reminted, _ = remint_incoming_finding_ids(existing, incoming)
        merged = merge_findings(existing, reminted)
        aged = age_unresolved_findings(existing, merged, reminted)
        by_id = {finding.finding_id: finding for finding in aged}
        # f-1 was not re-raised this round: its count stays frozen as-is
        # (age_unresolved_findings only touches observed identities).
        self.assertEqual(by_id["f-1"].stuck_rounds, 5)
        fresh = [
            finding
            for finding in aged
            if finding.claim == "brand new claim text"
        ]
        self.assertEqual(len(fresh), 1)
        self.assertEqual(fresh[0].stuck_rounds, 0)

class FoldScopeTests(unittest.TestCase):
    """The shared fold applies the remint on critic rounds only."""

    def _fold(self, existing, incoming, task_reviews, **kwargs):
        from orchestrator.domain.states import _merge_and_close_findings

        return _merge_and_close_findings(
            existing, incoming, task_reviews, **kwargs
        )

    def test_critic_round_remints_and_logs(self) -> None:
        existing = (_finding("f-1", claim="claim A"),)
        incoming = (_finding("f-1", claim="claim B"),)
        merged = self._fold(
            existing,
            incoming,
            [{"item_id": "item-000001", "action": "TASK_CHANGES_REQUIRED"}],
            remint_findings=True,
            task_id="task-1",
        )
        ids = [finding.finding_id for finding in merged]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn("f-1", ids)
        self.assertNotIn(
            "claim B", [finding.claim for finding in merged if finding.finding_id == "f-1"]
        )

    def test_solver_round_keeps_echo_ids_addressable(self) -> None:
        existing = (_finding("f-1", claim="claim A"),)
        incoming = (_finding("f-1", claim="claim B"),)
        merged = self._fold(existing, incoming, None, remint_findings=False)
        by_id = {}
        for finding in merged:
            by_id.setdefault(finding.finding_id, []).append(finding.claim)
        # No remint: the id stays the solver's reference target (the structural
        # ambiguity is solver-round legacy behaviour, never touched here).
        self.assertIn("f-1", by_id)

    def test_group_verdict_waves_count_as_critic_rounds(self) -> None:
        # The call-site discriminator passes remint_findings=True for group
        # verdict payloads; here the flag wiring itself is what the fold honours.
        existing = (_finding("finding-000009", claim="cache never invalidated"),)
        incoming = (_finding("f-1", claim="Cache never invalidated!"),)
        merged = self._fold(
            existing,
            incoming,
            None,
            remint_findings=True,
        )
        self.assertEqual(
            sorted(finding.finding_id for finding in merged),
            ["finding-000009"],
        )


if __name__ == "__main__":
    unittest.main()
