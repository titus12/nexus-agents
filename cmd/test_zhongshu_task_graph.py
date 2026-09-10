from __future__ import annotations

import unittest
import tempfile

from orchestrator.adapters import FakeFeishuAdapter, FakeMulticaAdapter
from orchestrator.app import OrchestratorApp
from orchestrator.context import StateContext
from orchestrator.parallel_runtime import ParallelFanIn
from orchestrator.states import validate_zhongshu_task_graph
from orchestrator.zhongshu_parallel import (
    build_solver_evidence_context,
    canonicalize_task_graph,
    merge_analyst_evidence,
    merge_analyst_outputs,
    validate_zhongshu_requirement_contract,
)


def worker_payload(worker_id: str, lens: str, task_key: str, requirement_id: str = "REQ-001"):
    return {
        "action": "TASK_PROPOSALS_READY",
        "worker_id": worker_id,
        "phase": "ZHONGSHU",
        "revision_id": "revision-1",
        "lens": lens,
        "requirements": [{
            "requirement_id": requirement_id,
            "statement": "Make the workflow reliable",
            "source": "user",
            "priority": "must",
            "scope": "in",
            "acceptance_signal": "The workflow completes reliably",
        }],
        "task_proposals": [{
            "task_key": task_key,
            "title": f"Task {task_key}",
            "objective": f"Deliver {task_key}",
            "source_requirement_ids": [requirement_id],
            "dependencies": [],
            "acceptance_signals": [f"{task_key} is observable"],
            "evidence_ids": [f"ev-{task_key}"],
            "unknowns": [],
            "risks": [],
            "parallelizable": True,
            "group_key": "group-001",
        }],
        "constraints": [],
        "conflicts": [],
        "unknowns": [],
    }


def evidence_worker_payload(worker_id: str, lens: str, requirement_id: str = "REQ-001"):
    return {
        "action": "EVIDENCE_PACKET_READY",
        "worker_id": worker_id,
        "phase": "ZHONGSHU",
        "revision_id": "revision-1",
        "lens": lens,
        "requirements": [{
            "requirement_id": requirement_id,
            "statement": "Make the workflow reliable",
            "source": "user",
            "priority": "must",
            "scope": "in",
            "kind": "task",
            "acceptance_signal": "The workflow completes reliably",
        }],
        "task_proposals": [],
        "candidate_items": [],
        "candidate_groups": [],
        "evidence_updates": [{
            "evidence_id": f"ev-{worker_id}",
            "requirement_id": requirement_id,
            "decision_relevance": "coverage",
            "source": f"{lens}:runtime.log:1",
            "conclusion": f"Observed evidence from {lens}",
            "unknowns": [],
        }],
        "confirmed_facts": [{
            "evidence_id": f"ev-{worker_id}",
            "requirement_id": requirement_id,
            "statement": f"Observed evidence from {lens}",
            "source_type": "log",
            "source": f"{lens}:runtime.log:1",
        }],
        "constraints": [],
        "conflicts": [],
        "unknowns": [],
        "unknown_requirement_ids": [],
        "unknown_resolutions": [],
        "risks": [],
        "scope": {"in_scope": ["workflow"]},
        "questions_for_solver": [],
        "questions_for_user": [],
        "summary": "evidence only",
    }


