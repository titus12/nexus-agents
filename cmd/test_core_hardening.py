from __future__ import annotations

import json
import shutil
import uuid
import unittest
from pathlib import Path

from orchestrator.context import StateContext
from orchestrator.events import Event
from orchestrator.adapters import _response_contract_for
from orchestrator.models import AgentRequest
from orchestrator.persistence import JsonStateStore
from orchestrator.states import _zhongshu_solver_prompt_v2
from orchestrator.transitions import TransitionPolicy


class CoreHardeningTests(unittest.TestCase):
    def test_solver_retry_exhaustion_can_reach_blocked(self) -> None:
        ctx = StateContext(task_id="task-1", workflow_state="ZHONGSHU_SOLVER")
        ctx.reply_retry_count = ctx.max_reply_retries
        event = Event(
            "AGENT_REPLY_REJECTED",
            {
                "reason": "SOLVER_ITEM_FIELD_MISSING:0:acceptance_signals",
                "resume_state": "ZHONGSHU_SOLVER",
                "max_retries": ctx.max_reply_retries,
            },
        )
        transition = TransitionPolicy.resolve(ctx, event)
        TransitionPolicy.validate_target(
            ctx,
            event,
            transition,
            {"ZHONGSHU_SOLVER": object(), "BLOCKED": object()},
        )
        self.assertEqual(transition.to_state, "BLOCKED")

    def test_solver_prompt_example_contains_every_required_item_field(self) -> None:
        ctx = StateContext(
            task_id="task-1",
            workflow_state="ZHONGSHU_SOLVER",
            raw_request="turn requirements into tasks",
            request_payload={
                "analyst_plan": {
                    "requirements": [
                        {
                            "requirement_id": "REQ-001",
                            "statement": "one requirement",
                            "priority": "must",
                        }
                    ],
                    "candidate_items": [],
                    "scope": {},
                }
            },
        )
        prompt = json.loads(_zhongshu_solver_prompt_v2(ctx))
        required = set(prompt["required_output"]["formal_item_required_fields"])
        example_item = prompt["output_example"]["plan"]["items"][0]
        example_group_item = prompt["output_example"]["plan"]["groups"][0]["items"][0]
        self.assertTrue(required.issubset(example_item))
        self.assertEqual(required, set(example_group_item))

    def test_transport_contract_exposes_solver_item_schema(self) -> None:
        request = AgentRequest(
            task_id="task-1",
            request_id="request-1",
            agent_id="solver-1",
            role="review-solver",
            phase="ZHONGSHU",
            prompt="",
            idempotency_key="request-1",
        )
        contract = _response_contract_for(request)
        self.assertEqual(
            set(contract["formal_item_required_fields"]),
            {
                "item_id",
                "title",
                "objective",
                "source_requirement_ids",
                "dependencies",
                "acceptance_signals",
                "unknowns",
                "risks",
                "parallelizable",
            },
        )

    def test_invalid_primary_state_is_restored_from_last_good_backup(self) -> None:
        root = Path.cwd() / "cmd" / f".core-hardening-{uuid.uuid4().hex}"
        root.mkdir(parents=True)
        try:
            store = JsonStateStore(root)
            ctx = StateContext(task_id="task-1", raw_request="原始需求")
            store.save_state(ctx)
            ctx.workflow_state = "ZHONGSHU_ANALYST"
            store.save_state(ctx)

            store.state_path.write_text('{"raw_request":"broken",', encoding="utf-8")
            loaded = store.load()

            self.assertEqual(loaded.task_id, "task-1")
            self.assertEqual(loaded.raw_request, "原始需求")
            self.assertEqual(
                loaded.last_error["code"] if loaded.last_error else "",
                "STATE_RECOVERED_FROM_BACKUP",
            )
            json.loads(store.state_path.read_text(encoding="utf-8"))
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
