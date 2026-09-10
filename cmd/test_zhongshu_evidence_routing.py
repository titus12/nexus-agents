from __future__ import annotations

import json
import copy
import tempfile
import unittest

from orchestrator.adapters import FakeFeishuAdapter, FakeMulticaAdapter
from orchestrator.app import OrchestratorApp
from orchestrator.context import StateContext
from orchestrator.parallel_runtime import ParallelFanIn
from orchestrator.states import ZhongshuAnalystState
from orchestrator.zhongshu_parallel import merge_analyst_evidence


class ZhongshuEvidenceRoutingTests(unittest.TestCase):
    @staticmethod
    def _canonical_contract():
        return [{
            "requirement_id": "REQ-1",
            "statement": "Review the workflow",
            "source": "user",
            "priority": "must",
            "scope": "in",
            "kind": "task",
            "acceptance_signal": "The review identifies the remaining optimization space",
        }]

    def test_merge_rebinds_changed_acceptance_signal_without_dropping_evidence(self):
        canonical = self._canonical_contract()
        worker = {
            "worker_id": "analyst-1",
            "revision_id": "revision-1",
            "action": "EVIDENCE_PACKET_READY",
            "phase": "ZHONGSHU",
            "requirements": [{
                **canonical[0],
                "acceptance_signal": "The review is complete",
            }],
            "task_proposals": [],
            "candidate_items": [],
            "candidate_groups": [],
            "evidence_updates": [{
                "evidence_id": "ev-1",
                "requirement_id": "REQ-1",
                "decision_relevance": "acceptance",
                "source": "cmd/orchestrator/app.py:1",
                "conclusion": "The canonical acceptance condition is needed by Solver.",
            }],
        }

        merged = merge_analyst_evidence(
            "task-1",
            "revision-1",
            [worker],
            canonical_requirements=canonical,
        )

        self.assertEqual(merged["plan"]["requirements"], canonical)
        self.assertEqual(
            merged["plan"]["evidence_updates"][0]["evidence_id"],
            "ev-1",
        )

    def test_merge_rejects_missing_or_extra_requirement_ids(self):
        canonical = self._canonical_contract()
        base = {
            "worker_id": "analyst-1",
            "revision_id": "revision-1",
            "action": "EVIDENCE_PACKET_READY",
            "phase": "ZHONGSHU",
            "requirements": [],
            "task_proposals": [],
            "candidate_items": [],
            "candidate_groups": [],
            "evidence_updates": [],
        }

        with self.assertRaisesRegex(ValueError, "REQUIREMENT_CONTRACT_ID"):
            merge_analyst_evidence(
                "task-1",
                "revision-1",
                [base],
                canonical_requirements=canonical,
            )

        extra = {
            **base,
            "requirements": [
                canonical[0],
                {**canonical[0], "requirement_id": "REQ-2"},
            ],
        }
        with self.assertRaisesRegex(ValueError, "REQUIREMENT_CONTRACT_ID"):
            merge_analyst_evidence(
                "task-1",
                "revision-1",
                [extra],
                canonical_requirements=canonical,
            )

    def test_analyst_prompt_requires_verbatim_immutable_contract_fields(self):
        ctx = StateContext(
            task_id="task-1",
            raw_request="Review the workflow",
            workflow_state="ZHONGSHU_ANALYST",
            current_phase="ZHONGSHU",
            current_role="review-analyst",
        )
        prompt = json.loads(ZhongshuAnalystState().request(ctx).prompt)
        rules = " ".join(prompt["working_rules"])
        for field in (
            "requirement_id",
            "statement",
            "source",
            "priority",
            "scope",
            "kind",
            "acceptance_signal",
        ):
            self.assertIn(field, rules)

    def test_parallel_analyst_prompt_preserves_canonical_requirements(self):
        with tempfile.TemporaryDirectory() as directory:
            contract = [{
                "requirement_id": "REQ-1",
                "statement": "Review",
                "source": "user",
                "priority": "must",
                "scope": "in",
                "kind": "task",
                "acceptance_signal": "reviewed",
            }]
            ctx = StateContext(
                task_id="task-analyst-contract-prompt",
                workflow_state="ZHONGSHU_ANALYST",
                current_phase="ZHONGSHU",
                current_role="review-analyst",
                expected_agent_id="analyst",
                active_request_id="request-1",
                raw_request="Review the workflow",
                zhongshu_parallel={"enabled": True, "analyst_max_workers": 3},
                request_payload={
                    "zhongshu_requirement_contract": copy.deepcopy(contract),
                },
            )
            app = OrchestratorApp(
                ctx,
                root=directory,
                multica=FakeMulticaAdapter(),
                feishu=FakeFeishuAdapter(),
                poll_interval=0,
            )
            captured: dict[str, object] = {}

            def fake_fanout(**kwargs):
                workers = kwargs["workers"]
                captured["requirements"] = [
                    json.loads(worker.request.prompt)["required_response_schema"]["requirements"]
                    for worker in workers
                ]
                results = [{
                    "action": "EVIDENCE_PACKET_READY",
                    "worker_id": worker.worker_id,
                    "phase": "ZHONGSHU",
                    "revision_id": "revision-1",
                    "requirements": copy.deepcopy(contract),
                    "task_proposals": [],
                    "candidate_items": [],
                    "candidate_groups": [],
                    "evidence_updates": [],
                    "confirmed_facts": [],
                    "constraints": [],
                    "conflicts": [],
                    "unknowns": [],
                    "unknown_requirement_ids": [],
                    "unknown_resolutions": [],
                    "risks": [],
                    "scope": None,
                    "questions_for_solver": [],
                    "questions_for_user": [],
                } for worker in workers]
                return ParallelFanIn("ZHONGSHU", "revision-1", tuple(results), (), ())

            app.run_parallel_fanout = fake_fanout
            event = app._run_parallel_state("ZHONGSHU_ANALYST")

            self.assertEqual(event.name, "AGENT_REPLY_ACCEPTED")
            self.assertEqual(captured["requirements"], [contract, contract, contract])

    def test_supplement_dispatches_one_worker_per_affected_item(self):
        with tempfile.TemporaryDirectory() as directory:
            ctx = StateContext(
                task_id="task-evidence",
                workflow_state="ZHONGSHU_ANALYST",
                current_phase="ZHONGSHU",
                current_role="review-analyst",
                expected_agent_id="analyst",
                active_request_id="request-1",
                raw_request="Review the workflow",
                request_payload={
                    "analyst_plan": {
                        "requirements": [{"requirement_id": "REQ-1", "statement": "Review"}],
                        "candidate_items": [],
                        "candidate_groups": [],
                        "evidence_updates": [],
                        "confirmed_facts": [],
                        "constraints": [],
                        "unknowns": [],
                        "risks": [],
                    },
                    "candidate_plan": {
                        "items": [{"item_id": "item-1"}, {"item_id": "item-2"}],
                        "groups": [],
                    },
                    "zhongshu_requirement_contract": [{
                        "requirement_id": "REQ-1",
                        "statement": "Review",
                        "source": "user",
                        "priority": "must",
                        "scope": "in",
                        "acceptance_signal": "reviewed",
                    }],
                    "zhongshu_evidence_request": {
                        "action": "REQUEST_ANALYST_EVIDENCE",
                        "affected_item_ids": ["item-2", "item-1"],
                        "findings": [{"finding_id": "finding-1"}],
                    },
                    "zhongshu_critic_review": {
                        "action": "REQUEST_ANALYST_EVIDENCE",
                        "affected_item_ids": ["item-2", "item-1"],
                        "findings": [{"finding_id": "finding-1"}],
                    },
                },
            )
            app = OrchestratorApp(
                ctx,
                root=directory,
                multica=FakeMulticaAdapter(),
                feishu=FakeFeishuAdapter(),
                poll_interval=0,
            )
            base = app.states["ZHONGSHU_ANALYST"].request(ctx)
            captured: dict[str, object] = {}

            def fake_fanout(**kwargs):
                workers = kwargs["workers"]
                captured["workers"] = workers
                results = []
                for worker in workers:
                    item_id = worker.request.context["evidence_item_scope"]
                    results.append({
                        "action": "EVIDENCE_SUPPLEMENT_READY",
                        "worker_id": worker.worker_id,
                        "phase": "ZHONGSHU",
                        "revision_id": "revision-1",
                        "evidence_updates": [{
                            "evidence_id": f"ev-{item_id}",
                            "finding_id": "finding-1",
                            "item_id": item_id,
                            "source": f"{item_id}:source",
                            "conclusion": "verified",
                            "unknowns": [],
                        }],
                    })
                return ParallelFanIn("ZHONGSHU", "revision-1", tuple(results), (), ())

            app.run_parallel_fanout = fake_fanout
            event = app._run_analyst_supplement(base, "revision-1")

            self.assertEqual(event.payload["action"], "READY_FOR_SOLVER")
            workers = captured["workers"]
            self.assertEqual(len(workers), 2)
            assigned = []
            for worker in workers:
                prompt = json.loads(worker.request.prompt)
                assigned.append(prompt["assigned_item_id"])
            self.assertEqual(assigned, ["item-1", "item-2"])
            self.assertTrue(all("item-" in worker.worker_id for worker in workers))

    def test_supplement_rejects_unscoped_request_without_global_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            ctx = StateContext(
                task_id="task-unscoped-evidence",
                workflow_state="ZHONGSHU_ANALYST",
                current_phase="ZHONGSHU",
                current_role="review-analyst",
                expected_agent_id="analyst",
                active_request_id="request-1",
                raw_request="Review the workflow",
                request_payload={
                    "analyst_plan": {
                        "requirements": [{"requirement_id": "REQ-1", "statement": "Review"}],
                        "candidate_items": [],
                        "candidate_groups": [],
                        "evidence_updates": [],
                        "confirmed_facts": [],
                        "constraints": [],
                        "unknowns": [],
                        "risks": [],
                    },
                    "zhongshu_evidence_request": {
                        "action": "REQUEST_ANALYST_EVIDENCE",
                        "findings": [{"finding_id": "finding-global"}],
                    },
                    "zhongshu_critic_review": {
                        "action": "REQUEST_ANALYST_EVIDENCE",
                        "findings": [{"finding_id": "finding-global"}],
                    },
                },
            )
            app = OrchestratorApp(
                ctx,
                root=directory,
                multica=FakeMulticaAdapter(),
                feishu=FakeFeishuAdapter(),
                poll_interval=0,
            )
            base = app.states["ZHONGSHU_ANALYST"].request(ctx)
            called = {"count": 0}

            def unexpected_fanout(**_kwargs):
                called["count"] += 1
                raise AssertionError("unscoped evidence must not dispatch a worker")

            app.run_parallel_fanout = unexpected_fanout
            event = app._run_analyst_supplement(base, "revision-1")

            self.assertEqual(event.payload["action"], "HUMAN_GATE")
            self.assertTrue(
                any(
                    error.startswith("finding_without_item_scope:")
                    for error in event.payload["evidence_scope_errors"]
                )
            )
            self.assertEqual(called["count"], 0)

    def test_supplement_batches_affected_items_by_analyst_capacity(self):
        with tempfile.TemporaryDirectory() as directory:
            item_ids = [f"item-{index}" for index in range(1, 8)]
            ctx = StateContext(
                task_id="task-batched-evidence",
                workflow_state="ZHONGSHU_ANALYST",
                current_phase="ZHONGSHU",
                current_role="review-analyst",
                expected_agent_id="analyst",
                active_request_id="request-1",
                raw_request="Review the workflow",
                zhongshu_parallel={"enabled": True, "analyst_max_workers": 3},
                request_payload={
                    "analyst_plan": {
                        "requirements": [{"requirement_id": "REQ-1", "statement": "Review"}],
                        "candidate_items": [],
                        "candidate_groups": [],
                        "evidence_updates": [],
                        "confirmed_facts": [],
                        "constraints": [],
                        "unknowns": [],
                        "risks": [],
                    },
                    "candidate_plan": {
                        "items": [{"item_id": item_id} for item_id in item_ids],
                        "groups": [],
                    },
                    "zhongshu_evidence_request": {
                        "action": "REQUEST_ANALYST_EVIDENCE",
                        "affected_item_ids": item_ids,
                        "findings": [],
                    },
                    "zhongshu_critic_review": {
                        "action": "REQUEST_ANALYST_EVIDENCE",
                        "affected_item_ids": item_ids,
                        "findings": [],
                    },
                },
            )
            app = OrchestratorApp(
                ctx,
                root=directory,
                multica=FakeMulticaAdapter(),
                feishu=FakeFeishuAdapter(),
                poll_interval=0,
            )
            base = app.states["ZHONGSHU_ANALYST"].request(ctx)
            batch_sizes = []

            def fake_fanout(**kwargs):
                workers = kwargs["workers"]
                batch_sizes.append(len(workers))
                results = []
                for worker in workers:
                    item_id = worker.request.context["evidence_item_scope"]
                    results.append({
                        "action": "EVIDENCE_SUPPLEMENT_READY",
                        "worker_id": worker.worker_id,
                        "phase": "ZHONGSHU",
                        "revision_id": "revision-1",
                        "evidence_updates": [{
                            "evidence_id": f"ev-{item_id}",
                            "finding_id": "finding-1",
                            "item_id": item_id,
                            "source": f"{item_id}:source",
                            "conclusion": "verified",
                            "unknowns": [],
                        }],
                    })
                return ParallelFanIn("ZHONGSHU", "revision-1", tuple(results), (), ())

            app.run_parallel_fanout = fake_fanout
            event = app._run_analyst_supplement(base, "revision-1")

            self.assertEqual(event.payload["action"], "READY_FOR_SOLVER")
            self.assertEqual(batch_sizes, [3, 3, 1])


if __name__ == "__main__":
    unittest.main()
