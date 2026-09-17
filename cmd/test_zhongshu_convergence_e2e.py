from __future__ import annotations

import tempfile
import unittest
from collections import Counter

from orchestrator.adapters import FakeMulticaAdapter
from orchestrator.app import OrchestratorApp
from orchestrator.domain.context import (
    ProgressState,
    RequestState,
    TaskIdentity,
    WorkflowContext,
)
from orchestrator.transport.external import ExternalMessage


def _plan() -> dict:
    return {
        "plan_id": "plan-1",
        "items": [
            {
                "item_id": "item-000001",
                "group_id": "group-000001",
                "title": "Task A",
                "objective": "do A",
                "dependencies": [],
                "source_requirement_ids": ["req-000001"],
                "acceptance_signals": ["A is observable"],
            },
            {
                "item_id": "item-000002",
                "group_id": "group-000001",
                "title": "Task B",
                "objective": "do B",
                "dependencies": [],
                "source_requirement_ids": ["req-000001"],
                "acceptance_signals": ["B is observable"],
            },
        ],
        "groups": [{"group_id": "group-000001", "item_ids": ["item-000001", "item-000002"]}],
        "requirements": [
            {
                "requirement_id": "req-000001",
                "statement": "run a review",
                "priority": "must",
                "scope": "in",
                "kind": "task",
            }
        ],
    }


class _ConvergingMultica(FakeMulticaAdapter):
    """Rejects one task once, then approves; the graph must still freeze."""

    def __init__(self) -> None:
        super().__init__()
        self.item_two_rejections = 0

    def dispatch(self, request):
        receipt = super().dispatch(request)
        context = request.context
        if (
            context.get("contract_mode")
            or context.get("zhongshu_dispatch_mode") == "requirement_contract"
        ):
            self._reply_requirement_contract(request)
            return receipt
        target = request.target_state
        if target == "ZHONGSHU_SOLVER":
            self._reply_solver(request)
        elif target == "ZHONGSHU_CRITIC":
            self._reply_critic(request)
        else:
            action = {
                "ZHONGSHU_ANALYST": "READY_FOR_SOLVER",
                "ZHONGSHU_FREEZE_CHECK": "FREEZE_APPROVED",
                "MENXIA_ITEM_SOLVER": "FEASIBLE",
                "MENXIA_ITEM_ANALYST": "EVIDENCE_SUFFICIENT",
                "MENXIA_ITEM_CRITIC": "APPROVE_ITEM",
                "MENXIA_GROUP_GATE": "APPROVE_GROUP",
            }.get(target)
            if action:
                self._reply(request, {"action": action})
        return receipt

    def _reply(self, request, payload: dict) -> None:
        context = request.context
        body = {
            "task_id": request.task_id,
            "request_id": request.request_id,
            "phase": request.phase,
            "role": request.role,
            "revision_id": str(context.get("revision_id") or ""),
            "plan_hash": str(context.get("plan_hash") or ""),
        }
        body.update(payload)
        self.queue_reply(
            request.request_id,
            ExternalMessage(request.agent_id, body, request.request_id),
        )

    def _reply_requirement_contract(self, request) -> None:
        self._reply(
            request,
            {
                "action": "REQUIREMENT_CONTRACT_READY",
                "requirements": [
                    {
                        "requirement_id": "req-000001",
                        "statement": "run a review",
                        "priority": "must",
                        "scope": "in",
                        "kind": "task",
                    }
                ],
            },
        )

    def _reply_solver(self, request) -> None:
        revision = bool(request.context.get("has_current_plan"))
        payload = {
            "action": "READY_FOR_CRITIC",
            "plan": _plan(),
            "plan_hash": "plan-1",
            "changes": [],
        }
        if revision:
            # Only on a revision: the initial run has no active findings, so a
            # finding batch there would be rejected as unknown.
            payload["finding_resolutions"] = [
                {
                    "finding_id": "finding-item2",
                    "response": "clarified the acceptance signal",
                    "changed_fields": ["acceptance_signals"],
                    "evidence": [],
                    "owner_role": "review-solver",
                    "next_action": "none",
                }
            ]
            # Only the rejected task is in the batch, so the approved task must
            # be carried forward untouched.
            payload["finding_batch"] = {
                "selected_finding_ids": ["finding-item2"],
                "remaining_finding_ids": [],
                "next_action": "none",
                "progress": {},
            }
        self._reply(request, payload)

    def _reply_critic(self, request) -> None:
        context = request.context
        item_id = str(context.get("item_id") or "")
        reject = item_id == "item-000002" and self.item_two_rejections == 0
        if reject:
            self.item_two_rejections += 1
            action = "TASK_CHANGES_REQUIRED"
            findings = [
                {
                    "finding_id": "finding-item2",
                    "severity": "P1",
                    "status": "OPEN",
                    "category": "acceptance",
                    "target": item_id,
                    "claim": "the acceptance signal is not observable",
                    "required_action": "name an observable acceptance signal",
                    "evidence_strength": "inference",
                    "worker_id": "worker-critic",
                    "item_id": item_id,
                    "group_id": str(context.get("group_id") or ""),
                }
            ]
        else:
            action = "TASK_APPROVED"
            findings = []
        self._reply(
            request,
            {
                "action": action,
                "reviewed_plan_hash": str(context.get("plan_hash") or ""),
                "group_id": str(context.get("group_id") or ""),
                "item_id": item_id,
                "reviewed_task_hash": str(context.get("task_hash") or ""),
                "reviewed_dependency_hash": str(context.get("dependency_hash") or ""),
                "worker_id": request.request_id,
                "findings": findings,
                "review_checks": {
                    "requirement_coverage": [],
                    "boundary": [],
                    "dependencies": [],
                    "acceptance": [],
                    "risks": [],
                },
            },
        )


