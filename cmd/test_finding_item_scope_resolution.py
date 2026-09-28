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
from orchestrator.domain.findings import (
    Finding,
    resolve_finding_group_id,
    resolve_finding_item_id,
)
from orchestrator.domain.policies.solver_plan import (
    is_retryable_solver_reply_error,
    materialize_solver_reply,
)
from orchestrator.domain.states import _attempted_item_ids
from orchestrator.domain.zhongshu.solver import (
    batch_item_scope,
    revision_scope,
    SolverBatch,
)


_INCIDENT_TARGET = (
    "group-000003/item-000004.acceptance_signals and task_review_ledger"
)


def _plan() -> dict:
    return {
        "requirements": [],
        "items": [
            {
                "item_id": "item-000001",
                "group_id": "group-000001",
                "title": "Task A",
                "objective": "do A",
                "source_requirement_ids": [],
                "dependencies": [],
                "acceptance_signals": ["A is observable"],
                "unknowns": [],
                "risks": [],
                "parallelizable": True,
            },
            {
                "item_id": "item-000004",
                "group_id": "group-000003",
                "title": "Task D",
                "objective": "do D",
                "source_requirement_ids": [],
                "dependencies": [],
                "acceptance_signals": ["D cites ev-000004, ev-005"],
                "unknowns": [],
                "risks": [],
                "parallelizable": True,
            },
        ],
        "groups": [
            {"group_id": "group-000001", "item_ids": ["item-000001"]},
            {"group_id": "group-000003", "item_ids": ["item-000004"]},
        ],
        "dependencies": [],
        "scope": {},
        "unknowns": [],
        "risks": [],
    }


def _review(findings):
    from orchestrator.domain.context import ReviewState

    return ReviewState(
        revision_id="rev-1",
        plan=_plan(),
        findings=tuple(findings),
        task_review_ledger=(),
    )


def _target_only_finding(finding_id: str = "f-1") -> Finding:
    """The incident shape: owner named only inside the free-text target."""

    return Finding.from_dict(
        {
            "finding_id": finding_id,
            "severity": "P1",
            "status": "OPEN",
            "category": "evidence_alignment",
            "target": _INCIDENT_TARGET,
            "claim": "acceptance signal is not auditable",
            "required_action": "reconcile the acceptance signal",
            "evidence_strength": "strong",
        }
    )


class ResolveFindingItemIdTests(unittest.TestCase):
    def test_explicit_item_id_wins(self) -> None:
        finding = Finding.from_dict(
            {"finding_id": "f-1", "severity": "P1", "item_id": "item-000002",
             "target": "item-000004"}
        )
        self.assertEqual(resolve_finding_item_id(finding), "item-000002")

    def test_incident_target_resolves_to_the_real_item_id(self) -> None:
        finding = _target_only_finding()
        self.assertEqual(finding.item_id, "item-000004")
        self.assertEqual(resolve_finding_item_id(finding), "item-000004")
        # The raw fallback is exercised on findings folded before the fix
        # (no item_id at all): the target alone must still resolve.
        self.assertEqual(
            resolve_finding_item_id({"item_id": "", "target": _INCIDENT_TARGET}),
            "item-000004",
        )

    def test_target_suffix_is_never_returned_verbatim(self) -> None:
        resolved = resolve_finding_item_id(
            {"item_id": "", "target": _INCIDENT_TARGET}
        )
        self.assertNotIn(" ", resolved)
        self.assertNotIn(".", resolved)

    def test_known_item_ids_disambiguate_multiple_candidates(self) -> None:
        resolved = resolve_finding_item_id(
            {"item_id": "", "target": "item-000002 vs item-000004"},
            known_item_ids=["item-000004"],
        )
        self.assertEqual(resolved, "item-000004")

    def test_clean_item_id_without_target_kept(self) -> None:
        self.assertEqual(resolve_finding_item_id({"item_id": "i-1"}), "i-1")

    def test_empty_target_yields_no_owner(self) -> None:
        self.assertEqual(resolve_finding_item_id({"item_id": "", "target": "plan"}), "")

    def test_group_id_derived_from_target(self) -> None:
        self.assertEqual(resolve_finding_group_id(_target_only_finding()), "group-000003")


