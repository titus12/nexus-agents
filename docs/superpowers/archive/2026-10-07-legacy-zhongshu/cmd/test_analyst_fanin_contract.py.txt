from __future__ import annotations

import unittest

from orchestrator.domain.context import (
    ProgressState,
    RequestState,
    ReviewState,
    TaskIdentity,
    WorkflowContext,
)
from orchestrator.domain.policies.parallel import aggregate_zhongshu_workers
from orchestrator.domain.states import ZhongshuAnalystState
from orchestrator.domain.transitions import TransitionRegistry


REVISION_ID = "task-test:ZHONGSHU_ANALYST:1"


def _worker(worker_id: str, *, source: str, acceptance: str, statement: str = "do X") -> dict:
    return {
        "action": "EVIDENCE_PACKET_READY",
        "worker_id": worker_id,
        "revision_id": REVISION_ID,
        "requirements": [
            {
                "requirement_id": "req-001",
                "statement": statement,
                "source": source,
                "priority": "must",
                "scope": "in",
                "kind": "task",
                "acceptance_signal": acceptance,
            }
        ],
        "evidence_updates": [],
        "task_proposals": [],
        "candidate_items": [],
        "candidate_groups": [],
    }


class AnalystFanInContractTests(unittest.TestCase):
    def test_worker_local_provenance_may_differ(self) -> None:
        workers = [
            _worker("worker-01", source="lens A @ req-001", acceptance="signal A"),
            _worker("worker-02", source="lens B @ req-001", acceptance="signal B"),
            _worker("worker-03", source="lens C @ req-001", acceptance="signal C"),
        ]
        merged = aggregate_zhongshu_workers(
            "task-test", "ZHONGSHU_ANALYST", REVISION_ID, "", workers
        )
        self.assertEqual(merged.get("action"), "READY_FOR_SOLVER")

    def test_semantic_statement_conflict_still_raises(self) -> None:
        workers = [
            _worker("worker-01", source="lens A", acceptance="signal A", statement="do X"),
            _worker("worker-02", source="lens B", acceptance="signal B", statement="do Y"),
        ]
        with self.assertRaises(ValueError) as ctx:
            aggregate_zhongshu_workers(
                "task-test", "ZHONGSHU_ANALYST", REVISION_ID, "", workers
            )
        self.assertIn("statement", str(ctx.exception))

    def test_requirement_id_format_variants_do_not_conflict(self) -> None:
        first = _worker("worker-01", source="A", acceptance="sA", statement="do X")
        second = _worker("worker-02", source="B", acceptance="sB", statement="do X")
        second["requirements"][0]["requirement_id"] = "req-000001"
        merged = aggregate_zhongshu_workers(
            "task-test", "ZHONGSHU_ANALYST", REVISION_ID, "", [first, second]
        )
        self.assertEqual(merged.get("action"), "READY_FOR_SOLVER")

    def test_near_duplicate_statement_passes(self) -> None:
        workers = [
            _worker(
                "worker-01",
                source="A",
                acceptance="sA",
                statement="orchestrator 应当通过 prompt 优化降低 Agent 输入文件传输开销",
            ),
            _worker(
                "worker-02",
                source="B",
                acceptance="sB",
                statement="orchestrator 应当通过 prompt 优化降低 Agent 输入传输开销。",
            ),
        ]
        merged = aggregate_zhongshu_workers(
            "task-test", "ZHONGSHU_ANALYST", REVISION_ID, "", workers
        )
        self.assertEqual(merged.get("action"), "READY_FOR_SOLVER")


CANONICAL = [
    {
        "requirement_id": "req-000001",
        "statement": "canonical statement",
        "priority": "must",
        "scope": "in",
        "kind": "task",
    }
]


def _context(requirements: tuple[dict, ...] = ()) -> WorkflowContext:
    return WorkflowContext(
        identity=TaskIdentity("task-1", "issue-1", "", "request-1"),
        progression=ProgressState("ZHONGSHU_ANALYST", 1, "2026-09-11T00:00:00Z"),
        request=RequestState(raw_request="review"),
        review=ReviewState(revision_id="rev-1", requirements=requirements),
    )


class AnalystContractPassTests(unittest.TestCase):
    def test_contract_action_loops_back_to_analyst(self) -> None:
        self.assertEqual(
            TransitionRegistry.default().target_for(
                "ZHONGSHU_ANALYST", "REQUIREMENT_CONTRACT_READY"
            ),
            "ZHONGSHU_ANALYST",
        )

    def test_first_pass_is_requirement_contract(self) -> None:
        effect = ZhongshuAnalystState._dispatch_effect(
            _context(), "ZHONGSHU_ANALYST"
        )
        self.assertEqual(effect.effect_type, "agent_dispatch")
        self.assertEqual(
            effect.payload["dispatch_context"]["zhongshu_dispatch_mode"],
            "requirement_contract",
        )

    def test_evidence_pass_injects_canonical_requirements(self) -> None:
        effect = ZhongshuAnalystState._dispatch_effect(
            _context(tuple(CANONICAL)),
            "ZHONGSHU_ANALYST",
            canonical_requirements=tuple(CANONICAL),
        )
        self.assertEqual(effect.effect_type, "node_dispatch")
        self.assertEqual(effect.payload["canonical_requirements"], CANONICAL)
        bindings = effect.payload["bindings"]
        self.assertTrue(bindings)
        for binding in bindings:
            self.assertEqual(
                binding["dispatch_context"]["requirement_contract"], CANONICAL
            )

    def test_canonical_contract_overrides_worker_paraphrase(self) -> None:
        workers = [
            _worker("worker-01", source="A", acceptance="sA", statement="paraphrase one"),
            _worker("worker-02", source="B", acceptance="sB", statement="paraphrase two"),
        ]
        merged = aggregate_zhongshu_workers(
            "task-test",
            "ZHONGSHU_ANALYST",
            REVISION_ID,
            "",
            workers,
            CANONICAL,
        )
        self.assertEqual(merged.get("action"), "READY_FOR_SOLVER")


if __name__ == "__main__":
    unittest.main()