def _context() -> WorkflowContext:
    return WorkflowContext(
        identity=TaskIdentity("task-e2e", "issue-e2e", "", "request-e2e"),
        progression=ProgressState("REQUEST_INTAKE", 0, "2026-09-15T00:00:00Z"),
        request=RequestState(
            raw_request="run a review", project_type="python", task_type="review"
        ),
    )


class ZhongshuConvergenceEndToEndTests(unittest.TestCase):
    """The whole linear FSM, driven through the real application.

    One task is rejected once and then revised; the graph must still reach the
    freeze and Menxia without the rejected task dragging the run into a budget
    block, which is exactly what the shipped runs never managed to do.
    """

    def _run(self) -> tuple[object, tuple]:
        with tempfile.TemporaryDirectory() as directory:
            adapter = _ConvergingMultica()
            app = OrchestratorApp(
                _context(),
                root=directory,
                multica=adapter,
                poll_interval=0,
                timeout_seconds=5,
            )

            self.assertTrue(app.run())
            snapshot = app.repository.load("task-e2e")
            dispatched = tuple(adapter.dispatched)
        return snapshot, dispatched

    def test_rejected_task_is_revised_and_the_graph_reaches_done(self) -> None:
        snapshot, _ = self._run()

        self.assertEqual(snapshot.context.progression.state, "DONE")
        ledger = {
            record.item_id: record
            for record in snapshot.context.review.task_review_ledger
        }
        self.assertEqual(set(ledger), {"item-000001", "item-000002"})
        self.assertTrue(all(record.status == "APPROVED" for record in ledger.values()))
        # One revision round was enough: the previous behaviour burned the whole
        # eight-round budget on approvals that kept getting revoked.
        self.assertEqual(snapshot.context.review.zhongshu_revision_round, 1)
        self.assertIsNone(snapshot.context.recovery.blocked_reason)

    def test_only_the_rejected_task_is_re_reviewed(self) -> None:
        _, dispatched = self._run()

        critic_targets = [
            request
            for request in dispatched
            if request.target_state == "ZHONGSHU_CRITIC"
        ]
        # Round 1 reviews both tasks; only the rejected task is re-reviewed.
        self.assertEqual(len(critic_targets), 3)
        reviewed = Counter(
            str(request.context.get("item_id") or "") for request in critic_targets
        )
        self.assertEqual(
            reviewed, {"item-000001": 1, "item-000002": 2}
        )

    def test_the_approved_task_is_not_rewritten_or_demoted(self) -> None:
        snapshot, _ = self._run()

        ledger = {
            record.item_id: record
            for record in snapshot.context.review.task_review_ledger
        }
        # The task that was never rejected keeps its approval with a zero
        # rejection count across the revision round.
        self.assertEqual(ledger["item-000001"].status, "APPROVED")
        self.assertEqual(ledger["item-000001"].changes_rounds, 0)
        self.assertEqual(
            [finding.status for finding in snapshot.context.review.findings],
            ["RESOLVED"],
        )


