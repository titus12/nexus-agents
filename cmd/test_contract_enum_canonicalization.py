from __future__ import annotations

import unittest

from orchestrator.structured_output import (
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
