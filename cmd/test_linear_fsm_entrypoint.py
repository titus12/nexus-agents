from __future__ import annotations

import tempfile
import unittest

from orchestrator.adapters import FakeMulticaAdapter
from orchestrator.app import OrchestratorApp
from orchestrator.domain.context import ProgressState, RequestState, TaskIdentity, WorkflowContext
from orchestrator.transport.external import ExternalMessage


class _ScriptedMultica(FakeMulticaAdapter):
    def dispatch(self, request):
        receipt = super().dispatch(request)
        actions = {
            "ZHONGSHU_ANALYST": "READY_FOR_SOLVER",
            "ZHONGSHU_SOLVER": "READY_FOR_CRITIC",
            "ZHONGSHU_CRITIC": "APPROVE_FREEZE",
            "ZHONGSHU_FREEZE_CHECK": "FREEZE_APPROVED",
            "MENXIA_ITEM_SOLVER": "FEASIBLE",
            "MENXIA_ITEM_ANALYST": "EVIDENCE_SUFFICIENT",
            "MENXIA_ITEM_CRITIC": "APPROVE_ITEM",
            "MENXIA_GROUP_GATE": "APPROVE_GROUP",
        }
        action = actions.get(request.target_state)
        if action:
            self.queue_reply(
                request.request_id,
                ExternalMessage(
                    request.agent_id,
                    {
                        "action": action,
                        "task_id": request.task_id,
                        "request_id": request.request_id,
                        "phase": request.phase,
                        "role": request.role,
                        "revision_id": request.context.get("revision_id", ""),
                        "plan_hash": (
                            "entry-plan"
                            if request.target_state == "ZHONGSHU_SOLVER"
                            else request.context.get("plan_hash", "")
                        ),
                        "findings": [],
                        "review_summary": (
                            f"review-{request.request_id}"
                            if request.target_state == "ZHONGSHU_CRITIC"
                            else ""
                        ),
                    },
                    request.request_id,
                ),
            )
        return receipt


def _context() -> WorkflowContext:
    return WorkflowContext(
        identity=TaskIdentity("task-entry", "issue-entry", "", "request-entry"),
        progression=ProgressState("REQUEST_INTAKE", 0, "2026-09-10T00:00:00Z"),
        request=RequestState(raw_request="run a review", project_type="go", task_type="review"),
    )


class LinearEntrypointTests(unittest.TestCase):
    def test_application_runs_only_through_new_engine(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter = _ScriptedMultica()
            app = OrchestratorApp(
                _context(),
                root=directory,
                multica=adapter,
                poll_interval=0,
                timeout_seconds=2,
            )

            self.assertTrue(app.run())
            snapshot = app.repository.load("task-entry")

        self.assertEqual(snapshot.context.progression.state, "DONE")
        self.assertEqual(snapshot.context.progression.sequence, 9)
        self.assertEqual(len(adapter.dispatched), 15)
        self.assertTrue(all(request.request_id for request in adapter.dispatched))


if __name__ == "__main__":
    unittest.main()
