from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).with_name("review_orchestrator_v2.py")
spec = importlib.util.spec_from_file_location("review_orchestrator_v2_under_test", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)


class ReplyHandlingTests(unittest.TestCase):
    def test_freeze_check_reads_nested_solver_plan_groups(self):
        orchestrator = object.__new__(module.OrchestratorV2)
        orchestrator.task_id = "task-20260820-test"
        orchestrator._state = {
            "plan_id": "plan-000001",
            "plan_version": 1,
            "skill_lock_hash": "",
        }

        frozen = orchestrator._build_frozen_plan(
            {
                "role": "review-solver",
                "action": "READY_FOR_CRITIC",
                "plan": {
                    "plan_id": "plan-000001",
                    "version": 2,
                        "groups": [
                        {
                            "group_id": "group-000001",
                            "objective": "Validate the proposal output",
                            "items": [
                                {
                                    "item_id": "item-000001",
                                    "title": "Inspect proposal output",
                                    "objective": "Verify generated output and warnings",
                                }
                            ],
                        }
                    ],
                    "dependencies": [],
                },
            },
            {"evidence": [{"evidence_id": "ev-000001"}]},
        )

        self.assertIsNotNone(frozen)
        self.assertEqual(len(frozen["groups"]), 1)
        self.assertEqual(frozen["groups"][0]["group_id"], "group-000001")
        self.assertEqual(frozen["version"], 2)

    def test_freeze_check_rejects_group_without_items(self):
        orchestrator = object.__new__(module.OrchestratorV2)
        orchestrator.task_id = "task-20260820-test"
        orchestrator._state = {
            "plan_id": "plan-000001",
            "plan_version": 1,
            "skill_lock_hash": "",
        }

        frozen = orchestrator._build_frozen_plan(
            {
                "role": "review-solver",
                "action": "READY_FOR_CRITIC",
                "plan": {
                    "groups": [
                        {
                            "group_id": "group-000001",
                            "title": "Empty group",
                            "items": [],
                        }
                    ]
                },
            },
            {"evidence": []},
        )

        self.assertIsNone(frozen)

    def test_find_agent_comments_does_not_fallback_to_other_agent(self):
        comments = [
            {
                "id": "wrong-agent",
                "author_type": "agent",
                "author_id": "solver-1",
                "created_at": "2026-08-19T10:00:01Z",
                "content": json.dumps({"action": "BLOCKED", "role": "review-solver"}),
            }
        ]

        result = module.find_agent_comments(comments, "analyst-1", "2026-08-19T09:59:59Z")

        self.assertEqual(result, [])

    def test_payload_role_must_match_requested_role_when_declared(self):
        self.assertTrue(module.payload_role_matches({"action": "BLOCKED"}, "analyst"))
        self.assertTrue(module.payload_role_matches({"role": "review-analyst"}, "analyst"))
        self.assertFalse(module.payload_role_matches({"role": "review-solver"}, "analyst"))

    def test_find_latest_ignores_system_runtime_and_truncated_comments(self):
        comments = [
            {
                "id": "system-1",
                "type": "system",
                "author_type": "agent",
                "author_id": "agent-1",
                "created_at": "2026-08-17T10:00:01Z",
                "content": "agent produced no new messages for 10m0s",
            },
            {
                "id": "system-2",
                "author_type": "agent",
                "author_id": "agent-1",
                "created_at": "2026-08-17T10:00:02Z",
                "content": "Output data may contain inappropriate content",
            },
            {
                "id": "truncated-1",
                "type": "comment",
                "author_type": "agent",
                "author_id": "agent-1",
                "created_at": "2026-08-17T10:00:03Z",
                "content_truncated": True,
                "content": '{"action":"READY_FOR_SOLVER"',
            },
            {
                "id": "valid-1",
                "type": "comment",
                "author_type": "agent",
                "author_id": "agent-1",
                "created_at": "2026-08-17T10:00:04Z",
                "content": json.dumps({"action": "READY_FOR_SOLVER"}),
            },
        ]

        result = module.find_latest_agent_comment(comments, "agent-1", "2026-08-17T09:59:59Z")

        self.assertIsNotNone(result)
        self.assertEqual(result["id"], "valid-1")

    def test_full_json_parses_but_truncated_json_does_not(self):
        full = {"action": "READY_FOR_SOLVER", "evidence": [{"evidence_id": "ev-000001"}]}
        full_comment = {"content": json.dumps(full)}
        truncated_comment = {"content": json.dumps(full)[:30], "content_truncated": True}

        self.assertEqual(module.extract_json_from_comment(full_comment), full)
        self.assertIsNone(module.extract_json_from_comment(truncated_comment))

    def test_comment_list_fetches_full_comments_by_default(self):
        with patch.object(module, "_run_cli", return_value=[] ) as run_cli:
            module.cli_comment_list("issue-1", recent=30)

        args = run_cli.call_args.args
        self.assertNotIn("--summary", args)
        self.assertIn("--recent", args)
        self.assertIn("30", args)

    def test_comment_list_can_still_request_summary_explicitly(self):
        with patch.object(module, "_run_cli", return_value=[] ) as run_cli:
            module.cli_comment_list("issue-1", recent=30, summary=True)

        self.assertIn("--summary", run_cli.call_args.args)

    def test_resume_metadata_preserves_omitted_values(self):
        metadata = module.build_metadata_update(
            "request", project_type=None, task_type=None, preserve_existing=True
        )

        self.assertEqual(metadata, {"raw_request": "request"})

    def test_new_task_metadata_keeps_legacy_defaults(self):
        metadata = module.build_metadata_update(
            "request", project_type=None, task_type=None, preserve_existing=False
        )

        self.assertEqual(metadata["project_type"], "unknown")
        self.assertEqual(metadata["task_type"], "feature")

    def test_explicit_metadata_overrides_saved_values(self):
        metadata = module.build_metadata_update(
            "request", project_type="go", task_type="review", preserve_existing=True
        )

        self.assertEqual(metadata, {
            "raw_request": "request",
            "project_type": "go",
            "task_type": "review",
        })

    def test_progress_labels_are_readable_and_format_safe(self):
        orchestrator = object.__new__(module.OrchestratorV2)
        orchestrator.task_id = "task-20260817-3b64cc"

        self.assertEqual(orchestrator._role("analyst"), "\u5206\u6790\u5E08")
        self.assertEqual(orchestrator._fmt_elapsed(123), "2 \u5206 3 \u79D2")
        self.assertEqual(orchestrator._task_ref(), "\uFF08\u4EFB\u52A1 3b64cc\uFF09")
        message = "%s\u4ECD\u5728\u5DE5\u4F5C\u4E2D\uFF0C\u5DF2\u7528\u65F6%s %s" % (
            orchestrator._role("analyst"),
            orchestrator._fmt_elapsed(123),
            orchestrator._task_ref(),
        )
        self.assertEqual(message, "\u5206\u6790\u5E08\u4ECD\u5728\u5DE5\u4F5C\u4E2D\uFF0C\u5DF2\u7528\u65F62 \u5206 3 \u79D2 \uFF08\u4EFB\u52A1 3b64cc\uFF09")


if __name__ == "__main__":
    unittest.main()
