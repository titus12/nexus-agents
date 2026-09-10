from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from orchestrator.app import _migrate_legacy_pending_events
from orchestrator.domain.context import ProgressState, TaskIdentity, WorkflowContext
from orchestrator.runtime.compat_effects import FileArtifactStore
from orchestrator.runtime.migration import legacy_dto_to_snapshot
from orchestrator.runtime.repository import JsonWorkflowRepository, WorkflowSnapshot


class LegacyDtoMigrationTests(unittest.TestCase):
    @staticmethod
    def _snapshot(state: str, sequence: int) -> WorkflowSnapshot:
        identity = TaskIdentity("task-1", "issue-1", "", "request-1")
        return WorkflowSnapshot(
            "task-1",
            WorkflowContext(identity, ProgressState(state, sequence, "t0")),
            sequence,
        )

    def test_all_operational_fields_are_mapped_to_immutable_aggregates(self):
        with tempfile.TemporaryDirectory() as directory:
            legacy = {
                "task_id": "task-1",
                "issue_id": "issue-1",
                "workflow_state": "ZHONGSHU_ANALYST",
                "sequence": 4,
                "state_version": 7,
                "raw_request": "review",
                "request_payload": {"requirements": ["r1"]},
                "last_agent_payload": {"action": "READY_FOR_SOLVER"},
                "reply_history": [{"request_id": "r1"}],
                "project_type": "go",
                "task_type": "review",
                "current_phase": "ZHONGSHU",
                "current_role": "review-analyst",
                "expected_agent_id": "agent-1",
                "active_request_id": "r1",
                "dispatch_status": "running",
                "dispatch_operation_id": "op-1",
                "dispatch_external_message_id": "comment-1",
                "dispatch_idempotency_key": "idem-1",
                "dispatch_attempt": 2,
                "heartbeat_count": 3,
                "last_heartbeat_epoch": 1789000000,
                "sent_notification_keys": ["n1"],
                "findings": [{"finding_id": "f1", "severity": "P1", "status": "OPEN"}],
                "active_finding_ids": ["f1"],
                "resolved_finding_ids": [],
            }
            snapshot = legacy_dto_to_snapshot(
                legacy,
                artifact_port=FileArtifactStore(directory),
            )

        context = snapshot.context
        self.assertEqual(snapshot.state_version, 7)
        self.assertEqual(context.progression.sequence, 4)
        self.assertEqual(context.request.raw_request, "review")
        self.assertIsNotNone(context.request.payload_ref)
        self.assertEqual(context.delivery.external_message_id, "comment-1")
        self.assertEqual(context.delivery.attempt, 2)
        self.assertEqual(context.audit.sent_notification_keys, ("n1",))
        self.assertEqual(context.audit.heartbeat_count, 3)
        self.assertEqual(context.review.findings[0].finding_id, "f1")

    def test_legacy_review_queue_becomes_immutable_menxia_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot = legacy_dto_to_snapshot(
                {
                    "task_id": "task-queue",
                    "issue_id": "issue-queue",
                    "workflow_state": "MENXIA_ITEM_SOLVER",
                    "active_request_id": "request-running",
                    "dispatch_status": "running",
                    "request_payload": {
                        "zhongshu_task_review_queue": {
                            "revision_id": "revision-7",
                            "plan_hash": "plan-hash-7",
                            "jobs": [
                                {
                                    "group_id": "group-a",
                                    "item_id": "item-1",
                                    "group_order": 0,
                                    "item_order": 0,
                                    "status": "COMPLETED",
                                },
                                {
                                    "group_id": "group-a",
                                    "item_id": "item-2",
                                    "group_order": 0,
                                    "item_order": 1,
                                    "status": "RUNNING",
                                },
                            ],
                        }
                    },
                },
                artifact_port=FileArtifactStore(directory),
            )

        review = snapshot.context.review
        self.assertIsNotNone(review)
        assert review is not None
        self.assertEqual(review.revision_id, "revision-7")
        self.assertEqual(review.plan_hash, "plan-hash-7")
        self.assertEqual(review.active_item_id, "item-2")
        self.assertEqual(review.completed_item_ids, ("item-1",))
        self.assertEqual(review.next_menxia_item().item_id, "item-2")

    def test_corrupt_derived_finding_index_is_rejected(self):
        with self.assertRaises(Exception):
            legacy_dto_to_snapshot(
                {
                    "task_id": "task-1",
                    "issue_id": "issue-1",
                    "workflow_state": "REQUEST_INTAKE",
                    "findings": [{"finding_id": "f1", "severity": "P1"}],
                    "active_finding_ids": ["other"],
                }
            )

    def test_legacy_audit_lines_are_not_replayed_as_inbox_events(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = JsonWorkflowRepository(root)
            repository.initialize(self._snapshot("ZHONGSHU_ANALYST", 1))
            (root / "events.jsonl").write_text(
                json.dumps(
                    {
                        "sequence": 1,
                        "task_id": "task-1",
                        "event": "START",
                        "created_at": "t1",
                        "from_state": "REQUEST_INTAKE",
                        "to_state": "ZHONGSHU_ANALYST",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            _migrate_legacy_pending_events(repository, root, "task-1")

            self.assertEqual(repository.pending_domain_events("task-1"), ())

    def test_explicit_pending_legacy_event_is_imported_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = JsonWorkflowRepository(root)
            repository.initialize(self._snapshot("REQUEST_INTAKE", 0))
            (root / "events.jsonl").write_text(
                json.dumps(
                    {
                        "sequence": 0,
                        "task_id": "task-1",
                        "event": "START",
                        "payload": {"raw_request": "review"},
                        "created_at": "t0",
                        "pending": True,
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            _migrate_legacy_pending_events(repository, root, "task-1")
            pending = repository.pending_domain_events("task-1")

            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0].name, "START")
            self.assertEqual(pending[0].event_id, "legacy:task-1:0:START")


if __name__ == "__main__":
    unittest.main()
