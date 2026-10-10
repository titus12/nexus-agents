from __future__ import annotations

import json
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from orchestrator.adapters import MulticaCliAdapter
from orchestrator.runtime.compat_effects import _skill_lock
from orchestrator.transport.external import AgentRequest


class PromptBundleDispatchTests(unittest.TestCase):
    def test_dispatch_comment_contains_reference_not_full_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            adapter = MulticaCliAdapter(root)
            request = AgentRequest(
                task_id="task-1",
                issue_id="SER-1",
                request_id="task-1:MENXIA_GROUP_SOLVER:1:req",
                agent_id="agent-1",
                role="review-solver",
                phase="MENXIA",
                target_state="MENXIA_GROUP_SOLVER",
                context={"active_runtime_skill_lock": _skill_lock('MENXIA_GROUP_SOLVER')},
                prompt="请读取并审查这份很长的方案。" * 1000,
                idempotency_key="task-1:MENXIA_GROUP_SOLVER:1",
            )

            with patch.object(
                adapter,
                "_run_with_read_retry",
                return_value=[],
            ), patch.object(
                adapter,
                "_run",
                side_effect=[{"id": "issue-updated"}, {"id": "comment-1"}],
            ):
                adapter.dispatch(request)

            dispatch_file = next(Path(root).glob("dispatch_*.json"))
            payload = json.loads(dispatch_file.read_text(encoding="utf-8"))
            self.assertIn("prompt_ref", payload)
            self.assertLess(len(payload["prompt"].encode("utf-8")), 2048)
            self.assertNotIn(request.prompt, payload["prompt"])
            manifest_path = Path(payload["prompt_ref"]["manifest_path"])
            self.assertTrue(manifest_path.exists())
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            prompt_path = manifest_path.parent / manifest["files"][0]["name"]
            self.assertEqual(prompt_path.read_text(encoding="utf-8"), request.prompt)
            actual_manifest_hash = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
            self.assertEqual(payload["prompt_ref"]["manifest_hash"], actual_manifest_hash)


if __name__ == "__main__":
    unittest.main()
