from __future__ import annotations

import tempfile
import unittest

from orchestrator.agent_result_file import write_agent_result_file
from orchestrator.structured_output import (
    build_structured_output_spec,
    fill_role_defaults,
    role_result_template,
)


class FillRoleDefaultsTests(unittest.TestCase):
    def test_backfills_missing_stable_fields(self) -> None:
        payload = {
            "action": "READY_FOR_CRITIC",
            "phase": "ZHONGSHU",
            "role": "review-solver",
            "state": "ZHONGSHU_SOLVER",
        }
        filled = fill_role_defaults(
            payload, phase="ZHONGSHU", role="review-solver", state="ZHONGSHU_SOLVER"
        )
        self.assertIn("scope", filled)
        self.assertIn("summary", filled)
        self.assertEqual(filled["action"], "READY_FOR_CRITIC")

    def test_writer_accepts_result_missing_scope(self) -> None:
        spec = build_structured_output_spec(
            "ZHONGSHU", "review-solver", {"active_runtime_state": "ZHONGSHU_SOLVER"}
        )
        assert spec is not None
        payload = role_result_template(
            "ZHONGSHU",
            "review-solver",
            task_id="task-1",
            request_id="req-1",
            state="ZHONGSHU_SOLVER",
            role_mode=spec.role_mode,
            schema_hash=spec.schema_hash,
            action="READY_FOR_CRITIC",
        )
        payload.pop("scope", None)
        with tempfile.TemporaryDirectory() as directory:
            result = write_agent_result_file(
                payload,
                target_path=f"{directory}/result.json",
                task_id="task-1",
                request_id="req-1",
                phase="ZHONGSHU",
                role="review-solver",
                allowed_root=directory,
                expected_schema_hash=spec.schema_hash,
                expected_state="ZHONGSHU_SOLVER",
                expected_role_mode=spec.role_mode,
            )
        self.assertIn("scope", result.payload)


if __name__ == "__main__":
    unittest.main()
