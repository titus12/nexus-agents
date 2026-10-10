"""Echo advisory + identity coercion at the reply boundary (2026-09-27).

Live incident task-20260927-de54aa: CRITIC:6 group-01 returned a structurally
perfect group review that was discarded because the agent hand-copied the
64-char ``structured_output_schema_hash`` with a two-character transposition
(``b0c3``/``c0b3``); CRITIC:8 group-01 died on ``item_id: expected string``
because the finding carried a null/number identity.  Both slips must cost a
log line, not a whole review wave.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from orchestrator.agent_result_file import (
    AgentResultFileError,
    read_agent_result_file,
    write_agent_result_file,
)
from orchestrator.contracts.common import coerce_scalar_strings
from orchestrator.structured_output import (
    build_structured_output_spec,
    role_result_template,
    validate_role_result_shape,
)


def _critic_spec():
    spec = build_structured_output_spec(
        "ZHONGSHU",
        "review-critic",
        {
            "active_runtime_state": "ZHONGSHU_CRITIC",
            "zhongshu_dispatch_mode": "group_review",
        },
    )
    assert spec is not None
    return spec


def _critic_payload(spec, *, echo: str | None = None) -> dict:
    payload = role_result_template(
        "ZHONGSHU",
        "review-critic",
        task_id="task-1",
        request_id="req-1",
        state="ZHONGSHU_CRITIC",
        role_mode=spec.role_mode,
        schema_hash=spec.schema_hash,
        action="APPROVE_GROUP",
    )
    payload["group_id"] = "group-000001"
    if echo is None:
        payload.pop("structured_output_schema_hash", None)
    else:
        payload["structured_output_schema_hash"] = echo
    return payload


def _typo_hash(value: str) -> str:
    """Swap two adjacent hex chars the way a hand-copy slip does."""

    swapped = value[:13] + value[14] + value[13] + value[15:]
    assert swapped != value
    return swapped


class EchoIsAdvisoryTests(unittest.TestCase):
    def test_echo_typo_is_accepted_and_stamped(self) -> None:
        spec = _critic_spec()
        payload = _critic_payload(spec, echo=_typo_hash(spec.schema_hash))
        with tempfile.TemporaryDirectory() as directory:
            result = write_agent_result_file(
                payload,
                target_path=f"{directory}/result.json",
                task_id="task-1",
                request_id="req-1",
                phase="ZHONGSHU",
                role="review-critic",
                allowed_root=directory,
                expected_schema_hash=spec.schema_hash,
                expected_state="ZHONGSHU_CRITIC",
                expected_role_mode=spec.role_mode,
            )
        self.assertEqual(
            result.payload["structured_output_schema_hash"], spec.schema_hash
        )

    def test_missing_echo_is_accepted_and_stamped(self) -> None:
        spec = _critic_spec()
        payload = _critic_payload(spec, echo=None)
        self.assertNotIn("structured_output_schema_hash", payload)
        with tempfile.TemporaryDirectory() as directory:
            result = write_agent_result_file(
                payload,
                target_path=f"{directory}/result.json",
                task_id="task-1",
                request_id="req-1",
                phase="ZHONGSHU",
                role="review-critic",
                allowed_root=directory,
                expected_schema_hash=spec.schema_hash,
                expected_state="ZHONGSHU_CRITIC",
                expected_role_mode=spec.role_mode,
            )
        self.assertEqual(
            result.payload["structured_output_schema_hash"], spec.schema_hash
        )

    def test_file_read_stamps_a_typed_echo(self) -> None:
        spec = _critic_spec()
        payload = _critic_payload(spec, echo=_typo_hash(spec.schema_hash))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            result = read_agent_result_file(
                {"result_path": str(path)},
                task_id="task-1",
                request_id="req-1",
                phase="ZHONGSHU",
                role="review-critic",
                allowed_root=directory,
                expected_schema_hash=spec.schema_hash,
                expected_state="ZHONGSHU_CRITIC",
                expected_role_mode=spec.role_mode,
                allow_transport_backfill=True,
            )
        self.assertEqual(
            result.payload["structured_output_schema_hash"], spec.schema_hash
        )

    def test_shape_validator_keeps_only_the_format_guard(self) -> None:
        spec = _critic_spec()
        payload = _critic_payload(spec, echo=spec.schema_hash)
        self.assertEqual(
            validate_role_result_shape(
                payload,
                phase="ZHONGSHU",
                role="review-critic",
                state="ZHONGSHU_CRITIC",
                role_mode=spec.role_mode,
                expected_schema_hash=spec.schema_hash,
            ),
            "",
        )
        malformed = _critic_payload(spec, echo="not-a-hash")
        self.assertEqual(
            validate_role_result_shape(
                malformed,
                phase="ZHONGSHU",
                role="review-critic",
                state="ZHONGSHU_CRITIC",
                role_mode=spec.role_mode,
                expected_schema_hash=spec.schema_hash,
            ),
            "STRUCTURED_ROLE_SCHEMA_HASH_INVALID",
        )


class IdentityStringCoercionTests(unittest.TestCase):
    def test_scalar_pass_maps_null_and_numbers_to_text(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "item_id": {"type": "string"},
                "group_id": {"type": "string"},
                "count": {"type": "string", "enum": ["a", "b"]},
            },
        }
        payload = {"item_id": None, "group_id": 3, "count": 7}
        coerced = coerce_scalar_strings(payload, schema)
        self.assertEqual(coerced["item_id"], "")
        self.assertEqual(coerced["group_id"], "3")
        self.assertEqual(coerced["count"], 7)

    def test_nested_finding_identity_is_coerced(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "findings": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"item_id": {"type": "string"}},
                    },
                }
            },
        }
        payload = {"findings": [{"item_id": 12}, {"item_id": None}]}
        coerced = coerce_scalar_strings(payload, schema)
        self.assertEqual(coerced["findings"][0]["item_id"], "12")
        self.assertEqual(coerced["findings"][1]["item_id"], "")

    def test_group_review_result_with_numeric_item_id_is_accepted(self) -> None:
        spec = _critic_spec()
        payload = _critic_payload(spec, echo=spec.schema_hash)
        payload["findings"] = [
            {
                "finding_id": "c-1",
                "category": "boundary",
                "target": "display text",
                "claim": "claim",
                "decision": "OPEN",
                "severity": "P2",
                "evidence_strength": "SINGLE",
                "item_id": 3,
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            result = write_agent_result_file(
                payload,
                target_path=f"{directory}/result.json",
                task_id="task-1",
                request_id="req-1",
                phase="ZHONGSHU",
                role="review-critic",
                allowed_root=directory,
                expected_schema_hash=spec.schema_hash,
                expected_state="ZHONGSHU_CRITIC",
                expected_role_mode=spec.role_mode,
            )
        self.assertEqual(result.payload["findings"][0]["item_id"], "3")

    def test_null_item_id_is_accepted_as_empty(self) -> None:
        spec = _critic_spec()
        payload = _critic_payload(spec, echo=spec.schema_hash)
        payload["findings"] = [
            {
                "finding_id": "c-1",
                "category": "boundary",
                "target": "group-000001/item-000002.display",
                "claim": "claim",
                "decision": "OPEN",
                "severity": "P2",
                "evidence_strength": "SINGLE",
                "item_id": None,
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            result = write_agent_result_file(
                payload,
                target_path=f"{directory}/result.json",
                task_id="task-1",
                request_id="req-1",
                phase="ZHONGSHU",
                role="review-critic",
                allowed_root=directory,
                expected_schema_hash=spec.schema_hash,
                expected_state="ZHONGSHU_CRITIC",
                expected_role_mode=spec.role_mode,
            )
        self.assertEqual(result.payload["findings"][0]["item_id"], "")

    def test_container_identity_still_fails_shape(self) -> None:
        spec = _critic_spec()
        payload = _critic_payload(spec, echo=spec.schema_hash)
        payload["findings"] = [
            {
                "finding_id": "c-1",
                "category": "boundary",
                "target": "display text",
                "claim": "claim",
                "decision": "OPEN",
                "severity": "P2",
                "evidence_strength": "SINGLE",
                "item_id": ["item-000002"],
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(AgentResultFileError) as raised:
                write_agent_result_file(
                    payload,
                    target_path=f"{directory}/result.json",
                    task_id="task-1",
                    request_id="req-1",
                    phase="ZHONGSHU",
                    role="review-critic",
                    allowed_root=directory,
                    expected_schema_hash=spec.schema_hash,
                    expected_state="ZHONGSHU_CRITIC",
                    expected_role_mode=spec.role_mode,
                )
        self.assertIn("item_id", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
