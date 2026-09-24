from __future__ import annotations

import unittest

from orchestrator.domain.context import (
    ProgressState,
    RequestState,
    ReviewState,
    TaskIdentity,
    WorkflowContext,
)
from orchestrator.domain.states import ZhongshuAnalystState
from orchestrator.runtime.agent_effects import AgentNodeJoiner
from orchestrator.runtime.nodes import WorkerResult

REVISION = "rev-1"
PLAN_HASH = "plan-hash"


def _worker_payload(worker_id: str) -> dict:
    lens = worker_id[-2:]
    return {
        "action": "EVIDENCE_PACKET_READY",
        "worker_id": worker_id,
        "revision_id": REVISION,
        "requirements": [
            {
                "requirement_id": "req-001",
                "statement": "do X",
                "source": f"lens {lens} @ req-001",
                "priority": "must",
                "scope": "in",
                "kind": "task",
                "acceptance_signal": f"signal {lens}",
            }
        ],
        "evidence_updates": [],
        "task_proposals": [],
        "candidate_items": [],
        "candidate_groups": [],
    }


def _salvaged_row(worker_id: str, **extra: object) -> dict:
    return {
        **_worker_payload(worker_id),
        "salvage_revision_id": REVISION,
        **extra,
    }


def _joiner(**kwargs) -> AgentNodeJoiner:
    return AgentNodeJoiner(
        "node:task-1:ZHONGSHU_ANALYST:2",
        task_id="task-1",
        state="ZHONGSHU_ANALYST",
        sequence=2,
        revision_id=REVISION,
        plan_hash=PLAN_HASH,
        **kwargs,
    )


def _context(
    salvaged: tuple[dict, ...] = (),
) -> WorkflowContext:
    return WorkflowContext(
        identity=TaskIdentity("task-1", "issue-1", "", "request-1"),
        progression=ProgressState("RETRY_WAIT", 3, "2026-09-21T00:00:00Z"),
        request=RequestState(raw_request="review"),
        review=ReviewState(
            revision_id=REVISION,
            salvaged_worker_payloads=salvaged,
        ),
    )


CANONICAL = [
    {
        "requirement_id": "req-000001",
        "statement": "canonical statement",
        "priority": "must",
        "scope": "in",
        "kind": "task",
    }
]


class AnalystWaveSalvageJoinTests(unittest.TestCase):
    def test_join_failure_salvages_successful_workers(self) -> None:
        results = (
            WorkerResult("zhongshu_analyst-worker-01", "FAILED", None),
            WorkerResult(
                "zhongshu_analyst-worker-02",
                "SUCCEEDED",
                None,
                result_payload=_worker_payload("zhongshu_analyst-worker-02"),
            ),
            WorkerResult(
                "zhongshu_analyst-worker-03",
                "SUCCEEDED",
                None,
                result_payload=_worker_payload("zhongshu_analyst-worker-03"),
            ),
        )

        node_result = _joiner().join(results)

        self.assertEqual(node_result.status, "FAILED")
        self.assertEqual(node_result.aggregate.get("action"), "FAIL")
        salvaged = node_result.aggregate.get("salvaged_worker_payloads")
        self.assertEqual(len(salvaged or []), 2)
        self.assertEqual(
            sorted(row["worker_id"] for row in salvaged),
            ["zhongshu_analyst-worker-02", "zhongshu_analyst-worker-03"],
        )
        for row in salvaged:
            self.assertEqual(row["salvage_revision_id"], REVISION)

    def test_join_failure_carries_previous_salvage(self) -> None:
        joiner = _joiner(
            salvaged_worker_payloads=(
                _salvaged_row("zhongshu_analyst-worker-02"),
                _salvaged_row("zhongshu_analyst-worker-03"),
            )
        )
        results = (
            WorkerResult("zhongshu_analyst-worker-01", "FAILED", None),
        )

        node_result = joiner.join(results)

        self.assertEqual(node_result.status, "FAILED")
        salvaged = node_result.aggregate.get("salvaged_worker_payloads")
        self.assertEqual(
            sorted(row["worker_id"] for row in salvaged),
            ["zhongshu_analyst-worker-02", "zhongshu_analyst-worker-03"],
        )

    def test_join_failure_fresh_result_beats_carried_row(self) -> None:
        joiner = _joiner(
            salvaged_worker_payloads=(
                _salvaged_row("zhongshu_analyst-worker-02", stale=True),
            )
        )
        results = (
            WorkerResult("zhongshu_analyst-worker-01", "FAILED", None),
            WorkerResult(
                "zhongshu_analyst-worker-02",
                "SUCCEEDED",
                None,
                result_payload=_worker_payload("zhongshu_analyst-worker-02"),
            ),
        )

        node_result = joiner.join(results)

        self.assertEqual(node_result.status, "FAILED")
        salvaged = node_result.aggregate.get("salvaged_worker_payloads")
        self.assertEqual(len(salvaged), 1)
        self.assertEqual(salvaged[0]["worker_id"], "zhongshu_analyst-worker-02")
        self.assertNotIn("stale", salvaged[0])

    def test_join_success_replays_carried_payloads(self) -> None:
        joiner = _joiner(
            salvaged_worker_payloads=(
                _salvaged_row("zhongshu_analyst-worker-02"),
                _salvaged_row("zhongshu_analyst-worker-03"),
            )
        )
        results = (
            WorkerResult(
                "zhongshu_analyst-worker-01",
                "SUCCEEDED",
                None,
                result_payload=_worker_payload("zhongshu_analyst-worker-01"),
            ),
        )

        node_result = joiner.join(results)

        self.assertEqual(node_result.status, "SUCCEEDED")
        self.assertEqual(node_result.aggregate.get("action"), "READY_FOR_SOLVER")
        self.assertEqual(
            set(node_result.aggregate.get("worker_ids") or []),
            {
                "zhongshu_analyst-worker-01",
                "zhongshu_analyst-worker-02",
                "zhongshu_analyst-worker-03",
            },
        )
        # The carried payloads were consumed; the salvage set must reset so
        # the next wave starts clean.
        self.assertEqual(node_result.aggregate.get("salvaged_worker_payloads"), [])


