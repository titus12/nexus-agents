"""Fix 3: the Analyst evidence packet routes straight back to its requester.

A Critic-demanded evidence round used to detour through the Solver
(transitions EVIDENCE_PACKET_READY -> ZHONGSHU_SOLVER), costing a full
solver hop (13-24 min in task-20260928-835a07) just to hand evidence over.
The requester is now recorded on the requesting wave and the fold emits a
Critic-targeted action that routes the packet back directly.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from orchestrator.adapters import FakeMulticaAdapter
from orchestrator.app import OrchestratorApp
from orchestrator.domain.context import (
    ProgressState,
    RecoveryState,
    ReviewState,
    TaskIdentity,
    WorkflowContext,
    apply_review_update,
    context_from_dto,
    context_to_dto,
)
from orchestrator.domain.events import DomainEvent
from orchestrator.domain.findings import Finding
from orchestrator.domain.policies.parallel import aggregate_zhongshu_workers
from orchestrator.domain.policies.prompts import build_prompt, critic_evidence_task
from orchestrator.domain.states import (
    ZhongshuAnalystState,
    ZhongshuSolverState,
)
from orchestrator.domain.transitions import TransitionRegistry
from orchestrator.runtime.reducer import LinearContextReducer
from orchestrator.runtime.repository import WorkflowSnapshot
from orchestrator.transport.external import ExternalMessage
from orchestrator.zhongshu_parallel import merge_analyst_evidence
from test_zhongshu_convergence_e2e import (
    _ConvergingMultica,
    _context as _e2e_context,
    _plan,
)

os.environ.setdefault("NEXUS_TEST_NO_EXTERNAL_NOTIFICATIONS", "1")


def _worker(worker_id: str = "w1") -> dict:
    return {
        "action": "EVIDENCE_PACKET_READY",
        "worker_id": worker_id,
        "phase": "ZHONGSHU",
        "requirements": [
            {"requirement_id": "R-1", "statement": "keep p99 under 200ms"}
        ],
        "task_proposals": [],
        "candidate_items": [],
        "candidate_groups": [],
        "evidence_updates": [
            {
                "evidence_id": "e1",
                "conclusion": "the service uses postgres",
                "source": "cmd/db.py",
                "source_type": "code",
                "decision_relevance": "risk",
            }
        ],
    }


def _context(
    state: str = "ZHONGSHU_ANALYST",
    *,
    evidence_requester: str | None = None,
) -> WorkflowContext:
    return WorkflowContext(
        identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
        progression=ProgressState(state, 3, "2026-09-28T00:00:00Z"),
        review=ReviewState(
            revision_id="R1",
            plan={"items": [{"item_id": "i1"}]},
            evidence_requester=evidence_requester,
        ),
    )


def _completed_event(aggregate: dict, sequence: int = 4) -> DomainEvent:
    return DomainEvent(
        name="NODE_COMPLETED",
        task_id="task-1",
        sequence=sequence,
        payload={
            **aggregate,
            "node_run_id": "node-1",
            "aggregate": aggregate,
        },
        occurred_at="2026-09-28T00:01:00Z",
        event_id=f"evt-{sequence}",
    )


class MergeTargetActionTests(unittest.TestCase):
    """The merge picks the return channel without touching the other one."""

    def test_default_merge_keeps_the_solver_channel(self) -> None:
        aggregate = merge_analyst_evidence(
            "task-1", "R1", [_worker()]
        )
        self.assertEqual(aggregate["action"], "READY_FOR_SOLVER")
        # The solver channel reads the packet under ``plan`` (stage routing).
        self.assertIn("plan", aggregate)
        self.assertIn("evidence_packet", aggregate)

    def test_critic_target_omits_the_plan_key(self) -> None:
        aggregate = merge_analyst_evidence(
            "task-1", "R1", [_worker()],
            target_action="EVIDENCE_PACKET_READY_FOR_CRITIC",
        )
        self.assertEqual(aggregate["action"], "EVIDENCE_PACKET_READY_FOR_CRITIC")
        # The packet is an empty graph; clobbering review.plan with it would
        # leave the Critic a snapshot without its plan.
        self.assertNotIn("plan", aggregate)
        self.assertIn("evidence_packet", aggregate)

    def test_unknown_target_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            merge_analyst_evidence(
                "task-1", "R1", [_worker()], target_action="READY_FOR_CRITIC"
            )


class AggregateRoutingTests(unittest.TestCase):
    """The fan-in routes by the recorded requester."""

    def test_critic_requester_routes_to_the_critic_edge(self) -> None:
        aggregate = aggregate_zhongshu_workers(
            "task-1", "ZHONGSHU_ANALYST", "R1", "hash",
            [_worker()], None, evidence_requester="ZHONGSHU_CRITIC",
        )
        self.assertEqual(aggregate["action"], "EVIDENCE_PACKET_READY_FOR_CRITIC")
        self.assertNotIn("plan", aggregate)

    def test_other_requesters_keep_the_solver_channel(self) -> None:
        for requester in ("", "ZHONGSHU_SOLVER", "ZHONGSHU_FREEZE_CHECK"):
            aggregate = aggregate_zhongshu_workers(
                "task-1", "ZHONGSHU_ANALYST", "R1", "hash",
                [_worker()], None, evidence_requester=requester,
            )
            self.assertEqual(aggregate["action"], "READY_FOR_SOLVER", requester)
            self.assertIn("plan", aggregate)


class RequesterLifecycleTests(unittest.TestCase):
    """The requester is recorded on the request and dropped on consumption."""

    def test_requesting_wave_records_the_requester(self) -> None:
        context = _context("ZHONGSHU_SOLVER")
        event = _completed_event(
            {"action": "REQUEST_ANALYST_EVIDENCE"}, sequence=5
        )

        decision = ZhongshuSolverState().handle(context, event)

        self.assertEqual(decision.transition.action, "REQUEST_ANALYST_EVIDENCE")
        self.assertIsNotNone(decision.update.review)
        self.assertEqual(
            decision.update.review.evidence_requester, "ZHONGSHU_SOLVER"
        )

    def test_set_and_clear_in_one_update_resolves_to_set(self) -> None:
        context = _context("ZHONGSHU_SOLVER")
        event = _completed_event(
            {"action": "REQUEST_ANALYST_EVIDENCE"}, sequence=5
        )
        decision = ZhongshuSolverState().handle(context, event)

        snapshot = LinearContextReducer().apply(
            WorkflowSnapshot("task-1", context, 3), decision
        )

        self.assertEqual(
            snapshot.context.review.evidence_requester, "ZHONGSHU_SOLVER"
        )

    def test_consumed_requester_is_cleared_on_the_evidence_wave(self) -> None:
        context = _context(evidence_requester="ZHONGSHU_CRITIC")
        aggregate = aggregate_zhongshu_workers(
            "task-1", "ZHONGSHU_ANALYST", "R1", "hash",
            [_worker()], None, evidence_requester="ZHONGSHU_CRITIC",
        )

        decision = ZhongshuAnalystState().handle(context, _completed_event(aggregate))

        self.assertEqual(
            decision.transition.action, "EVIDENCE_PACKET_READY_FOR_CRITIC"
        )
        self.assertTrue(decision.update.review.clear_evidence_requester)
        snapshot = LinearContextReducer().apply(
            WorkflowSnapshot("task-1", context, 3), decision
        )
        self.assertIsNone(snapshot.context.review.evidence_requester)

    def test_evidence_wave_folds_the_packet_and_keeps_the_plan(self) -> None:
        context = _context(evidence_requester="ZHONGSHU_CRITIC")
        aggregate = aggregate_zhongshu_workers(
            "task-1", "ZHONGSHU_ANALYST", "R1", "hash",
            [_worker()], None, evidence_requester="ZHONGSHU_CRITIC",
        )

        decision = ZhongshuAnalystState().handle(context, _completed_event(aggregate))

        self.assertEqual(
            decision.update.review.evidence_packet.get("evidence_updates"),
            aggregate["evidence_packet"].get("evidence_updates"),
        )
        # The Critic channel never touches review.plan.
        self.assertIsNone(decision.update.review.plan)

    def test_fsm_routes_the_critic_packet_to_the_critic_state(self) -> None:
        registry = TransitionRegistry.default()

        target = registry.target_for(
            "ZHONGSHU_ANALYST", "EVIDENCE_PACKET_READY_FOR_CRITIC", None
        )

        self.assertEqual(target, "ZHONGSHU_CRITIC")
        self.assertIn(
            "EVIDENCE_PACKET_READY_FOR_CRITIC",
            ZhongshuAnalystState.supported_actions,
        )

    def test_analyst_dispatch_carries_the_requester(self) -> None:
        context = _context(evidence_requester="ZHONGSHU_CRITIC")

        effect = ZhongshuAnalystState()._dispatch_effect(
            context,
            "ZHONGSHU_ANALYST",
            canonical_requirements=({"requirement_id": "R-1"},),
        )

        self.assertEqual(effect.payload.get("evidence_requester"), "ZHONGSHU_CRITIC")

    def test_apply_review_update_sentinel_precedence(self) -> None:
        from orchestrator.domain.context import ReviewUpdate

        current = ReviewState(
            revision_id="R1", evidence_requester="ZHONGSHU_CRITIC"
        )
        # Explicit new value beats the clear sentinel.
        kept = apply_review_update(
            current,
            ReviewUpdate(
                evidence_requester="ZHONGSHU_SOLVER",
                clear_evidence_requester=True,
            ),
        )
        self.assertEqual(kept.evidence_requester, "ZHONGSHU_SOLVER")
        # Clear sentinel alone drops the value.
        cleared = apply_review_update(
            kept,
            ReviewUpdate(clear_evidence_requester=True),
        )
        self.assertIsNone(cleared.evidence_requester)
        # No update fields keeps the value.
        unchanged = apply_review_update(
            cleared,
            ReviewUpdate(revision_id="R2"),
        )
        self.assertIsNone(unchanged.evidence_requester)

    def test_dto_roundtrip_preserves_the_requester(self) -> None:
        context = _context(evidence_requester="ZHONGSHU_CRITIC")

        restored = context_from_dto(context_to_dto(context))

        self.assertEqual(restored.review.evidence_requester, "ZHONGSHU_CRITIC")

    def test_dto_without_the_field_keeps_the_default(self) -> None:
        dto = context_to_dto(_context())
        dto["review"].pop("evidence_requester")

        restored = context_from_dto(dto)

        self.assertIsNone(restored.review.evidence_requester)


class CriticEvidencePromptTests(unittest.TestCase):
    """The plan-level Critic prompt carries a bounded evidence slice."""

    @staticmethod
    def _context(*, records: list, responses: list | None = None,
                 findings: tuple = ()) -> WorkflowContext:
        return WorkflowContext(
            identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
            progression=ProgressState("ZHONGSHU_SOLVER", 3, "2026-09-28T00:00:00Z"),
            review=ReviewState(
                revision_id="R1",
                plan={"items": [{"item_id": "i1"}]},
                findings=findings,
                evidence_packet={
                    "evidence_updates": records,
                    "finding_responses": responses or [],
                },
            ),
        )

    @staticmethod
    def _record(index: int) -> dict:
        return {
            "evidence_id": f"ev-{index:02d}",
            "requirement_id": "req-000001",
            "decision_relevance": "risk",
            "conclusion": f"conclusion {index}",
            "source": f"src/{index}.py",
        }

    def test_critic_prompt_carries_the_packet(self) -> None:
        context = self._context(
            records=[self._record(1)],
            responses=[
                {
                    "finding_id": "f-open",
                    "answer": "verified at src/1.py:1",
                    "suggested_disposition": "CLOSE",
                }
            ],
            findings=(
                Finding(finding_id="f-open", severity="P1", status="OPEN"),
            ),
        )

        content = build_prompt(
            context, target_state="ZHONGSHU_CRITIC"
        ).content

        self.assertIn("[Evidence packet]", content)
        self.assertIn("ev-01", content)
        self.assertIn("conclusion 1", content)
        self.assertIn("f-open", content)
        self.assertIn("verified at src/1.py:1", content)

    def test_other_states_carry_no_evidence_section(self) -> None:
        context = self._context(records=[self._record(1)])

        for state in ("ZHONGSHU_SOLVER", "ZHONGSHU_ANALYST", "ZHONGSHU_FREEZE_CHECK"):
            content = build_prompt(context, target_state=state).content
            self.assertNotIn("[Evidence packet]", content, state)

    def test_section_is_bounded_and_demand_first(self) -> None:
        # Twelve packet records, two named by an open finding's supporting
        # evidence but sitting at the packet's tail: the demanded records must
        # survive the cap exactly like the binding slices.
        records = [self._record(index) for index in range(1, 13)]
        tail = [self._record(97), self._record(98)]
        context = self._context(
            records=records + tail,
            findings=(
                Finding(
                    finding_id="f-open",
                    severity="P1",
                    status="OPEN",
                    supporting_evidence=("ev-97", "ev-98"),
                ),
            ),
        )

        content = critic_evidence_task(context)

        shown = [
            line for line in content.splitlines() if line.startswith("- ev-")
        ]
        self.assertEqual(len(shown), 8)
        self.assertIn("ev-97", content)
        self.assertIn("ev-98", content)
        self.assertNotIn("ev-12", content)

    def test_no_packet_omits_the_section(self) -> None:
        context = WorkflowContext(
            identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
            progression=ProgressState("ZHONGSHU_SOLVER", 3, "2026-09-28T00:00:00Z"),
            review=ReviewState(revision_id="R1", plan={"items": []}),
        )

        self.assertEqual(critic_evidence_task(context), "")


class AnalystContractQuoteRuleTests(unittest.TestCase):
    """Answers must carry verbatim clauses and facts, not pointers/opinions."""

    def test_analyst_rules_forbid_pointer_answers_and_opinion_as_evidence(
        self,
    ) -> None:
        from orchestrator.contracts import contract_for_state

        rules = " ".join(contract_for_state("ZHONGSHU_ANALYST").prompt_rules)
        self.assertIn("verbatim", rules)
        self.assertIn("not evidence", rules)

    def test_quote_budget_reaches_the_first_notice(self) -> None:
        # The 30-line/4096-char cap lived only in the skill and the rejection
        # feedback; workers kept quoting whole functions (task-20260929-dad75d:
        # five oversized-quote rejections in one run).  It must also ride the
        # contract rules and the [Evidence task] block the worker answers.
        from orchestrator.contracts import contract_for_state

        rules = " ".join(contract_for_state("ZHONGSHU_ANALYST").prompt_rules)
        self.assertIn("4096", rules)
        context = WorkflowContext(
            identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
            progression=ProgressState("ZHONGSHU_ANALYST", 3, "2026-09-28T00:00:00Z"),
            review=ReviewState(
                revision_id="R1",
                plan={"items": [{"item_id": "i1"}]},
                findings=(Finding(finding_id="f-1", severity="P1", status="OPEN"),),
            ),
        )
        content = build_prompt(context, target_state="ZHONGSHU_ANALYST").content
        self.assertIn("4096", content)
        self.assertIn("one contiguous span", content)


class _EvidenceDetourMultica(_ConvergingMultica):
    """The Critic demands evidence once; the packet must come straight back.

    Wave script: baseline convergence, except the first Critic group review
    answers REQUEST_ANALYST_EVIDENCE (one finding with a verifiable evidence
    target) and the Analyst wave that carries the demand answers with a full
    evidence packet.  The second group review approves, so the run still
    reaches DONE.
    """

    def __init__(self, evidence_site: str) -> None:
        super().__init__()
        self.evidence_site = evidence_site
        self.critic_review_waves = 0
        self.evidence_waves = 0
        self.review_wave_contexts: list = []

    def _item_verdict(self, item_id: str) -> tuple:
        return "TASK_APPROVED", []

    def _evidence_demand_reply(self, request) -> None:
        context = request.context
        self._reply(
            request,
            {
                "action": "REQUEST_ANALYST_EVIDENCE",
                "group_id": str(context.get("group_id") or ""),
                "reviewed_plan_hash": str(context.get("plan_hash") or ""),
                "findings": [
                    {
                        "finding_id": "finding-evidence-db",
                        "severity": "P1",
                        "status": "OPEN",
                        "category": "acceptance",
                        "target": "item-000001",
                        "claim": "the storage engine is unverified",
                        "decision": "REVISE",
                        "evidence_strength": "inference",
                        "required_action": (
                            "verify which database engine the service uses"
                        ),
                        "item_id": "item-000001",
                        "group_id": "group-000001",
                        "evidence_targets": [
                            {"path": self.evidence_site, "symbol": "ENGINE"}
                        ],
                    }
                ],
                "finding_responses": [],
                "review_checks": {
                    "requirement_coverage": [],
                    "boundary": [],
                    "dependencies": [],
                    "acceptance": [],
                    "risks": [],
                },
            },
        )

    def _evidence_packet_reply(self, request) -> None:
        self.evidence_waves += 1
        citation = Path(self.evidence_site).as_posix()
        self._reply(
            request,
            {
                "action": "EVIDENCE_PACKET_READY",
                "requirements": _plan()["requirements"],
                "task_proposals": [],
                "candidate_items": [],
                "candidate_groups": [],
                "note": f"verified at {citation}:1",
                "finding_responses": [
                    {
                        "finding_id": "finding-evidence-db",
                        "answer": f"ENGINE is defined at {citation}:1",
                        "evidence_ids": ["evidence-db-1"],
                        "suggested_disposition": "CLOSE",
                    }
                ],
                "evidence_updates": [
                    {
                        "evidence_id": "evidence-db-1",
                        "requirement_id": "req-000001",
                        "decision_relevance": "risk",
                        "source": citation,
                        "conclusion": "the service stores data in postgres",
                    }
                ],
            },
        )

    def dispatch(self, request):
        receipt = FakeMulticaAdapter.dispatch(self, request)
        context = request.context
        if (
            context.get("contract_mode")
            or context.get("zhongshu_dispatch_mode") == "requirement_contract"
        ):
            self._reply_requirement_contract(request)
            return receipt
        target = request.target_state
        mode = str(context.get("zhongshu_dispatch_mode") or "")
        if target == "ZHONGSHU_SOLVER" and mode == "group_revise":
            self._reply_group_revise(request)
        elif target == "ZHONGSHU_SOLVER":
            self._reply_solver(request)
        elif target == "ZHONGSHU_CRITIC" and mode == "group_review":
            self.critic_review_waves += 1
            if self.critic_review_waves == 1:
                self._evidence_demand_reply(request)
            else:
                self.review_wave_contexts.append(request.context)
                self._reply_group_review(request)
        elif target == "ZHONGSHU_ANALYST" and context.get("evidence_demand"):
            self._evidence_packet_reply(request)
        elif target in (
            "MENXIA_GROUP_SOLVER",
            "MENXIA_GROUP_ANALYST",
            "MENXIA_GROUP_CRITIC",
        ):
            self._reply_group_wave(request)
        else:
            action = {
                "ZHONGSHU_ANALYST": "READY_FOR_SOLVER",
                "ZHONGSHU_FREEZE_CHECK": "FREEZE_APPROVED",
                "MENXIA_GROUP_GATE": "APPROVE_GROUP",
            }.get(target)
            if action:
                payload: dict[str, object] = {"action": action}
                if target == "ZHONGSHU_ANALYST":
                    payload["findings"] = []
                self._reply(request, payload)
        return receipt


class EvidenceDirectRoutingEndToEndTests(unittest.TestCase):
    """The evidence packet routes Critic-ward inside the real FSM run."""

    def _run(self) -> tuple[object, _EvidenceDetourMultica]:
        with tempfile.TemporaryDirectory() as directory:
            site = Path(directory) / "evidence_site.py"
            site.write_text("ENGINE = \"postgres\"\n", encoding="utf-8")
            adapter = _EvidenceDetourMultica(site.as_posix())
            app = OrchestratorApp(
                _e2e_context(),
                root=directory,
                multica=adapter,
                poll_interval=0,
                timeout_seconds=15,
            )
            self.assertTrue(app.run())
            snapshot = app.repository.load("task-e2e")
            return snapshot, adapter

    def test_evidence_round_routes_back_to_the_critic_without_a_solver_hop(
        self,
    ) -> None:
        snapshot, adapter = self._run()

        self.assertEqual(snapshot.context.progression.state, "DONE")
        # One demand wave, one evidence wave.  The single open finding is
        # answered by one analyst worker, not repeated by all three
        # (analyst_default_workers=3 in the shared e2e context).
        self.assertEqual(adapter.critic_review_waves, 2)
        self.assertEqual(adapter.evidence_waves, 1)
        dispatched = adapter.dispatched
        demand_index = next(
            index
            for index, request in enumerate(dispatched)
            if request.target_state == "ZHONGSHU_CRITIC"
        )
        evidence_index = next(
            index
            for index, request in enumerate(dispatched)
            if request.context.get("evidence_demand")
        )
        between = [
            request.target_state
            for request in dispatched[demand_index + 1 : evidence_index]
        ]
        # The detour is gone: no Solver hop between the demand and the packet.
        self.assertNotIn("ZHONGSHU_SOLVER", between)

    def test_the_packet_lands_in_the_review_and_the_requester_is_cleared(
        self,
    ) -> None:
        snapshot, _ = self._run()

        review = snapshot.context.review
        packet = review.evidence_packet or {}
        self.assertTrue(packet.get("evidence_updates"))
        self.assertTrue(packet.get("requirements"))
        # Consumed on the evidence wave's clean join.
        self.assertIsNone(review.evidence_requester)
        # The Critic-channel fold must not clobber the real plan.
        self.assertEqual(
            [item.item_id for item in review.task_items],
            ["item-000001", "item-000002"],
        )
        # The demand finding survived the round trip into the review.
        self.assertIn(
            "finding-evidence-db",
            [finding.finding_id for finding in review.findings],
        )

    def test_the_re_dispatched_critic_wave_sees_the_new_evidence(self) -> None:
        _, adapter = self._run()

        self.assertTrue(adapter.review_wave_contexts)
        payload = json.dumps(
            adapter.review_wave_contexts[-1], ensure_ascii=False, default=str
        )
        # The direct-routed packet must reach the Critic's re-dispatch, or it
        # asks for evidence the run already holds.
        self.assertIn("evidence-db-1", payload)
        self.assertIn("the service stores data in postgres", payload)


if __name__ == "__main__":
    unittest.main()