class FindingFromDictNormalizationTests(unittest.TestCase):
    def test_target_only_finding_folds_with_derived_identity(self) -> None:
        finding = _target_only_finding()
        self.assertEqual(finding.item_id, "item-000004")
        self.assertEqual(finding.group_id, "group-000003")

    def test_garbage_explicit_id_falls_back_to_target(self) -> None:
        finding = Finding.from_dict(
            {
                "finding_id": "f-1",
                "severity": "P1",
                "item_id": _INCIDENT_TARGET,
                "target": _INCIDENT_TARGET,
            }
        )
        self.assertEqual(finding.item_id, "item-000004")


class RevisionScopeTests(unittest.TestCase):
    def _payload(self) -> dict:
        return {"finding_batch": {"selected_finding_ids": ["f-1"]}}

    def test_target_only_finding_scopes_the_real_item(self) -> None:
        scope = revision_scope(_review([_target_only_finding()]), self._payload())
        self.assertEqual(scope, {"item-000004"})

    def test_pre_fix_finding_without_item_id_still_scopes(self) -> None:
        # Findings persisted before the normalization carry an empty item_id;
        # resuming such a run must not fall back to the truncated target.
        legacy = Finding(
            finding_id="f-1",
            severity="P1",
            status="OPEN",
            target=_INCIDENT_TARGET,
        )
        self.assertEqual(legacy.item_id, "")
        scope = revision_scope(_review([legacy]), self._payload())
        self.assertEqual(scope, {"item-000004"})

    def test_declared_scope_matches_the_enforced_scope(self) -> None:
        review = _review([_target_only_finding()])
        batch = SolverBatch(("f-1",), ())
        self.assertEqual(
            batch_item_scope(review, batch), sorted(revision_scope(review, self._payload()))
        )

    def test_attempted_item_ids_record_the_real_item(self) -> None:
        recorded = _attempted_item_ids(
            (_target_only_finding(),), self._payload()
        )
        self.assertEqual(recorded, ("item-000004",))


class ScopedRevisionSurvivesTests(unittest.TestCase):
    def _revised(self) -> dict:
        plan = _plan()
        plan["items"][1]["acceptance_signals"] = [
            "D cites (evidence_id, requirement_id, worker_id, file:line) crosswalk"
        ]
        return plan

    def test_solver_edit_is_not_silently_discarded(self) -> None:
        scope = revision_scope(
            _review([_target_only_finding()]),
            {"finding_batch": {"selected_finding_ids": ["f-1"]}},
        )
        plan, error = materialize_solver_reply(
            {"plan": self._revised()}, _plan(), editable_item_ids=scope
        )
        self.assertEqual(error, "")
        self.assertEqual(
            plan["items"][1]["acceptance_signals"],
            ["D cites (evidence_id, requirement_id, worker_id, file:line) crosswalk"],
        )
        # The untouched task is still carried forward verbatim.
        self.assertEqual(plan["items"][0]["acceptance_signals"], ["A is observable"])

    def test_unresolvable_editable_id_is_a_loud_retryable_error(self) -> None:
        plan, error = materialize_solver_reply(
            {"plan": self._revised()},
            _plan(),
            editable_item_ids={_INCIDENT_TARGET},
        )
        self.assertIsNone(plan)
        self.assertTrue(error.startswith("SOLVER_SCOPE_ITEM_UNKNOWN:"))
        self.assertTrue(is_retryable_solver_reply_error(error))

    def test_scope_violation_via_typed_change_is_rejected(self) -> None:
        plan, error = materialize_solver_reply(
            {
                "changes": [
                    {
                        "op": "replace_item_fields",
                        "item_id": "item-000001",
                        "fields": {"objective": "rewritten without a batch"},
                    }
                ]
            },
            _plan(),
            editable_item_ids={"item-000004"},
        )
        self.assertIsNone(plan)
        self.assertEqual(error, "SOLVER_SCOPE_ITEM_UNKNOWN:item-000001")


class TrailingCommaRepairTests(unittest.TestCase):
    def test_trailing_comma_is_repaired(self) -> None:
        self.assertEqual(_extract_json('{"action":"A",}'), {"action": "A"})

    def test_trailing_comma_inside_string_is_preserved(self) -> None:
        value = _extract_json('{"a": "x,}", "b": 1,}')
        self.assertEqual(value, {"a": "x,}", "b": 1})

    def test_strip_reports_no_change_for_clean_text(self) -> None:
        self.assertIsNone(_strip_trailing_commas('{"a": 1}'))

    def test_concatenated_documents_stay_rejected(self) -> None:
        self.assertIsNone(_extract_json('{"action":"A",}{"action":"B"}'))


if __name__ == "__main__":
    unittest.main()
