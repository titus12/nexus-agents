"""Advisory draft lint: deterministic hints that never block routing (T3)."""
import tempfile
import unittest
from dataclasses import replace

from orchestrator.app import OrchestratorApp
from orchestrator.domain.requirements.budget import record_progress, relevant_facts_digest
from orchestrator.domain.requirements.document import render_markdown, text_digest
from orchestrator.domain.requirements.lint import lint_draft
from orchestrator.domain.requirements.model import DraftRevision, digest
from orchestrator.domain.requirements.review import merge_review, next_repair_route
from requirements_test_support import make_state, complete_draft, review_payload
from test_requirements_draft_first import DraftFirstScript
from test_requirements_workflow_e2e import context, pump


def base():
    state = make_state()
    return state, complete_draft(state)


def draft_with(**markdown_by_kind):
    state, draft = base()
    sections = tuple(replace(s, markdown=markdown_by_kind[s.kind]) if s.kind in markdown_by_kind else s
                     for s in draft.sections)
    rebuilt = DraftRevision(draft.version, draft.title, sections, draft.consensus_digest, '0'*64)
    return state, replace(rebuilt, document_digest=text_digest(render_markdown(rebuilt)))


def resection(state, draft, kind, **fields):
    sections = tuple(replace(s, **fields) if s.kind == kind else s for s in draft.sections)
    rebuilt = DraftRevision(draft.version, draft.title, sections, draft.consensus_digest, '0'*64)
    return replace(rebuilt, document_digest=text_digest(render_markdown(rebuilt)))


def rules(draft, state, sources=None):
    return [i.rule for i in lint_draft(draft, state.consensus, state.sources if sources is None else sources)]


class LintRuleTests(unittest.TestCase):
    def test_vague_phrase_hits_without_digits_or_pending(self):
        state, draft = draft_with(goals='- 系统应当稳定。\n- 响应时间不超过 200ms。')
        self.assertEqual(rules(draft, state).count('lint-vague-phrase'), 1)

    def test_vague_phrase_ignores_digits_and_pending_and_plan_wording(self):
        state, draft = draft_with(goals='- 系统应当在 3 秒内稳定。\n- 稳定标准待定。')
        self.assertNotIn('lint-vague-phrase', rules(draft, state))

    def test_acceptance_requires_a_test_type(self):
        state, draft = draft_with(acceptance='同一会话并发登录时只观察到一次连接建立。')
        self.assertIn('lint-acceptance-no-test-type', rules(draft, state))
        state, draft = draft_with(acceptance='回归测试：并发登录只观察到一次连接建立。')
        self.assertNotIn('lint-acceptance-no-test-type', rules(draft, state))

    def test_plan_as_result_flags_completed_wording_only(self):
        state, draft = draft_with(acceptance='已通过回归测试。')
        self.assertIn('lint-plan-as-result', rules(draft, state))
        state, draft = draft_with(acceptance='应通过回归测试。')
        self.assertNotIn('lint-plan-as-result', rules(draft, state))

    def test_unconfirmed_number_requires_source_or_basis(self):
        state, draft = draft_with(goals='超时时间为 30 秒。')
        self.assertIn('lint-unconfirmed-number', rules(draft, state))
        confirmed_source = replace(state.sources[0], source_id='src-000002', text='超时时间为 30 秒')
        self.assertNotIn('lint-unconfirmed-number', rules(draft, state, sources=(confirmed_source,)))
        bound = resection(state, draft, 'goals', decision_ids=('decision-000001',))
        self.assertNotIn('lint-unconfirmed-number', rules(bound, state))
        pending, draft_pending = draft_with(goals='超时时间待定，预期 30 秒内。')
        self.assertNotIn('lint-unconfirmed-number', rules(draft_pending, pending))
        plain, draft_plain = draft_with(goals='发送次数为 2。')
        self.assertNotIn('lint-unconfirmed-number', rules(draft_plain, plain))

    def test_scope_cut_prefix_in_constraints(self):
        state, draft = draft_with(constraints='- 不做数据导出。\n- 限制导出频率为每分钟 10 次。')
        self.assertEqual(rules(draft, state).count('lint-constraint-is-scope-cut'), 1)
        state, draft = draft_with(constraints='- 限制导出频率为每分钟 10 次。')
        self.assertNotIn('lint-constraint-is-scope-cut', rules(draft, state))

    def test_non_goal_duplicate_of_scope(self):
        state, draft = draft_with(scope='导出数据功能。', non_goals='- 导出数据功能。')
        self.assertIn('lint-non-goal-duplicates-scope', rules(draft, state))
        state, draft = draft_with(scope='导出数据功能。', non_goals='- 不修改其他会话的行为。')
        self.assertNotIn('lint-non-goal-duplicates-scope', rules(draft, state))

    def test_section_order_deviation(self):
        state, draft = base()
        order = [s.kind for s in draft.sections]
        swapped = [s if s.kind != 'goals' else next(x for x in draft.sections if x.kind == 'acceptance') for s in draft.sections]
        swapped = [s if s.kind != 'acceptance' else next(x for x in draft.sections if x.kind == 'goals') for s in swapped]
        assert [s.kind for s in swapped] != order
        reordered = DraftRevision(draft.version, draft.title, tuple(swapped), draft.consensus_digest, '0'*64)
        reordered = replace(reordered, document_digest=text_digest(render_markdown(reordered)))
        self.assertIn('lint-section-order', rules(reordered, state))
        self.assertNotIn('lint-section-order', rules(draft, state))

    def test_internal_id_heading_placeholder_are_advisory_hits(self):
        state, draft = draft_with(behavior='见 req-000001 的处理。')
        self.assertIn('lint-internal-id-in-text', rules(draft, state))
        state, draft = draft_with(behavior='见需求原文的处理。')
        self.assertNotIn('lint-internal-id-in-text', rules(draft, state))
        state, draft = draft_with(scope='## 子标题\n以会话标识区分连接。')
        self.assertIn('lint-heading-in-body', rules(draft, state))
        state, draft = draft_with(acceptance='回归测试：TODO 后补场景。')
        self.assertIn('lint-placeholder', rules(draft, state))
        state, draft = draft_with(acceptance='回归测试：【待确认：q-000001】的数值待定。')
        self.assertNotIn('lint-placeholder', rules(draft, state))

    def test_empty_sections_and_determinism(self):
        state, draft = draft_with(goals='', acceptance='')
        self.assertEqual(lint_draft(draft, state.consensus, state.sources),
                         lint_draft(draft, state.consensus, state.sources))
        issues = lint_draft(draft, state.consensus, state.sources)
        self.assertTrue(all(i.severity == 'advisory' and i.owner == 'solver' for i in issues))
        for rule in {i.rule for i in issues}:
            rows = [i for i in issues if i.rule == rule]
            self.assertEqual([i.issue_id for i in rows],
                             [f'{rule}-{n}' for n in range(1, len(rows)+1)])