class _StubbornBlockerMultica(_ConvergingMultica):
    """One task is rejected on the same P1 every round, forever."""

    def _reply_critic(self, request) -> None:
        context = request.context
        item_id = str(context.get("item_id") or "")
        if item_id != "item-000002":
            self._reply(request, {"action": "TASK_APPROVED", "item_id": item_id,
                                  "group_id": str(context.get("group_id") or ""),
                                  "reviewed_plan_hash": str(context.get("plan_hash") or ""),
                                  "reviewed_task_hash": str(context.get("task_hash") or ""),
                                  "reviewed_dependency_hash": str(context.get("dependency_hash") or ""),
                                  "worker_id": request.request_id,
                                  "findings": [],
                                  "review_checks": {}})
            return
        self._reply(
            request,
            {
                "action": "TASK_CHANGES_REQUIRED",
                "reviewed_plan_hash": str(context.get("plan_hash") or ""),
                "group_id": str(context.get("group_id") or ""),
                "item_id": item_id,
                "reviewed_task_hash": str(context.get("task_hash") or ""),
                "reviewed_dependency_hash": str(context.get("dependency_hash") or ""),
                "worker_id": request.request_id,
                "findings": [
                    {
                        "finding_id": "finding-item2",
                        "severity": "P1",
                        "status": "OPEN",
                        "category": "acceptance",
                        "target": item_id,
                        "claim": "the acceptance signal is still not observable",
                        "required_action": "name an observable acceptance signal",
                        "evidence_strength": "inference",
                        "worker_id": "worker-critic",
                        "item_id": item_id,
                        "group_id": str(context.get("group_id") or ""),
                    }
                ],
                "review_checks": {
                    "requirement_coverage": [],
                    "boundary": [],
                    "dependencies": [],
                    "acceptance": [],
                    "risks": [],
                },
            },
        )


class StubbornBlockerIsFrozenWithFollowUpsTests(unittest.TestCase):
    """A P1 the Critic never retracts must not end the run in a generic block.

    This is the token-free validation of the bounded-acceptance policy: the
    scripted Critic restates the same P1 every round, and the graph still
    reaches DONE with the residual risk recorded instead of the run dying.
    """

    def _run(self) -> tuple[object, tuple]:
        with tempfile.TemporaryDirectory() as directory:
            adapter = _StubbornBlockerMultica()
            app = OrchestratorApp(
                _context(),
                root=directory,
                multica=adapter,
                poll_interval=0,
                timeout_seconds=5,
            )

            self.assertTrue(app.run())
            snapshot = app.repository.load("task-e2e")
            dispatched = tuple(adapter.dispatched)
        return snapshot, dispatched

    def test_the_run_finishes_and_records_the_residual_p1(self) -> None:
        snapshot, _ = self._run()

        self.assertEqual(snapshot.context.progression.state, "DONE")
        findings = {
            str(finding.finding_id): finding
            for finding in snapshot.context.review.findings
        }
        self.assertEqual(findings["finding-item2"].status, "DEFERRED")
        ledger = {
            record.item_id: record.status
            for record in snapshot.context.review.task_review_ledger
        }
        # The task that was never rejected kept its approval while the stubborn
        # one was carried as a follow-up.
        self.assertEqual(ledger["item-000001"], "APPROVED")
        self.assertGreaterEqual(
            snapshot.context.review.zhongshu_revision_round, 3
        )


if __name__ == "__main__":
    unittest.main()