class ZhongshuTaskGraphTests(unittest.TestCase):
    def test_parallel_analyst_fan_in_uses_all_three_workers(self):
        with tempfile.TemporaryDirectory() as directory:
            app = OrchestratorApp(
                StateContext(
                    task_id="task-app",
                    workflow_state="ZHONGSHU_ANALYST",
                    raw_request="Make the workflow reliable",
                ),
                root=directory,
                multica=FakeMulticaAdapter(),
                feishu=FakeFeishuAdapter(),
                poll_interval=0,
            )
            results = [
                evidence_worker_payload("analyst-1", "architecture"),
                evidence_worker_payload("analyst-2", "runtime"),
                evidence_worker_payload("analyst-3", "risk"),
            ]
            captured: dict[str, object] = {}

            def fake_fanout(**kwargs):
                captured.setdefault("fanout_calls", []).append(kwargs)
                if kwargs["workers"][0].worker_id == "zhongshu_requirement_contract":
                    return ParallelFanIn(
                        "ZHONGSHU",
                        "revision-1",
                        ({
                            "action": "REQUIREMENT_CONTRACT_READY",
                            "worker_id": "zhongshu_requirement_contract",
                            "phase": "ZHONGSHU",
                            "revision_id": "revision-1",
                            "requirements": worker_payload(
                                "contract", "contract", "T-contract"
                            )["requirements"],
                        },),
                        (),
                        (),
                    )
                captured.update(kwargs)
                return ParallelFanIn(
                    "ZHONGSHU",
                    "revision-1",
                    tuple(results),
                    (),
                    (),
                )

            app.run_parallel_fanout = fake_fanout
            event = app._run_parallel_state("ZHONGSHU_ANALYST")

            self.assertEqual(event.name, "AGENT_REPLY_ACCEPTED")
            self.assertEqual(event.payload["plan"]["candidate_items"], [])
            self.assertEqual(len(event.payload["plan"]["evidence_updates"]), 3)
            workers = captured["workers"]
            self.assertEqual(len(workers), 3)
            self.assertTrue(all(worker.request.target_state == "ZHONGSHU_ANALYST" for worker in workers))
            self.assertTrue(all('"mode":"EVIDENCE_COLLECTION_READ_ONLY"' in worker.request.prompt for worker in workers))
            self.assertEqual(len(captured["fanout_calls"]), 2)
            self.assertTrue(all('"requirement_contract"' in worker.request.prompt for worker in workers))

    def test_requirement_contract_is_strict_and_authoritative(self):
        contract = {
            "action": "REQUIREMENT_CONTRACT_READY",
            "requirements": [{
                "requirement_id": "REQ-001",
                "statement": "Original requirement",
                "source": "user",
                "priority": "must",
                "scope": "in",
                "acceptance_signal": "Observable result",
            }],
        }
        self.assertEqual(validate_zhongshu_requirement_contract(contract), "")
        worker = worker_payload("analyst-1", "runtime", "T-1")
        worker["requirements"][0]["statement"] = "Different requirement"
        with self.assertRaisesRegex(ValueError, "worker requirement contract violation"):
            merge_analyst_outputs(
                "task-1",
                "revision-1",
                [worker],
                canonical_requirements=contract["requirements"],
            )

    def test_merge_keeps_all_unique_tasks_and_provenance(self):
        result = merge_analyst_outputs(
            "task-1",
            "revision-1",
            [
                worker_payload("analyst-1", "requirements", "T-1"),
                worker_payload("analyst-2", "boundaries", "T-2"),
                worker_payload("analyst-3", "risk", "T-1"),
            ],
        )
        tasks = result["plan"]["candidate_items"]
        self.assertEqual([item["item_id"] for item in tasks], ["T-1", "T-2"])
        self.assertEqual(tasks[0]["source_workers"], ["analyst-1", "analyst-3"])

    def test_evidence_merge_never_creates_tasks(self):
        result = merge_analyst_evidence(
            "task-1",
            "revision-1",
            [
                evidence_worker_payload("analyst-1", "architecture"),
                evidence_worker_payload("analyst-2", "runtime"),
            ],
        )
        self.assertEqual(result["action"], "READY_FOR_SOLVER")
        self.assertEqual(result["plan"]["candidate_items"], [])
        self.assertEqual(result["plan"]["candidate_groups"], [])
        self.assertEqual(len(result["plan"]["evidence_updates"]), 2)

    def test_solver_evidence_context_keeps_key_facts_only(self):
        first = evidence_worker_payload("analyst-1", "architecture")
        first["questions_for_solver"] = [{
            "question_id": "Q-1",
            "question": "Should Solver redesign the executor?",
            "context": "implementation choice, not an evidence gap",
        }]
        first["confirmed_facts"][0]["statement"] = "Unique fact nuance"
        result = merge_analyst_evidence(
            "task-1",
            "revision-1",
            [first, evidence_worker_payload("analyst-2", "runtime")],
        )

        context = build_solver_evidence_context(result["plan"])

        self.assertEqual(len(context["requirements"]), 1)
        self.assertEqual(len(context["evidence_updates"]), 2)
        self.assertEqual(
            context["evidence_updates"][0]["fact_statement"],
            "Unique fact nuance",
        )
        self.assertEqual(
            context["confirmed_fact_ids"],
            ["ev-analyst-1", "ev-analyst-2"],
        )
        self.assertEqual(context["confirmed_facts"], [])
        self.assertNotIn("questions_for_solver", context)
        self.assertEqual(
            context["context_policy"]["suppressed_questions_for_solver"],
            1,
        )
        self.assertNotIn("worker_evidence", context)

    def test_semantic_duplicates_are_reported_not_merged(self):
        item = {
            "item_id": "item-1",
            "title": "First",
            "objective": "same outcome",
            "source_requirement_ids": ["REQ-001"],
            "dependencies": [],
            "acceptance_signals": ["done"],
            "unknowns": [],
            "risks": [],
            "parallelizable": True,
        }
        second = dict(item, item_id="item-2", title="Second")
        plan = {
            "items": [item, second],
            "groups": [{"group_id": "group-1", "items": [item, second]}],
        }
        canonical, duplicates = canonicalize_task_graph(plan)
        self.assertEqual(duplicates, [["item-1", "item-2"]])
        self.assertEqual(len(canonical["items"]), 2)

    def test_merge_rejects_missing_must_requirement_coverage(self):
        payload = worker_payload("analyst-1", "requirements", "T-1")
        payload["task_proposals"][0]["source_requirement_ids"] = []
        with self.assertRaisesRegex(ValueError, "requirement coverage"):
            merge_analyst_outputs("task-1", "revision-1", [payload])

    def test_constraints_do_not_require_task_coverage(self):
        payload = worker_payload("analyst-1", "requirements", "T-1")
        payload["requirements"].append({
            "requirement_id": "REQ-008",
            "statement": "Do not modify orchestrator code",
            "source": "derived",
            "priority": "must",
            "scope": "in",
            "acceptance_signal": "Review only; no code changes",
            "kind": "constraint",
        })
        merged = merge_analyst_outputs("task-1", "revision-1", [payload])
        self.assertIn("Do not modify orchestrator code", merged["plan"]["constraints"])
        self.assertEqual(
            validate_zhongshu_task_graph(merged),
            "ZHONGSHU_ANALYST_TASK_GRAPH_FORBIDDEN",
        )

    def test_worker_contract_requires_compact_task_fields(self):
        payload = worker_payload("analyst-1", "requirements", "T-1")
        payload["task_proposals"][0].pop("acceptance_signals")
        self.assertEqual(
            validate_zhongshu_task_graph(payload),
            "ZHONGSHU_ANALYST_TASK_PROPOSALS_FORBIDDEN",
        )

    def test_canonical_graph_rejects_implementation_details(self):
        payload = merge_analyst_outputs(
            "task-1", "revision-1", [worker_payload("analyst-1", "requirements", "T-1")]
        )
        payload["plan"]["candidate_items"][0]["implementation_proposal"] = {}
        self.assertEqual(
            validate_zhongshu_task_graph(payload),
            "ZHONGSHU_TASK_IMPLEMENTATION_DETAIL_FORBIDDEN",
        )


if __name__ == "__main__":
    unittest.main()
