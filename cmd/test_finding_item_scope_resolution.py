"""Finding owner resolution and scoped-revision survival.

Live incident task-20260926-35833d: critic findings named their owning task
only inside the free-text ``target`` (``group-000003/item-000004.acceptance_
signals and task_review_ledger``), the scope derivation truncated that into a
garbage id, and ``carry_forward_revision_items`` silently replaced the
solver's fix with the reviewed plan.  The critic then re-rejected the same
stale capsule until the freeze gate blocked the run.
"""
from __future__ import annotations
import unittest
from orchestrator.adapters import _extract_json, _strip_trailing_commas
from orchestrator.domain.findings import Finding, resolve_finding_group_id, resolve_finding_item_id
_INCIDENT_TARGET = 'group-000003/item-000004.acceptance_signals and task_review_ledger'

def _plan() -> dict:
    return {'requirements': [], 'items': [{'item_id': 'item-000001', 'group_id': 'group-000001', 'title': 'Task A', 'objective': 'do A', 'source_requirement_ids': [], 'dependencies': [], 'acceptance_signals': ['A is observable'], 'unknowns': [], 'risks': [], 'parallelizable': True}, {'item_id': 'item-000004', 'group_id': 'group-000003', 'title': 'Task D', 'objective': 'do D', 'source_requirement_ids': [], 'dependencies': [], 'acceptance_signals': ['D cites ev-000004, ev-005'], 'unknowns': [], 'risks': [], 'parallelizable': True}], 'groups': [{'group_id': 'group-000001', 'item_ids': ['item-000001']}, {'group_id': 'group-000003', 'item_ids': ['item-000004']}], 'dependencies': [], 'scope': {}, 'unknowns': [], 'risks': []}

def _review(findings):
    from orchestrator.domain.context import ReviewState
    return ReviewState(revision_id='rev-1', plan=_plan(), findings=tuple(findings), task_review_ledger=())

def _target_only_finding(finding_id: str='f-1') -> Finding:
    """The incident shape: owner named only inside the free-text target."""
    return Finding.from_dict({'finding_id': finding_id, 'severity': 'P1', 'status': 'OPEN', 'category': 'evidence_alignment', 'target': _INCIDENT_TARGET, 'claim': 'acceptance signal is not auditable', 'required_action': 'reconcile the acceptance signal', 'evidence_strength': 'strong'})

class ResolveFindingItemIdTests(unittest.TestCase):

    def test_explicit_item_id_wins(self) -> None:
        finding = Finding.from_dict({'finding_id': 'f-1', 'severity': 'P1', 'item_id': 'item-000002', 'target': 'item-000004'})
        self.assertEqual(resolve_finding_item_id(finding), 'item-000002')

    def test_incident_target_resolves_to_the_real_item_id(self) -> None:
        finding = _target_only_finding()
        self.assertEqual(finding.item_id, 'item-000004')
        self.assertEqual(resolve_finding_item_id(finding), 'item-000004')
        self.assertEqual(resolve_finding_item_id({'item_id': '', 'target': _INCIDENT_TARGET}), 'item-000004')

    def test_target_suffix_is_never_returned_verbatim(self) -> None:
        resolved = resolve_finding_item_id({'item_id': '', 'target': _INCIDENT_TARGET})
        self.assertNotIn(' ', resolved)
        self.assertNotIn('.', resolved)

    def test_known_item_ids_disambiguate_multiple_candidates(self) -> None:
        resolved = resolve_finding_item_id({'item_id': '', 'target': 'item-000002 vs item-000004'}, known_item_ids=['item-000004'])
        self.assertEqual(resolved, 'item-000004')

    def test_clean_item_id_without_target_kept(self) -> None:
        self.assertEqual(resolve_finding_item_id({'item_id': 'i-1'}), 'i-1')

    def test_empty_target_yields_no_owner(self) -> None:
        self.assertEqual(resolve_finding_item_id({'item_id': '', 'target': 'plan'}), '')

    def test_group_id_derived_from_target(self) -> None:
        self.assertEqual(resolve_finding_group_id(_target_only_finding()), 'group-000003')

class FindingFromDictNormalizationTests(unittest.TestCase):

    def test_target_only_finding_folds_with_derived_identity(self) -> None:
        finding = _target_only_finding()
        self.assertEqual(finding.item_id, 'item-000004')
        self.assertEqual(finding.group_id, 'group-000003')

    def test_garbage_explicit_id_falls_back_to_target(self) -> None:
        finding = Finding.from_dict({'finding_id': 'f-1', 'severity': 'P1', 'item_id': _INCIDENT_TARGET, 'target': _INCIDENT_TARGET})
        self.assertEqual(finding.item_id, 'item-000004')

class TrailingCommaRepairTests(unittest.TestCase):

    def test_trailing_comma_is_repaired(self) -> None:
        self.assertEqual(_extract_json('{"action":"A",}'), {'action': 'A'})

    def test_trailing_comma_inside_string_is_preserved(self) -> None:
        value = _extract_json('{"a": "x,}", "b": 1,}')
        self.assertEqual(value, {'a': 'x,}', 'b': 1})

    def test_strip_reports_no_change_for_clean_text(self) -> None:
        self.assertIsNone(_strip_trailing_commas('{"a": 1}'))

    def test_concatenated_documents_stay_rejected(self) -> None:
        self.assertIsNone(_extract_json('{"action":"A",}{"action":"B"}'))
if __name__ == '__main__':
    unittest.main()
