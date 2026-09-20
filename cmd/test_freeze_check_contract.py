from __future__ import annotations

import unittest

from orchestrator.contracts import contract_for_state
from orchestrator.domain.transitions import TransitionRegistry
from orchestrator.structured_output import (
    StructuredOutputSpec,
    build_structured_output_spec,
    role_result_template,
    validate_role_result_shape,
)

_FREEZE_CONTRACT_ID = "nexus.zhongshu.freeze_check.v1"


def _freeze_context() -> dict:
    return {
        "target_state": "ZHONGSHU_FREEZE_CHECK",
        "active_runtime_state": "ZHONGSHU_FREEZE_CHECK",
        "revision_id": "task-1:ZHONGSHU_ANALYST:2",
        "plan_hash": "a" * 64,
    }


class FreezeCheckContractTests(unittest.TestCase):
    """The freeze check must dispatch and validate under its own contract.

    Regression for task-20260919-67a1a3: with no registered freeze-check
    contract the dispatch fell back to the critic contract, whose schema
    demanded state=ZHONGSHU_CRITIC while the reply validator demanded
    state=ZHONGSHU_FREEZE_CHECK -- no worker reply could ever pass and the
    reply-retry budget burned out four times in two minutes.
    """

    def test_every_dispatched_state_has_a_contract(self) -> None:
        for state in (
            "ZHONGSHU_ANALYST", "ZHONGSHU_SOLVER", "ZHONGSHU_CRITIC",
            "ZHONGSHU_FREEZE_CHECK", "MENXIA_ITEM_SOLVER",
            "MENXIA_ITEM_ANALYST", "MENXIA_ITEM_CRITIC", "MENXIA_GROUP_GATE",
        ):
            with self.subTest(state=state):
                self.assertEqual(contract_for_state(state).state, state)

    def test_freeze_dispatch_schema_carries_its_own_state_const(self) -> None:
        spec = build_structured_output_spec(
            "ZHONGSHU", "review-critic", _freeze_context()
        )

        self.assertIsNotNone(spec)
        self.assertEqual(spec.state, "ZHONGSHU_FREEZE_CHECK")
        self.assertEqual(spec.role_mode, "REVIEW_CURRENT_TASK_GRAPH")
        properties = spec.schema["properties"]
        self.assertEqual(properties["state"]["const"], "ZHONGSHU_FREEZE_CHECK")
        self.assertEqual(properties["contract_id"]["const"], _FREEZE_CONTRACT_ID)

    def test_spec_to_dict_resolves_the_contract_id(self) -> None:
        spec = build_structured_output_spec(
            "ZHONGSHU", "review-critic", _freeze_context()
        )

        self.assertEqual(spec.to_dict()["contract_id"], _FREEZE_CONTRACT_ID)

    def test_spec_to_dict_rejects_a_state_without_contract(self) -> None:
        spec = StructuredOutputSpec(
            mode="inline", protocol="p", schema={}, schema_hash="h",
            state="NOT_A_REAL_STATE",
        )

        with self.assertRaises(KeyError):
            spec.to_dict()

    def test_worker_reply_echoing_the_dispatch_state_validates(self) -> None:
        spec = build_structured_output_spec(
            "ZHONGSHU", "review-critic", _freeze_context()
        )
        payload = role_result_template(
            "ZHONGSHU",
            "review-critic",
            state="ZHONGSHU_FREEZE_CHECK",
            role_mode="REVIEW_CURRENT_TASK_GRAPH",
            schema_hash=spec.schema_hash,
            action="APPROVE_FREEZE",
        )

        self.assertEqual(
            validate_role_result_shape(
                payload,
                phase="ZHONGSHU",
                role="review-critic",
                state="ZHONGSHU_FREEZE_CHECK",
                role_mode="REVIEW_CURRENT_TASK_GRAPH",
                expected_schema_hash=spec.schema_hash,
            ),
            "",
        )

    def test_freeze_state_accepts_every_contract_action(self) -> None:
        from orchestrator.domain.states import ZhongshuFreezeCheckState

        contract_actions = set(contract_for_state("ZHONGSHU_FREEZE_CHECK").actions)
        supported = set(ZhongshuFreezeCheckState.supported_actions)

        missing = contract_actions - supported
        self.assertEqual(missing, set())

    def test_freeze_revisions_and_evidence_requests_route(self) -> None:
        registry = TransitionRegistry.default()

        self.assertEqual(
            registry.target_for("ZHONGSHU_FREEZE_CHECK", "REQUEST_SOLVER_REVISION"),
            "ZHONGSHU_SOLVER",
        )
        self.assertEqual(
            registry.target_for("ZHONGSHU_FREEZE_CHECK", "REQUEST_ANALYST_EVIDENCE"),
            "ZHONGSHU_ANALYST",
        )


if __name__ == "__main__":
    unittest.main()
