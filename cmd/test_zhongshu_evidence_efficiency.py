from __future__ import annotations

import copy
from dataclasses import replace
import unittest

from orchestrator.zhongshu_parallel import merge_zhongshu_evidence_packet
from orchestrator.runtime.agent_effects import AgentNodeJoiner
from orchestrator.runtime.nodes import WorkerResult, _with_reply_feedback
from orchestrator.domain.policies.zhongshu import project_evidence_record
from test_analyst_wave_salvage import CANONICAL, REVISION, _joiner, _worker_payload
from test_worker_reply_reask import _binding, _context


class EvidenceEfficiencyTests(unittest.TestCase):
    def test_supplement_preserves_scoped_evidence_and_conflicting_observations(self):
        old = {"phase": "ZHONGSHU", "requirements": CANONICAL,
               "plan_revision_id": "r1", "evidence_packet_version": 1,
               "worker_evidence": {"w1": {"lens": "original"}},
               "evidence_updates": [
                   {"evidence_id": "ev-1", "requirement_id": "req-1", "conclusion": "old"},
                   {"evidence_id": "ev-1", "requirement_id": "req-2", "conclusion": "peer"}]}
        new = {"phase": "ZHONGSHU", "requirements": CANONICAL,
               "plan_revision_id": "r2", "evidence_packet_version": 1,
               "worker_evidence": {"w2": {"lens": "supplement"}},
               "evidence_updates": [
                   {"evidence_id": "ev-1", "requirement_id": "req-1", "conclusion": "new"},
                   {"evidence_id": "ev-2", "requirement_id": "req-1", "conclusion": "added"}]}
        saved = copy.deepcopy((old, new))
        merged = merge_zhongshu_evidence_packet(old, new)
        self.assertEqual(len(merged["evidence_updates"]), 3)
        self.assertEqual(merged["evidence_updates"][0]["conclusion"], "old")
        self.assertEqual(merged["evidence_updates"][0]["observed_variants"][0]["conclusion"], "new")
        self.assertEqual(set(merged["worker_evidence"]), {"w1", "w2"})
        self.assertEqual(merged["evidence_packet_version"], 2)
        self.assertEqual((old, new), saved)
        projected = project_evidence_record(merged["evidence_updates"][0])
        self.assertEqual(projected["observed_variant_count"], 1)
        self.assertEqual(projected["observed_variants"][0]["conclusion"], "new")

    def test_changed_requirement_contract_starts_a_new_packet(self):
        old = {"phase": "ZHONGSHU", "requirements": CANONICAL,
               "evidence_updates": [{"evidence_id": "stale"}]}
        new = {"phase": "ZHONGSHU", "requirements": [{**CANONICAL[0], "statement": "changed"}],
               "evidence_updates": []}
        self.assertEqual(merge_zhongshu_evidence_packet(old, new), new)

    def test_direct_menxia_packet_does_not_use_zhongshu_merge(self):
        old = {"phase": "ZHONGSHU", "requirements": CANONICAL,
               "evidence_updates": [{"evidence_id": "zhongshu"}]}
        new = {"phase": "MENXIA", "requirements": CANONICAL, "evidence_updates": []}
        self.assertEqual(merge_zhongshu_evidence_packet(old, new), new)

    def test_contract_rejection_salvages_valid_sibling_and_keeps_id_gate(self):
        good = _worker_payload("zhongshu_analyst-worker-01")
        bad = _worker_payload("zhongshu_analyst-worker-02")
        bad["requirements"][0]["requirement_id"] = "invented"
        result = _joiner(canonical_requirements=tuple(CANONICAL)).join(tuple(
            WorkerResult(p["worker_id"], "SUCCEEDED", None, result_payload=p)
            for p in (good, bad)))
        self.assertEqual(result.status, "FAILED")
        self.assertIn("ID_MISMATCH", result.failure.message)
        self.assertEqual([p["worker_id"] for p in result.aggregate["salvaged_worker_payloads"]],
                         [good["worker_id"]])
        self.assertEqual(result.failure.worker_id, bad["worker_id"])
        retry = _joiner(canonical_requirements=tuple(CANONICAL),
                        salvaged_worker_payloads=tuple(result.aggregate["salvaged_worker_payloads"]))
        repaired = _worker_payload(bad["worker_id"])
        self.assertEqual(retry.join((WorkerResult(repaired["worker_id"], "SUCCEEDED", None,
                                               result_payload=repaired),)).status, "SUCCEEDED")

    def test_group_failure_retains_valid_peer_without_claiming_wave_completion(self):
        joiner = AgentNodeJoiner("node", task_id="task", state="ZHONGSHU_CRITIC",
                                sequence=4, revision_id="r", plan_hash="h", dispatch_mode="group_review",
                                binding_contexts={"w1": {"group_id": "g1", "member_ids": ["i1"],
                                                         "revision_id": "r", "plan_hash": "h"}})
        result = joiner.join((WorkerResult("w1", "SUCCEEDED", None,
                                          result_payload={"action": "APPROVE_GROUP", "group_id": "g1",
                                                          "revision_id": "r", "reviewed_plan_hash": "h",
                                                          "findings": []}),
                              WorkerResult("w2", "FAILED", None)))
        self.assertEqual(result.status, "FAILED")
        self.assertEqual(result.aggregate["group_reviews"][0]["group_id"], "g1")
        self.assertFalse(result.aggregate.get("group_review_complete", False))

    def test_wrong_hash_group_is_not_salvaged(self):
        joiner = AgentNodeJoiner("node", task_id="task", state="ZHONGSHU_CRITIC",
                                sequence=4, revision_id="r", plan_hash="h", dispatch_mode="group_review",
                                binding_contexts={"w1": {"group_id": "g1", "member_ids": ["i1"],
                                                         "revision_id": "r", "plan_hash": "h"}})
        result = joiner.join((WorkerResult("w1", "SUCCEEDED", None,
                                          result_payload={"action": "APPROVE_GROUP", "group_id": "g1",
                                                          "revision_id": "r", "reviewed_plan_hash": "wrong",
                                                          "findings": []}), WorkerResult("w2", "FAILED", None)))
        self.assertFalse(result.aggregate.get("group_reviews"))

    def test_schema_valid_group_approval_without_business_identity_is_bound_by_dispatch(self):
        from orchestrator.contracts.zhongshu_critic import CONTRACT
        payload = CONTRACT.example_payload()
        payload.update(action="APPROVE_GROUP", mode="REVIEW_GROUP", group_id="g1",
                       task_id="task", request_id="current-request", findings=[])
        self.assertEqual(CONTRACT.validate(payload), ())
        self.assertNotIn("revision_id", payload)
        self.assertNotIn("reviewed_plan_hash", payload)
        saved = copy.deepcopy(payload)
        joiner = AgentNodeJoiner("node", task_id="task", state="ZHONGSHU_CRITIC",
                                sequence=4, revision_id="r", plan_hash="h", dispatch_mode="group_review",
                                binding_contexts={"w1": {"group_id": "g1", "member_ids": ["i1"],
                                                         "revision_id": "r", "plan_hash": "h"}})
        result = joiner.join((WorkerResult("w1", "SUCCEEDED", None, result_payload=payload),))
        self.assertEqual(result.status, "SUCCEEDED")
        self.assertEqual(result.aggregate["action"], "APPROVE_CRITIC")
        self.assertEqual(result.aggregate["reviewed_plan_hash"], "h")
        self.assertEqual(payload, saved)

    def test_explicit_wrong_revision_still_rejected(self):
        joiner = AgentNodeJoiner("node", task_id="task", state="ZHONGSHU_CRITIC",
                                sequence=4, revision_id="r", plan_hash="h", dispatch_mode="group_review",
                                binding_contexts={"w1": {"group_id": "g1", "member_ids": ["i1"],
                                                         "revision_id": "r", "plan_hash": "h"}})
        result = joiner.join((WorkerResult("w1", "SUCCEEDED", None, result_payload={
            "action": "APPROVE_GROUP", "group_id": "g1", "plan_revision_id": "old", "findings": []}),))
        self.assertEqual(result.status, "FAILED")
        self.assertIn("REVISION_MISMATCH", result.failure.message)

    def test_partial_approval_fold_skips_peer_but_changed_surface_reopens_review(self):
        from test_worker_fail_group_salvage import WorkerFailSalvageFoldTests
        from orchestrator.domain.states import ZhongshuCriticState, _group_review_bindings
        context = WorkerFailSalvageFoldTests()._context()
        context = replace(context, review=replace(context.review, findings=()))
        joiner = AgentNodeJoiner("node", task_id="task-1", state="ZHONGSHU_CRITIC",
                                sequence=4, revision_id="R1", plan_hash="plan-hash",
                                dispatch_mode="group_review", binding_contexts={"w1": {
                                    "group_id": "group-000001", "member_ids": ["item-000001", "item-000002"],
                                    "revision_id": "R1", "plan_hash": "plan-hash"}})
        result = joiner.join((WorkerResult("w1", "SUCCEEDED", None, result_payload={
            "action": "APPROVE_GROUP", "group_id": "group-000001", "revision_id": "R1",
            "reviewed_plan_hash": "plan-hash", "findings": []}), WorkerResult("w2", "FAILED", None)))
        update = ZhongshuCriticState._review_update(context, result.aggregate)
        review = replace(context.review, task_review_ledger=update.task_review_ledger,
                         zhongshu_groups=update.zhongshu_groups)
        folded = replace(context, review=review)
        bindings = _group_review_bindings(folded, "R1", "plan-hash")
        self.assertEqual([row["group_id"] for row in bindings], ["group-000002"])
        changed = replace(review.task_items[0], objective="changed after approval")
        reopened = replace(folded, review=replace(review, task_items=(changed, *review.task_items[1:])))
        self.assertIn("group-000001", [row["group_id"] for row in
                                      _group_review_bindings(reopened, "R1", "plan-hash")])

    def test_reply_repair_carries_bounded_draft(self):
        result = _with_reply_feedback(_binding(), _context(), "bad format", "known finding " + "x" * 50000)
        self.assertIn("known finding", result.prompt_ref)
        self.assertIn("Do not repeat", result.prompt_ref)
        self.assertLess(len(result.prompt_ref), 18000)


if __name__ == "__main__":
    unittest.main()
