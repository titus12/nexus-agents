from __future__ import annotations

import tempfile
import unittest

from orchestrator.agent_result_file import (
    AgentResultFileError,
    read_agent_result_file,
    write_agent_result_file,
)
from orchestrator.structured_output import (
    accepted_role_modes,
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


class RoleModeAcceptanceTests(unittest.TestCase):
    """A reply may declare any mode the role contract publishes.

    The Solver contract declares both ``..._READ_ONLY`` (initial plan) and
    ``..._READ_ONLY_RESUME`` (revision).  Reading only the *dispatched* mode made
    every revision reply fail validation, and the pipeline then reported the
    unusable-but-delivered reply as "no result at all".
    """

    INITIAL = "TASK_GRAPH_FORMALIZATION_READ_ONLY"
    RESUME = "TASK_GRAPH_FORMALIZATION_READ_ONLY_RESUME"
    FOREIGN = "REVIEW_ONE_TASK"

    def _spec(self):
        spec = build_structured_output_spec(
            "ZHONGSHU", "review-solver", {"active_runtime_state": "ZHONGSHU_SOLVER"}
        )
        assert spec is not None
        return spec

    def _payload(self, spec, mode: str) -> dict:
        return role_result_template(
            "ZHONGSHU",
            "review-solver",
            task_id="task-1",
            request_id="req-1",
            state="ZHONGSHU_SOLVER",
            role_mode=mode,
            schema_hash=spec.schema_hash,
            action="READY_FOR_CRITIC",
        )

    def _write(self, spec, mode: str, *, allowed: tuple[str, ...]):
        payload = self._payload(spec, mode)
        with tempfile.TemporaryDirectory() as directory:
            return write_agent_result_file(
                payload,
                target_path=f"{directory}/result.json",
                task_id="task-1",
                request_id="req-1",
                phase="ZHONGSHU",
                role="review-solver",
                allowed_root=directory,
                expected_schema_hash=spec.schema_hash,
                expected_state="ZHONGSHU_SOLVER",
                expected_role_mode=self.INITIAL,
                allowed_role_modes=allowed,
            )

    def test_declared_modes_include_the_expected_one(self) -> None:
        self.assertEqual(
            accepted_role_modes(self.INITIAL, (self.INITIAL, self.RESUME)),
            frozenset({self.INITIAL, self.RESUME}),
        )
        self.assertEqual(accepted_role_modes(self.INITIAL), frozenset({self.INITIAL}))

    def test_revision_reply_is_accepted_when_only_the_initial_mode_was_dispatched(
        self,
    ) -> None:
        spec = self._spec()

        result = self._write(spec, self.RESUME, allowed=(self.INITIAL, self.RESUME))

        self.assertEqual(result.payload["mode"], self.RESUME)
        self.assertEqual(result.payload["action"], "READY_FOR_CRITIC")

    def test_revision_reply_is_rejected_when_the_role_does_not_declare_it(self) -> None:
        spec = self._spec()

        with self.assertRaises(AgentResultFileError) as raised:
            self._write(spec, self.RESUME, allowed=())

        self.assertIn("role mode mismatch", str(raised.exception))

    def test_mode_outside_the_role_contract_is_rejected(self) -> None:
        spec = self._spec()

        with self.assertRaises(AgentResultFileError) as raised:
            self._write(spec, self.FOREIGN, allowed=(self.INITIAL, self.RESUME))

        self.assertIn("role mode mismatch", str(raised.exception))

    def test_file_reader_accepts_the_sibling_mode(self) -> None:
        spec = self._spec()
        with tempfile.TemporaryDirectory() as directory:
            written = write_agent_result_file(
                self._payload(spec, self.RESUME),
                target_path=f"{directory}/result.json",
                task_id="task-1",
                request_id="req-1",
                phase="ZHONGSHU",
                role="review-solver",
                allowed_root=directory,
                expected_schema_hash=spec.schema_hash,
                expected_state="ZHONGSHU_SOLVER",
                expected_role_mode=self.INITIAL,
                allowed_role_modes=(self.INITIAL, self.RESUME),
            )
            reread = read_agent_result_file(
                {
                    "result_path": str(written.path),
                    "result_sha256": written.sha256,
                },
                task_id="task-1",
                request_id="req-1",
                phase="ZHONGSHU",
                role="review-solver",
                allowed_root=directory,
                expected_schema_hash=spec.schema_hash,
                expected_state="ZHONGSHU_SOLVER",
                expected_role_mode=self.INITIAL,
                allowed_role_modes=(self.INITIAL, self.RESUME),
            )

        self.assertEqual(reread.payload["mode"], self.RESUME)


if __name__ == "__main__":
    unittest.main()
