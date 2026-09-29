from __future__ import annotations

import unittest

from orchestrator.structured_output import (
    build_structured_output_spec,
    normalize_role_payload,
    role_result_template,
    validate_role_result_shape,
)

_HASH = "0" * 64


def _analyst_payload(relevance_values: list[object]) -> dict:
    payload = role_result_template(
        "ZHONGSHU",
        "review-analyst",
        task_id="task-1",
        request_id="task-1:ZHONGSHU_ANALYST:1",
        state="ZHONGSHU_ANALYST",
        role_mode="EVIDENCE_COLLECTION_READ_ONLY",
        schema_hash=_HASH,
        action="EVIDENCE_PACKET_READY",
    )
    payload["evidence_updates"] = [
        {
            "evidence_id": f"ev-{index}",
            "requirement_id": "req-1",
            "decision_relevance": value,
            "source": "a.py:1",
            "conclusion": "observed",
        }
        for index, value in enumerate(relevance_values)
    ]
    return payload


def _validate(payload: dict) -> str:
    return validate_role_result_shape(
        payload,
        phase="ZHONGSHU",
        role="review-analyst",
        state="ZHONGSHU_ANALYST",
        role_mode="EVIDENCE_COLLECTION_READ_ONLY",
    )


class RoleModeSchemaNarrowingTests(unittest.TestCase):
    """Each analyst hop's schema must only allow that hop's product.

    task-20260929-73fc9c: the un-scoped schema advertised evidence actions on
    a REQUIREMENT_CONTRACT_ONLY hop, the worker answered with an evidence
    packet, and the reply died in the quote gate.
    """

    def test_contract_hop_forbids_evidence_actions_and_content(self) -> None:
        spec = build_structured_output_spec(
            "ZHONGSHU",
            "review-analyst",
            {"target_state": "ZHONGSHU_ANALYST", "contract_mode": True},
        )
        assert spec is not None

        self.assertEqual(spec.role_mode, "REQUIREMENT_CONTRACT_ONLY")
        action = spec.schema["properties"]["action"]
        self.assertEqual(
            action["enum"], ["REQUIREMENT_CONTRACT_READY", "HUMAN_GATE", "BLOCKED"]
        )
        self.assertEqual(spec.schema["properties"]["evidence_updates"]["maxItems"], 0)
        self.assertEqual(spec.schema["properties"]["finding_responses"]["maxItems"], 0)

    def test_evidence_hop_forbids_the_contract_action(self) -> None:
        spec = build_structured_output_spec(
            "ZHONGSHU",
            "review-analyst",
            {"target_state": "ZHONGSHU_ANALYST"},
        )
        assert spec is not None

        self.assertEqual(spec.role_mode, "EVIDENCE_COLLECTION_READ_ONLY")
        enum = spec.schema["properties"]["action"]["enum"]
        self.assertIn("EVIDENCE_PACKET_READY", enum)
        self.assertIn("READY_FOR_SOLVER", enum)
        self.assertNotIn("REQUIREMENT_CONTRACT_READY", enum)
        self.assertNotIn("maxItems", spec.schema["properties"]["evidence_updates"])

    def test_validation_rejects_an_evidence_reply_on_a_contract_hop(self) -> None:
        payload = role_result_template(
            "ZHONGSHU",
            "review-analyst",
            task_id="task-1",
            request_id="req-1",
            state="ZHONGSHU_ANALYST",
            role_mode="REQUIREMENT_CONTRACT_ONLY",
            schema_hash=_HASH,
            action="EVIDENCE_PACKET_READY",
        )
        payload["evidence_updates"] = [
            {
                "evidence_id": "ev-1",
                "requirement_id": "req-1",
                "decision_relevance": "coverage",
                "source": "a.py:1",
                "conclusion": "observed",
            }
        ]

        error = validate_role_result_shape(
            payload,
            phase="ZHONGSHU",
            role="review-analyst",
            state="ZHONGSHU_ANALYST",
            role_mode="REQUIREMENT_CONTRACT_ONLY",
        )

        self.assertIn("STRUCTURED_ROLE_CONTRACT_INVALID", error)

    def test_validation_accepts_a_contract_reply_on_a_contract_hop(self) -> None:
        payload = role_result_template(
            "ZHONGSHU",
            "review-analyst",
            task_id="task-1",
            request_id="req-1",
            state="ZHONGSHU_ANALYST",
            role_mode="REQUIREMENT_CONTRACT_ONLY",
            schema_hash=_HASH,
            action="REQUIREMENT_CONTRACT_READY",
        )

        error = validate_role_result_shape(
            payload,
            phase="ZHONGSHU",
            role="review-analyst",
            state="ZHONGSHU_ANALYST",
            role_mode="REQUIREMENT_CONTRACT_ONLY",
        )

        self.assertEqual(error, "")


class DecisionRelevanceCanonicalizationTests(unittest.TestCase):
    def test_descriptive_synonyms_and_typos_validate_after_normalization(self) -> None:
        payload = _analyst_payload(
            ["depencency", "performance", "compatibility", "regression"]
        )
        self.assertIn("invalid enum", _validate(payload))

        normalize_role_payload(
            payload, phase="ZHONGSHU", role="review-analyst", state="ZHONGSHU_ANALYST"
        )

        self.assertEqual(_validate(payload), "")
        self.assertEqual(
            [update["decision_relevance"] for update in payload["evidence_updates"]],
            ["dependency", "risk", "risk", "risk"],
        )

    def test_case_variant_canonicalizes(self) -> None:
        payload = _analyst_payload(["Coverage", "RISK"])
        normalize_role_payload(
            payload, phase="ZHONGSHU", role="review-analyst", state="ZHONGSHU_ANALYST"
        )
        self.assertEqual(_validate(payload), "")
        self.assertEqual(
            [update["decision_relevance"] for update in payload["evidence_updates"]],
            ["coverage", "risk"],
        )

    def test_unknown_value_falls_back_without_rejecting(self) -> None:
        payload = _analyst_payload(["totally_unknown_label"])
        normalize_role_payload(
            payload, phase="ZHONGSHU", role="review-analyst", state="ZHONGSHU_ANALYST"
        )
        self.assertEqual(_validate(payload), "")
        self.assertEqual(
            payload["evidence_updates"][0]["decision_relevance"], "coverage"
        )

    def test_validation_still_rejects_unknown_without_normalization(self) -> None:
        payload = _analyst_payload(["totally_unknown_label"])
        self.assertIn("invalid enum", _validate(payload))


if __name__ == "__main__":
    unittest.main()
