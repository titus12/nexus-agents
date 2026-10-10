"""Budget policy v2: format repairs are free; total-call safety net (T8)."""
import unittest

from orchestrator.domain.requirements.budget import (BudgetExceeded, TOTAL_CALLS_CAP,
                                                     charge_dispatch, new_work_epoch)
from orchestrator.domain.requirements.model import BudgetLedger, DispatchCharge


def v1_ledger(**fields):
    return BudgetLedger(policy_version='requirements-policy-v1', **fields)


def charge(ledger, logical='d1', kind='initial', attempt=1):
    return charge_dispatch(ledger, logical, kind, attempt)


class FormatRepairTests(unittest.TestCase):
    def test_v2_format_repair_only_grows_total_calls(self):
        ledger = charge(BudgetLedger(), 'd1', 'initial')
        charged = charge(ledger, 'd1', 'format', 2)
        self.assertEqual(charged.calls, ledger.calls)
        self.assertEqual(charged.total_calls, ledger.total_calls + 1)
        self.assertEqual(charged.dispatch_charges[0].format_repairs, 1)

    def test_v1_format_repair_still_consumes_calls(self):
        ledger = charge(v1_ledger(), 'd1', 'initial')
        charged = charge(ledger, 'd1', 'format', 2)
        self.assertEqual(charged.calls, ledger.calls + 1)
        self.assertEqual(charged.total_calls, ledger.total_calls + 1)

    def test_two_format_repairs_per_dispatch_remain_the_cap(self):
        ledger = BudgetLedger()
        ledger = charge(charge(charge(ledger, 'd1', 'initial'), 'd1', 'format', 2), 'd1', 'format', 3)
        with self.assertRaisesRegex(BudgetExceeded, 'FORMAT_REPAIR_BUDGET_EXHAUSTED'):
            charge(ledger, 'd1', 'format', 4)

    def test_free_format_repairs_do_not_extend_the_call_budget(self):
        ledger = BudgetLedger(calls=11)
        for attempt in (2, 3):
            ledger = charge(ledger, 'd1', 'format', attempt)
        self.assertEqual(ledger.calls, 11)
        self.assertEqual(ledger.total_calls, 2)
        # The 12th real call hits the model-call budget; format repairs
        # did not consume it.
        ledger = charge(ledger, 'd2', 'initial')
        with self.assertRaisesRegex(BudgetExceeded, 'MODEL_CALL_BUDGET_EXHAUSTED'):
            charge(ledger, 'd3', 'initial')


class TotalCallCapTests(unittest.TestCase):
    def test_v2_total_calls_cap_fires_at_forty(self):
        ledger = BudgetLedger(total_calls=TOTAL_CALLS_CAP)
        with self.assertRaisesRegex(BudgetExceeded, 'TOTAL_CALL_BUDGET_EXHAUSTED'):
            charge(ledger, 'd1', 'initial')

    def test_cap_counts_format_repairs_towards_total(self):
        ledger = BudgetLedger(total_calls=TOTAL_CALLS_CAP - 1, calls=0)
        ledger = charge(ledger, 'd1', 'format', 2)
        self.assertEqual(ledger.total_calls, TOTAL_CALLS_CAP)
        with self.assertRaisesRegex(BudgetExceeded, 'TOTAL_CALL_BUDGET_EXHAUSTED'):
            charge(ledger, 'd2', 'format', 2)

    def test_v1_ledgers_keep_the_old_semantics_without_the_cap(self):
        ledger = v1_ledger(total_calls=TOTAL_CALLS_CAP + 5, calls=0)
        charged = charge(ledger, 'd1', 'initial')
        self.assertEqual(charged.calls, 1)


class PolicyVersionTests(unittest.TestCase):
    def test_new_ledgers_default_to_v2_and_both_versions_decode(self):
        self.assertEqual(BudgetLedger().policy_version, 'requirements-policy-v2')
        from orchestrator.domain.requirements.model import decode
        self.assertEqual(decode(BudgetLedger, {'policy_version': 'requirements-policy-v1'}).policy_version,
                         'requirements-policy-v1')
        self.assertEqual(decode(BudgetLedger, {}).policy_version, 'requirements-policy-v2')
        with self.assertRaises(ValueError):
            decode(BudgetLedger, {'policy_version': 'requirements-policy-v0'})

    def test_new_work_epoch_preserves_total_calls(self):
        ledger = BudgetLedger(total_calls=7, calls=5)
        epoch = new_work_epoch(ledger, 'new-input')
        self.assertEqual((epoch.work_epoch, epoch.total_calls, epoch.epoch_input_digest), (2, 7, 'new-input'))
        self.assertEqual(new_work_epoch(ledger, ''), ledger)


if __name__ == '__main__':
    unittest.main()
