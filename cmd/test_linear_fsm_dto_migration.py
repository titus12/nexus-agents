from __future__ import annotations

from dataclasses import FrozenInstanceError
import unittest

from orchestrator.domain.context import (
    AuditState,
    DeliveryState,
    ParallelState,
    ProgressState,
    RecoveryState,
    RequestState,
    ReviewState,
    TaskIdentity,
    WorkflowContext,
)
from orchestrator.domain.findings import Finding
from orchestrator.domain.context import context_from_dto, context_to_dto


class ImmutableContextMigrationTests(unittest.TestCase):
    def context(self) -> WorkflowContext:
        finding = Finding(
            finding_id="f-1",
            severity="P1",
            status="OPEN",
            owner_role="review-solver",
            group_id="g-1",
            item_id="i-1",
            supporting_evidence=("evidence-1",),
        )
        return WorkflowContext(
            identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
            progression=ProgressState("ZHONGSHU_ANALYST", 4, "2026-09-10T00:00:00Z"),
            delivery=DeliveryState(
                active_request_id="request-analyst",
                dispatch_operation_id="run-1",
                idempotency_key="idem-1",
                status="RUNNING",
                phase="ZHONGSHU",
                role="review-analyst",
                expected_agent_id="agent-1",
                attempt=1,
            ),
            recovery=RecoveryState(
                external_retry_count=1,
                timeout_request_id="request-analyst",
            ),
            request=RequestState(
                raw_request="review this change",
                payload_ref="artifact-request",
                project_type="go",
                task_type="review",
            ),
            review=ReviewState(
                revision_id="revision-1",
                active_group_id="g-1",
                active_item_id="i-1",
                findings=(finding,),
                last_reply_fingerprint="fingerprint-1",
            ),
            parallel=ParallelState(
                group_index=0,
                item_index=0,
                menxia_snapshot_ref="artifact-menxia",
            ),
            audit=AuditState(
                sent_notification_keys=("STATE_ENTER:4",),
                heartbeat_count=2,
                last_artifact_id="artifact-result",
            ),
            schema_version="4.0",
        )

    def test_context_aggregates_are_immutable(self) -> None:
        context = self.context()
        with self.assertRaises(FrozenInstanceError):
            context.delivery.status = "FAILED"  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            context.review.findings = ()  # type: ignore[union-attr,misc]

    def test_context_round_trip_preserves_typed_state(self) -> None:
        context = self.context()
        restored = context_from_dto(context_to_dto(context))
        self.assertEqual(restored, context)
        self.assertEqual(restored.review.findings[0].identity_key(), ("g-1", "i-1", "f-1"))
        self.assertEqual(restored.delivery.dispatch_operation_id, "run-1")
        self.assertEqual(restored.parallel.menxia_snapshot_ref, "artifact-menxia")


if __name__ == "__main__":
    unittest.main()
