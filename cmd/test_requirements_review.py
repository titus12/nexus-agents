import unittest
from dataclasses import replace
from orchestrator.domain.requirements.review import *
from requirements_test_support import make_state, complete_draft, review_payload


class ReviewTests(unittest.TestCase):
    def setUp(self):
        state = make_state()
        self.state = replace(state, draft=complete_draft(state))
        self.issue = {'rule': 'FAILURE_RULE', 'section_ids': ['S_behavior'], 'requirement_ids': ['req-000001'],
                      'claim': '失败行为未说明', 'impact': '无法判断是否创建第二个连接', 'severity': 'blocking',
                      'owner': 'solver', 'acceptance': '写明失败后的可观察行为'}

    def test_omitted_blocker_stays_open_and_closure_requires_evidence(self):
        state = merge_review(self.state, review_payload([self.issue]))
        again = merge_review(state, review_payload())
        self.assertFalse(again.review_stamp.passed)
        self.assertEqual(next_repair_route(again), 'REVIEW_NEEDS_DRAFT')
        closed = merge_review(again, review_payload(closures=[{'issue_id': state.issues[0].issue_id, 'status': 'resolved', 'evidence': 'S_behavior明确失败通知'}]))
        self.assertTrue(closed.review_stamp.passed)

    def test_issue_rename_does_not_fork_ledger_and_acceptance_stays_stable(self):
        state = merge_review(self.state, review_payload([self.issue]))
        issue = dict(self.issue, issue_id='finding-999999', claim='换种说法')
        self.assertEqual(len(merge_review(state, review_payload([issue])).issues), 1)
        issue['acceptance'] = '额外增加性能测试'
        merged = merge_review(state, review_payload([issue]))
        self.assertEqual(len(merged.issues), 1)
        self.assertEqual(merged.issues[0].acceptance, '写明失败后的可观察行为')
        self.assertEqual(merged.issues[0].status, 'open')

    def test_new_demand_requires_close_then_reopen(self):
        state = merge_review(self.state, review_payload([self.issue]))
        closed = merge_review(state, review_payload(closures=[
            {'issue_id': state.issues[0].issue_id, 'status': 'resolved', 'evidence': 'S_behavior明确失败通知'}]))
        new_demand = dict(self.issue, claim='换种说法', acceptance='额外增加性能测试')
        merged = merge_review(closed, review_payload([new_demand]))
        self.assertEqual(len(merged.issues), 2)
        by_id = {issue.issue_id: issue for issue in merged.issues}
        self.assertEqual(by_id[state.issues[0].issue_id].status, 'resolved')
        self.assertEqual(by_id[merged.issues[-1].issue_id].acceptance, '额外增加性能测试')

    def test_first_review_needs_all_axes(self):
        with self.assertRaisesRegex(ValueError, 'COVERAGE_INCOMPLETE'):
            merge_review(self.state, {'issues': [], 'coverage': []})

    def test_chapter_misplaced_issue_is_accepted_and_routes_to_solver_repair(self):
        issue = {'rule': 'chapter-misplaced', 'section_ids': ['S_constraints'], 'requirement_ids': [],
                 'claim': '"不做后台重试"属于本次范围取舍，写在约束第2行，应归责任边界',
                 'impact': '下游会把范围取舍当成外部限制', 'severity': 'blocking', 'owner': 'solver',
                 'acceptance': '移到责任边界后本章仍成立'}
        merged = merge_review(self.state, review_payload([issue]))
        row = next(i for i in merged.issues if i.rule == 'chapter-misplaced')
        self.assertEqual(row.status, 'open')
        self.assertEqual(row.acceptance, '移到责任边界后本章仍成立')
        self.assertFalse(merged.review_stamp.passed)
        self.assertEqual(next_repair_route(merged), 'REVIEW_NEEDS_DRAFT')
