from __future__ import annotations

import json
import logging
import tempfile
import unittest
from pathlib import Path

from orchestrator.prompt_bundle import PromptBundleBuilder


class PromptBundleTests(unittest.TestCase):
    def test_bundle_is_utf8_atomic_and_hash_addressed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = PromptBundleBuilder(root).build(
                task_id="task-1",
                request_id="request-1",
                phase="ZHONGSHU_SOLVER",
                role="review-solver",
                revision_id="revision-1",
                prompt="请读取方案文件并输出完整 JSON。",
            )

            self.assertTrue(bundle.manifest_path.exists())
            self.assertEqual(
                (bundle.root / "prompt.txt").read_text(encoding="utf-8"),
                "请读取方案文件并输出完整 JSON。",
            )
            self.assertFalse((bundle.root / "prompt.txt").read_bytes().startswith(b"\xef\xbb\xbf"))
            self.assertTrue((bundle.root / "context.json").exists())
            PromptBundleBuilder.verify(bundle)

            manifest = json.loads(bundle.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["encoding"], "utf-8")
            self.assertFalse(manifest["bom"])
            self.assertEqual(manifest["files"][0]["encoding"], "utf-8")
            self.assertEqual(manifest["files"][1]["name"], "context.json")
            self.assertEqual(
                json.loads((bundle.root / "context.json").read_text(encoding="utf-8"))["request_id"],
                "request-1",
            )

    def test_bundle_logs_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertLogs(level=logging.INFO) as captured:
                PromptBundleBuilder(Path(temporary)).build(
                    task_id="task-2",
                    request_id="request-2",
                    phase="ZHONGSHU_ANALYST",
                    role="review-analyst",
                    prompt="read the file",
                )
            self.assertIn("PROMPT_BUNDLE_CREATED", "\n".join(captured.output))
            self.assertIn("PROMPT_BUNDLE_REFERENCE", "\n".join(captured.output))

    def test_request_scoped_result_paths_are_distinct(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            builder = PromptBundleBuilder(Path(temporary))
            first = builder.result_path("task-concurrent", "worker-1-request")
            second = builder.result_path("task-concurrent", "worker-2-request")

            self.assertNotEqual(first, second)
            self.assertEqual(first.parent.name, "worker-1-request")
            self.assertEqual(second.parent.name, "worker-2-request")
            self.assertEqual(first.name, "result.json")
            self.assertEqual(second.name, "result.json")


if __name__ == "__main__":
    unittest.main()
