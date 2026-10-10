"""Canonical structured-plan regressions; no models or external notifications."""
from __future__ import annotations
import copy
from dataclasses import replace
import unittest
import tempfile
from orchestrator.domain.planning.compiler import commit_plan, PlanCompileError
from orchestrator.domain.planning.model import PlanRevision, CANONICAL_PLAN_SCHEMA
from orchestrator.domain.context import ReviewState, ReviewUpdate, apply_review_update, context_to_dto, context_from_dto

def context_for(review, state='ZHONGSHU_SOLVER'):
    context = legacy_context_for(review, state)
    return replace(context, request=replace(context.request, plan_schema=review.plan_schema))

def draft():
    return repaired_reply()['plan']

def canonical_review():
    return apply_review_update(ReviewState(revision_id='R0', plan_schema=CANONICAL_PLAN_SCHEMA), ReviewUpdate(revision_id='R1', plan=draft(), plan_base_ref=''))

class PlanRevisionTests(unittest.TestCase):

    def test_revision_is_deeply_isolated_and_dto_round_trips(self):
        p = draft()
        revision = commit_plan(None, p, expected_ref='')
        p['items'][0]['objective'] = 'outside mutation'
        exposed = revision.plan
        exposed['items'][0]['objective'] = 'copy mutation'
        self.assertEqual(revision.plan['items'][0]['objective'], 'Review 1')
        self.assertEqual(PlanRevision.from_dict(revision.to_dict()), revision)

    def test_unchanged_revision_is_idempotent_and_changed_revision_has_parent(self):
        first = commit_plan(None, draft(), expected_ref='')
        self.assertEqual(commit_plan(first, first.plan, expected_ref=first.reference), first)
        changed = first.plan
        changed['items'][0]['objective'] = 'New review'
        second = commit_plan(first, changed, expected_ref=first.reference)
        self.assertEqual(second.number, 2)
        self.assertEqual(second.parent_ref, first.reference)
        self.assertNotEqual(first.content_hash, second.content_hash)

    def test_stale_or_missing_base_is_rejected(self):
        first = commit_plan(None, draft(), expected_ref='')
        for expected in (None, 'old-revision'):
            with self.subTest(base=expected), self.assertRaises(PlanCompileError):
                commit_plan(first, first.plan, expected_ref=expected)

    def test_duplicate_items_and_group_ids_are_rejected(self):
        for key in ('items', 'groups'):
            p = draft()
            p[key].append(copy.deepcopy(p[key][0]))
            with self.subTest(key=key), self.assertRaises(PlanCompileError):
                commit_plan(None, p, expected_ref='')

    def test_requirement_baseline_and_frozen_work_are_preserved(self):
        first = commit_plan(None, draft(), expected_ref='')
        changed = first.plan
        changed['requirements'][0]['text'] = 'Invented scope'
        with self.assertRaises(PlanCompileError):
            commit_plan(first, changed, expected_ref=first.reference, requirements=first.plan['requirements'])
        changed = first.plan
        changed['items'][0]['objective'] = 'Changed frozen task'
        with self.assertRaises(PlanCompileError):
            commit_plan(first, changed, expected_ref=first.reference, frozen_group_ids=['GRP-001'])

    def test_snapshot_round_trip_preserves_revision_and_old_dtos_stay_legacy(self):
        c = context_for(canonical_review())
        saved = context_to_dto(c)
        restored = context_from_dto(saved)
        self.assertEqual(restored.review.plan_revision, c.review.plan_revision)
        old = context_to_dto(context_for(review_for(draft())))
        old['review'].pop('plan_schema', None)
        old['review'].pop('plan_revision', None)
        self.assertEqual(context_from_dto(old).review.plan_schema, 'legacy-v1')

    def test_mirror_tampering_or_unknown_policy_is_rejected_on_load(self):
        c = context_for(canonical_review())
        saved = context_to_dto(c)
        saved['review']['plan']['items'][0]['objective'] = 'Tampered mirror'
        with self.assertRaises(ValueError):
            context_from_dto(saved)
        saved = context_to_dto(c)
        saved['review']['plan_revision']['policy_version'] = 'unknown'
        with self.assertRaises(ValueError):
            context_from_dto(saved)

    def test_projection_only_business_write_is_rejected(self):
        review = canonical_review()
        items = (replace(review.task_items[0], objective='Bypass'),) + review.task_items[1:]
        with self.assertRaises(PlanCompileError):
            apply_review_update(review, ReviewUpdate(revision_id='R2', task_items=items))

    def test_changed_group_invalidates_only_its_document_and_approval(self):
        review = canonical_review()
        rows = tuple((replace(r, stage='FROZEN', approved_surface_hash='approved') for r in review.zhongshu_groups))
        rows = (replace(rows[0], stage='REVIEWING'),) + rows[1:]
        review = replace(review, zhongshu_groups=rows)
        changed = review.plan_revision.plan
        changed['items'][0]['objective'] = 'Revised task'
        next_review = apply_review_update(review, ReviewUpdate(revision_id='R2', plan=changed, plan_base_ref=review.plan_revision.reference))
        self.assertEqual(next_review.zhongshu_groups[0].doc_version, 2)
        self.assertEqual(next_review.zhongshu_groups[0].approved_surface_hash, '')
        self.assertEqual(next_review.zhongshu_groups[1:], review.zhongshu_groups[1:])

    def test_evidence_packet_does_not_replace_committed_plan(self):
        review = canonical_review()
        packet = {'candidate_items': [], 'candidate_groups': [], 'facts': ['new evidence']}
        updated = apply_review_update(review, ReviewUpdate(revision_id='R2', plan=packet, evidence_packet=packet))
        self.assertEqual(updated.plan_revision, review.plan_revision)
        self.assertEqual(updated.plan, review.plan)
        self.assertEqual(updated.evidence_packet, packet)

    def test_generated_documents_and_schema_are_verified_on_restore(self):
        for key, bad in (('doc_markdown', 'forged'), ('doc_source_hash', 'forged'), ('doc_version', 0)):
            saved = context_to_dto(context_for(canonical_review()))
            saved['review']['zhongshu_groups'][0][key] = bad
            with self.subTest(field=key), self.assertRaises(ValueError):
                context_from_dto(saved)
        saved = context_to_dto(context_for(canonical_review()))
        saved['request']['plan_schema'] = 'legacy-v1'
        with self.assertRaises(ValueError):
            context_from_dto(saved)

    def test_compiler_rejects_acceptance_heading_injection_and_missing_signals(self):
        for signal in ([], ['# Injected heading'], ['line one\nline two'], ['a' * 301]):
            p = draft()
            p['items'][0]['acceptance_signals'] = signal
            with self.subTest(signal=signal), self.assertRaises(PlanCompileError):
                commit_plan(None, p, expected_ref='')

    def test_frozen_sources_include_global_metadata_and_default_requirement_baseline(self):
        first = commit_plan(None, draft(), expected_ref='')
        changed = first.plan
        changed['plan_id'] = 'new global scope'
        with self.assertRaises(PlanCompileError):
            commit_plan(first, changed, expected_ref=first.reference, frozen_group_ids=['GRP-001'])
        changed = first.plan
        changed['requirements'][0]['text'] = 'new baseline'
        with self.assertRaises(PlanCompileError):
            commit_plan(first, changed, expected_ref=first.reference)

    def test_json_identity_distinguishes_booleans_from_numbers(self):
        p = draft()
        p['limit'] = 1
        first = commit_plan(None, p, expected_ref='')
        changed = first.plan
        changed['limit'] = True
        second = commit_plan(first, changed, expected_ref=first.reference)
        self.assertEqual(second.number, 2)
        self.assertIs(second.plan['limit'], True)
        review = apply_review_update(ReviewState(revision_id='R0', plan_schema=CANONICAL_PLAN_SCHEMA), ReviewUpdate(revision_id='R1', plan=p, plan_base_ref=''))
        saved = context_to_dto(context_for(review))
        saved['review']['plan']['limit'] = True
        with self.assertRaises(ValueError):
            context_from_dto(saved)
        p = draft()
        p['requirements'][0]['limit'] = 1
        first = commit_plan(None, p, expected_ref='')
        changed = first.plan
        changed['requirements'][0]['limit'] = True
        with self.assertRaises(PlanCompileError):
            commit_plan(first, changed, expected_ref=first.reference)
if __name__ == '__main__':
    unittest.main()
from canonical_plan_test_support import repaired_reply, review_for, context_for as legacy_context_for
