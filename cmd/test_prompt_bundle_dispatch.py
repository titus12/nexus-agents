from __future__ import annotations

import json
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from orchestrator.adapters import MulticaCliAdapter
from orchestrator.models import AgentRequest
from orchestrator.structured_output import build_structured_output_spec


class PromptBundleDispatchTests(unittest.TestCase):
    def test_dispatch_comment_contains_reference_not_full_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            adapter = MulticaCliAdapter(root)
            request = AgentRequest(
                task_id="task-1",
                issue_id="SER-1",
                request_id="task-1:ZHONGSHU_SOLVER:1:req",
                agent_id="agent-1",
                role="review-solver",
                phase="ZHONGSHU_SOLVER",
                prompt="请读取并审查这份很长的方案。" * 1000,
                idempotency_key="task-1:ZHONGSHU_SOLVER:1",
            )

            with patch.object(
                adapter,
                "_run",
                side_effect=[{"id": "issue-updated"}, {"id": "comment-1"}],
            ):
                adapter.dispatch(request)

            dispatch_file = next(Path(root).glob("dispatch_*.json"))
            payload = json.loads(dispatch_file.read_text(encoding="utf-8"))
            self.assertIn("prompt_ref", payload)
            self.assertLess(len(payload["prompt"].encode("utf-8")), 1024)
            manifest_path = Path(payload["prompt_ref"]["manifest_path"])
            self.assertTrue(manifest_path.exists())
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            prompt_path = manifest_path.parent / manifest["files"][0]["name"]
            self.assertEqual(prompt_path.read_text(encoding="utf-8"), request.prompt)
            actual_manifest_hash = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
            self.assertEqual(payload["prompt_ref"]["manifest_hash"], actual_manifest_hash)

    def test_result_file_schema_is_embedded_once_in_dispatch_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            adapter = MulticaCliAdapter(root)
            skill_path = Path(root) / "active-skill.md"
            skill_bytes = b"# Test active skill\n"
            skill_path.write_bytes(skill_bytes)
            spec = build_structured_output_spec(
                "ZHONGSHU",
                "review-solver",
                {"active_runtime_state": "ZHONGSHU_SOLVER"},
            )
            self.assertIsNotNone(spec)
            request = AgentRequest(
                task_id="task-1",
                issue_id="SER-1",
                request_id="task-1:ZHONGSHU_SOLVER:1:req",
                agent_id="agent-1",
                role="review-solver",
                phase="ZHONGSHU",
                prompt="请读取提示词包并完成任务。",
                idempotency_key="task-1:ZHONGSHU_SOLVER:1",
                context={
                    "active_runtime_skill": "test-skill",
                    "active_runtime_skill_lock": {
                        "status": "locked",
                        "name": "test-skill",
                        "source": str(skill_path),
                        "version": "1",
                        "sha256": hashlib.sha256(skill_bytes).hexdigest(),
                    },
                },
                structured_output=spec.to_dict(),
            )

            with patch.object(
                adapter,
                "_run",
                side_effect=[{"id": "issue-updated"}, {"id": "comment-1"}],
            ):
                adapter.dispatch(request)

            dispatch_file = next(Path(root).glob("dispatch_*.json"))
            payload = json.loads(dispatch_file.read_text(encoding="utf-8"))
            self.assertIn("structured_output", payload)
            self.assertNotIn("structured_output", payload["response_contract"])
            self.assertNotIn("structured_output", payload["transport"])
            prompt_text = payload["prompt"]
            canonical_path = str(Path(payload["prompt_ref"]["result_path"]))
            self.assertIn("relative result.json", prompt_text)
            self.assertIn("Orchestrator-owned canonical result", prompt_text)
            self.assertIn("do not write that path", prompt_text)
            self.assertIn("nexus-agent-result-ref-v1", prompt_text)
            self.assertIn("do not return the business JSON or a transport envelope inline", prompt_text)
            self.assertNotIn("Prefer returning the complete JSON object inline", prompt_text)
            self.assertNotIn(
                f"write the one complete business JSON result to {canonical_path}",
                prompt_text,
            )
            self.assertLess(
                len(dispatch_file.read_bytes()),
                24 * 1024,
                "dispatch envelope must stay below the Windows command-line safety budget",
            )


if __name__ == "__main__":
    unittest.main()
