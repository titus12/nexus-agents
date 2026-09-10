from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from os import environ
from pathlib import Path
from unittest.mock import patch

from orchestrator.adapters import FakeMulticaAdapter, MulticaCliAdapter
from orchestrator.context import StateContext
from orchestrator.models import AgentRequest, ExternalMessage
from orchestrator.structured_output import build_structured_output_spec, role_result_template
from orchestrator.states import ZhongshuAnalystState


class AgentResultFilePollTests(unittest.TestCase):
    @staticmethod
    def _structured_request(adapter: MulticaCliAdapter, task_id: str, request_id: str) -> AgentRequest:
        return AgentRequest(
            task_id=task_id,
            issue_id=f"SER-{task_id}",
            request_id=request_id,
            agent_id="agent-structured",
            role="review-analyst",
            phase="ZHONGSHU_ANALYST",
            prompt="short prompt",
            idempotency_key=f"key-{request_id}",
            dispatch_external_message_id="dispatch-structured",
            structured_output={"mode": "result_file"},
        )

    @staticmethod
    def _bound_analyst_request(task_id: str, request_id: str) -> AgentRequest:
        context = {
            "active_runtime_state": "ZHONGSHU_ANALYST",
            "contract_mode": True,
            "target_state": "ZHONGSHU_ANALYST",
        }
        spec = build_structured_output_spec("ZHONGSHU", "review-analyst", context)
        assert spec is not None
        return AgentRequest(
            task_id=task_id,
            issue_id=f"SER-{task_id}",
            request_id=request_id,
            agent_id="agent-structured",
            role="review-analyst",
            phase="ZHONGSHU",
            prompt="short prompt",
            idempotency_key=f"key-{request_id}",
            dispatch_external_message_id="dispatch-structured",
            context={
                "target_state": "ZHONGSHU_ANALYST",
                "structured_output_role_mode": spec.role_mode,
                "contract_mode": True,
            },
            target_state="ZHONGSHU_ANALYST",
            structured_output=spec.to_dict(),
        )

    def test_poll_does_not_accept_result_file_before_remote_run_is_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            adapter = MulticaCliAdapter(Path(temporary) / "transport")
            request = self._structured_request(adapter, "task-running", "req-running")
            result_path = adapter.prompt_bundle_builder.result_path(
                request.task_id,
                request.request_id,
            )
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_text(
                json.dumps({
                    "task_id": request.task_id,
                    "request_id": request.request_id,
                    "phase": request.phase,
                    "role": request.role,
                    "action": "READY_FOR_SOLVER",
                }),
                encoding="utf-8",
            )
            with patch.object(adapter, "_run_with_read_retry", return_value=[]), patch.object(
                adapter, "get_run_status", return_value="running"
            ):
                self.assertEqual(adapter.poll(request), [])

    def test_poll_accepts_result_file_after_remote_run_is_completed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            adapter = MulticaCliAdapter(Path(temporary) / "transport")
            request = self._structured_request(adapter, "task-completed", "req-completed")
            result_path = adapter.prompt_bundle_builder.result_path(
                request.task_id,
                request.request_id,
            )
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_text(
                json.dumps({
                    "task_id": request.task_id,
                    "request_id": request.request_id,
                    "phase": request.phase,
                    "role": request.role,
                    "action": "READY_FOR_SOLVER",
                }),
                encoding="utf-8",
            )
            with patch.object(adapter, "_run_with_read_retry", return_value=[]), patch.object(
                adapter, "get_run_status", return_value="completed"
            ):
                messages = adapter.poll(request)
            self.assertEqual(len(messages), 1)
            self.assertEqual(messages[0].payload["action"], "READY_FOR_SOLVER")

    def test_get_run_status_matches_coalesced_comment(self) -> None:
        adapter = MulticaCliAdapter("test-transport")
        request = self._structured_request(adapter, "task-coalesced", "req-coalesced")
        request = replace(request, dispatch_external_message_id="coalesced-comment")
        with patch.object(
            adapter,
            "_run_with_read_retry",
            return_value=[{
                "id": "run-1",
                "status": "running",
                "created_at": "2026-09-08T13:00:00Z",
                "trigger_comment_id": "trigger-comment",
                "coalesced_comment_ids": ["coalesced-comment"],
                "delivered_comment_ids": ["trigger-comment", "coalesced-comment"],
            }],
        ):
            self.assertEqual(adapter.get_run_status(request), "running")

    def test_poll_reads_full_result_from_human_readable_pointer_comment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            adapter = MulticaCliAdapter(root / "transport")
            result_path = adapter.prompt_bundle_builder.result_path(
                "task-text",
                "req-text",
            )
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result = {
                "task_id": "task-text",
                "request_id": "req-text",
                "phase": "ZHONGSHU_SOLVER",
                "role": "review-solver",
                "action": "READY_FOR_CRITIC",
            }
            result_bytes = json.dumps(result, ensure_ascii=False).encode("utf-8")
            result_path.write_bytes(result_bytes)
            pointer = "\n".join(
                [
                    "nexus-agent-result-ref-v1",
                    "- task_id: task-text",
                    "- request_id: req-text",
                    f"- result_path: {result_path}",
                    "- phase: ZHONGSHU_SOLVER",
                    "- role: review-solver",
                    "- action: READY_FOR_CRITIC",
                ]
            )
            request = AgentRequest(
                task_id="task-text",
                issue_id="SER-text",
                request_id="req-text",
                agent_id="agent-text",
                role="review-solver",
                phase="ZHONGSHU_SOLVER",
                prompt="short prompt",
                idempotency_key="key-text",
            )
            with patch.object(
                adapter,
                "_run_with_read_retry",
                return_value=[{
                    "id": "reply-text",
                    "author_id": "agent-text",
                    "created_at": "2026-09-02T08:00:00Z",
                    "content": pointer,
                }],
            ):
                messages = adapter.poll(request)

            self.assertEqual(len(messages), 1)
            self.assertEqual(messages[0].payload["action"], "READY_FOR_CRITIC")
            self.assertEqual(messages[0].payload["result_source"], "file")

    def test_poll_recovers_result_file_when_pointer_comment_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            adapter = MulticaCliAdapter(root / "transport")
            result_path = adapter.prompt_bundle_builder.result_path("task-2", "req-2")
            result = {
                "task_id": "task-2",
                "request_id": "req-2",
                "phase": "ZHONGSHU_ANALYST",
                "role": "review-analyst",
                "action": "READY_FOR_SOLVER",
            }
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_text(json.dumps(result), encoding="utf-8")
            request = AgentRequest(
                task_id="task-2",
                issue_id="SER-2",
                request_id="req-2",
                agent_id="agent-2",
                role="review-analyst",
                phase="ZHONGSHU_ANALYST",
                prompt="short prompt",
                idempotency_key="key-2",
            )
            with patch.object(adapter, "_run_with_read_retry", return_value=[]):
                messages = adapter.poll(request)

            self.assertEqual(len(messages), 1)
            self.assertEqual(messages[0].payload["action"], "READY_FOR_SOLVER")
            self.assertEqual(messages[0].payload["result_source"], "file")

    def test_poll_reads_full_result_from_short_pointer_comment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            adapter = MulticaCliAdapter(root / "transport")
            result_path = adapter.prompt_bundle_builder.result_path(
                "task-1",
                "req-1",
            )
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result = {
                "task_id": "task-1",
                "request_id": "req-1",
                "phase": "ZHONGSHU_SOLVER",
                "role": "review-solver",
                "action": "READY_FOR_CRITIC",
                "plan": {"groups": [{"group_id": "group-1"}]},
            }
            result_bytes = json.dumps(result, ensure_ascii=False).encode("utf-8")
            result_path.write_bytes(result_bytes)
            pointer = {
                "protocol": "nexus-agent-result-ref-v1",
                "task_id": "task-1",
                "request_id": "req-1",
                "result_path": str(result_path),
                "result_sha256": hashlib.sha256(result_bytes).hexdigest(),
            }
            request = AgentRequest(
                task_id="task-1",
                issue_id="SER-1",
                request_id="req-1",
                agent_id="agent-1",
                role="review-solver",
                phase="ZHONGSHU_SOLVER",
                prompt="short prompt",
                idempotency_key="key-1",
                dispatch_external_message_id="dispatch-1",
            )
            comment = {
                "id": "reply-1",
                "author_id": "agent-1",
                "created_at": "2026-09-02T08:00:00Z",
                "content": json.dumps(pointer, ensure_ascii=False),
            }
            with patch.object(
                adapter,
                "_run_with_read_retry",
                return_value=[comment],
            ):
                messages = adapter.poll(request)

            self.assertEqual(len(messages), 1)
            self.assertEqual(messages[0].payload["action"], "READY_FOR_CRITIC")
            self.assertEqual(messages[0].payload["result_source"], "file")
        self.assertEqual(messages[0].payload["result_sha256"], pointer["result_sha256"])

    def test_poll_rejects_current_request_pointing_to_another_worker_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            adapter = MulticaCliAdapter(root / "transport")
            other_path = adapter.prompt_bundle_builder.result_path(
                "task-concurrent",
                "worker-3-request",
            )
            other_path.parent.mkdir(parents=True, exist_ok=True)
            other_payload = {
                "task_id": "task-concurrent",
                "request_id": "worker-3-request",
                "phase": "ZHONGSHU_ANALYST",
                "role": "review-analyst",
                "action": "READY_FOR_SOLVER",
            }
            other_path.write_text(json.dumps(other_payload), encoding="utf-8")
            pointer = {
                "protocol": "nexus-agent-result-ref-v1",
                "task_id": "task-concurrent",
                "request_id": "worker-1-request",
                "result_path": str(other_path),
                "result_sha256": hashlib.sha256(
                    other_path.read_bytes()
                ).hexdigest(),
            }
            request = AgentRequest(
                task_id="task-concurrent",
                issue_id="SER-concurrent",
                request_id="worker-1-request",
                agent_id="agent-1",
                role="review-analyst",
                phase="ZHONGSHU_ANALYST",
                prompt="short prompt",
                idempotency_key="key-concurrent",
            )
            with patch.object(
                adapter,
                "_run_with_read_retry",
                return_value=[{
                    "id": "cross-worker-reply",
                    "author_id": "agent-1",
                    "created_at": "2026-09-02T08:00:00Z",
                    "content": json.dumps(pointer),
                }],
            ), patch(
                "orchestrator.adapters.read_agent_result_file",
                side_effect=AssertionError("cross-worker result must not be read"),
            ):
                messages = adapter.poll(request)

            self.assertEqual(messages, [])


    def test_poll_ignores_stale_pointer_before_reading_old_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old_path = root / "old-result.json"
            old_payload = {
                "task_id": "task-1",
                "request_id": "old-request",
                "phase": "ZHONGSHU_ANALYST",
                "role": "review-analyst",
                "action": "READY_FOR_SOLVER",
            }
            old_path.write_text(json.dumps(old_payload), encoding="utf-8")
            stale_pointer = {
                "protocol": "nexus-agent-result-ref-v1",
                "task_id": "task-1",
                "request_id": "old-request",
                "result_path": str(old_path),
            }
            request = AgentRequest(
                task_id="task-1",
                issue_id="SER-1",
                request_id="current-request",
                agent_id="agent-1",
                role="review-analyst",
                phase="ZHONGSHU_ANALYST",
                prompt="short prompt",
                idempotency_key="key-1",
            )
            adapter = MulticaCliAdapter(root / "transport")
            with patch.object(
                adapter,
                "_run_with_read_retry",
                return_value=[{
                    "id": "stale-reply",
                    "author_id": "agent-1",
                    "created_at": "2026-09-02T08:00:00Z",
                    "content": json.dumps(stale_pointer),
                }],
            ):
                messages = adapter.poll(request)

            self.assertEqual(messages, [])

    def test_poll_accepts_bom_result_from_pointer_comment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            adapter = MulticaCliAdapter(root / "transport")
            result_path = adapter.prompt_bundle_builder.result_path(
                "task-bom",
                "req-bom",
            )
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result = {
                "task_id": "task-bom",
                "request_id": "req-bom",
                "phase": "ZHONGSHU_ANALYST",
                "role": "review-analyst",
                "action": "READY_FOR_SOLVER",
            }
            result_bytes = b"\xef\xbb\xbf" + json.dumps(result).encode("utf-8")
            result_path.write_bytes(result_bytes)
            pointer = {
                "protocol": "nexus-agent-result-ref-v1",
                "task_id": "task-bom",
                "request_id": "req-bom",
                "result_path": str(result_path),
                "result_sha256": hashlib.sha256(result_bytes).hexdigest(),
            }
            request = AgentRequest(
                task_id="task-bom",
                issue_id="SER-bom",
                request_id="req-bom",
                agent_id="agent-bom",
                role="review-analyst",
                phase="ZHONGSHU_ANALYST",
                prompt="short prompt",
                idempotency_key="key-bom",
            )
            with patch.object(
                adapter,
                "_run_with_read_retry",
                return_value=[{
                    "id": "reply-bom",
                    "author_id": "agent-bom",
                    "created_at": "2026-09-02T08:00:00Z",
                    "content": json.dumps(pointer),
                }],
            ):
                messages = adapter.poll(request)

            self.assertEqual(len(messages), 1)
            self.assertTrue(messages[0].payload["result_has_bom"])

    def test_poll_persists_inline_result_under_orchestrator_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = AgentRequest(
                task_id="task-inline",
                issue_id="SER-inline",
                request_id="req-inline",
                agent_id="agent-inline",
                role="review-critic",
                phase="ZHONGSHU",
                prompt="short prompt",
                idempotency_key="key-inline",
            )
            adapter = MulticaCliAdapter(root / "transport")
            payload = {
                "task_id": "task-inline",
                "request_id": "req-inline",
                "phase": "ZHONGSHU",
                "role": "review-critic",
                "action": "APPROVE_FREEZE",
                "findings": [],
            }
            with patch.object(
                adapter,
                "_run_with_read_retry",
                return_value=[{
                    "id": "inline-reply",
                    "author_id": "agent-inline",
                    "created_at": "2026-09-02T08:00:00Z",
                    "content": json.dumps(payload, ensure_ascii=False),
                }],
            ):
                messages = adapter.poll(request)

            result_path = adapter.prompt_bundle_builder.result_path(
                "task-inline",
                "req-inline",
            )
            self.assertEqual(len(messages), 1)
            self.assertEqual(
                messages[0].payload["result_source"],
                "orchestrator_inline",
            )
            self.assertTrue(result_path.is_file())
            self.assertNotEqual(result_path.read_bytes()[:3], b"\xef\xbb\xbf")

    def test_poll_accepts_structured_inline_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            adapter = MulticaCliAdapter(Path(temporary) / "transport")
            request = self._structured_request(adapter, "task-inline-structured", "req-inline-structured")
            payload = {
                "task_id": request.task_id,
                "request_id": request.request_id,
                "phase": request.phase,
                "role": request.role,
                "action": "READY_FOR_SOLVER",
            }
            comment = {
                "id": "inline-structured-reply",
                "author_id": request.agent_id,
                "created_at": "2026-09-09T08:00:00Z",
                "content": json.dumps(payload, ensure_ascii=False),
            }
            with patch.object(
                adapter,
                "_run_with_read_retry",
                return_value=[comment],
            ), patch.object(adapter, "get_run_status", return_value="completed"):
                messages = adapter.poll(request)

            result_path = adapter.prompt_bundle_builder.result_path(
                request.task_id,
                request.request_id,
            )
            self.assertEqual(len(messages), 1)
            self.assertEqual(messages[0].payload["result_source"], "orchestrator_inline")
            self.assertTrue(result_path.is_file())

    def test_poll_bridges_valid_remote_result_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            remote_root = Path(temporary) / "multica_workspaces"
            with patch.dict(environ, {"MULTICA_WORKSPACES_ROOT": str(remote_root)}):
                adapter = MulticaCliAdapter(Path(temporary) / "transport")
            request = self._structured_request(adapter, "task-remote", "req-remote")
            remote_path = remote_root / "workspace-1" / "task-1" / "workdir" / "result.json"
            remote_path.parent.mkdir(parents=True, exist_ok=True)
            result = {
                "task_id": request.task_id,
                "request_id": request.request_id,
                "phase": request.phase,
                "role": request.role,
                "action": "READY_FOR_SOLVER",
            }
            result_bytes = json.dumps(result, ensure_ascii=False).encode("utf-8")
            remote_path.write_bytes(result_bytes)
            pointer = {
                "protocol": "nexus-agent-result-ref-v1",
                "task_id": request.task_id,
                "request_id": request.request_id,
                "result_path": str(remote_path),
                "result_sha256": hashlib.sha256(result_bytes).hexdigest(),
            }
            comment = {
                "id": "remote-pointer-reply",
                "author_id": request.agent_id,
                "created_at": "2026-09-09T08:00:00Z",
                "content": json.dumps(pointer),
            }
            with patch.object(
                adapter,
                "_run_with_read_retry",
                return_value=[comment],
            ), patch.object(adapter, "get_run_status", return_value="completed"):
                messages = adapter.poll(request)

            canonical_path = adapter.prompt_bundle_builder.result_path(
                request.task_id,
                request.request_id,
            )
            self.assertEqual(len(messages), 1)
            self.assertEqual(messages[0].payload["result_source"], "orchestrator_bridge")
            self.assertEqual(messages[0].payload["result_path"], str(canonical_path))
            self.assertTrue(canonical_path.is_file())

    def test_poll_recovers_remote_result_without_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            remote_root = Path(temporary) / "multica_workspaces"
            with patch.dict(environ, {"MULTICA_WORKSPACES_ROOT": str(remote_root)}):
                adapter = MulticaCliAdapter(Path(temporary) / "transport")
            request = self._structured_request(adapter, "task-discovered", "req-discovered")
            remote_path = (
                remote_root
                / "workspace-1"
                / "project-1"
                / "workdir"
                / "result.json"
            )
            remote_path.parent.mkdir(parents=True, exist_ok=True)
            remote_path.write_text(
                json.dumps({
                    "task_id": request.task_id,
                    "request_id": request.request_id,
                    "phase": request.phase,
                    "role": request.role,
                    "action": "READY_FOR_SOLVER",
                }),
                encoding="utf-8",
            )
            with patch.object(
                adapter,
                "_run_with_read_retry",
                return_value=[],
            ), patch.object(adapter, "get_run_status", return_value="completed"):
                messages = adapter.poll(request)

            canonical_path = adapter.prompt_bundle_builder.result_path(
                request.task_id,
                request.request_id,
            )
            self.assertEqual(len(messages), 1)
            self.assertEqual(
                messages[0].payload["result_source"],
                "orchestrator_bridge",
            )
            self.assertEqual(messages[0].payload["remote_result_path"], str(remote_path))
            self.assertEqual(messages[0].payload["result_path"], str(canonical_path))
            self.assertTrue(canonical_path.is_file())

    def test_poll_rejects_ambiguous_remote_result_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            remote_root = Path(temporary) / "multica_workspaces"
            with patch.dict(environ, {"MULTICA_WORKSPACES_ROOT": str(remote_root)}):
                adapter = MulticaCliAdapter(Path(temporary) / "transport")
            request = self._structured_request(adapter, "task-ambiguous", "req-ambiguous")
            for workspace_name in ("workspace-1", "workspace-2"):
                remote_path = (
                    remote_root
                    / workspace_name
                    / "project-1"
                    / "workdir"
                    / "result.json"
                )
                remote_path.parent.mkdir(parents=True, exist_ok=True)
                remote_path.write_text(
                    json.dumps({
                        "task_id": request.task_id,
                        "request_id": request.request_id,
                        "phase": request.phase,
                        "role": request.role,
                        "action": "READY_FOR_SOLVER",
                    }),
                    encoding="utf-8",
                )
            with patch.object(
                adapter,
                "_run_with_read_retry",
                return_value=[],
            ), patch.object(adapter, "get_run_status", return_value="completed"):
                messages = adapter.poll(request)

            self.assertEqual(messages, [])
            self.assertFalse(
                adapter.prompt_bundle_builder.result_path(
                    request.task_id,
                    request.request_id,
                ).exists()
            )

    def test_poll_rejects_transport_envelope_then_bridges_complete_remote_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            remote_root = root / "multica_workspaces"
            with patch.dict(environ, {"MULTICA_WORKSPACES_ROOT": str(remote_root)}):
                adapter = MulticaCliAdapter(root / "transport")
            request = self._bound_analyst_request("task-envelope", "req-envelope")
            spec = request.structured_output
            assert spec is not None
            remote_path = remote_root / "workspace-1" / "project-1" / "workdir" / "result.json"
            remote_path.parent.mkdir(parents=True, exist_ok=True)
            complete_payload = role_result_template(
                request.phase,
                request.role,
                task_id=request.task_id,
                request_id=request.request_id,
                state=request.target_state,
                role_mode=str(request.context["structured_output_role_mode"]),
                schema_hash=str(spec["schema_hash"]),
                action="REQUIREMENT_CONTRACT_READY",
            )
            complete_payload["summary"] = "complete result"
            remote_path.write_text(
                json.dumps(complete_payload, ensure_ascii=False),
                encoding="utf-8",
            )
            envelope = {
                "action": "REQUIREMENT_CONTRACT_READY",
                "protocol": "nexus-agent-result-file-v2",
                "task_id": request.task_id,
                "request_id": request.request_id,
                "phase": request.phase,
                "state": request.target_state,
                "role": request.role,
                "mode": request.context["structured_output_role_mode"],
                "structured_output_protocol": spec["protocol"],
                "structured_output_schema_hash": spec["schema_hash"],
                "result_path": "./result.json",
            }
            comment = {
                "id": "transport-envelope",
                "author_id": request.agent_id,
                "created_at": "2026-09-09T08:00:00Z",
                "content": json.dumps(envelope),
            }
            with patch.object(
                adapter,
                "_run_with_read_retry",
                return_value=[comment],
            ), patch.object(adapter, "get_run_status", return_value="completed"):
                messages = adapter.poll(request)

            canonical_path = adapter.prompt_bundle_builder.result_path(
                request.task_id,
                request.request_id,
            )
            self.assertEqual(len(messages), 1)
            self.assertEqual(messages[0].payload["result_source"], "orchestrator_bridge")
            self.assertEqual(messages[0].payload["remote_result_path"], str(remote_path))
            self.assertEqual(messages[0].payload["summary"], "complete result")
            self.assertEqual(
                json.loads(canonical_path.read_text(encoding="utf-8"))["summary"],
                "complete result",
            )

    def test_poll_reports_failed_remote_run_as_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            adapter = MulticaCliAdapter(Path(temporary) / "transport")
            request = self._structured_request(adapter, "task-failed", "req-failed")
            with patch.object(adapter, "_run_with_read_retry", return_value=[]), patch.object(
                adapter, "get_run_status", return_value="failed"
            ):
                messages = adapter.poll(request)

            self.assertEqual(len(messages), 1)
            self.assertEqual(messages[0].payload["action"], "__REMOTE_RUN_FAILED__")
            self.assertEqual(messages[0].payload["remote_run_status"], "failed")

    def test_failed_remote_run_enters_fsm_rejection_path(self) -> None:
        adapter = FakeMulticaAdapter()
        ctx = StateContext(
            task_id="task-failed-fsm",
            workflow_state="ZHONGSHU_ANALYST",
            current_phase="ZHONGSHU",
            current_role="review-analyst",
            expected_agent_id="agent-structured",
            active_request_id="req-failed-fsm",
            dispatch_external_message_id="dispatch-failed-fsm",
            dispatch_status="sent",
        )
        adapter.queue_reply(
            ctx.active_request_id,
            ExternalMessage(
                "agent-structured",
                {
                    "action": "__REMOTE_RUN_FAILED__",
                    "task_id": ctx.task_id,
                    "request_id": ctx.active_request_id,
                    "remote_run_status": "failed",
                },
                "remote-run:req-failed-fsm",
                "",
            ),
        )

        event = ZhongshuAnalystState(adapter).update(ctx)

        self.assertEqual(event.name, "AGENT_REPLY_REJECTED")
        self.assertEqual(event.payload["reason"], "REMOTE_RUN_FAILED")
        self.assertEqual(ctx.last_error["code"], "AGENT_REMOTE_RUN_FAILED")

    def test_poll_rejects_remote_pointer_outside_configured_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            remote_root = Path(temporary) / "multica_workspaces"
            outside_path = Path(temporary) / "outside" / "workdir" / "result.json"
            outside_path.parent.mkdir(parents=True, exist_ok=True)
            outside_path.write_text(
                json.dumps({
                    "task_id": "task-outside",
                    "request_id": "req-outside",
                    "phase": "ZHONGSHU_ANALYST",
                    "role": "review-analyst",
                    "action": "READY_FOR_SOLVER",
                }),
                encoding="utf-8",
            )
            with patch.dict(environ, {"MULTICA_WORKSPACES_ROOT": str(remote_root)}):
                adapter = MulticaCliAdapter(Path(temporary) / "transport")
            request = self._structured_request(adapter, "task-outside", "req-outside")
            pointer = {
                "protocol": "nexus-agent-result-ref-v1",
                "task_id": request.task_id,
                "request_id": request.request_id,
                "result_path": str(outside_path),
            }
            comment = {
                "id": "outside-pointer-reply",
                "author_id": request.agent_id,
                "created_at": "2026-09-09T08:00:00Z",
                "content": json.dumps(pointer),
            }
            with patch.object(adapter, "_run_with_read_retry", return_value=[comment]):
                messages = adapter.poll(request)

            self.assertEqual(messages, [])

    def test_poll_rejects_remote_pointer_with_wrong_file_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            remote_root = Path(temporary) / "multica_workspaces"
            wrong_path = remote_root / "workspace-1" / "task-1" / "not-workdir" / "result.json"
            wrong_path.parent.mkdir(parents=True, exist_ok=True)
            wrong_path.write_text(
                json.dumps({
                    "task_id": "task-shape",
                    "request_id": "req-shape",
                    "phase": "ZHONGSHU_ANALYST",
                    "role": "review-analyst",
                    "action": "READY_FOR_SOLVER",
                }),
                encoding="utf-8",
            )
            with patch.dict(environ, {"MULTICA_WORKSPACES_ROOT": str(remote_root)}):
                adapter = MulticaCliAdapter(Path(temporary) / "transport")
            request = self._structured_request(adapter, "task-shape", "req-shape")
            pointer = {
                "protocol": "nexus-agent-result-ref-v1",
                "task_id": request.task_id,
                "request_id": request.request_id,
                "result_path": str(wrong_path),
            }
            comment = {
                "id": "wrong-shape-pointer-reply",
                "author_id": request.agent_id,
                "created_at": "2026-09-09T08:00:00Z",
                "content": json.dumps(pointer),
            }
            with patch.object(adapter, "_run_with_read_retry", return_value=[comment]):
                messages = adapter.poll(request)

            self.assertEqual(messages, [])


if __name__ == "__main__":
    unittest.main()