class AnalystWaveSalvageStateTests(unittest.TestCase):
    def test_dispatch_skips_salvaged_workers(self) -> None:
        context = _context(
            (
                _salvaged_row("zhongshu_analyst-worker-02"),
                _salvaged_row("zhongshu_analyst-worker-03"),
            )
        )

        effect = ZhongshuAnalystState._dispatch_effect(
            context,
            "ZHONGSHU_ANALYST",
            revision_id=REVISION,
            plan_hash=PLAN_HASH,
            canonical_requirements=tuple(CANONICAL),
        )

        self.assertEqual(effect.effect_type, "node_dispatch")
        bindings = effect.payload["bindings"]
        self.assertEqual(
            [binding["worker_id"] for binding in bindings],
            ["zhongshu_analyst-worker-01"],
        )
        salvaged = effect.payload["salvaged_worker_payloads"]
        self.assertEqual(
            [row["worker_id"] for row in salvaged],
            ["zhongshu_analyst-worker-02", "zhongshu_analyst-worker-03"],
        )

    def test_dispatch_ignores_salvage_from_other_revision(self) -> None:
        context = _context(
            (
                _salvaged_row(
                    "zhongshu_analyst-worker-02", salvage_revision_id="rev-old"
                ),
            )
        )

        effect = ZhongshuAnalystState._dispatch_effect(
            context,
            "ZHONGSHU_ANALYST",
            revision_id=REVISION,
            plan_hash=PLAN_HASH,
            canonical_requirements=tuple(CANONICAL),
        )

        self.assertEqual(
            [binding["worker_id"] for binding in effect.payload["bindings"]],
            [
                "zhongshu_analyst-worker-01",
                "zhongshu_analyst-worker-02",
                "zhongshu_analyst-worker-03",
            ],
        )
        self.assertNotIn("salvaged_worker_payloads", effect.payload)

    def test_dispatch_falls_back_to_full_wave_when_all_salvaged(self) -> None:
        context = _context(
            (
                _salvaged_row("zhongshu_analyst-worker-01"),
                _salvaged_row("zhongshu_analyst-worker-02"),
                _salvaged_row("zhongshu_analyst-worker-03"),
            )
        )

        effect = ZhongshuAnalystState._dispatch_effect(
            context,
            "ZHONGSHU_ANALYST",
            revision_id=REVISION,
            plan_hash=PLAN_HASH,
            canonical_requirements=tuple(CANONICAL),
        )

        self.assertEqual(len(effect.payload["bindings"]), 3)
        self.assertNotIn("salvaged_worker_payloads", effect.payload)


class AnalystWaveSalvageFoldTests(unittest.TestCase):
    def _fold(self, payload: dict, salvaged: tuple[dict, ...] = ()):
        context = _context(salvaged)
        update = ZhongshuAnalystState._review_update(context, payload)
        if update is None:
            return None
        return ReviewState(
            revision_id=REVISION,
            salvaged_worker_payloads=update.salvaged_worker_payloads or (),
        )

    def test_fail_payload_folds_salvage(self) -> None:
        folded = self._fold(
            {
                "action": "FAIL",
                "salvaged_worker_payloads": [_salvaged_row("zhongshu_analyst-worker-02")],
            }
        )

        self.assertEqual(
            [row["worker_id"] for row in folded.salvaged_worker_payloads],
            ["zhongshu_analyst-worker-02"],
        )

    def test_payload_without_key_preserves_carried_set(self) -> None:
        from orchestrator.domain.context import apply_review_update

        context = _context((_salvaged_row("zhongshu_analyst-worker-02"),))
        update = ZhongshuAnalystState._review_update(
            context,
            {"action": "FAIL", "task_reviews": []},
        )
        folded = apply_review_update(context.review, update)

        self.assertEqual(
            [row["worker_id"] for row in folded.salvaged_worker_payloads],
            ["zhongshu_analyst-worker-02"],
        )

    def test_success_payload_with_empty_list_resets(self) -> None:
        folded = self._fold(
            {
                "action": "EVIDENCE_PACKET_READY",
                "salvaged_worker_payloads": [],
            },
            salvaged=(_salvaged_row("zhongshu_analyst-worker-02"),),
        )

        self.assertEqual(folded.salvaged_worker_payloads, ())


if __name__ == "__main__":
    unittest.main()