class LintRoutingTests(unittest.TestCase):
    def test_only_lint_issues_route_valid_into_review(self):
        with tempfile.TemporaryDirectory() as directory:
            app = OrchestratorApp(context(), directory, multica=LintyDraftScript(),
                                  poll_interval=0, timeout_seconds=2)
            pump(app, 'REQ_REVIEW')
            issues = app.ctx.requirements.issues
            lint = [i for i in issues if i.issue_id.startswith('lint-')]
            self.assertTrue(lint)
            self.assertTrue(all(i.severity == 'advisory' and i.owner == 'solver' for i in lint))
            self.assertIn('lint-acceptance-no-test-type', [i.rule for i in lint])
            self.assertIn('lint-vague-phrase', [i.rule for i in lint])
            self.assertFalse([i for i in issues if i.issue_id.startswith('mechanical-')])

    def test_merge_review_leaves_lint_open_and_route_ignores_it(self):
        state, draft = base()
        lint = lint_draft(draft, state.consensus, state.sources)
        self.assertTrue(lint)
        state = replace(state, draft=draft, issues=lint)
        merged = merge_review(state, review_payload())
        self.assertTrue(any(i.issue_id == lint[0].issue_id and i.status == 'open' for i in merged.issues))
        self.assertTrue(merged.review_stamp.passed)
        self.assertEqual(next_repair_route(merged), 'REVIEW_PASSED')

    def test_progress_digest_ignores_lint_issues(self):
        state, draft = base()
        lint = lint_draft(draft, state.consensus, state.sources)
        def digests(current):
            return (current.draft.document_digest, relevant_facts_digest(current.consensus.facts),
                    digest(current.consensus.active_decision_ids))
        self.assertEqual(digests(replace(state, draft=draft, issues=lint)), digests(replace(state, draft=draft)))
        ledger = record_progress(state.budget, *digests(replace(state, draft=draft, issues=lint)))
        ledger = record_progress(ledger, *digests(replace(state, draft=draft)))
        self.assertEqual(ledger.stalled_count, 1)


class LintyDraftScript(DraftFirstScript):
    def dispatch(self, request):
        receipt = super().dispatch(request)
        if request.target_state == 'REQ_DRAFT':
            payload = self.replies[request.request_id][0].payload
            for section in payload['document']['sections']:
                if section['kind'] == 'goals':
                    section['markdown'] += '\n- 系统应当稳定运行。'
        return receipt


if __name__ == '__main__':
    unittest.main()
