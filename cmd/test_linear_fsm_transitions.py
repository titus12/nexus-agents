from __future__ import annotations

import importlib
import unittest


def _load_transition_module():
    try:
        module = importlib.import_module("orchestrator.domain.transitions")
        return module
    except (ImportError, AttributeError) as error:
        raise AssertionError(
            "transition contract is not implemented: "
            f"orchestrator.domain.transitions.TransitionRegistry: {error}"
        ) from error


class LinearTransitionContractTests(unittest.TestCase):
    def setUp(self):
        self.transition_module = _load_transition_module()
        self.registry = self.transition_module.TransitionRegistry.default()

    def test_business_states_have_one_forward_successor(self):
        expected_forward_edges = (
            ("REQUEST_INTAKE", "START", "ZHONGSHU_ANALYST"),
            ("ZHONGSHU_ANALYST", "READY_FOR_SOLVER", "ZHONGSHU_SOLVER"),
            ("ZHONGSHU_SOLVER", "READY_FOR_CRITIC", "ZHONGSHU_CRITIC"),
            ("ZHONGSHU_CRITIC", "APPROVE_CRITIC", "ZHONGSHU_FREEZE_CHECK"),
            ("ZHONGSHU_FREEZE_CHECK", "FREEZE_APPROVED", "MENXIA_ITEM_SOLVER"),
            ("MENXIA_ITEM_SOLVER", "READY_FOR_ANALYST", "MENXIA_ITEM_ANALYST"),
            ("MENXIA_ITEM_ANALYST", "READY_FOR_CRITIC", "MENXIA_ITEM_CRITIC"),
            ("MENXIA_ITEM_CRITIC", "APPROVE_ITEM", "MENXIA_GROUP_GATE"),
            ("MENXIA_GROUP_GATE", "COMPLETE", "DONE"),
        )

        for source, action, target in expected_forward_edges:
            with self.subTest(source=source, action=action):
                self.assertEqual(self.registry.target_for(source, action), target)

    def test_dynamic_target_is_rejected_when_not_registered(self):
        with self.assertRaises(self.transition_module.TransitionError):
            self.registry.validate_target(
                "ZHONGSHU_CRITIC",
                "not-a-registered-state",
            )


if __name__ == "__main__":
    unittest.main()
