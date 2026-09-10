from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from orchestrator.agent_result_file import AgentResultFileError, read_agent_result_file


class AgentResultFileTests(unittest.TestCase):
    def test_reads_strict_utf8_and_validates_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "result.json"
            payload = {
                "task_id": "task-1",
                "request_id": "req-1",
                "phase": "ZHONGSHU_SOLVER",
                "role": "review-solver",
                "action": "READY_FOR_CRITIC",
            }
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            path.write_bytes(data)
            reference = {
                "result_path": str(path),
                "result_sha256": hashlib.sha256(data).hexdigest(),
            }

            result = read_agent_result_file(
                reference,
                task_id="task-1",
                request_id="req-1",
                phase="ZHONGSHU_SOLVER",
                role="review-solver",
                allowed_root=temporary,
            )

            self.assertEqual(result.payload["action"], "READY_FOR_CRITIC")
            self.assertEqual(result.payload["result_source"], "file")
            self.assertEqual(result.bytes, len(data))

    def test_rejects_path_outside_allowed_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with tempfile.TemporaryDirectory() as outside:
                path = Path(outside) / "result.json"
                path.write_text(
                    '{"task_id":"task-1","request_id":"req-1","action":"BLOCKED"}',
                    encoding="utf-8",
                )
                with self.assertRaises(AgentResultFileError):
                    read_agent_result_file(
                        {"result_path": str(path)},
                        task_id="task-1",
                        request_id="req-1",
                        phase="ZHONGSHU",
                        role="review-analyst",
                        allowed_root=temporary,
                    )

    def test_accepts_complete_utf8_result_with_bom(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "result.json"
            payload = {
                "task_id": "task-bom",
                "request_id": "req-bom",
                "phase": "ZHONGSHU_ANALYST",
                "role": "review-analyst",
                "action": "READY_FOR_SOLVER",
            }
            data = b"\xef\xbb\xbf" + json.dumps(payload).encode("utf-8")
            path.write_bytes(data)

            result = read_agent_result_file(
                {
                    "result_path": str(path),
                    "result_sha256": hashlib.sha256(data).hexdigest(),
                },
                task_id="task-bom",
                request_id="req-bom",
                phase="ZHONGSHU_ANALYST",
                role="review-analyst",
                allowed_root=temporary,
            )

            self.assertEqual(result.payload["action"], "READY_FOR_SOLVER")
            self.assertTrue(result.payload["result_has_bom"])
            self.assertEqual(result.bytes, len(data))


if __name__ == "__main__":
    unittest.main()
