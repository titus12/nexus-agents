from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from orchestrator.adapters import FakeMulticaAdapter, MulticaCliAdapter
from orchestrator.app import OrchestratorApp, _resolve_task_root
from orchestrator.comment_feed import IncrementalCommentFeed
from orchestrator.concurrency import ConcurrencyAdmission, ConcurrencyLimits
from orchestrator.context import StateContext
from orchestrator.events import Event
from orchestrator.lifecycle import LifecyclePaths
from orchestrator.models import AgentRequest, DispatchReceipt, ExternalMessage, Finding
from orchestrator.parallel_runtime import ParallelCoordinatorDriver, ParallelWorker
from orchestrator.states import _validate_state_payload
from orchestrator.states import build_state_registry
from orchestrator.state_machine import StateMachine
from test_analyst_contract_v31 import VALID_PLAN


class DeterministicHardeningTests(unittest.TestCase):
    def test_task_root_cannot_escape_runs_root(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertEqual(_resolve_task_root(root, "task-1"), (root / "task-1").resolve())
            with self.assertRaises(ValueError):
                _resolve_task_root(root, "..\\outside")

    def test_restart_recovery_uses_app_dispatch_router(self):
        with tempfile.TemporaryDirectory() as temp:
            ctx = StateContext(task_id="task-1", workflow_state="ZHONGSHU_ANALYST")
            app = OrchestratorApp(ctx, Path(temp), multica=FakeMulticaAdapter())
            app.store.save_state(ctx)
            calls = []
            app._dispatch_pending = lambda current_ctx, state: calls.append(
                (current_ctx.task_id, state.name)
            )
            app._ensure_started()
            self.assertEqual(calls, [("task-1", "ZHONGSHU_ANALYST")])

    def test_critic_finding_contract_is_complete_and_unique(self):
        valid = {
            "action": "REQUEST_SOLVER_REVISION",
            "findings": [{
                "finding_id": "f-1",
                "severity": "P1",
                "claim": "evidence is missing",
                "decision": "REQUEST_SOLVER_REVISION",
            }],
        }
        self.assertEqual(_validate_state_payload("MENXIA_ITEM_CRITIC", valid), "")
        invalid = dict(valid)
        invalid["findings"] = [dict(valid["findings"][0], finding_id="")]
        self.assertEqual(
            _validate_state_payload("MENXIA_ITEM_CRITIC", invalid),
            "MENXIA_CRITIC_FINDING_ID_MISSING:0",
        )

    def test_existing_finding_severity_cannot_be_downgraded(self):
        with tempfile.TemporaryDirectory() as temp:
            ctx = StateContext(task_id="task-1", findings=[{
                "finding_id": "f-1", "severity": "P1", "status": "OPEN",
                "owner_role": "review-critic",
            }])
            app = OrchestratorApp(ctx, Path(temp), multica=FakeMulticaAdapter())
            app._update_findings({"findings": [{
                "finding_id": "f-1",
                "severity": "P3",
                "status": "RESOLVED",
            }]})
            finding = ctx.finding_objects()[0]
            self.assertEqual(finding.severity, "P1")
            self.assertEqual(finding.owner_role, "review-critic")

    def test_cursor_keeps_seen_id_order_when_pruned(self):
        feed = IncrementalCommentFeed(max_seen_ids=128)
        comments = [
            {"id": f"c-{index}", "created_at": f"2026-09-03T10:{index // 60:02d}:{index % 60:02d}Z"}
            for index in range(129)
        ]
        feed.read("issue:task", lambda _since: comments)
        self.assertEqual(len(feed._cursors["issue:task"].seen_ids), 128)
        self.assertEqual(feed._cursors["issue:task"].seen_order[0], "c-1")

    def test_lease_refresh_extends_live_lease(self):
        now = [0.0]
        from datetime import datetime, timezone, timedelta

        clock = lambda: datetime.now(timezone.utc) + timedelta(seconds=now[0])
        admission = ConcurrencyAdmission(ConcurrencyLimits(lease_ttl_seconds=2), clock=clock)
        lease = admission.try_acquire("task", "ZHONGSHU_CRITIC", "worker", "rev")
        self.assertIsNotNone(lease)
        original_expiry = float(lease.expires_at)
        now[0] = 1.0
        self.assertTrue(admission.refresh(lease.lease_id))
        refreshed = admission.snapshot().active_leases[0]
        self.assertGreater(float(refreshed.expires_at), original_expiry)

    def test_serial_update_uses_newest_reply_from_batch(self):
        class Adapter:
            def poll(self, _request):
                return [
                    ExternalMessage("agent", {
                        "task_id": "task-1", "request_id": "request-1",
                        "phase": "ZHONGSHU", "role": "review-analyst",
                        "action": "BLOCKED", "reason": "old", "plan": VALID_PLAN,
                    }, "old"),
                    ExternalMessage("agent", {
                        "task_id": "task-1", "request_id": "request-1",
                        "phase": "ZHONGSHU", "role": "review-analyst",
                        "action": "READY_FOR_SOLVER", "plan": VALID_PLAN,
                    }, "new"),
                ]

        ctx = StateContext(task_id="task-1", workflow_state="ZHONGSHU_ANALYST")
        from orchestrator.states import ZhongshuAnalystState

        state = ZhongshuAnalystState(multica=Adapter())
        ctx.active_request_id = "request-1"
        ctx.expected_agent_id = "agent"
        ctx.current_phase = "ZHONGSHU"
        ctx.current_role = "review-analyst"
        event = state.update(ctx)
        self.assertEqual(event.payload.get("action"), "READY_FOR_SOLVER")

    def test_p2_gate_does_not_treat_blocked_prefix_as_acceptance(self):
        ctx = StateContext(
            task_id="task-1",
            workflow_state="HUMAN_GATE",
            resume_state="MENXIA_ITEM_CRITIC",
            request_payload={"human_gate_purpose": "P2_RISK"},
            findings=[{"finding_id": "f-1", "severity": "P2", "status": "OPEN"}],
        )
        machine = StateMachine(ctx, build_state_registry())
        transition = machine.dispatch(Event(
            "HUMAN_DECISION_RECEIVED",
            {"answer": "BLOCKED", "resume_state": "MENXIA_ITEM_CRITIC"},
        ))
        self.assertEqual(transition.to_state, "MENXIA_ITEM_SOLVER")

    def test_unbound_inline_reply_is_not_written_for_later_recovery(self):
        with tempfile.TemporaryDirectory() as temp:
            adapter = MulticaCliAdapter(Path(temp) / "transport")
            request = AgentRequest(
                task_id="task-1",
                issue_id="SER-1",
                request_id="request-1",
                agent_id="agent-1",
                role="review-analyst",
                phase="ZHONGSHU",
                prompt="work",
                idempotency_key="key-1",
                sent_after="2026-09-03T10:00:00Z",
                dispatch_external_message_id="dispatch-1",
            )
            comment = {
                "id": "unbound",
                "author_id": "agent-1",
                "parent_id": None,
                "created_at": "2026-09-03T10:00:01Z",
                "content": '{"action":"READY_FOR_SOLVER"}',
            }
            adapter._run = lambda *_args: [comment]
            self.assertEqual(adapter.poll(request), [])
            self.assertEqual(list((Path(temp) / "transport").rglob("result.json")), [])


if __name__ == "__main__":
    unittest.main()
